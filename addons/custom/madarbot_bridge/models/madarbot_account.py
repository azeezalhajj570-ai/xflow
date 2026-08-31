import json

from odoo import api, fields, models


class MadarBotAccount(models.Model):
    _name = 'madarbot.account'
    _description = 'Telegram Bot Account'
    _rec_name = 'name'
    _order = 'name ASC'

    name = fields.Char('Bot Name', required=True)
    token = fields.Char('Bot Token', required=True, groups='base.group_system')
    username = fields.Char('Bot Username', readonly=True, copy=False)
    active = fields.Boolean('Active', default=True)
    channel_ids = fields.One2many(
        'discuss.channel', 'telegram_account_id',
        string='Linked Channels',
    )
    message_ids = fields.One2many(
        'madarbot.telegram.message', 'account_id',
        string='Messages',
    )
    processed_update_ids = fields.Text(
        'Processed Update IDs',
        help='Comma-separated list of processed Telegram update IDs for idempotency',
        groups='base.group_system',
    )

    def _has_processed_update(self, update_id):
        if not update_id or not self.processed_update_ids:
            return False
        try:
            ids = json.loads(self.processed_update_ids or '[]')
            return update_id in ids
        except (ValueError, TypeError):
            return False

    def _add_processed_update(self, update_id):
        if not update_id:
            return
        ids = set()
        if self.processed_update_ids:
            try:
                ids = set(json.loads(self.processed_update_ids))
            except (ValueError, TypeError):
                ids = set()
        ids.add(update_id)
        max_size = 10000
        if len(ids) > max_size:
            ids = set(sorted(ids)[-max_size:])
        self.write({'processed_update_ids': json.dumps(list(ids))})

    @api.model
    def _crop_processed_update_ids(self):
        accounts = self.search([('active', '=', True)])
        for account in accounts:
            if account.processed_update_ids:
                try:
                    ids = json.loads(account.processed_update_ids)
                    if len(ids) > 1000:
                        account.processed_update_ids = json.dumps(sorted(ids)[-1000:])
                except (ValueError, TypeError):
                    pass
