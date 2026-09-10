from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.x_account_getxapi.services.getxapi_client import GetXAPIClient
from odoo.addons.x_account_getxapi.services.getxapi_dm_service import GetXAPIDMService

from .common import XAccountGetXAPITestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPIDMService(XAccountGetXAPITestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = GetXAPIClient(cls.env, 'test_key')
        cls.service = GetXAPIDMService(cls.client)

    def test_send(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {'message_id': 'dm-1', 'created_at': '2026-01-01'},
        }) as mocked:
            result = self.service.send('9', 'hello there')
        self.assertEqual(result['message_id'], 'dm-1')
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['recipient_id'], '9')
        self.assertEqual(body['text'], 'hello there')

    def test_send_requires_recipient(self):
        with self.assertRaises(ValueError):
            self.service.send('', 'hello')

    def test_send_requires_text(self):
        with self.assertRaises(ValueError):
            self.service.send('9', '')

    def test_list_conversations(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'conversations': [
                {'conversation_id': 'conv-1', 'type': 'ONE_TO_ONE',
                 'participants': [{'id': '9', 'screen_name': 'peer'}]},
            ],
            'next_cursor': 'c2', 'has_more': True,
        }) as mocked:
            result = self.service.list(auth_token='tok')
        self.assertEqual(len(result['conversations']), 1)
        self.assertEqual(result['conversations'][0]['conversation_id'], 'conv-1')
        self.assertEqual(result['cursor'], 'c2')
        self.assertTrue(result['has_more'])
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['auth_token'], 'tok')

    def test_list_conversations_requires_auth_token(self):
        with self.assertRaises(ValueError):
            self.service.list()

    def test_conversation_messages(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'messages': [
                {'id': 'm1', 'text': 'hello', 'sender_id': '9'},
            ],
        }) as mocked:
            result = self.service.conversation('conv-1', auth_token='tok')
        self.assertEqual(len(result['messages']), 1)
        self.assertEqual(result['messages'][0]['id'], 'm1')
        self.assertEqual(mocked.call_args.args[0], '/twitter/dm/conversation')
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['conversation_id'], 'conv-1')
