import base64
import json
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.x_account_twitter.services.twitter_activity import TwitterActivity
from odoo.addons.x_account_twitter.services.xchat_decryptor import XChatDecryptor

from .common import XAccountTwitterTestBase


class _FakeChat:
    """Minimal stand-in for the Chat XDK instance used by XChatDecryptor."""

    def __init__(self):
        self.decrypt_calls = []

    def set_signing_keys(self, keys):
        pass

    def extract_conversation_keys(self, events):
        return {'keys': {}}

    def decrypt_event(self, blob, keys, signing_keys):
        self.decrypt_calls.append(dict(keys or {}))
        if keys and '100' in {str(k) for k in keys}:
            return {'type': 'Message', 'id': 'm1',
                    'content': {'content_type': 'Text', 'text': 'hi'}}
        raise ValueError(
            "Decryption failed: Message encrypted with key version '100' "
            "but no matching key found")


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestXChatConversationKeys(XAccountTwitterTestBase):
    """Conversation keys recovered on one delivery are reused on the next.

    A webhook delivery carries only its own key-change blob, so a message whose
    key rotated earlier must be decryptable with keys persisted before it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Chat Keys Account',
            'media_id': cls.media.id,
            'social_account_handle': 'chat_keys',
            'twitter_user_id': '1234567890',
        })

    def test_decrypt_events_seeds_cached_keys_and_persists(self):
        key = base64.b64encode(b'k').decode()
        decryptor = XChatDecryptor(self.env, self.account)
        fake = _FakeChat()
        with patch.object(XChatDecryptor, '_chat_instance',
                          return_value=fake):
            with patch.object(XChatDecryptor, '_signing_keys',
                              return_value=[]):
                result = decryptor.decrypt_events(
                    ['blob'], cached_keys={'100': key},
                    conversation_id='c1')
        self.assertFalse(result['errors'])
        self.assertEqual(len(result['messages']), 1)
        # The seeded key is kept (and persisted) for the conversation.
        self.account.invalidate_recordset()
        stored = (self.account.x_chat_conversation_keys or {}).get('c1') or {}
        self.assertEqual(stored.get('100'), key)

    def test_unchanged_seeded_keys_are_not_rewritten(self):
        """Re-persisting identical keys on every delivery is a row lock on the
        account per delivery, contending with the UI's own saves: only a change
        is written."""
        key = base64.b64encode(b'k').decode()
        self.account.write({'x_chat_conversation_keys': {'c1': {'100': key}}})
        decryptor = XChatDecryptor(self.env, self.account)
        fake = _FakeChat()
        writes = []
        original = type(self.account).write

        def _spy(records, vals):
            writes.append(dict(vals))
            return original(records, vals)

        with patch.object(XChatDecryptor, '_chat_instance',
                          return_value=fake), \
             patch.object(XChatDecryptor, '_signing_keys',
                          return_value=[]), \
             patch.object(type(self.account), 'write', _spy):
            result = decryptor.decrypt_events(
                ['blob'], cached_keys={'100': key}, conversation_id='c1')
        # The seeded key decrypted the message and was already stored.
        self.assertEqual(len(result['messages']), 1)
        self.assertFalse(
            [vals for vals in writes if 'x_chat_conversation_keys' in vals],
            'unchanged conversation keys must not be rewritten')

    def test_decrypt_events_without_keys_reports_missing_key(self):
        decryptor = XChatDecryptor(self.env, self.account)
        fake = _FakeChat()
        with patch.object(XChatDecryptor, '_chat_instance',
                          return_value=fake):
            with patch.object(XChatDecryptor, '_signing_keys',
                              return_value=[]):
                result = decryptor.decrypt_events(['blob'])
        self.assertTrue(result['errors'])
        self.assertFalse(result['messages'])

    def test_missing_key_error_detection(self):
        activity = TwitterActivity(self.env)
        self.assertTrue(activity._missing_key_error(
            {'e': "Decryption failed: ... but no matching key found"}))
        self.assertFalse(activity._missing_key_error(
            {'e': 'some other crypto error'}))

    def test_collects_key_change_blobs_for_the_conversation(self):
        event_model = self.env['x.twitter.event']
        event_model.create({
            'account_id': self.account.id,
            'event_uuid': 'kc-event-1',
            'event_type': 'chat.received',
            'state': 'done',
            'payload': json.dumps({'payload': {
                'conversation_id': 'c1',
                'conversation_key_change_event': 'KC1'}}),
        })
        event_model.create({
            'account_id': self.account.id,
            'event_uuid': 'kc-event-2',
            'event_type': 'chat.received',
            'state': 'done',
            'payload': json.dumps({'payload': {
                'conversation_id': 'c2',
                'conversation_key_change_event': 'KC2'}}),
        })
        activity = TwitterActivity(self.env)
        blobs = activity._conversation_key_change_blobs(
            self.account, 'c1', current='KC0')
        self.assertIn('KC0', blobs)
        self.assertIn('KC1', blobs)
        self.assertNotIn('KC2', blobs)

    def test_duplicate_key_change_blobs_are_stored_once(self):
        """The same key repeated by many deliveries is kept once, not per row.

        Storing the blob once per delivery is what made x_twitter_event grow to
        dominate the database.
        """
        for uuid in ('kc-dup-1', 'kc-dup-2', 'kc-dup-3'):
            self.env['x.twitter.event'].create({
                'account_id': self.account.id,
                'event_uuid': uuid,
                'event_type': 'chat.received',
                'payload': json.dumps({'payload': {
                    'conversation_id': 'c1',
                    'conversation_key_change_event': 'SAME'}}),
            })
        changes = self.env['x.twitter.key.change'].sudo().search([
            ('account_id', '=', self.account.id),
            ('conversation_id', '=', 'c1'),
        ])
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes.blob, 'SAME')

    def test_key_change_blob_outlives_the_event_payload(self):
        """Clearing the payload of a processed event must not lose its key."""
        event = self.env['x.twitter.event'].create({
            'account_id': self.account.id,
            'event_uuid': 'kc-cleared-1',
            'event_type': 'chat.received',
            'payload': json.dumps({'payload': {
                'conversation_id': 'c1',
                'conversation_key_change_event': 'KC1'}}),
        })
        event.write({'state': 'done', 'payload': False})
        blobs = TwitterActivity(self.env)._conversation_key_change_blobs(
            self.account, 'c1', current='KC0')
        self.assertIn('KC0', blobs)
        self.assertIn('KC1', blobs)

    def test_event_create_extracts_conversation_fields(self):
        """A created event indexes its conversation and key-change presence, so
        the key-change lookup never LIKE-scans the payload text."""
        event = self.env['x.twitter.event'].create({
            'account_id': self.account.id,
            'event_uuid': 'kc-fields-1',
            'event_type': 'chat.received',
            'payload': json.dumps({'payload': {
                'conversation_id': 'c9',
                'conversation_key_change_event': 'KC9'}}),
        })
        self.assertEqual(event.conversation_id, 'c9')
        self.assertTrue(event.has_key_change)

    def test_event_create_tolerates_malformed_payload(self):
        event = self.env['x.twitter.event'].create({
            'account_id': self.account.id,
            'event_uuid': 'kc-bad-1',
            'event_type': 'chat.received',
            'payload': 'not json',
        })
        self.assertFalse(event.conversation_id)
        self.assertFalse(event.has_key_change)
