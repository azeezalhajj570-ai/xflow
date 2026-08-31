# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
from datetime import timedelta

from odoo import models, fields, api, _, Command, tools
from odoo.exceptions import ValidationError
from odoo.tools import html2plaintext
from odoo.addons.mail.tools.discuss import Store
from markupsafe import Markup

_logger = logging.getLogger(__name__)


def is_whatsapp_channel(channel):
    return channel.channel_type == "whatsapp"


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    channel_type = fields.Selection(
        selection_add=[('whatsapp', 'WhatsApp Conversation')],
        ondelete={'whatsapp': 'cascade'}
    )
    whatsapp_number = fields.Char(string="Phone Number")
    wa_account_id = fields.Many2one('whatsapp.account', string="WhatsApp Business Account")
    whatsapp_partner_id = fields.Many2one('res.partner', string="WhatsApp Partner", index='btree_not_null')
    last_wa_mail_message_id = fields.Many2one('mail.message', string="Last WA Partner Mail Message", index='btree_not_null')
    whatsapp_channel_valid_until = fields.Datetime(
        string="WhatsApp Channel Valid Until Datetime",
        compute="_compute_whatsapp_channel_valid_until"
    )
    whatsapp_channel_active = fields.Boolean(
        'Is Whatsapp Channel Active',
        compute="_compute_whatsapp_channel_active"
    )

    _group_public_id_check = models.Constraint(
        "CHECK (channel_type = 'channel' OR channel_type = 'whatsapp' OR group_public_id IS NULL)",
        "Group authorization and group auto-subscription are only supported on channels and whatsapp.",
    )

    @api.depends('whatsapp_partner_id', 'whatsapp_number')
    def _compute_display_name(self):
        whatsapp_channels = self.filtered('whatsapp_partner_id')
        for channel in whatsapp_channels:
            number = channel.whatsapp_number
            partner = channel.whatsapp_partner_id
            partner_name = partner.name if partner.name != partner.phone else False
            channel.display_name = f'{partner_name} ({number})' if partner_name else number
        super(DiscussChannel, self - whatsapp_channels)._compute_display_name()

    @api.constrains('channel_type', 'whatsapp_number')
    def _check_whatsapp_number(self):
        missing_number = self.filtered(lambda channel: channel.channel_type == 'whatsapp' and not channel.whatsapp_number)
        if missing_number:
            raise ValidationError(
                _("A phone number is required for WhatsApp channels %(channel_names)s",
                  channel_names=', '.join(missing_number.mapped('name'))
                ))

    @api.depends('last_wa_mail_message_id')
    def _compute_whatsapp_channel_valid_until(self):
        for channel in self:
            channel.whatsapp_channel_valid_until = channel.last_wa_mail_message_id.create_date + timedelta(hours=24) \
                if channel.channel_type == "whatsapp" and channel.last_wa_mail_message_id else False

    @api.depends('whatsapp_channel_valid_until')
    def _compute_whatsapp_channel_active(self):
        for channel in self:
            channel.whatsapp_channel_active = channel.whatsapp_channel_valid_until and \
                channel.whatsapp_channel_valid_until > fields.Datetime.now()

    def _compute_group_public_id(self):
        wa_channels = self.filtered(lambda channel: channel.channel_type == "whatsapp")
        wa_channels.filtered(lambda channel: not channel.group_public_id).group_public_id = self.env.ref('base.group_user')
        super(DiscussChannel, self - wa_channels)._compute_group_public_id()

    @api.constrains('group_public_id', 'group_ids')
    def _constraint_group_id_channel(self):
        wa_channels = self.filtered(lambda channel: channel.channel_type == "whatsapp")
        super(DiscussChannel, self - wa_channels)._constraint_group_id_channel()

    def whatsapp_channel_join_and_pin(self):
        self.ensure_one()
        if self.channel_type != 'whatsapp':
            raise ValidationError(_('This join method is not possible for regular channels.'))

        self.check_access('write')
        current_partner = self.env.user.partner_id
        member = self.channel_member_ids.filtered(lambda m: m.partner_id == current_partner)
        if member:
            if not member.is_pinned:
                member.write({'unpin_dt': False})
        else:
            new_member = self.env['discuss.channel.member'].with_context(tools.clean_context(self.env.context)).sudo().create([{
                'partner_id': current_partner.id,
                'channel_id': self.id,
            }])
            message_body = Markup(f'<div class="o_mail_notification">{_("joined the channel")}</div>')
            new_member.channel_id.message_post(body=message_body, message_type="notification", subtype_xmlid="mail.mt_comment")
            self._bus_send_store(Store(new_member).add(self, {"memberCount": self.member_count}))
        return Store(self).get_result()

    def _to_store_defaults(self, target):
        return super()._to_store_defaults(target) + [
            Store.Attr("whatsapp_channel_valid_until", predicate=is_whatsapp_channel),
            Store.One("whatsapp_partner_id", [], predicate=is_whatsapp_channel),
            Store.One("wa_account_id", ["name"], predicate=is_whatsapp_channel, sudo=True),
        ]

    def _to_store(self, store: Store, fields):
        super()._to_store(store, fields)

    def _types_allowing_seen_infos(self):
        return super()._types_allowing_seen_infos() + ["whatsapp"]

    @api.model
    def _get_whatsapp_channel(self, whatsapp_number, wa_account_id, partner=None, sender_name=False, create_if_not_found=False, related_message=False):
        """Creates a whatsapp channel.

        :param str whatsapp_number: whatsapp phone number of the customer. It should
          be formatted according to whatsapp standards, aka {country_code}{national_number}.

        :returns: whatsapp discussion discuss.channel
        """
        base_number = whatsapp_number if whatsapp_number.startswith('+') else f'+{whatsapp_number}'
        wa_number = base_number.lstrip('+')

        channel_domain = [
            ('whatsapp_number', '=', wa_number),
            ('wa_account_id', '=', wa_account_id.id)
        ]
        channel = self.sudo().search(channel_domain, order='create_date desc', limit=1)

        partners_to_notify = self.env['res.partner']
        if not channel and create_if_not_found:
            name = wa_number
            if partner:
                name = partner.name if partner.name != partner.phone else wa_number

            channel = self.sudo().with_context(tools.clean_context(self.env.context)).create({
                'name': name,
                'channel_type': 'whatsapp',
                'whatsapp_number': wa_number,
                'whatsapp_partner_id': partner.id if partner else False,
                'wa_account_id': wa_account_id.id,
            })
            partners_to_notify |= channel.whatsapp_partner_id if channel.whatsapp_partner_id else self.env['res.partner']
            if wa_account_id.notify_user_ids.partner_id:
                partners_to_notify |= wa_account_id.notify_user_ids.partner_id
            channel.channel_member_ids = [Command.clear()] + [Command.create({'partner_id': p.id}) for p in partners_to_notify if p]
            channel._broadcast(partners_to_notify.ids)
        elif channel and partner:
            if not channel.whatsapp_partner_id:
                channel.whatsapp_partner_id = partner
            if partner not in channel.channel_member_ids.partner_id:
                channel.add_members(partner.ids, post_joined_message=False)

        return channel

    def _notify_thread(self, message, msg_vals=False, **kwargs):
        parent_msg_id = kwargs.pop('parent_msg_id') if 'parent_msg_id' in kwargs else False
        # Only create whatsapp.message for inbound messages, don't trigger sends
        whatsapp_inbound_msg_uid = self.env.context.get('whatsapp_inbound_msg_uid') or kwargs.get('whatsapp_inbound_msg_uid')
        if whatsapp_inbound_msg_uid and self.channel_type == 'whatsapp':
            self.env['whatsapp.message'].create({
                'mail_message_id': message.id,
                'message_type': 'inbound',
                'mobile_number': f'+{self.whatsapp_number}',
                'msg_uid': whatsapp_inbound_msg_uid,
                'parent_id': parent_msg_id,
                'state': 'received',
                'wa_account_id': self.wa_account_id.id,
            })
            if parent_msg_id:
                self.env['whatsapp.message'].browse(parent_msg_id).state = 'replied'
        return super()._notify_thread(message, msg_vals=msg_vals, **kwargs)

    def message_post(self, *args, body='', attachment_ids=None, message_type='notification', parent_id=False, **kwargs):
        # Skip processing if this is an inbound message from WhatsApp (prevent infinite loop)
        if self.env.context.get('whatsapp_inbound_msg_uid') or kwargs.get('whatsapp_inbound_msg_uid'):
            return super().message_post(
                *args, body=body, attachment_ids=attachment_ids,
                message_type=message_type, parent_id=parent_id, **kwargs
            )
        
        # Don't process system notifications or messages from the WhatsApp partner
        if message_type == 'notification':
            return super().message_post(
                *args, body=body, attachment_ids=attachment_ids,
                message_type=message_type, parent_id=parent_id, **kwargs
            )
        
        if message_type != 'whatsapp_message' or self.channel_type != 'whatsapp':
            message = super().message_post(
                *args, body=body, attachment_ids=attachment_ids,
                message_type=message_type, parent_id=parent_id, **kwargs
            )
            # Only create outbound message if it's a user comment, not from WhatsApp partner
            if self.channel_type == 'whatsapp' and message.message_type == 'comment':
                # Don't send auto-reply for messages from the WhatsApp contact
                if message.author_id and message.author_id.id != self.whatsapp_partner_id.id:
                    self._create_whatsapp_message(message)
            return message

        messages = super().message_post(
            *args, body=body, message_type=message_type, attachment_ids=attachment_ids,
            parent_id=parent_id, **kwargs,
        )

        # Don't create outbound messages if the author is the WhatsApp partner (customer)
        if messages.author_id == self.whatsapp_partner_id:
            self.last_wa_mail_message_id = messages if not hasattr(messages, '__iter__') else messages[0]
            Store(bus_channel=self).add(self, "whatsapp_channel_valid_until").bus_send()
            return messages[0] if hasattr(messages, '__iter__') else messages

        whatsapp_message_vals = []
        for new_msg in (messages if hasattr(messages, '__iter__') else [messages]):
            if not new_msg.wa_message_ids:
                whatsapp_message_vals.append({
                    'body': new_msg.body,
                    'mail_message_id': new_msg.id,
                    'message_type': 'outbound',
                    'mobile_number': f'+{self.whatsapp_number}' if self.whatsapp_number else '',
                    'wa_account_id': self.wa_account_id.id,
                })

        if whatsapp_message_vals:
            self.env['whatsapp.message'].create(whatsapp_message_vals)._send_message()

        return messages[0] if hasattr(messages, '__iter__') else messages

    def _create_whatsapp_message(self, message):
        # Don't re-send messages synced from the business user's phone
        if self.env.context.get('wa_from_me_sync'):
            return

        if not message.body and not message.attachment_ids:
            return

        if message.message_type not in ('comment',):
            return

        # Don't create outbound message for system notifications or messages from the WhatsApp partner
        if message.message_type == 'notification':
            return
        
        # Don't send auto-reply for messages from the WhatsApp contact
        if message.author_id and message.author_id.id == self.whatsapp_partner_id.id:
            return

        body_text = html2plaintext(message.body)

        wa_msg = self.env['whatsapp.message'].create({
            'body': body_text,
            'mobile_number': self.whatsapp_number,
            'wa_account_id': self.wa_account_id.id,
            'mail_message_id': message.id,
            'message_type': 'outbound',
            'state': 'outgoing',
            'attachment_ids': [Command.set(message.attachment_ids.ids)],
        })
        wa_msg._send_message()

    def _action_unfollow(self, partner=None, guest=None, post_leave_message=True):
        if partner and self.channel_type == "whatsapp" \
                and next(
                    (member.partner_id for member in self.channel_member_ids if not member.partner_id.partner_share),
                    self.env["res.partner"]
                ) == partner:
            msg = _("You can't leave this channel. As you are the owner of this WhatsApp channel, you can only delete it.")
            partner._bus_send_transient_message(self, msg)
            return
        return super()._action_unfollow(partner, guest, post_leave_message)
