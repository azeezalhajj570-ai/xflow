import json

from odoo import fields
from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestChannelActionToggles(XAccountTestBase):
    """Per-chat engagement toggles (discuss.channel.x_auto_*).

    Each action has one base_automation rule on x.message whose filter_domain
    requires the matching toggle on the message's conversation, so a chat opts
    in/out of like/repost/comment/bookmark/follow on its own form. The rule
    gates through ``Model.filtered_domain`` (the on_time_created path), which is
    what ``base.automation._filter_post`` applies here — same pattern as
    ``test_message_age_filter``.
    """

    TOGGLES = {
        'like': 'x_auto_like',
        'repost': 'x_auto_repost',
        'comment': 'x_auto_comment',
        'bookmark': 'x_auto_bookmark',
        'follow': 'x_auto_follow',
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['social.account'].create({
            'name': 'toggle_account',
            'media_id': cls.env.ref('social_twitter.social_media_twitter').id,
            'social_account_handle': 'toggle_account',
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
            'x_connection_status': 'active',
        })

    def _channel(self, suffix, **toggles):
        vals = {
            'channel_type': 'x',
            'x_account_id': self.account.id,
            'x_conversation_id': 'toggle-conv-%s' % suffix,
            'name': 'Toggle %s' % suffix,
        }
        vals.update(toggles)
        return self.env['discuss.channel'].create(vals)

    def _message(self, channel, external_id):
        return self.env['x.message'].create({
            'channel_id': channel.id,
            'account_id': self.account.id,
            'direction': 'inbound',
            'external_id': external_id,
            'body_plain': 'See https://x.com/someone/status/123456789',
            'author_x_id': '999',
            'author_x_username': 'azeez',
            'external_created_at': fields.Datetime.now(),
        })

    def _rule(self, operation):
        return self.env.ref(
            'x_account.base_automation_x_chat_auto_%s' % operation)

    def test_toggle_fields_default_false(self):
        channel = self._channel('default')
        for field_name in self.TOGGLES.values():
            self.assertFalse(channel[field_name], field_name)

    def test_rules_active_and_bound(self):
        for operation in self.TOGGLES:
            with self.subTest(operation=operation):
                rule = self._rule(operation)
                self.assertTrue(rule.active)
                self.assertTrue(rule.action_server_ids)

    def test_action_rule_filters_by_channel_toggle(self):
        """Only the message whose chat has the toggle on passes the rule."""
        for operation, field_name in self.TOGGLES.items():
            with self.subTest(operation=operation):
                enabled = self._channel(
                    '%s-on' % operation, **{field_name: True})
                disabled = self._channel('%s-off' % operation)
                msg_on = self._message(enabled, 'evt-%s-on' % operation)
                msg_off = self._message(disabled, 'evt-%s-off' % operation)
                passed = self._rule(operation)._filter_post(msg_on | msg_off)[0]
                self.assertIn(msg_on, passed)
                self.assertNotIn(msg_off, passed)

    def test_comment_task_carries_channel_text(self):
        """The per-chat Auto Comment Text is what the reply task posts."""
        channel = self._channel(
            'comment-text', x_auto_comment=True,
            x_auto_comment_text='  Great point!  ')
        msg = self._message(channel, 'evt-comment-text')
        msg._run_channel_comment()
        task = self.env['x.account.task'].search([
            ('account_id', '=', self.account.id),
            ('operation', '=', 'comment'),
        ], limit=1)
        self.assertTrue(task)
        ctx = json.loads(task.task_context)
        self.assertEqual(ctx.get('text'), 'Great point!')
        self.assertEqual(ctx.get('tweet_id'), '123456789')

    def test_comment_skipped_without_text(self):
        """Auto Comment without text must not queue a doomed reply."""
        channel = self._channel('comment-notext', x_auto_comment=True)
        msg = self._message(channel, 'evt-comment-notext')
        msg._run_channel_comment()
        self.assertFalse(self.env['x.account.task'].search([
            ('account_id', '=', self.account.id),
            ('operation', '=', 'comment'),
        ]))

    def test_form_shows_toggles(self):
        arch = self.env.ref('x_account.x_group_channel_view_form').arch
        for field_name in self.TOGGLES.values():
            self.assertIn(field_name, arch)

    def test_list_shows_toggles(self):
        arch = self.env.ref('x_account.x_group_channel_view_tree').arch
        for field_name in self.TOGGLES.values():
            self.assertIn(field_name, arch)
