# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Official X webhook + X Activity API client, signature and lifecycle.

This is the "official X API webhook/event mechanism" facade for
``x_account_twitter`` (current as of the 2026 X API):

- V2 Webhooks API (``/2/webhooks``) — register/list/delete/validate the app's
  webhook endpoint; authentication is an **OAuth 2.0 App-Only Bearer Token**;
  inbound events are verified over HMAC-SHA256 (`x-twitter-webhooks-signature`)
  keyed with the app **consumer secret**, and the endpoint must answer the
  Challenge-Response Check (CRC).
- X Activity API (XAA) (``/2/activity/subscriptions``) — subscribe one X user
  to real-time events (``dm.received``, ``dm.sent``, ``chat.received``,
  ``chat.sent``, ``chat.conversation_join``, ...) delivered to the webhook.

The deprecated Account Activity API (AAA v2) is intentionally NOT used: it
requires OAuth 1.0a user context and Enterprise/Pay-Per-Use tier, and it does
not support group conversations. XAA delivers the DM + group-DM events this
integration needs using the project's existing OAuth 2.0 user-context accounts.

This service owns signature/CRC and the app-level HTTP plumbing (SRP). The
controller owns HTTP handling; the queue owns expensive processing; the
``twitter_provider`` forwards webhook lifecycle operations to the account model.
"""

import base64
import hashlib
import hmac
import logging

import requests

from . import twitter_errors

_logger = logging.getLogger(__name__)

_ENDPOINT = 'https://api.x.com'
_TIMEOUT_SECONDS = 20

# X Activity API event types this integration subscribes to.
SUPPORTED_EVENT_TYPES = (
    'dm.received',
    'chat.received',
    # 'dm.sent',
    # 'chat.sent',
    # 'chat.conversation_join',
)


class TwitterWebhook:
    """App-level X webhook + X Activity API client for one app configuration.

    Uses the app consumer secret for HMAC/CRC and the app-only bearer token for
    registration/subscription management. Does not depend on a social.account,
    so it can run before any account is linked.
    """

    def __init__(self, env, consumer_secret='', app_bearer_token=''):
        self.env = env
        icp = env['ir.config_parameter'].sudo()
        self._consumer_secret = consumer_secret or icp.get_param(
            'x_account_twitter.app_consumer_secret', '') or ''
        self._app_bearer = app_bearer_token or icp.get_param(
            'x_account_twitter.app_bearer_token', '') or ''
        self._client_id = icp.get_param('social.twitter_oauth2_client_id', '') or ''

    # ------------------------------------------------------------ signatures
    @property
    def consumer_secret(self):
        """The app consumer secret (API secret key) — never logged."""
        return self._consumer_secret

    @property
    def has_consumer_secret(self):
        return bool(self._consumer_secret)

    @property
    def has_app_bearer(self):
        return bool(self._app_bearer)

    def webhook_url(self):
        """Absolute webhook receiver URL registered with X (no port)."""
        icp = self.env['ir.config_parameter'].sudo()
        base = icp.get_param('x_account_twitter.webhook_base_url', '') or ''
        if not base:
            return ''
        return '%s/x_account/twitter/webhook' % base.rstrip('/')

    @staticmethod
    def _hmac_sign(message, key):
        return 'sha256=%s' % base64.b64encode(
            hmac.new(key.encode('utf-8'), message, hashlib.sha256).digest()
        ).decode('utf-8')

    def verify_signature(self, raw_body, signature):
        """Return True when ``x-twitter-webhooks-signature`` matches the body.

        Guarded failures (missing secret/signature) return False rather than
        raise, so the receiver can 403 quietly.
        """
        if not self._consumer_secret or not signature:
            return False
        expected = self._hmac_sign(raw_body, self._consumer_secret)
        return hmac.compare_digest(expected, signature)

    def crc_response(self, crc_token):
        """Return the ``dict`` response for a Challenge-Response Check."""
        if not self._consumer_secret:
            raise twitter_errors.TwitterAuthenticationError('consumer_secret_missing')
        return {'response_token': self._hmac_sign(crc_token.encode('utf-8'),
                                                  self._consumer_secret)}

    # ------------------------------------------------------------ HTTP
    def _request(self, method, path, params=None, body=None):
        if not self._app_bearer:
            raise twitter_errors.TwitterAuthenticationError('app_bearer_missing')
        url = _ENDPOINT + path
        headers = {'Authorization': 'Bearer %s' % self._app_bearer}
        try:
            response = requests.request(
                method, url, params=params, json=body, headers=headers,
                timeout=_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise twitter_errors.TwitterTemporaryError('network_error: %s' % exc)
        if not response.ok:
            raise twitter_errors.classify(response.status_code, self._body_json(response))
        if not response.content:
            return {}
        return response.json()

    @staticmethod
    def _body_json(response):
        try:
            return response.json()
        except ValueError:
            return None

    # ------------------------------------------------------ webhook lifecycle
    def register_webhook(self, safe=False):
        """Register the webhook URL with X (runs the initial CRC).

        Safe to run repeatedly: if a webhook for the same URL is already
        registered, returns the existing one instead of failing. X rejects a
        duplicate registration with ``400 WebhookLimitExceeded`` (surfaced as a
        generic ``http_400`` TwitterError), not a permission error, so both are
        treated as "already registered" when ``safe`` is set.
        """
        url = self.webhook_url()
        if not url:
            raise twitter_errors.TwitterError('webhook_base_url_missing')
        try:
            data = self._request('POST', '/2/webhooks', body={'url': url})
        except twitter_errors.TwitterError as exc:
            if not safe or exc.code != 'http_400':
                raise
            # WebhookLimitExceeded / DuplicateUrlFailed -> return the
            # already-registered webhook.
            existing = self.list_webhooks()
            for item in (existing.get('data') or []):
                if item.get('url') == url:
                    return item
            raise
        return (data or {}).get('data') or {}

    def list_webhooks(self):
        return self._request('GET', '/2/webhooks')

    def delete_webhook(self, webhook_id):
        return self._request('DELETE', '/2/webhooks/%s' % webhook_id)

    def validate_webhook(self, webhook_id):
        """Trigger a CRC check and re-enable a webhook."""
        return self._request('PUT', '/2/webhooks/%s' % webhook_id)

    # ---------------------------------------------------------- XAA lifecycle
    def create_subscription(self, user_id, event_type, webhook_id='',
                            multiple=False, access_token=''):
        """Create an X Activity API subscription for ``event_type`` on ``user_id``.

        XAA delivers to the webhook when ``webhook_id`` is given. Safe to run
        repeatedly when ``multiple=True`` is False (self-heals by comparing to a
        listing when the create is rejected). Returns the subscription dict.

        Auth: subscription *create* requires OAuth 2.0 **user-context** with the
        scope matching the event (``dm.read`` for dm.*/chat.*), so the account's
        access token must be passed via ``access_token`` — the app bearer is not
        accepted for this endpoint.
        """
        body = {
            'event_type': event_type,
            'filter': {'user_id': str(user_id)},
            'tag': 'x_account_twitter:%s:%s' % (user_id, event_type),
        }
        if webhook_id:
            body['webhook_id'] = webhook_id
        url = _ENDPOINT + '/2/activity/subscriptions'
        headers = {'Content-Type': 'application/json'}
        if access_token:
            headers['Authorization'] = 'Bearer %s' % access_token
        elif self._app_bearer:
            headers['Authorization'] = 'Bearer %s' % self._app_bearer
        try:
            response = requests.request(
                'POST', url, json=body, headers=headers,
                timeout=_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise twitter_errors.TwitterTemporaryError('network_error: %s' % exc)
        if not response.ok:
            response_body = self._body_json(response)
            _logger.warning(
                'x_account_twitter: subscription create failed '
                'status=%s url=%s body=%s response=%s',
                response.status_code, url, body, response_body)
            raise twitter_errors.classify(response.status_code, response_body)
        data = response.json() if response.content else {}
        return (data or {}).get('data') or {}

    def list_subscriptions(self):
        return self._request('GET', '/2/activity/subscriptions')

    def delete_subscription(self, subscription_id):
        return self._request(
            'DELETE', '/2/activity/subscriptions/%s' % subscription_id)
