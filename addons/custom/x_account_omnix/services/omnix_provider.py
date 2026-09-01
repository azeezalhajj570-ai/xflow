# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""OmniXProvider: optional X provider via the OmniX REST API.

OmniX acts as a real X account using the account's ``auth_token`` cookie
(already stored encrypted in ``x.session.store``) plus a shared Bearer API key
from ``ir.config_parameter``. Implements the XProvider surface (validate,
DMs, group automation ops, webhooks) and is a per-account either/or alternative
to SessionWebProvider.

SOLID layering:
- transport:        :class:`OmniXHttpClient` (requests, headers, status)
- parsing:          :class:`OmniXEnvelopeParser` (envelope → DTOs)
- error taxonomy:   :mod:`omnix_errors` (HTTP status → classified error)
- group sync:       :class:`OmniXGroupSync` (channels + partners)
- composition:      this class wires the above behind the ``XProvider`` contract
"""

import logging

from . import omnix_envelope
from .omnix_group_sync import OmniXGroupSync
from .omnix_http_client import OmniXHttpClient

_LOGGER = logging.getLogger(__name__)

# Explicit allowlist: all message (DM) + tweet + follow events. Without it
# OmniX may deliver only a default subset.
_WEBHOOK_EVENTS = [
    'message.received', 'message.sent', 'message.edited',
    'message.deleted', 'message.reactionAdded',
    'message.reactionRemoved',
    'tweet.mention', 'tweet.reply', 'tweet.quote', 'tweet.like',
    'tweet.retweet', 'user.follow',
]


class OmniXProvider:
    """Composition root: XProvider contract implemented over the OmniX REST API."""

    _needs_cookies = True

    def __init__(self, env, account, cookies):
        self.env = env
        self.account = account
        self.cookies = cookies or {}
        self._api_key = env['ir.config_parameter'].sudo().get_param(
            'x_account.omnix_api_key')
        self._encryption_code = account.x_encryption_code or ''
        self._client = OmniXHttpClient(
            self._api_key, self.cookies.get('auth_token'))
        self._group_sync = OmniXGroupSync(env, self._client)

    # ------------------------------------------------------------- validation
    def validate_session(self):
        """Return dict {valid, user, reason, status}.

        When the account already has a handle, calls GET /user/info for it.
        When the handle is missing (e.g. a token-only import), resolves the
        current user from the auth_token via GET /user/home_timeline (which
        returns the authed userId) followed by GET /user/info_by_id to obtain
        the username/name. A successful envelope validates the session.
        """
        if not self._api_key:
            return {'valid': False, 'user': None, 'reason': 'omnix_api_key_missing'}
        if not self.cookies.get('auth_token'):
            return {'valid': False, 'user': None, 'reason': 'Missing auth_token cookie'}
        try:
            handle = self.account.social_account_handle
            if handle:
                data = self._client.request(
                    'GET', '/user/info', params={'userName': handle})
                user = omnix_envelope.OmniXEnvelopeParser.user(data)
                if not user:
                    return {'valid': False, 'user': None,
                            'reason': 'Response missing user ID'}
                return {'valid': True, 'user': user, 'reason': 'omnix'}
            return self._validate_by_token()
        except RuntimeError as exc:
            return {'valid': False, 'user': None, 'reason': str(exc)}

    def _validate_by_token(self):
        """Resolve the authed user from the token when no handle is known."""
        home = self._client.request('POST', '/user/home_timeline')
        user_id = omnix_envelope.OmniXEnvelopeParser.home_user_id(home)
        if not user_id:
            return {'valid': False, 'user': None,
                    'reason': 'Token did not resolve to a user'}
        info = self._client.request(
            'GET', '/user/info_by_id', params={'userId': str(user_id)})
        user = omnix_envelope.OmniXEnvelopeParser.user(info)
        if not user:
            return {'valid': False, 'user': None,
                    'reason': 'Response missing user ID'}
        return {'valid': True, 'user': user, 'reason': 'omnix_token'}

    # ------------------------------------------------------------------- dm
    def get_conversations(self, limit=50, cursor=None):
        params = {}
        if cursor:
            params['cursor'] = cursor
        data = self._client.request('GET', '/dm/list', params=params)
        return omnix_envelope.OmniXEnvelopeParser.conversations(data, limit=limit)

    def get_dms(self, conversation_id, limit=100, cursor=None):
        body = {'conversation_id': conversation_id, 'count': int(limit)}
        if self._encryption_code:
            body['encryption_code'] = self._encryption_code
        if cursor:
            body['cursor'] = cursor
        data = self._client.request('POST', '/dm/conversation', body=body)
        return omnix_envelope.OmniXEnvelopeParser.messages(
            data, conversation_id, limit=limit)

    # ------------------------------------------------------- group members
    def fetch_groups(self, account, limit=100):
        """Fetch group-DM conversations + members and sync them into discuss."""
        return self._group_sync.fetch_groups(account, limit=limit)

    def fetch_group_messages(self, account, limit=100):
        """Fetch messages from X group-DM conversations and store them."""
        return self._group_sync.fetch_group_messages(account, limit=limit)

    def send_dm(self, recipient_id, text):
        if not recipient_id:
            raise ValueError('recipient_id is required')
        if not text:
            raise ValueError('text must be non-empty')
        data = self._client.request('POST', '/dm/send', body={
            'recipient_id': str(recipient_id),
            'text': text,
        })
        message = (data or {}).get('data') or {}
        return {
            'message_id': message.get('id') or message.get('message_id', ''),
            'created_at': message.get('created_at'),
        }

    def get_events(self, **kwargs):
        """Stub/placeholder: events are derived from get_conversations/get_dms."""
        return {'events': []}

    # -------------------------------------------------- group automation ops
    def like(self, tweet_id, **kwargs):
        if not tweet_id:
            raise ValueError('tweet_id is required')
        data = self._client.request('POST', '/tweet/favorite',
                                    body={'tweet_id': str(tweet_id)})
        return {'tweet_id': str(tweet_id),
                'liked': bool((data.get('data') or {}).get('favorited', True))}

    def comment(self, tweet_id, text, **kwargs):
        if not tweet_id or not text:
            raise ValueError('tweet_id and text are required')
        data = self._client.request('POST', '/tweet/create', body={
            'tweet_id': str(tweet_id),
            'text': text,
        })
        return {'tweet_id': str(tweet_id),
                'comment_id': (data.get('data') or {}).get('id', '')}

    def repost(self, tweet_id, **kwargs):
        if not tweet_id:
            raise ValueError('tweet_id is required')
        data = self._client.request('POST', '/tweet/retweet',
                                    body={'tweet_id': str(tweet_id)})
        return {'tweet_id': str(tweet_id),
                'retweeted': bool((data.get('data') or {}).get('retweeted', True))}

    def follow(self, screen_name, **kwargs):
        if not screen_name:
            raise ValueError('screen_name is required')
        screen_name = str(screen_name).lstrip('@')
        data = self._client.request('POST', '/user/follow',
                                    body={'userName': screen_name})
        return {'screen_name': screen_name,
                'followed': bool((data.get('data') or {}).get('followed', True))}

    def post_tweet(self, text, **kwargs):
        if not text:
            raise ValueError('text is required')
        data = self._client.request('POST', '/tweet/create', body={'text': text})
        return {'tweet_id': (data.get('data') or {}).get('id', '')}

    # ---------------------------------------------------------------- webhooks
    def register_webhook(self, url, secret=None, events=None):
        """Register an OmniX webhook for this account.

        Returns {id, url, valid, secret}. OmniX runs a CRC handshake on
        registration; the webhook only becomes valid once the receiver answers.
        Passing the account's XChat encryption code opts the webhook into DM
        (direct message) events, which are otherwise not delivered. The
        account's auth_token is required by OmniX at registration.
        """
        body = {'url': url}
        if self.cookies.get('auth_token'):
            body['auth_token'] = self.cookies.get('auth_token')
        if secret:
            body['secret'] = secret
        if self._encryption_code:
            body['encryption_code'] = self._encryption_code
        body['events'] = events or _WEBHOOK_EVENTS
        data = self._client.request('POST', '/webhooks', body=body)
        return omnix_envelope.OmniXEnvelopeParser.webhook(data, fallback_url=url)

    def list_webhooks(self):
        """Return the webhooks registered for this account."""
        data = self._client.request('GET', '/webhooks')
        return omnix_envelope.OmniXEnvelopeParser.webhook_list(data)

    def validate_webhook(self, webhook_id):
        """Re-run the CRC handshake for a webhook id."""
        data = self._client.request('PUT', '/webhooks/%s' % webhook_id)
        return (data or {}).get('data') or {}

    def delete_webhook(self, webhook_id):
        """Delete a webhook. The watcher stops and no further events arrive."""
        data = self._client.request('DELETE', '/webhooks/%s' % webhook_id)
        return (data or {}).get('data') or {}

    def replay_webhook(self, webhook_id, from_date=None, to_date=None):
        """Ask OmniX to re-send a webhook's events from a time window.

        Re-delivers events for the webhook (up to the past 24h). from_date /
        to_date are UTC 'yyyymmddhhmm' strings; both default to the last 24h.
        Delivery is async — returns a job_id.
        """
        from datetime import datetime, timedelta, timezone
        if not from_date:
            from_date = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime('%Y%m%d%H%M')
        if not to_date:
            to_date = datetime.now(timezone.utc).strftime('%Y%m%d%H%M')
        data = self._client.request('POST', '/webhooks/replay', body={
            'webhook_id': str(webhook_id),
            'from_date': from_date,
            'to_date': to_date,
        })
        return (data or {}).get('data') or {}


# Register the provider with x_account's XProviderRegistry at import time (OCP):
# x_account stays closed for modification (no built-in omnix entry) and open for
# extension — installing this module adds the 'omnix' option.
def _register_omnix_provider():
    try:
        from odoo.addons.x_account.services.x_provider import XProviderRegistry
        XProviderRegistry.register('omnix', __name__ + '.OmniXProvider')
    except (ImportError, AttributeError):
        _LOGGER.exception('Failed to register OmniX provider')


_register_omnix_provider()
