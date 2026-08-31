
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    iap_provider_social_relay_endpoint = fields.Char(
        string='IAP Social Relay Endpoint',
        config_parameter='iap_provider_social_relay.endpoint',
        help='Base URL of your relay service (example: https://relay.example.com).',
    )
    iap_provider_social_relay_default_service = fields.Char(
        string='Default Service Technical Name',
        config_parameter='iap_provider_social_relay.default_service',
        help='Optional iap.service technical_name used when provider is Social Relay.',
    )
