# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.addons.whatsapp_evaluation.tests.common import WhatsAppEvaluationCommon

class TestWhatsAppTemplate(WhatsAppEvaluationCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Test Partner'})
        cls.sale_order = cls.env['sale.order'].create({
            'partner_id': cls.partner.id,
            'name': 'SO/TEST/001'
        })
        
        cls.template = cls.env['whatsapp.template'].create({
            'name': 'Test Template',
            'body': 'Hello {{1}}, order {{2}} is ready.',
            'model_id': cls.env.ref('sale.model_sale_order').id,
        })
        
        cls.env['whatsapp.template.variable'].create({
            'wa_template_id': cls.template.id,
            'name': '{{1}}',
            'line_type': 'body',
            'field_type': 'field',
            'field_name': 'partner_id',
            'demo_value': 'Demo User'
        })
        cls.env['whatsapp.template.variable'].create({
            'wa_template_id': cls.template.id,
            'name': '{{2}}',
            'line_type': 'body',
            'field_type': 'field',
            'field_name': 'name',
            'demo_value': 'SO/000'
        })

    def test_variable_substitution(self):
        """ Test that variables are correctly replaced with record values """
        var_values = self.template.variable_ids._get_variables_value(self.sale_order)
        formatted_body = self.template._get_formatted_body(variable_values=var_values)
        
        self.assertIn('Test Partner', formatted_body)
        self.assertIn('SO/TEST/001', formatted_body)
        self.assertEqual(formatted_body, 'Hello Test Partner, order SO/TEST/001 is ready.')

    def test_variable_demo_fallback(self):
        """ Test fallback to demo value """
        var_values = {}
        for var in self.template.variable_ids:
            var_values[f"{var.line_type}-{var.name}"] = var.demo_value
            
        formatted_body = self.template._get_formatted_body(variable_values=var_values)
        self.assertIn('Demo User', formatted_body)
        self.assertIn('SO/000', formatted_body)
