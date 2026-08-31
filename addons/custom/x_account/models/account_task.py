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
        """Claim and run due tasks (per-account single-flight)."""
        import json as _json

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
            ])
            if running >= self._MAX_RUNNING_PER_ACCOUNT:
                continue
            task.write({'status': 'running', 'claimed_at': now})
            claimed |= task
        for task in claimed:
            try:
                from odoo.addons.x_account.services.x_service import XService
                provider = XService.get_provider(task.account_id)
                fn = getattr(provider, task.operation, None)
                if not fn or not callable(fn):
                    task._schedule_retry('Unknown operation %s' % task.operation)
                    continue
                try:
                    ctx = _json.loads(task.task_context or '{}')
                except ValueError:
                    ctx = {}
                result = fn(**{k: v for k, v in ctx.items() if k != 'self'})
                task.write({'status': 'success', 'result': result})
            except Exception as exc:
                task._schedule_retry(str(exc))
        return len(claimed)

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
