import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class MailingMailing(models.Model):
    _inherit = 'mailing.mailing'

    mailing_type = fields.Selection(selection_add=[
        ('telegram', 'Telegram Message'),
    ], ondelete={'telegram': 'set default'})

    telegram_force_send = fields.Boolean(
        'Send Directly',
        help='Immediately send the Telegram Mailing instead of queuing up.',
    )

    def _action_send_mail(self, res_ids=None):
        mass_telegram = self.filtered(lambda m: m.mailing_type == 'telegram')
        if mass_telegram:
            mass_telegram._action_send_telegram(res_ids=res_ids)
        return super(MailingMailing, self - mass_telegram)._action_send_mail(res_ids=res_ids)

    def _action_send_telegram(self, res_ids=None):
        for mailing in self:
            if not res_ids:
                res_ids = mailing._get_remaining_recipients()
            if res_ids:
                mailing._enqueue_telegram_messages(res_ids)
        return True

    def _enqueue_telegram_messages(self, res_ids):
        Message = self.env['madarbot.telegram.message']
        contacts = self.env['mailing.contact'].browse(res_ids)
        account = self.env['madarbot.account'].sudo().search([('active', '=', True)], limit=1)
        if not account:
            _logger.error('No active Telegram bot account for mass mailing %s', self.id)
            return False
        body = self._render_body()
        for contact in contacts:
            if not contact.telegram_chat_id:
                self._create_error_trace(contact, 'telegram_chat_missing', 'No Telegram chat ID')
                continue
            trace = self.env['mailing.trace'].create({
                'mass_mailing_id': self.id,
                'res_id': contact.id,
                'model': 'mailing.contact',
                'trace_type': 'telegram',
                'trace_status': 'pending',
            })
            Message.create({
                'direction': 'outgoing',
                'state': 'pending',
                'telegram_chat_id': contact.telegram_chat_id,
                'body': body,
                'account_id': account.id,
                'mailing_trace_id': trace.id,
            })
        return True

    def _render_body(self):
        return (self.body_plaintext or self.body_html or '')

    def _create_error_trace(self, contact, failure_type, reason):
        self.env['mailing.trace'].create({
            'mass_mailing_id': self.id,
            'res_id': contact.id,
            'model': 'mailing.contact',
            'trace_type': 'telegram',
            'trace_status': 'error',
            'failure_type': failure_type,
            'failure_reason': reason,
        })

    def _get_pretty_mailing_type(self):
        if self.mailing_type == 'telegram':
            return _('Telegram Message')
        return super()._get_pretty_mailing_type()

    def action_retry_failed(self):
        mass_telegram = self.filtered(lambda m: m.mailing_type == 'telegram')
        if mass_telegram:
            mass_telegram._action_retry_failed_telegram()
        return super(MailingMailing, self - mass_telegram).action_retry_failed()

    def _action_retry_failed_telegram(self):
        failed_traces = self.env['mailing.trace'].sudo().search([
            ('mass_mailing_id', 'in', self.ids),
            ('trace_type', '=', 'telegram'),
            ('trace_status', '=', 'error'),
        ])
        res_ids = failed_traces.mapped('res_id')
        self._enqueue_telegram_messages(res_ids)
