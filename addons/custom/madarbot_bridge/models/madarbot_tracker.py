from odoo import api, fields, models


class MadarBotTelegramTracker(models.Model):
    _name = 'madarbot.telegram.tracker'
    _description = 'Telegram Message Delivery Tracker'
    _order = 'tracked_at DESC, id DESC'
    _rec_name = 'display_name'

    display_name = fields.Char(compute='_compute_display_name')
    message_id = fields.Many2one(
        'madarbot.telegram.message', string='Telegram Message',
        required=True, ondelete='cascade', index=True,
    )
    state = fields.Selection([
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('processed', 'Processed'),
        ('error', 'Error'),
        ('dead_letter', 'Dead Letter'),
        ('cancelled', 'Cancelled'),
    ], string='State', required=True)
    error_code = fields.Integer('Error Code')
    error_description = fields.Text('Error Description')
    tracked_at = fields.Datetime('Tracked At', default=fields.Datetime.now, required=True)

    def _compute_display_name(self):
        for record in self:
            record.display_name = f'[{record.state}] {record.message_id.display_name}'
