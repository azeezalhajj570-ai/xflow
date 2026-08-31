# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import models, fields

class MailMessage(models.Model):
    _inherit = 'mail.message'

    message_type = fields.Selection(
        selection_add=[('whatsapp_message', 'WhatsApp')],
        ondelete={'whatsapp_message': lambda recs: recs.write({'message_type': 'comment'})},
    )
    wa_message_ids = fields.One2many('whatsapp.message', 'mail_message_id', string='Related WhatsApp Messages')

    def _message_format(self, fnames, format_reply=True):
        """ Override to add WhatsApp status to the message dict """
        vals_list = super()._message_format(fnames, format_reply=format_reply)
        for vals in vals_list:
            message_sudo = self.browse(vals['id']).sudo()
            if message_sudo.wa_message_ids:
                # Take the status of the most recent linked WhatsApp message
                last_wa_msg = message_sudo.wa_message_ids[0]
                vals['whatsapp_status'] = last_wa_msg.state
        return vals_list
