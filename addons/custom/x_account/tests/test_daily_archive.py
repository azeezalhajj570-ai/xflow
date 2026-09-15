from datetime import datetime as _real_datetime
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.x_account.models.social_account import _local_minute_of_day
from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestDailyAutoArchive(XAccountTestBase):
    """Daily scheduled action: archive accounts flagged with x_auto_archive
    while the current time is inside the window set on the account.

    The company is on Asia/Riyadh (UTC+3), so 23:30 UTC is 02:30 local.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env.company.partner_id.tz = 'Asia/Riyadh'

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

    def _cron_at(self, utc_hour, utc_minute):
        """Run the cron as if the UTC (process) clock read the given time."""
        return patch.object(
            fields.Datetime, 'now',
            return_value=_real_datetime(2026, 9, 15, utc_hour, utc_minute, 0))

    # ------------------------------------------------------------- conversion

    def test_local_minute_of_day_uses_the_timezone(self):
        """23:30 UTC is 02:30 in the company timezone: minute 150, not 1410."""
        now = _real_datetime(2026, 9, 15, 23, 30)
        self.assertEqual(_local_minute_of_day(now, 'Asia/Riyadh'), 150)
        self.assertEqual(_local_minute_of_day(now, None), 1410)
        self.assertEqual(_local_minute_of_day(now, 'Not/AZone'), 1410)

    # ------------------------------------------------------------------- cron

    def test_window_is_read_on_the_company_timezone(self):
        """The window is the local one: 02:00-15:00 local matches 23:30 UTC."""
        flagged = self._flagged('flagged', 2, 15)
        unflagged = self._make_account('unflagged')
        with self._cron_at(23, 30):
            archived = flagged.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 1)
        flagged.invalidate_recordset()
        unflagged.invalidate_recordset()
        self.assertFalse(flagged.active)
        self.assertTrue(unflagged.active)

    def test_window_is_not_read_on_the_utc_process_clock(self):
        """Regression: 23:00-23:59 looks like "now" on the UTC clock, but the
        company clock says 02:30, so the account must be left alone."""
        flagged = self._flagged('flagged_utc', 23, 23.99)
        with self._cron_at(23, 30):
            archived = flagged.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 0)
        flagged.invalidate_recordset()
        self.assertTrue(flagged.active)

    def test_outside_local_window_archives_nothing(self):
        flagged = self._flagged('flagged_late', 10, 11)
        with self._cron_at(23, 30):
            archived = flagged.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 0)
        flagged.invalidate_recordset()
        self.assertTrue(flagged.active)

    def test_window_wrapping_past_midnight(self):
        """23:00 local -> 03:00 local contains 02:30 local."""
        flagged = self._flagged('flagged_overnight', 23, 3)
        with self._cron_at(23, 30):
            archived = flagged.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 1)
        flagged.invalidate_recordset()
        self.assertFalse(flagged.active)

    def test_company_without_timezone_falls_back_to_utc(self):
        self.env.company.partner_id.tz = False
        flagged = self._flagged('flagged_no_tz', 23, 23.99)
        with self._cron_at(23, 30):
            archived = flagged.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 1)
        flagged.invalidate_recordset()
        self.assertFalse(flagged.active)

    def test_each_account_uses_its_own_window(self):
        """One sweep archives the accounts whose window matches and leaves the
        others untouched."""
        inside = self._flagged('inside', 2, 3)
        outside = self._flagged('outside', 12, 13)
        with self._cron_at(23, 30):
            archived = inside.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 1)
        inside.invalidate_recordset()
        outside.invalidate_recordset()
        self.assertFalse(inside.active)
        self.assertTrue(outside.active)

    def test_archived_account_only_once(self):
        """An account already archived must not be re-processed (so its X
        subscriptions are not pruned a second time the next day)."""
        account = self._flagged('already_gone', 2, 15)
        account.write({'active': False})
        account.invalidate_recordset()
        with self._cron_at(23, 30):
            archived = account.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 0)

    # -------------------------------------------------------------- validation

    def test_enabling_archive_daily_requires_a_window(self):
        with self.assertRaises(ValidationError):
            self._make_account('no_window', x_auto_archive=True)

    def test_clearing_the_window_of_a_flagged_account_is_refused(self):
        account = self._flagged('flagged', 2, 15)
        with self.assertRaises(ValidationError):
            account.write({'x_auto_archive_start': 0, 'x_auto_archive_end': 0})

    def test_flagged_account_without_window_is_skipped(self):
        """Accounts flagged before the window existed (so with no window) are
        left alone instead of being archived."""
        account = self._make_account('legacy', x_auto_archive=False)
        self.env.cr.execute(
            'UPDATE social_account SET x_auto_archive = TRUE WHERE id = %s',
            (account.id,))
        account.invalidate_recordset()
        with self._cron_at(23, 30):
            archived = account.env['social.account']._cron_archive_flagged_accounts()
        self.assertEqual(archived, 0)
        account.invalidate_recordset()
        self.assertTrue(account.active)
