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
    member_ids = fields.Many2many(
        'res.partner',
        string='Members',
    )
    member_pool_ids = fields.Many2many(
        'res.partner',
        string='Selectable Members',
        compute='_compute_member_pool_ids',
        help='Channel group members available for selection.',
    )
    cooldown_sec = fields.Integer(
        string='Cooldown (seconds)',
        default=10,
        help='Time to wait between each follow request.',
    )

    def _followable_members(self, channel=None):
        """Channel group members that can be bulk-followed.

        Excludes members without an ``x_username``, the account's own X
        profile, and members the account already follows (``x_following_ids``).
        """
        channel = channel or self.channel_id
        if not channel:
            return self.env['res.partner']
        members = channel.x_group_member_ids
        account = channel.x_account_id
        if not account:
            return members.filtered('x_username')
        own_user_id = str(account.twitter_user_id or '').strip()
        own_handle = (account.social_account_handle or '').lower().lstrip('@')
        followed_ids = account.sudo().x_following_ids.ids
        result = self.env['res.partner']
        for member in members:
            if not member.x_username:
                continue
            if member.id in followed_ids:
                continue
            username = member.x_username.lower().lstrip('@')
            if own_handle and username == own_handle:
                continue
            if own_user_id and member.x_user_id \
                    and str(member.x_user_id).strip() == own_user_id:
                continue
            result |= member
        return result

    @api.depends('channel_id')
    def _compute_member_pool_ids(self):
        for composer in self:
            composer.member_pool_ids = composer._followable_members()

    @api.model
    def default_get(self, fields_list):
        result = super().default_get(fields_list)
        active_model = self.env.context.get('active_model')
        active_id = self.env.context.get('active_id')
        if active_model == 'discuss.channel' and active_id:
            result['channel_id'] = active_id
            if 'member_ids' in fields_list:
                channel = self.env['discuss.channel'].browse(active_id)
                result['member_ids'] = [
                    (6, 0, self._followable_members(channel).ids)
                ]
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
        members = self.member_ids & self._followable_members()
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