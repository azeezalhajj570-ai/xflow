# -*- coding: utf-8 -*-
{
    'name': 'Social TikTok',
    'category': 'Marketing/Social Marketing',
    'summary': 'Manage your TikTok accounts and schedule video posts',
    'version': '19.0.1.0.0',
    'description': """
Social TikTok for Odoo
=======================
Manage your TikTok accounts and schedule video posts directly from Odoo with
full TikTok Content Posting API integration.

Features:
- OAuth2-based TikTok account authentication
- Schedule and publish video posts
- Direct video upload via TikTok's Content Posting API
- Manage multiple TikTok accounts
- Stream integration for monitoring TikTok activity
- Full integration with Odoo Social Marketing framework
    """,
    'depends': ['social'],
    'data': [
        'data/social_media_data.xml',
        'views/social_tiktok_templates.xml',
        'views/social_post_template_views.xml',
        'views/social_post_views.xml',
        'views/social_stream_post_views.xml',
        'views/res_config_settings_views.xml',
    ],
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
            'social_tiktok/static/src/js/stream_post_kanban_record.js',
            'social_tiktok/static/src/scss/social_tiktok.scss',
            'social_tiktok/static/src/xml/**/*',
        ],
    },
    'author': 'Black Monkey',
    'website': 'https://amis.lk/',
    'license': 'LGPL-3',
}
