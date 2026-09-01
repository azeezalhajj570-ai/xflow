from odoo.tests import tagged

from .common import XAccountOmniXTestBase


@tagged('post_install', '-at_install', 'x_account_omnix')
class TestOmniXWebhook(XAccountOmniXTestBase):
    """OmniX webhook event routing + registration actions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'test-encryption-key')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.omnix_api_key', 'omnix_live_test_key')
        cls.account = cls.env['social.account'].create({
            'name': 'Webhook Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'webhook_user',
            'x_provider': 'omnix',
            'x_auth_method': 'session_cookie',
        })

    def _handle(self, event):
        return self.env['discuss.channel'].sudo()._handle_x_webhook_event(
            self.account, event)

    def test_message_received_event_saves_message(self):
        channel = self._handle(
            {
                'type': 'message.received',
                'conversation_id': 'conv-msg-1',
                'sender_id': '777',
                'message_id': 'msg-1',
                'text': 'hello via webhook',
                'created_at': '2026-01-01T00:00:00Z',
            },
        )
        self.assertTrue(channel)
        xm = self.env['x.message'].sudo().search([
            ('external_id', '=', 'msg-1'),
        ], limit=1)
        self.assertTrue(xm)
        self.assertEqual(xm.direction, 'inbound')
        self.assertEqual(xm.body_plain, 'hello via webhook')

    def test_message_sent_event_is_outbound(self):
        self._handle(
            {
                'type': 'message.sent',
                'conversation_id': 'conv-msg-2',
                'sender_id': '777',
                'message_id': 'msg-2',
                'text': 'outbound',
            },
        )
        xm = self.env['x.message'].sudo().search([
            ('external_id', '=', 'msg-2'),
        ], limit=1)
        self.assertTrue(xm)
        self.assertEqual(xm.direction, 'outbound')

    def test_tweet_event_routes_to_channel(self):
        self._handle(
            {
                'type': 'tweet.mention',
                'tweet_id': 'tweet-9',
                'text': 'check this out',
                'author_screen_name': 'alice',
            },
        )
        xm = self.env['x.message'].sudo().search([
            ('external_id', '=', 'tweet-9'),
        ], limit=1)
        self.assertTrue(xm)
        self.assertEqual(xm.body_plain, 'check this out')

    def test_user_follow_event_creates_one_message_per_actor(self):
        self._handle(
            {
                'type': 'user.follow',
                'actor_ids': ['a1', 'a2'],
                'actor_screen_names': ['alice', 'bob'],
            },
        )
        messages = self.env['x.message'].sudo().search([
            ('external_id', 'in', ['follow-a1', 'follow-a2']),
        ])
        self.assertEqual(len(messages), 2)

    def test_action_register_webhook_requires_encryption_code(self):
        with self.assertRaises(ValueError):
            self.account.action_register_webhook()
