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
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {
                'conversations': [
                    {'conversation_id': 'conv-1', 'type': 'one_to_one'},
                ],
            }
        }) as mocked:
            result = self.service.list()
        self.assertEqual(len(result['conversations']), 1)
        self.assertEqual(result['conversations'][0]['conversation_id'], 'conv-1')

    def test_list_messages(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {
                'messages': [
                    {'id': 'm1', 'text': 'hello', 'sender_id': '9'},
                ],
            }
        }) as mocked:
            result = self.service.list(conversation_id='conv-1')
        self.assertEqual(len(result['messages']), 1)
        self.assertEqual(result['messages'][0]['id'], 'm1')
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['conversation_id'], 'conv-1')
