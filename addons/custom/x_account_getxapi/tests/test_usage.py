from odoo.tests import tagged

from .common import XAccountGetXAPITestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPIUsage(XAccountGetXAPITestBase):
    """Usage records must keep their account attribution forever."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['social.account'].create({
            'name': 'Usage Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'usage_user',
            'twitter_user_id': '12345',
        })

    def test_usage_account_cannot_be_deleted(self):
        """Deleting an account with usage records must be blocked so spend
        history is never silently anonymized and hidden from the usage page."""
        self.env['getxapi.api.usage'].sudo().create({
            'account_id': self.account.id,
            'endpoint': 'twitter/tweet/favorite',
            'method': 'POST',
            'status_code': 402,
            'success': False,
        })
        with self.assertRaises(Exception):
            self.account.unlink()

    def test_usage_without_account_can_be_attributed(self):
        """Rows whose account reference was lost can still be re-attributed."""
        usage = self.env['getxapi.api.usage'].sudo().create({
            'account_id': self.account.id,
            'endpoint': 'twitter/tweet/favorite',
            'method': 'POST',
            'status_code': 402,
            'success': False,
        })
        usage.write({'account_id': self.account.id})
        self.assertEqual(usage.account_id, self.account)