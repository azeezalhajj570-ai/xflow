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
import uuid

from odoo.addons.x_account.services.x_provider import x_conversation_is_group

from .getxapi_client import GetXAPIClient
from .getxapi_dm_service import GetXAPIDMService
from .getxapi_media_service import GetXAPIMediaService
from .getxapi_tweet_service import GetXAPITweetService
from .getxapi_user_service import GetXAPIUserService
from . import getxapi_errors
from . import getxapi_envelope

_LOGGER = logging.getLogger(__name__)


def _existing_columns(cr, table, columns):
    """Return the subset of ``columns`` physically present on ``table``.

    Tests run against a lean install (only the module's dependencies), so
    optional columns contributed by heavy modules (e.g. accountant's
    ``res_partner.autopost_bills`` or purchase's ``group_rfq``) may not
    exist; bulk SQL must only reference columns the database actually has.
    """
    cr.execute(
        'SELECT column_name FROM information_schema.columns '
        'WHERE table_schema = current_schema() AND table_name = %s '
        'AND column_name IN %s', (table, tuple(columns)))
    present = {row[0] for row in cr.fetchall()}
    return [col for col in columns if col in present]


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

    def _preflight_write(self, operation, screen_name=None, target_user_id=None):
        """Fail fast BEFORE spending a paid write on a doomed request.

        Validates the local preconditions every paid write depends on:
        a GetXAPI auth token, the account's own numeric X user id, and (for
        follows) that the target is not the account itself. Raises
        :class:`GetXAPIPreflightError` — a non-retryable error — so the task
        queue does not re-queue it and no paid HTTP call is made.
        """
        if not self._auth_token:
            raise getxapi_errors.GetXAPIPreflightError(
                operation, 'missing_getxapi_auth_token')
        own_user_id = str(
            getattr(self.account, 'twitter_user_id', '') or '').strip()
        if not own_user_id:
            raise getxapi_errors.GetXAPIPreflightError(
                operation, 'missing_twitter_user_id')
        if target_user_id and str(target_user_id).strip() == own_user_id:
            raise getxapi_errors.GetXAPIPreflightError(
                operation, 'cannot_follow_self')
        own_handle = (
            getattr(self.account, 'social_account_handle', '') or ''
        ).lstrip('@').strip().lower()
        if (screen_name and own_handle
                and str(screen_name).lstrip('@').strip().lower() == own_handle):
            raise getxapi_errors.GetXAPIPreflightError(
                operation, 'cannot_follow_self')

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
            user = self._users.info(handle, billable=False)
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
        self._preflight_write('like')
        return self._tweets.like(post_id, **self._write_args(kwargs))

    def comment(self, post, text=None, **kwargs):
        """Reply to a post via GetXAPI."""
        post_id = self._post_id(post)
        if not post_id:
            raise ValueError('post_id is required')
        text = (text or '').strip() or 'Thanks for sharing!'
        self._preflight_write('comment')
        return self._tweets.create(
            text, reply_to_tweet_id=post_id, **self._write_args(kwargs))

    def repost(self, post, **kwargs):
        """Repost (retweet) a post via GetXAPI."""
        post_id = self._post_id(post)
        if not post_id:
            raise ValueError('post_id is required')
        self._preflight_write('repost')
        return self._tweets.retweet(post_id, **self._write_args(kwargs))

    def bookmark(self, post, **kwargs):
        """Bookmark a post via GetXAPI."""
        post_id = self._post_id(post)
        if not post_id:
            raise ValueError('post_id is required')
        self._preflight_write('bookmark')
        return self._tweets.bookmark(post_id, **self._write_args(kwargs))

    def unbookmark(self, post, **kwargs):
        """Remove a post from bookmarks via GetXAPI."""
        post_id = self._post_id(post)
        if not post_id:
            raise ValueError('post_id is required')
        self._preflight_write('unbookmark')
        return self._tweets.unbookmark(post_id, **self._write_args(kwargs))

    def _write_args(self, kwargs):
        """Request body fields shared by the tweet-write calls.

        Channel automation stores the target under both ``post`` and
        ``tweet_id`` so every provider family can read it. The id is already
        handed to :class:`GetXAPITweetService` positionally, so forwarding the
        leftover ``tweet_id`` as a body field raised "got multiple values for
        argument 'tweet_id'".
        """
        kwargs.pop('tweet_id', None)
        kwargs['auth_token'] = self._auth_token
        return kwargs

    def follow(self, screen_name=None, target_user_id=None, **kwargs):
        """Follow a user via GetXAPI."""
        if not target_user_id and not screen_name:
            raise ValueError('target_user_id or screen_name is required')
        self._preflight_write(
            'follow', screen_name=screen_name, target_user_id=target_user_id)
        if target_user_id:
            user = self._users.info_by_id(target_user_id, billable=False)
            screen_name = user.get('username')
        if not screen_name:
            raise ValueError('target_user_id or screen_name is required')
        kwargs['auth_token'] = self._auth_token
        return self._users.follow(screen_name, **kwargs)

    def post_tweet(self, text, **kwargs):
        """Create a new tweet via GetXAPI."""
        if not text:
            raise ValueError('text is required')
        self._preflight_write('post_tweet')
        kwargs['auth_token'] = self._auth_token
        return self._tweets.create(text, **kwargs)

    def get_dms(self, conversation_id, limit=100, cursor=None):
        """Return normalized messages for one conversation."""
        return self._dms.conversation(
            conversation_id, auth_token=self._auth_token,
            count=limit, cursor=cursor)

    def send_dm(self, recipient_id, text):
        """Send a direct message via GetXAPI."""
        self._preflight_write('send_dm')
        return self._dms.send(recipient_id, text, auth_token=self._auth_token)

    def fetch_groups(self, account, limit=100, create_missing=True):
        """Sync the account's DM conversations into discuss channels.

        GetXAPI's inbox mixes group and 1:1 conversations; both are synced —
        groups into ``x_group`` channels, 1:1 conversations into ``x``
        channels carrying the peer on ``x_partner_id`` so DMs can be sent
        through GetXAPI. An existing 1:1 channel is backfilled with its peer;
        its stored channel type is left untouched (Odoo forbids changing
        ``channel_type`` after creation), which is safe because send routing
        resolves the conversation shape at send time.

        The whole inbox is paginated (cursor + ``has_more``) so conversations
        beyond the first page are seen, and records are upserted with raw SQL
        bulk statements for speed.
        """
        return self._sync_inbox(account, limit=limit, create_missing=create_missing)

    def sync_chat_names(self, account, limit=200, create_missing=True):
        """Refresh chat channel names (and create missing channels) from GetXAPI.

        Lists DM/group conversations — walking every inbox page via
        ``cursor`` / ``has_more`` — updates the name of every channel that
        already exists, and creates channels for conversations that were never
        seen before (``create_missing``). Payloads are persisted with raw SQL
        bulk statements so one run stays fast even with a large inbox; the
        returned dict carries both the legacy name-sync counts
        (``conversations``/``updated``/``unchanged``/``missing``) and the
        create-side counts (``groups``/``created``/``members``).
        """
        if not self._auth_token:
            raise ValueError(
                'Set the GetXAPI Auth Token on this account first — the '
                'inbox list requires it.')
        return self._sync_inbox(account, limit=limit, create_missing=create_missing)

    def _iter_inbox_conversations(self, auth_token, limit=50, tab='all'):
        """Yield every conversation in the account's DM inbox.

        GetXAPI paginates the inbox (~50 per page); walking every page via
        ``cursor`` / ``has_more`` guarantees the whole inbox is seen, not just
        the most recent page. A safety cap and a repeated-cursor guard stop
        runaway loops on a misbehaving upstream.
        """
        cursor = None
        seen_cursors = set()
        pages = 0
        max_pages = 200  # ~50/page -> up to ~10k conversations
        while pages < max_pages:
            result = self._dms.list(
                auth_token=auth_token, count=limit, cursor=cursor, tab=tab)
            conversations = result.get('conversations', [])
            if conversations:
                yield conversations
            pages += 1
            next_cursor = result.get('cursor')
            has_more = bool(result.get('has_more', bool(next_cursor)))
            if not has_more or not next_cursor:
                return
            if next_cursor in seen_cursors:
                _LOGGER.warning(
                    'GetXAPI inbox pagination stuck on cursor %r (page %s) '
                    'for account %s; stopping.',
                    next_cursor, pages, self.account.id)
                return
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        _LOGGER.warning(
            'GetXAPI inbox pagination hit the %s-page cap for account %s; '
            'stopping.', max_pages, self.account.id)

    def _sync_inbox(self, account, limit=50, create_missing=True, tab='all'):
        """Full-inbox sync shared by ``fetch_groups`` and ``sync_chat_names``.

        Lists every page of the account's DM inbox and persists the
        conversations with raw SQL: one batch partner upsert, one batch
        channel upsert, one batch member upsert, and batched name/peer
        updates. Works on reduced/field-limited recordsets and bypasses the
        ORM per-record round trips so a run with thousands of conversations
        stays fast.

        Returns:
            {'conversations', 'groups', 'created', 'updated', 'unchanged',
             'missing', 'members'}
        """
        env = self.env
        cr = env.cr
        env.flush_all()
        my_user_id = str(getattr(account, 'twitter_user_id', '') or '')
        owner_partner_ids = []
        if self.env.user.partner_id:
            owner_partner_ids.append(self.env.user.partner_id.id)
        if account.create_uid and account.create_uid.partner_id:
            owner_partner_ids.append(account.create_uid.partner_id.id)
        owner_partner_ids = [
            pid for pid in dict.fromkeys(owner_partner_ids) if pid]

        # 1. Walk the whole inbox, deduplicating by conversation id.
        conversations = {}
        groups = 0
        for page in self._iter_inbox_conversations(
                self._auth_token, limit=limit, tab=tab):
            for conv in page:
                conv_id = str(conv.get('conversation_id') or '')
                if not conv_id:
                    continue
                if conv_id not in conversations:
                    conversations[conv_id] = conv
                    if x_conversation_is_group(
                            conv_id, group=conv.get('group'),
                            conv_type=conv.get('type', '')):
                        groups += 1

        # 2. Upsert participants as res.partner, keyed by x_user_id.
        uid_info = {}
        for conv in conversations.values():
            for p in conv.get('participants') or []:
                x_uid = str(p.get('id') or '').strip()
                if not x_uid:
                    continue
                info = uid_info.setdefault(
                    x_uid, {'name': '', 'username': ''})
                if not info['name']:
                    info['name'] = str(
                        p.get('name') or p.get('userName') or '')
                if not info['username']:
                    info['username'] = str(p.get('userName') or '')

        partner_by_uid = {}
        if uid_info:
            cr.execute(
                'SELECT id, x_user_id FROM res_partner '
                'WHERE x_user_id IN %s', (tuple(uid_info),))
            for pid, x_uid in cr.fetchall():
                partner_by_uid.setdefault(x_uid, pid)
        missing_uids = [x_uid for x_uid in uid_info
                        if x_uid not in partner_by_uid]
        members = 0
        if missing_uids:
            partner_insert_cols = _existing_columns(cr, 'res_partner', (
                'name', 'complete_name', 'type', 'active', 'is_company',
                'partner_share', 'autopost_bills', 'group_rfq', 'group_on',
                'is_created_by_ocr', 'x_user_id', 'x_username', 'create_uid',
                'write_uid', 'create_date', 'write_date',
                'commercial_partner_id'))
            insert_cols = [
                col for col in partner_insert_cols
                if col != 'commercial_partner_id']
            rows = []
            params = []
            for x_uid in missing_uids:
                info = uid_info[x_uid]
                name = info['name'] or info['username'] or x_uid
                vals = {
                    'name': name,
                    'complete_name': name,
                    'type': 'contact',
                    'active': True,
                    'is_company': False,
                    'partner_share': True,
                    'autopost_bills': 'ask',
                    'group_rfq': 'default',
                    'group_on': 'default',
                    'is_created_by_ocr': False,
                    'x_user_id': x_uid,
                    'x_username': info['username'],
                    'create_uid': 1,
                    'write_uid': 1,
                    'create_date': 'now()',
                    'write_date': 'now()',
                }
                placeholder = [
                    'now()' if col in ('create_date', 'write_date')
                    else '%s'
                    for col in insert_cols]
                rows.append('(%s)' % ', '.join(placeholder))
                params += [vals[col] for col in insert_cols
                           if col not in ('create_date', 'write_date')]
            cr.execute(
                'INSERT INTO res_partner (%s) VALUES %s '
                'RETURNING id, x_user_id'
                % (', '.join(insert_cols), ', '.join(rows)), params)
            for pid, x_uid in cr.fetchall():
                partner_by_uid.setdefault(x_uid, pid)
            members = len(missing_uids)
            if 'commercial_partner_id' in partner_insert_cols:
                cr.execute(
                    'UPDATE res_partner SET commercial_partner_id = id '
                    'WHERE id IN %s AND commercial_partner_id IS NULL',
                    (tuple(partner_by_uid[x_uid] for x_uid in missing_uids),))

        # 3. Resolve name / type / members per conversation.
        tasks = []
        for conv_id, conv in conversations.items():
            is_group = x_conversation_is_group(
                conv_id, group=conv.get('group'),
                conv_type=conv.get('type', ''))
            name, _ctype = self._chat_name_from_conversation(conv, my_user_id)
            name = name or conv_id
            peer_partner_id = None
            participant_ids = []
            for p in conv.get('participants') or []:
                x_uid = str(p.get('id') or '').strip()
                pid = partner_by_uid.get(x_uid) if x_uid else None
                if not pid:
                    continue
                participant_ids.append(pid)
                if (not is_group and x_uid != my_user_id
                        and peer_partner_id is None):
                    peer_partner_id = pid
            tasks.append({
                'conv_id': conv_id,
                'is_group': is_group,
                'name': name,
                'peer_partner_id': peer_partner_id,
                'participant_ids': participant_ids,
            })

        # 4. Load existing channels for this account in one bulk query.
        conv_ids = [task['conv_id'] for task in tasks]
        existing_ids = {}
        channel_names = {}
        channel_peers = {}
        if conv_ids:
            cr.execute(
                'SELECT id, x_conversation_id, name, x_partner_id FROM '
                'discuss_channel WHERE x_account_id = %s '
                'AND x_conversation_id IN %s',
                (account.id, tuple(conv_ids)))
            for cid, cconv_id, cname, cpeer in cr.fetchall():
                existing_ids.setdefault(cconv_id, cid)
                channel_names[cid] = cname
                channel_peers[cid] = cpeer

        # 5. Split into create / update buckets.
        created = unchanged = updated = missing = 0
        to_create = []
        name_updates = []
        x_partner_updates = []
        for task in tasks:
            cid = existing_ids.get(task['conv_id'])
            if not cid:
                if create_missing:
                    to_create.append(task)
                else:
                    missing += 1
                continue
            if task['name'] and channel_names.get(cid) != task['name']:
                name_updates.append((cid, task['name']))
                updated += 1
            else:
                unchanged += 1
            if (not task['is_group'] and task['peer_partner_id']
                    and channel_peers.get(cid) != task['peer_partner_id']):
                x_partner_updates.append((cid, task['peer_partner_id']))

        # 6. Bulk-insert missing channels.
        new_channel_ids = {}
        if to_create:
            channel_insert_cols = _existing_columns(cr, 'discuss_channel', (
                'name', 'channel_type', 'x_account_id', 'x_partner_id',
                'x_conversation_id', 'uuid', 'x_company_id', 'create_uid',
                'write_uid', 'active', 'create_date', 'write_date'))
            rows = []
            params = []
            company_id = account.company_id.id or None
            for task in to_create:
                vals = {
                    'name': task['name'],
                    'channel_type': 'x_group' if task['is_group'] else 'x',
                    'x_account_id': account.id,
                    'x_partner_id': task['peer_partner_id'],
                    'x_conversation_id': task['conv_id'],
                    'uuid': str(uuid.uuid4()),
                    'x_company_id': company_id,
                    'create_uid': 1,
                    'write_uid': 1,
                    'active': True,
                    'create_date': 'now()',
                    'write_date': 'now()',
                }
                placeholder = [
                    'now()' if col in ('create_date', 'write_date')
                    else '%s'
                    for col in channel_insert_cols]
                rows.append('(%s)' % ', '.join(placeholder))
                params += [vals[col] for col in channel_insert_cols
                           if col not in ('create_date', 'write_date')]
            cr.execute(
                'INSERT INTO discuss_channel (%s) VALUES %s '
                'RETURNING id, x_conversation_id'
                % (', '.join(channel_insert_cols), ', '.join(rows)), params)
            for cid, cconv_id in cr.fetchall():
                new_channel_ids[cconv_id] = cid
                created += 1

        # 7. Bulk-apply name and peer backfills with a single UPDATE each.
        if name_updates:
            vrows = ', '.join(['(%s, %s)'] * len(name_updates))
            params = []
            for cid, ch_name in name_updates:
                params += [cid, ch_name]
            cr.execute(
                'UPDATE discuss_channel c SET name = v.channel_name, '
                'write_uid = 1, write_date = now() '
                'FROM (VALUES %s) AS v(id, channel_name) '
                'WHERE c.id = v.id' % vrows, params)
        if x_partner_updates:
            vrows = ', '.join(['(%s, %s)'] * len(x_partner_updates))
            params = []
            for cid, pid in x_partner_updates:
                params += [cid, pid]
            cr.execute(
                'UPDATE discuss_channel c SET x_partner_id = v.partner_id, '
                'write_uid = 1, write_date = now() '
                'FROM (VALUES %s) AS v(id, partner_id) '
                'WHERE c.id = v.id' % vrows, params)

        # 8. Reconcile channel members (cumulative, never removes).
        channel_members = {}
        for task in tasks:
            cid = (new_channel_ids.get(task['conv_id'])
                   or existing_ids.get(task['conv_id']))
            if not cid:
                continue
            if task['conv_id'] in new_channel_ids:
                pool = task['participant_ids'] + owner_partner_ids
            else:
                pool = task['participant_ids']
            if pool:
                channel_members.setdefault(cid, set()).update(pool)
        if channel_members:
            existing_members = set()
            cids = list(channel_members)
            for start in range(0, len(cids), 1000):
                chunk = cids[start:start + 1000]
                cr.execute(
                    'SELECT channel_id, partner_id FROM '
                    'discuss_channel_member WHERE channel_id IN %s '
                    'AND partner_id IS NOT NULL', (tuple(chunk),))
                existing_members.update(cr.fetchall())
            to_insert = [
                (cid, pid) for cid, pool in channel_members.items()
                for pid in pool if (cid, pid) not in existing_members]
            if to_insert:
                rows = ', '.join(['(%s, %s, 0)'] * len(to_insert))
                params = []
                for cid, pid in to_insert:
                    params += [cid, pid]
                cr.execute(
                    'INSERT INTO discuss_channel_member (channel_id, '
                    'partner_id, new_message_separator) VALUES %s' % rows,
                    params)

        env.invalidate_all()

        _LOGGER.info(
            'GetXAPI inbox sync for account %s: %s conversation(s), '
            '%s group(s), %s channel(s) created, %s updated, %s unchanged, '
            '%s missing, %s new partner(s)',
            account.id, len(conversations), groups, created, updated,
            unchanged, missing, members)
        return {
            'conversations': len(conversations),
            'groups': groups,
            'created': created,
            'updated': updated,
            'unchanged': unchanged,
            'missing': missing,
            'members': members,
        }

    @staticmethod
    def _chat_name_from_conversation(conv, my_user_id=''):
        """Derive a human name for one GetXAPI conversation entry."""
        participants = conv.get('participants') or []

        def _label(p):
            return str(p.get('userName') or p.get('username')
                       or p.get('name') or '')

        is_group = x_conversation_is_group(
            conv.get('conversation_id') or conv.get('id'),
            group=conv.get('group'), conv_type=conv.get('type', ''))
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
                    if channel._save_x_message(
                        direction='outbound' if msg.get('from_me') else 'inbound',
                        external_id=msg['id'],
                        body=msg.get('text', ''),
                        external_created_at=msg.get('created_at'),
                        author_partner=author_partner,
                        author_x_id=sender_id,
                    ):
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
