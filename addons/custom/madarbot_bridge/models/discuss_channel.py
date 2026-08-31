from odoo import fields, models


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    channel_type = fields.Selection(
        selection_add=[('telegram', 'Telegram Channel')],
        ondelete={'telegram': 'cascade'},
    )
    telegram_chat_id = fields.Char('Telegram Chat ID', index=True, copy=False)
    telegram_account_id = fields.Many2one(
        'madarbot.account', string='Telegram Bot Account',
        ondelete='set null', copy=False,
    )

    def _notify_thread(self, message, msg_vals=False, **kwargs):
        rdata = super()._notify_thread(message, msg_vals=msg_vals, **kwargs)
        telegram_channels = self.filtered(lambda c: c.channel_type == 'telegram')
        if telegram_channels:
            telegram_channels._enqueue_telegram_notification(message, msg_vals=msg_vals, **kwargs)
        return rdata

    def _enqueue_telegram_notification(self, message, msg_vals=False, **kwargs):
        for channel in self:
            if not channel.telegram_chat_id or not channel.telegram_account_id:
                continue
            body = (msg_vals or message).get('body', '') if isinstance(msg_vals, dict) else (message.body or '')
            self.env['madarbot.telegram.message'].create({
                'direction': 'outgoing',
                'state': 'pending',
                'telegram_chat_id': channel.telegram_chat_id,
                'body': self._strip_html(body),
                'account_id': channel.telegram_account_id.id,
                'channel_id': channel.id,
                'mail_message_id': message.id if isinstance(message, models.BaseModel) else None,
            })

    def _strip_html(self, html):
        import re
        text = re.sub(r'<[^>]+>', '', html or '')
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
        return text.strip()
