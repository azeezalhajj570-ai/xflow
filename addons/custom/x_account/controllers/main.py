# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class XAccountController(http.Controller):

    @http.route('/x_account/webhook/<int:account_id>', type='http',
                auth='public', csrf=False, methods=['GET', 'POST'], website=False)
    def omnix_webhook(self, account_id, **kwargs):
        """OmniX webhook receiver.

        GET  -> CRC handshake: answer ?crc_token with the HMAC response token.
        POST -> signed event delivery: verify x-twitter-webhooks-signature,
                then route each event into discuss channels.
        """
        account = request.env['social.account'].sudo().browse(account_id)
        secret = account.x_webhook_secret
        if not secret:
            _logger.warning('Webhook for account %s has no secret set', account_id)
            return http.Response(status=404)

        if request.httprequest.method == 'GET':
            crc_token = request.params.get('crc_token')
            if not crc_token:
                return http.Response(status=400)
            response_token = 'sha256=%s' % base64.b64encode(
                hmac.new(secret.encode(), crc_token.encode(), hashlib.sha256)
                    .digest()).decode()
            return request.make_response(
                json.dumps({'response_token': response_token}),
                headers=[('Content-Type', 'application/json')],
            )

        # POST: verify HMAC over the raw body.
        signature = request.httprequest.headers.get('x-twitter-webhooks-signature', '')
        raw_body = request.httprequest.get_data()
        expected = 'sha256=%s' % base64.b64encode(
            hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(signature, expected):
            _logger.warning('Bad webhook signature for account %s', account_id)
            return http.Response(status=403)

        try:
            payload = json.loads(raw_body.decode('utf-8'))
        except ValueError:
            return http.Response(status=400)

        event = payload.get('event') or payload
        request.env['discuss.channel'].sudo()._handle_x_webhook_event(account, event)
        return request.make_response(
            json.dumps({'status': 'ok'}),
            headers=[('Content-Type', 'application/json')],
        )
