from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account_omnix')
class XAccountOmniXTestBase(XAccountTestBase):
    """Shared test base for the x_account_omnix module.

    Reuses x_account's XAccountTestBase (social_twitter stat/stream patches).
    """
