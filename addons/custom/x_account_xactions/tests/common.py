from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase
from odoo.addons.x_account_xactions.services.xactions_provider import (
    XActionsProvider,
)

REPORT_PAYLOAD = {
    'success': True,
    'account': 'xactions_user',
    'postsRead': 1,
    'truncated': False,
    'posts': [{
        'id': '111',
        'text': 'Hello X',
        'createdAt': '2026-01-01T10:00:00.000Z',
        'author': {'id': '42', 'username': 'xactions_user', 'name': 'X Actions'},
        'metrics': {'likes': 7, 'retweets': 3, 'replies': 2, 'quotes': 1,
                    'views': 100},
        'audience': {
            'likers': [{'userId': '77', 'username': 'bob', 'name': 'Bob',
                        'verified': True}],
            'commenters': [{'userId': '88', 'username': 'carol', 'name': 'Carol'}],
            'retweeters': [{'id': '99', 'username': 'dave', 'name': 'Dave'}],
        },
        'counts': {'likers': 1, 'commenters': 1, 'retweeters': 1},
        'errors': {},
    }],
    'audience': [],
    'stats': {'posts': 1},
}


@tagged('post_install', '-at_install', 'x_account_xactions')
class XAccountXActionsTestBase(XAccountTestBase):
    """Shared test base for x_account_xactions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.xactions_base_url', 'http://xactions.test:3001')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.xactions_token', 'test.jwt.token')
        cls.account = cls.env['social.account'].create({
            'name': 'XActions Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'xactions_user',
            'x_provider': 'xactions',
        })
        cls.provider = XActionsProvider(cls.env, cls.account)
