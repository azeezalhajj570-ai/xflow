# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Public X webhook receiver (V2 Webhooks API + X Activity API).

Route: ``GET|POST /x_account/twitter/webhook``

- ``GET ?crc_token=<token>`` — X's Challenge-Response Check: reply with the
  HMAC-SHA256 ``response_token`` so X knows this endpoint is ours.
- ``POST`` — X Activity API event delivery, verified by the
  ``x-twitter-webhooks-signature`` header (HMAC-SHA256 over the raw body, keyed
  with the app consumer secret). The body is parsed, de-duplicated by
  ``data.event_uuid`` and enqueued as an ``x.account.task`` so this handler
  returns immediately (X retries on timeout/5xx).

Security: every POST is signature-verified; invalid signatures get a bare 403.
Ingress logging is metadata-only (length, SHA-256 digest, JSON shape and event
type) so diagnostics can compare the signed raw body with the later opaque
XChat event without logging message content or credentials.
"""

import json
import logging
import hashlib

from odoo import http
from odoo.http import request

from odoo.addons.x_account_twitter.services.twitter_activity import TwitterActivity
from odoo.addons.x_account_twitter.services.twitter_webhook import TwitterWebhook

_logger = logging.getLogger(__name__)


class TwitterWebhookController(http.Controller):

    @http.route('/x_account/twitter/webhook', type='http', auth='public',
                csrf=False, methods=['GET', 'POST'], website=False)
    def webhook(self, **kwargs):
        req = request.httprequest
        if req.method == 'GET':
            return self._handle_crc(kwargs.get('crc_token'))
        return self._handle_event(req)

    # ---------------------------------------------------------------- helpers
    def _handle_crc(self, crc_token):
        if not crc_token:
            return http.Response('crc_token_missing', status=400)
        service = TwitterWebhook(request.env)
        if not service.has_consumer_secret:
            return http.Response('not_configured', status=400)
        try:
            payload = service.crc_response(crc_token)
        except Exception:
            return http.Response('not_configured', status=400)
        return http.Response(
            json.dumps(payload), headers={'Content-Type': 'application/json'},
            status=200)

    def _handle_event(self, req):
        raw_body = req.get_data(as_text=False)
        signature = req.headers.get('x-twitter-webhooks-signature')
        source = req.remote_addr or 'unknown'
        body_digest = hashlib.sha256(raw_body).hexdigest()[:16]
        _logger.info('x_account_twitter: webhook POST received method=%s path=%s '
                     'from=%s signature_present=%s content_type=%s body_bytes=%s '
                     'body_sha256=%s',
                     req.method, req.path,
                     source,
                     bool(signature),
                     req.content_type,
                     len(raw_body), body_digest)
        service = TwitterWebhook(request.env)
        if not service.verify_signature(raw_body, signature):
            # Fail-closed: reject without echoing the body.
            _logger.warning('x_account_twitter: rejecting webhook with invalid '
                            'or missing signature from=%s', source)
            return http.Response('invalid_signature', status=403)
        envelope = self._json_load(raw_body)
        if envelope is None:
            _logger.warning('x_account_twitter: received non-JSON webhook body '
                            'from=%s body_bytes=%s body_sha256=%s', source,
                            len(raw_body), body_digest)
            return http.Response('accepted', status=200)
        data = envelope.get('data') if isinstance(envelope, dict) else None
        _logger.info('x_account_twitter: parsed webhook envelope from=%s '
                     'json_type=%s keys=%s event_type=%s event_uuid_present=%s',
                     source, type(envelope).__name__,
                     sorted(envelope) if isinstance(envelope, dict) else [],
                     data.get('event_type') if isinstance(data, dict) else None,
                     bool(data.get('event_uuid')) if isinstance(data, dict) else False)
        result = TwitterActivity(request.env).ingest_webhook(envelope)
        _logger.info('x_account_twitter: webhook event result=%s',
                     json.dumps(result))
        return http.Response('accepted', status=200)

    @staticmethod
    def _json_load(raw_body):
        try:
            return json.loads(raw_body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None
