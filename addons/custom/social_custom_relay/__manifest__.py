{
    'name': 'Social Custom Relay',
    'version': '19.0.1.0.0',
    'summary': 'Route Social/IAP relay calls to a custom service',
    'category': 'Marketing/Social Marketing',
    'description': """
Social Custom Relay
====================
Route Odoo Social API calls through a custom relay service instead of the
default Odoo IAP infrastructure. Enables self-hosted social media posting
for on-premise deployments.

Features:
- Configurable relay endpoint for all social platforms
- Works with Facebook, Instagram, YouTube, Twitter, LinkedIn
- Compatible with Social Relay Service module
- Keep your API calls within your infrastructure
    """,
    'license': 'LGPL-3',
    'author': 'Custom',
    'website': 'https://github.com/azeezalhajj570-ai/odooo',
    'depends': [
        'social',
        'iap',
        'social_facebook',
        'social_instagram',
        'social_youtube',
        'social_twitter',
        'social_linkedin',
        'social_push_notifications',
    ],
    'data': [
        'data/ir_config_parameter_data.xml',
    ],
    'images': ['static/description/banner.png'],
    'external_dependencies': {},
    'price': 0.0,
    'currency': 'EUR',
    'installable': True,
    'application': False,
}
