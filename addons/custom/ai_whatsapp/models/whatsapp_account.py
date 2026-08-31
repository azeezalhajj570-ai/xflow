from odoo import fields, models


class WhatsAppAccount(models.Model):
    _inherit = 'whatsapp.account'

    routing_mode = fields.Selection([
        ('ai', 'AI Agent'),
        ('chatbot', 'Chatbot Script'),
        ('human', 'Human Only'),
    ], string='Routing Mode', default='ai',
        help='How incoming WhatsApp messages are handled:\n'
             '- AI Agent: AI automatically replies\n'
             '- Chatbot Script: Scripted chatbot handles the conversation\n'
             '- Human Only: Messages are forwarded to operators only')

    whatsapp_ai_cooldown_seconds = fields.Integer(
        string='AI Cooldown (seconds)',
        default=60,
        help='Minimum interval between AI responses to the same conversation.',
    )

    ai_respond_domain = fields.Text(
        string='AI Respond Filter',
        default='[]',
        help='Optional domain to restrict which conversations the AI agent responds to. '
             'Leave empty to respond to all conversations on this account.\n'
             'Example: [("whatsapp_partner_id", "!=", False)] to only respond to conversations with known customers.',
    )

    ai_agent_id = fields.Many2one(
        comodel_name='ai.agent',
        string='AI Agent',
        help='AI agent that will automatically respond to incoming WhatsApp messages. '
             'Leave empty to disable AI auto-reply.',
        domain="[('is_system_agent', '=', False)]",
    )

    chatbot_script_id = fields.Many2one(
        comodel_name='chatbot.script',
        string='Chatbot Script',
        help='Scripted chatbot that handles incoming WhatsApp conversations step by step.',
    )
