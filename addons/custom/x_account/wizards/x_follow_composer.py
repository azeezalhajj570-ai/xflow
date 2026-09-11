# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class XFollowComposer(models.TransientModel):
    """Bulk-follow the members of an X group conversation.

    Opened from the group chat form via the "Follow Members" button. Enqueues
    one ``x.account.task`` per selected member, staggered by the configured
    cooldown so each follow call is spaced out on the account.
    """

    _name = 'x.follow.composer'
    _description = 'Bulk Follow X Members'

    channel_id = fields.Many2one(
        'discuss.channel',
        string='Conversation',
        required=True,
        ondelete='cascade',
    )
    account_id = fields.Many2one(
        'social.account',
        string='X Account',
        related='channel_id.x_account_id',
        readonly=True,
    )
    line_ids = fields.One2many(
        'x.follow.composer.line',
        'composer_id',
        string='Members',
    )
    cooldown_sec = fields.Integer(
        string='Cooldown (seconds)',
        default=10,
        help='Time to wait between each follow request.',
    )

    @api.model
    def default_get(self, fields_list):
        result = super().default_get(fields_list)
        active_model = self.env.context.get('active_model')
        active_id = self.env.context.get('active_id')
        if active_model == 'discuss.channel' and active_id:
            result['channel_id'] = active_id
            if 'line_ids' in fields_list:
                channel = self.env['discuss.channel'].browse(active_id)
                lines = [(5, 0, 0)]
                for member in channel.x_group_member_ids:
                    lines.append((0, 0, {
                        'partner_id': member.id,
                        'do_follow': bool(member.x_username),
                    }))
                result['line_ids'] = lines
        return result

    def action_follow(self):
        self.ensure_one()
        channel = self.channel_id
        if channel.channel_type not in ('x', 'x_group'):
            raise ValidationError(
                _('Bulk follow is only available on X conversations, got %r')
                % channel.channel_type)
        account = channel.x_account_id
        if not account or not account.active or account.x_connection_status == 'disabled':
            return self._follow_result(
                _('No valid X account for conversation %s.') % channel.name,
                kind='danger')
        members = self.line_ids.filtered(
            lambda line: line.do_follow and line.partner_id.x_username
        ).mapped('partner_id')
        if not members:
            return self._follow_result(
                _('Select at least one member with an X username.'),
                kind='warning')
        cooldown = max(self.cooldown_sec or 0, 0)
        now = fields.Datetime.now()
        tasks = self.env['x.account.task'].sudo()
        for index, member in enumerate(members):
            task_ctx = {
                'screen_name': member.x_username,
                'channel_id': channel.id,
                'source': 'bulk_follow',
            }
            tasks |= self.env['x.account.task'].sudo().create({
                'account_id': account.id,
                'operation': 'follow',
                'priority': 1,
                'task_context': json.dumps(task_ctx),
                'next_retry_at': now + timedelta(seconds=index * cooldown),
            })
        _logger.info(
            'Bulk follow: enqueued %s task(s) for channel %s, account %s, '
            'cooldown %ss', len(tasks), channel.id, account.id, cooldown)
        if cooldown:
            message = _('Enqueued %s follow request(s), one every %s seconds.')
            message = message % (len(tasks), cooldown)
        else:
            message = _('Enqueued %s follow request(s).') % len(tasks)
        return self._follow_result(message, kind='success')

    @staticmethod
    def _follow_result(message, kind='success'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Follow Members',
                'message': message,
                'type': kind,
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }


class XFollowComposerLine(models.TransientModel):
    """A group member line in the bulk-follow wizard."""

    _name = 'x.follow.composer.line'
    _description = 'Bulk Follow Member Line'

    composer_id = fields.Many2one(
        'x.follow.composer',
        string='Wizard',
        ondelete='cascade',
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Member',
        required=True,
        ondelete='cascade',
    )
    name = fields.Char(string='Name', related='partner_id.name', readonly=True)
    x_username = fields.Char(
        string='Username', related='partner_id.x_username', readonly=True)
    do_follow = fields.Boolean(string='Follow', default=True)