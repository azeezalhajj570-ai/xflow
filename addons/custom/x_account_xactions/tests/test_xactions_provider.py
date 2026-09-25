from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.x_account.services.x_provider import XProviderRegistry
from odoo.addons.x_account.services.x_service import XService
from odoo.addons.x_account_xactions.services.xactions_client import (
    XActionsClient,
    XActionsError,
)
from odoo.addons.x_account_xactions.services.xactions_provider import (
    XActionsProvider,
)

from .common import REPORT_PAYLOAD, XAccountXActionsTestBase


@tagged('post_install', '-at_install', 'x_account_xactions')
class TestXActionsProvider(XAccountXActionsTestBase):

    def test_registry_resolves_xactions(self):
        self.assertIs(XProviderRegistry.resolve('xactions'), XActionsProvider)

    def test_get_provider_dispatches_xactions(self):
        self.assertIsInstance(XService.get_provider(self.account),
                              XActionsProvider)

    def test_declares_read_operations_capability(self):
        self.assertTrue(XActionsProvider._read_operations >= {
            'fetch_user_posts', 'fetch_post_comments',
            'fetch_post_retweeters', 'fetch_post_likers'})

    def test_validate_session_ok(self):
        with patch.object(XActionsClient, 'get', return_value={
            'id': 'u1', 'username': 'operator', 'hasSession': True,
        }):
            result = self.provider.validate_session()
        self.assertTrue(result['valid'])
        self.assertEqual(result['reason'], 'xactions')
        self.assertTrue(result['has_session'])

    def test_validate_session_network_error(self):
        with patch.object(XActionsClient, 'get',
                          side_effect=XActionsError(0, 'boom',
                                                    code='network_error')):
            result = self.provider.validate_session()
        self.assertFalse(result['valid'])
        self.assertIn('boom', result['reason'])

    def test_missing_base_url_fails_fast(self):
        """The base URL is configuration: unset means a clear error, not a bad
        relative URL."""
        icp = self.env['ir.config_parameter'].sudo()
        previous = icp.get_param('x_account.xactions_base_url')
        try:
            icp.set_param('x_account.xactions_base_url', '')
            with self.assertRaises(XActionsError) as ctx:
                XActionsClient(self.env).get('/api/user/me')
            self.assertEqual(ctx.exception.code, 'config_error')
        finally:
            icp.set_param('x_account.xactions_base_url', previous or '')

    def test_fetch_user_posts_maps_per_post_limit(self):
        with patch.object(XActionsClient, 'post',
                          return_value=REPORT_PAYLOAD) as mocked:
            self.provider.fetch_user_posts(
                'xactions_user', limit=5, per_post_limit=250)
        self.assertEqual(mocked.call_args.kwargs['json']['perPostLimit'], 250)

    def test_fetch_user_posts_returns_metrics_and_inline_audience(self):
        with patch.object(XActionsClient, 'post',
                          return_value=REPORT_PAYLOAD) as mocked:
            result = self.provider.fetch_user_posts('xactions_user', limit=5)
        self.assertEqual(len(result['posts']), 1)
        post = result['posts'][0]
        self.assertEqual(post['id'], '111')
        self.assertEqual(post['favorite_count'], 7)
        self.assertEqual(post['audience']['likers'][0]['username'], 'bob')
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['posts'], 5)
        self.assertEqual(body['types'],
                         ['likers', 'commenters', 'retweeters'])

    def test_fetch_post_comments(self):
        with patch.object(XActionsClient, 'get', return_value={
            'commenters': [{'userId': '5', 'username': 'eve', 'name': 'Eve'}],
            'hasMore': False,
        }) as mocked:
            result = self.provider.fetch_post_comments('111', limit=20)
        self.assertEqual(result['comments'][0]['author_username'], 'eve')
        self.assertEqual(mocked.call_args.kwargs['params']['postId'], '111')

    def test_fetch_post_retweeters_is_unsupported_per_post(self):
        result = self.provider.fetch_post_retweeters('111')
        self.assertTrue(result['unsupported'])
        self.assertEqual(result['users'], [])

    def test_fetch_post_likers_is_unsupported_per_post(self):
        result = self.provider.fetch_post_likers('111')
        self.assertTrue(result['unsupported'])
        self.assertEqual(result['users'], [])

    def test_supported_operations(self):
        ops = self.provider.supported_operations()
        self.assertIn('validate_session', ops)
        self.assertIn('fetch_user_posts', ops)
        self.assertIn('fetch_post_comments', ops)
