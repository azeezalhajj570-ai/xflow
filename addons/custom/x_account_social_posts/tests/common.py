from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


class _StubProvider:
    """Minimal XProvider read surface for tests (no HTTP)."""

    def __init__(self, posts=None, comments=None, retweeters=None, likers=None,
                 likers_unsupported=False):
        self._posts = posts or []
        self._comments = comments or []
        self._retweeters = retweeters or []
        self._likers = likers or []
        self._likers_unsupported = likers_unsupported

    def fetch_user_posts(self, screen_name, limit=20, cursor=None,
                         per_post_limit=None):
        return {'posts': self._posts, 'cursor': '', 'has_more': False}

    def fetch_post_comments(self, tweet_id, limit=50, cursor=None):
        return {'comments': self._comments, 'cursor': '', 'has_more': False}

    def fetch_post_retweeters(self, tweet_id, limit=100, cursor=None):
        return {'users': self._retweeters, 'cursor': '', 'has_more': False}

    def fetch_post_likers(self, tweet_id, limit=100, cursor=None):
        if self._likers_unsupported:
            return {'users': [], 'unsupported': True,
                    'reason': 'x_no_public_likers_endpoint'}
        return {'users': self._likers, 'cursor': '', 'has_more': False}


@tagged('post_install', '-at_install', 'x_account_social_posts')
class XAccountSocialPostsTestBase(XAccountTestBase):
    """Shared test base for x_account_social_posts.

    Reuses x_account's XAccountTestBase (social_twitter HTTP/stream patches).
    A 'session_web' provider account is used so no optional provider module is
    required; the provider itself is stubbed in each test.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'X Posts Test Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'testx',
            'x_provider': 'session_web',
        })

    @staticmethod
    def post_dto(**overrides):
        dto = {
            'id': '111',
            'text': 'Hello X',
            'author_id': '42',
            'author_username': 'testx',
            'author_name': 'Test X',
            'created_at': '2026-01-01T10:00:00Z',
            'favorite_count': 7,
            'retweet_count': 3,
            'reply_count': 2,
            'quote_count': 1,
            'raw': {'rest_id': '111'},
        }
        dto.update(overrides)
        return dto
