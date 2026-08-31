from unittest.mock import patch

from odoo.tests import tagged
from odoo.addons.x_account.tests.common import XAccountTestBase

from odoo.addons.x_account.services.session_manager import XSessionManager
from odoo.addons.x_account.services.providers.session_web import SessionWebProvider


@tagged('post_install', '-at_install', 'x_account')
class TestXPortability(XAccountTestBase):
    """Unit-level portability test (mocked HTTP).

    The real end-to-end test (real XAction account -> real X read + permitted
    write) is operational and documented in docs/x_account/00-specification.md.
    This verifies the restore-across-restart mechanics using the durable store.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'portability-test-key')
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.cookie = 'auth_token=port_auth; ct0=port_ct0'

    def test_restore_after_restart_repeats_operations(self):
        account = self.env['social.account'].create({
            'name': 'Portability X Account',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'portuser',
        })
        XSessionManager.create_store(account, self.cookie, source='test')

        # First run
        with patch.object(SessionWebProvider, 'validate_session',
                          return_value={'valid': True,
                                        'user': {'id': '1', 'username': 'portuser',
                                                 'name': 'X'},
                                        'reason': 'ok'}):
            from odoo.addons.x_account.services.x_service import XService
            self.assertTrue(XService.validate(account)['valid'])
            provider = XService.get_provider(account)
            self.assertEqual(provider.cookies['auth_token'], 'port_auth')

        # Simulate Odoo restart: drop the in-memory runtime registry.
        XSessionManager.drop_runtime(account)

        # Second run must restore session from durable store, not memory.
        with patch.object(SessionWebProvider, 'validate_session',
                          return_value={'valid': True,
                                        'user': {'id': '1', 'username': 'portuser',
                                                 'name': 'X'},
                                        'reason': 'ok'}):
            from odoo.addons.x_account.services.x_service import XService
            provider = XService.get_provider(account)
            self.assertEqual(provider.cookies['auth_token'], 'port_auth')
            self.assertTrue(XService.validate(account)['valid'])
        account.invalidate_recordset()
        self.assertEqual(account.x_connection_status, 'active')
