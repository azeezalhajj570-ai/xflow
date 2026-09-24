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

    def test_initial_status_not_configured(self):
        # A fresh account has no X auth and no chat keys, so the aggregated
        # status the form shows is "not configured".
        self.assertEqual(self.account.x_connection_state, 'new')
        self.assertEqual(self.account.x_connection_status, 'not_configured')

    def test_transition_active(self):
        self.account._transition('active')
        self.assertEqual(self.account.x_connection_state, 'active')

    def test_transition_all_states(self):
        for state in ('new', 'authenticating', 'active', 'disconnected',
                      'invalid', 'reauth_required', 'error', 'disabled'):
            self.account._transition(state)
            self.assertEqual(self.account.x_connection_state, state)

    def test_aggregated_status_requires_chat_encryption(self):
        """Valid X authentication alone is not "connected": the required Chat
        Encryption setup must be complete too."""
        self.account._transition('active')
        self.assertEqual(self.account.x_connection_status, 'not_configured')
        self.account.write({'x_chat_initialized': True})
        self.assertEqual(self.account.x_connection_status, 'active')

    def test_aggregated_status_maps_connection_failures_to_error(self):
        self.account.write({'x_chat_initialized': True})
        self.account._transition('active')
        self.assertEqual(self.account.x_connection_status, 'active')
        for state in ('reauth_required', 'disconnected', 'invalid', 'error'):
            with self.subTest(state=state):
                self.account._transition(state)
                self.assertEqual(self.account.x_connection_status, 'error')

    def test_aggregated_status_maps_chat_failures_to_error(self):
        self.account.write({'x_chat_initialized': True})
        self.account._transition('active')
        self.assertEqual(self.account.x_chat_status, 'ready')
        self.account.write({'x_chat_pin_locked': True})
        self.assertEqual(self.account.x_connection_status, 'error')
        self.account.write({
            'x_chat_pin_locked': False,
            'x_chat_decrypt_stopped': True,
        })
        self.assertEqual(self.account.x_connection_status, 'error')
        self.account.write({'x_chat_decrypt_stopped': False})
        self.assertEqual(self.account.x_connection_status, 'active')

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

    def test_reauth_notification_emails_company_users(self):
        """A reauth notice must also email the users who manage X accounts in
        the account's company, not only push an in-app notification."""
        notif_user = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'X Reauth Receiver',
                'login': 'x_reauth_receiver',
                'email': 'x_reauth_receiver@example.com',
                'group_ids': [(6, 0, [
                    self.env.ref('social.group_social_user').id])],
                'company_ids': [(6, 0, self.account.company_id.ids)],
            })
        with self.mock_mail_gateway():
            self.account._notify_reauth_required('invalid_request: bad token')
        mails = self._new_mails
        self.assertTrue(mails)
        self.assertIn(notif_user.partner_id, mails.recipient_ids)
        self.assertIn('reauthentication', mails.subject)
        self.assertIn(self.account.name, mails.subject)
        self.assertIn('reauthentication', mails.body)

    def _notify_user(self, login, email):
        """An internal user who manages X accounts in the account's company."""
        return self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': login,
                'login': login,
                'email': email,
                'group_ids': [(6, 0, [
                    self.env.ref('social.group_social_user').id])],
                'company_ids': [(6, 0, self.account.company_id.ids)],
            })

    def test_decrypt_stopped_notification_emails_company_users(self):
        """The stopped-decryption notice must reach the users who manage X
        accounts for the account's company, naming the account."""
        notif_user = self._notify_user(
            'x_decrypt_receiver', 'x_decrypt_receiver@example.com')
        with self.mock_mail_gateway():
            self.account._notify_chat_decrypt_stopped(120)
        mails = self._new_mails
        self.assertTrue(mails)
        self.assertIn(notif_user.partner_id, mails.recipient_ids)
        self.assertIn('decrypt', mails.subject)
        self.assertIn(self.account.name, mails.subject)
        self.assertIn('decrypt', mails.body)

    def test_decrypt_streak_alerts_once_at_the_threshold(self):
        """Dropped deliveries only count up to one notice per episode."""
        self._notify_user('x_decrypt_alert', 'x_decrypt_alert@example.com')
        threshold = self.account._CHAT_DECRYPT_ALERT_THRESHOLD
        self.account._record_chat_decrypt_outcome(dropped=threshold - 1)
        self.assertFalse(self.account.x_chat_decrypt_stopped)
        with self.mock_mail_gateway():
            self.account._record_chat_decrypt_outcome(dropped=1)
        self.assertTrue(self.account.x_chat_decrypt_stopped)
        self.assertEqual(self.account.x_chat_decrypt_fail_streak, threshold)
        self.assertTrue(self.account.x_chat_decrypt_notified_at)
        self.assertTrue(self._new_mails)
        # Same episode: more drops must not send a second notice.
        with self.mock_mail_gateway():
            self.account._record_chat_decrypt_outcome(dropped=500)
        self.assertFalse(self._new_mails)

    def test_stored_message_rearms_the_decrypt_alert(self):
        """A message that decrypts again closes the episode and re-arms the
        alert, so a later outage is reported too."""
        self._notify_user('x_decrypt_rearm', 'x_decrypt_rearm@example.com')
        threshold = self.account._CHAT_DECRYPT_ALERT_THRESHOLD
        with self.mock_mail_gateway():
            self.account._record_chat_decrypt_outcome(dropped=threshold)
        self.assertTrue(self.account.x_chat_decrypt_stopped)
        self.account._record_chat_decrypt_outcome(stored=1)
        self.assertEqual(self.account.x_chat_decrypt_fail_streak, 0)
        self.assertFalse(self.account.x_chat_decrypt_stopped)
        self.assertFalse(self.account.x_chat_decrypt_notified_at)
        with self.mock_mail_gateway():
            self.account._record_chat_decrypt_outcome(dropped=threshold)
        self.assertTrue(self.account.x_chat_decrypt_stopped)
        self.assertTrue(self._new_mails)

    def test_key_reconfiguration_clears_the_decrypt_alert(self):
        """Reconfiguring the key is the operator's fix: it clears the flag so
        the account does not stay flagged after being repaired."""
        threshold = self.account._CHAT_DECRYPT_ALERT_THRESHOLD
        with self.mock_mail_gateway():
            self.account._record_chat_decrypt_outcome(dropped=threshold)
        self.assertTrue(self.account.x_chat_decrypt_stopped)
        self.account.write({'x_encryption_code': 'new-pin'})
        self.assertFalse(self.account.x_chat_decrypt_stopped)
        self.assertEqual(self.account.x_chat_decrypt_fail_streak, 0)

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
