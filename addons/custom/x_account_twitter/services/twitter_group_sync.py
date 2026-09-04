# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Group sync from the official X API into Odoo discuss channels.

Owns the "sync" side of the twitter provider (SRP): given the account's
``TwitterApiClient`` it lists the user's group conversations, upserts
``res.partner`` members and ``discuss.channel`` (``channel_type='x_group'``)
records, and reports what it did. The provider itself only forwards these calls.

Two official X endpoints back this:
- ``GET /2/chat/conversations`` (dm.read + users.read) lists the authenticated
  user's inbox, including the XChat groups (X's Groups product) whose ids are
  ``g``-prefixed; this is the primary source and answers "Fetch Groups".
- ``GET /2/dm_events`` (dm.read) covers the legacy Direct Message
  conversations; it is kept as a fallback when the Chat API is not reachable
  for the app/account. Its group detection is best-effort: group conversations
  have a single numeric id ``[0-9]{15,19}`` and only ``ParticipantsJoin`` /
  ``ParticipantsLeave`` events carry a ``participant_ids`` list.

Read-only rights and encryption: only conversation *metadata* (members, admin,
created_at) is exposed in plaintext by the Chat API; group ``group_name`` and
message bodies are end-to-end encrypted (``encoded_event`` blobs), so message
fetches skip XChat ``g`` conversations and plaintext DM messages are only read
via the DM events endpoint.

