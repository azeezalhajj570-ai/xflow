from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.x_account.services.x_provider import XProviderRegistry
from odoo.addons.x_account.services.x_service import XService

from odoo.addons.x_account_getxapi.services.getxapi_client import GetXAPIClient
from odoo.addons.x_account_getxapi.services.getxapi_dm_service import GetXAPIDMService
from odoo.addons.x_account_getxapi.services.getxapi_errors import (
    GetXAPIError,
    GetXAPIPreflightError,
)
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
            'x_getxapi_auth_token': 'test_auth_token',
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
        }) as mocked:
            result = self.provider.follow(screen_name='someuser')
        self.assertTrue(result['success'])
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['username'], 'someuser')
        self.assertEqual(body['auth_token'], 'test_auth_token')

    def test_follow_requires_target(self):
        with self.assertRaises(ValueError):
            self.provider.follow()

    def test_follow_without_auth_token_raises_preflight(self):
        """A missing auth token must fail fast, not spend a paid follow call."""
        with patch.object(self.provider, '_auth_token', ''):
            with patch.object(GetXAPIClient, 'post') as mocked:
                with self.assertRaises(GetXAPIPreflightError) as ctx:
                    self.provider.follow(screen_name='someuser')
        self.assertEqual(ctx.exception.code, 'preflight_failed')
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(mocked.call_count, 0)

    def test_follow_self_handle_raises_preflight(self):
        with patch.object(GetXAPIClient, 'post') as mocked:
            with self.assertRaises(GetXAPIPreflightError) as ctx:
                self.provider.follow(screen_name='@getxapi_user')
        self.assertIn('cannot_follow_self', ctx.exception.message)
        self.assertEqual(mocked.call_count, 0)

    def test_follow_self_user_id_raises_before_paid_lookup(self):
        with patch.object(GetXAPIClient, 'get') as mocked:
            with self.assertRaises(GetXAPIPreflightError) as ctx:
                self.provider.follow(target_user_id='12345')
        self.assertIn('cannot_follow_self', ctx.exception.message)
        self.assertEqual(mocked.call_count, 0)

    def test_write_without_user_id_raises_preflight(self):
        self.account.write({'twitter_user_id': False})
        try:
            with patch.object(GetXAPIClient, 'post') as mocked:
                with self.assertRaises(GetXAPIPreflightError) as ctx:
                    self.provider.like({'post_id': '111'})
            self.assertIn('missing_twitter_user_id', ctx.exception.message)
            self.assertEqual(mocked.call_count, 0)
        finally:
            self.account.write({'twitter_user_id': '12345'})

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
        }) as mocked:
            result = self.provider.send_dm('9', 'hello')
        self.assertEqual(result['message_id'], 'dm-1')
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['auth_token'], 'test_auth_token')

    def test_send_dm_requires_auth_token(self):
        with patch.object(self.provider, '_auth_token', ''):
            with patch.object(GetXAPIClient, 'post') as mocked:
                with self.assertRaises(GetXAPIError):
                    self.provider.send_dm('9', 'hello')
        self.assertEqual(mocked.call_count, 0)

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

    def test_credit_blocked_account_tasks_not_claimed(self):
        task = self.env['x.account.task'].create({
            'account_id': self.account.id,
            'operation': 'like',
            'task_context': '{"post_id": "1"}',
        })
        self.account.write({'x_getxapi_credit_blocked': True})
        with patch.object(GetXAPIProvider, 'like') as mocked:
            claimed = self.env['x.account.task']._claim_and_run(limit=10)
        task.invalidate_recordset()
        self.assertFalse(claimed)
        self.assertEqual(task.status, 'pending')
        self.assertEqual(mocked.call_count, 0)

    def test_claimed_task_released_when_account_blocked(self):
        """A task claimed just as the breaker trips is released, not run."""
        task = self.env['x.account.task'].create({
            'account_id': self.account.id,
            'operation': 'like',
            'task_context': '{"post_id": "1"}',
        })
        self.account.write({'x_getxapi_credit_blocked': True})
        with patch.object(GetXAPIProvider, 'like') as mocked:
            task._execute_operation()
        task.invalidate_recordset()
        self.assertEqual(task.status, 'pending')
        self.assertEqual(mocked.call_count, 0)


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

    def test_sync_counts_channels_that_do_not_exist_when_not_creating(self):
        with patch.object(GetXAPIDMService, 'list', return_value={
                'conversations': [self._conv('ghost', name='Ghost Chat')],
                'cursor': ''}):
            result = self.provider.sync_chat_names(
                self.account, create_missing=False)
        self.assertEqual(result['missing'], 1)
        self.assertEqual(result['updated'], 0)
        self.assertFalse(self.env['discuss.channel'].sudo().search_count([
            ('x_conversation_id', '=', 'ghost')]))

    def test_sync_creates_missing_channels(self):
        with patch.object(GetXAPIDMService, 'list', return_value={
                'conversations': [self._conv('brand-new', name='Fresh Chat')],
                'cursor': ''}):
            result = self.provider.sync_chat_names(self.account)
        self.assertEqual(result['missing'], 0)
        self.assertEqual(result['created'], 1)
        channel = self.env['discuss.channel'].sudo().search([
            ('x_account_id', '=', self.account.id),
            ('x_conversation_id', '=', 'brand-new')], limit=1)
        self.assertTrue(channel)
        self.assertEqual(channel.channel_type, 'x_group')
        self.assertEqual(channel.name, 'Fresh Chat')
        self.assertTrue(channel.x_group_member_count)

    def test_fetch_groups_syncs_g_prefixed_chat_as_group_without_type(self):
        """XChat 'g...' conversations sync as groups even when GetXAPI omits
        the group flag and type in the inbox payload."""
        conv_id = 'g2032517123456'
        conv = {
            'conversation_id': conv_id,
            'type': '',
            'group': False,
            'participants': [
                {'id': '12345', 'userName': 'getxapi_names'},
                {'id': '999', 'userName': 'peer_user'},
            ],
        }
        with patch.object(GetXAPIDMService, 'list', return_value={
                'conversations': [conv], 'cursor': ''}):
            result = self.provider.fetch_groups(self.account)
        self.assertEqual(result['created'], 1)
        self.assertEqual(result['groups'], 1)
        channel = self.env['discuss.channel'].sudo().search([
            ('x_account_id', '=', self.account.id),
            ('x_conversation_id', '=', conv_id)], limit=1)
        self.assertTrue(channel)
        self.assertEqual(channel.channel_type, 'x_group')
        self.assertTrue(channel._x_is_group_conversation())

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

    def test_fetch_groups_syncs_one_to_one_as_direct_channel(self):
        conv_id = '1196139139269320708-12345'
        with patch.object(GetXAPIDMService, 'list', return_value={
                'conversations': [self._conv(conv_id, ctype='one_to_one')],
                'cursor': ''}):
            result = self.provider.fetch_groups(self.account)
        self.assertEqual(result['created'], 1)
        channel = self.env['discuss.channel'].sudo().search([
            ('x_account_id', '=', self.account.id),
            ('x_conversation_id', '=', conv_id)], limit=1)
        self.assertTrue(channel)
        self.assertEqual(channel.channel_type, 'x')
        self.assertEqual(channel.x_partner_id.x_user_id, '999')

    def test_fetch_groups_backfills_partner_on_existing_direct_channel(self):
        conv_id = '1196139139269320709-12345'
        channel = self.env['discuss.channel'].sudo()._get_x_channel(
            self.account, conversation_id=conv_id, channel_type='x_group',
            create_if_not_found=True)
        with patch.object(GetXAPIDMService, 'list', return_value={
                'conversations': [self._conv(conv_id, ctype='one_to_one')],
                'cursor': ''}):
            self.provider.fetch_groups(self.account)
        channel.invalidate_recordset()
        self.assertEqual(channel.x_partner_id.x_user_id, '999')
        self.assertFalse(channel._x_is_group_conversation())

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
