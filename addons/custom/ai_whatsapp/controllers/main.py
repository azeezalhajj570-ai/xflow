import logging

from odoo import _, http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AIWhatsAppController(http.Controller):

    @http.route('/ai_whatsapp/forward_operator', methods=['POST'], type='jsonrpc', auth='user')
    def forward_operator(self, channel_id):
        channel = request.env['discuss.channel'].sudo().search([
            ('id', '=', channel_id),
            ('channel_type', '=', 'whatsapp'),
        ])
        if not channel:
            return {'success': False, 'notification': _("Channel not found.")}
        if channel.current_handler_type == 'human':
            return {'success': False, 'notification': _("Already handled by a human.")}
        channel.sudo().action_human_takeover()
        return {
            'success': True,
            'notification': _("You have taken over this conversation."),
            'notification_type': 'success',
        }
