from odoo.tests import tagged
from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestXAccountLifecycle(XAccountTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Lifecycle X Account',
            'media_id': cls.twitter_media.id,
        })

    def test_initial_status_new(self):
        self.assertEqual(self.account.x_connection_status, 'new')

    def test_transition_active(self):
        self.account._transition('active')
        self.assertEqual(self.account.x_connection_status, 'active')

    def test_transition_all_states(self):
        for state in ('new', 'authenticating', 'active', 'disconnected',
                      'invalid', 'reauth_required', 'error', 'disabled'):
            self.account._transition(state)
            self.assertEqual(self.account.x_connection_status, state)

    def test_lifecycle_message_posted(self):
        self.account._post_lifecycle_message('Account connected')
        messages = self.env['mail.message'].sudo().search([
            ('model', '=', 'social.account'),
            ('res_id', '=', self.account.id),
        ])
        self.assertTrue(messages)
        self.assertIn('Account connected', str(messages[0].body))

    def test_last_error_set(self):
        self.account._set_last_error('verify_credentials returned HTTP 401')
        self.assertEqual(self.account.last_error, 'verify_credentials returned HTTP 401')

    def test_audit_fields(self):
        self.account.write({
            'x_migration_status': 'migrated',
            'source_account_id': 'acc_1',
            'source_user_id': 'usr_1',
            'migration_batch_id': 'batch_1',
            'migration_timestamp': self.account.last_validated,
        })
        self.assertEqual(self.account.x_migration_status, 'migrated')
        self.assertEqual(self.account.source_account_id, 'acc_1')
        self.assertEqual(self.account.migration_batch_id, 'batch_1')
