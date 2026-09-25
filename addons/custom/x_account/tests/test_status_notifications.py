from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestXStatusNotifications(XAccountTestBase):
    """Connection-status emails are throttled so a flapping account cannot
    email on every error/active transition.

    The email methods are mocked: these tests assert *how many* notices the
    decision sends, not their content (covered by TestXAccountLifecycle).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')

    def setUp(self):
        super().setUp()
        self.account = self.env['social.account'].create({
            'name': 'Flapping X Account',
            'media_id': self.twitter_media.id,
        })
        # One transition away from the aggregated status turning 'active'.
        self.account.write({'x_chat_initialized': True})

    # ---------------------------------------------------------------- helpers
    def _notify(self, action):
        """Run ``action`` with both status mails mocked.

        Returns ``(failures, recoveries)``: how many failure and recovery
        notices the decision sent.
        """
        model = type(self.account)
        with patch.object(model, '_notify_x_status_failed') as failed, \
                patch.object(model, '_notify_x_status_connected') as connected:
            action()
        return failed.call_count, connected.call_count

    def _fail(self):
        """One aggregated transition into 'error'."""
        self.account._transition('disconnected')

    def _recover(self):
        """One aggregated transition back to 'active'."""
        self.account._transition('active')

    def _set_error_age(self, age):
        """Backdate the error episode to simulate an outage of ``age``."""
        self.account.write({
            'x_status_error_started_at': fields.Datetime.now() - age,
        })

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

    # ------------------------------------------------------------------ tests
    def test_first_error_sends_one_failure_email(self):
        failures, recoveries = self._notify(self._fail)
        self.assertEqual((failures, recoveries), (1, 0))
        self.assertTrue(self.account.x_status_error_started_at)
        self.assertTrue(self.account.x_status_notification_at)

    def test_repeated_error_does_not_email_again(self):
        self._notify(self._fail)
        # A fresh read of the same failing state is still the same episode, so
        # there is nothing new to report.
        self.account.write({'x_connection_status': 'active'})
        failures, recoveries = self._notify(
            self.account._sync_x_connection_status)
        self.assertEqual((failures, recoveries), (0, 0))

    def test_short_recovery_is_silent(self):
        self._notify(self._fail)
        self._set_error_age(timedelta(minutes=1))
        failures, recoveries = self._notify(self._recover)
        self.assertEqual((failures, recoveries), (0, 0))
        self.assertFalse(self.account.x_status_error_started_at)

    def test_recovery_after_five_minutes_emails_once(self):
        self._notify(self._fail)
        self._set_error_age(timedelta(minutes=6))
        failures, recoveries = self._notify(self._recover)
        self.assertEqual((failures, recoveries), (0, 1))
        self.assertFalse(self.account.x_status_error_started_at)

    def test_flapping_does_not_generate_an_email_storm(self):
        self._recover()

        def flap():
            self._fail()
            for _ in range(6):
                self._set_error_age(timedelta(seconds=30))
                self._recover()  # short flap: silent
                self._fail()     # same cooldown window: silent

        failures, recoveries = self._notify(flap)
        self.assertEqual((failures, recoveries), (1, 0))

    def test_cooldown_suppresses_a_repeat_outage(self):
        self._recover()
        self._notify(self._fail)
        self._set_error_age(timedelta(seconds=30))
        self._notify(self._recover)  # short flap: silent close
        failures, recoveries = self._notify(self._fail)
        self.assertEqual((failures, recoveries), (0, 0))

    def test_normal_active_is_silent(self):
        failures, recoveries = self._notify(self._recover)
        self.assertEqual((failures, recoveries), (0, 0))
        # A refresh of an already-healthy account is not even a transition.
        failures, recoveries = self._notify(
            self.account._sync_x_connection_status)
        self.assertEqual((failures, recoveries), (0, 0))

    def test_reauth_notice_is_not_throttled(self):
        self._notify_user('x_reauth_throttle', 'x_reauth_throttle@example.com')
        # A recent status notice must not hold back the critical alert.
        self.account.write(
            {'x_status_notification_at': fields.Datetime.now()})
        with self.mock_mail_gateway():
            self.account._notify_reauth_required('invalid_request: bad token')
        self.assertTrue(self._new_mails)
        self.assertIn('reauthentication', self._new_mails.subject)

    def test_decrypt_stopped_still_alerts_once_per_episode(self):
        self._notify_user('x_decrypt_throttle', 'x_decrypt_throttle@example.com')
        # A recent status notice must not hold back the critical alert.
        self.account.write(
            {'x_status_notification_at': fields.Datetime.now()})
        threshold = self.account._CHAT_DECRYPT_ALERT_THRESHOLD
        model = type(self.account)
        with patch.object(model, '_notify_x_status_failed'), \
                patch.object(model, '_notify_x_status_connected'):
            with self.mock_mail_gateway():
                self.account._record_chat_decrypt_outcome(dropped=threshold)
            self.assertTrue(self._new_mails)
            with self.mock_mail_gateway():
                self.account._record_chat_decrypt_outcome(dropped=threshold)
            self.assertFalse(self._new_mails)

    def test_new_outage_after_the_cooldown_emails_again(self):
        self._recover()
        self._notify(self._fail)
        self._set_error_age(timedelta(seconds=30))
        self._notify(self._recover)  # short flap: silent close
        # The previous notice is now outside the cooldown window, so a genuinely
        # new outage notifies again.
        self.account.write({
            'x_status_notification_at':
                fields.Datetime.now() - timedelta(minutes=31),
        })
        failures, recoveries = self._notify(self._fail)
        self.assertEqual((failures, recoveries), (1, 0))
