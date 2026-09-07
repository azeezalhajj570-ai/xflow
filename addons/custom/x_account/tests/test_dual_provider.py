from unittest.mock import patch, MagicMock

from odoo.tests import tagged
from odoo.exceptions import UserError
from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestDualProviderArchitecture(XAccountTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Test Account',
            'media_id': cls.twitter_media.id,
            'x_provider': 'session_web',
        })

    def test_event_provider_field_exists(self):
        self.assertTrue(hasattr(self.account, 'x_event_provider'))

    def test_action_provider_field_exists(self):
        self.assertTrue(hasattr(self.account, 'x_action_provider'))

    def test_get_event_provider_fallback_to_x_provider(self):
        """When x_event_provider is not set, fall back to x_provider."""
        self.account.write({'x_provider': 'session_web', 'x_event_provider': False})
        provider = self.account.get_event_provider()
        self.assertEqual(type(provider).__name__, 'SessionWebProvider')

    def test_get_action_provider_fallback_to_x_provider(self):
        """When x_action_provider is not set, fall back to x_provider."""
        self.account.write({'x_provider': 'session_web', 'x_action_provider': False})
        provider = self.account.get_action_provider()
        self.assertEqual(type(provider).__name__, 'SessionWebProvider')

    def test_get_event_provider_official_requires_credentials(self):
        """Setting x_event_provider='official' without OAuth tokens raises."""
        self.account.write({
            'x_provider': 'session_web',
            'x_event_provider': 'official',
        })
        has_oauth = hasattr(self.account, 'x_oauth2_access_token')
        has_oauth1 = hasattr(self.account, 'twitter_oauth_token')
        if has_oauth:
            self.account.write({'x_oauth2_access_token': False})
        if has_oauth1:
            self.account.write({'twitter_oauth_token': False})
        with self.assertRaises(UserError):
            self.account.get_event_provider()

    def test_get_action_provider_getxapi_requires_api_key(self):
        """Setting x_action_provider='getxapi' without API key raises."""
        self.account.write({
            'x_provider': 'session_web',
            'x_action_provider': 'getxapi',
        })
        with patch.object(type(self.env['ir.config_parameter']), 'get_param',
                         return_value=False):
            with self.assertRaises(UserError):
                self.account.get_action_provider()

    def test_get_provider_for_operation_event_ops(self):
        """Event operations route to event provider."""
        self.account.write({
            'x_provider': 'session_web',
            'x_event_provider': False,
        })
        for op in ('process_webhook_event', 'register_webhook',
                   'unsubscribe_all_events'):
            provider = self.account.get_provider_for_operation(op)
            self.assertEqual(type(provider).__name__, 'SessionWebProvider')

    def test_get_provider_for_operation_action_ops(self):
        """Action operations route to action provider."""
        self.account.write({
            'x_provider': 'session_web',
            'x_action_provider': False,
        })
        for op in ('like', 'comment', 'repost', 'follow', 'post_tweet', 'send_dm'):
            provider = self.account.get_provider_for_operation(op)
            self.assertEqual(type(provider).__name__, 'SessionWebProvider')

    def test_get_provider_for_operation_unknown_fallback(self):
        """Unknown operations fall back to x_provider."""
        self.account.write({'x_provider': 'session_web'})
        provider = self.account.get_provider_for_operation('unknown_op')
        self.assertEqual(type(provider).__name__, 'SessionWebProvider')

    def test_backward_compatibility_no_new_fields(self):
        """Accounts without new fields work as before."""
        self.account.write({
            'x_provider': 'session_web',
            'x_event_provider': False,
            'x_action_provider': False,
        })
        event_provider = self.account.get_event_provider()
        action_provider = self.account.get_action_provider()
        self.assertEqual(type(event_provider).__name__, 'SessionWebProvider')
        self.assertEqual(type(action_provider).__name__, 'SessionWebProvider')

    def test_resolve_provider_code_mapping(self):
        """_resolve_provider_code maps semantic names to registry codes."""
        self.account.write({'x_provider': 'session_web'})
        code = self.account._resolve_provider_code('official', {'official': 'twitter'})
        self.assertEqual(code, 'twitter')

    def test_resolve_provider_code_passthrough(self):
        """_resolve_provider_code passes through unknown codes."""
        self.account.write({'x_provider': 'session_web'})
        code = self.account._resolve_provider_code('unknown', {'official': 'twitter'})
        self.assertEqual(code, 'unknown')

    def test_resolve_provider_code_fallback(self):
        """_resolve_provider_code falls back to x_provider when field is empty."""
        self.account.write({'x_provider': 'session_web'})
        code = self.account._resolve_provider_code(False, {'official': 'twitter'})
        self.assertEqual(code, 'session_web')

    def test_task_queue_routes_action_operations(self):
        """Task queue routes action operations to action provider."""
        self.account.write({
            'x_provider': 'session_web',
            'x_action_provider': False,
        })
        task = self.env['x.account.task'].create({
            'account_id': self.account.id,
            'operation': 'like',
        })
        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.like',
                   return_value={'success': True}) as mock_like:
            task._execute_operation()
        mock_like.assert_called_once()

    def test_task_queue_routes_event_operations(self):
        """Task queue routes event operations to event provider."""
        self.account.write({
            'x_provider': 'session_web',
            'x_event_provider': False,
        })
        task = self.env['x.account.task'].create({
            'account_id': self.account.id,
            'operation': 'get_conversations',
        })
        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                   return_value={'conversations': []}) as mock_get:
            task._execute_operation()
        mock_get.assert_called_once()
