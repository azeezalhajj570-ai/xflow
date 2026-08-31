# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Social YouTube',
    'category': 'Marketing/Social Marketing',
    'summary': 'Manage your YouTube videos and schedule video uploads',
    'version': '19.0.1.0.0',
    'description': """
Social YouTube for Odoo
========================
Manage your YouTube channel, upload videos, and schedule posts directly from
Odoo with full YouTube Data API v3 integration.

Features:
- OAuth2-based YouTube account authentication
- Upload and schedule video posts
- Comment moderation and reply management
- Stream monitoring for channel activity
- Full integration with Odoo Social Marketing framework
    """,
    'depends': ['social', 'iap'],
    'data': [
        'data/social_media_data.xml',
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/social_account_views.xml',
        'views/social_post_views.xml',
        'views/social_post_template_views.xml',
        'views/social_stream_post_views.xml',
        'views/social_youtube_templates.xml',
        'wizard/social_account_revoke_youtube_views.xml',
    ],
    'author': 'Custom',
    'website': 'https://github.com/azeezalhajj570-ai/odooo',
    'images': ['static/description/banner.png'],
    'external_dependencies': {},
    'price': 0.0,
    'currency': 'EUR',
    'installable': True,
    'application': True,
    'auto_install': True,
    'assets': {
        'web.assets_backend': [
            'social_youtube/static/src/js/social_youtube_upload_field.js',
            'social_youtube/static/src/js/stream_post_comment.js',
            'social_youtube/static/src/js/stream_post_comment_list.js',
            'social_youtube/static/src/js/stream_post_comments.js',
            'social_youtube/static/src/js/stream_post_comments_reply.js',
            'social_youtube/static/src/js/stream_post_kanban_dashboard.js',
            'social_youtube/static/src/js/stream_post_kanban_record.js',
            ('after', 'social/static/src/js/social_post_formatter_mixin.js', 'social_youtube/static/src/js/social_post_formatter_mixin.js'),
            'social_youtube/static/src/scss/social_youtube.scss',
            'social_youtube/static/src/xml/**/*',
        ],
    },
    'license': 'OEEL-1',
}
