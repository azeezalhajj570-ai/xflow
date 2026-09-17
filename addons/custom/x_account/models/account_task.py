# Part of Odoo. See LICENSE file for full copyright and licensing details.

import ast
import json
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class XAccountTask(models.Model):
    """Durable, retryable, owned, prioritized X task queue.

    MVP worker uses ir.cron + PostgreSQL skip-locked claiming. This model is
    designed to be fronted by an approved Odoo queue mechanism at production scale.
    """

    _name = 'x.account.task'
    _description = 'X Account Task'
    _order = 'create_date desc, priority desc'

    account_id = fields.Many2one(
        'social.account',
        string='X Account',
        required=True,
        index=True,
        ondelete='cascade',
    )
    group_id = fields.Many2one(
        'x.account.group',
        string='X Account Group',
        ondelete='set null',
    )
    operation = fields.Char(
        string='Operation',
        required=True,
        index=True,
        help='Provider operation to execute (e.g. like, comment, send_dm).',
    )
    status = fields.Selection(
        [
            ('pending', 'Pending'),
            ('running', 'Running'),
            ('success', 'Success'),
            ('failed', 'Failed'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status',
        default='pending',
        index=True,
    )
    priority = fields.Integer(string='Priority', default=0)
    retry_count = fields.Integer(string='Retry Count', default=0, readonly=True)
    max_attempts = fields.Integer(string='Max Attempts', default=3)
    claimed_at = fields.Datetime(string='Claimed At', readonly=True)
    next_retry_at = fields.Datetime(string='Next Retry At', index=True)
    done_at = fields.Datetime(
        string='Done At',
        readonly=True,
        index=True,
        help='When the task reached a terminal state (success, failed or '
             'cancelled). Empty while the task is pending or running.',
    )
    error = fields.Text(string='Error', readonly=True)
    result = fields.Text(string='Result', readonly=True)
    backoff_base = fields.Integer(string='Backoff Base (seconds)', default=30)
    task_context = fields.Text(
        string='Task Context',
        help='JSON call kwargs passed to the provider operation. Must not '
             'contain credentials.',
    )
    target_post_id = fields.Char(
        string='Target Post ID',
        compute='_compute_task_targets',
        store=True,
        index=True,
        help='Target tweet/post id extracted from the task context.',
    )
    target_screen_name = fields.Char(
        string='Target Screen Name',
        compute='_compute_task_targets',
        store=True,
        index=True,
        help='Target X username extracted from the task context.',
    )
    source = fields.Char(
        string='Source',
        compute='_compute_task_targets',
        store=True,
        index=True,
        help='Where the task came from (e.g. channel_automation, group, webhook).',
    )
    processing_time = fields.Integer(
        string='Period of Processing (sec)',
        compute='_compute_processing_time',
        readonly=True,
        help='Seconds between task creation and completion (done_at - create_date).',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        related='account_id.company_id',
        store=True,
        index=True,
    )

    _MAX_RUNNING_PER_ACCOUNT = 1
    # Tasks claimed + run per commit in the cron path. Bounds the work (and so
    # the rollback) of one transaction well under ``limit_time_real``: a sweep
    # killed at the timeout loses at most one chunk, not the whole batch.
    _CLAIM_CHUNK_SIZE = 20
    # Advisory-lock namespace for the per-account claim (arbitrary fixed int4).
    _ACCOUNT_LOCK_NAMESPACE = 0x58414354  # 'XACT'
    # Used only by the manual "Ignore Stale" action (action_ignore_stale).
    # Claiming itself no longer age-gates tasks: every due pending task is
    # candidate, so nothing can sit in ``pending`` forever waiting to be
    # processed.
    _QUEUE_MAX_AGE_MINUTES = 60
    _WEBHOOK_OPERATION = 'process_webhook_event'
    _TERMINAL_STATUSES = ('success', 'failed', 'cancelled')
    # A task in one of these states means the operation is already done, or in
    # flight, for its target: an equivalent task must not be created for it.
    # ``failed``/``cancelled`` are deliberately absent so a genuine retry still
    # gets its one task.
    _DUPLICATE_BLOCKING_STATUSES = ('pending', 'running', 'success')
    # Engagement operations that are idempotent per target: repeating one is
    # either a no-op X rejects or a duplicate the operator never wanted.
    _DUPLICATE_CLEANUP_OPERATIONS = (
        'like', 'repost', 'comment', 'follow', 'bookmark', 'unbookmark')

    @staticmethod
    def _target_post_id_from_context(ctx):
        """Normalize the target post id out of a task context.

        Channel automation stores ``{'post': {'post_id': ...}}`` while group
        automation stores a top-level ``target_id``. Reading only the top-level
        ``post_id`` left ``target_post_id`` empty for every channel task, which
        made the dedup index unusable and let each re-post of the same link
        spawn another task.
        """
        nested = ctx.get('post')
        if not isinstance(nested, dict):
            nested = {}
        for value in (ctx.get('post_id'), nested.get('post_id'),
                      ctx.get('target_id')):
            if value:
                return str(value)
        return False

    @staticmethod
    def _dedup_target(target_post_id, target_screen_name):
        """The per-target key an operation is idempotent on.

        Tweet operations key on the post id; ``follow`` has no post id and keys
        on the screen name instead.
        """
        return str(target_post_id or '').strip() or (target_screen_name or False)

    @api.depends('task_context')
    def _compute_task_targets(self):
        for task in self:
            ctx = task._task_context()
            task.target_post_id = task._target_post_id_from_context(ctx)
            task.target_screen_name = ctx.get('screen_name') or False
            task.source = ctx.get('source') or False

    @api.depends('create_date', 'done_at')
    def _compute_processing_time(self):
        for task in self:
            if task.create_date and task.done_at:
                task.processing_time = int(
                    (task.done_at - task.create_date).total_seconds())
            else:
                task.processing_time = 0

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        for task in res:
            if not task.next_retry_at:
                task.write({'next_retry_at': fields.Datetime.now()})
        return res

    def write(self, vals):
        """Stamp ``done_at`` when a task reaches a terminal state.

        Done in ``write`` so every path that settles a task (success, permanent
        failure, manual cancel/ignore) records when it finished, which the
        operations pivot/report groups on.
        """
        if vals.get('status') in self._TERMINAL_STATUSES and not vals.get('done_at'):
            vals = dict(vals)
            vals['done_at'] = fields.Datetime.now()
        return super().write(vals)

    @api.model
    def _process_queue(self, limit=100, shard=0, shards=1, commit=False):
        """Claim and run due tasks for one shard of the queue.

        ``shard``/``shards`` let several cron jobs (one per shard) drain
        disjoint account buckets concurrently. ``commit`` is set only by the
        cron path: it commits after each account so a sweep never holds one
        long transaction. The manual action and tests leave it off so they stay
        inside their own transaction.
        """
        claimed = self._claim_and_run(
            limit=limit, shard=shard, shards=shards, commit=commit)
        return len(claimed)

    @api.model
    def _claim_and_run(self, limit=100, company_ids=None, shard=0, shards=1,
                       commit=False):
        """Claim and run due tasks, returning the claimed recordset.

        Shared by the cron worker(s) (`_process_queue`) and the manual "Process
        Queue" action so the caller can inspect per-task results afterwards.

        Fair-share: the sweep is split evenly among every account that has due
        tasks, so a huge backlog on one account (e.g. a broken token) cannot
        starve other accounts — each account is drained in lock-step instead
        of one account monopolizing the ``limit``. Within an account the
        user-facing operations (like, repost, comment, bookmark, follow, DMs,
        ...) are claimed first so a webhook backfill never starves them; the
        ``process_webhook_event`` backfill only fills whatever capacity the
        account's share still has. Within each bucket tasks run in
        ``priority desc, create_date asc`` order.

        Concurrency: the account is the unit of work. Claiming takes a
        transaction-scoped advisory lock per account (per-account
        single-flight) and selects rows with ``FOR UPDATE SKIP LOCKED``, so two
        workers — e.g. the sharded cron jobs — never run the same account, let
        alone the same task. With ``shards > 1`` only accounts whose id falls
        in this shard are handled, so the shards cover the queue without
        contending.

        Company-scoped: when ``company_ids`` is given, only tasks belonging to
        those companies are claimed. No filter keeps ``_process_queue`` (the
        cron, running as ``__system__`` with a single allowed company)
        sweeping the *whole* backlog across accounts of every company, while
        ``action_process_queue`` passes the operator's allowed companies so a
        user's manual run never touches another company's backlog. The sudo'd
        searches below bypass the company record rule, hence the explicit
        domain filter.

        No age gate: claiming ignores ``create_date`` so a pending task is
        always a candidate — nothing gets stranded in ``pending`` forever
        (older backlog is drained in fair-share order at the same bounded
        rate). Use ``action_ignore_stale`` to manually fail tasks you no
        longer want processed.
        """
        now = fields.Datetime.now()
        domain = [
            ('status', 'in', ('pending',)),
            ('next_retry_at', '<=', now),
        ]
        if company_ids:
            domain.append(('company_id', 'in', list(company_ids)))
        import logging
        _logger = logging.getLogger(__name__)
        _logger.info('Task queue: searching with domain=%s', domain)
        # Claiming below reads rows directly, so push pending ORM writes (e.g.
        # a just-created task's ``next_retry_at``) to the database first.
        self.env.flush_all()
        grouped = self.sudo()._read_group(
            domain, ['account_id'], ['account_id:count'], order='account_id')
        account_ids = [account.id for g in grouped for account in g[0]]
        _logger.info('Task queue: found account_ids=%s grouped=%s', account_ids, grouped)
        # Halt paid work for accounts whose provider reports it cannot help
        # (e.g. GetXAPI credit exhaustion): their tasks stay pending, without
        # burning attempts or paid calls, until the block is lifted.
        accounts = self.env['social.account'].sudo().browse(account_ids).exists()
        account_ids = [
            account.id for account in accounts
            if account.active and not account._x_action_blocked_reason()]
        if shards > 1:
            account_ids = [
                account_id for account_id in account_ids
                if account_id % shards == shard]
        if not account_ids:
            return self.env['x.account.task']
        share = max(limit // len(account_ids), 1)
        claimed = self.env['x.account.task']
        for account_id in account_ids:
            claimed |= self._drain_account(account_id, share, now, commit)
        return claimed

    def _drain_account(self, account_id, share, now, commit):
        """Claim, run and commit one account's due tasks in bounded chunks.

        The worker can be killed at ``limit_time_real`` (or crash) between
        commits, so each chunk is claimed, executed and committed on its own.
        Committing only once after the whole account batch meant a slow sweep
        (e.g. webhook decryption) that hit the timeout rolled the entire batch
        back, leaving it ``pending`` to be retried forever while the backlog
        grew. ``commit`` is off for the manual action and tests, which stay in
        one transaction and therefore drain in a single chunk.
        """
        chunk = self._CLAIM_CHUNK_SIZE if commit else share
        claimed = self.env['x.account.task']
        remaining = share
        while remaining > 0:
            tasks = self._claim_account_tasks(
                account_id, min(chunk, remaining), now)
            if not tasks:
                break
            claimed |= tasks
            self._execute_operation_batch(tasks)
            remaining -= len(tasks)
            if commit:
                self._commit_queue_progress()
        return claimed

    def _claim_account_tasks(self, account_id, share, now):
        """Atomically claim up to ``share`` due tasks for one account.

        Returns the claimed ``running`` tasks — empty when another worker owns
        the account, when it already has a RUNNING task, or when nothing is
        due.
        """
        if share <= 0:
            return self.env['x.account.task']
        cr = self.env.cr
        # Per-account single-flight: the transaction-scoped advisory lock means
        # only one worker owns this account at a time. It is released by the
        # per-account commit (or at the end of the sweep when not committing).
        cr.execute(
            'SELECT pg_try_advisory_xact_lock(%s, %s)',
            (self._ACCOUNT_LOCK_NAMESPACE, account_id))
        if not cr.fetchone()[0]:
            return self.env['x.account.task']
        # A pre-existing RUNNING task — including a stale one left by a crashed
        # worker — still blocks new claims for the account.
        running = self.sudo().search_count([
            ('account_id', '=', account_id),
            ('status', '=', 'running'),
        ])
        if running >= self._MAX_RUNNING_PER_ACCOUNT:
            return self.env['x.account.task']
        # User-facing actions first, then fill the account's share with the
        # webhook backfill.
        ids = self._claim_account_rows(account_id, share, now, webhook=False)
        if len(ids) < share:
            ids += self._claim_account_rows(
                account_id, share - len(ids), now, webhook=True)
        if not ids:
            return self.env['x.account.task']
        cr.execute(
            'UPDATE x_account_task SET status = %s, claimed_at = %s '
            'WHERE id = ANY(%s)',
            ('running', now, ids))
        # The rows changed outside the ORM: drop the cached claim fields.
        self.invalidate_model(['status', 'claimed_at'])
        return self.browse(ids)

    def _claim_account_rows(self, account_id, limit, now, webhook):
        """Select due task ids for one account with a non-blocking row lock."""
        operator = '=' if webhook else '<>'
        self.env.cr.execute(
            "SELECT id FROM x_account_task "
            "WHERE account_id = %s AND status = %s AND next_retry_at <= %s "
            "AND operation " + operator + " %s "
            "ORDER BY priority DESC, create_date ASC "
            "FOR UPDATE SKIP LOCKED LIMIT %s",
            (account_id, 'pending', now, self._WEBHOOK_OPERATION, limit))
        return [row[0] for row in self.env.cr.fetchall()]

    def _commit_queue_progress(self):
        """Commit the current account's work so locks release incrementally.

        Only the cron path (``commit=True``) calls this; the manual action and
        tests keep a single transaction.
        """
        self.env.cr.commit()
        # The committed transaction invalidated nothing ORM-side; drop caches
        # so later reads (e.g. the caller inspecting task.status) re-fetch.
        self.env.invalidate_all(flush=False)

    def _scope_inactive_reason(self):
        """Why this task must not run, or False when it may run.

        An archived scope — the linked account, the automation group
        (``x.account.group``) or the linking ``discuss.channel`` — must never
        execute paid/action work. The queue cancels such tasks instead of
        running them (like/repost/comment/bookmark/DM/...), so archiving an
        account or a group stops all work for it.
        """
        self.ensure_one()
        account = self.account_id
        if not account or not account.active:
            return 'account archived'
        if self.group_id and not self.group_id.active:
            return 'group archived'
        ctx = self._task_context()
        channel_id = ctx.get('channel_id')
        if channel_id:
            try:
                channel = self.env['discuss.channel'].with_context(
                    active_test=False).browse(int(channel_id))
            except (TypeError, ValueError):
                return False
            if not channel.exists() or not channel.active:
                return 'channel archived'
        return False

    def _execute_operation_batch(self, tasks, operation=None, **extra_ctx):
        """Execute multiple tasks' operations in batch when supported.

        Groups tasks by account and operation, then calls the batch method
        if available (e.g., process_webhook_events). Falls back to individual
        execution for operations without batch support.
        """
        if not tasks:
            return
        sub = self.env['x.account.task']
        for task in tasks:
            reason = task._scope_inactive_reason()
            if reason:
                task.write({'status': 'cancelled', 'error': 'Skipped: %s' % reason})
            else:
                sub |= task
        if not sub:
            return
        import json as _json
        grouped = {}
        for task in sub:
            key = (task.account_id.id, operation or task.operation)
            if key not in grouped:
                grouped[key] = self.env['x.account.task']
            grouped[key] |= task
        for (account_id, op), group_tasks in grouped.items():
            account = group_tasks[0].account_id
            if not account:
                for task in group_tasks:
                    task._schedule_retry('Missing account')
                continue
            try:
                provider = account.get_provider_for_operation(op)
                batch_op = op + 's' if not op.endswith('s') else op
                batch_fn = getattr(provider, batch_op, None)
                if batch_fn and callable(batch_fn) and len(group_tasks) > 1:
                    ctx_list = []
                    for task in group_tasks:
                        try:
                            ctx = _json.loads(task.task_context or '{}')
                        except ValueError:
                            ctx = {}
                        ctx.update(extra_ctx)
                        ctx_list.append(ctx)
                    if op == self._WEBHOOK_OPERATION:
                        event_uuids = [ctx.get('event_uuid') for ctx in ctx_list if ctx.get('event_uuid')]
                        if event_uuids:
                            result = batch_fn(event_uuids=event_uuids)
                            for task in group_tasks:
                                task.write({'status': 'success', 'result': str(result)})
                            continue
                for task in group_tasks:
                    task._execute_operation(operation=op, **extra_ctx)
            except Exception as exc:
                for task in group_tasks:
                    task._schedule_retry(exc)

    def _execute_operation(self, operation=None, **extra_ctx):
        """Execute one task's operation via the account provider.

        Used by both the cron worker and the automation rule so a task can be
        run immediately when created (auto-execute) or later by the queue.
        """
        self.ensure_one()
        import json as _json
        if self.status == 'cancelled':
            return None
        reason = self._scope_inactive_reason()
        if reason:
            # An archived account/group/channel must never run: cancel the task
            # instead of paying for a doomed call.
            self.write({'status': 'cancelled', 'error': 'Skipped: %s' % reason})
            return None
        account = self.account_id
        if not account:
            self._schedule_retry('Missing account')
            return None
        blocked = account._x_action_blocked_reason()
        if blocked:
            # Already claimed when the provider tripped its breaker mid-sweep:
            # release the task back to pending instead of paying for a doomed
            # call or failing work the operator can resume.
            self.write({'status': 'pending', 'error': blocked})
            return None
        try:
            op = operation or self.operation
            provider = account.get_provider_for_operation(op)
            fn = getattr(provider, op, None)
            if not fn or not callable(fn):
                self._schedule_retry('Unknown operation %s' % op)
                return None
            try:
                ctx = _json.loads(self.task_context or '{}')
            except ValueError:
                ctx = {}
            ctx.update(extra_ctx)
            result = fn(**{k: v for k, v in ctx.items() if k != 'self'})
            self.write({'status': 'success', 'result': result})
            if op == 'follow' and self._follow_succeeded(result):
                self._record_follow(account)
            return result
        except Exception as exc:
            self._schedule_retry(exc)
            return None

    def _schedule_retry(self, error):
        """Retry ``error`` with backoff, or fail it permanently.

        Only errors flagged retryable by the provider (transient rate limits,
        timeouts, 5xx) are re-queued. Permanent conditions — depleted credits,
        invalid credentials, a recipient who cannot receive DMs — fail
        immediately instead of burning every attempt.
        """
        self.ensure_one()
        message = str(error)
        retryable = getattr(error, 'retryable', True)
        self.write({'error': message})
        if retryable and self.retry_count < self.max_attempts - 1:
            delay = self.backoff_base * (2 ** self.retry_count)
            self.write({
                'retry_count': self.retry_count + 1,
                'status': 'pending',
                'next_retry_at': fields.Datetime.now() + timedelta(seconds=delay),
            })
        else:
            self.write({'status': 'failed'})

    def _task_context(self):
        try:
            return json.loads(self.task_context or '{}')
        except ValueError:
            return {}

    @api.model
    def _has_blocking_task(self, account_id, operation, target_post_id=None,
                           target_screen_name=None):
        """Whether an equivalent task already exists for this target.

        Pending means the work is queued, running means it is happening and
        success means it is done — in all three cases another task would either
        duplicate the work or fail deterministically (X rejects a repeat
        retweet). ``failed`` and ``cancelled`` deliberately do not block, so a
        genuine retry still gets exactly one task.
        """
        domain = [
            ('account_id', '=', account_id),
            ('operation', '=', operation),
            ('status', 'in', self._DUPLICATE_BLOCKING_STATUSES),
        ]
        if target_post_id:
            domain.append(('target_post_id', '=', str(target_post_id)))
        elif target_screen_name:
            domain.append(('target_screen_name', '=', target_screen_name))
        else:
            return False
        return bool(self.sudo().search_count(domain))

    @staticmethod
    def _follow_succeeded(result):
        """Whether a follow operation result signals success.

        Providers either return a plain truthy DTO or a dict with a success
        flag ('ok', 'success', 'followed', ...). An empty result counts as
        failure so the account is not marked as following the target.
        """
        if not result:
            return False
        if isinstance(result, dict):
            for key in ('ok', 'success', 'successful', 'followed'):
                if key in result:
                    return bool(result[key])
            if not result:
                return False
        return True

    def _find_followed_partner(self, account, screen_name):
        """Resolve the followed X member partner for a follow task.

        Prefers the task's conversation members, then falls back to a global
        ``x_username`` match, so the partner only has X identity data.
        """
        if not screen_name:
            return None
        ctx = self._task_context()
        members = self.env['res.partner']
        channel_id = ctx.get('channel_id')
        if channel_id:
            channel = self.env['discuss.channel'].browse(channel_id)
            if channel and channel.exists():
                members = channel.x_group_member_ids
        needle = str(screen_name).lstrip('@')
        partner = members.filtered(
            lambda p: p.x_username and p.x_username.lstrip('@') == needle)
        if not partner:
            partner = self.env['res.partner'].sudo().search([
                ('x_user_id', '!=', False),
                '|',
                ('x_username', '=', needle),
                ('x_username', '=', '@' + needle),
            ], limit=1)
        return partner

    def _record_follow(self, account):
        """Mark the followed target partner on the account (``x_following_ids``)."""
        self.ensure_one()
        ctx = self._task_context()
        partner = self._find_followed_partner(account, ctx.get('screen_name'))
        if not partner:
            return
        account.sudo().write({'x_following_ids': [(4, partner.id)]})

    def action_cancel_duplicate_tasks(self, operations=None):
        """Cancel failed tasks whose operation already succeeded for the target.

        A recurring link (the same tweet re-posted every hour) used to create a
        fresh task each time. X rejects the repeat, so the queue burned every
        attempt and the task ended ``failed`` — a duplicate of work that had
        already succeeded, not a real failure. Marking them ``cancelled`` keeps
        the failed list meaningful: what remains is targets that genuinely never
        succeeded.

        Defaults to the idempotent engagement operations; pass ``operations`` to
        narrow it.
        """
        ops = list(operations or self._DUPLICATE_CLEANUP_OPERATIONS)
        if not ops:
            return self._duplicate_cleanup_notification(0)
        Task = self.sudo()
        succeeded = Task.search_read(
            [('status', '=', 'success'), ('operation', 'in', ops)],
            ['account_id', 'operation', 'target_post_id', 'target_screen_name'])
        done_keys = set()
        for row in succeeded:
            target = self._dedup_target(
                row['target_post_id'], row['target_screen_name'])
            if target:
                done_keys.add((row['account_id'][0], row['operation'], target))
        duplicates = Task.browse()
        if done_keys:
            failed = Task.search([
                ('status', '=', 'failed'),
                ('operation', 'in', ops),
            ])
            for task in failed:
                target = self._dedup_target(
                    task.target_post_id, task.target_screen_name)
                if target and (task.account_id.id, task.operation,
                               target) in done_keys:
                    duplicates |= task
        for operation in ops:
            subset = duplicates.filtered(lambda t, op=operation:
                                         t.operation == op)
            if subset:
                subset.write({
                    'status': 'cancelled',
                    'error': 'Duplicate: %s already performed for this target'
                             % operation,
                })
        return self._duplicate_cleanup_notification(len(duplicates))

    @staticmethod
    def _duplicate_cleanup_notification(count):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Cancel Duplicate Failed Tasks',
                'message': ('%s duplicate task(s) cancelled.' % count if count
                            else 'No duplicate failed tasks found.'),
                'type': 'success' if count else 'info',
                'sticky': False,
            },
        }

    def action_ignore_stale(self, age_minutes=None):
        """Mark pending tasks older than the given age as failed.

        Manual cleanup tool: claiming no longer age-gates tasks (every due
        pending task is processed in fair-share order), so this is only for
        tasks you no longer want to run. Tasks created more than
        ``age_minutes`` (default ``_QUEUE_MAX_AGE_MINUTES``) ago are swept to
        ``failed``. Linked ``x.twitter.event`` records still queued are
        marked ``skipped`` so the webhook inbox reflects the same stale
        state.
        """
        minutes = age_minutes or self._QUEUE_MAX_AGE_MINUTES
        cutoff = fields.Datetime.now() - timedelta(minutes=minutes)
        stale = self.sudo().search([
            ('status', 'in', ('pending',)),
            ('create_date', '<', cutoff),
        ])
        stale.write({
            'status': 'failed',
            'error': 'Ignored: stale task older than %d minutes' % minutes,
        })
        event_model = self.env.get('x.twitter.event')
        if event_model:
            event_model.sudo().search([
                ('task_id', 'in', stale.ids),
                ('state', '=', 'queued'),
            ]).write({
                'state': 'skipped',
                'error': 'Ignored: stale task older than %d minutes' % minutes,
            })
        return len(stale)

    def action_process_queue(self, limit=100, max_batches=1000):
        """Manually run the pending-task queue and report messages created.

        Sweeps the queue until no due ``pending`` tasks remain (capped by
        ``max_batches`` so a single click cannot run forever), then reports how
        many tasks were claimed and how many messages were extracted and
        created from the processed webhook events. Each claimed task runs the
        provider operation, which extracts the webhook payload and creates the
        ``x.message`` (and its ``discuss.channel``) via ``_save_x_message``.
        """
        total_claimed = 0
        total_messages = 0
        company_ids = self.env.companies.ids
        for _batch in range(max_batches):
            claimed = self._claim_and_run(limit=limit, company_ids=company_ids)
            if not claimed:
                break
            total_claimed += len(claimed)
            for task in claimed:
                if task.status == 'success' and task.result:
                    total_messages += self._result_message_count(task.result)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Process Queue',
                'message': 'Claimed %s task(s), created %s message(s)' % (
                    total_claimed, total_messages),
                'type': 'success',
                'sticky': False,
            },
        }

    @staticmethod
    def _result_message_count(result):
        if not result:
            return 0
        if isinstance(result, str):
            try:
                data = json.loads(result)
            except (ValueError, TypeError):
                try:
                    data = ast.literal_eval(result)
                except (ValueError, SyntaxError):
                    return 0
        elif isinstance(result, dict):
            data = result
        else:
            return 0
        if not isinstance(data, dict):
            return 0
        return int(data.get('messages') or 0)

    def action_cancel(self):
        self.write({'status': 'cancelled'})
