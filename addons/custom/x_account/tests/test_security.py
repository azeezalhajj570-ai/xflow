from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.addons.x_account.tests.common import XAccountTestBase

from odoo.addons.x_account.services.session_manager import XSessionManager


@tagged('post_install', '-at_install', 'x_account')
class TestXSecurity(XAccountTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'security-test-key')
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Security X Account',
            'media_id': cls.twitter_media.id,
        })
        XSessionManager.create_store(cls.account, 'auth_token=secret_token; ct0=secret_ct0',
                                     source='test')
        # A plain internal user with no x.account manager / social manager roles.
        cls.plain_user = cls._create_plain_user()

    @classmethod
    def _create_plain_user(cls):
        user = cls.env['res.users'].create({
            'name': 'Plain User',
            'login': 'plain_x_user',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        return user

    def test_encrypted_blob_does_not_contain_credentials(self):
        blob = self.account.x_session_store_id.encrypted_blob
        self.assertNotIn('secret_token', blob)
        self.assertNotIn('secret_ct0', blob)

    def test_plain_user_cannot_read_session_store(self):
        store = self.account.x_session_store_id
        with self.assertRaises(AccessError):
            store.with_user(self.plain_user).read(['encrypted_blob'])

    def test_plain_user_cannot_access_session_store_model(self):
        with self.assertRaises(AccessError):
            self.env['x.session.store'].with_user(self.plain_user).search([])

    def test_cross_account_isolation(self):
        """A task on account A references only account A's session."""
        account_b = self.env['social.account'].create({
            'name': 'Other X Account',
            'media_id': self.account.media_id.id,
        })
        task_a = self.env['x.account.task'].create({
            'account_id': self.account.id,
            'operation': 'get_conversations',
        })
        task_b = self.env['x.account.task'].create({
            'account_id': account_b.id,
            'operation': 'get_conversations',
        })
        self.assertEqual(task_a.account_id.id, self.account.id)
        self.assertEqual(task_b.account_id.id, account_b.id)
        self.assertNotEqual(task_a.account_id.id, task_b.account_id.id)

    def test_validate_does_not_leak_credentials(self):
        from odoo.addons.x_account.services.x_service import XService
        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.validate_session',
                   return_value={'valid': True,
                                 'user': {'id': '1', 'username': 'u', 'name': 'U'},
                                 'reason': 'ok'}):
            XService.validate(self.account)
        self.assertEqual(self.account.last_error, False)
        self.assertNotIn('secret_ct0', self.account.last_error or '')
