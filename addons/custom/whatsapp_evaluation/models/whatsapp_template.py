from odoo import models, fields, api, tools, _
from odoo.tools.safe_eval import safe_eval
import re

class WhatsAppTemplate(models.Model):
    _name = 'whatsapp.template'
    _description = 'WhatsApp Template'

    name = fields.Char(string="Name", required=True)
    body = fields.Text(string="Body", required=True)
    model_id = fields.Many2one('ir.model', string="Applies to", required=True, ondelete='cascade')
    model = fields.Char(related='model_id.model', string="Related Document Model", store=True)
    header_type = fields.Selection([
        ('text', 'Text'),
        ('image', 'Image'),
        ('video', 'Video'),
        ('document', 'Document'),
        ('location', 'Location')
    ], string="Header Type", default="text", required=True)
    report_id = fields.Many2one('ir.actions.report', string="Report", domain="[('model', '=', model)]")
    lang_code = fields.Selection([
        ('en_US', 'English'),
        ('ar_AA', 'Arabic'), # Adjust code if using ar_001 or specific country
        # Add more as needed or use a char field if we want flexibility, but selection strictly suggested
    ], string="Language", help="Language for this template")

    variable_ids = fields.One2many('whatsapp.template.variable', 'wa_template_id', string="Variables")

    def _get_formatted_body(self, variable_values=None):
        self.ensure_one()
        variable_values = variable_values or {}
        body = self.body
        for var in self.variable_ids:
            if var.line_type == 'body':
                body = body.replace(var.name, variable_values.get(f'{var.line_type}-{var.name}', var.demo_value))
        return body

    def _generate_attachment_from_report(self, record):
        self.ensure_one()
        if self.header_type == 'document' and self.report_id:
            try:
                report_content, report_format = self.report_id._render_qweb_pdf(record.id)
                if self.report_id.print_report_name:
                    report_name = safe_eval(self.report_id.print_report_name, {'object': record}) + '.' + report_format
                else:
                    report_name = self.display_name + '.' + report_format
                
                return self.env['ir.attachment'].create({
                    'name': report_name,
                    'raw': report_content, 
                    'mimetype': 'application/pdf',
                    'res_model': record._name,
                    'res_id': record.id,
                })
            except Exception as e:
                # Log error or handle gracefully
                return self.env['ir.attachment']
        return self.env['ir.attachment']

    @api.model
    def _can_use_whatsapp(self, model_name):
        """Check if the model can use WhatsApp (has templates or logic allowed)."""
        return len(self._find_default_for_model(model_name)) > 0

    @api.model
    def _find_default_for_model(self, model_name, lang_code=None):
        domain = [('model', '=', model_name)]
        if lang_code:
            domain.append(('lang_code', '=', lang_code))
        
        template = self.search(domain, limit=1)
        if not template and lang_code:
             # Fallback to any template if specific lang not found, or maybe just English?
             # For now, strict fallback to NO template or just ignoring lang if not found can be risky.
             # Let's try to find an English one or generic one.
             domain = [('model', '=', model_name), ('lang_code', '=', 'en_US')]
             template = self.search(domain, limit=1)
             if not template:
                 # Final fallback: any
                 template = self.search([('model', '=', model_name)], limit=1)
                 
        return template

    def action_send_template(self, record):
        """
        Send this template to the given record.
        Designed for use in Automation Rules.
        """
        self.ensure_one()
        
        # 1. Determine Phone
        phone = False
        partner = False
        if 'mobile' in record and record.mobile:
            phone = record.mobile
        elif 'phone' in record and record.phone:
            phone = record.phone
        elif 'partner_id' in record and record.partner_id:
            partner = record.partner_id
            phone = partner.mobile or partner.phone
            
        if not phone:
            # Cannot send without phone
            return False
            
        # 2. Render Body
        var_values = self.variable_ids._get_variables_value(record)
        body = self._get_formatted_body(variable_values=var_values)
        
        # 3. Generate Attachment
        attachment_ids = []
        attachment = self._generate_attachment_from_report(record)
        if attachment:
            attachment_ids.append(attachment.id)
            
        wa_account = self.env['whatsapp.account'].search([], limit=1)
        if not wa_account:
             return False

        wa_msg = self.env['whatsapp.message'].create({
            'body': body,
            'mobile_number': phone,
            'wa_account_id': wa_account.id,
            'mail_message_id': mail_message.id,
            'message_type': 'outbound',
            'state': 'outgoing',
            'attachment_ids': [(6, 0, attachment_ids)],
            'partner_id': partner.id if partner else False
        })

        wa_msg._send_message()
        return wa_msg
