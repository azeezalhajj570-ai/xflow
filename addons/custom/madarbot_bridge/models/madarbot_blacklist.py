from odoo import api, fields, models


class MadarBotBlacklist(models.Model):
    _name = 'madarbot.blacklist'
    _description = 'Telegram User Blacklist'
    _rec_name = 'telegram_user_id'

    telegram_user_id = fields.Char('Telegram User ID', required=True, index=True)
    telegram_username = fields.Char('Telegram Username')
    reason = fields.Text('Reason')
    active = fields.Boolean('Active', default=True)
    blocked_at = fields.Datetime('Blocked At', default=fields.Datetime.now)

    _sql_constraints = [
        ('unique_telegram_user_id', 'UNIQUE(telegram_user_id)',
         'This Telegram user is already blacklisted.'),
    ]

    @api.model
    def _add(self, telegram_user_id, reason=None, username=None):
        record = self.search([('telegram_user_id', '=', telegram_user_id)], limit=1)
        if record:
            if not record.active:
                record.write({'active': True, 'reason': reason or record.reason})
            return record
        return self.create({
            'telegram_user_id': telegram_user_id,
            'telegram_username': username or '',
            'reason': reason or '',
        })

    @api.model
    def _remove(self, telegram_user_id):
        record = self.search([('telegram_user_id', '=', telegram_user_id)], limit=1)
        if record:
            record.write({'active': False})
        return record

    @api.model
    def is_blacklisted(self, telegram_user_id):
        return bool(self.search_count([
            ('telegram_user_id', '=', telegram_user_id),
            ('active', '=', True),
        ]))