Names: ``group_name`` is used when the API returns one (it may come through
encrypted in which case it is rejected), otherwise the name is derived from
member usernames (same convention as OmniX) — applied only at channel creation
so manual edits are never overwritten.
"""

import logging
import re

from .twitter_errors import TwitterError

_LOGGER = logging.getLogger(__name__)

# A group DM conversation has a single numeric id; a 1:1 is "id-id".
_GROUP_CONVERSATION_RE = re.compile(r'^[0-9]{15,19}$')
# XChat (X Groups product) conversations are g-prefixed ids.
_CHAT_GROUP_ID_RE = re.compile(r'^g[0-9]+$')
# 1:1 chat conversations look like "<user_id>-<user_id>".
_DIRECT_CONVERSATION_RE = re.compile(r'^\d{1,19}-\d{1,19}$')

_DM_EVENT_FIELDS = (
    'created_at,dm_conversation_id,id,event_type,participant_ids,'
    'sender_id,text'
)
# Chat event fields used by GET /2/chat/conversations/{id}/events.
_CHAT_EVENT_FIELDS = (
    'created_at,encoded_event,event_type,id,participant_ids,sender_id,text'
)


class TwitterGroupSync:
    """Syncs X conversations + members for one account's provider client."""

    def __init__(self, env, client):
        self.env = env
        self.client = client

    # ------------------------------------------------------------------ public
    def fetch_groups(self, account, limit=100):
        """Fetch the account's X conversations + members and sync them into
        discuss.channel (channel_type 'x_group' / 'x') and res.partner.

        Primary source is the official Chat API (``GET /2/chat/conversations``)
        on ``api.x.com``. Temporary failures (network errors, 5xx) are retried
        inside the client and, if still failing, fall back to deriving groups
        from ``GET /2/dm_events``. Permanent failures (401/403/404) are NEVER
        hidden by the fallback — they surface as TwitterError.

        Returns a summary dict of groups created/updated.
        """
        try:
            return self._fetch_groups_chat(account, limit)
        except TwitterError as exc:
            if not exc.retryable:
                raise
            _LOGGER.warning(
                'X Chat API temporarily unavailable for account %s; '
                'falling back to legacy DM events (%s)', account.id, exc.message)
        return self._fetch_groups_dm_events(account, limit)

    def _fetch_groups_chat(self, account, limit):
        """Sync conversations from ``GET /2/chat/conversations`` (official Chat API).

        Both group (``type == 'group'``, ids ``g``-prefixed) and 1:1
        (``type == 'direct'``, ids ``<uid>-<uid>``) conversations are synced:
        groups into ``x_group`` channels, direct into ``x`` channels. The
        account is passed explicitly and every created channel is owned by it.
        """
        count = min(int(limit), 100)
        conversations = []
        users = {}
        pagination_token = None
        while True:
            params = {
                'chat_conversation.fields':
                    'admin_ids,created_at,group_name,member_ids,participant_ids,type',
                'expansions': 'admin_ids,member_ids,participant_ids',
                'user.fields': 'name,username,profile_image_url',
                'max_results': count,
            }
            if pagination_token:
                params['pagination_token'] = pagination_token
            data = self.client.request(
                'GET', '/2/chat/conversations', params=params)
            page = (data or {}).get('data') or []
            for conv in (page if isinstance(page, list) else []):
                if not conv.get('id'):
                    continue
                conversations.append(conv)
            users.update(self._users_by_id(data))
            meta = (data or {}).get('meta') or {}
            pagination_token = meta.get('next_token')
            if not pagination_token or not meta.get('has_more'):
                break
        return self._sync_conversations(account, conversations, users)

    def _sync_conversations(self, account, conversations, users):
        """Upsert conversations into discuss.channel, owned by ``account``.

        ``conversations`` is the raw list from the Chat API. Type is preserved
        on the channel (``x_group`` for groups, ``x`` for 1:1). Idempotent:
        re-syncing the same conversation updates the existing channel instead
        of creating a duplicate (unique per account + conversation id).
        """
        channel_model = self.env['discuss.channel'].sudo()
        partner_model = self.env['res.partner'].sudo()
        member_model = self.env['discuss.channel.member'].sudo()
        created = updated = members = 0

        def _replace_members(channel, participant_ids):
            member_model.search([
                ('channel_id', '=', channel.id),
            ]).unlink()
            member_model.create([
                {'channel_id': channel.id, 'partner_id': pid}
                for pid in participant_ids
            ])

        for conv in conversations:
            conv_id = str(conv.get('id') or '').strip()
            if not conv_id:
                continue
            conv_type = conv.get('type') or ('x_group' if _CHAT_GROUP_ID_RE.match(conv_id)
                                             else 'x')
            if conv_type == 'group':
                channel_type = 'x_group'
            elif conv_type == 'direct':
                channel_type = 'x'
            else:
                # Unknown type: fall back to the id shape (g -> group, else 1:1).
                channel_type = 'x_group' if _CHAT_GROUP_ID_RE.match(conv_id) else 'x'

            member_ids = set()
            for key in ('member_ids', 'participant_ids', 'admin_ids'):
                for x_uid in conv.get(key) or []:
                    if x_uid:
                        member_ids.add(str(x_uid))
            # The connected account is always a member of its own conversations.
            owner_uid = str(account.twitter_user_id) if account.twitter_user_id else ''
            if owner_uid:
                member_ids.add(owner_uid)
            if channel_type == 'x' and _DIRECT_CONVERSATION_RE.match(conv_id):
                # 1:1 conversation id is "<uid>-<uid>": the counterpart is the
                # half that is not the owner (the API may not always populate
                # participant_ids for direct conversations).
                for half in conv_id.split('-'):
                    if half and half != owner_uid:
                        member_ids.add(half)
                        break

            participant_ids = []
            member_names = []
            for x_uid in sorted(member_ids, key=lambda s: (len(s), s)):
                partner = partner_model.search([('x_user_id', '=', x_uid)], limit=1)
                if not partner:
                    user = users.get(x_uid) or {}
                    partner = partner_model.create({
                        'name': user.get('name') or user.get('username') or x_uid,
                        'x_user_id': x_uid,
                        'x_username': user.get('username'),
                    })
                    members += 1
                participant_ids.append(partner.id)
                member_names.append(partner.x_username or partner.name or x_uid)

            group_name = self._safe_group_name(conv.get('group_name'))
            if not group_name and conv.get('group_name'):
                # group_name may be ciphertext: try the Chat XDK decrypt.
                try:
                    decryptor = self._xchat_decryptor()
                    if decryptor.available:
                        plain = decryptor.decrypt_metadata(conv.get('group_name'))
                        group_name = self._safe_group_name(plain)
                except Exception:
                    _LOGGER.warning(
                        'Failed to decrypt group_name for conversation %s',
                        conv_id, exc_info=False)
            if channel_type == 'x':
                # 1:1 conversation: name it after the OTHER participant so each
                # conversation is distinguishable (never the owner).
                other_names = [n for uid, n in zip(
                    sorted(member_ids, key=lambda s: (len(s), s)), member_names)
                    if uid != owner_uid]
                channel_name = group_name or ', '.join(other_names[:4]) or (
                    ', '.join(member_names[:4])) or conv_id
            else:
                channel_name = group_name or ', '.join(member_names[:4]) or conv_id

            channel = channel_model._get_x_channel(
                account,
                conversation_id=conv_id,
                channel_type=channel_type,
                create_if_not_found=False,
            )
            if not channel:
                # Re-attach orphaned channels (x_account_id NULL) left behind by
                # earlier sync paths, matched by conversation id — never guess
                # the owner from a global lookup.
                orphan = channel_model.search([
                    ('channel_type', '=', channel_type),
                    ('x_conversation_id', '=', conv_id),
                    ('x_account_id', '=', False),
                ], limit=1)
                if orphan:
                    orphan.write({'x_account_id': account.id})
                    channel = orphan
                    updated += 1
                    if group_name:
                        channel.write({'name': group_name})
                    _replace_members(channel, participant_ids)
                    continue
                channel = channel_model._get_x_channel(
                    account,
                    conversation_id=conv_id,
                    channel_type=channel_type,
                    create_if_not_found=True,
                    member_ids=participant_ids,
                )
                channel.write({'name': channel_name})
                created += 1
            else:
                updated += 1
                if group_name:
                    channel.write({'name': group_name})
                elif channel_type == 'x':
                    # Keep 1:1 channel names meaningful (the other participant).
                    if channel.name != channel_name:
                        channel.write({'name': channel_name})
                _replace_members(channel, participant_ids)
        return {'groups': created + updated, 'created': created, 'updated': updated,
                'members': members}

    def _fetch_groups_dm_events(self, account, limit):
        """Fallback conversation derivation from ``GET /2/dm_events`` (legacy DMs).

        Used only when the Chat API is temporarily unavailable. Group
        conversations (single numeric id or ParticipantsJoin/Leave events) map
        to ``x_group`` channels; 1:1 conversations (``id-id`` ids) map to ``x``
        channels. Runs through the same ``_sync_conversations`` upsert so
        ownership and idempotency are preserved.
        """
        count = min(int(limit), 100)
        events = []
        pagination_token = None
        users = {}
        while True:
            params = {
                'dm_event.fields': _DM_EVENT_FIELDS,
                'expansions': 'sender_id,participant_ids',
                'user.fields': 'name,username,profile_image_url',
                'max_results': count,
            }
            if pagination_token:
                params['pagination_token'] = pagination_token
            data = self.client.request('GET', '/2/dm_events', params=params)
            page = (data or {}).get('data') or []
            if isinstance(page, list):
                events.extend(page)
            users.update(self._users_by_id(data))
            meta = (data or {}).get('meta') or {}
            pagination_token = meta.get('next_token')
            if not pagination_token or len(events) >= int(limit):
                break

        conversations = []
        seen = set()
        for event in events:
            conv_id = event.get('dm_conversation_id')
            if not conv_id:
                continue
            conv_key = str(conv_id)
            if conv_key in seen:
                continue
            event_type = event.get('event_type')
            participant_ids = [str(pid) for pid in (event.get('participant_ids') or [])]
            if bool(_GROUP_CONVERSATION_RE.match(conv_key)):
                conv_type = 'group'
            elif _DIRECT_CONVERSATION_RE.match(conv_key):
                conv_type = 'direct'
            elif event_type in ('ParticipantsJoin', 'ParticipantsLeave'):
                conv_type = 'group'
            else:
                continue
            seen.add(conv_key)
            conversations.append({
                'id': conv_key,
                'type': conv_type,
                'participant_ids': participant_ids,
                'member_ids': participant_ids,
                'group_name': '',
            })

        return self._sync_conversations(account, conversations, users)

    def fetch_group_messages(self, account, limit=100):
        """Fetch messages from X conversations and store them as x.message.

        Primary source is the Chat events API
        (``GET /2/chat/conversations/{id}/events``). MessageCreate events are
        stored as x.message records (idempotent by external event id). Events
        whose body is end-to-end encrypted (``encoded_event`` present, no
        plaintext ``text``) are NOT silently discarded: they are recorded with
        an explicit ``encrypted`` flag on the channel's message state and
        counted in ``encrypted_skipped``. Temporary Chat API failures fall back
        to the legacy DM events endpoint (``GET /2/dm_conversations/{id}/dm_events``)
        for that conversation only; permanent failures are counted in
        ``failures`` and logged.

        Returns a summary dict.
        """
        channels = self.env['discuss.channel'].sudo().search([
            ('channel_type', 'in', ('x_group', 'x')),
            ('x_account_id', '=', account.id),
        ])
        per_group = min(int(limit), 100)
        total = 0
        failures = 0
        encrypted_skipped = 0
        for channel in channels:
            conv_id = channel.x_conversation_id
            if not conv_id:
                continue
            try:
                result = self.get_dms(conv_id, limit=per_group)
                encrypted_skipped += len(result.get('encrypted', []))
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
                        encrypted=msg.get('encrypted', False),
                    )
                    total += 1
                for enc in result.get('encrypted', []):
                    # Persist an explicit marker so automation/UI can see the
                    # sync state instead of silently missing the message.
                    channel._save_x_message(
                        direction='inbound',
                        external_id=enc['id'],
                        body='',
                        external_created_at=enc.get('created_at'),
                        author_x_id=enc.get('sender_id'),
                        encrypted=True,
                        no_mail=True,
                    )
                if result.get('encrypted') and result.get('messages'):
                    channel.write({'x_sync_status': 'partial'})
                elif result.get('encrypted'):
                    channel.write({'x_sync_status': 'encrypted'})
                else:
                    channel.write({'x_sync_status': 'ok'})
            except Exception:
                _LOGGER.exception('Failed to fetch messages for conversation %s', conv_id)
                channel.write({'x_sync_status': 'failed'})
                failures += 1
        return {'groups': len(channels), 'messages': total, 'failures': failures,
                'encrypted_skipped': encrypted_skipped}

    def get_dms(self, conversation_id, limit=100):
        """Return the message list for one conversation.

        Uses the Chat events API (``GET /2/chat/conversations/{id}/events``) on
        ``api.x.com`` for XChat conversations; falls back to the legacy DM
        events endpoint (``GET /2/dm_conversations/{id}/dm_events``) for legacy
        numeric/1:1 conversations or when the Chat events endpoint is
        temporarily unavailable.

        Messages are normalized to ``{id, sender_id, text, created_at,
        from_me, encrypted}`` for the shared ``_save_x_message`` contract.
        """
        conv_id = str(conversation_id)
        if not (_CHAT_GROUP_ID_RE.match(conv_id) or _DIRECT_CONVERSATION_RE.match(conv_id)):
            return self._get_dms_legacy(conv_id, limit)
        try:
            return self._get_dms_chat_events(conv_id, limit)
        except TwitterError as exc:
            if not exc.retryable:
                raise
            _LOGGER.warning(
                'X Chat events API temporarily unavailable for conversation %s; '
                'falling back to legacy DM events (%s)', conv_id, exc.message)
            return self._get_dms_legacy(conv_id, limit)

    def _get_dms_chat_events(self, conversation_id, limit):
        """Fetch events for one conversation via the official Chat events API.

        Plaintext MessageCreate events are returned directly. Events carrying an
        ``encoded_event`` blob are batch-decrypted with the Chat XDK when the
        account has a key blob; successfully decrypted messages are returned
        with their text, failures stay in ``encrypted`` (explicit marker).
        """
        count = min(int(limit), 100)
        messages = []
        encrypted = []
        raw_blobs = []
        key_change_blobs = []
        pagination_token = None
        while True:
            params = {
                'chat_event.fields': _CHAT_EVENT_FIELDS,
                'max_results': count,
            }
            if pagination_token:
                params['pagination_token'] = pagination_token
            data = self.client.request(
                'GET', '/2/chat/conversations/%s/events' % conversation_id,
                params=params)
            page = (data or {}).get('data') or []
            for event in (page if isinstance(page, list) else []):
                if not event.get('id'):
                    continue
                event_type = event.get('event_type') or ''
                sender_id = event.get('sender_id')
                text = event.get('text', '') or ''
                encoded = event.get('encoded_event')
                if encoded and not text:
                    # Encrypted event: collect the blob for batch decryption.
                    raw_blobs.append(encoded)
                    encrypted.append({
                        'id': event.get('id'),
                        'sender_id': sender_id,
                        'created_at': event.get('created_at'),
                        'raw': encoded,
                    })
                    continue
                if event_type == 'MessageCreate' or text:
                    messages.append({
                        'id': event.get('id'),
                        'sender_id': sender_id,
                        'text': text,
                        'created_at': event.get('created_at'),
                        'from_me': bool(sender_id) and str(sender_id) == str(
                            self.client.account.twitter_user_id),
                        'encrypted': False,
                    })
            meta = (data or {}).get('meta') or {}
            key_change_blobs.extend(
                meta.get('conversation_key_events') or [])
            pagination_token = meta.get('next_token')
            if not pagination_token or len(messages) + len(encrypted) >= int(limit):
                break

        # Batch-decrypt the encoded blobs with the Chat XDK when available.
        decryptor = self._xchat_decryptor()
        if raw_blobs and decryptor.available:
            try:
                result = decryptor.decrypt_events(
                    raw_blobs, key_change_events=key_change_blobs)
                by_id = {}
                for dm in result.get('messages') or []:
                    ev = dm.get('event') or {}
                    eid = ev.get('id') or ev.get('message_id')
                    if eid:
                        by_id[str(eid)] = ev
                still_encrypted = []
                for enc in encrypted:
                    ev = by_id.get(str(enc['id']))
                    if ev and ev.get('type') == 'Message':
                        content = ev.get('content') or {}
                        sender = ev.get('sender_id') or enc.get('sender_id')
                        messages.append({
                            'id': enc['id'],
                            'sender_id': sender,
                            'text': content.get('text', ''),
                            'created_at': ev.get('created_at') or enc.get('created_at'),
                            'from_me': bool(sender) and str(sender) == str(
                                self.client.account.twitter_user_id),
                            'encrypted': False,
                        })
                    else:
                        still_encrypted.append(enc)
                encrypted = still_encrypted
            except Exception as exc:
                _LOGGER.warning(
                    'Chat XDK decryption failed for conversation %s (%s); '
                    'keeping encrypted markers', conversation_id, exc)
        return {'messages': messages, 'encrypted': encrypted}

    def _xchat_decryptor(self):
        """Return the Chat XDK decryptor for the account (lazily built)."""
        if getattr(self, '_decryptor', None) is None:
            from .xchat_decryptor import XChatDecryptor
            self._decryptor = XChatDecryptor(
                self.env, self.client.account, client=self.client)
        return self._decryptor

    def _get_dms_legacy(self, conversation_id, limit):
        """Fetch messages via the legacy ``/2/dm_conversations/{id}/dm_events``."""
        count = min(int(limit), 100)
        messages = []
        pagination_token = None
        while True:
            params = {
                'dm_event.fields': _DM_EVENT_FIELDS,
                'expansions': 'sender_id',
                'user.fields': 'name,username',
                'max_results': count,
                'event_types': 'MessageCreate',
            }
            if pagination_token:
                params['pagination_token'] = pagination_token
            data = self.client.request(
                'GET', '/2/dm_conversations/%s/dm_events' % conversation_id,
                params=params)
            page = (data or {}).get('data') or []
            for event in (page if isinstance(page, list) else []):
                if event.get('event_type') != 'MessageCreate' or not event.get('id'):
                    continue
                sender_id = event.get('sender_id')
                messages.append({
                    'id': event.get('id'),
                    'sender_id': sender_id,
                    'text': event.get('text', ''),
                    'created_at': event.get('created_at'),
                    'from_me': bool(sender_id) and str(sender_id) == str(
                        self.client.account.twitter_user_id),
                    'encrypted': False,
                })
            meta = (data or {}).get('meta') or {}
            pagination_token = meta.get('next_token')
            if not pagination_token or len(messages) >= int(limit):
                break
        return {'messages': messages, 'encrypted': []}

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _safe_group_name(group_name):
        """Return ``group_name`` when it is readable plaintext, else ''.

        XChat encrypts group names in the API response; reject strings that
        look like encoded blobs (very long, or containing control characters).
        """
        if not group_name or not isinstance(group_name, str):
            return ''
        name = group_name.strip()
        if not name or len(name) >= 80:
            return ''
        if any(ord(char) < 32 for char in name):
            return ''
        return name

    @staticmethod
    def _users_by_id(data):
        includes = (data or {}).get('includes') or {}
        users = includes.get('users') or []
        return {str(user.get('id')): user for user in users if user.get('id')}