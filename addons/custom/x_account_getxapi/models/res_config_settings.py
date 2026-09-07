# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""GetXAPI configuration fields on res.config.settings.

The GetXAPI API key is GetXAPI-specific configuration. It is declared here
(not in x_account) so x_account's settings stay GetXAPI-agnostic.
"""

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    x_getxapi_api_key = fields.Char(
        string='GetXAPI API Key',
        config_parameter='x_account.getxapi_api_key',
        help='GetXAPI API key (Authorization: Bearer). Used only by the optional '
             'GetXAPI provider; never stored in x.session.store.',
    )
