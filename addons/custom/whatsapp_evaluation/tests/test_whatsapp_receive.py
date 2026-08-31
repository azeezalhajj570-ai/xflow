# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.addons.whatsapp_evaluation.tests.common import WhatsAppEvaluationCommon
from unittest.mock import patch

class TestWhatsAppReceive(WhatsAppEvaluationCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.WebhookController = cls.env['whatsapp_evaluation.account'] # access via model for context, but we testing controller logic really
        # We need to simulate the controller method _handle_messages_upsert ideally, 
        # but that is in a controller class. We can instanciate it or just test the logic that calls _get_whatsapp_channel
        
        # Additional setup: Partner with mobile
        cls.sender_partner = cls.env['res.partner'].create({
            'name': 'Sender',
            'phone': '123456789'
        })

    def test_receive_message_creates_channel_and_notifies(self):
        """ Test that receiving a new message creates a channel and adds notify users """
        
        # Simulate incoming data
        incoming_mobile = '123456789' # Matches sender_partner
        wa_account = self.wa_account
        
        # 1. Call logic to get/create channel (this is what controller calls)
        channel = self.env['discuss.channel']._get_whatsapp_channel(
            incoming_mobile, wa_account, create_if_not_found=True
        )
        
        # 2. Verify Channel Created
        self.assertTrue(channel, "Channel should be created")
        self.assertEqual(channel.channel_type, 'whatsapp')
        self.assertEqual(channel.whatsapp_number, incoming_mobile)
        
        # 3. Verify Members
        member_partners = channel.channel_member_ids.partner_id
        
        # Should include the sender (Partner found by mobile)
        self.assertIn(self.sender_partner, member_partners, "Sender partner should be a member")
        
        # Should include the notify user from account (current user in this test case)
        self.assertIn(self.env.user.partner_id, member_partners, "Notify user should be a member")
        
        # 4. Simulate Message Post (Controller Logic)
        # We manually call message_post as the controller would
        channel.message_post(
            body="Incoming Test Message",
            message_type='whatsapp_message',
            whatsapp_inbound_msg_uid='TEST_UID_123'
        )
        
        # 5. Verify Odoo Message
        last_msg = channel.message_ids[0]
        self.assertEqual(last_msg.body, "<p>Incoming Test Message</p>")
        
        # 6. Verify WhatsApp Message Record created (via explicit create in controller or hook?)
        # Wait, in main.py I saw controller explicitly creates it. 
        # But here I am testing model logic. 
        # If I want to test controller, I'd need to mock request.
        
        # Let's just verify the channel creation logic which was the main fix.
