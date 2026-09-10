import json

from odoo import fields
from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestXDMEnqueue(XAccountTestBase):
    """discuss.channel._enqueue_send_dm: generic DM routing to user/group."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'dm-test-key')
        # Do not let the task auto-execution rules fire while we are crafting
        # task records; the enqueue tests only inspect the queue state.
        for xmlid in (
            'x_account.base_automation_x_task_send_dm',
            'x_account.base_automation_x_task_send_group_dm',
        ):
            rule = cls.env.ref(xmlid)
            rule.write({'active': False})
            if not hasattr(cls, '_automation_xmlids'):
                cls._automation_xmlids = []
            cls._automation_xmlids.append(rule)

    @classmethod
    def tearDownClass(cls):
        for rule in reversed(getattr(cls, '_automation_xmlids', [])):
            rule.write({'active': True})
        super().tearDownClass()

    def _make_account(self, handle, status='new'):
        return self.env['social.account'].create({
            'name': handle,
            'media_id': self.twitter_media.id,
            'social_account_handle': handle,
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
            'x_connection_status': status,
        })

    def _make_user_channel(self, account, partner, conv_id):
        return self.env['discuss.channel'].create({
            'channel_type': 'x',
            'x_account_id': account.id,
            'x_partner_id': partner.id,
            'x_conversation_id': conv_id,
            'name': partner.name,
        })

    def test_user_channel_enqueues_send_dm(self):
        account = self._make_account('sender_user')
        partner = self.env['res.partner'].create({
            'name': 'X User',
            'x_user_id': '424242',
            'x_username': 'x_user',
        })
        channel = self._make_user_channel(
            account, partner, '12345-67890')
        task = channel._enqueue_send_dm(text='Hello there')
        self.assertEqual(task.operation, 'send_dm')
        self.assertEqual(task.account_id.id, account.id)
        self.assertEqual(task.status, 'pending')
        self.assertEqual(
            json.loads(task.task_context),
            {'recipient_id': '424242', 'text': 'Hello there'})

    def test_group_channel_enqueues_send_group_dm(self):
        account = self._make_account('sender_group')
        channel = self.env['discuss.channel'].create({
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': 'g-conv-7',
            'name': 'X Group',
        })
        task = channel._enqueue_send_dm(text='Group update')
        self.assertEqual(task.operation, 'send_group_dm')
        self.assertEqual(task.account_id.id, account.id)
        self.assertEqual(
            json.loads(task.task_context),
            {'conversation_id': 'g-conv-7', 'text': 'Group update'})

    def test_default_text_fallback(self):
        account = self._make_account('sender_default')
        channel = self.env['discuss.channel'].create({
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': 'g-conv-8',
            'name': 'X Group',
        })
        task = channel._enqueue_send_dm()
        self.assertIn('text', json.loads(task.task_context))

    def test_requires_conversation_id(self):
        account = self._make_account('sender_noconv')
        partner = self.env['res.partner'].create({
            'name': 'X User',
            'x_user_id': '777',
        })
        channel = self._make_user_channel(account, partner, 'pre-created')
        channel.write({'x_conversation_id': False})
        with self.assertRaises(ValueError):
            channel._enqueue_send_dm(text='Hi')

    def test_requires_recipient_for_user_channel(self):
        account = self._make_account('sender_norecipient')
        partner = self.env['res.partner'].create({'name': 'No XID'})
        channel = self._make_user_channel(
            account, partner, '11111-22222')
        with self.assertRaises(ValueError):
            channel._enqueue_send_dm(text='Hi')

    def test_rejects_non_x_channel(self):
        channel = self.env['discuss.channel'].create({
            'channel_type': 'channel',
            'name': 'Internal',
        })
        with self.assertRaises(ValueError):
            channel._enqueue_send_dm(text='Hi')

    def test_disabled_account_raises(self):
        account = self._make_account('sender_disabled', status='disabled')
        channel = self.env['discuss.channel'].create({
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': 'g-conv-9',
            'name': 'X Group',
        })
        with self.assertRaises(ValueError):
            channel._enqueue_send_dm(text='Hi')
        self.assertFalse(
            self.env['x.account.task'].search([('account_id', '=', account.id)]))

    def test_x_message_helper_routes_to_channel(self):
        account = self._make_account('sender_msg')
        partner = self.env['res.partner'].create({
            'name': 'X User',
            'x_user_id': '55555',
        })
        channel = self._make_user_channel(account, partner, '33333-44444')
        msg = self.env['x.message'].create({
            'channel_id': channel.id,
            'account_id': account.id,
            'direction': 'inbound',
            'external_id': 'evt-1',
            'body_plain': 'hello',
            'external_created_at': fields.Datetime.now(),
        })
        task = msg._run_channel_send_dm(text='Auto reply')
        self.assertEqual(task.operation, 'send_dm')
        self.assertEqual(
            json.loads(task.task_context),
            {'recipient_id': '55555', 'text': 'Auto reply'})