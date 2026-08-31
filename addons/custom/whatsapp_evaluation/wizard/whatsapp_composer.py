# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api, _, tools
from odoo.exceptions import UserError
import re

class WhatsAppComposer(models.TransientModel):
    _name = 'whatsapp.composer'
    _description = 'Send WhatsApp Wizard'

    res_model = fields.Char('Document Model Name', required=True)
    res_id = fields.Integer('Document ID', required=True)
    
    phone = fields.Char(string="Phone Number", required=True)
    wa_account_id = fields.Many2one('whatsapp.account', string="WhatsApp Account", required=True)
    
    body = fields.Text(string="Message", required=True)
    attachment_ids = fields.Many2many('ir.attachment', string="Attachments")
    
    @api.model
    def default_get(self, fields):
        result = super().default_get(fields)
        if self.env.context.get('active_model') and self.env.context.get('active_id'):
            result['res_model'] = self.env.context['active_model']
            result['res_id'] = self.env.context['active_id']
            
            record = self.env[result['res_model']].browse(result['res_id'])
            if 'phone' in record:
                result['phone'] = record.phone
            elif 'partner_id' in record and record.partner_id.phone:
                result['phone'] = record.partner_id.phone
                
            account = self.env['whatsapp.account'].search([], limit=1)
            if account:
                result['wa_account_id'] = account.id

            lang_code = 'en_US'
            if 'partner_id' in record and record.partner_id.lang:
                lang_code = record.partner_id.lang

            template = self.env['whatsapp.template']._find_default_for_model(result['res_model'], lang_code=lang_code)
            if template:
                var_values = template.variable_ids._get_variables_value(record)
                result['body'] = template._get_formatted_body(variable_values=var_values)
                
                # Dynamic Attachment
                attachment = template._generate_attachment_from_report(record)
                if attachment:
                    result['attachment_ids'] = [(4, attachment.id)]
        return result

    def action_send_whatsapp(self):
        self.ensure_one()
        
        # Format body for Odoo Chatter (HTML)
        # 1. Convert newlines to <br/> and linkify URLs
        body_html = tools.plaintext2html(self.body)
        
        # 2. Basic Markdown to HTML conversion
        # Format *bold*
        body_html = re.sub(r'\*([^*]+)\*', r'<b>\1</b>', body_html)
        # Format _italics_
        body_html = re.sub(r'_([^_]+)_', r'<i>\1</i>', body_html)
        
        # Create message linked to the document
        mail_message = self.env['mail.message'].create({
            'model': self.res_model,
            'res_id': self.res_id,
            'body': body_html,
            'message_type': 'comment',
            'subtype_id': self.env.ref('mail.mt_comment').id,
            'attachment_ids': [(6, 0, self.attachment_ids.ids)]
        })
        
        wa_msg = self.env['whatsapp.message'].create({
            'body': self.body,
            'mobile_number': self.phone,
            'wa_account_id': self.wa_account_id.id,
            'mail_message_id': mail_message.id,
            'message_type': 'outbound',
            'state': 'outgoing',
            'attachment_ids': [(6, 0, self.attachment_ids.ids)]
        })
        
        wa_msg._send_message()
        
        return {'type': 'ir.actions.act_window_close'}
