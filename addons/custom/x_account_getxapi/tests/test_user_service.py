from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.x_account_getxapi.services.getxapi_client import GetXAPIClient
from odoo.addons.x_account_getxapi.services.getxapi_user_service import GetXAPIUserService

from .common import XAccountGetXAPITestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPIUserService(XAccountGetXAPITestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = GetXAPIClient(cls.env, 'test_key')
        cls.service = GetXAPIUserService(cls.client)

    def test_info(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {'rest_id': '12345', 'legacy': {'screen_name': 'testuser', 'name': 'Test'}},
        }) as mocked:
            result = self.service.info('testuser')
        self.assertEqual(result['id'], '12345')
        self.assertEqual(result['username'], 'testuser')
        mocked.assert_called_once()

    def test_info_strips_at(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {'rest_id': '12345', 'legacy': {'screen_name': 'testuser'}},
        }) as mocked:
            self.service.info('@testuser')
        params = mocked.call_args.kwargs.get('params', {})
        self.assertEqual(params.get('userName'), 'testuser')

    def test_info_by_id(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {'rest_id': '12345', 'legacy': {'screen_name': 'testuser'}},
        }) as mocked:
            result = self.service.info_by_id('12345')
        self.assertEqual(result['id'], '12345')

    def test_search(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {
                'entries': [
                    {'rest_id': '100', 'legacy': {'screen_name': 'alice'}},
                ],
                'has_more': False,
            }
        }):
            result = self.service.search('alice')
        self.assertEqual(len(result['users']), 1)

    def test_tweets(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {
                'entries': [
                    {'rest_id': '1', 'legacy': {'full_text': 'tweet 1'}},
                ],
                'has_more': False,
            }
        }):
            result = self.service.tweets('testuser')
        self.assertEqual(len(result['tweets']), 1)

    def test_followers(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {
                'entries': [
                    {'rest_id': '100', 'legacy': {'screen_name': 'alice'}},
                    {'rest_id': '200', 'legacy': {'screen_name': 'bob'}},
                ],
                'has_more': False,
            }
        }):
            result = self.service.followers('testuser')
        self.assertEqual(len(result['users']), 2)

    def test_following(self):
        with patch.object(GetXAPIClient, 'get', return_value={
            'data': {
                'entries': [
                    {'rest_id': '100', 'legacy': {'screen_name': 'alice'}},
                ],
                'has_more': False,
            }
        }):
            result = self.service.following('testuser')
        self.assertEqual(len(result['users']), 1)

    def test_follow(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {'result': {'following': True, 'user_id': '100'}},
        }) as mocked:
            result = self.service.follow('alice')
        self.assertTrue(result['success'])
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['userName'], 'alice')

    def test_follow_strips_at(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {'result': {'following': True}},
        }) as mocked:
            self.service.follow('@alice')
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['userName'], 'alice')

    def test_unfollow(self):
        with patch.object(GetXAPIClient, 'post', return_value={
            'data': {'result': {'following': False, 'user_id': '100'}},
        }) as mocked:
            result = self.service.unfollow('alice')
        self.assertTrue(result['success'])
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['userName'], 'alice')
