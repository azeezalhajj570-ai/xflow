# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging
import random

import requests

from odoo import fields

_logger = logging.getLogger(__name__)

_DEFAULT_BEARER = (
    'AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8'
    'LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA'
)

_WEB_BASE = 'https://x.com'
_REST_BASE = 'https://x.com/i/api'

_USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]


class SessionWebProvider:
    """Isolated compatibility provider that reproduces the required XAction
    web-session behavior and is replaceable.

    Ported from XAction's native-fetch cookie-session client (no Puppeteer).
    All X HTTP calls from Odoo must go through XService -> XProvider -> this.
    """

    def __init__(self, env, account, cookies):
        self.env = env
        self.account = account
        self.cookies = cookies or {}
        self._bearer = env['ir.config_parameter'].sudo().get_param(
            'x_account.web_bearer_token', _DEFAULT_BEARER)

    # ------------------------------------------------------------------ utils
    @staticmethod
    def parse_cookie_string(cookie_string):
        cookies = {}
        if not cookie_string:
            return cookies
        for pair in cookie_string.split(';'):
            pair = pair.strip()
            if '=' not in pair:
                continue
            name, value = pair.split('=', 1)
            if name:
                cookies[name.strip()] = value.strip()
        return cookies

    def _cookie_header(self):
        return '; '.join('%s=%s' % (k, v) for k, v in self.cookies.items())

    def _headers(self, authenticated=True):
        headers = {
            'authorization': 'Bearer %s' % self._bearer,
            'accept': 'application/json',
            'accept-language': 'en-US,en;q=0.9',
            'content-type': 'application/json',
            'user-agent': random.choice(_USER_AGENTS),
            'x-twitter-active-user': 'yes',
            'x-twitter-client-language': 'en',
        }
        if authenticated:
            headers['cookie'] = self._cookie_header()
            headers['x-csrf-token'] = self.cookies.get('ct0', '')
            headers['x-twitter-auth-type'] = 'OAuth2Session'
        return headers

    # ------------------------------------------------------------- validation
    def validate_session(self):
        """Return dict {valid, user, reason, status}. Port of auth.validateSession."""
        if not self.cookies.get('auth_token') or not self.cookies.get('ct0'):
            return {'valid': False, 'user': None, 'reason': 'Missing auth_token or ct0 cookies'}
        try:
            resp = requests.get(
                '%s/i/api/1.1/account/verify_credentials.json' % _WEB_BASE,
                headers=self._headers(True),
                timeout=15,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            return {'valid': False, 'user': None, 'reason': 'Network error: %s' % exc}
        if resp.status_code >= 400:
            return {
                'valid': False, 'user': None,
                'reason': 'verify_credentials returned HTTP %s' % resp.status_code,
                'status': resp.status_code,
            }
        try:
            data = resp.json()
        except ValueError:
            return {'valid': False, 'user': None, 'reason': 'Non-JSON response'}
        user_id = data.get('id_str') or data.get('id')
        if not user_id:
            return {'valid': False, 'user': None, 'reason': 'Response missing user ID'}
        return {
            'valid': True,
            'user': {
                'id': str(user_id),
                'username': data.get('screen_name', ''),
                'name': data.get('name', ''),
            },
            'reason': 'ok',
        }

    # --------------------------------------------------------------- requests
    def _request(self, path, method='GET', body=None, params=None):
        url = '%s%s' % (_REST_BASE, path)
        kwargs = {
            'headers': self._headers(True),
            'timeout': 20,
        }
        if params:
            kwargs['params'] = params
        if body is not None:
            kwargs['json'] = body
        resp = requests.request(method, url, **kwargs)
        if resp.status_code == 429:
            raise RuntimeError('rate_limit')
        if resp.status_code in (401, 403):
            raise RuntimeError('authentication_failure')
        if resp.status_code >= 400:
            raise RuntimeError('http_%s' % resp.status_code)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    # ------------------------------------------------------------------- dm
    def get_conversations(self, limit=50, cursor=None):
        params = {}
        if cursor:
            params['cursor'] = cursor
        data = self._request('/1.1/dm/inbox_initial_state.json', params=params)
        inbox = data.get('inbox_initial_state') or data or {}
        conversations = inbox.get('conversations') or {}
        result = []
        entries = list(conversations.items())[:limit]
        for conv_id, conv in entries:
            result.append({
                'conversation_id': conv_id,
                'users': conv.get('participants', {}).get('users') or [],
                'last_message': None,
                'group': bool(conv.get('info', {}).get('type') == 'GROUP_DM'),
            })
        return {'conversations': result, 'cursor': inbox.get('status', {}).get('last_visible_activity_at')}

    def get_dms(self, conversation_id, limit=100, cursor=None):
        params = {}
        if cursor:
            params['max_id'] = cursor
        path = '/1.1/dm/conversation/%s.json' % conversation_id
        data = self._request(path, params=params)
        timeline = data.get('conversation_timeline') or data or {}
        messages = []
        for event in (timeline.get('entries') or [])[:limit]:
            message = (event or {}).get('message') or {}
            msg_create = message.get('message_create') or {}
            msg_data = msg_create.get('message_data') or {}
            messages.append({
                'id': message.get('id'),
                'text': msg_data.get('text', ''),
                'sender_id': msg_create.get('sender_id', ''),
                'created_at': message.get('ext_created_at'),
                'conversation_id': msg_data.get('conversation_id'),
            })
        return {'messages': messages}

    def send_dm(self, recipient_id, text):
        if not recipient_id:
            raise ValueError('recipient_id is required')
        if not text:
            raise ValueError('text must be non-empty')
        body = {
            'event': {
                'type': 'message_create',
                'message_create': {
                    'target': {'recipient_id': str(recipient_id)},
                    'message_data': {'text': text},
                },
            },
        }
        data = self._request('/1.1/dm/new2.json', method='POST', body=body)
        event = data.get('event') or {}
        return {
            'message_id': event.get('id', ''),
            'created_at': fields.Datetime.now().isoformat(),
        }

    def get_events(self, **kwargs):
        """Stub/placeholder: events are derived from get_conversations/get_dms."""
        return {'events': []}

    # -------------------------------------------------- group automation ops
    # These map to x.com internal (GraphQL/REST) endpoints whose query IDs rotate
    # and go stale (see XAction's endpoints.js). They are enqueued by
    # x.account.group automation and executed per-account by the task cron; each
    # verifies the response and raises on failure so the queue retries/backs off.

    def like(self, tweet_id, **kwargs):
        if not tweet_id:
            raise ValueError('tweet_id is required')
        data = self._request(
            '/1.1/favorites/create.json', method='POST',
            body={'id': str(tweet_id)}
        )
        return {'tweet_id': str(tweet_id), 'liked': bool(data)}

    def comment(self, tweet_id, text, **kwargs):
        if not tweet_id or not text:
            raise ValueError('tweet_id and text are required')
        body = {
            'status': text,
            'in_reply_to_status_id': str(tweet_id),
        }
        data = self._request('/1.1/statuses/update.json', method='POST', body=body)
        return {'tweet_id': str(tweet_id), 'comment_id': data.get('id_str', '')}

    def repost(self, tweet_id, **kwargs):
        if not tweet_id:
            raise ValueError('tweet_id is required')
        data = self._request(
            '/1.1/statuses/retweet/%s.json' % str(tweet_id), method='POST'
        )
        return {'tweet_id': str(tweet_id), 'retweeted': 'id' in data or bool(data)}

    def follow(self, screen_name, **kwargs):
        if not screen_name:
            raise ValueError('screen_name is required')
        body = {'screen_name': str(screen_name).lstrip('@')}
        data = self._request('/1.1/friendships/create.json', method='POST', body=body)
        return {'screen_name': str(screen_name), 'followed': bool(data)}
