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
        self.assertIn('dm.read', url)
        self.assertIn('dm.write', url)
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

    def test_invalid_refresh_token_classified_as_invalid_token(self):
        """A 400 from the token endpoint with an OAuth2 invalid_grant /
        invalid_request error must classify as TwitterInvalidTokenError, not a
        generic http_400 — so refresh callers know re-authorization is needed
        instead of hammering X."""
        for error_code in ('invalid_grant', 'invalid_request', 'invalid_client'):
            body = {'error': error_code,
                    'error_description': 'Value passed for the token was invalid.'}
            with patch('requests.post',
                       return_value=self._mock_response(400, body)):
                exc = None
                try:
                    self.client.refresh('dead-refresh-token')
                except twitter_errors.TwitterInvalidTokenError as caught:
                    exc = caught
                self.assertIsNotNone(exc, error_code)
                self.assertEqual(exc.code, 'invalid_token')
                self.assertIn(error_code, exc.message)


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

    def test_refresh_400_marks_account_reauth_required(self):
        account = self._make_account()
        account.write({
            'x_oauth2_token_expires_at': fields.Datetime.now() - timedelta(minutes=5)})
        # A 400 from the token endpoint (revoked/expired refresh token) is
        # classified as a generic non-retryable http_400.
        with patch.object(
                TwitterOAuth2Client, 'refresh',
                side_effect=twitter_errors.TwitterError('http_400', 'Invalid or expired refresh token')):
            token = account._x_oauth2_ensure_access_token()
        self.assertIsNone(token)
        self.assertEqual(account.x_connection_status, 'reauth_required')
        self.assertEqual(account.last_error, 'Invalid or expired refresh token')

    def test_invalid_token_marks_reauth_with_clear_message(self):
        """An invalid_grant/invalid_request response surfaces a UI-friendly
        're-authorization required' reason instead of a generic HTTP 400."""
        account = self._make_account()
        account.write({
            'x_oauth2_token_expires_at': fields.Datetime.now() - timedelta(minutes=5)})
        with patch.object(
                TwitterOAuth2Client, 'refresh',
                side_effect=twitter_errors.TwitterInvalidTokenError(
                    'invalid_grant: Value passed for the token was invalid.')):
            token = account._x_oauth2_ensure_access_token()
        self.assertIsNone(token)
        self.assertEqual(account.x_connection_status, 'reauth_required')
        self.assertIn('re-authorization required', account.last_error)
        self.assertIn('invalid_grant', account.last_error)

    def test_reauth_required_short_circuits_no_refresh_retry(self):
        """Once an account is reauth_required (dead refresh token), subsequent
        ensure-access-token calls must NOT re-contact X's token endpoint until
        the account is re-authorized."""
        account = self.env['social.account'].create({
            'name': 'Reauth No-Retry',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'reauth2',
            'twitter_user_id': '7777',
            'x_oauth2_access_token': 'stale',
            'x_oauth2_refresh_token': 'dead-rt',
            'x_oauth2_token_expires_at': fields.Datetime.now() - timedelta(minutes=5),
            'x_connection_status': 'reauth_required',
            'last_error': 'dead',
        })
        with patch.object(TwitterOAuth2Client, 'refresh') as mocked:
            token = account._x_oauth2_ensure_access_token()
        self.assertIsNone(token)
        mocked.assert_not_called()

    def test_relink_restores_active_and_refresh(self):
        """A successful OAuth relink (fresh token exchange via the callback)
        resets the account to active and refresh-token rotation works again."""
        account = self.env['social.account'].create({
            'name': 'Relink Recovers',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'relink_covers',
            'twitter_user_id': '7778',
            'x_oauth2_access_token': 'stale-at',
            'x_oauth2_refresh_token': 'dead-rt',
            'x_oauth2_token_expires_at': fields.Datetime.now() - timedelta(minutes=5),
            'x_connection_status': 'reauth_required',
            'last_error': 'invalid_grant: dead',
        })
        # Re-authorization through the OAuth 2.0 callback.
        relinked = self.env['social.account']._create_or_update_twitter_oauth2(
            self.twitter_media,
            {'id': '7778', 'name': 'Relink Recovers', 'username': 'relink_cookie'},
            {'access_token': 'fresh-at', 'refresh_token': 'fresh-rt'},
            7200,
        )
        self.assertEqual(relinked.id, account.id)
        self.assertEqual(relinked.x_connection_status, 'active')
        self.assertFalse(relinked.last_error)
        # The refreshed account can now rotate tokens again.
        relinked.write({'x_oauth2_token_expires_at':
                        fields.Datetime.now() - timedelta(minutes=5)})
        with patch.object(
                TwitterOAuth2Client, 'refresh',
                return_value={'access_token': 'rotated-at',
                              'refresh_token': 'rotated-rt',
                              'expires_in': 7200}) as mocked:
            token = relinked._x_oauth2_ensure_access_token()
        self.assertEqual(token, 'rotated-at')
        mocked.assert_called_once()
        relinked.invalidate_recordset()
        self.assertEqual(relinked.x_oauth2_refresh_token, 'rotated-rt')
        self.assertEqual(relinked.x_connection_status, 'active')

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
        # The callback just proved the credentials work (token exchange +
        # /users/me), so the account must be live — not left on the 'new'
        # default that nothing later promotes for OAuth2 accounts.
        self.assertEqual(account.x_connection_status, 'active')
        self.assertTrue(account.last_connected)

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

    def test_create_or_update_reactivates_stale_existing(self):
        """Re-linking an account stuck in 'new'/'reauth_required' must flip it
        back to 'active' — the callback proves the fresh credentials work."""
        stale = self.env['social.account'].create({
            'name': 'Stale User',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'stale_user',
            'twitter_user_id': '1000',
            'x_oauth2_access_token': 'old-at',
            'x_oauth2_refresh_token': 'old-rt',
            'x_connection_status': 'reauth_required',
            'last_error': 'http_400',
        })
        relinked = self.env['social.account']._create_or_update_twitter_oauth2(
            self.twitter_media,
            {'id': '1000', 'name': 'Stale User', 'username': 'stale_user'},
            {'access_token': 'fresh-at', 'refresh_token': 'fresh-rt'},
            7200,
        )
        self.assertEqual(relinked.id, stale.id)
        self.assertEqual(relinked.x_connection_status, 'active')
        self.assertFalse(relinked.last_error)

    def test_relink_reuses_canonical_medium_when_orphan_exists(self):
        """Re-linking an account whose canonical utm.medium still exists
        (orphaned by a previously deleted account) must not crash with 'The
        name must be unique' nor grow a new suffixed medium. It reuses the
        canonical '[X] name' medium instead."""
        # Simulate a previously-deleted account that left its medium behind.
        orphan_medium_name = '[%s] %s' % (
            self.twitter_media.name, 'Relink Medium User')
        self.env['utm.medium'].sudo().create({'name': orphan_medium_name})
        account = self.env['social.account'].create({
            'name': 'Relink Medium User',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'relink_medium_user',
            'twitter_user_id': '2001',
            'x_oauth2_access_token': 'at',
            'x_oauth2_refresh_token': 'rt',
            'x_oauth2_token_expires_at': fields.Datetime.now() + timedelta(hours=1),
        })
        # The create must have reused the exact canonical medium (no '[2]').
        self.assertEqual(account.utm_medium_id.name, orphan_medium_name)
        medium_count_after_create = self.env['utm.medium'].sudo().search_count(
            [('name', '=', orphan_medium_name)])
        self.assertEqual(medium_count_after_create, 1)

        relinked = self.env['social.account']._create_or_update_twitter_oauth2(
            self.twitter_media,
            {'id': '2001', 'name': 'Relink Medium User',
             'username': 'relink_medium_user'},
            {'access_token': 'fresh-at', 'refresh_token': 'fresh-rt'},
            7200,
        )
        self.assertEqual(relinked.id, account.id)
        # No duplicate medium was created during the relink write either.
        medium_count_after_relink = self.env['utm.medium'].sudo().search_count(
            [('name', '=', orphan_medium_name)])
        self.assertEqual(medium_count_after_relink, 1)

    def test_create_and_write_do_not_create_duplicate_medium(self):
        """Creating then re-writing the same account name keeps a single
        canonical utm.medium (no '[2]', '[3]', ... duplicates)."""
        account = self.env['social.account'].create({
            'name': 'Dedupe Medium User',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'dedupe_user',
            'twitter_user_id': '2002',
            'x_oauth2_access_token': 'at',
            'x_oauth2_refresh_token': 'rt',
            'x_oauth2_token_expires_at': fields.Datetime.now() + timedelta(hours=1),
        })
        account.write({'name': 'Dedupe Medium User'})
        mediums = self.env['utm.medium'].sudo().search([
            ('name', 'like', '[%s] Dedupe Medium User%%' % self.twitter_media.name),
        ])
        self.assertEqual(len(mediums), 1)
        self.assertEqual(mediums.name, '[%s] Dedupe Medium User' % self.twitter_media.name)


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