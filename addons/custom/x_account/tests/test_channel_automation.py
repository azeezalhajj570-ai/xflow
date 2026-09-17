from odoo import fields
from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestChannelAutomationArchivedScope(XAccountTestBase):
    """Archiving an account or a group (chat) channel must stop the
    message-model automation rules (like/repost/comment/bookmark/...) from
    running any task for it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')

    def _make_account(self, handle, status='active'):
        return self.env['social.account'].create({
            'name': handle,
            'media_id': self.twitter_media.id,
            'social_account_handle': handle,
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
            'x_connection_status': status,
        })

    def _make_channel(self, account):
        return self.env['discuss.channel'].create({
            'channel_type': 'x',
            'x_account_id': account.id,
            'x_conversation_id': 'cv-%s' % account.id,
            'name': 'X chat',
        })

    def _make_message(self, account, channel, body='Check https://x.com/status/123456789'):
        return self.env['x.message'].create({
            'channel_id': channel.id,
            'account_id': account.id,
            'direction': 'inbound',
            'external_id': 'evt-%s' % channel.id,
            'body_plain': body,
            'external_created_at': fields.Datetime.now(),
        })

    def test_archived_account_does_not_run_like(self):
        account = self._make_account('archived_like')
        msg = self._make_message(account, self._make_channel(account))
        account.write({'active': False})
        msg._run_channel_like()
        self.assertFalse(self.env['x.account.task'].search([
            ('operation', '=', 'like'),
            ('account_id', '=', account.id),
        ]))

    def test_archived_channel_does_not_run_repost(self):
        account = self._make_account('archived_channel')
        channel = self._make_channel(account)
        msg = self._make_message(account, channel)
        channel.write({'active': False})
        msg._run_channel_repost()
        self.assertFalse(self.env['x.account.task'].search([
            ('operation', '=', 'repost'),
            ('account_id', '=', account.id),
        ]))

    def test_archived_account_does_not_fallback_to_company_account(self):
        """Regression: an archived account must stop the automation entirely
        instead of silently rerouting it to another company X account."""
        archived = self._make_account('archived_no_fallback')
        self._make_account('fallback_target', status='active')
        msg = self._make_message(archived, self._make_channel(archived))
        archived.write({'active': False})
        msg._run_channel_like()
        self.assertFalse(self.env['x.account.task'].search([
            ('operation', '=', 'like'),
        ]))

    def test_archived_channel_skips_send_dm(self):
        account = self._make_account('archived_dm', status='active')
        channel = self._make_channel(account)
        msg = self._make_message(account, channel)
        channel.write({'active': False})
        self.assertFalse(msg._run_channel_send_dm(text='Hi'))
        self.assertFalse(self.env['x.account.task'].search([
            ('account_id', '=', account.id),
        ]))


@tagged('post_install', '-at_install', 'x_account')
class TestChannelAutomationDedup(XAccountTestBase):
    """One task per target: repeating an engagement must not spawn another.

    A recurring link re-posts the same tweet every hour. X rejects the repeat
    retweet, so the extra task burned every attempt and ended ``failed`` for
    work that had already been done.
    """

    TWEET_ID = '123456789'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')

    def _make_account(self, handle):
        return self.env['social.account'].create({
            'name': handle,
            'media_id': self.twitter_media.id,
            'social_account_handle': handle,
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
            'x_connection_status': 'active',
        })

    def _make_channel(self, account, suffix='1'):
        return self.env['discuss.channel'].create({
            'channel_type': 'x',
            'x_account_id': account.id,
            'x_conversation_id': 'cv-%s-%s' % (account.id, suffix),
            'name': 'X chat',
        })

    def _make_message(self, account, channel, external_id, body=None,
                      username=None):
        return self.env['x.message'].create({
            'channel_id': channel.id,
            'account_id': account.id,
            'direction': 'inbound',
            'external_id': external_id,
            'body_plain': body or 'Check https://x.com/status/%s' % self.TWEET_ID,
            'author_x_id': '999',
            'author_x_username': username,
            'external_created_at': fields.Datetime.now(),
        })

    def _tasks(self, account, operation):
        return self.env['x.account.task'].search([
            ('account_id', '=', account.id),
            ('operation', '=', operation),
        ])

    def test_repeat_tweet_creates_one_task_per_operation(self):
        """The same tweet twice must not queue the engagement twice."""
        account = self._make_account('dedup_ops')
        channel = self._make_channel(account)
        first = self._make_message(account, channel, 'evt-ops-1')
        second = self._make_message(account, channel, 'evt-ops-2')
        for operation in ('like', 'repost', 'comment', 'bookmark', 'unbookmark'):
            with self.subTest(operation=operation):
                first._run_channel_automation(operation)
                second._run_channel_automation(operation)
                tasks = self._tasks(account, operation)
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks.target_post_id, self.TWEET_ID)

    def test_repeat_follow_creates_one_task(self):
        """``follow`` has no post id and dedups on the screen name."""
        account = self._make_account('dedup_follow')
        channel = self._make_channel(account)
        first = self._make_message(account, channel, 'evt-follow-1',
                                   username='azeez')
        self._make_message(account, channel, 'evt-follow-2', username='azeez')
        first._run_channel_automation('follow')
        self.env['x.message'].search([
            ('external_id', '=', 'evt-follow-2')])._run_channel_automation(
                'follow')
        tasks = self._tasks(account, 'follow')
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks.target_screen_name, 'azeez')

    def test_succeeded_task_blocks_a_new_one(self):
        account = self._make_account('dedup_succeeded')
        channel = self._make_channel(account)
        msg = self._make_message(account, channel, 'evt-succ-1')
        first = self.env['x.account.task'].create({
            'account_id': account.id,
            'operation': 'repost',
            'status': 'success',
            'task_context': '{"post": {"post_id": "%s"}}' % self.TWEET_ID,
        })
        msg._run_channel_automation('repost')
        self.assertEqual(len(self._tasks(account, 'repost')), 1)
        self.assertEqual(self._tasks(account, 'repost'), first)

    def test_failed_task_still_allows_a_retry(self):
        """``failed`` must not block: a genuine retry still gets one task."""
        account = self._make_account('dedup_failed')
        channel = self._make_channel(account)
        msg = self._make_message(account, channel, 'evt-fail-1')
        self.env['x.account.task'].create({
            'account_id': account.id,
            'operation': 'repost',
            'status': 'failed',
            'task_context': '{"post": {"post_id": "%s"}}' % self.TWEET_ID,
        })
        msg._run_channel_automation('repost')
        self.assertEqual(len(self._tasks(account, 'repost')), 2)

    def test_two_accounts_get_their_own_task(self):
        first_account = self._make_account('dedup_acc_a')
        second_account = self._make_account('dedup_acc_b')
        first_msg = self._make_message(
            first_account, self._make_channel(first_account, 'a'), 'evt-acc-a')
        second_msg = self._make_message(
            second_account, self._make_channel(second_account, 'b'), 'evt-acc-b')
        first_msg._run_channel_automation('repost')
        second_msg._run_channel_automation('repost')
        self.assertEqual(len(self._tasks(first_account, 'repost')), 1)
        self.assertEqual(len(self._tasks(second_account, 'repost')), 1)