# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import timedelta

from odoo import _, api, fields, models


class XAccountGroup(models.Model):
    """A named collection of external X accounts with automation actions.

    The investigation found no existing Odoo business-grouping model suitable for
    grouping X accounts with automation actions, so this minimal model is required.
    """

    _name = 'x.account.group'
    _description = 'X Account Group'

    name = fields.Char(string='Name', required=True)
    description = fields.Text(string='Description')
    account_ids = fields.Many2many(
        'social.account',
        string='X Accounts',
        help='X accounts belonging to this group.',
    )
    actions = fields.Selection(
        [
            ('like', 'Like'),
            ('comment', 'Comment'),
            ('repost', 'Repost'),
            ('follow', 'Follow'),
        ],
        string='Automation Action',
        default='like',
        help='Action automated for the group.',
    )
    auto_execute = fields.Boolean(
        string='Auto Execute',
        help='Enqueue tasks automatically when rules fire.',
    )
    cooldown_sec = fields.Integer(
        string='Cooldown (seconds)',
        default=60,
        help='Minimum interval between automated actions per account.',
    )
    paused = fields.Boolean(string='Paused')
    last_executed_at = fields.Datetime(string='Last Executed At', readonly=True)

    def _enqueue_group_operation(self, target_id=None, operation=None, **ctx):
        """Enqueue one task per account in the group for the given operation.

        Automation invokes this (via base_automation server action). It never
        performs X HTTP itself; it only enqueues x.account.task records that the
        cron worker executes per-account with single-flight.

        Cooldown is enforced per account: if an account has a task for this
        operation that ran within cooldown_sec, it is skipped.
        """
        for group in self:
            if group.paused or not group.auto_execute:
                continue
            op = operation or group.actions
            if not op:
                continue
            now = fields.Datetime.now()
            self._cr.execute("""
                SELECT account_id, count(*)
                FROM x_account_task
                WHERE status != 'cancelled'
                  AND operation = %s
                  AND create_date >= %s
                GROUP BY account_id
            """, (op, now - timedelta(seconds=group.cooldown_sec or 0)))
            recent = dict(self._cr.fetchall())
            task_ctx = {
                'target_id': target_id,
                'group_id': group.id,
                **ctx,
            }
            for account in group.account_ids:
                if not account.active or account.x_connection_status == 'disabled':
                    continue
                if recent.get(account.id, 0):
                    continue
                self.env['x.account.task'].create({
                    'account_id': account.id,
                    'group_id': group.id,
                    'operation': op,
                    'priority': 1,
                    'task_context': self._stringify_task_context(task_ctx),
                })
            # Update last_executed_at via raw SQL so it does not re-trigger the
            # on_create_or_write base_automation rule (would recurse forever).
            self._cr.execute(
                'UPDATE x_account_group SET last_executed_at = %s WHERE id = %s',
                (now, group.id))

    @api.model
    def _stringify_task_context(self, ctx):
        import json as _json
        return _json.dumps(ctx)
