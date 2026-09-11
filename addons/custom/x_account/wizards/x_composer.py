# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class XMessageComposer(models.TransientModel):
    """Send a message directly to an X conversation (1:1 or group).

    Opened from the X Chat form via the "Send Message" button. Sends
    synchronously through the account's provider so the operator gets
    immediate success/error feedback.
    """

    _name = 'x.message.composer'
    _description = 'Send X Message'

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
    conversation_id = fields.Char(
        string='Conversation ID',
        related='channel_id.x_conversation_id',
        readonly=True,
    )
    channel_type = fields.Selection(
        related='channel_id.channel_type',
        readonly=True,
    )
    body = fields.Text(string='Message', required=True)

    @api.model
    def default_get(self, fields_list):
        result = super().default_get(fields_list)
        if 'channel_id' in fields_list:
            active_model = self.env.context.get('active_model')
            active_id = self.env.context.get('active_id')
            if active_model == 'discuss.channel' and active_id:
                result['channel_id'] = active_id
        return result

    def action_send(self):
        self.ensure_one()
        body = (self.body or '').strip()
        if not body:
            raise ValidationError(_('Message text is required.'))
        channel = self.channel_id
        if channel.channel_type not in ('x', 'x_group'):
            raise ValidationError(
                _('Send Message is only available on X conversations, got %r')
                % channel.channel_type)
        account = channel.x_account_id
        if not account or not account.active or account.x_connection_status == 'disabled':
            return self._send_result(
                'Send Message',
                _('No valid X account for conversation %s.') % channel.name,
                kind='danger')
        if channel.channel_type == 'x_group':
            operation = 'send_group_dm'
            conv_id = channel.x_conversation_id
            if not conv_id:
                return self._send_result(
                    'Send Message',
                    _('This group conversation has no conversation id.'),
                    kind='danger')
            kwargs = {'conversation_id': conv_id, 'text': body}
        else:
            operation = 'send_dm'
            recipient_id = channel.x_partner_id.x_user_id
            if not recipient_id:
                return self._send_result(
                    'Send Message',
                    _('Cannot resolve the recipient X user id for this conversation.'),
                    kind='danger')
            kwargs = {'recipient_id': recipient_id, 'text': body}
        provider = account.get_provider_for_operation(operation)
        fn = getattr(provider, operation, None)
        if not fn or not callable(fn):
            return self._send_result(
                'Send Message',
                _('Provider %s does not support %s.') % (account.x_provider, operation),
                kind='warning')
        try:
            fn(**kwargs)
        except Exception as exc:
            _logger.warning(
                'x.message.composer: %s failed for channel %s: %s',
                operation, channel.id, exc)
            return self._send_result(
                'Send Message',
                _('Sending failed: %s') % exc,
                kind='danger')
        _logger.info(
            'x.message.composer: %s sent to channel %s via account %s',
            operation, channel.id, account.id)
        return self._send_result(
            'Send Message',
            _('Message sent.'),
            kind='success')

    @staticmethod
    def _send_result(title, message, kind='success'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': kind,
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }