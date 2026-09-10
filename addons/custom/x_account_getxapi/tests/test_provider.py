from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.x_account.services.x_provider import XProviderRegistry
from odoo.addons.x_account.services.x_service import XService

from odoo.addons.x_account_getxapi.services.getxapi_client import GetXAPIClient
from odoo.addons.x_account_getxapi.services.getxapi_dm_service import GetXAPIDMService
from odoo.addons.x_account_getxapi.services.getxapi_provider import GetXAPIProvider

from .common import XAccountGetXAPITestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPIProvider(XAccountGetXAPITestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # x_provider is a compute backed by the ir.config_parameter default,
        # so XService.get_provider only resolves GetXAPI while the param is set.
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.provider', 'getxapi')
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


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestSyncChatNames(XAccountGetXAPITestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['social.account'].create({
            'name': 'GetXAPI Names Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'getxapi_names',
            'twitter_user_id': '12345',
            'x_provider': 'getxapi',
            'x_getxapi_auth_token': 'test_auth_token',
            'x_auth_method': 'session_cookie',
        })
        cls.provider = GetXAPIProvider(cls.env, cls.account)

    def _conv(self, conv_id, ctype='group', name='', participants=None):
        return {
            'conversation_id': conv_id,
            'type': ctype,
            'group': ctype == 'group',
            'name': name,
            'participants': participants if participants is not None else [
                {'id': '12345', 'userName': 'getxapi_names'},
                {'id': '999', 'userName': 'peer_user'},
            ],
            'cursor': '',
        }

    def test_sync_updates_existing_group_channel_name(self):
        channel = self.env['discuss.channel'].sudo()._get_x_channel(
            self.account, conversation_id='c1', channel_type='x_group',
            create_if_not_found=True)
        self.assertEqual(channel.name, 'c1')
        conversations = [self._conv('c1', name='Team Chat')]
        with patch.object(GetXAPIDMService, 'list', return_value={
                'conversations': conversations, 'cursor': ''}):
            result = self.provider.sync_chat_names(self.account)
        self.assertEqual(result['updated'], 1)
        self.assertEqual(result['missing'], 0)
        self.assertEqual(channel.name, 'Team Chat')

    def test_sync_skips_channels_that_do_not_exist(self):
        with patch.object(GetXAPIDMService, 'list', return_value={
                'conversations': [self._conv('ghost', name='Ghost Chat')],
                'cursor': ''}):
            result = self.provider.sync_chat_names(self.account)
        self.assertEqual(result['missing'], 1)
        self.assertEqual(result['updated'], 0)
        self.assertFalse(self.env['discuss.channel'].sudo().search_count([
            ('x_conversation_id', '=', 'ghost')]))

    def test_sync_one_to_one_uses_peer_handle(self):
        channel = self.env['discuss.channel'].sudo()._get_x_channel(
            self.account, conversation_id='c2', channel_type='x',
            create_if_not_found=True)
        with patch.object(GetXAPIDMService, 'list', return_value={
                'conversations': [self._conv('c2', ctype='one_to_one')],
                'cursor': ''}):
            result = self.provider.sync_chat_names(self.account)
        self.assertEqual(result['updated'], 1)
        self.assertEqual(channel.name, 'peer_user')

    def test_sync_counts_unchanged_and_paginates_cursor(self):
        ch = self.env['discuss.channel'].sudo()._get_x_channel(
            self.account, conversation_id='c3', channel_type='x_group',
            create_if_not_found=True)
        ch.write({'name': 'Team Chat'})
        with patch.object(GetXAPIDMService, 'list', side_effect=[
                {'conversations': [self._conv('c3', name='Team Chat')],
                 'cursor': 'next1'},
                {'conversations': [self._conv('c4', name='Other Chat')],
                 'cursor': ''},
        ]) as lst:
            result = self.provider.sync_chat_names(self.account)
        self.assertEqual(lst.call_args_list[1].kwargs.get('cursor'), 'next1')
        self.assertEqual(result['conversations'], 2)
        self.assertEqual(result['unchanged'], 1)
