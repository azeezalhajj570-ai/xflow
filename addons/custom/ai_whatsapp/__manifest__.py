{
    'name': 'AI WhatsApp',
    'version': '19.0.4.0.0',
    'category': 'AI',
    'summary': 'AI Agent + Chatbot auto-reply for WhatsApp, with human takeover',
    'description': """
AI WhatsApp - Full Livechat Feature Parity for WhatsApp
========================================================

This module brings the full livechat routing system to WhatsApp conversations:
AI agents, scripted chatbots, and human operators — all in one workflow.

Features:
---------
* AI agents automatically respond to incoming WhatsApp messages
* Scripted chatbots with step-based conversation flows
* Three routing modes: AI Agent, Chatbot Script, or Human Only
* Seamless human takeover with "Take Over" button
* WhatsApp-specific system prompts for concise, plain text responses
* Canned responses via standard Discuss :: shortcut
* Full integration with existing AI agent framework
    """,
    'author': 'Azeez Tech',
    'website': 'https://github.com/azeezalhajj570-ai/whatsapp_addons',
    'license': 'LGPL-3',
    'depends': [
        'whatsapp_evaluation',
        'ai',
        'im_livechat',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ai_agent_data.xml',
        'views/whatsapp_account_views.xml',
        'views/ai_whatsapp_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ai_whatsapp/static/src/discuss/core/common/**/*',
        ],
    },
    'images': ['static/description/banner.png'],
    'external_dependencies': {},
    'price': 0.0,
    'currency': 'EUR',
    'installable': True,
    'application': True,
    'auto_install': False,
}
