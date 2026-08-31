from odoo import fields, models


class MailingTrace(models.Model):
    _inherit = 'mailing.trace'

    trace_type = fields.Selection(selection_add=[
        ('telegram', 'Telegram Message'),
    ], ondelete={'telegram': 'set default'})

    telegram_message_id = fields.Integer('Telegram Message ID', index=True)

    failure_type = fields.Selection(selection_add=[
        ('telegram_server', 'Telegram Server Error'),
        ('telegram_blocked', 'User Blocked Bot'),
        ('telegram_chat_not_found', 'Chat Not Found'),
    ])
