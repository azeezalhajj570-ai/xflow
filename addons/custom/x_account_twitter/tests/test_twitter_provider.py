from datetime import timedelta
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.x_account.services.x_provider import XProviderRegistry
from odoo.addons.x_account.services.x_service import XService

from odoo.addons.x_account_twitter.services.twitter_api_client import TwitterApiClient
from odoo.addons.x_account_twitter.services.twitter_link import TwitterLink
from odoo.addons.x_account_twitter.services.twitter_provider import TwitterProvider

from .common import XAccountTwitterTestBase


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterRegistration(XAccountTwitterTestBase):
    """Provider self-registration + resolution through XProviderRegistry."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_consumer_key', 'test-consumer-key')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_consumer_secret_key', 'test-consumer-secret')

        cls.account = cls.env['social.account'].create({
            'name': 'Twitter Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'tw_user',
            'twitter_user_id': '12345',
            'twitter_oauth_token': 'test-oauth-token',
            'twitter_oauth_token_secret': 'test-oauth-secret',
            'x_provider': 'twitter',
            'x_auth_method': 'oauth1',
        })

    def test_registry_resolves_twitter_to_provider(self):
        self.assertIs(XProviderRegistry.resolve('twitter'), TwitterProvider)

    def test_get_provider_dispatches_twitter(self):
        provider = XService.get_provider(self.account)
        self.assertIsInstance(provider, TwitterProvider)
        self.assertFalse(provider._needs_cookies)

    def test_twitter_is_valid_selection_value(self):
        self.assertEqual(self.account.x_provider, 'twitter')

    def test_provider_does_not_require_cookies(self):
        # TwitterProvider must not attempt to load session cookies.
        provider = XService.get_provider(self.account)
        self.assertFalse(provider._needs_cookies)


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterLinkParsing(XAccountTwitterTestBase):
    """TwitterLink: URL -> post reference."""

    def test_parse_x_url(self):
        ref = TwitterLink.resolve('https://x.com/example/status/123456')
        self.assertEqual(ref['platform'], 'x')
        self.assertEqual(ref['post_id'], '123456')
        self.assertEqual(ref['canonical_url'], 'https://x.com/-/status/123456')

    def test_parse_twitter_url(self):
        ref = TwitterLink.resolve('https://twitter.com/example/status/987654')
        self.assertEqual(ref['post_id'], '987654')

    def test_parse_with_query_and_fragment(self):
        ref = TwitterLink.resolve('https://x.com/example/status/123456?s=20#frag')
        self.assertEqual(ref['post_id'], '123456')

    def test_rejects_non_status_url(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            TwitterLink.resolve('https://x.com/example')
        with self.assertRaises(ValidationError):
            TwitterLink.resolve('https://google.com/example/status/123')

    def test_rejects_empty(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            TwitterLink.resolve('')


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterProviderRepost(XAccountTwitterTestBase):
    """Repost + validation + error classification with a mocked transport."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_consumer_key', 'test-consumer-key')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_consumer_secret_key', 'test-consumer-secret')
        cls.account = cls.env['social.account'].create({
            'name': 'Twitter Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'tw_user',
            'twitter_user_id': '12345',
            'twitter_oauth_token': 'test-oauth-token',
            'twitter_oauth_token_secret': 'test-oauth-secret',
            'x_provider': 'twitter',
            'x_auth_method': 'oauth1',
        })
        cls.provider = TwitterProvider(cls.env, cls.account)

    def _mock_response(self, status_code=200, json_data=None):
        response = MagicMock()
        response.status_code = status_code
        response.ok = status_code < 400
        response.content = b'{}' if status_code < 400 else b''
        response.json.return_value = json_data if json_data is not None else {}
        return response

    # ------------------------------------------------------------- repost
    def test_repost_calls_api_and_normalizes(self):
        with patch.object(TwitterApiClient, 'request', return_value={
            'data': {'retweeted': True},
        }) as req:
            result = self.provider.repost({'post_id': '123456'})
        self.assertTrue(result['success'])
        self.assertEqual(result['operation'], 'repost')
        self.assertEqual(result['platform'], 'x')
        self.assertEqual(result['post_id'], '123456')
        # endpoint + body
        path = req.call_args.args[1]
        self.assertEqual(path, '/2/users/12345/retweets')
        self.assertEqual(req.call_args.kwargs['body'], {'tweet_id': '123456'})

    def test_repost_accepts_link_reference(self):
        ref = TwitterLink.resolve('https://x.com/example/status/555')
        with patch.object(TwitterApiClient, 'request', return_value={
            'data': {'retweeted': True},
        }) as req:
            result = self.provider.repost(ref)
        self.assertEqual(result['post_id'], '555')
        self.assertEqual(req.call_args.kwargs['body'], {'tweet_id': '555'})

    def test_repost_requires_post_id(self):
        with self.assertRaises(ValueError):
            self.provider.repost({})

    # ------------------------------------------------------------- validate
    def test_validate_session_ok(self):
        with patch.object(TwitterApiClient, 'request', return_value={
            'data': {'id': '12345', 'username': 'tw_user', 'name': 'Twitter Account'},
        }) as req:
            result = self.provider.validate_session()
        self.assertTrue(result['valid'])
        self.assertEqual(result['user']['id'], '12345')
        self.assertEqual(req.call_args.args[1], '/2/users/me')

    def test_validate_session_missing_tokens(self):
        account = self.account.with_context(todo='no tokens')
        account.write({'twitter_oauth_token': False,
                       'twitter_oauth_token_secret': False})
        try:
            provider = TwitterProvider(self.env, account)
            result = provider.validate_session()
            self.assertFalse(result['valid'])
            self.assertEqual(result['reason'], 'twitter_oauth_token_missing')
        finally:
            account.write({'twitter_oauth_token': 'test-oauth-token',
                           'twitter_oauth_token_secret': 'test-oauth-secret'})

    # ------------------------------------------------------------- errors
    def test_rate_limit_is_retryable(self):
        from odoo.addons.x_account_twitter.services.twitter_errors import (
            TwitterRateLimitError)
        with patch.object(TwitterApiClient, 'request',
                          side_effect=TwitterRateLimitError('rate limited')):
            result = self.provider.validate_session()
        self.assertFalse(result['valid'])
        self.assertEqual(result['reason'], 'rate_limit')

    def test_permission_denied_classification(self):
        from odoo.addons.x_account_twitter.services.twitter_errors import (
            TwitterPermissionError)
        with patch.object(TwitterApiClient, 'request',
                          side_effect=TwitterPermissionError('forbidden')):
            result = self.provider.validate_session()
        self.assertFalse(result['valid'])
        self.assertEqual(result['reason'], 'permission_denied')

    def test_temporary_error_is_retryable(self):
        from odoo.addons.x_account_twitter.services.twitter_errors import (
            TwitterTemporaryError)
        err = TwitterTemporaryError('boom')
        self.assertTrue(err.retryable)
        with patch.object(TwitterApiClient, 'request', side_effect=err):
            result = self.provider.validate_session()
        self.assertFalse(result['valid'])
        self.assertEqual(result['reason'], 'temporary_error')

    def test_repost_surfaces_normalized_error(self):
        from odoo.addons.x_account_twitter.services.twitter_errors import (
            TwitterNotFoundError)
        with patch.object(TwitterApiClient, 'request',
                          side_effect=TwitterNotFoundError('gone')):
            with self.assertRaises(TwitterNotFoundError) as ctx:
                self.provider.repost({'post_id': '999'})
        self.assertEqual(ctx.exception.code, 'not_found')
        self.assertFalse(ctx.exception.retryable)


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterProviderWebhookSubscriptions(XAccountTwitterTestBase):
    """_subscribe_all must create XAA subscriptions with the account's OAuth 2.0
    user-context token (X rejects the app bearer for subscription create)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_oauth2_client_id', 'test-client-id')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_oauth2_client_secret', 'test-client-secret')
        cls.account = cls.env['social.account'].create({
            'name': 'OAuth2 Twitter Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'oauth2_user',
            'twitter_user_id': '12345',
            'x_provider': 'twitter',
            'x_auth_method': 'oauth2',
            'x_oauth2_access_token': 'user-at-123',
            'x_oauth2_refresh_token': 'user-rt-123',
            'x_oauth2_token_expires_at': fields.Datetime.now() + timedelta(hours=1),
            'x_connection_status': 'active',
        })

    def test_subscribe_all_passes_user_access_token(self):
        from odoo.addons.x_account_twitter.services.twitter_webhook import (
            TwitterWebhook)
        provider = TwitterProvider(self.env, self.account)
        hook = self.env['x.twitter.webhook'].sudo().create({
            'name': 'https://x.example.com/x_account/twitter/webhook',
            'webhook_id': 'wh-test',
            'valid': True,
        })
        created = {}

        def _fake_create(user_id, event_type, webhook_id='', multiple=False,
                         access_token=''):
            created[event_type] = access_token
            return {'subscription_id': 'sub-%s' % event_type}

        with patch.object(TwitterWebhook, 'create_subscription',
                          side_effect=_fake_create):
            provider._subscribe_all(TwitterWebhook(self.env), hook)
        # every supported event was subscribed with the account's access token
        from odoo.addons.x_account_twitter.services.twitter_webhook import (
            SUPPORTED_EVENT_TYPES)
        self.assertEqual(
            set(created), set(SUPPORTED_EVENT_TYPES))
        self.assertTrue(all(tok == 'user-at-123' for tok in created.values()))
        subs = self.env['x.twitter.subscription'].sudo().search([
            ('account_id', '=', self.account.id),
        ])
        self.assertEqual(len(subs), len(SUPPORTED_EVENT_TYPES))
        self.assertTrue(all(s.state == 'active' for s in subs))
        # re-running is idempotent (rows exist, no new API calls)
        with patch.object(TwitterWebhook, 'create_subscription',
                          side_effect=_fake_create) as mocked:
            provider._subscribe_all(TwitterWebhook(self.env), hook)
        self.assertEqual(mocked.call_count, 0)
