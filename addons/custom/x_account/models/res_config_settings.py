# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    x_auth_method = fields.Selection(
        [
            ('session_cookie', 'Session Cookie'),
            ('oauth1', 'OAuth 1.0a (Official Publish)'),
        ],
        string='X Authentication Method',
        config_parameter='x_account.auth_method',
        default='session_cookie',
        help='Authentication method used when adding X accounts.',
    )
    x_provider = fields.Selection(
        [
            ('session_web', 'Session Web'),
            ('official_publish', 'Official Publish'),
        ],
        string='X Provider',
        config_parameter='x_account.provider',
        default='session_web',
        help='Provider implementation used for X HTTP operations.',
    )
    x_encryption_key_configured = fields.Boolean(
        string='X Session Encryption Key Configured',
        config_parameter='x_account.encryption_key_configured',
        help='Indicates that the encryption key is supplied via deployment '
             'config (X_SESSION_ENCRYPTION_KEY). The key itself is never stored in Odoo.',
    )
    x_web_api_base = fields.Char(
        string='X Web API Base URL',
        config_parameter='x_account.web_api_base',
        default='https://x.com/',
    )
    x_web_bearer_token = fields.Char(
        string='X Web Bearer Token',
        config_parameter='x_account.web_bearer_token',
        help='Bearer token used by the undocumented web-session provider. '
             'Stored encrypted/out of direct exposure; used only by the provider.',
    )
