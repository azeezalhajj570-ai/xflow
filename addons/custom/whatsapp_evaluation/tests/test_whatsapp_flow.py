# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.tests import common
from unittest.mock import patch

class TestWhatsAppFlow(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super(TestWhatsAppFlow, cls).setUpClass()
        cls.Composer = cls.env['whatsapp.composer']
        cls.Account = cls.env['whatsapp.account']
        
        # Setup Account
        cls.wa_account = cls.Account.create({
            'name': 'Test Account',
            'base_url': 'https://api.example.com',
            'instance_name': 'test_instance',
            'api_key': 'test_key',
            'notify_user_ids': [(6, 0, [cls.env.user.id])]
        })
        
        # Setup Sale Order
        cls.partner = cls.env['res.partner'].create({'name': 'Test Customer', 'phone': '+1234567890'})
        cls.sale_order = cls.env['sale.order'].create({
            'partner_id': cls.partner.id,
            'name': 'SO/FLOW/001'
        })

    def test_composer_default_get(self):
        """ Test that composer opens with correct default values and context """
        context = {
            'active_model': 'sale.order',
            'active_id': self.sale_order.id,
            'active_ids': [self.sale_order.id],
        }
        defaults = self.Composer.with_context(context).default_get(['res_model', 'res_id', 'wa_account_id', 'phone', 'body'])
        
        self.assertEqual(defaults['res_model'], 'sale.order')
        self.assertEqual(defaults['res_id'], self.sale_order.id)
        self.assertEqual(defaults['wa_account_id'], self.wa_account.id)
        self.assertEqual(defaults['phone'], '+1234567890')
        # Check if body is pre-filled (assuming default template logic or fallback)
        self.assertTrue(defaults.get('body'))

    @patch('odoo.addons.whatsapp_evaluation.tools.whatsapp_api.WhatsAppApi.send_whatsapp_text')
    def test_send_message_flow(self, mock_send):
        """ Test sending a message via composer creation """
        # Mock successful API response
        mock_send.return_value = {'key': {'id': 'test_msg_id'}}
        
        composer = self.Composer.create({
            'res_model': 'sale.order',
            'res_id': self.sale_order.id,
            'wa_account_id': self.wa_account.id,
            'phone': '+1234567890',
            'body': 'Test Message content'
        })
        
        composer.action_send_whatsapp()
        
        # Verify message created (linked via mail_message)
        mail_message = self.env['mail.message'].search([
            ('model', '=', 'sale.order'), 
            ('res_id', '=', self.sale_order.id)
        ], limit=1, order='id DESC')
        message = self.env['whatsapp.message'].search([
            ('mail_message_id', '=', mail_message.id)
        ], limit=1)
        self.assertTrue(message)
        self.assertEqual(message.body, 'Test Message content')
        self.assertEqual(message.state, 'sent')
        
        mock_send.assert_called_once()
