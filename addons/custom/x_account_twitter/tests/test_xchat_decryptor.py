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

    def test_select_key_record_persisted_version(self):
        """Test that persisted version is used when set."""
        records = self._make_records([
            '1772923727027',
            '1773107189834',  # latest
        ])
        client = MagicMock()
        client.request.return_value = {'data': records}
        
        # Set persisted version to the older one
        self.account.x_chat_signing_key_version = '1772923727027'
        
        decryptor = self._make_decryptor(client=client)
        selected = decryptor._select_key_record()
        
        # Should use the persisted version, not the latest
        self.assertEqual(selected['public_key_version'], '1772923727027')
        self.assertEqual(selected['juicebox_config']['config_key'], 'config_0')

    def test_select_key_record_persisted_version_missing(self):
        """Test clear error when persisted version not found in API response."""
        records = self._make_records(['1773107189834'])
        client = MagicMock()
        client.request.return_value = {'data': records}
        
        # Set persisted version that doesn't exist in API response
        self.account.x_chat_signing_key_version = '9999999999999'
        
        decryptor = self._make_decryptor(client=client)
        
        with self.assertRaises(ValueError) as cm:
            decryptor._select_key_record()
        
        error_msg = str(cm.exception)
        self.assertIn('9999999999999', error_msg)
        self.assertIn('1773107189834', error_msg)
        self.assertIn('reconciliation required', error_msg)

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
    def test_initialize_uses_persisted_version(self, mock_chat_class):
        """Test that initialize uses persisted version when set."""
        records = self._make_records([
            '1772923727027',
            '1773107189834',
        ])
        client = MagicMock()
        client.request.return_value = {'data': records}
        
        # Set persisted version
        self.account.x_chat_signing_key_version = '1772923727027'
        
        mock_chat = MagicMock()
        mock_chat_class.return_value = mock_chat
        
        decryptor = self._make_decryptor(client=client)
        decryptor.initialize()
        
        # Verify Chat was initialized with the persisted version's config
        mock_chat_class.assert_called_once()
        call_args = mock_chat_class.call_args[0][0]
        self.assertIn('config_0', call_args)  # config from first record
        
        # Verify set_identity was called with the persisted version
        mock_chat.set_identity.assert_called_once_with('123456789', '1772923727027')

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
