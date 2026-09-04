from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.x_account_twitter.services.twitter_api_client import TwitterApiClient
from odoo.addons.x_account_twitter.services import twitter_errors

from .common import XAccountTwitterTestBase


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterApiClient(XAccountTwitterTestBase):
    """TwitterApiClient: transport, OAuth header delegation, status mapping."""

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
        cls.client = TwitterApiClient(cls.account)

    def _mock_response(self, status_code, json_data=None):
        response = MagicMock()
        response.status_code = status_code
        response.ok = status_code < 400
        response.content = b'{}' if status_code < 400 else b''
        response.json.return_value = json_data if json_data is not None else {}
        response.headers = {}
        return response

    # ------------------------------------------------------------ transport
    def test_request_delegates_oauth_headers(self):
        """The client must build the OAuth header via social_twitter's helper."""
        from odoo.addons.social_twitter.models.social_account import SocialAccount
        with patch.object(SocialAccount, '_get_twitter_oauth_header',
                          return_value={'Authorization': 'OAuth test'}) as hdr, \
             patch('requests.request', return_value=self._mock_response(200, {})) as mocked:
            self.client.request('POST', '/2/users/12345/retweets',
                                body={'tweet_id': '1'})
        hdr.assert_called_once()
        url = hdr.call_args.args[0]
        self.assertIn('/2/users/12345/retweets', url)
        self.assertEqual(mocked.call_args.kwargs['json'], {'tweet_id': '1'})

    def test_request_returns_json_on_2xx(self):
        with patch('requests.request', return_value=self._mock_response(
                200, {'data': {'retweeted': True}})):
            result = self.client.request('GET', '/2/users/me')
        self.assertEqual(result, {'data': {'retweeted': True}})

    def test_request_429_raises_rate_limit(self):
        with patch('requests.request', return_value=self._mock_response(429)):
            with self.assertRaises(twitter_errors.TwitterRateLimitError) as ctx:
                self.client.request('POST', '/2/users/12345/retweets',
                                    body={'tweet_id': '1'})
        self.assertEqual(ctx.exception.code, 'rate_limit')
        self.assertTrue(ctx.exception.retryable)

    def test_request_401_raises_authentication(self):
        with patch('requests.request', return_value=self._mock_response(
                401, {'detail': 'Unauthorized', 'title': 'Unauthorized'})):
            with self.assertRaises(twitter_errors.TwitterAuthenticationError) as ctx:
                self.client.request('GET', '/2/users/me')
        self.assertEqual(ctx.exception.code, 'authentication_failure')
        self.assertFalse(ctx.exception.retryable)

    def test_request_403_raises_permission(self):
        with patch('requests.request', return_value=self._mock_response(403)):
            with self.assertRaises(twitter_errors.TwitterPermissionError) as ctx:
                self.client.request('GET', '/2/users/me')
        self.assertEqual(ctx.exception.code, 'permission_denied')

    def test_request_404_raises_not_found(self):
        with patch('requests.request', return_value=self._mock_response(404)):
            with self.assertRaises(twitter_errors.TwitterNotFoundError) as ctx:
                self.client.request('GET', '/2/users/me')
        self.assertEqual(ctx.exception.code, 'not_found')

    def test_request_500_raises_temporary(self):
        with patch('requests.request', return_value=self._mock_response(500)):
            with self.assertRaises(twitter_errors.TwitterTemporaryError) as ctx:
                self.client.request('GET', '/2/users/me')
        self.assertEqual(ctx.exception.code, 'temporary_error')
        self.assertTrue(ctx.exception.retryable)

    def test_network_error_raises_temporary(self):
        import requests
        with patch('requests.request', side_effect=requests.RequestException('boom')):
            with self.assertRaises(twitter_errors.TwitterTemporaryError) as ctx:
                self.client.request('GET', '/2/users/me')
        self.assertTrue(ctx.exception.retryable)

    # -------------------------------------------------------------- hosts
    def test_chat_api_uses_api_x_com_host(self):
        """Chat conversations must go to https://api.x.com (official host)."""
        from odoo.addons.social_twitter.models.social_account import SocialAccount
        with patch.object(SocialAccount, '_get_twitter_oauth_header',
                          return_value={'Authorization': 'Bearer t'}) as hdr, \
             patch('requests.request', return_value=self._mock_response(200, {})) as mocked:
            self.client.request('GET', '/2/chat/conversations')
        self.assertEqual(mocked.call_args.args[1], 'https://api.x.com/2/chat/conversations')
        self.assertEqual(hdr.call_args.args[0], 'https://api.x.com/2/chat/conversations')

    def test_chat_events_api_uses_api_x_com_host(self):
        from odoo.addons.social_twitter.models.social_account import SocialAccount
        with patch.object(SocialAccount, '_get_twitter_oauth_header',
                          return_value={'Authorization': 'Bearer t'}), \
             patch('requests.request', return_value=self._mock_response(200, {})) as mocked:
            self.client.request('GET', '/2/chat/conversations/g1/events')
        self.assertEqual(mocked.call_args.args[1],
                         'https://api.x.com/2/chat/conversations/g1/events')

    def test_legacy_dm_events_uses_api_twitter_com_host(self):
        """Legacy DM endpoints stay on api.twitter.com (social_twitter family)."""
        from odoo.addons.social_twitter.models.social_account import SocialAccount
        with patch.object(SocialAccount, '_get_twitter_oauth_header',
                          return_value={'Authorization': 'Bearer t'}), \
             patch('requests.request', return_value=self._mock_response(200, {})) as mocked:
            self.client.request('GET', '/2/dm_events')
        self.assertEqual(mocked.call_args.args[1], 'https://api.twitter.com/2/dm_events')

    def test_legacy_users_me_uses_api_twitter_com_host(self):
        from odoo.addons.social_twitter.models.social_account import SocialAccount
        with patch.object(SocialAccount, '_get_twitter_oauth_header',
                          return_value={'Authorization': 'Bearer t'}), \
             patch('requests.request', return_value=self._mock_response(200, {})) as mocked:
            self.client.request('GET', '/2/users/me')
        self.assertEqual(mocked.call_args.args[1], 'https://api.twitter.com/2/users/me')

    # -------------------------------------------------------------- retry
    def test_retries_temporary_5xx_with_backoff(self):
        """A 503 should be retried with bounded backoff, then classified."""
        from odoo.addons.social_twitter.models.social_account import SocialAccount
        with patch.object(SocialAccount, '_get_twitter_oauth_header',
                          return_value={'Authorization': 'Bearer t'}), \
             patch.object(TwitterApiClient, '_sleep') as sleep, \
             patch('requests.request', side_effect=[
                 self._mock_response(503), self._mock_response(503),
                 self._mock_response(503), self._mock_response(503),
             ]) as mocked:
            with self.assertRaises(twitter_errors.TwitterTemporaryError):
                self.client.request('GET', '/2/chat/conversations')
        # DEFAULT_RETRIES=2 -> 3 attempts total.
        self.assertEqual(mocked.call_count, 3)
        # Exponential backoff: 2s then 4s.
        self.assertEqual(sleep.call_args_list[0].args[0], 2.0)
        self.assertEqual(sleep.call_args_list[1].args[0], 4.0)

    def test_retries_honor_retry_after(self):
        from odoo.addons.social_twitter.models.social_account import SocialAccount
        response = self._mock_response(503)
        response.headers = {'Retry-After': '5'}
        with patch.object(SocialAccount, '_get_twitter_oauth_header',
                          return_value={'Authorization': 'Bearer t'}), \
             patch.object(TwitterApiClient, '_sleep') as sleep, \
             patch('requests.request', side_effect=[
                 response, self._mock_response(200, {'data': {}})]) as mocked:
            result = self.client.request('GET', '/2/chat/conversations')
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(sleep.call_args_list[0].args[0], 5.0)
        self.assertEqual(result, {'data': {}})

    def test_no_retry_on_permanent_errors(self):
        """401/403/404 must not be retried."""
        from odoo.addons.social_twitter.models.social_account import SocialAccount
        for status in (401, 403, 404):
            with patch.object(SocialAccount, '_get_twitter_oauth_header',
                              return_value={'Authorization': 'Bearer t'}), \
                 patch.object(TwitterApiClient, '_sleep') as sleep, \
                 patch('requests.request', return_value=self._mock_response(status)) as mocked:
                with self.assertRaises(twitter_errors.TwitterError):
                    self.client.request('GET', '/2/chat/conversations')
            self.assertEqual(mocked.call_count, 1)
            sleep.assert_not_called()

    def test_retries_disabled_with_retries_zero(self):
        from odoo.addons.social_twitter.models.social_account import SocialAccount
        with patch.object(SocialAccount, '_get_twitter_oauth_header',
                          return_value={'Authorization': 'Bearer t'}), \
             patch.object(TwitterApiClient, '_sleep') as sleep, \
             patch('requests.request', return_value=self._mock_response(503)) as mocked:
            with self.assertRaises(twitter_errors.TwitterTemporaryError):
                self.client.request('GET', '/2/chat/conversations', retries=0)
        self.assertEqual(mocked.call_count, 1)
        sleep.assert_not_called()
