# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    x_auth_method = fields.Selection(
        selection_add=[
            ('oauth2', 'OAuth 2.0 (Official Publish)'),
        ],
    )
    x_oauth2_client_id = fields.Char(
        string='X OAuth 2.0 Client ID',
        config_parameter='social.twitter_oauth2_client_id',
        help='OAuth 2.0 Client ID from your X app (Dev Portal > Keys and tokens > '
             'OAuth 2.0 Client ID). Used for the PKCE account-linking flow.',
    )
    x_oauth2_client_secret = fields.Char(
        string='X OAuth 2.0 Client Secret',
        config_parameter='social.twitter_oauth2_client_secret',
        help='OAuth 2.0 Client Secret from your X app. Used to exchange codes '
             'and refresh tokens (confidential client).',
    )