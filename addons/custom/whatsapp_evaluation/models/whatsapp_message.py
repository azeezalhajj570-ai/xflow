# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
from odoo import models, fields, api, _
from odoo.addons.whatsapp_evaluation.tools.whatsapp_api import WhatsAppApi
from odoo.addons.whatsapp_evaluation.tools.whatsapp_exception import WhatsAppError
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)


class WhatsAppMessage(models.Model):
    _name = 'whatsapp.message'
    _description = 'WhatsApp Messages'
    _order = 'id desc'
    _rec_name = 'mobile_number'

    mobile_number = fields.Char(string="Sent To")
    mobile_number_formatted = fields.Char(
        string="Mobile Number Formatted",
        compute="_compute_mobile_number_formatted",
        readonly=False,
        store=True
    )
    message_type = fields.Selection([
        ('outbound', 'Outbound'),
        ('inbound', 'Inbound')
    ], string="Message Type", default='outbound')
    state = fields.Selection([
        ('outgoing', 'In Queue'),
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('read', 'Read'),
        ('replied', 'Replied'),
        ('received', 'Received'),
        ('error', 'Failed'),
        ('bounced', 'Bounced'),
        ('cancel', 'Cancelled')
    ], string="State", default='outgoing')
    failure_type = fields.Selection([
        ('account', 'Account Error'),
        ('blacklisted', 'Blacklisted Phone Number'),
        ('network', 'Network Error'),
        ('outdated_channel', 'The channel is no longer active'),
        ('phone_invalid', 'Wrong Number Format'),
        ('template', 'Template Quality Rating Too Low'),
        ('unknown', 'Unknown Error'),
        ('whatsapp_recoverable', 'Identified Error'),
        ('whatsapp_unrecoverable', 'Other Technical Error')
    ])
    failure_reason = fields.Char(string="Failure Reason")
    msg_uid = fields.Char(string="WhatsApp Message ID")
    wa_account_id = fields.Many2one('whatsapp.account', string="WhatsApp Business Account")
    parent_id = fields.Many2one('whatsapp.message', 'Response To', index='btree_not_null', ondelete="set null")
    mail_message_id = fields.Many2one('mail.message', index=True)
    body = fields.Html(related='mail_message_id.body', string="Body", related_sudo=False)
    partner_id = fields.Many2one('res.partner', string='Contact')
    tag_ids = fields.Many2many('whatsapp.tag', string='Tags')
    attachment_ids = fields.Many2many('ir.attachment', string="Attachments")

    _unique_msg_uid = models.Constraint(
        'unique(msg_uid)',
        "Each whatsapp message should correspond to a single message uuid.",
    )

    _SUPPORTED_ATTACHMENT_TYPE = {
        'audio': ('audio/aac', 'audio/mp4', 'audio/mpeg', 'audio/amr', 'audio/ogg'),
        'document': (
            'text/plain', 'application/pdf', 'application/vnd.ms-powerpoint', 'application/msword',
            'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ),
        'image': ('image/jpeg', 'image/png'),
        'video': ('video/mp4',),
    }

    @api.depends('mobile_number')
    def _compute_mobile_number_formatted(self):
        for message in self:
            message.mobile_number_formatted = message.mobile_number or ''

    def _send_message(self):
        for record in self:
            if record.state != 'outgoing' or record.message_type != 'outbound':
                continue

            api = record.wa_account_id.sudo()._get_api_client()
            response = {}

            try:
                if record.attachment_ids:
                    attachment = record.attachment_ids[0]
                    whatsapp_media_type = next((
                        media_type
                        for media_type, mimetypes in self._SUPPORTED_ATTACHMENT_TYPE.items()
                        if attachment.mimetype in mimetypes
                    ), False)
                    if not whatsapp_media_type:
                        raise WhatsAppError(_("Attachment mimetype is not supported by WhatsApp: %s.", attachment.mimetype))

                    caption = html2plaintext(record.body) if record.body else ''
                    response = api._send_whatsapp_media(record.mobile_number, attachment, caption)
                else:
                    text_body = html2plaintext(record.body) if record.body else ''
                    response = api._send_whatsapp(record.mobile_number, text_body)

                msg_id = False
                if isinstance(response, dict):
                    if 'key' in response:
                        msg_id = response['key'].get('id')
                    elif 'id' in response:
                        msg_id = response['id']

                record.write({
                    'state': 'sent',
                    'msg_uid': msg_id,
                    'failure_reason': False
                })
            except WhatsAppError as e:
                record.write({
                    'state': 'error',
                    'failure_reason': str(e)
                })
            except Exception as e:
                _logger.exception("Error sending WhatsApp message")
                record.write({
                    'state': 'error',
                    'failure_reason': str(e)
                })

    def button_resend(self):
        for record in self:
            if record.state == 'error':
                record.state = 'outgoing'
                record._send_message()

    def button_cancel_send(self):
        for record in self:
            if record.state != 'outgoing':
                raise UserError(_("You can not cancel message which is not in queue."))
        self.write({'state': 'cancel'})
