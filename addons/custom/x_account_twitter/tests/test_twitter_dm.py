from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.x_account_twitter.services.twitter_api_client import TwitterApiClient
from odoo.addons.x_account_twitter.services.twitter_provider import TwitterProvider

from .common import XAccountTwitterTestBase


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterProviderSendDM(XAccountTwitterTestBase):
    """TwitterProvider send_dm / send_group_dm over the official X API v2."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
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
        cls.provider = TwitterProvider(cls.env, cls.account)

    # -------------------------------------------------------------- send_dm
    def test_send_dm_calls_api_and_normalizes(self):
        with patch.object(TwitterApiClient, 'request', return_value={
            'data': {'dm_conversation_id': '111-222', 'dm_event_id': '999'},
        }) as req:
            result = self.provider.send_dm(recipient_id='424242', text='Hello')
        self.assertTrue(result['success'])
        self.assertEqual(result['operation'], 'send_dm')
        self.assertEqual(result['platform'], 'x')
        self.assertEqual(result['message_id'], '999')
        self.assertEqual(result['conversation_id'], '111-222')
        self.assertEqual(result['text'], 'Hello')
        self.assertEqual(
            req.call_args.args[1], '/2/dm_conversations/with/424242/messages')
        self.assertEqual(req.call_args.kwargs['body'], {'text': 'Hello'})

    def test_send_dm_accepts_participant_alias(self):
        with patch.object(TwitterApiClient, 'request', return_value={
            'data': {'dm_conversation_id': '111-222', 'dm_event_id': '1001'},
        }) as req:
            self.provider.send_dm(participant_id='424242', text='Hi')
        self.assertEqual(
            req.call_args.args[1], '/2/dm_conversations/with/424242/messages')

    def test_send_dm_requires_recipient(self):
        with self.assertRaises(ValueError):
            self.provider.send_dm(text='Hi')

    def test_send_dm_requires_text(self):
        with self.assertRaises(ValueError):
            self.provider.send_dm(recipient_id='424242')

    # --------------------------------------------------------- send_group_dm
    def test_send_group_dm_calls_api_and_normalizes(self):
        with patch.object(TwitterApiClient, 'request', return_value={
            'data': {'dm_conversation_id': 'g-conv-7', 'dm_event_id': '1002'},
        }) as req:
            result = self.provider.send_group_dm(
                conversation_id='g-conv-7', text='Group update')
        self.assertTrue(result['success'])
        self.assertEqual(result['operation'], 'send_group_dm')
        self.assertEqual(result['message_id'], '1002')
        self.assertEqual(result['conversation_id'], 'g-conv-7')
        self.assertEqual(
            req.call_args.args[1], '/2/dm_conversations/g-conv-7/messages')
        self.assertEqual(req.call_args.kwargs['body'], {'text': 'Group update'})

    def test_send_group_dm_accepts_dm_conversation_alias(self):
        with patch.object(TwitterApiClient, 'request', return_value={
            'data': {'dm_conversation_id': 'g-conv-7', 'dm_event_id': '1003'},
        }) as req:
            self.provider.send_group_dm(dm_conversation_id='g-conv-7', text='Hi')
        self.assertEqual(
            req.call_args.args[1], '/2/dm_conversations/g-conv-7/messages')

    def test_send_group_dm_requires_conversation(self):
        with self.assertRaises(ValueError):
            self.provider.send_group_dm(text='Hi')

    def test_send_group_dm_requires_text(self):
        with self.assertRaises(ValueError):
            self.provider.send_group_dm(conversation_id='g-conv-7')

    # ------------------------------------------------------- capability model
    def test_dm_operations_supported(self):
        supported = self.provider.supported_operations()
        self.assertIn('send_dm', supported)
        self.assertIn('send_group_dm', supported)