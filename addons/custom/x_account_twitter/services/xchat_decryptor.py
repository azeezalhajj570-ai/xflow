# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Official Chat XDK integration for decrypting XChat encrypted events.

Wraps the official ``chatxdk`` package (https://docs.x.com/xchat/xchat-xdk) so
the twitter provider can turn XChat ``encoded_event`` blobs into readable
messages, and encrypted group metadata (``group_name``) into plaintext.

Key material is drawn from one of two modes on the account
(``x_chat_key_mode``):

- ``key_blob`` (default): the account carries an opaque private-key blob
  (``x_chat_key_blob``) exported by ``chatxdk export_keys()``, and the
  registered key version (``x_chat_signing_key_version``). Decryption imports
  the blob with ``import_keys`` and sets the identity.
- ``juicebox``: the account carries the XChat PIN (``x_encryption_code``).
  Decryption recovers the private keys from X's secure key backup (Juicebox)
  with ``unlock(pin)`` and sets the identity — no private key blob is stored
  on the server.

Without usable key material decryption is skipped (the caller keeps the
``encrypted`` marker) — we never invent keys or guess.

Sender verification uses participants' public keys fetched from the X API
(``GET /2/users/{id}/public_keys``) mapped into ``SigningKeyEntry``.

This service is provider-specific (official X Chat API) and lives in
``x_account_twitter``; ``x_account`` stays provider-agnostic and only stores the
secret fields. No dependency on ``x_account_omnix``.
"""

import ast
import base64
import binascii
import logging
import re
import time

_LOGGER = logging.getLogger(__name__)

# Strict hex (an unambiguous encoding: a base64 blob of real key material is
# never pure hex alphabet) and a Python bytes/bytearray repr, both accepted as
# storage forms for the Text key-blob field.
_HEX_RE = re.compile(r'^[0-9a-fA-F]+$')
_B64URL_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_BYTES_REPR_RE = re.compile(r"^(?:bytearray\()?b'(.*)'\)?$", re.DOTALL)


class XChatDecryptor:
    """Lazy, per-account wrapper around the official Chat XDK."""

    def __init__(self, env, account, client=None):
        self.env = env
        self.account = account
        self.client = client  # TwitterApiClient for public-key lookups
        self._chat = None
        self._signing_keys_cache = None
        self._public_keys_cache = None
        self._public_key_records_cache = {}
        self._conversation_keys = None

    # ------------------------------------------------------------------ setup
    @property
    def available(self):
        """True when the account has usable key material for the chosen mode.

        Juicebox mode also requires the PIN not to be locked out (X rejected
        the PIN previously; each wrong attempt consumes one of the limited
        guesses before the secure backup is permanently locked).
        """
        if self.account.x_chat_key_mode == 'juicebox':
            return bool(self.account.x_encryption_code) \
                and not getattr(self.account, 'x_chat_pin_locked', False)
        return bool(self.account.x_chat_key_blob)

    def initialize(self):
        """Set up the Chat XDK instance from the account's configured mode.

        For ``key_blob`` this validates/imports the stored blob; for
        ``juicebox`` it recovers keys from X's secure key backup using the
        account PIN. Raises ValueError when no key material is configured.
        Returns the unlocked ``Chat`` instance.
        """
        account = self.account
        keys_record = self._public_keys_record()
        if account.x_chat_key_mode == 'juicebox':
            if not account.x_encryption_code:
                raise ValueError(
                    'No XChat encryption code (PIN) configured for account %s'
                    % account.id)
            if getattr(account, 'x_chat_pin_locked', False):
                raise ValueError(
                    'X Chat PIN previously rejected for account %s; unlock '
                    'attempts paused to protect the secure backup. Re-enter '
                    'the PIN to resume.' % account.id)
            if not (keys_record and keys_record.get('juicebox_config')):
                raise ValueError(
                    'Account %s has no X Chat secure-backup (Juicebox) config; '
                    'it cannot unlock keys by PIN. Use an imported key blob, or '
                    'run first-time setup (generate_keypairs + setup + register '
                    'public keys).' % account.id)
            from chat_xdk import Chat
            import json as _json
            chat = Chat(_json.dumps(keys_record['juicebox_config']))
            try:
                self._unlock_with_retry(chat, account.x_encryption_code)
                if getattr(account, 'x_chat_pin_locked', False):
                    account.sudo().write({'x_chat_pin_locked': False})
            except Exception as exc:
                if self._is_invalid_pin_error(exc):
                    _LOGGER.warning(
                        'X Chat PIN rejected for account %s; locking further '
                        'attempts until the PIN changes (each wrong attempt '
                        'consumes one of X\'s limited guesses before the '
                        'secure backup is permanently locked).', account.id)
                    account.sudo().write({'x_chat_pin_locked': True})
                raise
        else:
            if not account.x_chat_key_blob:
                raise ValueError(
                    'No X Chat key blob configured for account %s' % account.id)
            # ``import_keys`` strictly requires bytes (a str raises
            # ``TypeError: argument 'keys': 'str' object cannot be cast as
            # bytes``); the account field is Text, so decode the stored
            # encoding first.
            blob = self._decode_key_blob(account.x_chat_key_blob)
            from chat_xdk import Chat
            chat = Chat()
            # A key blob belongs to one concrete registered key version.  Do
            # not replace that explicit version with whichever public-key row
            # happens to be returned first by the API (the API can return
            # several, including rotated historical keys).
            version = account.x_chat_signing_key_version \
                or keys_record.get('public_key_version') or '1'
            chat.import_keys(blob, version=version)
        signing_key_version = account.x_chat_signing_key_version \
            or keys_record.get('public_key_version') or '1'
        if account.twitter_user_id:
            chat.set_identity(str(account.twitter_user_id), signing_key_version)
        chat.set_cache_keys(True)
        self._chat = chat
        self._conversation_keys = None
        return chat

    @staticmethod
    def _decode_key_blob(blob):
        """Decode the stored ``x_chat_key_blob`` text into raw bytes.

        ``Chat.export_keys()`` returns raw bytes and ``Chat.import_keys``
        accepts bytes only, while the account field is Text — so the blob is
        stored encoded. Accepted encodings, tried in order: strict hex,
        standard base64, URL-safe base64 and a Python bytes/bytearray repr;
        anything else is passed through as the raw UTF-8 bytes of the text and
        the native XDK itself rejects invalid key material (deterministic
        failure). Only the detected encoding name and byte length are logged,
        never the content.

        Whitespace is stripped only for the base64/hex candidates: a Python
        repr can legitimately contain a literal space (byte 0x20).
        """
        raw_text = blob or ''
        if not raw_text.strip():
            raise ValueError('Empty X Chat key blob')
        cleaned = ''.join(raw_text.split())
        if _HEX_RE.match(cleaned) and len(cleaned) % 2 == 0:
            try:
                raw = bytes.fromhex(cleaned)
                _LOGGER.info('X Chat key blob decoded from hex (%d bytes)',
                             len(raw))
                return raw
            except ValueError:
                pass
        try:
            raw = base64.b64decode(cleaned, validate=True)
            _LOGGER.info('X Chat key blob decoded from base64 (%d bytes)',
                         len(raw))
            return raw
        except (ValueError, binascii.Error):
            pass
        if _B64URL_RE.match(cleaned):
            try:
                raw = base64.urlsafe_b64decode(
                    cleaned + '=' * (-len(cleaned) % 4))
                _LOGGER.info('X Chat key blob decoded from URL-safe base64 '
                             '(%d bytes)', len(raw))
                return raw
            except (ValueError, binascii.Error):
                pass
        repr_match = _BYTES_REPR_RE.match(raw_text.strip())
        if repr_match:
            try:
                raw = bytes(ast.literal_eval("b'%s'" % repr_match.group(1)))
                _LOGGER.info('X Chat key blob decoded from a Python bytes '
                             'repr (%d bytes)', len(raw))
                return raw
            except (ValueError, SyntaxError):
                pass
        raw = raw_text.encode('utf-8')
        _LOGGER.info('X Chat key blob used as raw UTF-8 bytes (%d bytes)',
                     len(raw))
        return raw

    @staticmethod
    def _is_transient_juicebox_error(exc):
        """True when an exception is a retryable X Juicebox transient error.

        The SDK surfaces server-side backpressure/rate limiting as "Juicebox
        error: Transient error - retry". Network errors, timeouts, and
        connection failures are also transient and should be retried. A wrong
        PIN or missing registration is *not* transient and must be surfaced
        immediately.
        """
        message = str(exc) or ''
        message_lower = message.lower()
        # SDK-reported transient errors
        if 'Transient error - retry' in message:
            return True
        # Network/connection errors
        transient_needles = [
            'timeout', 'timed out', 'connection', 'network',
            'temporarily unavailable', 'retry', 'unreachable',
        ]
        return any(needle in message_lower for needle in transient_needles)

    @staticmethod
    def _is_invalid_pin_error(exc):
        """True when an exception is a Juicebox PIN rejection.

        X limits the number of wrong PIN attempts before permanently locking
        the secure backup. When the SDK surfaces "Invalid PIN" (optionally with
        ``guesses_remaining``), the operator must correct the PIN — retrying
        the same wrong PIN only burns the remaining guesses.

        Must NOT match transient errors, network failures, or other
        configuration issues.
        """
        message = (str(exc) or '').lower()
        # Must contain "invalid pin" or "wrong pin" to be a PIN rejection
        # Also check for "guesses_remaining" which is specific to PIN errors
        return ('invalid pin' in message or
                'wrong pin' in message or
                'guesses_remaining' in message)

    @staticmethod
    def _unlock_with_retry(chat, pin, tries=4, base_delay=4.0):
        """Call ``chat.unlock(pin)``, retrying on transient Juicebox errors.

        Transient errors are common right after configuring the secure-backup
        service and are expected to clear. Retries sleep with a small linear
        backoff so a busy Juicebox gets a chance to recover. Non-transient
        errors (wrong PIN, missing keys) are re-raised immediately.
        """
        for attempt in range(1, tries + 1):
            try:
                return chat.unlock(pin)
            except Exception as exc:
                if not XChatDecryptor._is_transient_juicebox_error(exc):
                    raise
                if attempt == tries:
                    _LOGGER.warning(
                        'X Chat Juicebox unlock still transient after %d '
                        'attempts: %s', tries, exc)
                    raise
                _LOGGER.info(
                    'X Chat Juicebox unlock transient (attempt %d/%d); '
                    'retrying in %.1fs', attempt, tries,
                    base_delay * attempt)
                time.sleep(base_delay * attempt)

    def _chat_instance(self):
        """Build + unlock a Chat XDK instance from the account's key mode."""
        if self._chat is not None:
            return self._chat
        return self.initialize()

    def _build_signup_params(self):
        """Return the Chat XDK values needed to initialize a *new* chat key.

        Only meaningful for ``juicebox`` mode where no keys exist yet: new keys
        must be created by ``Chat.generate_keypairs()`` and registered with
        ``set_identity``. ``key_blob`` mode always imports pre-existing keys.
        """
        return {
            'key_mode': self.account.x_chat_key_mode or 'key_blob',
            'pin': self.account.x_encryption_code,
            'key_blob': self.account.x_chat_key_blob,
            'signing_key_version': self.account.x_chat_signing_key_version,
        }

    def _public_keys_record(self):
        """Fetch the account's Chat public-key record(s) from the X API.

        Includes the ``juicebox_config`` needed to construct a ``Chat`` instance
        that can recover keys via the secure key backup (``setup``/``unlock``).
        Returns the first record dict, or {} on failure/none found.
        """
        if self._public_keys_cache is not None:
            return self._public_keys_cache
        record = {}
        if self.client is not None and self.account.twitter_user_id:
            records = self._public_key_records_for(self.account.twitter_user_id)
            if records:
                record = records[0]
            else:
                _LOGGER.warning(
                    'Failed to fetch Chat public keys for account %s; '
                    'decryption may skip signature verification / unlock',
                    self.account.id, exc_info=False)
        self._public_keys_cache = record or {}
        return self._public_keys_cache

    def _public_key_records_for(self, user_id):
        """Return every public-key version published by ``user_id``.

        X signs each chat event with a ``public_key_version``.  Retaining only
        the first API row makes verification fail as soon as a participant has
        rotated signing keys and the delivery was signed by another retained
        version.  The XDK selects the matching version itself when it receives
        all rows.
        """
        if not user_id or self.client is None:
            return []
        cache_key = str(user_id)
        if cache_key in self._public_key_records_cache:
            return self._public_key_records_cache[cache_key]
        try:
            data = self.client.request(
                'GET', '/2/users/%s/public_keys' % user_id,
                params={'public_key.fields':
                        'public_key_version,public_key,signing_public_key,'
                        'identity_public_key_signature,juicebox_config'})
            rows = (data or {}).get('data') or []
            records = [row for row in rows if isinstance(row, dict)]
            self._public_key_records_cache[cache_key] = records
            return records
        except Exception as exc:
            # A failed public-key fetch is a guaranteed verification failure
            # for every event signed by this user (the XDK refuses to skip
            # verification): warn with the failure reason, metadata only.
            _LOGGER.warning(
                'Failed to fetch Chat public keys for user %s (account %s): '
                '%s: %s', user_id, self.account.id, type(exc).__name__,
                str(exc)[:200])
            self._public_key_records_cache[cache_key] = []
            return []

    @staticmethod
    def _map_public_key_record(record, user_id):
        """Map one X API public-key record into a ``SigningKeyEntry`` dict.

        ``_signing_keys()`` uses this layout; ``signing_public_key`` is the key
        used to verify the sender's message signature, the identity ``public_key``
        and ``identity_public_key_signature`` cover cross-key binding.
        """
        return {
            'user_id': str(record.get('user_id') or user_id),
            'public_key_version': record.get('public_key_version'),
            'public_key': record.get('signing_public_key'),
            'identity_public_key': record.get('public_key'),
            'identity_public_key_signature':
                record.get('identity_public_key_signature'),
        }

    def _public_key_record_for(self, user_id):
        """Fetch a *participant's* Chat public-key record from the X API.

        Distinct from ``_public_keys_record()`` which always returns the
        account's own record. Returns the first record dict, or {} on
        failure/none found. ``user_id`` may be passed as str or int.
        """
        if not user_id:
            return {}
        user_id = str(user_id)
        rows = self._public_key_records_for(user_id)
        return rows[0] if rows else {}

    def _ensure_signing_key_for(self, user_id):
        """Fetch + cache the signing key of ``user_id`` (e.g. a message sender).

        In group conversations the message sender is often a *different* X user
        than the account's own identity, so verifying their
        ``message_event_signature`` requires their signing public key in the
        store. Appends the entry if not already known; returns True when known.
        """
        if not user_id:
            return False
        user_id = str(user_id)
        keys = self._ensure_signing_key_store()
        if any(entry.get('user_id') == user_id for entry in keys):
            return True
        records = self._public_key_records_for(user_id)
        if not records:
            _LOGGER.warning(
                'No Chat public keys registered for sender %s (account %s); '
                'the XDK will reject this sender\'s events as unverified '
                '(signature missing or no matching signing key)',
                user_id, self.account.id)
            return False
        keys.extend(self._map_public_key_record(record, user_id)
                    for record in records)
        return True

    def _ensure_signing_key_store(self):
        """Return the signing-key store, seeding it with the account's own keys."""
        if self._signing_keys_cache is None:
            keys = []
            # The account's signing key may also have rotated.  Seed every
            # version, not merely the first response row.
            for record in self._public_key_records_for(
                    self.account.twitter_user_id):
                keys.append(self._map_public_key_record(
                    record, self.account.twitter_user_id))
            self._signing_keys_cache = keys
        return self._signing_keys_cache

    def _signing_keys(self, sender_ids=None):
        """Return SigningKeyEntry list for sender verification.

        Includes the account's own registered public keys, plus (when
        ``sender_ids`` is given) the signing keys of each distinct message
        sender so their signatures can be verified.
        """
        keys = self._ensure_signing_key_store()
        for user_id in sender_ids or []:
            self._ensure_signing_key_for(user_id)
        return keys

    # ------------------------------------------------------------------ public
    def _absorb_key_changes(self, chat, signing_keys, key_change_events):
        """Recover + cache conversation keys from raw key-change events.

        Key-change events let us rebuild the conversation-key state so that
        messages in a conversation whose key rotated can still be decrypted
        (``extract_conversation_keys`` caches the current conversation key on
        the SDK instance). Swallows per-event failures so one bad key-change
        never blocks the rest of the batch.
        """
        if not key_change_events:
            return self._conversation_keys
        if self._conversation_keys is None:
            self._conversation_keys = {}
        try:
            extracted = chat.extract_conversation_keys(
                list(key_change_events)) \
                if hasattr(chat, 'extract_conversation_keys') else {}
            if isinstance(extracted, dict):
                keys = extracted.get('keys') or {}
                # Chat XDK returns {key_version: raw_key_bytes}; preserve the
                # direction because decrypt_event accepts exactly that map.
                self._conversation_keys.update(
                    {str(version): key for version, key in keys.items()})
        except Exception as exc:
            _LOGGER.warning(
                'Key-change absorption failed for account %s (rotated '
                'conversation keys may be undecryptable): %s: %s',
                self.account.id, type(exc).__name__, str(exc)[:200])
        return self._conversation_keys

    def decrypt_events(self, raw_events, key_change_events=None,
                       sender_ids=None):
        """Decrypt a batch of raw ``encoded_event`` blobs.

        ``raw_events`` is a list of base64 event strings (the ``encoded_event``
        field of each Chat event). ``key_change_events`` are the raw key-change
        events from ``meta.conversation_key_events`` (needed to recover
        conversation keys, including rotated ones). ``sender_ids`` lists the
        ``sender_id`` of each raw event so the caller can help resolve the
        signing key used to verify the sender's message signature in group
        conversations (fetching it from the X API when it is not the account's
        own key).

        Returns ``{'messages': [...], 'errors': [...]}`` where each message is
        the SDK's decrypted event dict. Raises ValueError when no key material
        is configured (caller should keep the ``encrypted`` marker).
        """
        chat = self._chat_instance()
        signing_keys = self._signing_keys(sender_ids)
        self._absorb_key_changes(chat, signing_keys, key_change_events)
        blobs = list(raw_events or [])
        if key_change_events:
            blobs = list(key_change_events) + blobs
        result = chat.decrypt_events(blobs, signing_keys)
        if not isinstance(result, dict):
            result = {}
        messages = result.get('messages') or []
        errors = result.get('errors') or {}
        return {'messages': messages, 'errors': errors}

    def decrypt_event(self, raw_event, key_change_events=None, sender_id=None):
        """Decrypt a single encoded event (raises on failure)."""
        chat = self._chat_instance()
        signing_keys = self._signing_keys([sender_id] if sender_id else None)
        self._absorb_key_changes(chat, signing_keys, key_change_events)
        return chat.decrypt_event(raw_event, self._conversation_keys or None,
                                  signing_keys)

    def decrypt_metadata(self, ciphertext):
        """Decrypt an encrypted conversation metadata field (e.g. group_name).

        Uses the Chat XDK's generic ``decrypt`` with the raw conversation key.
        Returns the plaintext string, or None if it cannot be decrypted.
        """
        if not ciphertext:
            return None
        try:
            chat = self._chat_instance()
            raw = chat.decrypt(ciphertext, None)
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode('utf-8', errors='replace')
            return raw
        except Exception:
            return None
