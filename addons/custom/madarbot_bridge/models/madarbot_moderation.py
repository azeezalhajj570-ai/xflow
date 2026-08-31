from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models


class MadarBotModerationRule(models.Model):
    _name = 'madarbot.moderation.rule'
    _description = 'Telegram Moderation Rule'

    name = fields.Char('Rule Name', required=True)
    channel_id = fields.Many2one('discuss.channel', string='Channel')
    account_id = fields.Many2one('madarbot.account', string='Bot Account')
    rule_type = fields.Selection([
        ('rate_limit', 'Rate Limit'),
        ('repeated_message', 'Repeated Message Detection'),
    ], string='Rule Type', required=True)
    max_messages = fields.Integer('Max Messages', default=5)
    window_minutes = fields.Integer('Time Window (minutes)', default=1)
    action = fields.Selection([
        ('warn', 'Warn'),
        ('delete', 'Delete Message'),
        ('restrict', 'Restrict User'),
        ('ban', 'Ban User'),
    ], string='Action', default='warn', required=True)
    active = fields.Boolean('Active', default=True)


class MadarBotRateLimiter(models.Model):
    _name = 'madarbot.rate.limiter'
    _description = 'Telegram Rate Limiter'

    telegram_user_id = fields.Integer('Telegram User ID', required=True, index=True)
    channel_id = fields.Many2one('discuss.channel', string='Channel', index=True)
    message_count = fields.Integer('Message Count', default=1)
    window_start = fields.Datetime('Window Start', required=True, default=fields.Datetime.now)

    @api.model
    def check_rate_limit(self, tg_user_id, channel_id, rule):
        window_start = fields.Datetime.now() - timedelta(minutes=rule.window_minutes)
        record = self.search([
            ('telegram_user_id', '=', tg_user_id),
            ('channel_id', '=', channel_id),
            ('window_start', '>=', window_start),
        ], limit=1)
        if record:
            record.message_count += 1
            return record.message_count > rule.max_messages
        self.create({
            'telegram_user_id': tg_user_id,
            'channel_id': channel_id,
        })
        return False

    @api.model
    def _cron_cleanup_rate_limits(self):
        cutoff = fields.Datetime.now() - timedelta(hours=24)
        self.search([('window_start', '<', cutoff)]).unlink()


class MadarBotRepeatedMessage(models.Model):
    _name = 'madarbot.repeated.message'
    _description = 'Repeated Message Detection'

    telegram_user_id = fields.Integer('Telegram User ID', required=True, index=True)
    channel_id = fields.Many2one('discuss.channel', string='Channel', index=True)
    message_hash = fields.Char('Message Hash', required=True, index=True)
    created_at = fields.Datetime('Created At', default=fields.Datetime.now, required=True)

    _sql_constraints = [
        ('unique_user_channel_hash', 'UNIQUE(telegram_user_id, channel_id, message_hash)',
         'This exact message was already sent by this user in this channel.'),
    ]

    @api.model
    def check_repeated(self, tg_user_id, channel_id, message_text):
        import hashlib
        msg_hash = hashlib.md5(message_text.encode()).hexdigest()
        existing = self.search_count([
            ('telegram_user_id', '=', tg_user_id),
            ('channel_id', '=', channel_id),
            ('message_hash', '=', msg_hash),
        ])
        if existing:
            return True
        self.create({
            'telegram_user_id': tg_user_id,
            'channel_id': channel_id,
            'message_hash': msg_hash,
        })
        return False
