import logging
import re
from datetime import timedelta

from odoo import models, fields, api
from odoo.addons.mail.tools.discuss import Store
from odoo.tools import html2plaintext
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


def is_whatsapp_channel(channel):
    return channel.channel_type == "whatsapp"


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    whatsapp_ai_agent_id = fields.Many2one(
        'ai.agent',
        compute='_compute_whatsapp_ai_agent_id',
        string='WhatsApp AI Agent',
    )

    current_handler_type = fields.Selection([
        ('ai', 'AI Agent'),
        ('chatbot', 'Chatbot'),
        ('human', 'Human'),
    ], string='Current Handler', default=False)

    last_ai_response_time = fields.Datetime(
        string='Last AI Response',
        help='Timestamp of the last AI response sent in this channel.',
    )

    @api.depends('wa_account_id', 'wa_account_id.ai_agent_id', 'channel_type')
    def _compute_whatsapp_ai_agent_id(self):
        for channel in self:
            if channel.channel_type == 'whatsapp' and channel.wa_account_id:
                channel.whatsapp_ai_agent_id = channel.wa_account_id.ai_agent_id
            else:
                channel.whatsapp_ai_agent_id = False

    def _to_store_defaults(self, target):
        result = super()._to_store_defaults(target)
        return [s for s in result if not (getattr(s, 'field_name', None) == 'wa_account_id')] + [
            Store.Attr("current_handler_type", predicate=is_whatsapp_channel),
            Store.One("whatsapp_ai_agent_id", predicate=is_whatsapp_channel, sudo=True),
        ]

    def _sync_field_names(self):
        field_names = super()._sync_field_names()
        field_names[None].append(Store.One("whatsapp_ai_agent_id", predicate=is_whatsapp_channel, sudo=True))
        return field_names

    def _get_whatsapp_handler(self):
        self.ensure_one()
        if self.channel_type != 'whatsapp' or not self.wa_account_id:
            return False
        account = self.wa_account_id
        if account.routing_mode == 'ai' and account.ai_agent_id:
            return ('ai', account.ai_agent_id)
        elif account.routing_mode == 'chatbot' and account.chatbot_script_id:
            return ('chatbot', account.chatbot_script_id)
        return ('human', False)

    def _is_ai_mentioned(self, message):
        if not message.body or self.channel_type != 'whatsapp' or not self.wa_account_id:
            return False
        body_text = html2plaintext(message.body)
        mentions = set(re.findall(r'@(\d+)', body_text))
        if not mentions:
            return False
        phones = set()
        account = self.wa_account_id
        if account.ai_agent_id and account.ai_agent_id.partner_id:
            p = account.ai_agent_id.partner_id
            phones.update([p.phone or '', p.mobile or ''])
        for user in account.notify_user_ids:
            if user.partner_id:
                p = user.partner_id
                phones.update([p.phone or '', p.mobile or ''])
        for phone in phones:
            clean = re.sub(r'[+\-\s]', '', phone)
            if clean in mentions:
                return True
        return False

    def message_post(self, *args, body='', message_type='notification', **kwargs):
        is_inbound = bool(self.env.context.get('whatsapp_inbound_msg_uid'))
        is_ai_response = self.env.context.get('wa_ai_response')
        is_chatbot_response = self.env.context.get('wa_chatbot_response')
        is_from_me_sync = self.env.context.get('wa_from_me_sync')
        message = super().message_post(*args, body=body, message_type=message_type, **kwargs)

        if self.channel_type != 'whatsapp':
            return message

        if (
            not is_inbound
            and not is_ai_response
            and not is_chatbot_response
            and not is_from_me_sync
            and message_type == 'comment'
            and self.current_handler_type in ('ai', 'chatbot')
            and message.author_id != self.whatsapp_partner_id
        ):
            _logger.info(
                "WhatsApp: Human takeover on channel %s (was %s)",
                self.name, self.current_handler_type,
            )
            self.write({
                'current_handler_type': 'human',
                'chatbot_current_step_id': False,
            })
            return message

        is_mentioned = self._is_ai_mentioned(message)
        if is_inbound and (message.author_id == self.whatsapp_partner_id or is_mentioned):
            handler = self._get_whatsapp_handler()
            if handler and handler[0] != 'human':
                handler_type, handler_obj = handler

                if handler_type == 'ai':
                    ai_respond_domain = safe_eval(self.wa_account_id.ai_respond_domain or '[]')
                    if ai_respond_domain and not self.filtered_domain(ai_respond_domain):
                        _logger.info(
                            "WhatsApp AI: Channel %s does not match respond domain, skipping",
                            self.name,
                        )
                        return message
                    cooldown_seconds = self.wa_account_id.whatsapp_ai_cooldown_seconds or 60
                    if self.last_ai_response_time:
                        elapsed = fields.Datetime.now() - self.last_ai_response_time
                        remaining = timedelta(seconds=cooldown_seconds) - elapsed
                        if remaining.total_seconds() > 0:
                            _logger.info(
                                "WhatsApp AI: Cooldown active for %s — %.0fs remaining",
                                self.name, remaining.total_seconds(),
                            )
                            return message
                    if not self.current_handler_type:
                        self.write({'current_handler_type': 'ai'})
                    elif self.current_handler_type == 'human':
                        self.write({'current_handler_type': 'ai'})
                        _logger.info(
                            "WhatsApp AI: Re-activating AI for %s (human takeover expired)",
                            self.name,
                        )
                    _logger.info(
                        "WhatsApp AI: Triggering AI response for channel %s (agent: %s)",
                        self.name, handler_obj.name,
                    )
                    try:
                        handler_obj.with_context(discuss_channel=self)._generate_response_for_channel(
                            message, self
                        )
                        self.write({'last_ai_response_time': fields.Datetime.now()})
                    except Exception:
                        _logger.exception("WhatsApp AI: Error generating response for channel %s", self.name)

                elif handler_type == 'chatbot':
                    if not self.current_handler_type:
                        self.write({'current_handler_type': 'chatbot'})
                    elif self.current_handler_type == 'human':
                        self.write({'current_handler_type': 'chatbot'})
                        _logger.info(
                            "WhatsApp Chatbot: Re-activating chatbot for %s (human takeover expired)",
                            self.name,
                        )
                    _logger.info(
                        "WhatsApp Chatbot: Processing step for channel %s (script: %s)",
                        self.name, handler_obj.title,
                    )
                    try:
                        self._process_chatbot_step(message, handler_obj)
                    except Exception:
                        _logger.exception("WhatsApp Chatbot: Error processing step for channel %s", self.name)

        return message

    def _match_whatsapp_answer(self, step, body_text):
        """Match user's WhatsApp text to a chatbot answer by name or index."""
        if not step.answer_ids:
            return step.answer_ids
        text = body_text.strip().lower()
        answers = step.answer_ids
        exact = answers.filtered(lambda a: a.name.strip().lower() == text)
        if exact:
            return exact[:1]
        try:
            idx = int(text)
            if 1 <= idx <= len(answers):
                return answers[idx - 1:idx]
        except (ValueError, IndexError):
            pass
        contains = answers.filtered(lambda a: text in a.name.strip().lower())
        if contains:
            return contains[:1]
        return self.env['chatbot.script.answer']

    def _wa_format_step_body(self, step, original_body):
        """Append numbered answer options to bot message for WhatsApp text-only display."""
        if not step.answer_ids:
            return original_body
        from odoo.tools import html2plaintext, plaintext2html
        text = html2plaintext(original_body).strip()
        options = []
        for i, ans in enumerate(step.answer_ids, 1):
            options.append(f"{i}. {ans.name.strip()}")
        return plaintext2html(text + "\n\n" + "\n".join(options))

    def _process_chatbot_step(self, message, chatbot_script):
        """Process an inbound message through the chatbot step engine."""
        self.ensure_one()

        # Chatbot replies are outbound messages.  Keep the inbound uid out of
        # the context so that discuss_channel#message_post does not create a
        # second inbound whatsapp.message record with the same msg_uid, and tag
        # the context so this module knows not to treat the reply as a human
        # takeover.
        clean_ctx = {k: v for k, v in self.env.context.items() if k != 'whatsapp_inbound_msg_uid'}
        clean_ctx['wa_chatbot_response'] = True
        clean_self = self.with_context(clean_ctx)

        if not clean_self.chatbot_current_step_id:
            starting = chatbot_script._get_welcome_steps()
            if not starting:
                return
            last_welcome = starting[-1]
            clean_self.write({'chatbot_current_step_id': last_welcome.id})
            posted = chatbot_script._post_welcome_steps(clean_self)
            for msg in posted:
                clean_self._send_whatsapp_message(msg)
            return

        current_step = clean_self.chatbot_current_step_id
        body_text = html2plaintext(message.body) if message.body else ''

        if current_step.answer_ids:
            matched = clean_self._match_whatsapp_answer(current_step, body_text)
            last_bot_msg = clean_self.env['chatbot.message'].sudo().search([
                ('discuss_channel_id', '=', clean_self.id),
                ('script_step_id', '=', current_step.id),
            ], limit=1, order='id DESC')
            if matched and last_bot_msg:
                last_bot_msg.write({'user_script_answer_id': matched[:1].id})

        try:
            next_step = current_step._process_answer(clean_self, body_text)
        except Exception as e:
            _logger.warning("WhatsApp Chatbot: Answer processing error: %s", e)
            return

        if not next_step:
            _logger.info("WhatsApp Chatbot: Script ended for channel %s", clean_self.name)
            clean_self.write({'current_handler_type': 'human', 'chatbot_current_step_id': False})
            return

        clean_self.write({'chatbot_current_step_id': next_step.id})

        if next_step.step_type == 'forward_operator':
            _logger.info("WhatsApp Chatbot: Forward to operator triggered for channel %s", clean_self.name)
            clean_self.action_human_takeover()
            return

        posted_message = next_step._process_step(clean_self)
        if posted_message and hasattr(posted_message, 'id'):
            if next_step.answer_ids and posted_message.body:
                updated_body = clean_self._wa_format_step_body(next_step, posted_message.body)
                posted_message.sudo().write({'body': updated_body})
            clean_self._send_whatsapp_message(posted_message)

    def _send_whatsapp_message(self, mail_message):
        """Send a mail.message posted by bot/AI via Evolution API."""
        if not mail_message or not mail_message.body:
            return
        from odoo.tools import html2plaintext
        wa_msg = self.env['whatsapp.message'].sudo().create({
            'body': html2plaintext(mail_message.body),
            'mobile_number': self.whatsapp_number,
            'wa_account_id': self.wa_account_id.id,
            'mail_message_id': mail_message.id,
            'message_type': 'outbound',
            'state': 'outgoing',
        })
        wa_msg._send_message()

    def action_human_takeover(self):
        """Hand off this channel from AI/chatbot to human operators."""
        self.ensure_one()
        self.write({
            'current_handler_type': 'human',
            'chatbot_current_step_id': False,
        })
        self.message_post(
            body='A human operator has taken over this conversation.',
            message_type='notification',
            subtype_xmlid='mail.mt_comment',
        )
        if self.wa_account_id and self.wa_account_id.notify_user_ids:
            partner_ids = self.wa_account_id.notify_user_ids.partner_id.ids
            self._broadcast(partner_ids)
