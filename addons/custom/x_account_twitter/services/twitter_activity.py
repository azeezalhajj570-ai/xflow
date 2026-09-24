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
from .twitter_group_sync import canonical_chat_conversation_id

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
        # One X Chat decryptor per account, reused across the events of a
        # batch: building a fresh decryptor per event re-ran the Juicebox
        # unlock, the public-key API fetch and the signing-version write for
        # every single event, which is what made a batch outrun
        # ``limit_time_real`` and get killed mid-sweep.
        self._chat_decryptors = {}
        # (account_id, conversation_id) pairs whose key chain was already
        # pulled from the Chat API in this batch, so an unrecoverable
        # conversation cannot turn every event of the batch into an API call.
        self._key_seed_attempted = set()
        # Chat decrypt outcomes for this batch, per account id: how many
        # encrypted deliveries were stored vs dropped for lack of plaintext.
        # Accumulated in memory and flushed once per batch — a write per
        # delivery would add a row lock to every event of a busy account.
        self._chat_decrypt_outcomes = {}
        # Registered public-key versions per account id, fetched for the
        # decrypt-failure log line at most once per batch. The GET shares the
        # ``public_keys`` endpoint whose 24h budget key registration needs, so a
        # fetch per undecryptable event is what starved registration.
        self._public_key_versions_cache = {}

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
                _logger.info('x_account_twitter: webhook control notification '
                             '(replay_job_status) payload=%s',
                             json.dumps(envelope, default=str))
                return {'status': 'ignored', 'reason': 'control_notification'}
            _logger.warning(
                'x_account_twitter: webhook payload has no data object keys=%s',
                list(envelope) if isinstance(envelope, dict) else type(envelope))
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

        account = self.env['social.account'].sudo().with_context(
            active_test=False).search([
                ('media_type', '=', 'twitter'),
                ('twitter_user_id', '=', str(user_id)),
            ], limit=1)
        if not account:
            _logger.warning(
                'x_account_twitter: no X account for event user_id=%s', user_id)
            return {'status': 'ignored', 'reason': 'no_account',
                    'event_type': event_type}
        if not account.active:
            # The account was archived but its XAA subscription is still live
            # on X's side, so X keeps delivering events for it. Resolving the
            # archived account (active_test=False) lets us skip quietly here —
            # no event row, no queue task — while the self-heal cron prunes the
            # dead subscription (archiving alone does not delete it).
            _logger.info(
                'x_account_twitter: event user_id=%s belongs to archived X '
                'account %s (%s); ignoring and not enqueuing',
                user_id, account.id, account.name)
            return {'status': 'ignored', 'reason': 'account_archived',
                    'event_type': event_type, 'account_id': account.id}
        configured_events = account.x_subscription_event_ids.mapped('name')
        if not configured_events:
            configured_events = ['dm.received', 'chat.received']
        if event_type not in configured_events:
            _logger.info(
                'x_account_twitter: event type %s not configured for account %s',
                event_type, account.id)
            return {'status': 'ignored', 'reason': 'event_not_configured',
                    'event_type': event_type}

        existing = self.env['x.twitter.event'].sudo().search([
            ('account_id', '=', account.id),
            ('event_uuid', '=', event_uuid),
        ], limit=1)
        if existing:
            _logger.info('x_account_twitter: duplicate event %s ignored', event_uuid)
            return {'status': 'skipped', 'reason': 'duplicate',
                    'event_type': event_type}

        payload = data.get('payload') or {}
        if not self._envelope_has_message_content(event_type, payload):
            # Read receipts, typing indicators, membership/key-change-only
            # deliveries: nothing to store, so don't burn a queue task on them.
            # The event row is still recorded (state ``skipped``) for audit and
            # delivery de-duplication.
            try:
                with self.env.cr.savepoint():
                    self.env['x.twitter.event'].sudo().create({
                        'event_uuid': event_uuid,
                        'account_id': account.id,
                        'event_type': event_type,
                        'state': 'skipped',
                        'payload': json.dumps({
                            'event_uuid': event_uuid,
                            'event_type': event_type,
                            'user_id': str(user_id),
                            'payload': payload,
                        }),
                    })
            except psycopg2.IntegrityError:
                pass
            _logger.info(
                'x_account_twitter: %s event %s carries no message body; '
                'recorded as skipped without a task', event_type, event_uuid)
            return {'status': 'skipped', 'reason': 'no_message_content',
                    'event_type': event_type, 'event_uuid': event_uuid}
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

    @staticmethod
    def _close_event(event, state, error=None):
        """Move an event to a terminal state and drop its payload.

        The payload is only needed while the delivery is queued (or retried
        after a temporary failure). Once processed, the message lives in its
        channel and any key-change blob in ``x.twitter.key.change``, so the
        large payload is cleared to keep the table from growing without bound.
        """
        vals = {'state': state, 'payload': False}
        if error:
            vals['error'] = error
        event.write(vals)

    def process_event(self, event):
        """Execute one queued x.twitter.event (called by the task worker).

        Idempotent: uses the existing idempotent channel/message helpers and
        re-keys every step by external id. Returns a summary dict.
        """
        event = event.sudo()
        event_uuid = event.event_uuid
        event.write({'state': 'processing'})
        try:
            data = json.loads(event.payload or '{}')
            payload = data.get('payload') or {}
            event_type = event.event_type
            account = event.account_id
            if not account:
                self._close_event(event, 'skipped', 'missing_account')
                return {'processed': False, 'reason': 'no_account'}
            result = self._handle(event_type, account, payload)
            self._close_event(event, 'done')
            self._flush_chat_decrypt_outcomes()
            return {'processed': True, 'event_type': event_type, **result}
        except twitter_errors.TwitterTemporaryError as exc:
            # Retryable: let the task queue back off and retry.
            event.write({'state': 'failed', 'error': str(exc)})
            raise
        except Exception as exc:
            # Non-retryable (bad payload): mark done-with-error and move on —
            # unless the cursor/connection itself died: then no further write
            # can succeed and the event must NOT be consumed. Re-raise so the
            # task queue / cron rolls the whole transaction back and retries.
            _logger.exception('x_account_twitter: failed to process event %s', event_uuid)
            if self._is_fatal_db_error(exc):
                raise
            self._close_event(event, 'done', str(exc))
            return {'processed': False, 'error': str(exc)}

    def process_events_batch(self, events):
        """Process multiple queued x.twitter.events in batch.

        Groups events by account and conversation to optimize channel lookups.
        Returns a summary dict with counts of processed/skipped events and messages.
        """
        events = events.sudo()
        processed = 0
        skipped = 0
        messages = 0
        errors = []
        for event in events:
            event_uuid = event.event_uuid
            try:
                event.write({'state': 'processing'})
                data = json.loads(event.payload or '{}')
                payload = data.get('payload') or {}
                event_type = event.event_type
                account = event.account_id
                if not account:
                    self._close_event(event, 'skipped', 'missing_account')
                    skipped += 1
                    continue
                result = self._handle(event_type, account, payload)
                self._close_event(event, 'done')
                processed += 1
                messages += result.get('messages', 0)
                # A delivery with nothing readable is skipped, not failed, so
                # counting it is what lets the task show it stored nothing.
                if result.get('skipped'):
                    skipped += 1
            except twitter_errors.TwitterTemporaryError as exc:
                event.write({'state': 'failed', 'error': str(exc)})
                errors.append({'event_uuid': event_uuid, 'error': str(exc)})
            except Exception as exc:
                _logger.exception('x_account_twitter: batch failed to process event %s', event_uuid)
                if self._is_fatal_db_error(exc):
                    # The cursor/connection is dead: every remaining event in
                    # this batch would fail the same way and be wrongly marked
                    # done. Abort the batch and let the caller roll back the
                    # transaction; unprocessed events stay queued for retry.
                    raise
                try:
                    self._close_event(event, 'done', str(exc))
                except Exception:
                    pass
                processed += 1
                errors.append({'event_uuid': event_uuid, 'error': str(exc)})
        self._flush_chat_decrypt_outcomes()
        return {
            'processed': processed,
            'skipped': skipped,
            'messages': messages,
            'errors': len(errors),
        }

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
            text = self._usable_dm_text(ev)
            if text is None:
                continue
            mc = ev.get('message_create') or {}
            sender_id = mc.get('sender_id')
            recipient_id = (mc.get('target') or {}).get('recipient_id')
            message_id = ev.get('id')
            if not sender_id or not message_id:
                continue
            # 1:1 DMs use the canonical "{smaller}-{larger}" conversation id.
            conv_id = self._conversation_key(sender_id, recipient_id)
            conversation = conv_id
            channel = channel_model._get_x_channel(
                account, conversation_id=conversation, channel_type='x',
                create_if_not_found=True)
            author_partner = self._ensure_partner(
                partner_model, sender_id, sender_name_map.get(sender_id, ''))
            if channel._save_x_message(
                direction='outbound' if outbound else 'inbound',
                external_id=str(message_id),
                body=text or '',
                external_created_at=ev.get('created_timestamp'),
                author_partner=author_partner,
                author_x_id=sender_id,
                author_x_username=author_partner.x_username if author_partner else False,
            ):
                count += 1
        return {'messages': count}

    def _handle_chat(self, account, payload, outbound=False):
        """Record an XChat (encrypted) group/DM event.

        XChat message bodies are end-to-end encrypted and delivered as
        ``encoded_event`` blobs. When the account has a Chat key blob the event
        is decrypted (plaintext stored); otherwise the event carries no storable
        body, so no ``x.message`` is created — the conversation and its members
        are still ensured so the chat stays visible, and the undecryptable
        state is reported back through the ``encrypted`` flag. The
        ``chat.conversation_join`` event is used to ensure the group channel
        exists so later membership sync can attach members.
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
            if not (body or '').strip():
                # Some deliveries carry the text in the clear even though the
                # decryptor only understands ``encoded_event``.
                plain = (payload.get('text') or '').strip()
                if plain:
                    body, decrypted = plain, True
            if not (body or '').strip():
                # Nothing readable: storing a body-less row only ever produced
                # an invisible marker, so skip it. The channel-level sync status
                # is what reports the encrypted state.
                _logger.info(
                    'x_account_twitter: chat event %s has no plaintext '
                    '(account_id=%s, channel_id=%s); not stored',
                    message_id, account.id, channel.id)
                self._note_chat_decrypt_outcome(account, stored=False)
                return {'messages': 0, 'encrypted': not decrypted,
                        'skipped': 'no_plaintext', 'channel': channel.id}
            channel._save_x_message(
                direction='outbound' if outbound else 'inbound',
                external_id=str(message_id),
                body=body,
                external_created_at=created_at_msec,
                author_partner=author_partner,
                author_x_id=sender_id,
                no_mail=True,
            )
            self._note_chat_decrypt_outcome(account, stored=True)
            return {'messages': 1, 'encrypted': False, 'channel': channel.id}
        return {'messages': 0, 'skipped': 'missing_ids',
                'channel': channel.id if channel else 0}

    def _note_chat_decrypt_outcome(self, account, stored):
        """Accumulate one chat delivery's decrypt outcome for this batch."""
        counts = self._chat_decrypt_outcomes.setdefault(
            account.id, {'stored': 0, 'dropped': 0})
        counts['stored' if stored else 'dropped'] += 1

    def _flush_chat_decrypt_outcomes(self):
        """Push this batch's decrypt outcomes onto the accounts.

        The account turns them into a consecutive-unread streak and raises the
        "stopped decrypting" notice when it crosses the alert threshold, so an
        account whose key material broke (no secure-backup config, throttled
        public-key fetch, rejected PIN) tells its users instead of silently
        storing nothing.
        """
        if not self._chat_decrypt_outcomes:
            return
        outcomes, self._chat_decrypt_outcomes = self._chat_decrypt_outcomes, {}
        accounts = self.env['social.account'].sudo().browse(list(outcomes))
        for account in accounts.exists():
            counts = outcomes[account.id]
            account._record_chat_decrypt_outcome(
                stored=counts['stored'], dropped=counts['dropped'])

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
        if not channel or not channel.exists():
            return
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
        try:
            existing_partners = set(channel.channel_member_ids.partner_id.ids)
        except Exception:
            return
        if account.create_uid and account.create_uid.partner_id:
            if account.create_uid.partner_id.id not in existing_partners:
                try:
                    member_model.create({
                        'channel_id': channel.id,
                        'partner_id': account.create_uid.partner_id.id,
                    })
                    existing_partners.add(account.create_uid.partner_id.id)
                except Exception:
                    pass
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
                try:
                    member_model.create({
                        'channel_id': channel.id,
                        'partner_id': partner.id,
                    })
                    existing_partners.add(partner.id)
                except Exception:
                    pass

    @staticmethod
    def _missing_key_error(errors):
        """Whether a decrypt error is a missing/rotated conversation key."""
        return any('no matching key' in str(err).lower()
                   for err in (errors or {}).values())

    def _conversation_key_change_blobs(self, account, conversation_id,
                                       current=''):
        """Key-change blobs for a conversation, from the deduplicated store.

        A webhook delivery carries only its own ``conversation_key_change_event``;
        a message whose key rotated earlier needs the conversation's whole
        key-change chain, which is rebuilt from the distinct blobs persisted in
        ``x.twitter.key.change`` — one row per key, however many deliveries
        repeated it.
        """
        blobs = [current] if current else []
        if not conversation_id:
            return blobs
        changes = self.env['x.twitter.key.change'].sudo().search([
            ('account_id', '=', account.id),
            ('conversation_id', '=', str(conversation_id)),
        ], order='id desc', limit=100)
        for change in changes:
            if change.blob not in blobs:
                blobs.append(change.blob)
        return blobs

    @staticmethod
    def _cached_conversation_keys(account, conversation_id):
        """Conversation keys cached on the account for one conversation.

        The webhook names a 1:1 conversation with the colon-separated id while
        the Chat API and the channel-based fetch use the canonical hyphen id,
        so both cache entries are read — either name may hold the keys.
        """
        cache = account.x_chat_conversation_keys or {}
        keys = dict(cache.get(str(conversation_id)) or {})
        canonical = canonical_chat_conversation_id(conversation_id)
        if canonical != str(conversation_id):
            keys.update(cache.get(canonical) or {})
        return keys

    def _seed_conversation_keys_from_chat_api(self, decryptor, account,
                                              conversation_id):
        """Recover a conversation's key chain from the Chat events feed.

        A webhook delivery for a 1:1 XChat conversation carries no
        ``conversation_key_change_event`` (only group deliveries do), so a
        message whose conversation key was created before this account's
        subscription has no key anywhere local: not in the delivery, not in
        ``x_chat_conversation_keys`` and not in any stored event. ``GET
        /2/chat/conversations/{id}/events`` returns the conversation's whole
        key-change chain in ``meta.conversation_key_events`` and is the only
        remaining source. The request goes out with the canonical id (X's Chat
        API rejects the colon form) while the recovered keys are cached under
        the id this delivery used, which is the id the next lookup reads.

        Returns the conversation's ``{version: base64 key}`` cache; ``{}`` when
        the API exposes no key-change event, the fetch fails, or this
        conversation was already attempted in this batch.
        """
        if not decryptor.client:
            return {}
        attempt_key = (account.id, str(conversation_id))
        if attempt_key in self._key_seed_attempted:
            return {}
        self._key_seed_attempted.add(attempt_key)
        api_id = canonical_chat_conversation_id(conversation_id)
        try:
            data = decryptor.client.request(
                'GET', '/2/chat/conversations/%s/events' % api_id,
                params={'chat_event.fields': 'id', 'max_results': 100})
        except Exception as exc:
            _logger.warning(
                'x_account_twitter: could not read the chat key-change chain '
                'for conversation %s (account_id=%s): %s: %s',
                api_id, account.id, type(exc).__name__, str(exc)[:200])
            return {}
        key_events = ((data or {}).get('meta') or {}).get(
            'conversation_key_events') or []
        if not key_events:
            _logger.info(
                'x_account_twitter: conversation %s exposes no key-change '
                'event (account_id=%s); its key cannot be recovered from the '
                'Chat API either', api_id, account.id)
            return {}
        _logger.info(
            'x_account_twitter: seeding %d chat key-change event(s) for '
            'conversation %s from the Chat API (account_id=%s)',
            len(key_events), api_id, account.id)
        return decryptor.collect_conversation_keys(conversation_id, key_events)

    def _chat_decryptor_for(self, account):
        """Return this account's Chat decryptor, building it at most once.

        Reusing the instance across a batch means the Juicebox unlock, the
        public-key API fetch and the signing-version write happen once per
        batch instead of once per event.
        """
        decryptor = self._chat_decryptors.get(account.id)
        if decryptor is None:
            from odoo.addons.x_account_twitter.services.xchat_decryptor import (
                XChatDecryptor)
            from odoo.addons.x_account_twitter.services.twitter_api_client import (
                TwitterApiClient)
            decryptor = XChatDecryptor(
                self.env, account, client=TwitterApiClient(account))
            self._chat_decryptors[account.id] = decryptor
        return decryptor

    def _registered_public_key_versions(self, account, decryptor):
        """X's registered public-key versions, for the decrypt-failure log line.

        Deliberately hard to trigger. The GET shares the ``public_keys``
        endpoint whose 24h budget the key registration needs, and it used to
        run for *every* undecryptable event: when decryption broke, the
        diagnostic alone drained the budget (one account logged 650+ throttled
        fetches in two hours) and every "Setup Chat Keys" click then failed with
        a 429 for lack of budget. So it is:

        - skipped entirely while the account is known to be failing (its last
          decrypt outcome was a failure) — retrying it cannot fix decryption and
          only starves the registration that can. Reading resumes once a message
          decrypts again or the key material is reconfigured, both of which
          restart the streak;
        - fetched at most once per account per batch, failures included, so a
          throttled read is not retried per event either.

        Returns the version list, ``None`` when it could not be read, or the
        string ``'paused'`` when it was skipped on purpose.
        """
        if account.id in self._public_key_versions_cache:
            return self._public_key_versions_cache[account.id]
        if account.x_chat_decrypt_fail_streak:
            versions = 'paused'
        else:
            versions = None
            try:
                if decryptor.client and account.twitter_user_id:
                    data = decryptor.client.request(
                        'GET',
                        '/2/users/%s/public_keys' % account.twitter_user_id,
                        params={'public_key.fields': 'public_key_version'})
                    records = (data or {}).get('data') or []
                    versions = [str(record.get('public_key_version'))
                                for record in records
                                if record.get('public_key_version')]
            except Exception:
                # Diagnostic only: never break the decryption flow over it.
                versions = None
        self._public_key_versions_cache[account.id] = versions
        return versions

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
        conversation_id = payload.get('conversation_id')
        sender_id = payload.get('sender_id')
        key_change = payload.get('conversation_key_change_event') or ''
        try:
            account = account.sudo()
            decryptor = self._chat_decryptor_for(account)
            if not decryptor.available:
                _logger.warning('x_account_twitter: chat decryption key missing '
                                'account_id=%s event_id=%s key_mode=%s',
                                account.id, payload.get('id'),
                                account.x_chat_key_mode or 'key_blob')
                return '', False
            # Conversation keys recovered on earlier deliveries (persisted on
            # the account) so a message whose key rotated before this delivery
            # can still be decrypted.
            cached_keys = self._cached_conversation_keys(account, conversation_id)
            # Feed any key-change event first so the conversation key is
            # recoverable, then decrypt the message blob. Pass the sender so a
            # different-user sender in group chats can be signature-verified.
            result = decryptor.decrypt_events(
                [encoded],
                key_change_events=[key_change] if key_change else None,
                sender_ids=[sender_id],
                cached_keys=cached_keys,
                conversation_id=conversation_id)
            errors = result.get('errors') or {}
            if errors and conversation_id and self._missing_key_error(errors):
                # The message key rotated in an earlier event that this delivery
                # does not carry: rebuild the conversation's key-change chain
                # from events we already stored and retry once.
                wider = self._conversation_key_change_blobs(
                    account, conversation_id, current=key_change)
                if len(wider) > (1 if key_change else 0):
                    _logger.info(
                        'x_account_twitter: retrying chat decrypt with %d '
                        'key-change blob(s) account_id=%s conversation_id=%s',
                        len(wider), account.id, conversation_id)
                    result = decryptor.decrypt_events(
                        [encoded],
                        key_change_events=wider,
                        sender_ids=[sender_id],
                        cached_keys=cached_keys,
                        conversation_id=conversation_id)
                    errors = result.get('errors') or {}
            if errors and conversation_id and self._missing_key_error(errors):
                # Still no key: the conversation key predates this account's
                # subscription, so no delivery ever carried its key-change
                # event and no stored event holds one either. Pull the
                # conversation's key chain from the Chat API and retry once.
                seeded = self._seed_conversation_keys_from_chat_api(
                    decryptor, account, conversation_id)
                if seeded:
                    retry_keys = dict(cached_keys or {})
                    retry_keys.update(seeded)
                    result = decryptor.decrypt_events(
                        [encoded],
                        sender_ids=[sender_id],
                        cached_keys=retry_keys,
                        conversation_id=conversation_id)
                    errors = result.get('errors') or {}
            if errors:
                # Chat XDK intentionally returns per-event crypto failures in
                # ``errors`` rather than raising.  Log only stable metadata;
                # never log the opaque event, key blob, PIN, or plaintext.
                error_kinds = sorted({str(error)[:160] for error in errors.values()})
                digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]
                
                # Extract the key version the message was encrypted with from the error
                import re
                webhook_key_version = None
                for error in errors.values():
                    match = re.search(r"encrypted with key version '(\d+)'", str(error))
                    if match:
                        webhook_key_version = match.group(1)
                        break
                
                # Registered key versions, for the "local vs X" comparison
                # below — fetched at most once per account per batch.
                api_versions = self._registered_public_key_versions(
                    account, decryptor)

                _logger.warning(
                    'x_account_twitter: Chat XDK rejected webhook event '
                    'account_id=%s event_id=%s sender_id=%s key_mode=%s '
                    'local_key_version=%s webhook_key_version=%s api_versions=%s '
                    'ciphertext_len=%s key_change_len=%s ciphertext_sha256=%s errors=%s',
                    account.id, payload.get('id'), payload.get('sender_id'),
                    account.x_chat_key_mode or 'key_blob',
                    account.x_chat_signing_key_version or 'unset',
                    webhook_key_version or 'unknown',
                    api_versions or 'fetch_failed',
                    len(encoded), len(key_change), digest, error_kinds)
            _logger.info('x_account_twitter: decrypt result keys=%s messages=%d',
                         list(result.keys()), len(result.get('messages') or []))
            for msg in result.get('messages') or []:
                _logger.info('x_account_twitter: msg keys=%s', list(msg.keys()))
                # The message is the event itself, not nested under 'event'
                ev = msg if 'type' in msg else (msg.get('event') or {})
                _logger.info('x_account_twitter: decrypted event type=%s keys=%s',
                             ev.get('type'), list(ev.keys()))
                if ev.get('type') == 'Message':
                    content = ev.get('content') or {}
                    _logger.info('x_account_twitter: decrypted message content keys=%s content=%s',
                                list(content.keys()), str(content)[:1000])
                    text = content.get('text', '')
                    urls = content.get('urls') or []
                    entities = content.get('entities') or []
                    attachments = content.get('attachments') or []
                    url_texts = []
                    if urls:
                        url_texts.extend([u.get('expanded_url') or u.get('url') or '' for u in urls if u])
                    for entity in entities:
                        if entity.get('type') == 'url':
                            url_texts.append(entity.get('expanded_url') or entity.get('url') or '')
                    for attachment in attachments:
                        if attachment.get('type') == 'url':
                            url_texts.append(attachment.get('expanded_url') or attachment.get('url') or '')
                        elif attachment.get('type') == 'media':
                            url_texts.append(attachment.get('url') or attachment.get('media_url') or '')
                        elif 'post' in attachment:
                            post_data = attachment.get('post') or {}
                            url_texts.append(post_data.get('post_url') or '')
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
    def _is_fatal_db_error(self, exc):
        """True when the error broke the cursor/connection itself.

        After such an error no further write can succeed in this transaction;
        callers must abort (re-raise) instead of marking events done so the
        queue/cron rolls back and retries the batch later.
        """
        if isinstance(exc, (psycopg2.InterfaceError, psycopg2.OperationalError)):
            return True
        try:
            return bool(self.env.cr.closed)
        except Exception:
            return True

    @staticmethod
    def _usable_dm_text(event):
        """Text of a legacy DM event, or ``None`` when it carries no content.

        ``None`` for non-``message_create`` events (typing, read receipts,
        reactions, membership), for a missing/blank ``text`` (media-only), and
        for a pure backslash/whitespace escaping artifact — almost always a
        payload/decoding artifact rather than a real message.
        """
        if not isinstance(event, dict) or event.get('type') != 'message_create':
            return None
        text = ((event.get('message_create') or {}).get('message_data')
                or {}).get('text') or ''
        if not text.strip():
            return None
        if not text.replace('\\', '').strip():
            _logger.warning(
                'x_account_twitter: skipping DM %s whose body is a pure '
                'backslash/escaping artifact (%d chars); treating as payload '
                'noise', event.get('id'), len(text.strip()))
            return None
        return text

    @classmethod
    def _envelope_has_message_content(cls, event_type, payload):
        """Whether an envelope can yield a message body.

        Conservative: an encrypted ``chat.received`` blob is kept even when it
        cannot be decrypted yet, so a real message is never dropped here — only
        envelopes that can never produce a body are.
        """
        if event_type == 'dm.received':
            return any(cls._usable_dm_text(ev)
                       for ev in (payload.get('direct_message_events') or []))
        if event_type == 'chat.received':
            return bool(payload.get('encoded_event')
                        or (payload.get('text') or '').strip())
        return True

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
        account = self.env['social.account'].sudo().with_context(
            active_test=False).search([
                ('media_type', '=', 'twitter'),
                ('twitter_user_id', '=', str(user_id)),
            ], limit=1)
        if account:
            account.write({'x_connection_status': 'disconnected'})
            _logger.info('x_account_twitter: user %s revoked app access', user_id)
