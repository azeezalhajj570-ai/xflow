# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""OmniX configuration fields on res.config.settings.

The OmniX API key and the webhook base URL are OmniX-specific configuration.
They are declared here (not in x_account) so x_account's settings stay
OmniX-agnostic.
"""

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    x_provider = fields.Selection(
        selection_add=[
            ('omnix', 'OmniX REST API'),
        ],
        ondelete={'omnix': 'cascade'},
    )

    x_omnix_api_key = fields.Char(
        string='OmniX API Key',
        config_parameter='x_account.omnix_api_key',
        help='OmniX API key (Authorization: Bearer). Used only by the optional '
             'OmniX provider; never stored in x.session.store.',
    )
    x_webhook_base_url = fields.Char(
        string='X Webhook Base URL',
        config_parameter='x_account.webhook_base_url',
        help='Public https base URL of this Odoo instance, e.g. '
             'https://azeez-tech.com. OmniX webhooks are registered at '
             '<base>/x_account/webhook.',
    )
