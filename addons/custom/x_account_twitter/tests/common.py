from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account_twitter')
class XAccountTwitterTestBase(XAccountTestBase):
    """Shared test base for x_account_twitter.

    Reuses x_account's XAccountTestBase (social_twitter stat/stream patches).
    """
