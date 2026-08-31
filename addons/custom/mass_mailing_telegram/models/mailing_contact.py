from odoo import fields, models


class MailingContact(models.Model):
    _inherit = 'mailing.contact'

    telegram_chat_id = fields.Char('Telegram Chat ID', index=True)
