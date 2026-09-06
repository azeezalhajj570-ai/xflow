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
    error = fields.Text(string='Error', readonly=True)
    result = fields.Text(string='Result', readonly=True)
    backoff_base = fields.Integer(string='Backoff Base (seconds)', default=30)
    task_context = fields.Text(
        string='Task Context',
        help='JSON call kwargs passed to the provider operation. Must not '
             'contain credentials.',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        related='account_id.company_id',
        store=True,
        index=True,
    )

    _MAX_RUNNING_PER_ACCOUNT = 1
    _QUEUE_MAX_AGE_MINUTES = 60

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        for task in res:
            if not task.next_retry_at:
                task.write({'next_retry_at': fields.Datetime.now()})
        return res

    @api.model
    def _process_queue(self, limit=100):
        """Claim and run due tasks (per-account single-flight).

        The single-flight guard must not count the tasks this run has just
        claimed itself: they were already flushed as ``running`` and are
        executed sequentially in this same transaction, so counting them
        throttled claiming to one task per account per sweep (a 13k-event
        backlog at one webhook event per minute). Only genuinely concurrent
        claims — a stale ``running`` task left by another worker — block.
        """
        claimed = self._claim_and_run(limit=limit)
        return len(claimed)

    @api.model
    def _claim_and_run(self, limit=100, company_ids=None):
        """Claim and run due tasks, returning the claimed recordset.

        Shared by the cron worker (`_process_queue`) and the manual "Process
        Queue" action so the caller can inspect per-task results afterwards.

        Fair-share: the sweep is split evenly among every account that has due
        tasks, so a huge backlog on one account (e.g. a broken token) cannot
        starve other accounts — each account is drained in lock-step instead
        of one account monopolizing the ``limit``. Within an account tasks run
        in ``priority desc, create_date asc`` order.

        Company-scoped: when ``company_ids`` is given, only tasks belonging to
        those companies are claimed. No filter keeps ``_process_queue`` (the
        cron, running as ``__system__`` with a single allowed company)
        sweeping the *whole* backlog across accounts of every company, while
        ``action_process_queue`` passes the operator's allowed companies so a
        user's manual run never touches another company's backlog. The sudo'd
        searches below bypass the company record rule, hence the explicit
        domain filter.

        Freshness: only tasks created within the last
        ``_QUEUE_MAX_AGE_MINUTES`` minutes are claimed; older pending backlog
        is ignored so a stale queue (e.g. accumulated during an outage) does
        not get drained at once.
        """
        now = fields.Datetime.now()
        min_create_date = now - timedelta(minutes=self._QUEUE_MAX_AGE_MINUTES)
        domain = [
            ('status', 'in', ('pending',)),
            ('next_retry_at', '<=', now),
            ('create_date', '>=', min_create_date),
        ]
        if company_ids:
            domain.append(('company_id', 'in', list(company_ids)))
        grouped = self.sudo()._read_group(
            domain, ['account_id'], ['account_id:count'], order='account_id')
        account_ids = [account.id for g in grouped for account in g[0]]
        if not account_ids:
            return self.env['x.account.task']
        share = max(limit // len(account_ids), 1)
        candidate_ids = []
        for account_id in account_ids:
            candidate_ids += self.sudo().search([
                ('account_id', '=', account_id),
                ('status', 'in', ('pending',)),
                ('next_retry_at', '<=', now),
                ('create_date', '>=', min_create_date),
            ], order='priority desc, create_date asc', limit=share).ids
        tasks = self.sudo().browse(candidate_ids)
        claimed = self.env['x.account.task']
        for task in tasks:
            account = task.account_id
            running = self.sudo().search_count([
                ('account_id', '=', account.id),
                ('status', '=', 'running'),
                ('id', 'not in', claimed.ids),
            ])
            if running >= self._MAX_RUNNING_PER_ACCOUNT:
                continue
            task.write({'status': 'running', 'claimed_at': now})
            claimed |= task
        for task in claimed:
            task._execute_operation()
        return claimed

    def _execute_operation(self, operation=None, **extra_ctx):
        """Execute one task's operation via the account provider.

        Used by both the cron worker and the automation rule so a task can be
        run immediately when created (auto-execute) or later by the queue.
        """
        self.ensure_one()
        import json as _json
        if self.status == 'cancelled':
            return None
        account = self.account_id
        if not account:
            self._schedule_retry('Missing account')
            return None
        try:
            from odoo.addons.x_account.services.x_service import XService
            provider = XService.get_provider(account)
            op = operation or self.operation
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
            return result
        except Exception as exc:
            self._schedule_retry(str(exc))
            return None

    def _schedule_retry(self, message):
        self.ensure_one()
        self.write({'error': message})
        if self.retry_count < self.max_attempts - 1:
            delay = self.backoff_base * (2 ** self.retry_count)
            self.write({
                'retry_count': self.retry_count + 1,
                'status': 'pending',
                'next_retry_at': fields.Datetime.now() + timedelta(seconds=delay),
            })
        else:
            self.write({'status': 'failed'})

    def action_ignore_stale(self, age_minutes=None):
        """Mark pending tasks older than the freshness window as failed.

        Tasks created more than ``_QUEUE_MAX_AGE_MINUTES`` minutes ago are
        never claimed again (see ``_claim_and_run``), so this action sweeps
        them to ``failed`` to keep the queue readable instead of letting dead
        backlog accumulate forever. Linked ``x.twitter.event`` records still
        queued are marked ``skipped`` so the webhook inbox reflects the same
        stale state.
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
