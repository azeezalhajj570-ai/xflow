from odoo import api, fields, models


class MailGuest(models.Model):
    _inherit = 'mail.guest'

    telegram_chat_id = fields.Char('Telegram Chat ID', index=True, copy=False)
    telegram_user_id = fields.Integer('Telegram User ID', index=True, copy=False)
    telegram_username = fields.Char('Telegram Username', copy=False)
    telegram_language_code = fields.Char('Telegram Language Code', copy=False)
    telegram_is_bot = fields.Boolean('Is Bot', default=False, copy=False)

    @api.model
    def _get_or_create_telegram_guest(self, tg_user):
        user_id = tg_user.get('id')
        if not user_id:
            return self.env['mail.guest']
        guest = self.search([('telegram_user_id', '=', user_id)], limit=1)
        if guest:
            parts = [tg_user.get('first_name', ''), tg_user.get('last_name', '')]
            guest.write({
                'telegram_username': tg_user.get('username', ''),
                'telegram_language_code': tg_user.get('language_code', ''),
                'name': ' '.join(filter(None, parts)),
            })
            return guest
        parts = [tg_user.get('first_name', ''), tg_user.get('last_name', '')]
        return self.create({
            'name': ' '.join(filter(None, parts)),
            'telegram_chat_id': str(user_id),
            'telegram_user_id': user_id,
            'telegram_username': tg_user.get('username', ''),
            'telegram_language_code': tg_user.get('language_code', ''),
            'telegram_is_bot': tg_user.get('is_bot', False),
        })
