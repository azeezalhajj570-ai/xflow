from unittest.mock import patch

from odoo.tests import tagged
from odoo.addons.x_account.tests.common import XAccountTestBase

from odoo.addons.x_account.services.session_manager import XSessionManager
from odoo.addons.x_account.services.providers.session_web import SessionWebProvider


@tagged('post_install', '-at_install', 'x_account')
class TestXSession(XAccountTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'test-encryption-key')

        cls.account = cls.env['social.account'].create({
            'name': 'Test X Account',
            'media_id': cls.twitter_media.id,
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
        })

        cls.cookie = 'auth_token=test_auth_token; ct0=test_ct0'

    def test_parse_cookie_string(self):
        cookies = SessionWebProvider.parse_cookie_string('auth_token=abc; ct0=def')
        self.assertEqual(cookies, {'auth_token': 'abc', 'ct0': 'def'})
        cookies = SessionWebProvider.parse_cookie_string('')
        self.assertEqual(cookies, {})

    def test_encrypt_decrypt_roundtrip(self):
        blob = XSessionManager.encrypt(self.env, self.cookie)
        self.assertNotIn('test_auth_token', blob)
        self.assertNotIn('test_ct0', blob)
        decrypted = XSessionManager.decrypt(self.env, blob)
        self.assertEqual(decrypted, self.cookie.encode('utf-8'))

    def test_blob_format_and_alg(self):
        blob = XSessionManager.encrypt(self.env, self.cookie)
        self.assertTrue(blob.startswith('aes-256-gcm:'))
        parts = blob.split(':')
        self.assertEqual(len(parts), 4)

    def test_create_store_persists_encrypted(self):
        XSessionManager.create_store(self.account, self.cookie, source='test')
        self.assertTrue(self.account.x_session_store_id)
        blob = self.account.x_session_store_id.encrypted_blob
        self.assertNotIn('test_auth_token', blob)
        self.assertEqual(self.account.x_session_store_id.alg, 'aes-256-gcm')

    def test_load_returns_session(self):
        XSessionManager.create_store(self.account, self.cookie, source='test')
        loaded = XSessionManager.load(self.account)
        self.assertEqual(loaded, self.cookie)

    def test_delete_store_removes_session(self):
        XSessionManager.create_store(self.account, self.cookie, source='test')
        self.assertTrue(self.account.x_session_store_id)
        XSessionManager.delete_store(self.account)
        self.assertFalse(self.account.x_session_store_id)
        self.assertIsNone(XSessionManager.load(self.account))

    def test_load_without_store_returns_none(self):
        self.assertIsNone(XSessionManager.load(self.account))

    def test_validate_valid_session(self):
        result = {
            'valid': True,
            'user': {'id': '123', 'username': 'foo', 'name': 'Foo'},
            'reason': 'ok',
        }
        with patch.object(SessionWebProvider, 'validate_session', return_value=result):
            from odoo.addons.x_account.services.x_service import XService
            outcome = XService.validate(self.account)
        self.assertTrue(outcome['valid'])
        self.assertEqual(self.account.x_connection_status, 'active')
        self.assertTrue(self.account.last_validated)

    def test_validate_invalid_session_without_invalidation(self):
        result = {'valid': False, 'user': None, 'reason': 'verify_credentials returned HTTP 401', 'status': 401}
        with patch.object(SessionWebProvider, 'validate_session', return_value=result):
            from odoo.addons.x_account.services.x_service import XService
            outcome = XService.validate(self.account)
        self.assertFalse(outcome['valid'])
        self.assertEqual(self.account.last_error, result['reason'])
