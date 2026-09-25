from datetime import datetime
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.x_account.models.social_account import SocialAccount
from odoo.addons.x_account_social_posts.services.post_sync import XPostSync

from .common import XAccountSocialPostsTestBase, _StubProvider


@tagged('post_install', '-at_install', 'x_account_social_posts')
class TestXPostSync(XAccountSocialPostsTestBase):

    def test_sync_timeline_creates_then_updates(self):
        stream = self.account._get_or_create_x_posts_stream()
        self.assertTrue(stream)

        stub = _StubProvider(posts=[self.post_dto()])
        with patch.object(SocialAccount, 'get_provider_for_operation',
                          return_value=stub):
            result = XPostSync(self.env, self.account).sync_timeline(stream)

        self.assertEqual(result['created'], 1)
        self.assertEqual(result['updated'], 0)
        post = self.env['social.stream.post'].search([
            ('stream_id', '=', stream.id), ('x_tweet_id', '=', '111')])
        self.assertEqual(len(post), 1)
        self.assertEqual(post.x_favorite_count, 7)
        self.assertEqual(post.x_retweet_count, 3)
        self.assertEqual(post.x_reply_count, 2)
        self.assertEqual(post.x_quote_count, 1)
        self.assertEqual(post.author_name, 'Test X')
        self.assertEqual(post.published_date, datetime(2026, 1, 1, 10, 0, 0))
        self.assertEqual(post.post_link, 'https://x.com/testx/status/111')
        self.assertEqual(post.author_link, 'https://x.com/testx')
        self.assertTrue(post.is_author)

        # Re-running updates in place (no duplicate post).
        stub2 = _StubProvider(posts=[self.post_dto(favorite_count=9)])
        with patch.object(SocialAccount, 'get_provider_for_operation',
                          return_value=stub2):
            result2 = XPostSync(self.env, self.account).sync_timeline(stream)
        self.assertEqual(result2['created'], 0)
        self.assertEqual(result2['updated'], 1)
        self.assertEqual(self.env['social.stream.post'].search_count(
            [('stream_id', '=', stream.id)]), 1)
        self.assertEqual(post.x_favorite_count, 9)

    def test_sync_interactions_stores_comments_and_retweeters(self):
        stream = self.account._get_or_create_x_posts_stream()
        post = self.env['social.stream.post'].create({
            'stream_id': stream.id,
            'x_tweet_id': '111',
            'message': 'Hello X',
        })
        stub = _StubProvider(
            comments=[{
                'id': '900', 'text': 'Nice!', 'author_id': '77',
                'author_username': 'bob', 'author_name': 'Bob',
                'created_at': '2026-01-02T09:00:00Z',
                'favorite_count': 4, 'reply_count': 0, 'retweet_count': 1,
                'raw': {'rest_id': '900'},
            }],
            retweeters=[{'id': '88', 'username': 'carol', 'name': 'Carol'}],
        )
        with patch.object(SocialAccount, 'get_provider_for_operation',
                          return_value=stub):
            summary = XPostSync(
                self.env, self.account).sync_interactions(post)

        self.assertEqual(summary['comments'], 1)
        self.assertEqual(summary['retweets'], 1)
        comment = self.env['x.post.interaction'].search([
            ('stream_post_id', '=', post.id), ('kind', '=', 'comment')])
        self.assertEqual(comment.text, 'Nice!')
        self.assertEqual(comment.author_x_username, 'bob')
        self.assertEqual(comment.like_count, 4)
        self.assertEqual(comment.author_partner_id.x_user_id, '77')
        retweet = self.env['x.post.interaction'].search([
            ('stream_post_id', '=', post.id), ('kind', '=', 'retweet')])
        self.assertEqual(retweet.author_x_username, 'carol')
        self.assertTrue(post.x_interactions_fetched_at)

    def test_sync_interactions_is_idempotent(self):
        stream = self.account._get_or_create_x_posts_stream()
        post = self.env['social.stream.post'].create({
            'stream_id': stream.id, 'x_tweet_id': '111'})
        stub = _StubProvider(retweeters=[
            {'id': '88', 'username': 'carol', 'name': 'Carol'}])
        with patch.object(SocialAccount, 'get_provider_for_operation',
                          return_value=stub):
            sync = XPostSync(self.env, self.account)
            sync.sync_interactions(post, kinds=('retweet',))
            sync.sync_interactions(post, kinds=('retweet',))
        self.assertEqual(self.env['x.post.interaction'].search_count([
            ('stream_post_id', '=', post.id), ('kind', '=', 'retweet')]), 1)

    def test_sync_interactions_reports_unsupported_likes(self):
        stream = self.account._get_or_create_x_posts_stream()
        post = self.env['social.stream.post'].create({
            'stream_id': stream.id, 'x_tweet_id': '111'})
        stub = _StubProvider(likers_unsupported=True)
        with patch.object(SocialAccount, 'get_provider_for_operation',
                          return_value=stub):
            summary = XPostSync(self.env, self.account).sync_interactions(
                post, kinds=('like',))
        self.assertEqual(summary['likes'], 0)
        self.assertTrue(summary['unsupported_notes'])
        self.assertEqual(self.env['x.post.interaction'].search_count(
            [('stream_post_id', '=', post.id), ('kind', '=', 'like')]), 0)

    def test_sync_interactions_without_tweet_id_is_noop(self):
        stream = self.account._get_or_create_x_posts_stream()
        post = self.env['social.stream.post'].create({'stream_id': stream.id})
        summary = XPostSync(self.env, self.account).sync_interactions(post)
        self.assertEqual(summary['comments'], 0)
        self.assertEqual(summary['retweets'], 0)
