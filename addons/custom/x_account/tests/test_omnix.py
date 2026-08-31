from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.x_account.services.providers.omnix import OmniXProvider
from odoo.addons.x_account.services.providers.session_web import SessionWebProvider
from odoo.addons.x_account.services.x_provider import XProviderRegistry
from odoo.addons.x_account.services.x_service import XService
from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestOmniXProvider(XAccountTestBase):
    """T18: optional OmniX REST provider (per-account either/or with session)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'test-encryption-key')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.omnix_api_key', 'omnix_live_test_key')

        cls.account = cls.env['social.account'].create({
            'name': 'OmniX Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'omnix_user',
            'twitter_user_id': '12345',
            'x_provider': 'omnix',
            'x_auth_method': 'session_cookie',
        })
        from odoo.addons.x_account.services.session_manager import XSessionManager
        XSessionManager.create_store(
            cls.account, 'auth_token=test-auth-token; ct0=test-ct0')
        cls.cookies = SessionWebProvider.parse_cookie_string(
            XSessionManager.load(cls.account))
        cls.provider = OmniXProvider(cls.env, cls.account, cls.cookies)

    # ------------------------------------------------------------- validation
    def test_validate_session_ok(self):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            'status': True,
            'data': {'id': '12345', 'userName': 'omnix_user', 'name': 'OmniX Account'},
            'error': None,
        }
        with patch.object(self.provider, '_request', return_value=response.json()) as req:
            result = self.provider.validate_session()
        self.assertTrue(result['valid'])
        self.assertEqual(result['reason'], 'omnix')
        self.assertEqual(result['user']['id'], '12345')
        self.assertEqual(result['user']['username'], 'omnix_user')
        self.assertEqual(req.call_args.args[1], '/user/info')

    def test_validate_session_missing_api_key(self):
        with patch.object(self.provider, '_api_key', ''):
            result = self.provider.validate_session()
        self.assertFalse(result['valid'])
        self.assertEqual(result['reason'], 'omnix_api_key_missing')

    def test_validate_session_missing_auth_token(self):
        provider = OmniXProvider(self.env, self.account, {})
        result = provider.validate_session()
        self.assertFalse(result['valid'])
        self.assertEqual(result['reason'], 'Missing auth_token cookie')

    def test_validate_session_network_error(self):
        with patch.object(self.provider, '_request',
                          side_effect=RuntimeError('network_error: boom')):
            result = self.provider.validate_session()
        self.assertFalse(result['valid'])
        self.assertEqual(result['reason'], 'network_error: boom')

    def test_validate_session_without_handle_resolves_token_user(self):
        """No handle on the account -> resolve current user from auth_token."""
        account = self.account.with_context(todo='no handle')
        account.write({'social_account_handle': False})
        provider = OmniXProvider(self.env, account, self.cookies)
        home = {
            'status': True,
            'data': {'userId': '999888', 'tweet_count': 5, 'tweets': []},
            'error': None,
        }
        info = {
            'status': True,
            'data': {'id': '999888', 'userName': 'token_user', 'name': 'Token User'},
            'error': None,
        }
        with patch.object(provider, '_request',
                          side_effect=[home, info]) as req:
            result = provider.validate_session()
        self.assertTrue(result['valid'])
        self.assertEqual(result['reason'], 'omnix_token')
        self.assertEqual(result['user']['id'], '999888')
        self.assertEqual(result['user']['username'], 'token_user')
        self.assertEqual(req.call_args_list[0].args[1], '/user/home_timeline')
        self.assertEqual(req.call_args_list[1].args[1], '/user/info_by_id')
        self.assertEqual(req.call_args_list[1].kwargs['params']['userId'], '999888')

    # ------------------------------------------------------------------- dm
    def test_get_conversations(self):
        data = {
            'status': True,
            'data': {
                'conversations': [
                    {'conversation_id': 'conv-1', 'type': 'one_to_one',
                     'participants': [{'id': '9'}], 'participant_count': 1,
                     'last_message': {'text': 'hi'}},
                    {'conversation_id': 'conv-2', 'type': 'group',
                     'participants': [{'id': '1'}, {'id': '2'}],
                     'participant_count': 2, 'last_message': None},
                ],
                'next_cursor': {'cursor_id': 'abc'},
            },
            'error': None,
        }
        with patch.object(self.provider, '_request', return_value=data) as req:
            result = self.provider.get_conversations(limit=10)
        self.assertEqual(len(result['conversations']), 2)
        self.assertEqual(result['conversations'][0]['conversation_id'], 'conv-1')
        self.assertFalse(result['conversations'][0]['group'])
        self.assertTrue(result['conversations'][1]['group'])
        self.assertEqual(result['conversations'][1]['participant_count'], 2)
        self.assertEqual(result['cursor'], 'abc')
        self.assertEqual(req.call_args.args[1], '/dm/list')

    def test_get_dms(self):
        data = {
            'status': True,
            'data': {
                'conversation_id': 'conv-1',
                'messages': [
                    {'seq_id': 'm1', 'text': 'hello', 'sender_id': '9',
                     'createdAt': '2026-01-01T00:00:00Z'},
                ],
                'next_cursor': 'cur1',
            },
            'error': None,
        }
        with patch.object(self.provider, '_request', return_value=data) as req:
            result = self.provider.get_dms('conv-1')
        self.assertEqual(result['messages'][0]['id'], 'm1')
        self.assertEqual(result['messages'][0]['conversation_id'], 'conv-1')
        self.assertEqual(result['cursor'], 'cur1')
        self.assertEqual(req.call_args.args[1], '/dm/conversation')
        self.assertEqual(req.call_args.kwargs['body']['conversation_id'], 'conv-1')

    def test_fetch_groups_syncs_channels_and_partners(self):
        """fetch_groups upserts group channels + member partners."""
        data = {
            'status': True,
            'data': {
                'conversations': [
                    {'conversation_id': 'grp-1', 'type': 'group',
                     'participants': [
                         {'id': '100', 'userName': 'alice', 'name': 'Alice'},
                         {'id': '200', 'userName': 'bob', 'name': 'Bob'},
                     ]},
                ],
            },
            'error': None,
        }
        with patch.object(self.provider, '_request', return_value=data):
            result = self.provider.fetch_groups(self.account)
        self.assertEqual(result['groups'], 1)
        self.assertEqual(result['created'], 1)
        self.assertEqual(result['members'], 2)
        channel = self.env['discuss.channel'].sudo().search([
            ('x_conversation_id', '=', 'grp-1'),
            ('x_account_id', '=', self.account.id),
        ], limit=1)
        self.assertTrue(channel)
        self.assertEqual(channel.channel_type, 'x_group')
        alice = self.env['res.partner'].sudo().search(
            [('x_user_id', '=', '100')], limit=1)
        bob = self.env['res.partner'].sudo().search(
            [('x_user_id', '=', '200')], limit=1)
        self.assertTrue(alice)
        self.assertTrue(bob)
        self.assertEqual(alice.x_username, 'alice')

    def test_send_dm(self):
        data = {'status': True, 'data': {'id': 'dm-1', 'created_at': '2026-01-01'}, 'error': None}
        with patch.object(self.provider, '_request', return_value=data) as req:
            result = self.provider.send_dm('9', 'hello there')
        self.assertEqual(result['message_id'], 'dm-1')
        self.assertEqual(req.call_args.kwargs['body']['recipient_id'], '9')
        self.assertEqual(req.call_args.kwargs['body']['text'], 'hello there')

    def test_send_dm_requires_args(self):
        with self.assertRaises(ValueError):
            self.provider.send_dm('', 'hello')
        with self.assertRaises(ValueError):
            self.provider.send_dm('9', '')

    # ------------------------------------------------------- automation ops
    def test_like(self):
        data = {'status': True, 'data': {'favorited': True}, 'error': None}
        with patch.object(self.provider, '_request', return_value=data) as req:
            result = self.provider.like('111')
        self.assertTrue(result['liked'])
        self.assertEqual(req.call_args.kwargs['body']['tweet_id'], '111')

    def test_comment(self):
        data = {'status': True, 'data': {'id': '222'}, 'error': None}
        with patch.object(self.provider, '_request', return_value=data) as req:
            result = self.provider.comment('111', 'nice post')
        self.assertEqual(result['comment_id'], '222')
        self.assertEqual(req.call_args.kwargs['body']['text'], 'nice post')

    def test_repost(self):
        data = {'status': True, 'data': {'retweeted': True}, 'error': None}
        with patch.object(self.provider, '_request', return_value=data):
            result = self.provider.repost('111')
        self.assertTrue(result['retweeted'])

    def test_follow(self):
        data = {'status': True, 'data': {'followed': True}, 'error': None}
        with patch.object(self.provider, '_request', return_value=data) as req:
            result = self.provider.follow('@someuser')
        self.assertTrue(result['followed'])
        self.assertEqual(req.call_args.kwargs['body']['userName'], 'someuser')

    def test_post_tweet(self):
        data = {'status': True, 'data': {'id': '333'}, 'error': None}
        with patch.object(self.provider, '_request', return_value=data) as req:
            result = self.provider.post_tweet('hello world')
        self.assertEqual(result['tweet_id'], '333')
        self.assertEqual(req.call_args.kwargs['body']['text'], 'hello world')

    # ------------------------------------------------------- error handling
    def test_http_402_is_transient(self):
        """Insufficient credits must surface as rate_limit (transient), not invalid."""
        with patch.object(self.provider, '_request',
                          side_effect=RuntimeError('rate_limit')):
            result = self.provider.validate_session()
        self.assertFalse(result['valid'])
        self.assertEqual(result['reason'], 'rate_limit')

    def test_http_401_maps_to_authentication_failure(self):
        with patch.object(self.provider, '_request',
                          side_effect=RuntimeError('authentication_failure')):
            result = self.provider.validate_session()
        self.assertFalse(result['valid'])
        self.assertEqual(result['reason'], 'authentication_failure')

    def test_request_attaches_auth_token(self):
        with patch('requests.request') as mocked:
            mocked.return_value = MagicMock(
                status_code=200, json=lambda: {'status': True, 'data': {}, 'error': None})
            self.provider._request('GET', '/user/info', params={'userName': 'u'})
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs['params']['auth_token'], 'test-auth-token')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer omnix_live_test_key')


@tagged('post_install', '-at_install', 'x_account')
class TestOmniXDispatch(XAccountTestBase):
    """T18: XService dispatch + selection for the 'omnix' provider code."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'test-encryption-key')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.omnix_api_key', 'omnix_live_test_key')
        from odoo.addons.x_account.services.session_manager import XSessionManager
        cls.account = cls.env['social.account'].create({
            'name': 'Dispatch Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'dispatch_user',
            'x_provider': 'omnix',
            'x_auth_method': 'session_cookie',
        })
        XSessionManager.create_store(
            cls.account, 'auth_token=test-auth-token; ct0=test-ct0')

    def test_registry_resolves_omnix_to_provider(self):
        self.assertIs(XProviderRegistry.resolve('omnix'), OmniXProvider)

    def test_get_provider_dispatches_omnix(self):
        provider = XService.get_provider(self.account)
        self.assertIsInstance(provider, OmniXProvider)

    def test_validate_via_xservice(self):
        with patch.object(OmniXProvider, '_request', return_value={
            'status': True,
            'data': {'id': '1', 'userName': 'dispatch_user', 'name': 'Dispatch'},
            'error': None,
        }):
            result = XService.validate(self.account)
        self.assertTrue(result['valid'])
        self.assertEqual(self.account.x_connection_status, 'active')
        self.assertIsNotNone(self.account.last_validated)

    def test_session_web_works_without_api_key(self):
        """Optionality: an account on session_web needs no OmniX key."""
        self.env['ir.config_parameter'].sudo().set_param(
            'x_account.omnix_api_key', False)
        account = self.env['social.account'].create({
            'name': 'Session Only',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'session_only',
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
        })
        provider = XService.get_provider(account)
        self.assertIsInstance(provider, SessionWebProvider)

    def test_action_fetch_groups_calls_provider(self):
        """action_fetch_groups dispatches to the provider and syncs channels."""
        data = {
            'status': True,
            'data': {
                'conversations': [
                    {'conversation_id': 'grp-act', 'type': 'group',
                     'participants': [{'id': '300', 'userName': 'carol',
                                       'name': 'Carol'}]},
                ],
            },
            'error': None,
        }
        with patch.object(OmniXProvider, '_request', return_value=data):
            result = self.account.action_fetch_groups()
        self.assertEqual(result['groups'], 1)
        channel = self.env['discuss.channel'].sudo().search([
            ('x_conversation_id', '=', 'grp-act'),
            ('x_account_id', '=', self.account.id),
        ], limit=1)
        self.assertTrue(channel)
        self.assertEqual(channel.channel_type, 'x_group')
