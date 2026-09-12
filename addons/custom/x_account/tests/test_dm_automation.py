import json
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase
from odoo.addons.x_account.services.providers.session_web import SessionWebProvider


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

    def test_mistyped_group_one_to_one_enqueues_send_dm(self):
        """A 1:1 stored as x_group (GetXAPI ``<id>:<id>`` id) sends via send_dm."""
        account = self._make_account('sender_mistyped')
        account.write({'twitter_user_id': '111'})
        channel = self.env['discuss.channel'].create({
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': '111:222',
            'name': 'GetXAPI 1:1',
        })
        task = channel._enqueue_send_dm(text='Hi there')
        self.assertEqual(task.operation, 'send_dm')
        self.assertEqual(task.account_id.id, account.id)
        self.assertEqual(
            json.loads(task.task_context),
            {'recipient_id': '222', 'text': 'Hi there'})

    def test_mistyped_group_hyphen_one_to_one_enqueues_send_dm(self):
        """A 1:1 with the official ``<id>-<id>`` shape but x_group type also sends."""
        account = self._make_account('sender_mistyped_hyphen')
        account.write({'twitter_user_id': '111'})
        channel = self.env['discuss.channel'].create({
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': '111-222',
            'name': 'GetXAPI 1:1 hyphen',
        })
        task = channel._enqueue_send_dm(text='Hi there')
        self.assertEqual(task.operation, 'send_dm')
        self.assertEqual(
            json.loads(task.task_context),
            {'recipient_id': '222', 'text': 'Hi there'})

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

    def test_channel_action_send_message_opens_composer(self):
        account = self._make_account('sender_page')
        partner = self.env['res.partner'].create({
            'name': 'X User',
            'x_user_id': '66666',
        })
        channel = self._make_user_channel(account, partner, 'abc-123')
        action = channel.action_send_message()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'x.message.composer')
        self.assertEqual(action['context']['default_channel_id'], channel.id)
        self.assertEqual(action['target'], 'new')

    def test_channel_action_send_message_rejects_non_x(self):
        channel = self.env['discuss.channel'].create({
            'channel_type': 'channel',
            'name': 'Internal',
        })
        with self.assertRaises(ValueError):
            channel.action_send_message()

    def test_composer_default_get_prefills_channel(self):
        account = self._make_account('sender_defaultget')
        partner = self.env['res.partner'].create({
            'name': 'X User',
            'x_user_id': '77777',
        })
        channel = self._make_user_channel(account, partner, 'conv-1')
        composer = self.env['x.message.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'body': 'hi'})
        self.assertEqual(composer.channel_id.id, channel.id)

    def test_composer_send_dm_calls_provider(self):
        account = self._make_account('sender_comp_dm')
        partner = self.env['res.partner'].create({
            'name': 'X User',
            'x_user_id': '88888',
        })
        channel = self._make_user_channel(account, partner, 'conv-dm')
        composer = self.env['x.message.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'body': 'Hello DM'})
        with patch.object(SessionWebProvider, 'send_dm',
                          return_value={'success': True}) as mock_send:
            result = composer.action_send()
        mock_send.assert_called_once_with(
            recipient_id='88888', text='Hello DM')
        self.assertEqual(result['tag'], 'display_notification')
        self.assertEqual(result['params']['type'], 'success')

    def test_composer_send_group_dm_calls_provider(self):
        account = self._make_account('sender_comp_group')
        channel = self.env['discuss.channel'].create({
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': 'g-conv-group-1',
            'name': 'X Group',
        })
        composer = self.env['x.message.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'body': 'Group hello'})
        with patch.object(SessionWebProvider, 'send_group_dm',
                          return_value={'success': True}, create=True) as mock_send:
            result = composer.action_send()
        mock_send.assert_called_once_with(
            conversation_id='g-conv-group-1', text='Group hello')
        self.assertEqual(result['params']['type'], 'success')

    def test_composer_mistyped_group_one_to_one_uses_send_dm(self):
        """Composer treats a colon-id x_group as 1:1 and calls send_dm."""
        account = self._make_account('sender_comp_mistyped')
        account.write({'twitter_user_id': '111'})
        channel = self.env['discuss.channel'].create({
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': '111:222',
            'name': 'GetXAPI 1:1',
        })
        composer = self.env['x.message.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'body': 'Hello DM'})
        with patch.object(SessionWebProvider, 'send_dm',
                          return_value={'success': True}) as mock_send:
            result = composer.action_send()
        mock_send.assert_called_once_with(recipient_id='222', text='Hello DM')
        self.assertEqual(result['params']['type'], 'success')

    def test_composer_send_failure_surfaces_error(self):
        account = self._make_account('sender_comp_fail')
        partner = self.env['res.partner'].create({
            'name': 'X User',
            'x_user_id': '99999',
        })
        channel = self._make_user_channel(account, partner, 'conv-fail')
        composer = self.env['x.message.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'body': 'Hello'})
        with patch.object(SessionWebProvider, 'send_dm',
                          side_effect=Exception('Too Many Requests')):
            result = composer.action_send()
        self.assertEqual(result['params']['type'], 'danger')
        self.assertIn('Too Many Requests', result['params']['message'])

    def test_composer_requires_body(self):
        account = self._make_account('sender_comp_nobody')
        partner = self.env['res.partner'].create({
            'name': 'X User',
            'x_user_id': '101010',
        })
        channel = self._make_user_channel(account, partner, 'conv-nobody')
        composer = self.env['x.message.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'body': '   '})
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            composer.action_send()