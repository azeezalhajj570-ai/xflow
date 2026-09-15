from odoo.tests import tagged

from .common import XAccountGetXAPITestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPIUsage(XAccountGetXAPITestBase):
    """Usage records must keep their account attribution."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['social.account'].create({
            'name': 'Usage Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'usage_user',
            'twitter_user_id': '12345',
        })

    def _usage(self, **vals):
        base = {
            'account_id': self.account.id,
            'endpoint': 'twitter/tweet/favorite',
            'method': 'POST',
            'status_code': 402,
            'success': False,
        }
        base.update(vals)
        return self.env['getxapi.api.usage'].sudo().create(base)

    def test_usage_account_cannot_be_deleted(self):
        """Deleting an account with usage records is refused, so spend history
        is never silently anonymized and hidden from the usage page."""
        self._usage()
        with self.assertRaises(Exception):
            self.account.unlink()

    def test_account_without_usage_can_be_deleted(self):
        """The restriction only applies to accounts that have spend history."""
        self.account.unlink()
        self.assertFalse(self.account.exists())
