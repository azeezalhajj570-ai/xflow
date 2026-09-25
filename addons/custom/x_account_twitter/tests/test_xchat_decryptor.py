from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from .common import XAccountTwitterTestBase


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestXChatDecryptorKeySelection(XAccountTwitterTestBase):
    """Test XChatDecryptor key record selection logic."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Test X Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'test_account',
            'twitter_user_id': '123456789',
            'x_provider': 'twitter',
            'x_auth_method': 'oauth2',
            'x_chat_key_mode': 'juicebox',
            'x_encryption_code': '1234',
        })

    def _make_decryptor(self, account=None, client=None):
        """Helper to create XChatDecryptor instance."""
        from odoo.addons.x_account_twitter.services.xchat_decryptor import XChatDecryptor
        return XChatDecryptor(self.env, account or self.account, client)

    def _make_records(self, versions):
        """Helper to create mock public key records with given versions."""
        records = []
        for i, version in enumerate(versions):
            records.append({
                'public_key_version': version,
                'juicebox_config': {'config_key': f'config_{i}'},
                'public_key': f'public_key_{i}',
                'signing_public_key': f'signing_key_{i}',
                'identity_public_key_signature': f'signature_{i}',
            })
        return records

    def test_select_key_record_single_record(self):
        """Test selection with a single public key record."""
        records = self._make_records(['1788042324027'])
        client = MagicMock()
        client.request.return_value = {'data': records}
        
        decryptor = self._make_decryptor(client=client)
        selected = decryptor._select_key_record()
        
        self.assertEqual(selected['public_key_version'], '1788042324027')
        self.assertEqual(selected['juicebox_config']['config_key'], 'config_0')

    def test_select_key_record_multiple_records_selects_latest(self):
        """Test that multiple records select the latest by numeric version."""
        # Create records in non-sorted order
        records = self._make_records([
            '1772923727027',  # oldest
            '1773107189834',  # newest
            '1773086666050',  # middle
        ])
        client = MagicMock()
        client.request.return_value = {'data': records}
        
        decryptor = self._make_decryptor(client=client)
        selected = decryptor._select_key_record()
        
        # Should select the highest numeric version
        self.assertEqual(selected['public_key_version'], '1773107189834')
        self.assertEqual(selected['juicebox_config']['config_key'], 'config_1')

    def test_select_key_record_numeric_ordering_not_lexicographic(self):
        """Test that version comparison is numeric, not lexicographic."""
        # These versions would sort incorrectly as strings
        # '9' > '10' lexicographically, but 9 < 10 numerically
        records = self._make_records(['9', '10', '100'])
        client = MagicMock()
        client.request.return_value = {'data': records}
        
        decryptor = self._make_decryptor(client=client)
        selected = decryptor._select_key_record()
        
        # Should select 100 (highest numeric), not '9' (highest lexicographic)
        self.assertEqual(selected['public_key_version'], '100')

    def test_select_key_record_rotated_key_wins_over_persisted_version(self):
        """A rotated key is used immediately.

        The newest registered row wins even when an older version is still
        persisted on the account, so decryption follows X's rotation instead of
        staying pinned to the superseded key (the rotation is persisted after
        the next successful unlock).
        """
        records = self._make_records([
            '1772923727027',  # persisted, superseded
            '1773107189834',  # latest
        ])
        client = MagicMock()
        client.request.return_value = {'data': records}
        
        self.account.x_chat_signing_key_version = '1772923727027'
        
        decryptor = self._make_decryptor(client=client)
        selected = decryptor._select_key_record()
        
        self.assertEqual(selected['public_key_version'], '1773107189834')
        self.assertEqual(selected['juicebox_config']['config_key'], 'config_1')

    def test_select_key_record_unknown_persisted_version_still_selects(self):
        """A persisted version X no longer publishes is not fatal.

        The newest registered row is used, so the account keeps decrypting
        without an operator reconciliation step.
        """
        records = self._make_records(['1773107189834'])
        client = MagicMock()
        client.request.return_value = {'data': records}
        
        self.account.x_chat_signing_key_version = '9999999999999'
        
        decryptor = self._make_decryptor(client=client)
        selected = decryptor._select_key_record()
        
        self.assertEqual(selected['public_key_version'], '1773107189834')

    def test_select_key_record_no_records(self):
        """Test handling when no records are returned."""
        client = MagicMock()
        client.request.return_value = {'data': []}
        
        decryptor = self._make_decryptor(client=client)
        selected = decryptor._select_key_record()
        
        self.assertEqual(selected, {})

    def test_select_key_record_no_client(self):
        """Test handling when no client is provided."""
        decryptor = self._make_decryptor(client=None)
        selected = decryptor._select_key_record()
        
        self.assertEqual(selected, {})

    @patch('chat_xdk.Chat')
    def test_initialize_persists_version_after_unlock(self, mock_chat_class):
        """Test that successful unlock persists the selected version."""
        records = self._make_records(['1773107189834'])
        client = MagicMock()
        client.request.return_value = {'data': records}
        
        # Ensure no persisted version initially
        self.assertFalse(self.account.x_chat_signing_key_version)
        
        mock_chat = MagicMock()
        mock_chat_class.return_value = mock_chat
        
        decryptor = self._make_decryptor(client=client)
        decryptor.initialize()
        
        # Verify unlock was called
        mock_chat.unlock.assert_called_once_with('1234')
        
        # Verify version was persisted
        self.assertEqual(self.account.x_chat_signing_key_version, '1773107189834')
        
        # Verify set_identity was called with the same version
        mock_chat.set_identity.assert_called_once_with('123456789', '1773107189834')

    @patch('chat_xdk.Chat')
    def test_initialize_uses_latest_config_with_the_persisted_identity(
            self, mock_chat_class):
        """The realm config comes from the newest row, the identity from the
        account.

        The secure-backup config moves with the key rotation, so it is always
        taken from the newest registered row; ``set_identity`` keeps the
        account's persisted version until a successful unlock persists the new
        one.
        """
        records = self._make_records([
            '1772923727027',
            '1773107189834',
        ])
        client = MagicMock()
        client.request.return_value = {'data': records}
        
        self.account.x_chat_signing_key_version = '1772923727027'
        
        mock_chat = MagicMock()
        mock_chat_class.return_value = mock_chat
        
        decryptor = self._make_decryptor(client=client)
        decryptor.initialize()
        
        mock_chat_class.assert_called_once()
        call_args = mock_chat_class.call_args[0][0]
        self.assertIn('config_1', call_args)  # config from the newest record
        
        mock_chat.set_identity.assert_called_once_with(
            '123456789', '1772923727027')

    @patch('chat_xdk.Chat')
    def test_initialize_only_writes_a_changed_signing_version(
            self, mock_chat_class):
        """The signing version is written only when it changes.

        It is stable for a healthy account, and rewriting it on every batch took
        a row lock on ``social_account`` per batch, contending with the UI's own
        saves on the account (observed as serialization failures).
        """
        records = self._make_records(['1773107189834'])
        client = MagicMock()
        client.request.return_value = {'data': records}
        mock_chat_class.return_value = MagicMock()

        writes = []
        original = type(self.account).write

        def _spy(records_, vals):
            writes.append(dict(vals))
            return original(records_, vals)

        with patch.object(type(self.account), 'write', _spy):
            # First run: nothing was persisted yet, so the version is written.
            XChatDecryptor(self.env, self.account, client=client).initialize()
            self.assertEqual(
                self.account.x_chat_signing_key_version, '1773107189834')
            # Second run with the same registered version: no write at all.
            writes.clear()
            XChatDecryptor(self.env, self.account, client=client).initialize()
            self.assertFalse(
                [vals for vals in writes
                 if 'x_chat_signing_key_version' in vals],
                'the unchanged signing version must not be rewritten')

    @patch('chat_xdk.Chat')
    def test_initialize_unlock_and_set_identity_use_same_version(self, mock_chat_class):
        """Test that unlock and set_identity use the same key record/version."""
        records = self._make_records([
            '1772923727027',  # oldest
            '1773107189834',  # latest
        ])
        client = MagicMock()
        client.request.return_value = {'data': records}
        
        mock_chat = MagicMock()
        mock_chat_class.return_value = mock_chat
        
        decryptor = self._make_decryptor(client=client)
        decryptor.initialize()
        
        # Verify Chat was initialized with the latest version's config
        mock_chat_class.assert_called_once()
        call_args = mock_chat_class.call_args[0][0]
        self.assertIn('config_1', call_args)  # config from latest record
        
        # Verify set_identity was called with the same version
        mock_chat.set_identity.assert_called_once_with('123456789', '1773107189834')
        
        # Verify the persisted version matches what was used
        self.assertEqual(self.account.x_chat_signing_key_version, '1773107189834')
