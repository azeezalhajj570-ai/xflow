# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""XActions provider configuration on res.config.settings.

XActions-specific configuration, declared here (not in x_account) so the base
module stays provider-agnostic.
"""

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    x_provider = fields.Selection(
        selection_add=[
            ('xactions', 'XActions API'),
        ],
        ondelete={'xactions': 'cascade'},
    )

    x_xactions_base_url = fields.Char(
        string='Base URL',
        config_parameter='x_account.xactions_base_url',
        default='https://xactions.azeez-tech.com',
        help='XActions API base URL. Configurable per environment — for '
             'example https://xactions.azeez-tech.com (hosted), '
             'http://xactions-api-1:3001 (same docker network) or '
             'http://localhost:3001 (local).',
    )
    x_xactions_token = fields.Char(
        string='API Token (JWT)',
        config_parameter='x_account.xactions_token',
        help='Bearer token for the XActions API. Leave empty to log in with '
             'the identifier/password below.',
    )
    x_xactions_identifier = fields.Char(
        string='Identifier',
        config_parameter='x_account.xactions_identifier',
        help='XActions username or email, used to obtain a token when no '
             'static token is set.',
    )
    x_xactions_password = fields.Char(
        string='Password',
        config_parameter='x_account.xactions_password',
    )
