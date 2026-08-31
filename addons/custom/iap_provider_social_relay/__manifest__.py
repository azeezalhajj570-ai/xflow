{
    'name': 'IAP Provider Social Relay',
    'summary': 'Concrete IAP alternative provider using a relay endpoint',
    'version': '19.0.1.0.0',
    'category': 'Tools',
    'description': """
IAP Provider Social Relay
=========================
Concrete implementation of an IAP alternative provider that routes requests
through a self-hosted relay endpoint. Enables Odoo Social features to work
without connecting to the official Odoo IAP service.

Ideal for on-premise and air-gapped deployments where external API access
is restricted or where you want to control the relay infrastructure.

Features:
- Custom relay endpoint configuration via system settings
- Compatible with all IAP-dependent social modules
- Self-hosted infrastructure for full data control
    """,
    'license': 'AGPL-3',
    'author': 'Custom',
    'website': 'https://github.com/azeezalhajj570-ai/odooo',
    'depends': [
        'iap',
        'iap_alternative_provider',
        'base_setup',
    ],
    'data': [
        'data/ir_config_parameter_data.xml',
        'views/res_config_settings_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'external_dependencies': {},
    'price': 0.0,
    'currency': 'EUR',
    'installable': True,
    'application': False,
}
