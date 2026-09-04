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

    # ------------------------------------------------------------ X webhooks
    x_twitter_webhook_enabled = fields.Boolean(
        string='X Webhooks Enabled',
        config_parameter='x_account_twitter.webhook_enabled',
        help='Enable real-time X event delivery via the official V2 Webhooks API '
             '+ X Activity API (sub-second DM / chat / post events).',
    )
    x_twitter_webhook_base_url = fields.Char(
        string='X Webhook Base URL',
        config_parameter='x_account_twitter.webhook_base_url',
        help='Public, HTTPS base URL where Odoo is reachable (no path, no port, '
             'e.g. https://x.example.com). The receiver is registered as '
             '<base>/x_account/twitter/webhook. X requires a public HTTPS URL '
             'with no port.',
    )
    x_twitter_app_consumer_secret = fields.Char(
        string='X App Consumer Secret (API Secret Key)',
        config_parameter='x_account_twitter.app_consumer_secret',
        help='The X app API Secret Key (consumer secret). Used to verify the '
             'HMAC-SHA256 webhook signature and answer the Challenge-Response '
             'Check (CRC). Never leave this in the Developer Console logs.',
    )
    x_twitter_app_bearer_token = fields.Char(
        string='X App Bearer Token (App-Only)',
        config_parameter='x_account_twitter.app_bearer_token',
        help='OAuth 2.0 App-Only Bearer Token of the X app. Used to register and '
             'manage webhooks and X Activity API subscriptions.',
    )
    x_twitter_webhook_url = fields.Char(
        string='X Webhook URL',
        compute='_compute_x_twitter_webhook_url',
        help='The webhook receiver URL to register with X.',
    )

    def _compute_x_twitter_webhook_url(self):
        icp = self.env['ir.config_parameter'].sudo()
        base = icp.get_param('x_account_twitter.webhook_base_url', '')
        for record in self:
            record.x_twitter_webhook_url = '%s/x_account/twitter/webhook' % (
                base.rstrip('/') if base else '')