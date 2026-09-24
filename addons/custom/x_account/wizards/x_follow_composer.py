# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging
import random
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class XFollowComposer(models.TransientModel):
    """Bulk-follow the members of an X group conversation.

    Opened from the group chat form via the "Follow Members" button. Enqueues
    one ``x.account.task`` per selected member, each scheduled after a random
    pause drawn from a min/max range so the follow calls never follow a fixed
    interval pattern.
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
    min_delay_sec = fields.Integer(
        string='Minimum Delay (seconds)',
        default=60,
        required=True,
        help='Lower bound of the random pause between two follow requests.',
    )
    max_delay_sec = fields.Integer(
        string='Maximum Delay (seconds)',
        default=300,
        required=True,
        help='Upper bound of the random pause between two follow requests.',
    )

    @api.constrains('min_delay_sec', 'max_delay_sec')
    def _check_delay_range(self):
        for wizard in self:
            if wizard.min_delay_sec < 0:
                raise ValidationError(
                    _('Minimum delay cannot be negative.'))
            if wizard.max_delay_sec < wizard.min_delay_sec:
                raise ValidationError(_(
                    'Maximum delay must be greater than or equal to the '
                    'minimum delay.'))

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
        if not account or not account.active or account.x_connection_state == 'disabled':
            return self._follow_result(
                _('No valid X account for conversation %s.') % channel.name,
                kind='danger')
        members = self.member_ids & self._followable_members()
        if not members:
            return self._follow_result(
                _('Select at least one member with an X username.'),
                kind='warning')
        min_delay = max(self.min_delay_sec or 0, 0)
        max_delay = max(self.max_delay_sec or min_delay, min_delay)
        now = fields.Datetime.now()
        # Follow in a random order so the sequence never mirrors the member list.
        ordered = self.env['res.partner'].browse(
            random.sample(members.ids, len(members)))
        tasks = self.env['x.account.task'].sudo()
        offset = 0
        for index, member in enumerate(ordered):
            if index:
                offset += random.randint(min_delay, max_delay)
            task_ctx = {
                'screen_name': member.x_username,
                'channel_id': channel.id,
                'source': 'bulk_follow',
                'sequence': index,
                'min_delay_sec': min_delay,
                'max_delay_sec': max_delay,
                'scheduled_offset_sec': offset,
            }
            tasks |= self.env['x.account.task'].sudo().create({
                'account_id': account.id,
                'operation': 'follow',
                'priority': 1,
                'task_context': json.dumps(task_ctx),
                'next_retry_at': now + timedelta(seconds=offset),
            })
        _logger.info(
            'Bulk follow: enqueued %s task(s) for channel %s, account %s, '
            'random delay %s-%ss', len(tasks), channel.id, account.id,
            min_delay, max_delay)
        message = _(
            'Enqueued %s follow request(s), spaced randomly between %s and %s '
            'seconds.') % (len(tasks), min_delay, max_delay)
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