from unittest.mock import patch

from odoo.tests import tagged

from .common import XAccountTwitterTestBase


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterLinkAccount(XAccountTwitterTestBase):
    """Account linking must reuse social_twitter's official OAuth flow."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        # minimal social_twitter consumer config so create()/stats don't hit IAP
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_consumer_key', 'test-consumer-key')
        cls.env['ir.config_parameter'].sudo().set_param(
            'social.twitter_consumer_secret_key', 'test-consumer-secret')

    # ------------------------------------------------ _action_add_account routing
    def test_action_add_account_oauth1_routes_to_oauth2_authorize(self):
        """With auth method oauth1 (legacy) or oauth2, Link Account must open the
        OAuth 2.0 PKCE authorize URL. X disabled OAuth 1.0a for Free-tier/new
        apps, so the stock social_twitter request-token flow is bypassed."""
        for auth_method in ('oauth1', 'oauth2'):
            with self.subTest(auth_method=auth_method):
                self.env['ir.config_parameter'].sudo().set_param(
                    'x_account.auth_method', auth_method)
                # The override short-circuits before the stock OAuth 1.0a flow.
                with patch.object(
                        type(self.twitter_media),
                        '_add_twitter_accounts_from_configuration',
                        return_value=None) as mocked:
                    result = self.twitter_media._action_add_account()
                self.assertEqual(result['type'], 'ir.actions.act_url')
                self.assertEqual(result['url'], '/x_account/twitter/oauth2/authorize')
                self.assertEqual(result['target'], 'self')
                mocked.assert_not_called()

    def test_action_add_account_session_cookie_keeps_x_account_wizard(self):
        """Default (session_cookie) keeps x_account's import-session wizard."""
        self.env['ir.config_parameter'].sudo().set_param(
            'x_account.auth_method', 'session_cookie')
        # The override must fall through to x_account's branch (session wizard).
        result = self.twitter_media._action_add_account()
        self.assertEqual(result['res_model'], 'x.import.session')

    # ------------------------------------------------ create() provider assignment
    def test_create_assigns_twitter_provider_for_oauth_account(self):
        """An account created by the OAuth callback (tokens present, no explicit
        x_provider) is auto-assigned the 'twitter' provider."""
        account = self.env['social.account'].create({
            'name': 'OAuth Twitter',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'oauth_user',
            'twitter_user_id': '999',
            'twitter_oauth_token': 'token',
            'twitter_oauth_token_secret': 'secret',
        })
        self.assertEqual(account.x_provider, 'twitter')
        self.assertEqual(account.x_auth_method, 'oauth1')

    def test_create_preserves_explicit_provider(self):
        """Session imports / explicit provider assignments are untouched."""
        account = self.env['social.account'].create({
            'name': 'Session Twitter',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'sess_user',
            'x_provider': 'session_web',
        })
        self.assertEqual(account.x_provider, 'session_web')

    # ------------------------------------------------ _skip_oauth_stats
    def test_oauth_account_with_tokens_keeps_stats(self):
        """A real OAuth-linked twitter account (tokens) must NOT be skipped."""
        account = self.env['social.account'].create({
            'name': 'OAuth Twitter',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'oauth_user',
            'twitter_user_id': '999',
            'twitter_oauth_token': 'token',
            'twitter_oauth_token_secret': 'secret',
            'x_provider': 'twitter',
        })
        self.assertNotIn(account, account._skip_oauth_stats())

    def test_twitter_provider_without_tokens_skips_stats(self):
        """A 'twitter'-provider account without tokens must skip OAuth stats
        (avoids slow IAP signing)."""
        account = self.env['social.account'].create({
            'name': 'Broken Twitter',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'broken_user',
            'x_provider': 'twitter',
        })
        # _skip_oauth_stats filters the recordset it is called on (like the
        # parent implementation) — pass a set containing the account.
        self.assertIn(account, account._skip_oauth_stats())
