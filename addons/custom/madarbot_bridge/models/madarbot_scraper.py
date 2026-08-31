from odoo import fields, models


class MadarBotScrapedGroup(models.Model):
    _name = 'madarbot.scraped.group'
    _description = 'Scraped Telegram Group'
    _rec_name = 'title'

    title = fields.Char('Group Title', required=True)
    telegram_chat_id = fields.Char('Telegram Chat ID', required=True, index=True)
    chat_type = fields.Selection([
        ('group', 'Group'),
        ('supergroup', 'Supergroup'),
        ('channel', 'Channel'),
    ], string='Chat Type', required=True)
    username = fields.Char('Username')
    member_count = fields.Integer('Member Count')
    description = fields.Text('Description')
    invite_link = fields.Char('Invite Link')
    scraped_by = fields.Many2one('madarbot.account', string='Scraped By')
    last_scraped = fields.Datetime('Last Scraped')
    active = fields.Boolean('Active', default=True)


class MadarBotScrapedMessage(models.Model):
    _name = 'madarbot.scraped.message'
    _description = 'Scraped Telegram Message'

    telegram_message_id = fields.Integer('Telegram Message ID', required=True, index=True)
    group_id = fields.Many2one(
        'madarbot.scraped.group', string='Group',
        required=True, ondelete='cascade', index=True,
    )
    sender_user_id = fields.Integer('Sender User ID', index=True)
    sender_name = fields.Char('Sender Name')
    sender_username = fields.Char('Sender Username')
    message_text = fields.Text('Message Text')
    has_media = fields.Boolean('Has Media')
    media_type = fields.Char('Media Type')
    reply_to_message_id = fields.Integer('Reply To Message ID')
    posted_at = fields.Datetime('Posted At', required=True)
    created_at = fields.Datetime('Created At', default=fields.Datetime.now, required=True)
