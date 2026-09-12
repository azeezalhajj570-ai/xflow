from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.x_account_getxapi.services.getxapi_client import GetXAPIClient
from odoo.addons.x_account_getxapi.services.getxapi_errors import GetXAPIError

from .common import XAccountGetXAPITestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPIClient(XAccountGetXAPITestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = GetXAPIClient(cls.env, 'test_api_key')

    def _mock_response(self, status_code=200, json_data=None, ok=True):
        resp = MagicMock()
        resp.status_code = status_code
        resp.ok = ok and status_code < 400
        resp.content = b'{}' if json_data is not None else b''
        resp.json.return_value = json_data or {}
        resp.headers = {}
        return resp

    def test_get_request(self):
        with patch('requests.request') as mocked:
            mocked.return_value = self._mock_response(200, {'data': {'id': '1'}})
            result = self.client.get('/twitter/user/info', params={'userName': 'test'})
        self.assertEqual(result, {'data': {'id': '1'}})
        self.assertEqual(mocked.call_args.args[0], 'GET')
        self.assertIn('/twitter/user/info', mocked.call_args.args[1])

    def test_post_request(self):
        with patch('requests.request') as mocked:
            mocked.return_value = self._mock_response(200, {'data': {'retweeted': True}})
            result = self.client.post('/twitter/tweet/retweet', json={'tweet_id': '123'})
        self.assertEqual(result, {'data': {'retweeted': True}})
        self.assertEqual(mocked.call_args.args[0], 'POST')

    def test_auth_header(self):
        with patch('requests.request') as mocked:
            mocked.return_value = self._mock_response(200, {})
            self.client.get('/twitter/user/info')
        headers = mocked.call_args.kwargs['headers']
        self.assertEqual(headers['Authorization'], 'Bearer test_api_key')

    def test_missing_api_key_raises(self):
        client = GetXAPIClient(self.env, '', account_id=999)
        with self.assertRaises(GetXAPIError):
            client.get('/twitter/user/info')

    def test_cost_logged_on_success(self):
        with patch('requests.request') as mocked:
            mocked.return_value = self._mock_response(200, {'data': {}})
            result = self.client.get('/twitter/tweet/retweet')
        self.assertEqual(result, {'data': {}})

    def test_cost_logged_on_failure(self):
        with patch('requests.request') as mocked:
            mocked.return_value = self._mock_response(401, {'error': 'unauthorized'}, ok=False)
            with self.assertRaises(GetXAPIError):
                self.client.get('/twitter/user/info')

    def test_retry_on_500(self):
        with patch('requests.request') as mocked:
            mocked.side_effect = [
                self._mock_response(500, ok=False),
                self._mock_response(200, {'data': {}}),
            ]
            with patch.object(GetXAPIClient, '_sleep'):
                result = self.client.get('/twitter/user/info')
        self.assertEqual(result, {'data': {}})
        self.assertEqual(mocked.call_count, 2)

    def test_retry_on_network_error(self):
        import requests as req
        with patch('requests.request') as mocked:
            mocked.side_effect = [
                req.ConnectionError('boom'),
                self._mock_response(200, {'data': {}}),
            ]
            with patch.object(GetXAPIClient, '_sleep'):
                result = self.client.get('/twitter/user/info')
        self.assertEqual(result, {'data': {}})
        self.assertEqual(mocked.call_count, 2)

    def test_no_retry_on_4xx(self):
        with patch('requests.request') as mocked:
            mocked.return_value = self._mock_response(404, {'error': 'not found'}, ok=False)
            with self.assertRaises(GetXAPIError):
                self.client.get('/twitter/user/info')
        self.assertEqual(mocked.call_count, 1)

    def test_write_no_retry_on_timeout(self):
        import requests as req
        with patch('requests.request') as mocked:
            mocked.side_effect = req.Timeout('timeout')
            with self.assertRaises(GetXAPIError):
                self.client.post('/twitter/tweet/create', json={'text': 'hello'})
        self.assertEqual(mocked.call_count, 1)

    def test_transient_429_retried_then_succeeds(self):
        with patch('requests.request') as mocked:
            mocked.side_effect = [
                self._mock_response(429, {'error': 'Too Many Requests',
                                          'retry_after': 5}, ok=False),
                self._mock_response(200, {'data': {'retweeted': True}}),
            ]
            with patch.object(GetXAPIClient, '_sleep') as sleep:
                result = self.client.post('/twitter/tweet/retweet',
                                          json={'tweet_id': '1'})
        self.assertEqual(result, {'data': {'retweeted': True}})
        self.assertEqual(mocked.call_count, 2)
        sleep.assert_called_once_with(5.0)

    def test_429_retries_exhausted_raises_rate_limit(self):
        with patch('requests.request') as mocked:
            mocked.return_value = self._mock_response(
                429, {'error': 'Too Many Requests'}, ok=False)
            with patch.object(GetXAPIClient, '_sleep'):
                with self.assertRaises(GetXAPIError) as ctx:
                    self.client.get('/twitter/user/info')
        self.assertEqual(ctx.exception.code, 'rate_limit')
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(mocked.call_count, 1 + self.client.DEFAULT_RETRIES)

    def test_daily_dm_limit_429_never_retried(self):
        with patch('requests.request') as mocked:
            mocked.return_value = self._mock_response(429, {
                'error': 'Too Many Requests',
                'twitter_error_code': 502,
                'retry_after': 86400,
            }, ok=False)
            with self.assertRaises(GetXAPIError) as ctx:
                self.client.post('/twitter/dm/send', json={
                    'auth_token': 'tok', 'recipient_id': '9', 'text': 'hi'})
        self.assertEqual(ctx.exception.code, 'daily_dm_limit')
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(ctx.exception.twitter_error_code, 502)
        self.assertEqual(ctx.exception.retry_after, 86400.0)
        self.assertEqual(mocked.call_count, 1)

    def test_429_error_logged_with_upstream_fields(self):
        account = self.env['social.account'].create({
            'name': 'Usage Account',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'usage_user',
            'twitter_user_id': '999',
            'x_provider': 'getxapi',
            'x_getxapi_auth_token': 'tok',
        })
        client = GetXAPIClient(self.env, 'test_api_key', account_id=account.id)
        with patch('requests.request') as mocked:
            mocked.return_value = self._mock_response(429, {
                'error': 'Too Many Requests',
                'twitter_error_code': 502,
                'retry_after': 3600,
            }, ok=False)
            # Plain try/except on purpose: Odoo's assertRaises wraps its block
            # in a savepoint and rolls it back on the expected exception, which
            # would also undo the usage row asserted on below.
            try:
                client.post('/twitter/dm/send', json={
                    'auth_token': 'tok', 'recipient_id': '9', 'text': 'hi'})
                self.fail('GetXAPIError was not raised')
            except GetXAPIError:
                pass
        record = self.env['getxapi.api.usage'].search([
            ('account_id', '=', account.id),
        ], limit=1)
        self.assertEqual(record.status_code, 429)
        self.assertEqual(record.error_type, 'daily_dm_limit')
        self.assertEqual(record.twitter_error_code, 502)
        self.assertEqual(record.retry_after, 3600.0)
        self.assertIn('daily DM', record.error_message)

    def test_pagination_first_page(self):
        with patch('requests.request') as mocked:
            mocked.return_value = self._mock_response(200, {
                'data': {
                    'tweets': [{'id': '1'}, {'id': '2'}],
                    'has_more': False,
                }
            })
            pages = list(self.client.paginate('/twitter/tweet/advanced_search',
                                               params={'query': 'test'}))
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(pages[0]), 2)

    def test_pagination_multi_page(self):
        with patch('requests.request') as mocked:
            mocked.side_effect = [
                self._mock_response(200, {
                    'data': {'tweets': [{'id': '1'}], 'has_more': True,
                             'next_cursor': 'cur1'},
                }),
                self._mock_response(200, {
                    'data': {'tweets': [{'id': '2'}], 'has_more': False},
                }),
            ]
            pages = list(self.client.paginate('/twitter/tweet/advanced_search',
                                               params={'query': 'test'}))
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0][0]['id'], '1')
        self.assertEqual(pages[1][0]['id'], '2')

    def test_pagination_stops_on_repeated_cursor(self):
        with patch('requests.request') as mocked:
            mocked.side_effect = [
                self._mock_response(200, {
                    'data': {'tweets': [{'id': '1'}], 'has_more': True,
                             'next_cursor': 'cur1'},
                }),
                self._mock_response(200, {
                    'data': {'tweets': [{'id': '2'}], 'has_more': True,
                             'next_cursor': 'cur1'},
                }),
            ]
            pages = list(self.client.paginate('/twitter/tweet/advanced_search',
                                               params={'query': 'test'}))
        self.assertEqual(len(pages), 2)

    def test_pagination_max_pages(self):
        cursor_counter = [0]
        def mock_request(*args, **kwargs):
            cursor_counter[0] += 1
            return self._mock_response(200, {
                'data': {'tweets': [{'id': '1'}], 'has_more': True,
                         'next_cursor': 'cur%s' % cursor_counter[0]},
            })
        with patch('requests.request', side_effect=mock_request):
            pages = list(self.client.paginate('/twitter/tweet/advanced_search',
                                               params={'query': 'test'},
                                               max_pages=3))
        self.assertEqual(len(pages), 3)

    def test_pagination_max_items(self):
        with patch('requests.request') as mocked:
            mocked.return_value = self._mock_response(200, {
                'data': {'tweets': [{'id': '1'}, {'id': '2'}], 'has_more': True,
                         'next_cursor': 'cur'},
            })
            pages = list(self.client.paginate('/twitter/tweet/advanced_search',
                                               params={'query': 'test'},
                                               max_items=2))
        self.assertEqual(len(pages), 1)

    def test_collect_pages(self):
        with patch('requests.request') as mocked:
            mocked.side_effect = [
                self._mock_response(200, {
                    'data': {'tweets': [{'id': '1'}], 'has_more': True,
                             'next_cursor': 'cur1'},
                }),
                self._mock_response(200, {
                    'data': {'tweets': [{'id': '2'}], 'has_more': False},
                }),
            ]
            items = self.client.collect_pages('/twitter/tweet/advanced_search',
                                               params={'query': 'test'})
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['id'], '1')
        self.assertEqual(items[1]['id'], '2')
