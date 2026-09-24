from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestAccountTracking(XAccountTestBase):
    """X account settings carry mail.thread tracking, so every change is
    logged in the account chatter (Odoo style).

    Secrets are deliberately left untracked: a tracking value is written to
    mail.message in clear text, so tracking a PIN or a key blob would leak the
    credential into the chatter of every follower.

    Odoo defers tracking to a precommit hook, and ``create`` marks the new
    record as "tracking discarded", so a write that follows a create in the
    same transaction produces nothing until the pending hooks are run —
    ``_flush_tracking`` is that run.
    """

    TRACKED = [
        'active',
        'x_connection_status',
        'x_auto_archive',
        'x_auto_archive_start',
        'x_auto_archive_end',
        'x_auth_method',
        'x_session_store_id',
        'x_chat_key_mode',
        'x_chat_initialized',
        'x_chat_pin_locked',
        'x_chat_decrypt_stopped',
        'x_migration_status',
    ]

    SECRETS = [
        'x_encryption_code',
        'x_chat_key_blob',
        'x_chat_conversation_keys',
    ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Tracked Account',
            'media_id': cls.media.id,
            'social_account_handle': 'tracked_acc',
        })
        cls._flush_tracking()

    @classmethod
    def _flush_tracking(cls):
        """Run the pending tracking hooks. Odoo creates tracking messages in
        a precommit callback, which a test transaction never reaches."""
        cls.env.flush_all()
        cls.env.cr.precommit.run()

    def _tracking_value(self, field_name, record=None):
        """Newest tracking value logged for ``field_name``, if any."""
        self._flush_tracking()
        record = record or self.account
        record.invalidate_recordset()
        values = record.message_ids.tracking_value_ids.filtered(
            lambda v: v.field_id.name == field_name)
        return values.sorted('id', reverse=True)[:1]

    def test_model_has_a_chatter(self):
        self.assertIn('message_ids', self.account._fields)
        self.assertIn('message_follower_ids', self.account._fields)
        self.assertTrue(self.account.message_post(body='hello'))

    def test_fields_are_declared_as_tracked(self):
        for name in self.TRACKED:
            with self.subTest(field=name):
                self.assertTrue(
                    getattr(self.account._fields[name], 'tracking', None),
                    '%s is not tracked' % name)

    def test_connection_status_change_is_logged(self):
        """The aggregated status is system-controlled but still tracked: when it
        moves, the chatter records it."""
        self.account.write({'x_chat_initialized': True})
        self.account.write({'x_connection_state': 'active'})
        value = self._tracking_value('x_connection_status')
        self.assertTrue(value, 'x_connection_status change was not logged')
        self.assertEqual(value.new_value_char, 'متصل')

    def test_archive_window_changes_are_logged(self):
        self.account.write({
            'x_auto_archive': True,
            'x_auto_archive_start': 2.0,
            'x_auto_archive_end': 15.0,
        })
        self.assertTrue(self._tracking_value('x_auto_archive'))
        start = self._tracking_value('x_auto_archive_start')
        self.assertTrue(start)
        self.assertEqual(start.new_value_float, 2.0)

    def test_chat_key_state_change_is_logged(self):
        self.account.write({'x_chat_initialized': True})
        value = self._tracking_value('x_chat_initialized')
        self.assertTrue(value)
        self.assertEqual(value.new_value_integer, 1)

    def test_archive_and_unarchive_are_logged(self):
        """Archiving is a tracked change: the chatter shows who archived the
        account and when (the daily cron does it unattended)."""
        self.account.write({'active': False})
        self._flush_tracking()
        self.account.write({'active': True})
        self.assertTrue(self._tracking_value('active'))
        self.account.invalidate_recordset()
        self.assertTrue(self.account.active)
        values = self.account.message_ids.tracking_value_ids.filtered(
            lambda v: v.field_id.name == 'active')
        self.assertEqual(len(values), 2)

    def test_secrets_are_not_tracked(self):
        for name in self.SECRETS:
            with self.subTest(field=name):
                self.assertFalse(
                    getattr(self.account._fields[name], 'tracking', None),
                    '%s must never be tracked' % name)

    def test_secrets_never_reach_the_chatter(self):
        """Even writing a credential posts no message: the value must not end
        up in a chatter body a follower can read."""
        secret = 'super-secret-pin-42'
        before = self.account.message_ids
        self.account.write({'x_encryption_code': secret})
        self._flush_tracking()
        self.account.invalidate_recordset()
        new_bodies = (self.account.message_ids - before).mapped('body')
        self.assertNotIn(secret, ' '.join(str(body) for body in new_bodies))
