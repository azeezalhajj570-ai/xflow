from odoo import _, api, fields, models


class MadarBotTelegramComposer(models.TransientModel):
    _name = 'madarbot.telegram.composer'
    _description = 'Telegram Message Composer'

    body = fields.Text('Message Body', required=True)
    account_id = fields.Many2one('madarbot.account', string='Bot Account', required=True)
    channel_ids = fields.Many2many('discuss.channel', string='Channels',
                                   domain=[('channel_type', '=', 'telegram')])
    guest_ids = fields.Many2many('mail.guest', string='Guests')
    mailing_id = fields.Many2one('mailing.mailing', string='Mass Mailing')

    def _action_send(self):
        self.ensure_one()
        Message = self.env['madarbot.telegram.message']
        count = 0
        if self.channel_ids:
            for channel in self.channel_ids:
                Message.create({
                    'direction': 'outgoing',
                    'state': 'pending',
                    'telegram_chat_id': channel.telegram_chat_id,
                    'body': self.body,
                    'account_id': self.account_id.id,
                    'channel_id': channel.id,
                })
                count += 1
        if self.guest_ids:
            for guest in self.guest_ids:
                if guest.telegram_chat_id:
                    Message.create({
                        'direction': 'outgoing',
                        'state': 'pending',
                        'telegram_chat_id': guest.telegram_chat_id,
                        'body': self.body,
                        'account_id': self.account_id.id,
                        'guest_id': guest.id,
                    })
                    count += 1
        if self.mailing_id:
            mailing = self.mailing_id
            for contact in mailing.mailing_contact_ids:
                if not contact.telegram_chat_id:
                    continue
                Message.create({
                    'direction': 'outgoing',
                    'state': 'pending',
                    'telegram_chat_id': contact.telegram_chat_id,
                    'body': self.body,
                    'account_id': self.account_id.id,
                })
                count += 1
        return {
            'type': 'ir.actions.act_window_close',
        }
