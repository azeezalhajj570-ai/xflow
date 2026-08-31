from odoo import models, fields

class WhatsAppTag(models.Model):
    _name = 'whatsapp.tag'
    _description = 'WhatsApp Tag'

    name = fields.Char(string='Tag Name', required=True)
    color = fields.Integer(string='Color')
