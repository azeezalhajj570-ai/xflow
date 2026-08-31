# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'Social Reddit',
    'category': 'Marketing/Social Marketing',
    'summary': 'Manage your Reddit accounts and schedule posts',
    'version': '19.0.1.0.0',
    'description': """
Social Reddit for Odoo
=======================
Manage your Reddit accounts and schedule text, link, and image posts directly
from Odoo with full Reddit API integration.

Features:
- OAuth2-based Reddit account authentication
- Schedule and publish text, link, and image posts
- Manage multiple Reddit accounts
- Subreddit targeting for each post
- Stream integration for monitoring Reddit activity
- Full integration with Odoo Social Marketing framework
    """,
    'depends': ['social'],
    'data': [
        'data/social_media_data.xml',
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/social_account_views.xml',
        'views/social_post_views.xml',
        'views/social_post_template_views.xml',
        'views/social_reddit_templates.xml',
    ],
    'author': 'Odoo',
    'website': 'https://github.com/azeezalhajj570-ai/odooo',
    'images': [
        'static/description/banner.png',
    ],
    'external_dependencies': {},
    'price': 0.0,
    'currency': 'EUR',
    'auto_install': False,
    'installable': True,
    'application': True,
    'assets': {
        'web.assets_backend': [
            'social_reddit/static/src/scss/social_reddit.scss',
            'social_reddit/static/src/xml/**/*',
        ],
    },
    'license': 'OEEL-1',
}
