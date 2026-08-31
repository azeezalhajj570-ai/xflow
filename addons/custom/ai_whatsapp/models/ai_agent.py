import logging
from textwrap import dedent

from odoo import fields, models
from odoo.addons.ai.models.ai_agent import TEMPERATURE_MAP
from odoo.addons.ai.utils.llm_api_service import LLMApiService

_logger = logging.getLogger(__name__)


PREPROMPTS = {
    'whatsapp': dedent("""
        - You are a WhatsApp customer support agent. Keep responses short and concise.
        - WhatsApp messages have a character limit preference — be brief.
        - Never use markdown — no asterisks, no bold, no italics, no bullet lists.
        - Use plain text with simple line breaks for structure.
        - Prefer natural sentences over lists. If you must list, use dashes.
        - Avoid using HTML elements in your response.
        - If you don't know the answer, say so honestly rather than making up information.
    """).strip(),
}


class AIAgent(models.Model):
    _inherit = 'ai.agent'

    whatsapp_account_ids = fields.One2many(
        comodel_name='whatsapp.account',
        inverse_name='ai_agent_id',
        string='WhatsApp Accounts',
        help='WhatsApp accounts where this AI agent is active',
    )

    def _is_user_access_allowed(self):
        return super()._is_user_access_allowed() or bool(self.whatsapp_account_ids)

    def _post_ai_response(self, channel, message):
        from odoo.tools import html_sanitize

        if channel.channel_type != 'whatsapp':
            return super()._post_ai_response(channel, message)

        formatted_message = html_sanitize(message)
        clean_ctx = {k: v for k, v in channel.env.context.items() if k != 'whatsapp_inbound_msg_uid'}
        clean_ctx['wa_ai_response'] = True
        channel.sudo().with_context(clean_ctx).message_post(
            author_id=self.partner_id.id,
            body=formatted_message,
            message_type='comment',
            silent=True,
            subtype_xmlid='mail.mt_comment',
        )

    def _build_system_context(self, extra_system_context: str = ""):
        messages = super()._build_system_context(extra_system_context)
        discuss_channel = self.env.context.get('discuss_channel', self.env['discuss.channel'])
        if discuss_channel.channel_type == 'whatsapp':
            messages.append(PREPROMPTS['whatsapp'])
        return messages

    def _retrieve_chat_history(self, discuss_channel, no_messages=20):
        if discuss_channel.channel_type != 'whatsapp':
            return super()._retrieve_chat_history(discuss_channel, no_messages)

        import html as _html
        from odoo.tools import html2plaintext

        max_messages = max(no_messages, 50)
        chat_history = []
        for message in discuss_channel.message_ids[1 : max_messages + 1]:
            if not message.body:
                chat_history.append({'content': '', 'role': 'user'})
                continue
            body_text = _html.unescape(message.body)
            content = html2plaintext(body_text)

            image_attachments = message.sudo().attachment_ids.filtered(
                lambda a: a.mimetype and a.mimetype.startswith('image/')
            )
            if image_attachments:
                img_names = ', '.join(a.name for a in image_attachments)
                content += f'\n[Attached images: {img_names}]'

            chat_history.append({
                'content': content,
                'role': 'assistant' if message.sudo().author_id.agent_ids else 'user',
            })
        chat_history.reverse()
        return chat_history

    def _build_extra_system_context(self, discuss_channel):
        extra = super()._build_extra_system_context(discuss_channel)
        if discuss_channel.channel_type != 'whatsapp':
            return extra

        wa_context = []
        if discuss_channel.whatsapp_partner_id:
            partner = discuss_channel.whatsapp_partner_id
            wa_context.append(f"Customer name: {partner.name}")
            wa_context.append(f"Customer phone: {partner.phone or discuss_channel.whatsapp_number or ''}")
        if discuss_channel.wa_account_id:
            wa_context.append(f"WhatsApp account: {discuss_channel.wa_account_id.name}")
        if wa_context:
            extra += "\n\n" + "\n".join(wa_context) if extra else "\n".join(wa_context)
        return extra

    def _generate_response(self, prompt, chat_history=None, extra_system_context="", files=None):
        self.ensure_one()
        _logger.debug("[AI Prompt] %s", prompt)
        system_messages = self._build_system_context(extra_system_context=extra_system_context)
        if rag_context := self._build_rag_context(prompt):
            system_messages.extend(rag_context)
        llm_response = LLMApiService(env=self.env, provider=self._get_provider()).request_llm(
            self.llm_model,
            system_messages,
            [],
            inputs=(chat_history or []) + [{'role': 'user', 'content': prompt}],
            tools=self.sudo().topic_ids.tool_ids._get_ai_tools(),
            temperature=TEMPERATURE_MAP[self.response_style],
            files=files or [],
        )
        if rag_context:
            llm_response = self._get_llm_response_with_sources(llm_response)
        return llm_response

    def _generate_response_for_channel(self, mail_message, channel):
        if channel.channel_type != 'whatsapp':
            return super()._generate_response_for_channel(mail_message, channel)

        self.ensure_one()
        prompt, session_info_context = self._parse_user_message(mail_message)

        files = self._build_image_files(mail_message, channel)

        try:
            response = self.with_context(discuss_channel=channel)._generate_response(
                prompt=prompt,
                chat_history=[{'content': session_info_context, 'role': 'user'}] + self._retrieve_chat_history(channel),
                extra_system_context=self._build_extra_system_context(channel),
                files=files,
            )
        except Exception:
            if self.env.user._is_internal():
                raise
            response = [self.env._("Oops, it looks like our AI is unreachable")]
        for message in response or []:
            self._post_ai_response(channel, message)

    def _build_image_files(self, mail_message, channel):
        files = []
        seen = set()
        for att in mail_message.sudo().attachment_ids:
            if att.mimetype and att.mimetype.startswith('image/') and att.datas:
                key = (att.id, att.checksum)
                if key not in seen:
                    seen.add(key)
                    files.append({
                        'mimetype': att.mimetype or 'image/jpeg',
                        'value': att.datas,
                        'file_ref': f'<{att.name}>',
                    })
        for msg in channel.sudo().message_ids[:15]:
            if msg == mail_message:
                continue
            for att in msg.attachment_ids:
                if att.mimetype and att.mimetype.startswith('image/') and att.datas:
                    key = (att.id, att.checksum)
                    if key not in seen:
                        seen.add(key)
                        files.append({
                            'mimetype': att.mimetype or 'image/jpeg',
                            'value': att.datas,
                            'file_ref': f'<{att.name}>',
                        })
        return files

    def _tool_web_search(self, ai, query, retrieval_mode='fact', context_hint=''):
        """Perform a web search using the agent's LLM provider web grounding.

        :param ai: the AI eval context dict (unused but kept for API consistency)
        :param query: comprehensive search query
        :param retrieval_mode: 'fact', 'summary', or 'deep'
        :param context_hint: how the result will be used
        :return: str — search results with [WEB_SOURCE:xxx] citations
        """
        query = query or ''
        retrieval_mode = retrieval_mode or 'fact'
        context_hint = context_hint or ''

        if not query:
            return 'No query provided for web search.'

        agent = self
        if not self:
            channel = self.env.context.get('discuss_channel')
            if channel and channel.wa_account_id and channel.wa_account_id.ai_agent_id:
                agent = channel.wa_account_id.ai_agent_id
            else:
                agent = self.env['ai.agent'].search([('id', '!=', False)], limit=1)
        if not agent:
            return 'Web search failed: no AI agent found.'

        system_prompt = (
            "You are a search assistant. Use the web search tool to find "
            "factual, up-to-date information.\n"
            "Return results as plain text.\n"
            "Citation rules (strictly enforced):\n"
            "- Factual claims MUST be cited by placing source citations at the "
            "end of the paragraph that contains them.\n"
            "- Format: [WEB_SOURCE:<source_id>] where source_id is the exact "
            "alphanumeric key as it appears in the tool result, copied "
            "character for character.\n"
            "- Each paragraph carries only the citations for claims made within it.\n"
            "- Never place citations mid-sentence or mid-paragraph.\n"
        )

        if retrieval_mode == 'fact':
            system_prompt += "\nDepth: fact — quick factual lookups (addresses, dates, names). Be very brief."
        elif retrieval_mode == 'summary':
            system_prompt += "\nDepth: summary — a balanced summary of the topic with multiple sources."
        elif retrieval_mode == 'deep':
            system_prompt += "\nDepth: deep — in-depth research with multiple perspectives and rich detail."

        if context_hint:
            system_prompt += f"\nContext hint: {context_hint}"

        try:
            provider = agent._get_provider()
            llm_model = agent.llm_model
            service = LLMApiService(self.env, provider=provider)
            responses = service.request_llm(
                llm_model,
                [system_prompt],
                [query],
                web_grounding=True,
            )
            return '\n'.join(responses) if responses else 'No results found.'
        except Exception as e:
            _logger.exception("Web search tool error: %s", e)
            return f'Web search failed: {e}'
