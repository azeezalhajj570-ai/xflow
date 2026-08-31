# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, models, fields, _
from odoo.exceptions import ValidationError

class WhatsAppTemplateVariable(models.Model):
    _name = 'whatsapp.template.variable'
    _description = 'WhatsApp Template Variable'
    _order = 'line_type desc, name, id'

    name = fields.Char(string="Placeholder", required=True)
    wa_template_id = fields.Many2one(comodel_name='whatsapp.template', required=True, ondelete='cascade')
    model = fields.Char(string="Model Name", related='wa_template_id.model')

    line_type = fields.Selection([
        ('body', 'Body'),
        ('header', 'Header')], string="Variable location", required=True, default='body')
    
    field_type = fields.Selection([
        ('user_name', 'User Name'),
        ('user_mobile', 'User Mobile'),
        ('free_text', 'Free Text'),
        ('portal_url', 'Portal Link'),
        ('field', 'Field of Model')], string="Type", default='free_text', required=True)
        
    field_name = fields.Char(string="Field")
    demo_value = fields.Char(string="Sample Value", default="Sample Value", required=True)

    def _get_variables_value(self, record):
        value_by_name = {}
        user = self.env.user
        for variable in self:
            if variable.field_type == 'user_name':
                value = user.name
            elif variable.field_type == 'user_mobile':
                value = user.mobile
            elif variable.field_type == 'portal_url':
                try:
                    if hasattr(record, 'get_portal_url'):
                        value = record.get_base_url() + record.get_portal_url()
                    else:
                        value = record.get_base_url()
                except Exception:
                    value = variable.demo_value
            elif variable.field_type == 'field':
                try:
                    vals = record.mapped(variable.field_name)
                    value = vals[0] if vals else None
                    if hasattr(value, 'display_name'):
                        value = value.display_name
                except Exception:
                    value = variable.demo_value
            else:
                value = variable.demo_value

            value_str = str(value) if value is not None and value is not False else ''
            value_by_name[f"{variable.line_type}-{variable.name}"] = value_str

        return value_by_name
