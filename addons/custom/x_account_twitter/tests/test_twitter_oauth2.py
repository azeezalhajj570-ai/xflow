from datetime import timedelta
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.x_account_twitter.services import twitter_errors
from odoo.addons.x_account_twitter.services.twitter_api_client import TwitterApiClient
from odoo.addons.x_account_twitter.services.twitter_oauth2 import TwitterOAuth2Client

from .common import XAccountTwitterTestBase


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterOAuth2Linking(XAccountTwitterTestBase):
    """Link Account -> OAuth 2.0 authorize route."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')

    def test_action_add_account_routes_to_oauth2(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'x_account.auth_method', 'oauth2')
        action = self.twitter_media._action_add_account()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertIn('/x_account/twitter/oauth2/authorize', action['url'])

    def test_action_add_account_routes_legacy_oauth1_to_oauth2(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'x_account.auth_method', 'oauth1')
        action = self.twitter_media._action_add_account()
        self.assertIn('/x_account/twitter/oauth2/authorize', action['url'])


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterOAuth2Client(XAccountTwitterTestBase):
    """TwitterOAuth2Client: PKCE authorize URL + token/user plumbing."""

    def setUp(self):
        super().setUp()
        self.client = TwitterOAuth2Client(
            'client-id', 'client-secret', 'https://odoo.test/x_account/twitter/oauth2/callback')

    def _mock_response(self, status_code, json_data=None):
        response = MagicMock()
        response.status_code = status_code
        response.ok = status_code < 400
        response.json.return_value = json_data if json_data is not None else {}
        return response

    def test_authorize_url_has_pkce_params(self):
        url = self.client.build_authorize_url('state-123', 'verifier-123')
        self.assertIn('response_type=code', url)
        self.assertIn('client_id=client-id', url)
        self.assertIn('code_challenge_method=S256', url)
        self.assertIn('code_challenge=', url)
        self.assertIn('scope=', url)
        self.assertIn('offline.access', url)
        self.assertTrue(url.startswith('https://twitter.com/i/oauth2/authorize'))

    def test_pkce_challenge_is_sha256_base64url(self):
        challenge = TwitterOAuth2Client._code_challenge('verifier-123')
        self.assertTrue(challenge)
        self.assertNotIn('=', challenge)

    def test_exchange_code_sends_form_and_basic_auth(self):
        with patch('requests.post', return_value=self._mock_response(200, {
            'access_token': 'at', 'refresh_token': 'rt', 'expires_in': 7200,
        })) as mocked:
            tokens = self.client.exchange_code('code-1', 'verifier-123')
        self.assertEqual(tokens['access_token'], 'at')
        kwargs = mocked.call_args
        self.assertEqual(kwargs.args[0], TwitterOAuth2Client.TOKEN_URL)
        data = kwargs.kwargs.get('data')
        self.assertEqual(data['grant_type'], 'authorization_code')
        self.assertEqual(data['code'], 'code-1')
        self.assertEqual(data['code_verifier'], 'verifier-123')
        self.assertEqual(data['client_id'], 'client-id')
        auth = kwargs.kwargs['headers'].get('Authorization', '')
        self.assertTrue(auth.startswith('Basic '))

    def test_refresh_uses_refresh_token_grant(self):
        with patch('requests.post', return_value=self._mock_response(200, {
            'access_token': 'new-at', 'refresh_token': 'new-rt', 'expires_in': 7200,
        })) as mocked:
            tokens = self.client.refresh('rt')
        self.assertEqual(tokens['access_token'], 'new-at')
        data = mocked.call_args.kwargs['data']
        self.assertEqual(data['grant_type'], 'refresh_token')
        self.assertEqual(data['refresh_token'], 'rt')

    def test_get_me_uses_bearer(self):
        with patch('requests.get', return_value=self._mock_response(200, {
            'data': {'id': '12345', 'name': 'User', 'username': 'user'}})) as mocked:
            user = self.client.get_me('at')
        self.assertEqual(user['id'], '12345')
        self.assertEqual(
            mocked.call_args.kwargs['headers']['Authorization'], 'Bearer at')

    def test_token_error_classified(self):
        with patch('requests.post', return_value=self._mock_response(401, {
            'detail': 'Unauthorized', 'title': 'Unauthorized'})):
            with self.assertRaises(twitter_errors.TwitterAuthenticationError):
                self.client.exchange_code('code-1', 'verifier-123')


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterOAuth2Account(XAccountTwitterTestBase):
    """social.account OAuth 2.0 token storage, Bearer header + linking."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_oauth2_client_id', 'test-client-id')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_oauth2_client_secret', 'test-client-secret')

    def _make_account(self):
        return self.env['social.account'].create({
            'name': 'OAuth2 Account',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'oauth2_user',
            'twitter_user_id': '777',
            'x_oauth2_access_token': 'at',
            'x_oauth2_refresh_token': 'rt',
            'x_oauth2_token_expires_at': fields.Datetime.now() + timedelta(hours=1),
        })

    def test_oauth2_account_gets_twitter_provider(self):
        account = self._make_account()
        self.assertEqual(account.x_provider, 'twitter')
        self.assertEqual(account.x_auth_method, 'oauth2')

    def test_header_is_bearer_when_oauth2_tokens_present(self):
        account = self._make_account()
        headers = account._get_twitter_oauth_header(
            'https://api.twitter.com/2/users/me', method='GET')
        self.assertEqual(headers['Authorization'], 'Bearer at')

    def test_header_falls_back_to_oauth1_when_no_oauth2(self):
        from odoo.addons.social_twitter.models.social_account import (
            SocialAccount as TwitterSocialAccount)
        account = self.env['social.account'].create({
            'name': 'OAuth1 Account',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'oauth1_user',
            'twitter_user_id': '111',
            'twitter_oauth_token': 't1',
            'twitter_oauth_token_secret': 's1',
            'x_provider': 'twitter',
            'x_auth_method': 'oauth1',
        })
        with patch.object(TwitterSocialAccount, '_get_twitter_oauth_header',
                          return_value={'Authorization': 'OAuth legacy'}) as base:
            headers = account._get_twitter_oauth_header(
                'https://api.twitter.com/2/users/me', method='GET')
        self.assertEqual(headers['Authorization'], 'OAuth legacy')
        base.assert_called_once()

    def test_expired_token_refreshes(self):
        account = self._make_account()
        account.write({
            'x_oauth2_token_expires_at': fields.Datetime.now() - timedelta(minutes=5)})
        with patch.object(TwitterOAuth2Client, 'refresh', return_value={
            'access_token': 'new-at',
            'refresh_token': 'new-rt',
            'expires_in': 7200,
        }) as mocked:
            token = account._x_oauth2_ensure_access_token()
        self.assertEqual(token, 'new-at')
        ref = mocked.call_args.args[0]
        self.assertEqual(ref, 'rt')
        account.invalidate_recordset()
        self.assertEqual(account.x_oauth2_access_token, 'new-at')
        self.assertEqual(account.x_oauth2_refresh_token, 'new-rt')

    def test_refresh_failure_raises_authentication(self):
        account = self._make_account()
        account.write({
            'x_oauth2_token_expires_at': fields.Datetime.now() - timedelta(minutes=5)})
        with patch.object(TwitterOAuth2Client, 'refresh', side_effect=
                          twitter_errors.TwitterAuthenticationError('nope')):
            with self.assertRaises(twitter_errors.TwitterAuthenticationError):
                account._x_oauth2_ensure_access_token()

    def test_create_or_update_creates_oauth2_account(self):
        account = self.env['social.account']._create_or_update_twitter_oauth2(
            self.twitter_media,
            {'id': '888', 'name': 'New User', 'username': 'new_user'},
            {'access_token': 'at2', 'refresh_token': 'rt2'},
            7200,
        )
        self.assertEqual(account.social_account_handle, 'new_user')
        self.assertEqual(account.twitter_user_id, '888')
        self.assertEqual(account.x_oauth2_access_token, 'at2')
        self.assertEqual(account.x_provider, 'twitter')
        self.assertEqual(account.x_auth_method, 'oauth2')

    def test_create_or_update_updates_existing(self):
        created = self.env['social.account']._create_or_update_twitter_oauth2(
            self.twitter_media,
            {'id': '999', 'name': 'Same User', 'username': 'same_user'},
            {'access_token': 'at-old', 'refresh_token': 'rt-old'},
            7200,
        )
        updated = self.env['social.account']._create_or_update_twitter_oauth2(
            self.twitter_media,
            {'id': '999', 'name': 'Same User', 'username': 'same_user'},
            {'access_token': 'at-new', 'refresh_token': 'rt-new'},
            7200,
        )
        self.assertEqual(created.id, updated.id)
        self.assertEqual(updated.x_oauth2_access_token, 'at-new')
        count = self.env['social.account'].search_count([
            ('twitter_user_id', '=', '999')])
        self.assertEqual(count, 1)


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterOAuth2ApiClient(XAccountTwitterTestBase):
    """TwitterApiClient: bearer transport + 401 refresh-retry."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_oauth2_client_id', 'test-client-id')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_oauth2_client_secret', 'test-client-secret')
        cls.account = cls.env['social.account'].create({
            'name': 'OAuth2 API Client',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'api_user',
            'twitter_user_id': '555',
            'x_oauth2_access_token': 'at',
            'x_oauth2_refresh_token': 'rt',
            'x_oauth2_token_expires_at': fields.Datetime.now() + timedelta(hours=1),
        })
        cls.client = TwitterApiClient(cls.account)

    def _mock_response(self, status_code, json_data=None):
        response = MagicMock()
        response.status_code = status_code
        response.ok = status_code < 400
        response.content = b'{}' if status_code < 400 else b''
        response.json.return_value = json_data if json_data is not None else {}
        return response

    def test_request_uses_bearer_header(self):
        with patch('requests.request', return_value=self._mock_response(
                200, {'data': {'retweeted': True}})) as mocked:
            result = self.client.request('GET', '/2/users/me')
        self.assertEqual(result, {'data': {'retweeted': True}})
        auth = mocked.call_args.kwargs['headers']['Authorization']
        self.assertEqual(auth, 'Bearer at')

    def test_401_triggers_refresh_and_retries_once(self):
        responses = iter([
            self._mock_response(401, {'detail': 'Unauthorized'}),
            self._mock_response(200, {'data': {'retweeted': True}}),
        ])
        with patch('requests.request', side_effect=lambda *a, **k: next(responses)) as mocked, \
                patch.object(type(self.account), '_x_oauth2_force_refresh',
                             return_value='new-at') as refresh:
            result = self.client.request('POST', '/2/users/555/retweets',
                                         body={'tweet_id': '1'})
        self.assertEqual(result['data']['retweeted'], True)
        self.assertEqual(refresh.call_count, 1)
        self.assertEqual(mocked.call_count, 2)


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterOAuth2Provider(XAccountTwitterTestBase):
    """TwitterProvider.validate_session with OAuth 2.0 credentials."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_oauth2_client_id', 'test-client-id')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_oauth2_client_secret', 'test-client-secret')
        cls.account = cls.env['social.account'].create({
            'name': 'OAuth2 Provider',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'provider_user',
            'twitter_user_id': '444',
            'x_oauth2_access_token': 'at',
            'x_oauth2_refresh_token': 'rt',
            'x_oauth2_token_expires_at': fields.Datetime.now() + timedelta(hours=1),
        })

    def test_validate_session_ok(self):
        from odoo.addons.x_account_twitter.services.twitter_provider import (
            TwitterProvider)
        provider = TwitterProvider(self.env, self.account)
        with patch.object(TwitterApiClient, 'request', return_value={
            'data': {'id': '444', 'username': 'provider_user', 'name': 'OAuth2'},
        }) as req:
            result = provider.validate_session()
        self.assertTrue(result['valid'])
        self.assertEqual(result['user']['id'], '444')
        self.assertEqual(req.call_args.args[1], '/2/users/me')

    def test_validate_session_missing_credentials(self):
        from odoo.addons.x_account_twitter.services.twitter_provider import (
            TwitterProvider)
        account = self.account.with_context(todo='no tokens')
        account.write({'x_oauth2_access_token': False,
                       'x_oauth2_refresh_token': False})
        try:
            provider = TwitterProvider(self.env, account)
            result = provider.validate_session()
            self.assertFalse(result['valid'])
            self.assertEqual(result['reason'], 'twitter_oauth_token_missing')
        finally:
            account.write({'x_oauth2_access_token': 'at',
                           'x_oauth2_refresh_token': 'rt'})