# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.addons.whatsapp_evaluation.tests.common import WhatsAppEvaluationCommon
from odoo import Command

class TestWhatsAppComposerFormatting(WhatsAppEvaluationCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Test Partner', 'phone': '+1234567890'})
        
    def test_composer_formatting(self):
        """ Test that the composer correctly formats newlines and markdown for Odoo Chatter """
        
        raw_body = "Hello World\nThis is a *bold* statement.\nAnd this is _italics_."
        
        composer = self.env['whatsapp.composer'].create({
            'res_model': 'res.partner',
            'res_id': self.partner.id,
            'phone': self.partner.phone,
            'wa_account_id': self.wa_account.id,
            'body': raw_body,
        })
        
        composer.action_send_whatsapp()
        
        # Check the last message on the partner
        message = self.partner.message_ids[0]
        
        # Verify HTML formatting
        self.assertIn('Hello World<br>', message.body) # plaintext2html usually adds <br> or <br/>
        self.assertIn('<b>bold</b>', message.body)
        self.assertIn('<i>italics</i>', message.body)
        
        # Verify plain text preserved for WhatsApp message
        wa_msg = self.env['whatsapp.message'].search([('mail_message_id', '=', message.id)])
        self.assertEqual(wa_msg.body, raw_body)
