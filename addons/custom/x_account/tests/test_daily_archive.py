from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestDailyAutoArchive(XAccountTestBase):
    """Scheduled action: archive accounts flagged with x_auto_archive while the
    current time is inside the datetime window set on the account."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')

    def _make_account(self, handle, **vals):
        base = {
            'name': handle,
            'media_id': self.twitter_media.id,
            'x_auto_archive': False,
        }
        base.update(vals)
        return self.env['social.account'].create(base)

    def _flagged(self, handle, start, end, **vals):
        return self._make_account(
            handle, x_auto_archive=True, x_auto_archive_start=start,
            x_auto_archive_end=end, **vals)

    def test_flag_and_cron_archive_inside_window(self):
        now = fields.Datetime.now()
        flagged = self._flagged(
            'flagged', now - timedelta(hours=1), now + timedelta(hours=1))
        unflagged = self._make_account('unflagged')
        archived = flagged.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 1)
        flagged.invalidate_recordset()
        unflagged.invalidate_recordset()
        self.assertFalse(flagged.active)
        self.assertTrue(unflagged.active)

    def test_outside_window_archives_nothing(self):
        now = fields.Datetime.now()
        flagged = self._flagged(
            'flagged_late', now - timedelta(hours=2), now - timedelta(hours=1))
        archived = flagged.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 0)
        flagged.invalidate_recordset()
        self.assertTrue(flagged.active)

    def test_future_window_archives_nothing(self):
        now = fields.Datetime.now()
        flagged = self._flagged(
            'flagged_soon', now + timedelta(hours=1), now + timedelta(hours=2))
        archived = flagged.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 0)
        flagged.invalidate_recordset()
        self.assertTrue(flagged.active)

    def test_each_account_uses_its_own_window(self):
        """One sweep archives the accounts whose window matches and leaves the
        others untouched."""
        now = fields.Datetime.now()
        inside = self._flagged(
            'inside', now - timedelta(minutes=30), now + timedelta(minutes=30))
        outside = self._flagged(
            'outside', now + timedelta(hours=1), now + timedelta(hours=2))
        archived = inside.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 1)
        inside.invalidate_recordset()
        outside.invalidate_recordset()
        self.assertFalse(inside.active)
        self.assertTrue(outside.active)

    def test_enabling_archive_daily_requires_a_window(self):
        with self.assertRaises(ValidationError):
            self._make_account('no_window', x_auto_archive=True)

    def test_clearing_the_window_of_a_flagged_account_is_refused(self):
        now = fields.Datetime.now()
        account = self._flagged(
            'flagged', now - timedelta(hours=1), now + timedelta(hours=1))
        with self.assertRaises(ValidationError):
            account.write({'x_auto_archive_start': False})

    def test_flagged_account_without_window_is_skipped(self):
        """Accounts flagged before the window existed are left alone instead of
        being archived."""
        account = self._make_account('legacy', x_auto_archive=False)
        self.env.cr.execute(
            'UPDATE social_account SET x_auto_archive = TRUE WHERE id = %s',
            (account.id,))
        account.invalidate_recordset()
        archived = account.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 0)
        account.invalidate_recordset()
        self.assertTrue(account.active)

    def test_archived_account_only_once(self):
        """An account already archived must not be re-processed (so its X
        subscriptions are not pruned a second time)."""
        now = fields.Datetime.now()
        account = self._flagged(
            'already_gone', now - timedelta(hours=1), now + timedelta(hours=1))
        account.write({'active': False})
        account.invalidate_recordset()
        archived = account.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 0)
