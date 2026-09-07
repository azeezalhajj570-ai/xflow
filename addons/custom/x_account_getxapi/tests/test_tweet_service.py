from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.x_account_getxapi.services.getxapi_client import GetXAPIClient
from odoo.addons.x_account_getxapi.services.getxapi_tweet_service import GetXAPITweetService

from .common import XAccountGetXAPITestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPITweetService(XAccountGetXAPITestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = GetXAPIClient(cls.env, 'test_key')
        cls.service = GetXAPITweetService(cls.client)

    def test_search(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {
                'entries': [{'rest_id': '1', 'legacy': {'full_text': 'result'}}],
                'has_more': False,
            }
        }) as mocked:
            result = self.service.search('python')
        self.assertEqual(len(result['tweets']), 1)
        mocked.assert_called_once()
        self.assertIn('query', mocked.call_args.kwargs.get('params', {}))

    def test_detail(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {'rest_id': '123', 'legacy': {'full_text': 'hello'}},
        }) as mocked:
            result = self.service.detail('123')
        self.assertEqual(result['id'], '123')
        self.assertEqual(result['text'], 'hello')

    def test_thread(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {
                'entries': [
                    {'rest_id': '1', 'legacy': {'full_text': 'first'}},
                    {'rest_id': '2', 'legacy': {'full_text': 'second'}},
                ],
            }
        }):
            result = self.service.thread('1')
        self.assertEqual(len(result['tweets']), 2)

    def test_replies(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {
                'entries': [{'rest_id': '3', 'legacy': {'full_text': 'reply'}}],
                'has_more': False,
            }
        }):
            result = self.service.replies('1')
        self.assertEqual(len(result['tweets']), 1)

    def test_retweeters(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {
                'entries': [
                    {'rest_id': '100', 'legacy': {'screen_name': 'alice'}},
                ],
                'has_more': False,
            }
        }):
            result = self.service.retweeters('1')
        self.assertEqual(len(result['users']), 1)

    def test_create(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {'result': {'rest_id': '999', 'text': 'new tweet'}},
        }) as mocked:
            result = self.service.create('new tweet')
        self.assertTrue(result['success'])
        self.assertEqual(result['tweet_id'], '999')
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['text'], 'new tweet')

    def test_edit(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {'result': {'rest_id': '999', 'text': 'edited'}},
        }) as mocked:
            result = self.service.edit('999', 'edited')
        self.assertTrue(result['success'])
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['tweet_id'], '999')
        self.assertEqual(body['text'], 'edited')

    def test_like(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {'result': {'favorited': True}},
        }) as mocked:
            result = self.service.like('111')
        self.assertTrue(result['success'])
        self.assertEqual(result['post_id'], '111')
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['tweet_id'], '111')

    def test_retweet(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {'result': {'retweeted': True}},
        }) as mocked:
            result = self.service.retweet('222')
        self.assertTrue(result['success'])
        self.assertEqual(result['post_id'], '222')
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['tweet_id'], '222')
