# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Parse, route, dedup and enqueue/process inbound X Activity API events.

The webhook receiver hands us the raw XAA envelope
``{data: {event_uuid, event_type, filter, payload, includes}}``. This service:

- routes the event to the correct ``social.account`` by ``filter.user_id``
  (validating the account actually exists and is an X account),
- deduplicates by ``event_uuid`` (X may redeliver),
- enqueues the real work on the existing ``x.account.task`` queue so the HTTP
  endpoint acknowledges fast and expensive/retryable work happens in the
  background (single-flight per account, backoff, rate-limit aware),
- on execution integrates DM / group-DM events into the existing discuss.channel
  + x.message + res.partner workflow (``_get_x_channel`` / ``_save_x_message``),
  which are already idempotent by external id.
"""

import json
import logging
import hashlib

import psycopg2

from odoo import fields

from . import twitter_errors

_logger = logging.getLogger(__name__)

# Event types we subscribe to and can process.
EVENT_TYPES = (
    'dm.received',
    'chat.received',
    # 'dm.sent',
    # 'chat.sent',
    # 'chat.conversation_join',
)


class TwitterActivity:
    """Routes and processes X Activity API events for ``x_account_twitter``."""

    def __init__(self, env):
        self.env = env

    # ---------------------------------------------------------------- ingress
    def ingest_webhook(self, envelope):
        """Parse + route + dedup + enqueue one inbound webhook envelope.

        Returns ``{'status': 'accepted' | 'skipped' | 'ignored', ...}``. Never
        raises on malformed/unknown events; logs instead. The receiver always
        ACKs to X regardless (best-effort delivery).
        """
        data = self._envelope_data(envelope)
        if not data:
            # Known X control/status envelopes (e.g. replay job notifications)
            # carry no processable activity; acknowledge silently.
            if isinstance(envelope, dict) and 'replay_job_status' in envelope:
                _logger.info('x_account_twitter: ignoring webhook control '
                             'notification (replay_job_status)')
                return {'status': 'ignored', 'reason': 'control_notification'}
            _logger.warning('x_account_twitter: webhook payload has no data object')
            return {'status': 'ignored', 'reason': 'no_data'}
        event_uuid = data.get('event_uuid')
        event_type = data.get('event_type')
        flt = data.get('filter') or {}
        user_id = flt.get('user_id')

        if event_type == 'oauth.revoke':
            # A user revoked app access: mark their account and stop sending.
            self._handle_revoke(user_id)
            return {'status': 'processed', 'event_type': event_type}
        if event_type not in EVENT_TYPES:
            _logger.info('x_account_twitter: ignoring unsupported event %r',
                         event_type)
            return {'status': 'ignored', 'reason': 'unknown_event',
                    'event_type': event_type}
        if not event_uuid or not user_id:
            return {'status': 'ignored', 'reason': 'missing_ids',
                    'event_type': event_type}

        account = self.env['social.account'].sudo().search([
            ('twitter_user_id', '=', str(user_id)),
        ], limit=1)
        if not account or account.media_type != 'twitter':
            _logger.warning(
                'x_account_twitter: no X account for event user_id=%s', user_id)
            return {'status': 'ignored', 'reason': 'no_account',
                    'event_type': event_type}

        existing = self.env['x.twitter.event'].sudo().search([
            ('event_uuid', '=', event_uuid),
        ], limit=1)
        if existing:
            _logger.info('x_account_twitter: duplicate event %s ignored', event_uuid)
            return {'status': 'skipped', 'reason': 'duplicate',
                    'event_type': event_type}

        payload = data.get('payload') or {}
        try:
            with self.env.cr.savepoint():
                event = self.env['x.twitter.event'].sudo().create({
                    'event_uuid': event_uuid,
                    'account_id': account.id,
                    'event_type': event_type,
                    'state': 'queued',
                    'payload': json.dumps({
                        'event_uuid': event_uuid,
                        'event_type': event_type,
                        'user_id': str(user_id),
                        'payload': payload,
                    }),
                })
        except psycopg2.IntegrityError:
            # Lost the race against a concurrent delivery of the same event:
            # the UNIQUE(event_uuid) constraint fired between our search and
            # create. The savepoint rolled back just this insert; treat the
            # delivery as a duplicate.
            _logger.info('x_account_twitter: duplicate event %s ignored '
                         '(concurrent)', event_uuid)
            return {'status': 'skipped', 'reason': 'duplicate',
                    'event_type': event_type}
        task = account.env['x.account.task'].create({
            'account_id': account.id,
            'operation': 'process_webhook_event',
            'priority': 10,
            'max_attempts': 5,
            'task_context': json.dumps({'event_uuid': event_uuid}),
        })
        event.write({'task_id': task.id, 'state': 'queued'})
        return {'status': 'accepted', 'event_type': event_type,
                'event_uuid': event_uuid, 'account_id': account.id}

    def process_event(self, event):
        """Execute one queued x.twitter.event (called by the task worker).

        Idempotent: uses the existing idempotent channel/message helpers and
        re-keys every step by external id. Returns a summary dict.
        """
        event = event.sudo()
        event_uuid = event.event_uuid
        if event_uuid and self.env['x.twitter.event'].sudo().search_count([
            ('event_uuid', '=', event_uuid),
            ('state', 'in', ('done', 'processing')),
            ('id', '!=', event.id),
        ]):
            _logger.info('x_account_twitter: event %s already processed', event_uuid)
            event.write({'state': 'done'})
            return {'processed': False, 'duplicate': True}
        event.write({'state': 'processing'})
        try:
            data = json.loads(event.payload or '{}')
            payload = data.get('payload') or {}
            event_type = event.event_type
            account = event.account_id
            if not account:
                event.write({'state': 'skipped', 'error': 'missing_account'})
                return {'processed': False, 'reason': 'no_account'}
            result = self._handle(event_type, account, payload)
            event.write({'state': 'done'})
            return {'processed': True, 'event_type': event_type, **result}
        except twitter_errors.TwitterTemporaryError as exc:
            # Retryable: let the task queue back off and retry.
            event.write({'state': 'failed', 'error': str(exc)})
            raise
        except Exception as exc:
            # Non-retryable (bad payload): mark done-with-error and move on.
            _logger.exception('x_account_twitter: failed to process event %s', event_uuid)
            event.write({'state': 'done', 'error': str(exc)})
            return {'processed': False, 'error': str(exc)}

    # ------------------------------------------------------------ dispatcher
    def _handle(self, event_type, account, payload):
        if event_type == 'dm.received':
            return self._handle_dm(account, payload, outbound=False)
        if event_type == 'chat.received':
            return self._handle_chat(account, payload, outbound=False)
        # if event_type == 'dm.sent':
        #     return self._handle_dm(account, payload, outbound=True)
        # if event_type in ('chat.sent', 'chat.conversation_join'):
        #     return self._handle_chat(account, payload,
        #                              outbound=(event_type == 'chat.sent'))
        return {'processed': False, 'reason': 'unsupported'}

    def _handle_dm(self, account, payload, outbound=False):
        """Store a legacy (unencrypted) DM event into its discuss channel."""
        events = payload.get('direct_message_events') or []
        touch = payload.get('_event_uuid')
        count = 0
        sender_name_map = {}
        users = payload.get('users') or {}
        for x_uid, info in users.items():
            inner = (info or {}).get('data') or info
            sender_name_map[str(x_uid)] = inner.get('name') or inner.get('username') or ''
        channel_model = self.env['discuss.channel'].sudo()
        partner_model = self.env['res.partner'].sudo()
        for ev in events:
            if not isinstance(ev, dict) or ev.get('type') != 'message_create':
                continue
            mc = ev.get('message_create') or {}
            sender_id = mc.get('sender_id')
            recipient_id = (mc.get('target') or {}).get('recipient_id')
            message_id = ev.get('id')
            text = (mc.get('message_data') or {}).get('text', '')
            if not sender_id or not message_id:
                continue
            # Guard: a DM event whose text is nothing but a run of backslashes
            # (or whitespace) is almost always a payload/escaping artifact (e.g.
            # an encrypted event miscategorized as a legacy DM, or a double-
            # decoded envelope), not a real message. Never surface it as body.
            stripped = (text or '').replace('\\', '')
            if (text or '').strip() and not stripped.strip():
                _logger.warning(
                    'x_account_twitter: skipping DM %s whose body is a pure '
                    'backslash/escaping artifact (%d chars); treating as '
                    'payload noise', message_id, len((text or '').strip()))
                continue
            # 1:1 DMs use the canonical "{smaller}-{larger}" conversation id.
            conv_id = self._conversation_key(sender_id, recipient_id)
            conversation = conv_id
            channel = channel_model._get_x_channel(
                account, conversation_id=conversation, channel_type='x',
                create_if_not_found=True)
            author_partner = self._ensure_partner(
                partner_model, sender_id, sender_name_map.get(sender_id, ''))
            channel._save_x_message(
                direction='outbound' if outbound else 'inbound',
                external_id=str(message_id),
                body=text or '',
                external_created_at=ev.get('created_timestamp'),
                author_partner=author_partner,
                author_x_id=sender_id,
                author_x_username=author_partner.x_username if author_partner else False,
            )
            count += 1
        return {'messages': count}

    def _handle_chat(self, account, payload, outbound=False):
        """Record an XChat (encrypted) group/DM event.

        XChat message bodies are end-to-end encrypted and delivered as
        ``encoded_event`` blobs. When the account has a Chat key blob the event
        is decrypted (plaintext stored); otherwise it is recorded with the
        existing ``encrypted`` marker and an empty body — we never fabricate a
        body. The ``chat.conversation_join`` event is used to ensure the group
        channel exists so later membership sync can attach members.
        """
        conversation_id = payload.get('conversation_id')
        sender_id = payload.get('sender_id')
        message_id = payload.get('id')
        created_at_msec = payload.get('created_at_msec')
        conv = conversation_id or ''
        # XChat groups use colon-separated participant ids or a 'g...' chat id;
        # 1:1 conversations use the canonical "{lo}-{hi}" id.
        is_group = (':' in conv) or conv.startswith('g')
        channel_type = 'x_group' if is_group else 'x'
        channel_model = self.env['discuss.channel'].sudo()
        channel = False
        if conversation_id:
            channel = channel_model._get_x_channel(
                account, conversation_id=conversation_id,
                channel_type=channel_type, create_if_not_found=True)
            self._sync_channel_members(
                channel, account, conversation_id=conversation_id,
                sender_id=sender_id, channel_type=channel_type,
                participant_ids=payload.get('participant_ids')
                or payload.get('member_ids'))
        if message_id and sender_id and channel:
            author_partner = self.env['res.partner'].sudo().search(
                [('x_user_id', '=', str(sender_id))], limit=1)
            body, decrypted = self._decrypt_chat_event(account, payload)
            channel._save_x_message(
                direction='outbound' if outbound else 'inbound',
                external_id=str(message_id),
                body=body,
                external_created_at=created_at_msec,
                author_partner=author_partner,
                author_x_id=sender_id,
                encrypted=not decrypted,
                no_mail=True,
            )
            return {'messages': 1, 'encrypted': not decrypted,
                    'channel': channel.id}
        return {'messages': 0, 'encrypted': True,
                'channel': channel.id if channel else 0}

    # ------------------------------------------------------------ chat helpers
    def _sync_channel_members(self, channel, account, conversation_id='',
                              sender_id=None, channel_type='x_group',
                              participant_ids=None):
        """Ensure the channel has the participants it needs to surface in Discuss.

        Webhook-created channels only carry the system user as member, so they
        never appear in any user's Discuss sidebar. Add the account owner, the
        event sender, explicit ``participant_ids`` (join events), and any
        participants encoded in the conversation id (``uid:uid`` / ``uid-uid``)
        as members — the same membership the group-sync path maintains. Only
        ever adds members; never removes.
        """
        member_model = self.env['discuss.channel.member'].sudo()
        partner_model = self.env['res.partner'].sudo()
        want = set()
        owner_uid = account.twitter_user_id
        if owner_uid:
            want.add(str(owner_uid))
        if sender_id:
            want.add(str(sender_id))
        for pid in (participant_ids or []):
            if pid:
                want.add(str(pid))
        conv = str(conversation_id or '')
        # A 1:1 conversation id is "<uid>:<uid>" or "<uid>-<uid>": add every
        # half (the owner + the counterpart). Group ids are 'g...' or a plain
        # numeric group id; a single uid is just that participant.
        halves = [h for h in conv.replace(':', '-').split('-') if h]
        if halves and all(h.isdigit() for h in halves):
            for half in halves:
                want.add(half)
        existing_partners = set(channel.channel_member_ids.partner_id.ids)
        for x_uid in sorted(want, key=lambda s: (len(s), s)):
            if not x_uid:
                continue
            partner = partner_model.search([('x_user_id', '=', x_uid)], limit=1)
            if not partner:
                partner = partner_model.create({
                    'name': x_uid,
                    'x_user_id': x_uid,
                })
            if partner.id not in existing_partners:
                member_model.create({
                    'channel_id': channel.id,
                    'partner_id': partner.id,
                })
                existing_partners.add(partner.id)

    def _decrypt_chat_event(self, account, payload):
        """Attempt to decrypt a webhook ``encoded_event`` blob.

        Returns ``(body, decrypted)``. When the account has no Chat key blob, or
        decryption fails/returns no usable message, returns ``('', False)`` so
        the caller keeps the ``encrypted`` marker — never invent a body.
        """
        encoded = payload.get('encoded_event')
        if not encoded:
            _logger.warning('x_account_twitter: encrypted chat event missing '
                            'encoded_event account_id=%s event_id=%s',
                            account.id, payload.get('id'))
            return '', False
        key_change = payload.get('conversation_key_change_event') or ''
        try:
            from odoo.addons.x_account_twitter.services.xchat_decryptor import (
                XChatDecryptor)
            from odoo.addons.x_account_twitter.services.twitter_api_client import (
                TwitterApiClient)
            decryptor = XChatDecryptor(
                self.env, account, client=TwitterApiClient(account))
            if not decryptor.available:
                _logger.warning('x_account_twitter: chat decryption key missing '
                                'account_id=%s event_id=%s key_mode=%s',
                                account.id, payload.get('id'),
                                account.x_chat_key_mode or 'key_blob')
                return '', False
            # Feed any key-change event first so the conversation key is
            # recoverable, then decrypt the message blob. Pass the sender so a
            # different-user sender in group chats can be signature-verified.
            result = decryptor.decrypt_events(
                [encoded],
                key_change_events=[key_change] if key_change else None,
                sender_ids=[payload.get('sender_id')])
            errors = result.get('errors') or {}
            if errors:
                # Chat XDK intentionally returns per-event crypto failures in
                # ``errors`` rather than raising.  Log only stable metadata;
                # never log the opaque event, key blob, PIN, or plaintext.
                error_kinds = sorted({str(error)[:160] for error in errors.values()})
                digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]
                _logger.warning(
                    'x_account_twitter: Chat XDK rejected webhook event '
                    'account_id=%s event_id=%s sender_id=%s key_mode=%s '
                    'key_version=%s ciphertext_len=%s key_change_len=%s '
                    'ciphertext_sha256=%s errors=%s',
                    account.id, payload.get('id'), payload.get('sender_id'),
                    account.x_chat_key_mode or 'key_blob',
                    account.x_chat_signing_key_version or 'unset', len(encoded),
                    len(key_change), digest, error_kinds)
            for msg in result.get('messages') or []:
                ev = msg.get('event') or {}
                if ev.get('type') == 'Message':
                    content = ev.get('content') or {}
                    text = content.get('text', '')
                    urls = content.get('urls') or []
                    if urls:
                        url_texts = [u.get('expanded_url') or u.get('url') or '' for u in urls if u]
                        url_texts = [u for u in url_texts if u]
                        if url_texts:
                            text = (text + '\n' + '\n'.join(url_texts)).strip() if text else '\n'.join(url_texts)
                    if text:
                        return text, True
        except Exception as exc:
            # Log the failure reason (bounded, metadata only — the XDK raises
            # ValueError with descriptive messages such as a wrong PIN or a
            # missing secure-backup config, which the exception type alone
            # cannot distinguish).
            _logger.warning(
                'x_account_twitter: Chat XDK decryption setup failed '
                'account_id=%s event_id=%s key_mode=%s error=%s: %s '
                '(keeping encrypted marker)', account.id, payload.get('id'),
                account.x_chat_key_mode or 'key_blob', type(exc).__name__,
                str(exc)[:200], exc_info=False)
        return '', False

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _envelope_data(envelope):
        if not isinstance(envelope, dict):
            return None
        data = envelope.get('data')
        return data if isinstance(data, dict) else None

    @staticmethod
    def _conversation_key(sender_id, recipient_id):
        try:
            a, b = sorted([int(sender_id), int(recipient_id)])
            return '%s-%s' % (a, b)
        except (TypeError, ValueError):
            return '%s-%s' % (sender_id, recipient_id)

    def _ensure_partner(self, partner_model, x_uid, name=''):
        partner = partner_model.search([('x_user_id', '=', str(x_uid))], limit=1)
        if partner:
            return partner
        partner = partner_model.create({
            'name': name or str(x_uid),
            'x_user_id': str(x_uid),
        })
        return partner

    def _handle_revoke(self, user_id):
        if not user_id:
            return
        account = self.env['social.account'].sudo().search([
            ('twitter_user_id', '=', str(user_id)),
        ], limit=1)
        if account:
            account.write({'x_connection_status': 'disconnected'})
            _logger.info('x_account_twitter: user %s revoked app access', user_id)
