# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json

from odoo import http
from odoo.http import request


class XAccountController(http.Controller):

    @http.route('/x_account/inbound', type='json', auth='public', csrf=False,
                methods=['POST'])
    def inbound_webhook(self, **kwargs):
        """Receive inbound X events (e.g. DMs) and route into discuss channels."""
        data = request.get_json_data() or {}
        payload = data.get('payload') or data
        env = request.env
        events = payload.get('events') or []
        for event in events:
            env['discuss.channel'].sudo()._handle_x_inbound_event(event)
        return {'status': 'ok'}
