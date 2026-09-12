# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""GetXAPIProvider: optional X provider via the GetXAPI REST API.

GetXAPI is a paid per-call API gateway to X/Twitter data. This provider
implements the XProvider surface (validate, DMs, tweet reads/writes, user
operations, media upload) and is a per-account either/or alternative to
SessionWebProvider and other providers.

SOLID layering:
- transport:        :class:`GetXAPIClient` (requests, headers, status)
- parsing:          :class:`GetXAPIEnvelopeParser` (envelope -> DTOs)
- error taxonomy:   :mod:`getxapi_errors` (HTTP status -> classified error)
- cost tracking:    :mod:`getxapi_cost` (centralized pricing table)
- services:         tweet, user, DM, media services
- composition:      this class wires the above behind the XProvider contract
"""

import logging

from .getxapi_client import GetXAPIClient
from .getxapi_dm_service import GetXAPIDMService
from .getxapi_media_service import GetXAPIMediaService
from .getxapi_tweet_service import GetXAPITweetService
from .getxapi_user_service import GetXAPIUserService
from . import getxapi_envelope

_LOGGER = logging.getLogger(__name__)


class GetXAPIProvider:
    """Composition root: XProvider contract implemented over the GetXAPI REST API."""

    _needs_cookies = False
    _needs_encryption_code = False

    def __init__(self, env, account):
        self.env = env
        self.account = account
        self._api_key = env['ir.config_parameter'].sudo().get_param(
            'x_account.getxapi_api_key')
        self._auth_token = getattr(account, 'x_getxapi_auth_token', '') or ''
        self._client = GetXAPIClient(
            env, self._api_key, account_id=account.id)
        self._tweets = GetXAPITweetService(self._client)
        self._users = GetXAPIUserService(self._client)
        self._dms = GetXAPIDMService(self._client)
        self._media = GetXAPIMediaService(self._client)

    def validate_session(self):
        """Return dict {valid, user, reason, status}.

        Calls GET /twitter/user/info with the account's handle to verify the
        API key works and the account is accessible.
        """
        if not self._api_key:
            return {'valid': False, 'user': None, 'reason': 'getxapi_api_key_missing'}
        try:
            handle = self.account.social_account_handle
            if not handle:
                return {'valid': False, 'user': None,
                        'reason': 'Account has no X handle configured'}
            user = self._users.info(handle)
            if not user or not user.get('id'):
                return {'valid': False, 'user': None,
                        'reason': 'Response missing user ID'}
            return {'valid': True, 'user': user, 'reason': 'getxapi'}
        except Exception as exc:
            return {'valid': False, 'user': None, 'reason': str(exc)}

    def like(self, post, **kwargs):
        """Like a post via GetXAPI.

        ``post`` is a normalized post reference dict carrying ``post_id``.
        """
        post_id = self._post_id(post)
        if not post_id:
            raise ValueError('post_id is required')
        if self._auth_token:
            kwargs['auth_token'] = self._auth_token
        return self._tweets.like(post_id, **kwargs)

    def comment(self, post, text=None, **kwargs):
        """Reply to a post via GetXAPI."""
        post_id = self._post_id(post)
        if not post_id:
            raise ValueError('post_id is required')
        text = (text or '').strip() or 'Thanks for sharing!'
        if self._auth_token:
            kwargs['auth_token'] = self._auth_token
        return self._tweets.create(text, reply_to_tweet_id=post_id, **kwargs)

    def repost(self, post, **kwargs):
        """Repost (retweet) a post via GetXAPI."""
        post_id = self._post_id(post)
        if not post_id:
            raise ValueError('post_id is required')
        if self._auth_token:
            kwargs['auth_token'] = self._auth_token
        return self._tweets.retweet(post_id, **kwargs)

    def bookmark(self, post, **kwargs):
        """Bookmark a post via GetXAPI."""
        post_id = self._post_id(post)
        if not post_id:
            raise ValueError('post_id is required')
        if self._auth_token:
            kwargs['auth_token'] = self._auth_token
        return self._tweets.bookmark(post_id, **kwargs)

    def unbookmark(self, post, **kwargs):
        """Remove a post from bookmarks via GetXAPI."""
        post_id = self._post_id(post)
        if not post_id:
            raise ValueError('post_id is required')
        if self._auth_token:
            kwargs['auth_token'] = self._auth_token
        return self._tweets.unbookmark(post_id, **kwargs)

    def follow(self, screen_name=None, target_user_id=None, **kwargs):
        """Follow a user via GetXAPI."""
        if target_user_id:
            user = self._users.info_by_id(target_user_id)
            screen_name = user.get('username')
        if not screen_name:
            raise ValueError('target_user_id or screen_name is required')
        if self._auth_token:
            kwargs['auth_token'] = self._auth_token
        return self._users.follow(screen_name, **kwargs)

    def post_tweet(self, text, **kwargs):
        """Create a new tweet via GetXAPI."""
        if not text:
            raise ValueError('text is required')
        if self._auth_token:
            kwargs['auth_token'] = self._auth_token
        return self._tweets.create(text, **kwargs)

    def get_dms(self, conversation_id, limit=100, cursor=None):
        """Return normalized messages for one conversation."""
        return self._dms.conversation(
            conversation_id, auth_token=self._auth_token,
            count=limit, cursor=cursor)

    def send_dm(self, recipient_id, text):
        """Send a direct message via GetXAPI."""
        return self._dms.send(recipient_id, text, auth_token=self._auth_token)

    def fetch_groups(self, account, limit=100):
        """Fetch group-DM conversations and sync them into discuss channels."""
        result = self._dms.list(auth_token=self._auth_token, count=limit)
        conversations = result.get('conversations', [])
        groups = [c for c in conversations if c.get('group')]
        channel_model = self.env['discuss.channel'].sudo()
        partner_model = self.env['res.partner'].sudo()
        created = updated = members = 0
        for conv in groups:
            conv_id = conv.get('conversation_id')
            if not conv_id:
                continue
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
                participant_ids.append(partner.id)
                member_names.append(partner.x_username or partner.name or str(x_uid))
            group_name = conv.get('name') or ', '.join(member_names[:4]) or conv_id
            channel = channel_model._get_x_channel(
                account, conversation_id=conv_id, channel_type='x_group',
                create_if_not_found=False)
            if not channel:
                channel = channel_model._get_x_channel(
                    account, conversation_id=conv_id, channel_type='x_group',
                    create_if_not_found=True, member_ids=participant_ids)
                channel.write({'name': group_name})
                created += 1
            else:
                updated += 1
        return {'groups': len(groups), 'created': created, 'updated': updated,
                'members': members}

    @staticmethod
    def _chat_name_from_conversation(conv, my_user_id=''):
        """Derive a human name for one GetXAPI conversation entry."""
        participants = conv.get('participants') or []

        def _label(p):
            return str(p.get('userName') or p.get('username')
                       or p.get('name') or '')

        is_group = bool(conv.get('group')) or conv.get('type') == 'group'
        if is_group:
            name = conv.get('name') or ''
            if name:
                return name, 'x_group'
            names = [_label(p) for p in participants
                     if _label(p) and str(p.get('id')) != str(my_user_id)]
            return ', '.join(names[:4]), 'x_group'
        for p in participants:
            if str(p.get('id')) != str(my_user_id) and _label(p):
                return _label(p), 'x'
        return '', 'x'

    def sync_chat_names(self, account, limit=200):
        """Refresh the names of existing X chat channels from GetXAPI.

        Lists DM/group conversations (cursor-paginated, max 5 pages) and only
        updates discuss channels that already exist for this account; missing
        channels are counted, never created. Returns conversation counts.
        """
        channel_model = self.env['discuss.channel'].sudo()
        if not self._auth_token:
            raise ValueError(
                'Set the GetXAPI Auth Token on this account first — the '
                'inbox list requires it.')
        my_user_id = str(getattr(account, 'twitter_user_id', '') or '')
        updated = unchanged = missing = seen = 0
        cursor = None
        for _page in range(10):  # ~50 conversations per page, max 10 pages
            result = self._dms.list(
                auth_token=self._auth_token, count=limit, cursor=cursor)
            conversations = result.get('conversations', [])
            seen += len(conversations)
            for conv in conversations:
                conv_id = conv.get('conversation_id')
                if not conv_id:
                    continue
                name, _ctype = self._chat_name_from_conversation(conv, my_user_id)
                channel = channel_model.search([
                    ('x_account_id', '=', account.id),
                    ('x_conversation_id', '=', str(conv_id)),
                    ('channel_type', 'in', ('x', 'x_group')),
                ], limit=1)
                if not channel:
                    missing += 1
                    continue
                if name and channel.name != name:
                    channel.write({'name': name})
                    updated += 1
                else:
                    unchanged += 1
            cursor = result.get('cursor')
            if not cursor or not result.get('has_more', bool(cursor)):
                break
        return {'conversations': seen, 'updated': updated,
                'unchanged': unchanged, 'missing': missing}

    def fetch_group_messages(self, account, limit=100):
        """Fetch messages from X group-DM conversations and store them."""
        channels = self.env['discuss.channel'].sudo().search([
            ('channel_type', '=', 'x_group'),
            ('x_account_id', '=', account.id),
        ])
        total = 0
        failures = 0
        for channel in channels:
            conv_id = channel.x_conversation_id
            if not conv_id:
                continue
            try:
                result = self.get_dms(conv_id, limit=limit)
                for msg in result.get('messages', []):
                    author_partner = False
                    sender_id = msg.get('sender_id')
                    if sender_id:
                        author_partner = self.env['res.partner'].sudo().search(
                            [('x_user_id', '=', str(sender_id))], limit=1)
                    channel._save_x_message(
                        direction='outbound' if msg.get('from_me') else 'inbound',
                        external_id=msg['id'],
                        body=msg.get('text', ''),
                        external_created_at=msg.get('created_at'),
                        author_partner=author_partner,
                        author_x_id=sender_id,
                    )
                    total += 1
            except Exception:
                _LOGGER.exception('Failed to fetch messages for group %s', conv_id)
                failures += 1
        return {'groups': len(channels), 'messages': total, 'failures': failures}

    def get_conversations(self, limit=50, cursor=None):
        """Return DM conversations."""
        return self._dms.list(
            auth_token=self._auth_token, count=limit, cursor=cursor)

    def supported_operations(self):
        """Operations this provider supports for the task queue."""
        return (
            'validate_session', 'like', 'comment', 'repost', 'follow',
            'bookmark', 'unbookmark',
            'post_tweet', 'get_dms', 'send_dm', 'fetch_groups',
            'fetch_group_messages', 'sync_chat_names',
        )

    @staticmethod
    def _post_id(post):
        if isinstance(post, dict):
            return post.get('post_id') or post.get('tweet_id') or post.get('id')
        return getattr(post, 'post_id', None) or getattr(post, 'id', None)


def _register_getxapi_provider():
    try:
        from odoo.addons.x_account.services.x_provider import XProviderRegistry
        XProviderRegistry.register('getxapi', __name__ + '.GetXAPIProvider')
    except (ImportError, AttributeError):
        _LOGGER.exception('Failed to register GetXAPI provider')


_register_getxapi_provider()
