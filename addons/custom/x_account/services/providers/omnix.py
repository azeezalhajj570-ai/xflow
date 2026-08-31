# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

import requests

_LOGGER = logging.getLogger(__name__)

_OMNIX_BASE = 'https://api.omnixapi.com/api/v1/twitter'

# HTTP status → classified error code (mirrors the lifecycle error taxonomy).
_HTTP_ERROR_CODES = {
    400: 'authentication_failure',   # missing/invalid auth_token
    401: 'authentication_failure',   # missing/invalid API key
    402: 'rate_limit',               # insufficient credits (transient, stays ACTIVE)
    403: 'authentication_failure',
    404: 'http_404',
    429: 'rate_limit',
}


class OmniXProvider:
    """Optional X provider via the OmniX REST API.

    OmniX acts as a real X account using the account's `auth_token` cookie
    (already stored encrypted in x.session.store) plus a shared Bearer API key
    from ir.config_parameter. Implements the XProvider surface (validate,
    DMs, group automation ops) and is a per-account either/or alternative to
    SessionWebProvider. Optional — accounts using SessionWebProvider never
    touch this provider or the API key.
    """

    _needs_cookies = True

    def __init__(self, env, account, cookies):
        self.env = env
        self.account = account
        self.cookies = cookies or {}
        self._api_key = env['ir.config_parameter'].sudo().get_param(
            'x_account.omnix_api_key')

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
        auth_token = self.cookies.get('auth_token')
        if not auth_token:
            return {'valid': False, 'user': None, 'reason': 'Missing auth_token cookie'}
        try:
            handle = self.account.social_account_handle
            if handle:
                data = self._request('GET', '/user/info',
                                     params={'userName': handle})
                user = data.get('data') or {}
                user_id = user.get('id') or user.get('rest_id')
                if not user_id:
                    return {'valid': False, 'user': None,
                            'reason': 'Response missing user ID'}
                return {
                    'valid': True,
                    'user': {
                        'id': str(user_id),
                        'username': user.get('userName', '') or handle,
                        'name': user.get('name', ''),
                    },
                    'reason': 'omnix',
                }
            return self._validate_by_token(auth_token)
        except RuntimeError as exc:
            return {'valid': False, 'user': None, 'reason': str(exc)}

    def _validate_by_token(self, auth_token):
        """Resolve the authed user from the token when no handle is known."""
        home = self._request('POST', '/user/home_timeline')
        user_id = (home.get('data') or {}).get('userId')
        if not user_id:
            return {'valid': False, 'user': None,
                    'reason': 'Token did not resolve to a user'}
        info = self._request('GET', '/user/info_by_id',
                             params={'userId': str(user_id)})
        user = info.get('data') or {}
        if not user.get('id'):
            return {'valid': False, 'user': None,
                    'reason': 'Response missing user ID'}
        return {
            'valid': True,
            'user': {
                'id': str(user.get('id')),
                'username': user.get('userName', ''),
                'name': user.get('name', ''),
            },
            'reason': 'omnix_token',
        }

    # ------------------------------------------------------------------- dm
    def get_conversations(self, limit=50, cursor=None):
        params = {}
        if cursor:
            params['cursor'] = cursor
        data = self._request('GET', '/dm/list', params=params)
        inbox = data.get('data') or {}
        conversations = inbox.get('conversations') or []
        if isinstance(conversations, dict):
            conversations = list(conversations.values())
        return {
            'conversations': [
                {
                    'conversation_id': conv.get('conversation_id') or conv.get('id'),
                    'type': conv.get('type', 'one_to_one'),
                    'participants': conv.get('participants') or [],
                    'participant_count': conv.get('participant_count', 0),
                    'last_message': conv.get('last_message'),
                    'group': conv.get('type') == 'group',
                }
                for conv in conversations[:limit]
            ],
            'cursor': (inbox.get('next_cursor') or {}).get('cursor_id') or inbox.get('cursor'),
        }

    def get_dms(self, conversation_id, limit=100, cursor=None):
        body = {'conversation_id': conversation_id, 'limit': int(limit)}
        if cursor:
            body['cursor'] = cursor
        data = self._request('POST', '/dm/conversation', body=body)
        conv = data.get('data') or {}
        messages = conv.get('messages') or []
        return {
            'messages': [
                {
                    'id': msg.get('id') or msg.get('seq_id'),
                    'text': msg.get('text') or msg.get('body', ''),
                    'sender_id': msg.get('sender_id') or (msg.get('sender') or {}).get('id'),
                    'created_at': msg.get('created_at') or msg.get('createdAt'),
                    'conversation_id': conversation_id,
                }
                for msg in messages[:limit]
            ],
            'cursor': conv.get('next_cursor'),
        }

    # ------------------------------------------------------- group members
    def fetch_groups(self, account, limit=100):
        """Fetch group-DM conversations + their members and sync them into
        discuss.channel (channel_type 'x_group') and res.partner.

        OmniX's /dm/list returns at most 5 participants per group (a hard
        preview cap) and its participant_count is always 5 for groups, so we
        sync exactly the members OmniX returns and report only what we synced.

        Returns a summary dict of groups created/updated.
        """
        count = min(int(limit), 100)
        conversations = []
        cursor_id = graph_snapshot_id = None
        while True:
            params = {'count': count}
            if cursor_id:
                params['cursor_id'] = cursor_id
            if graph_snapshot_id:
                params['graph_snapshot_id'] = graph_snapshot_id
            data = self._request('GET', '/dm/list', params=params)
            inbox = data.get('data') or {}
            page = inbox.get('conversations') or []
            if isinstance(page, dict):
                page = list(page.values())
            conversations.extend(page)
            if not inbox.get('has_more'):
                break
            nxt = inbox.get('next_cursor') or {}
            cursor_id = nxt.get('cursor_id')
            graph_snapshot_id = nxt.get('graph_snapshot_id')
            if not cursor_id:
                break
            if len(conversations) >= int(limit):
                break
        groups = [c for c in conversations if c.get('type') == 'group']

        channel_model = self.env['discuss.channel'].sudo()
        partner_model = self.env['res.partner'].sudo()
        created = updated = members = 0

        for conv in groups:
            conv_id = conv.get('conversation_id') or conv.get('id')
            if not conv_id:
                continue
            # Upsert members -> res.partner (keyed by x_user_id)
            participant_ids = []
            member_names = []
            for p in conv.get('participants') or []:
                x_uid = p.get('id')
                if not x_uid:
                    continue
                partner = partner_model.search([('x_user_id', '=', str(x_uid))], limit=1)
                if not partner:
                    partner = partner_model.create({
                        'name': p.get('name') or p.get('userName') or str(x_uid),
                        'x_user_id': str(x_uid),
                        'x_username': p.get('userName'),
                    })
                    members += 1
                # Keep verification flags fresh on every sync.
                partner.write({
                    'x_is_verified': bool(p.get('isVerified')),
                    'x_is_blue_verified': bool(p.get('isBlueVerified')),
                })
                participant_ids.append(partner.id)
                member_names.append(partner.x_username or partner.name or str(x_uid))
            # Best-effort group name: OmniX does not return a group name, so we
            # derive one from member usernames. Only applied at creation time —
            # existing names (e.g. manually corrected) are never overwritten.
            group_name = conv.get('name') or ', '.join(member_names[:4]) or conv_id
            # Upsert group channel
            channel = channel_model._get_x_channel(
                account,
                conversation_id=conv_id,
                channel_type='x_group',
                create_if_not_found=False,
            )
            if not channel:
                channel = channel_model._get_x_channel(
                    account,
                    conversation_id=conv_id,
                    channel_type='x_group',
                    create_if_not_found=True,
                    member_ids=participant_ids,
                )
                channel.write({'name': group_name})
                created += 1
            else:
                updated += 1
                # Refresh member list (replace members directly via the
                # discuss.channel.member model, not Command on create/write).
                # Name is intentionally left untouched (preserve manual edits).
                member_model = self.env['discuss.channel.member'].sudo()
                member_model.search([
                    ('channel_id', '=', channel.id),
                ]).unlink()
                member_model.create([
                    {'channel_id': channel.id, 'partner_id': pid}
                    for pid in participant_ids
                ])
        return {'groups': len(groups), 'created': created, 'updated': updated,
                'members': members}

    def send_dm(self, recipient_id, text):
        if not recipient_id:
            raise ValueError('recipient_id is required')
        if not text:
            raise ValueError('text must be non-empty')
        data = self._request('POST', '/dm/send', body={
            'recipient_id': str(recipient_id),
            'text': text,
        })
        message = data.get('data') or {}
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
        data = self._request('POST', '/tweet/favorite',
                             body={'tweet_id': str(tweet_id)})
        return {'tweet_id': str(tweet_id), 'liked': bool(data.get('data', {}).get('favorited', True))}

    def comment(self, tweet_id, text, **kwargs):
        if not tweet_id or not text:
            raise ValueError('tweet_id and text are required')
        data = self._request('POST', '/tweet/create', body={
            'tweet_id': str(tweet_id),
            'text': text,
        })
        return {'tweet_id': str(tweet_id), 'comment_id': (data.get('data') or {}).get('id', '')}

    def repost(self, tweet_id, **kwargs):
        if not tweet_id:
            raise ValueError('tweet_id is required')
        data = self._request('POST', '/tweet/retweet',
                             body={'tweet_id': str(tweet_id)})
        return {'tweet_id': str(tweet_id), 'retweeted': bool(data.get('data', {}).get('retweeted', True))}

    def follow(self, screen_name, **kwargs):
        if not screen_name:
            raise ValueError('screen_name is required')
        screen_name = str(screen_name).lstrip('@')
        data = self._request('POST', '/user/follow',
                             body={'userName': screen_name})
        return {'screen_name': screen_name, 'followed': bool(data.get('data', {}).get('followed', True))}

    def post_tweet(self, text, **kwargs):
        if not text:
            raise ValueError('text is required')
        data = self._request('POST', '/tweet/create', body={'text': text})
        return {'tweet_id': (data.get('data') or {}).get('id', '')}

    # --------------------------------------------------------------- internals
    def _headers(self):
        return {
            'Authorization': 'Bearer %s' % self._api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def _request(self, method, path, params=None, body=None, path_args=None):
        """Call an OmniX endpoint and return the parsed envelope data.

        Raises RuntimeError with a classified error code on failure.
        """
        if not self._api_key:
            raise RuntimeError('omnix_api_key_missing')
        auth_token = self.cookies.get('auth_token')
        if not auth_token:
            raise RuntimeError('Missing auth_token cookie')
        url = _OMNIX_BASE + path
        if path_args:
            url = url % path_args
        req_params = dict(params or {})
        req_params['auth_token'] = auth_token
        try:
            resp = requests.request(
                method, url, params=req_params, json=body,
                headers=self._headers(), timeout=20)
        except requests.RequestException as exc:
            raise RuntimeError('network_error: %s' % exc)
        code = _HTTP_ERROR_CODES.get(resp.status_code)
        if code:
            raise RuntimeError(code)
        if resp.status_code >= 400:
            raise RuntimeError('http_%s' % resp.status_code)
        try:
            envelope = resp.json()
        except ValueError:
            raise RuntimeError('non_json_response')
        if not envelope.get('status', True):
            raise RuntimeError(str(envelope.get('error') or 'omnix_request_failed'))
        return envelope
