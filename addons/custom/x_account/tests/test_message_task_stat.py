from odoo import fields
from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase

TWEET_ID = '123456789'


@tagged('post_install', '-at_install', 'x_account')
class TestMessageTaskLink(XAccountTestBase):
    """A message keeps the tasks it spawned, so its form can show them.

    Tasks used to record their origin only inside ``task_context`` (JSON), which
    the message form cannot resolve. The message automation now stamps
    ``message_id``, and the form exposes a stat button over it.
    """

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
            'x_connection_state': 'active',
        })

    def _make_channel(self, account, conversation_id=None, suffix='1'):
        return self.env['discuss.channel'].create({
            'channel_type': 'x',
            'x_account_id': account.id,
            'x_conversation_id': conversation_id or 'cv-%s-%s' % (
                account.id, suffix),
            'name': 'X chat',
            'x_auto_comment_text': 'Nice post!',
        })

    def _make_dm_channel(self, account, conversation_id='12345-67890'):
        """A 1:1 conversation with a resolvable recipient, so a DM task can be
        enqueued from it (``_x_dm_recipient_user_id`` reads the partner)."""
        partner = self.env['res.partner'].create({
            'name': 'X User',
            'x_user_id': '424242',
            'x_username': 'x_user',
        })
        channel = self._make_channel(account, conversation_id=conversation_id)
        channel.write({'x_partner_id': partner.id})
        return channel

    def _make_message(self, account, channel, external_id='evt-1',
                      body=None, username='azeez'):
        return self.env['x.message'].create({
            'channel_id': channel.id,
            'account_id': account.id,
            'direction': 'inbound',
            'external_id': external_id,
            'body_plain': body or 'Check https://x.com/status/%s' % TWEET_ID,
            'author_x_id': '999',
            'author_x_username': username,
            'external_created_at': fields.Datetime.now(),
        })

    # ------------------------------------------------------------- stamping
    def test_engagement_task_points_back_at_its_message(self):
        account = self._make_account('link_engagement')
        message = self._make_message(account, self._make_channel(account))
        message._run_channel_like()
        self.assertEqual(len(message.task_ids), 1)
        self.assertEqual(message.task_ids.operation, 'like')
        self.assertEqual(message.task_ids.message_id, message)
        self.assertEqual(message.task_count, 1)

    def test_follow_task_points_back_at_its_message(self):
        account = self._make_account('link_follow')
        message = self._make_message(account, self._make_channel(account))
        message._run_channel_follow()
        self.assertEqual(message.task_ids.operation, 'follow')
        self.assertEqual(message.task_ids.message_id, message)

    def test_send_dm_task_points_back_at_its_message(self):
        account = self._make_account('link_dm')
        channel = self._make_dm_channel(account)
        message = self._make_message(account, channel)
        task = message._run_channel_send_dm(text='Hi')
        self.assertEqual(task.operation, 'send_dm')
        self.assertEqual(task.message_id, message)

    def test_duplicate_message_does_not_own_the_task(self):
        """The task belongs to the message that created it, not to a later
        message linking the same tweet."""
        account = self._make_account('link_duplicate')
        channel = self._make_channel(account)
        first = self._make_message(account, channel, external_id='evt-first')
        second = self._make_message(account, channel, external_id='evt-second')
        first._run_channel_like()
        second._run_channel_like()
        self.assertEqual(first.task_count, 1)
        self.assertEqual(second.task_count, 0)

    def test_channel_enqueue_without_a_message_stays_unlinked(self):
        """The channel-level server action has no message to attribute."""
        account = self._make_account('link_channel_only')
        channel = self._make_dm_channel(account)
        task = channel._enqueue_send_dm(text='Hi')
        self.assertFalse(task.message_id)

    # -------------------------------------------------------------- stat UI
    def test_stat_action_opens_only_this_message_tasks(self):
        account = self._make_account('link_action')
        channel = self._make_channel(account)
        message = self._make_message(account, channel)
        other = self._make_message(
            account, self._make_channel(account, suffix='2'),
            external_id='evt-other')
        message._run_channel_like()
        other._run_channel_like()
        action = message.action_view_tasks()
        self.assertEqual(action['res_model'], 'x.account.task')
        self.assertEqual(action['domain'], [('message_id', '=', message.id)])
        self.assertEqual(action['views'], [(False, 'list')])
        # The task action defaults to "Last Hour"; the stat must not, or older
        # tasks of the message would be filtered out of its own list.
        self.assertEqual(action['context'], {})

    def test_message_form_exposes_the_task_stat_button(self):
        arch = self.env.ref('x_account.x_message_view_form').arch
        self.assertIn('name="action_view_tasks"', arch)
        self.assertIn('name="task_count"', arch)
        self.assertIn('widget="statinfo"', arch)
        self.assertIn('oe_stat_button', arch)

    def test_task_form_shows_the_source_message(self):
        arch = self.env.ref('x_account.x_account_task_view_form').arch
        self.assertIn('name="message_id"', arch)
