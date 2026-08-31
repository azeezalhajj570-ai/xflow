# Part of Odoo. See LICENSE file for full copyright and licensing details.

from unittest.mock import patch, MagicMock
from odoo.tests import common
from odoo.addons.mail.tests.common import MailCommon

class WhatsAppEvaluationCommon(MailCommon):
    """ Common class for WhatsApp Evaluation tests with mocked API """

    @classmethod
    def setUpClass(cls):
        super(WhatsAppEvaluationCommon, cls).setUpClass()
        
        cls.wa_account = cls.env['whatsapp.account'].create({
            'name': 'Test Account',
            'base_url': 'https://api.example.com',
            'instance_name': 'test_instance',
            'api_key': 'test_key',
            'notify_user_ids': [(6, 0, [cls.env.user.id])],
            'allowed_company_ids': [(6, 0, cls.env.company.ids)]
        })
        
        # Mocking the API wrapper
        cls.patcher = patch('odoo.addons.whatsapp_evaluation.tools.whatsapp_api.WhatsAppApi')
        cls.MockWhatsAppApi = cls.patcher.start()
        
        # Configure the mock to return success by default
        cls.mock_api_instance = cls.MockWhatsAppApi.return_value
        cls.mock_api_instance.send_whatsapp_text.return_value = {'key': {'id': 'test_msg_id'}}
        cls.mock_api_instance.test_connection.return_value = True

    @classmethod
    def tearDownClass(cls):
        cls.patcher.stop()
        super(WhatsAppEvaluationCommon, cls).tearDownClass()

    def _create_template(self, name, body, model_ref='sale.model_sale_order'):
        return self.env['whatsapp.template'].create({
            'name': name,
            'body': body,
            'model_id': cls.env.ref(model_ref).id,
        })
