from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.x_account.services.providers.official_publish import XOfficialPublishAdapter
from odoo.addons.x_account.services.x_provider import XProviderRegistry
from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestXOfficialPublish(XAccountTestBase):
    """T16: optional publish-only OAuth adapter (separate from auth and session).

    The adapter is publish/stats-only (post_tweet, get_account_stats,
    get_last_tweets_stats) and never touches sessions. Its validate_session is a
    capability check, not a network call.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'test-encryption-key')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_consumer_key', 'test-consumer-key')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_consumer_secret_key', 'test-consumer-secret')

        cls.account = cls.env['social.account'].create({
            'name': 'Publish Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'publish_user',
            'twitter_user_id': '4242',
            'twitter_oauth_token': 'test-oauth-token',
            'twitter_oauth_token_secret': 'test-oauth-secret',
            'x_provider': 'official_publish',
            'x_auth_method': 'oauth1',
        })
        cls.adapter = XOfficialPublishAdapter(cls.env, cls.account)

    def test_validate_session_oauth1(self):
        result = self.adapter.validate_session()
        self.assertTrue(result['valid'])
        self.assertEqual(result['reason'], 'oauth1_adapter')
        self.assertEqual(result['user']['username'], 'publish_user')

    def test_validate_session_requires_tokens(self):
        account = self.account.with_context(todo='no tokens')
        account.write({'twitter_oauth_token': False,
                       'twitter_oauth_token_secret': False})
        try:
            result = self.adapter.validate_session()
            self.assertFalse(result['valid'])
            self.assertEqual(result['reason'], 'oauth1_token_missing')
        finally:
            account.write({'twitter_oauth_token': 'test-oauth-token',
                           'twitter_oauth_token_secret': 'test-oauth-secret'})

    def test_validate_session_requires_consumer_keys(self):
        with patch.object(self.adapter, '_check_oauth_configured', return_value=False):
            result = self.adapter.validate_session()
        self.assertFalse(result['valid'])
        self.assertEqual(result['reason'], 'social_twitter_consumer_keys_missing')

    def test_post_tweet_returns_tweet_id(self):
        response = MagicMock()
        response.ok = True
        response.json.return_value = {'data': {'id': '111222'}}
        with patch.object(self.adapter, '_request', return_value=response) as req:
            result = self.adapter.post_tweet('hello world')
        self.assertEqual(result, {'tweet_id': '111222'})
        url = req.call_args.args[0]
        self.assertIn('/2/tweets', url)
        self.assertEqual(req.call_args.kwargs['json']['text'], 'hello world')

    def test_post_tweet_requires_text(self):
        with self.assertRaises(ValueError):
            self.adapter.post_tweet('')

    def test_post_tweet_requires_consumer_keys(self):
        with patch.object(self.adapter, '_check_oauth_configured', return_value=False):
            with self.assertRaises(RuntimeError):
                self.adapter.post_tweet('hello')

    def test_post_tweet_failure_raises(self):
        response = MagicMock()
        response.ok = False
        response.text = 'forbidden'
        with patch.object(self.adapter, '_request', return_value=response):
            with self.assertRaises(RuntimeError):
                self.adapter.post_tweet('hello')

    def test_get_account_stats(self):
        response = MagicMock()
        response.ok = True
        response.json.return_value = {
            'data': [{'public_metrics': {
                'followers_count': 10, 'following_count': 5,
                'tweet_count': 3, 'listed_count': 1,
            }}],
        }
        with patch.object(self.adapter, '_request', return_value=response) as req:
            result = self.adapter.get_account_stats()
        self.assertEqual(result['followers_count'], 10)
        self.assertEqual(result['tweet_count'], 3)
        self.assertEqual(req.call_args.kwargs['params']['usernames'], 'publish_user')

    def test_get_last_tweets_stats(self):
        response = MagicMock()
        response.ok = True
        response.json.return_value = {
            'data': [
                {'public_metrics': {'like_count': 2, 'retweet_count': 1}},
                {'public_metrics': {'like_count': 3, 'retweet_count': 0}},
            ],
        }
        with patch.object(self.adapter, '_request', return_value=response):
            result = self.adapter.get_last_tweets_stats(count=2)
        self.assertEqual(result['count'], 2)
        self.assertEqual(result['engagement'], 5)
        self.assertEqual(result['stories'], 1)


@tagged('post_install', '-at_install', 'x_account')
class TestOmniXExtensionPoint(XAccountTestBase):
    """T16/T18: OmniX is a built-in optional provider; registry stays extensible."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'test-encryption-key')

    def test_builtin_provider_resolution(self):
        from odoo.addons.x_account.services.providers.omnix import OmniXProvider
        from odoo.addons.x_account.services.providers.session_web import SessionWebProvider
        self.assertIs(
            XProviderRegistry.resolve('session_web'), SessionWebProvider)
        self.assertIs(
            XProviderRegistry.resolve('official_publish'),
            XOfficialPublishAdapter)
        self.assertIs(
            XProviderRegistry.resolve('omnix'), OmniXProvider)

    def test_omnix_is_valid_selection_value(self):
        """'omnix' is a valid x_provider value (per-account either/or with session)."""
        account = self.env['social.account'].create({
            'name': 'OmniX Account',
            'media_id': self.twitter_media.id,
            'x_provider': 'omnix',
        })
        self.assertEqual(account.x_provider, 'omnix')

    def test_unknown_provider_rejected_by_selection(self):
        """An unregistered provider code is still rejected by the selection."""
        with self.assertRaises(ValueError):
            self.env['social.account'].create({
                'name': 'Unknown Provider',
                'media_id': self.twitter_media.id,
                'x_provider': 'not_a_provider',
            })
