import json
import logging
from datetime import timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = 'https://api.telegram.org/bot'

OUTGOING_STATES = ['pending', 'processing', 'sent', 'delivered', 'error', 'dead_letter', 'cancelled']
INCOMING_STATES = ['pending', 'processing', 'processed', 'error', 'dead_letter']


class MadarBotTelegramMessage(models.Model):
    _name = 'madarbot.telegram.message'
    _description = 'Telegram Message Queue'
    _order = 'scheduled_at ASC, id ASC'
    _rec_name = 'display_name'

    display_name = fields.Char(compute='_compute_display_name', store=False)

    direction = fields.Selection([
        ('incoming', 'Incoming'),
        ('outgoing', 'Outgoing'),
    ], string='Direction', required=True, default='outgoing', index=True)

    state = fields.Selection([
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('processed', 'Processed'),
        ('error', 'Error'),
        ('dead_letter', 'Dead Letter'),
        ('cancelled', 'Cancelled'),
    ], string='State', required=True, default='pending', index=True, tracking=True)

    telegram_chat_id = fields.Char('Telegram Chat ID', required=True, index=True)
    telegram_message_id = fields.Integer('Telegram Message ID', index=True, copy=False)
    update_id = fields.Integer('Telegram Update ID', index=True, copy=False)

    body = fields.Text('Message Body', required=True)
    body_mimetype = fields.Char('Body MIME Type', default='text/plain')

    account_id = fields.Many2one(
        'madarbot.account', string='Bot Account',
        required=True, ondelete='cascade', index=True,
    )
    guest_id = fields.Many2one(
        'mail.guest', string='Sender Guest',
        ondelete='set null', index=True,
    )
    channel_id = fields.Many2one(
        'discuss.channel', string='Discuss Channel',
        ondelete='set null', index=True,
    )
    mail_message_id = fields.Many2one(
        'mail.message', string='Related Mail Message',
        ondelete='set null', index=True, copy=False,
    )
    mailing_trace_id = fields.Many2one(
        'mailing.trace', string='Mailing Trace',
        ondelete='set null', copy=False,
    )

    tracker_ids = fields.One2many(
        'madarbot.telegram.tracker', 'message_id',
        string='Trackers',
    )

    error_code = fields.Integer('Error Code')
    error_description = fields.Text('Error Description')
    retry_count = fields.Integer('Retry Count', default=0)
    max_retries = fields.Integer('Max Retries', default=3)

    scheduled_at = fields.Datetime(
        'Scheduled At', default=fields.Datetime.now,
        index=True, required=True,
    )
    sent_at = fields.Datetime('Sent At', copy=False)
    delivered_at = fields.Datetime('Delivered At', copy=False)
    processed_at = fields.Datetime('Processed At', copy=False)

    _sql_constraints = [
        ('check_state_direction',
         "CHECK( (direction='incoming' AND state IN ('pending','processing','processed','error','dead_letter'))"
         " OR (direction='outgoing' AND state IN ('pending','processing','sent','delivered','error','dead_letter','cancelled')) )",
         "Invalid state for the given direction"),
    ]

    def _compute_display_name(self):
        for record in self:
            chat = record.telegram_chat_id or '?'
            body_preview = (record.body or '')[:60]
            record.display_name = f'[{record.direction}] {chat}: {body_preview}'

    def action_cancel(self):
        self.filtered(lambda m: m.state == 'pending').write({'state': 'cancelled'})

    def action_retry(self):
        to_retry = self.filtered(lambda m: m.state in ('error', 'dead_letter'))
        to_retry.write({'state': 'pending', 'retry_count': 0, 'error_code': 0, 'error_description': ''})
        to_retry.mapped('tracker_ids').unlink()

    @api.model
    def _process_incoming_messages(self, batch_size=50):
        messages = self.search([
            ('direction', '=', 'incoming'),
            ('state', '=', 'pending'),
        ], limit=batch_size, order='scheduled_at ASC')
        for msg in messages:
            try:
                msg._process_single_incoming()
            except Exception:
                _logger.exception('Failed to process incoming message %s', msg.id)

    def _process_single_incoming(self):
        self.env.cr.execute(
            'SELECT id FROM madarbot_telegram_message WHERE id = %s AND state = %s FOR UPDATE NOWAIT',
            [self.id, 'pending'],
        )
        if not self.env.cr.fetchone():
            return
        self.write({'state': 'processing'})
        try:
            guest = self.env['mail.guest'].sudo().search([
                ('telegram_user_id', '=', self.guest_id.telegram_user_id),
            ], limit=1) if self.guest_id else False
            if not guest and self.guest_id:
                guest = self.guest_id

            channel = self.channel_id
            if not channel:
                channel = self.env['discuss.channel'].sudo().search([
                    ('telegram_chat_id', '=', self.telegram_chat_id),
                ], limit=1)

            if not channel:
                account = self.account_id
                chat = self._fetch_telegram_chat(account)
                channel = self.env['discuss.channel'].sudo().create({
                    'name': f'Telegram: {chat.get("title", self.telegram_chat_id)}',
                    'channel_type': 'telegram',
                    'telegram_chat_id': self.telegram_chat_id,
                    'telegram_account_id': account.id,
                })
                if guest:
                    channel._add_members(guests=guest)

            message = channel.with_user(self.env.ref('base.partner_root')).message_post(
                body=self.body,
                message_type='comment',
                author_guest_id=guest.id if guest else False,
                telegram_message_id=self.telegram_message_id,
                telegram_chat_id=self.telegram_chat_id,
            )
            self.write({
                'state': 'processed',
                'channel_id': channel.id,
                'mail_message_id': message.id,
                'processed_at': fields.Datetime.now(),
            })
        except Exception:
            self._handle_failure()

    def _handle_failure(self):
        self.retry_count += 1
        vals = {'state': 'error', 'retry_count': self.retry_count}
        if self.retry_count >= self.max_retries:
            vals['state'] = 'dead_letter'
        self.write(vals)

    def _fetch_telegram_chat(self, account):
        try:
            resp = requests.get(
                f'{TELEGRAM_API_BASE}{account.token}/getChat',
                params={'chat_id': self.telegram_chat_id},
                timeout=10,
            )
            return resp.json().get('result', {})
        except Exception:
            _logger.exception('Failed to fetch chat info for %s', self.telegram_chat_id)
            return {}

    @api.model
    def _process_outgoing_messages(self, batch_size=50):
        messages = self.search([
            ('direction', '=', 'outgoing'),
            ('state', '=', 'pending'),
            ('scheduled_at', '<=', fields.Datetime.now()),
        ], limit=batch_size, order='scheduled_at ASC')
        for msg in messages:
            try:
                msg._process_single_outgoing()
            except Exception:
                _logger.exception('Failed to process outgoing message %s', msg.id)

    def _process_single_outgoing(self):
        self.env.cr.execute(
            'SELECT id FROM madarbot_telegram_message WHERE id = %s AND state = %s FOR UPDATE NOWAIT',
            [self.id, 'pending'],
        )
        if not self.env.cr.fetchone():
            return
        self.write({'state': 'processing'})
        account = self.account_id.sudo()
        if not account or not account.token:
            self.write({'state': 'error', 'error_description': 'No active bot account'})
            self._create_tracker()
            return
        try:
            resp = requests.post(
                f'{TELEGRAM_API_BASE}{account.token}/sendMessage',
                json={
                    'chat_id': self.telegram_chat_id,
                    'text': self.body[:4096],
                    'parse_mode': 'HTML',
                    'disable_web_page_preview': True,
                },
                timeout=15,
            )
            result = resp.json()
            if result.get('ok'):
                self.write({
                    'state': 'sent',
                    'telegram_message_id': result['result'].get('message_id'),
                    'sent_at': fields.Datetime.now(),
                })
                self._create_tracker(state='sent')
                self._update_mailing_trace('sent')
            else:
                error_code = result.get('error_code', 0)
                desc = result.get('description', 'Unknown error')
                self.write({
                    'state': 'error',
                    'error_code': error_code,
                    'error_description': desc,
                    'retry_count': self.retry_count + 1,
                })
                self._create_tracker(state='error', error_code=error_code, error_description=desc)
                if self.retry_count >= self.max_retries:
                    self.write({'state': 'dead_letter'})
                    self._update_mailing_trace('error')
        except requests.Timeout:
            self.write({
                'state': 'error',
                'error_description': 'Request timed out',
                'retry_count': self.retry_count + 1,
            })
            self._create_tracker(state='error', error_description='Timeout')
        except Exception as e:
            self.write({
                'state': 'error',
                'error_description': str(e),
                'retry_count': self.retry_count + 1,
            })
            self._create_tracker(state='error', error_description=str(e))
            if self.retry_count >= self.max_retries:
                self.write({'state': 'dead_letter'})

    def _create_tracker(self, state=None, error_code=None, error_description=None):
        self.env['madarbot.telegram.tracker'].create({
            'message_id': self.id,
            'state': state or self.state,
            'error_code': error_code,
            'error_description': error_description,
        })

    def _update_mailing_trace(self, trace_status):
        if self.mailing_trace_id:
            self.mailing_trace_id.write({'trace_status': trace_status})

    @api.model
    def _cron_process_incoming(self):
        self._process_incoming_messages()

    @api.model
    def _cron_process_outgoing(self):
        self._process_outgoing_messages()

    @api.model
    def _cron_cleanup_dead_letters(self):
        cutoff = fields.Datetime.now() - timedelta(days=30)
        self.search([
            ('state', '=', 'dead_letter'),
            ('write_date', '<', cutoff),
        ]).unlink()

    @api.model
    def _send_telegram_message(self, chat_id, body, account_id, mailing_trace_id=None, channel_id=None):
        return self.create({
            'direction': 'outgoing',
            'state': 'pending',
            'telegram_chat_id': chat_id,
            'body': body,
            'account_id': account_id,
            'mailing_trace_id': mailing_trace_id,
            'channel_id': channel_id,
        })

    def _send_mass_telegram(self, res_ids):
        mailing = self.env['mailing.mailing'].browse(self._context.get('active_id'))
        account = self.env['madarbot.account'].sudo().search([('active', '=', True)], limit=1)
        if not account:
            raise UserError(_('No active Telegram bot account configured'))
        contacts = self.env['mailing.contact'].browse(res_ids)
        for contact in contacts:
            if not contact.telegram_chat_id:
                continue
            body = mailing.body_plaintext or mailing.body_html or ''
            self.create({
                'direction': 'outgoing',
                'state': 'pending',
                'telegram_chat_id': contact.telegram_chat_id,
                'body': body,
                'account_id': account.id,
                'mailing_trace_id': self.env['mailing.trace'].create({
                    'mass_mailing_id': mailing.id,
                    'res_id': contact.id,
                    'model': 'mailing.contact',
                    'trace_type': 'telegram',
                    'trace_status': 'pending',
                }).id,
            })
