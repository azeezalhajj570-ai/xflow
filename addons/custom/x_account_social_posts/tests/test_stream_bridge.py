from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.x_account.models.social_account import SocialAccount

from .common import XAccountSocialPostsTestBase, _StubProvider


@tagged('post_install', '-at_install', 'x_account_social_posts')
class TestXStreamBridge(XAccountSocialPostsTestBase):

    def test_get_or_create_stream_is_idempotent(self):
        stream = self.account._get_or_create_x_posts_stream()
        self.assertTrue(stream)
        self.assertEqual(stream.stream_type_id.stream_type, 'x_account_posts')
        again = self.account._get_or_create_x_posts_stream()
        self.assertEqual(stream, again)

    def test_is_x_account_stream(self):
        stream = self.account._get_or_create_x_posts_stream()
        self.assertTrue(stream._is_x_account_stream())
        self.assertEqual(stream.account_id, self.account)

    def test_fetch_stream_data_respects_skip_context(self):
        stream = self.account._get_or_create_x_posts_stream()
        # The create-time context must not trigger a provider read.
        with patch.object(SocialAccount, 'get_provider_for_operation'
                          ) as mocked_provider:
            result = stream.with_context(
                x_skip_stream_fetch=True)._fetch_stream_data()
        self.assertFalse(result)
        self.assertEqual(mocked_provider.call_count, 0)
        self.assertEqual(self.env['social.stream.post'].search_count(
            [('stream_id', '=', stream.id)]), 0)

    def test_fetch_stream_data_syncs_via_provider(self):
        stream = self.account._get_or_create_x_posts_stream()
        stub = _StubProvider(posts=[self.post_dto(id='555')])
        with patch.object(SocialAccount, 'get_provider_for_operation',
                          return_value=stub):
            result = stream._fetch_stream_data()
        self.assertTrue(result)
        self.assertEqual(self.env['social.stream.post'].search_count(
            [('stream_id', '=', stream.id), ('x_tweet_id', '=', '555')]), 1)

    def test_wizard_fetches_posts_and_interactions(self):
        stub = _StubProvider(
            posts=[self.post_dto(id='777')],
            comments=[{
                'id': '901', 'text': 'hi', 'author_id': '5',
                'author_username': 'dave', 'author_name': 'Dave',
                'created_at': '2026-01-03T08:00:00Z',
            }],
        )
        wizard = self.env['x.post.fetch.wizard'].create({
            'account_id': self.account.id,
            'limit': 5,
            'fetch_comments': True,
            'fetch_retweeters': False,
            'fetch_likers': False,
        })
        with patch.object(SocialAccount, 'get_provider_for_operation',
                          return_value=stub):
            action = wizard.action_fetch()
        self.assertEqual(action['type'], 'ir.actions.client')
        post = self.env['social.stream.post'].search([
            ('x_tweet_id', '=', '777')], limit=1)
        self.assertTrue(post)
        self.assertEqual(self.env['x.post.interaction'].search_count(
            [('stream_post_id', '=', post.id), ('kind', '=', 'comment')]), 1)
