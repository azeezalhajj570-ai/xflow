from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class XAccountGetXAPITestBase(XAccountTestBase):
    """Shared test base for the x_account_getxapi module.

    Reuses x_account's XAccountTestBase (social_twitter stat/stream patches).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'test-encryption-key')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.getxapi_api_key', 'getxapi_test_key')
