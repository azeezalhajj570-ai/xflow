{
    'name': 'Social Relay Service',
    'version': '19.0.1.0.0',
    'summary': 'Self-hosted relay endpoints for Odoo Social integrations',
    'category': 'Marketing/Social Marketing',
    'description': """
Social Relay Service
=====================
Self-hosted relay endpoints for Odoo Social Marketing integrations.

This module provides the server-side component for relaying Odoo Social API
requests through your own infrastructure instead of the official Odoo IAP
service. Works with Social Custom Relay to create a complete self-hosted
social posting pipeline.

Features:
- Configurable relay endpoints
- Secure API key authentication
- Compatible with all Odoo Social modules
- Complete data sovereignty
    """,
    'license': 'LGPL-3',
    'author': 'Custom',
    'website': 'https://github.com/azeezalhajj570-ai/odooo',
    'depends': ['base_setup', 'web'],
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
