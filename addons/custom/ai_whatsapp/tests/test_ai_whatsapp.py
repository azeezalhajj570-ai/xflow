from unittest.mock import patch, MagicMock

from odoo.tests import common, tagged
from odoo.addons.mail.tests.common import MailCommon
from odoo.addons.ai.models.ai_agent import AIAgent
from odoo.addons.whatsapp_evaluation.models.whatsapp_message import WhatsAppMessage
from odoo.tools import html2plaintext


@tagged('post_install', '-at_install', 'ai_whatsapp')
class TestAIWhatsApp(MailCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Never hit the real Evolution API during tests.  This also avoids an
        # Odoo 19 test-framework incompatibility with requests tuple timeouts.
        cls._send_message_patcher = patch.object(WhatsAppMessage, '_send_message', return_value=None)
        cls._send_message_patcher.start()

        # Prevent the AI provider from being called when an inbound message
        # triggers an AI agent in tests that do not explicitly mock it.
        cls._generate_response_patcher = patch.object(
            AIAgent, '_generate_response', return_value=['This is a test response.']
        )
        cls._generate_response_patcher.start()

        cls.wa_account = cls.env['whatsapp.account'].create({
            'name': 'Test WhatsApp Account',
            'base_url': 'https://api.example.com',
            'instance_name': 'test_instance',
            'api_key': 'test_key',
        })

        cls.ai_agent = cls.env['ai.agent'].create({
            'name': 'Test AI Agent',
            'system_prompt': 'You are a test assistant.',
            'llm_model': 'gpt-4o',
            'response_style': 'balanced',
        })

        cls.wa_account.ai_agent_id = cls.ai_agent.id

        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Customer',
            'phone': '+1234567890',
        })

        cls.channel = cls.env['discuss.channel'].create({
            'name': 'Test WhatsApp Channel',
            'channel_type': 'whatsapp',
            'whatsapp_number': '+1234567890',
            'wa_account_id': cls.wa_account.id,
            'whatsapp_partner_id': cls.partner.id,
        })

    @classmethod
    def tearDownClass(cls):
        cls._send_message_patcher.stop()
        cls._generate_response_patcher.stop()
        super().tearDownClass()

    def test_01_ai_agent_assignment(self):
        self.assertEqual(self.wa_account.ai_agent_id, self.ai_agent)
        self.assertIn(self.wa_account, self.ai_agent.whatsapp_account_ids)

    def test_02_ai_agent_access_allowed(self):
        self.assertTrue(self.ai_agent._is_user_access_allowed())

    def test_03_whatsapp_system_context(self):
        context = {'discuss_channel': self.channel}
        messages = self.ai_agent.with_context(context)._build_system_context()

        whatsapp_prompt_found = any('WhatsApp' in msg for msg in messages)
        self.assertTrue(whatsapp_prompt_found, "WhatsApp pre-prompt should be in system context")

    @patch('odoo.addons.ai.models.ai_agent.AIAgent._generate_response')
    def test_04_ai_response_trigger(self, mock_generate):
        mock_generate.return_value = ['Hello! How can I help you?']

        message = self.channel.with_context(whatsapp_inbound_msg_uid='test_msg_123').message_post(
            body='Hello',
            author_id=self.partner.id,
            message_type='whatsapp_message',
            subtype_xmlid='mail.mt_comment',
        )

        mock_generate.assert_called_once()

    def test_05_no_ai_response_for_outbound(self):
        with patch('odoo.addons.ai.models.ai_agent.AIAgent._generate_response') as mock_generate:
            message = self.channel.message_post(
                body='Outbound message',
                message_type='whatsapp_message',
            )

            mock_generate.assert_not_called()

    def test_06_no_ai_response_when_disabled(self):
        self.wa_account.ai_agent_id = False

        with patch('odoo.addons.ai.models.ai_agent.AIAgent._generate_response') as mock_generate:
            message = self.channel.with_context(whatsapp_inbound_msg_uid='test_msg_456').message_post(
                body='Hello',
                author_id=self.partner.id,
                message_type='whatsapp_message',
            )

            mock_generate.assert_not_called()

    # ------------------------------------------------------------------
    # Routing Mode Tests
    # ------------------------------------------------------------------

    def test_07_routing_mode_default_is_ai(self):
        self.assertEqual(self.wa_account.routing_mode, 'ai')

    def test_08_get_handler_ai(self):
        self.wa_account.write({
            'routing_mode': 'ai',
            'ai_agent_id': self.ai_agent.id,
        })
        handler = self.channel._get_whatsapp_handler()
        self.assertEqual(handler, ('ai', self.ai_agent))

    def test_09_get_handler_chatbot(self):
        chatbot_script = self.env['chatbot.script'].create({
            'title': 'Test Script',
            'operator_partner_id': self.env.user.partner_id.id,
        })
        self.wa_account.write({
            'routing_mode': 'chatbot',
            'chatbot_script_id': chatbot_script.id,
        })
        handler = self.channel._get_whatsapp_handler()
        self.assertEqual(handler, ('chatbot', chatbot_script))

    def test_10_get_handler_human(self):
        self.wa_account.write({'routing_mode': 'human'})
        handler = self.channel._get_whatsapp_handler()
        self.assertEqual(handler, ('human', False))

    def test_11_get_handler_no_account(self):
        channel_no_acc = self.env['discuss.channel'].create({
            'name': 'No Account',
            'channel_type': 'whatsapp',
            'whatsapp_number': '+9999999999',
        })
        handler = channel_no_acc._get_whatsapp_handler()
        self.assertFalse(handler)

    # ------------------------------------------------------------------
    # Chatbot Step Engine Tests
    # ------------------------------------------------------------------

    def test_12_chatbot_welcome_steps(self):
        chatbot_script = self.env['chatbot.script'].create({
            'title': 'Welcome Script',
            'operator_partner_id': self.env.user.partner_id.id,
        })
        step = self.env['chatbot.script.step'].create({
            'chatbot_script_id': chatbot_script.id,
            'sequence': 10,
            'step_type': 'text',
            'message': '<p>Welcome to our service!</p>',
        })
        self.wa_account.write({
            'routing_mode': 'chatbot',
            'chatbot_script_id': chatbot_script.id,
        })
        self.channel.write({'current_handler_type': False, 'chatbot_current_step_id': False})

        with patch.object(type(self.channel), '_send_whatsapp_message') as mock_send:
            msg = self.channel.with_context(whatsapp_inbound_msg_uid='wa_uid').message_post(
                body='Hi',
                author_id=self.partner.id,
                message_type='whatsapp_message',
            )
            self.assertEqual(self.channel.chatbot_current_step_id, step)
            mock_send.assert_called_once()

    def test_13_chatbot_answer_matching(self):
        chatbot_script = self.env['chatbot.script'].create({
            'title': 'QA Script',
            'operator_partner_id': self.env.user.partner_id.id,
        })
        step = self.env['chatbot.script.step'].create({
            'chatbot_script_id': chatbot_script.id,
            'sequence': 10,
            'step_type': 'question_selection',
            'message': '<p>Choose an option:</p>',
        })
        answer_a = self.env['chatbot.script.answer'].create({
            'script_step_id': step.id,
            'name': 'Option A',
        })
        answer_b = self.env['chatbot.script.answer'].create({
            'script_step_id': step.id,
            'name': 'Option B',
        })
        next_step = self.env['chatbot.script.step'].create({
            'chatbot_script_id': chatbot_script.id,
            'sequence': 20,
            'step_type': 'text',
            'message': '<p>You chose B</p>',
            'triggering_answer_ids': [(6, 0, [answer_b.id])],
        })

        self.wa_account.write({
            'routing_mode': 'chatbot',
            'chatbot_script_id': chatbot_script.id,
        })
        self.channel.write({'current_handler_type': False, 'chatbot_current_step_id': False})

        with patch.object(type(self.channel), '_send_whatsapp_message') as mock_send:
            self.channel.with_context(whatsapp_inbound_msg_uid='wa_1').message_post(
                body='Hi', author_id=self.partner.id, message_type='whatsapp_message',
            )

        self.assertEqual(self.channel.chatbot_current_step_id, step)

        with patch.object(type(self.channel), '_send_whatsapp_message') as mock_send:
            self.channel.with_context(whatsapp_inbound_msg_uid='wa_2').message_post(
                body='Option B', author_id=self.partner.id, message_type='whatsapp_message',
            )

        self.assertEqual(self.channel.chatbot_current_step_id, next_step)
        self.assertNotEqual(self.channel.current_handler_type, 'human')

    def test_14_chatbot_answer_matching_by_number(self):
        chatbot_script = self.env['chatbot.script'].create({
            'title': 'Number Script',
            'operator_partner_id': self.env.user.partner_id.id,
        })
        step = self.env['chatbot.script.step'].create({
            'chatbot_script_id': chatbot_script.id,
            'sequence': 10,
            'step_type': 'question_selection',
            'message': '<p>Pick:</p>',
        })
        answer_a = self.env['chatbot.script.answer'].create({
            'script_step_id': step.id,
            'name': 'First',
        })
        answer_b = self.env['chatbot.script.answer'].create({
            'script_step_id': step.id,
            'name': 'Second',
        })
        next_step = self.env['chatbot.script.step'].create({
            'chatbot_script_id': chatbot_script.id,
            'sequence': 20,
            'step_type': 'text',
            'message': '<p>Got Second</p>',
            'triggering_answer_ids': [(6, 0, [answer_b.id])],
        })

        self.wa_account.write({
            'routing_mode': 'chatbot',
            'chatbot_script_id': chatbot_script.id,
        })
        self.channel.write({'current_handler_type': False, 'chatbot_current_step_id': False})

        with patch.object(type(self.channel), '_send_whatsapp_message'):
            self.channel.with_context(whatsapp_inbound_msg_uid='w1').message_post(
                body='Hi', author_id=self.partner.id, message_type='whatsapp_message',
            )

        with patch.object(type(self.channel), '_send_whatsapp_message'):
            self.channel.with_context(whatsapp_inbound_msg_uid='w2').message_post(
                body='2', author_id=self.partner.id, message_type='whatsapp_message',
            )

        self.assertEqual(self.channel.chatbot_current_step_id, next_step)

    def test_15_chatbot_script_ends(self):
        chatbot_script = self.env['chatbot.script'].create({
            'title': 'End Script',
            'operator_partner_id': self.env.user.partner_id.id,
        })
        self.env['chatbot.script.step'].create({
            'chatbot_script_id': chatbot_script.id,
            'sequence': 10,
            'step_type': 'text',
            'message': '<p>Welcome</p>',
        })

        self.wa_account.write({
            'routing_mode': 'chatbot',
            'chatbot_script_id': chatbot_script.id,
        })
        self.channel.write({'current_handler_type': False, 'chatbot_current_step_id': False})

        with patch.object(type(self.channel), '_send_whatsapp_message'):
            self.channel.with_context(whatsapp_inbound_msg_uid='w1').message_post(
                body='Hi', author_id=self.partner.id, message_type='whatsapp_message',
            )

        with patch.object(type(self.channel), '_send_whatsapp_message'):
            self.channel.with_context(whatsapp_inbound_msg_uid='w2').message_post(
                body='Thanks', author_id=self.partner.id, message_type='whatsapp_message',
            )

        self.assertEqual(self.channel.current_handler_type, 'human')

    def test_16_chatbot_forward_operator(self):
        chatbot_script = self.env['chatbot.script'].create({
            'title': 'Fwd Script',
            'operator_partner_id': self.env.user.partner_id.id,
        })
        self.env['chatbot.script.step'].create({
            'chatbot_script_id': chatbot_script.id,
            'sequence': 10,
            'step_type': 'text',
            'message': '<p>Welcome</p>',
        })
        fwd_step = self.env['chatbot.script.step'].create({
            'chatbot_script_id': chatbot_script.id,
            'sequence': 20,
            'step_type': 'forward_operator',
            'message': '<p>Forwarding...</p>',
        })
        answer = self.env['chatbot.script.answer'].create({
            'script_step_id': fwd_step.id,
            'name': 'Forward',
        })
        self.env['chatbot.script.step'].create({
            'chatbot_script_id': chatbot_script.id,
            'sequence': 30,
            'step_type': 'text',
            'message': '<p>Never reached</p>',
            'triggering_answer_ids': [(6, 0, [answer.id])],
        })

        self.wa_account.write({
            'routing_mode': 'chatbot',
            'chatbot_script_id': chatbot_script.id,
        })
        self.channel.write({'current_handler_type': False, 'chatbot_current_step_id': False})

        with patch.object(type(self.channel), '_send_whatsapp_message'):
            self.channel.with_context(whatsapp_inbound_msg_uid='w1').message_post(
                body='Hi', author_id=self.partner.id, message_type='whatsapp_message',
            )

        with patch.object(type(self.channel), '_send_whatsapp_message'):
            self.channel.with_context(whatsapp_inbound_msg_uid='w2').message_post(
                body='Thanks', author_id=self.partner.id, message_type='whatsapp_message',
            )

        self.assertEqual(self.channel.current_handler_type, 'human')
        self.assertFalse(self.channel.chatbot_current_step_id)

    # ------------------------------------------------------------------
    # Human Takeover Tests
    # ------------------------------------------------------------------

    def test_17_action_human_takeover(self):
        self.channel.write({'current_handler_type': 'ai'})
        self.channel.action_human_takeover()
        self.assertEqual(self.channel.current_handler_type, 'human')
        self.assertFalse(self.channel.chatbot_current_step_id)

    def test_18_auto_takeover_on_human_reply(self):
        self.channel.write({'current_handler_type': 'ai'})
        self.channel.message_post(
            body='Let me handle this',
            message_type='comment',
            author_id=self.env.user.partner_id.id,
        )
        self.assertEqual(self.channel.current_handler_type, 'human')

    def test_19_no_auto_takeover_from_customer(self):
        self.channel.write({'current_handler_type': 'ai'})
        self.channel.with_context(whatsapp_inbound_msg_uid='inbound').message_post(
            body='Customer text',
            author_id=self.partner.id,
            message_type='whatsapp_message',
        )
        self.assertEqual(self.channel.current_handler_type, 'ai')

    def test_20_no_auto_takeover_for_notification(self):
        self.channel.write({'current_handler_type': 'ai'})
        self.channel.message_post(
            body='System event',
            message_type='notification',
        )
        self.assertEqual(self.channel.current_handler_type, 'ai')

    # ------------------------------------------------------------------
    # _match_whatsapp_answer Tests
    # ------------------------------------------------------------------

    def test_21_match_by_exact_text(self):
        chatbot_script = self.env['chatbot.script'].create({
            'title': 'Match Script',
            'operator_partner_id': self.env.user.partner_id.id,
        })
        step = self.env['chatbot.script.step'].create({
            'chatbot_script_id': chatbot_script.id,
            'sequence': 10,
            'step_type': 'question_selection',
            'message': '<p>Choose:</p>',
        })
        ans = self.env['chatbot.script.answer'].create({
            'script_step_id': step.id,
            'name': 'Option X',
        })
        matched = self.channel._match_whatsapp_answer(step, 'Option X')
        self.assertEqual(matched, ans)

    def test_22_match_by_number(self):
        chatbot_script = self.env['chatbot.script'].create({
            'title': 'Num Script',
            'operator_partner_id': self.env.user.partner_id.id,
        })
        step = self.env['chatbot.script.step'].create({
            'chatbot_script_id': chatbot_script.id,
            'sequence': 10,
            'step_type': 'question_selection',
            'message': '<p>Pick:</p>',
        })
        ans1 = self.env['chatbot.script.answer'].create({
            'script_step_id': step.id,
            'name': 'First',
        })
        ans2 = self.env['chatbot.script.answer'].create({
            'script_step_id': step.id,
            'name': 'Second',
        })
        matched = self.channel._match_whatsapp_answer(step, '2')
        self.assertEqual(matched, ans2)

    def test_23_match_by_partial_text(self):
        chatbot_script = self.env['chatbot.script'].create({
            'title': 'Partial Script',
            'operator_partner_id': self.env.user.partner_id.id,
        })
        step = self.env['chatbot.script.step'].create({
            'chatbot_script_id': chatbot_script.id,
            'sequence': 10,
            'step_type': 'question_selection',
            'message': '<p>Choose:</p>',
        })
        ans = self.env['chatbot.script.answer'].create({
            'script_step_id': step.id,
            'name': 'Check Order Status',
        })
        matched = self.channel._match_whatsapp_answer(step, 'order')
        self.assertEqual(matched, ans)

    def test_24_match_no_answers(self):
        chatbot_script = self.env['chatbot.script'].create({
            'title': 'NoAns Script',
            'operator_partner_id': self.env.user.partner_id.id,
        })
        step = self.env['chatbot.script.step'].create({
            'chatbot_script_id': chatbot_script.id,
            'sequence': 10,
            'step_type': 'text',
            'message': '<p>Just text</p>',
        })
        matched = self.channel._match_whatsapp_answer(step, 'anything')
        self.assertEqual(matched, step.answer_ids)

    # ------------------------------------------------------------------
    # Computed Fields Tests
    # ------------------------------------------------------------------

    def test_25_current_handler_type_persists(self):
        self.channel.write({'current_handler_type': 'ai'})
        self.assertEqual(self.channel.current_handler_type, 'ai')

    def test_26_current_handler_type_cleared_on_human(self):
        self.channel.write({'current_handler_type': 'ai'})
        self.channel.write({'current_handler_type': 'human'})
        self.assertEqual(self.channel.current_handler_type, 'human')

    def test_27_whatsapp_ai_agent_id_computed(self):
        self.channel._compute_whatsapp_ai_agent_id()
        self.assertEqual(self.channel.whatsapp_ai_agent_id, self.ai_agent)

    def test_28_whatsapp_ai_agent_id_empty_on_no_account(self):
        channel_no_acc = self.env['discuss.channel'].create({
            'name': 'No Account WA',
            'channel_type': 'whatsapp',
            'whatsapp_number': '+9999999998',
        })
        channel_no_acc._compute_whatsapp_ai_agent_id()
        self.assertFalse(channel_no_acc.whatsapp_ai_agent_id)

    # ------------------------------------------------------------------
    # Context / Chat History Tests
    # ------------------------------------------------------------------

    def test_29_retrieve_chat_history_converts_html_to_plaintext(self):
        """WhatsApp _retrieve_chat_history must convert HTML body to plain text."""
        # Post two messages — index 0 is the "current" message (skipped),
        # index 1 is what goes into history.
        self.channel.message_post(
            body='<b>Hello</b> <i>world</i>',
            message_type='comment',
            author_id=self.partner.id,
            subtype_xmlid='mail.mt_comment',
        )
        self.channel.message_post(
            body='Current message',
            message_type='comment',
            author_id=self.partner.id,
            subtype_xmlid='mail.mt_comment',
        )
        history = self.ai_agent._retrieve_chat_history(self.channel, no_messages=5)
        self.assertEqual(len(history), 1, "Should have exactly one entry (the older message)")
        entry = history[0]
        self.assertNotIn('<b>', entry['content'])
        self.assertNotIn('</b>', entry['content'])
        self.assertNotIn('<i>', entry['content'])
        self.assertIn('Hello', entry['content'])
        self.assertIn('world', entry['content'])

    def test_30_retrieve_chat_history_no_html_in_whatsapp_messages(self):
        """WhatsApp message bodies with formatting must yield clean plain text."""
        self.channel.message_post(
            body='<p>Order <b>#1234</b> is <i>confirmed</i></p>',
            message_type='comment',
            author_id=self.partner.id,
            subtype_xmlid='mail.mt_comment',
        )
        self.channel.message_post(
            body='Another msg',
            message_type='comment',
            author_id=self.partner.id,
            subtype_xmlid='mail.mt_comment',
        )
        history = self.ai_agent._retrieve_chat_history(self.channel, no_messages=5)
        self.assertEqual(len(history), 1, "Should have exactly one entry (the older message)")
        entry = history[0]
        self.assertNotIn('<b>', entry['content'])
        self.assertNotIn('</b>', entry['content'])
        self.assertNotIn('<p>', entry['content'])
        self.assertIn('Order', entry['content'])
        self.assertIn('#1234', entry['content'])
        self.assertIn('confirmed', entry['content'])

    def test_31_retrieve_chat_history_non_whatsapp_unchanged(self):
        """Non-WhatsApp channels must fall through to base implementation."""
        regular_channel = self.env['discuss.channel'].create({
            'name': 'General',
            'channel_type': 'channel',
        })
        regular_channel.message_post(
            body='<b>bold</b> text',
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )
        regular_channel.message_post(
            body='Current',
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )
        history = self.ai_agent._retrieve_chat_history(regular_channel, no_messages=5)
        self.assertEqual(len(history), 1, "Should have exactly one entry (the older message)")
        entry = history[0]
        content = str(entry['content'])
        # Non-WhatsApp channel should keep markup as stored (message_post
        # escapes HTML tags, so the literal <b> or its escaped entity remains).
        self.assertTrue(
            '<b>' in content or '&lt;b&gt;' in content,
            "Base implementation should not strip HTML tags from non-WhatsApp messages",
        )
        self.assertIn('bold', content)
        self.assertIn('text', content)

    def test_32_retrieve_chat_history_empty_channel(self):
        """Empty channel must return empty history."""
        empty_channel = self.env['discuss.channel'].create({
            'name': 'Empty WA',
            'channel_type': 'whatsapp',
            'whatsapp_number': '+0000000000',
            'wa_account_id': self.wa_account.id,
            'whatsapp_partner_id': self.partner.id,
        })
        history = self.ai_agent._retrieve_chat_history(empty_channel, no_messages=5)
        self.assertEqual(history, [])

    def test_33_build_extra_system_context_includes_customer_info(self):
        """WhatsApp extra system context must include customer name and phone."""
        extra = self.ai_agent._build_extra_system_context(self.channel)
        self.assertIn('Test Customer', extra)
        self.assertIn('+1234567890', extra)

    def test_34_build_extra_system_context_non_whatsapp_empty(self):
        """Non-WhatsApp channel must not include WhatsApp customer info."""
        regular_channel = self.env['discuss.channel'].create({
            'name': 'General',
            'channel_type': 'channel',
        })
        extra = self.ai_agent._build_extra_system_context(regular_channel)
        self.assertNotIn('Customer name', extra)
        self.assertNotIn('Customer phone', extra)
