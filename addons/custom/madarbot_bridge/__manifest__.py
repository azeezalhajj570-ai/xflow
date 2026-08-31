{
    'name': 'MadarBot Bridge',
    'summary': 'Telegram integration for Odoo Discuss',
    'version': '19.0.1.0.0',
    'category': 'Discuss',
    'description': """
MadarBot Bridge - Telegram Integration for Odoo Discuss
========================================================

Connect your Odoo instance with Telegram via the MadarBot bridge. Allows your
team to send and receive Telegram messages directly within Odoo Discuss.

Features:
- Two-way Telegram messaging in Odoo Discuss
- Telegram contact synchronization
- Automated message handling and routing
- Seamless integration with Odoo's messaging framework
- Secure and auditable message logging
    """,
    'author': 'Custom',
    'website': 'https://github.com/azeezalhajj570-ai/odooo',
    'license': 'LGPL-3',
    'depends': [
        'mail',
        'bus',
        'contacts',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/madarbot_security.xml',
        'data/madarbot_data.xml',
        'data/madarbot_cron.xml',
        'views/madarbot_views.xml',
        'views/madarbot_menus.xml',
        'wizards/madarbot_composer_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'external_dependencies': {},
    'price': 0.0,
    'currency': 'EUR',
    'installable': True,
    'application': True,
    'auto_install': False,
}
