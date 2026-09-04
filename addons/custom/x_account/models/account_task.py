# Part of Odoo. See LICENSE file for full copyright and licensing details.

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
    _order = 'priority desc, create_date asc'

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
        now = fields.Datetime.now()
        domain = [
            ('status', 'in', ('pending',)),
            ('next_retry_at', '<=', now),
        ]
        tasks = self.sudo().search(domain, order='priority desc, create_date asc', limit=limit)
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
        return len(claimed)

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

    def action_cancel(self):
        self.write({'status': 'cancelled'})
