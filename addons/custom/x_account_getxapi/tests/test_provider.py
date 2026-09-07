from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.x_account.services.x_provider import XProviderRegistry
from odoo.addons.x_account.services.x_service import XService

from odoo.addons.x_account_getxapi.services.getxapi_client import GetXAPIClient
from odoo.addons.x_account_getxapi.services.getxapi_provider import GetXAPIProvider

from .common import XAccountGetXAPITestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPIProvider(XAccountGetXAPITestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['social.account'].create({
            'name': 'GetXAPI Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'getxapi_user',
            'twitter_user_id': '12345',
            'x_provider': 'getxapi',
            'x_auth_method': 'session_cookie',
        })
        cls.provider = GetXAPIProvider(cls.env, cls.account)

    def test_registry_resolves_getxapi(self):
        self.assertIs(XProviderRegistry.resolve('getxapi'), GetXAPIProvider)

    def test_get_provider_dispatches_getxapi(self):
        provider = XService.get_provider(self.account)
        self.assertIsInstance(provider, GetXAPIProvider)

    def test_validate_session_ok(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {'rest_id': '12345', 'legacy': {'screen_name': 'getxapi_user', 'name': 'GetXAPI User'}},
        }):
            result = self.provider.validate_session()
        self.assertTrue(result['valid'])
        self.assertEqual(result['reason'], 'getxapi')
        self.assertEqual(result['user']['id'], '12345')

    def test_validate_session_missing_api_key(self):
        with patch.object(self.provider, '_api_key', ''):
            result = self.provider.validate_session()
        self.assertFalse(result['valid'])
        self.assertEqual(result['reason'], 'getxapi_api_key_missing')

    def test_validate_session_no_handle(self):
        self.account.write({'social_account_handle': False})
        result = self.provider.validate_session()
        self.assertFalse(result['valid'])
        self.assertIn('handle', result['reason'].lower())

    def test_validate_session_network_error(self):
        with patch.object(GetXAPIClient, 'get',
                          side_effect=Exception('network_error: boom')):
            result = self.provider.validate_session()
        self.assertFalse(result['valid'])
        self.assertIn('boom', result['reason'])

    def test_like(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {'result': {'favorited': True}},
        }):
            result = self.provider.like({'post_id': '111'})
        self.assertTrue(result['success'])

    def test_repost(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {'result': {'retweeted': True}},
        }):
            result = self.provider.repost({'post_id': '111'})
        self.assertTrue(result['success'])

    def test_follow(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {'result': {'following': True}},
        }):
            result = self.provider.follow(screen_name='someuser')
        self.assertTrue(result['success'])

    def test_post_tweet(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {'result': {'rest_id': '222'}},
        }):
            result = self.provider.post_tweet('hello world')
        self.assertTrue(result['success'])
        self.assertEqual(result['tweet_id'], '222')

    def test_send_dm(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {'message_id': 'dm-1', 'created_at': '2026-01-01'},
        }):
            result = self.provider.send_dm('9', 'hello')
        self.assertEqual(result['message_id'], 'dm-1')

    def test_supported_operations(self):
        ops = self.provider.supported_operations()
        self.assertIn('validate_session', ops)
        self.assertIn('like', ops)
        self.assertIn('repost', ops)
        self.assertIn('follow', ops)
        self.assertIn('post_tweet', ops)
        self.assertIn('send_dm', ops)

    def test_validate_via_xservice(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {'rest_id': '12345', 'legacy': {'screen_name': 'getxapi_user', 'name': 'GetXAPI User'}},
        }):
            result = XService.validate(self.account)
        self.assertTrue(result['valid'])
        self.assertEqual(self.account.x_connection_status, 'active')
