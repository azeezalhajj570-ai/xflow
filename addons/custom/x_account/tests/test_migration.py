from unittest.mock import patch

from odoo.tests import tagged
from odoo.addons.x_account.tests.common import XAccountTestBase

from odoo.addons.x_account.services.providers.session_web import SessionWebProvider


@tagged('post_install', '-at_install', 'x_account')
class TestXMigration(XAccountTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'migration-test-key')

    def _row(self, **kw):
        base = {
            'username': 'wfaw0533',
            'display_name': 'Unknown',
            'auth_token': 'real_auth',
            'ct0': 'real_ct0',
            'user_id': '12345',
            'source_account_id': 'acc_1',
            'source_user_id': 'usr_1',
            'session_cookie': 'auth_token=real_auth; ct0=real_ct0',
        }
        base.update(kw)
        return base

    def test_migrate_creates_account(self):
        rows = [self._row()]
        accounts = self.env['social.account']._migrate_from_xaction(rows, 'batch_1')
        self.assertEqual(len(accounts), 1)
        account = accounts[0]
        self.assertEqual(account.social_account_handle, 'wfaw0533')
        self.assertEqual(account.x_migration_status, 'pending')
        self.assertEqual(account.source_account_id, 'acc_1')
        self.assertEqual(account.migration_batch_id, 'batch_1')
        self.assertTrue(account.x_session_store_id)
        self.assertNotIn('real_auth', account.x_session_store_id.encrypted_blob)

    def test_migrate_is_idempotent(self):
        rows = [self._row()]
        accounts1 = self.env['social.account']._migrate_from_xaction(rows, 'batch_1')
        accounts2 = self.env['social.account']._migrate_from_xaction(rows, 'batch_1')
        self.assertEqual(accounts1.ids, accounts2.ids)

    def test_migrate_session_roundtrip(self):
        rows = [self._row()]
        accounts = self.env['social.account']._migrate_from_xaction(rows, 'batch_1')
        account = accounts[0]
        from odoo.addons.x_account.services.session_manager import XSessionManager
        loaded = XSessionManager.load(account)
        self.assertEqual(loaded, 'auth_token=real_auth; ct0=real_ct0')

    def test_migrate_then_validate(self):
        rows = [self._row()]
        accounts = self.env['social.account']._migrate_from_xaction(rows, 'batch_1')
        account = accounts[0]
        with patch.object(SessionWebProvider, 'validate_session',
                          return_value={'valid': True,
                                        'user': {'id': '12345', 'username': 'wfaw0533',
                                                 'name': 'Unknown'},
                                        'reason': 'ok'}):
            from odoo.addons.x_account.services.x_service import XService
            XService.validate(account)
        account.invalidate_recordset()
        self.assertEqual(account.x_connection_status, 'active')

    def test_migration_rollback_deletes_session_only(self):
        rows = [self._row()]
        accounts = self.env['social.account']._migrate_from_xaction(rows, 'batch_1')
        account = accounts[0]
        from odoo.addons.x_account.services.session_manager import XSessionManager
        XSessionManager.delete_store(account)
        self.assertFalse(account.x_session_store_id)
