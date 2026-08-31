{
    'name': 'Telegram Marketing',
    'summary': 'Design, send and track Telegram messages',
    'version': '19.0.1.0.0',
    'category': 'Marketing/Email Marketing',
    'description': """
Telegram Marketing for Odoo
============================
Design, send and track Telegram marketing campaigns directly from Odoo.
Extends the mass_mailing module with Telegram channel support.

Features:
- Create and manage Telegram mailing campaigns
- Track delivery and engagement metrics
- Segment audiences using Odoo's existing mailing lists
- Full integration with Telegram via MadarBot Bridge
- UTM tracking for campaign attribution
    """,
    'author': 'Custom',
    'website': 'https://github.com/azeezalhajj570-ai/odooo',
    'license': 'LGPL-3',
    'depends': [
        'mass_mailing',
        'madarbot_bridge',
    ],
    'data': [
        'data/utm.xml',
        'security/ir.model.access.csv',
        'views/mailing_mailing_views.xml',
        'views/mailing_trace_views.xml',
        'views/mailing_menus.xml',
    ],
    'images': ['static/description/banner.png'],
    'external_dependencies': {},
    'price': 0.0,
    'currency': 'EUR',
    'installable': True,
    'application': True,
    'auto_install': False,
}
