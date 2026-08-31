# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'WhatsApp',
    'category': 'Marketing',
    'summary': 'Send WhatsApp messages, templates, and notifications directly from Odoo',
    'version': '19.0.0.1',
    'description': """
WhatsApp Messaging for Odoo allows you to communicate with your customers directly from Odoo using WhatsApp.

Key Features:
- Send WhatsApp messages to contacts and customers
- Use WhatsApp message templates
- Integrated WhatsApp composer inside Odoo
- Phone number validation before sending
- Chatter & messaging menu integration
- Multi-company support
- Secure access control and audit-friendly logging

Use Cases:
- Sales follow-ups
- Order confirmations
- Customer notifications
- Marketing campaigns
- Lead engagement

Fully integrated with Odoo's messaging and contact management system.
""",
    'depends': [
        'base',
        'web',
        'mail',
        'phone_validation',
        'sale',
        'account',
        'point_of_sale',
    ],
    'data': [
        'security/whatsapp_security.xml',
        'security/ir.model.access.csv',
        'security/ir_rules.xml',
        'data/whatsapp_template_data.xml',
        'views/whatsapp_account_views.xml',
        'views/whatsapp_message_views.xml',
        'views/whatsapp_template_views.xml',
        'wizard/whatsapp_composer_views.xml',
        'views/whatsapp_menus.xml',
    ],
    'demo': [
        'data/whatsapp_evaluation_demo.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'whatsapp_evaluation/static/src/core/common/message_patch.js',
            'whatsapp_evaluation/static/src/core/common/message_patch.xml',
            'whatsapp_evaluation/static/src/core/common/store_service_patch.js',
            'whatsapp_evaluation/static/src/core/common/thread_model_patch.js',
            'whatsapp_evaluation/static/src/core/public_web/discuss_app_model_patch.js',
            'whatsapp_evaluation/static/src/core/public_web/thread_model_patch.js',
            'whatsapp_evaluation/static/src/core/web/channel_member_list_patch.js',
            'whatsapp_evaluation/static/src/core/web/discuss_app_category_model_patch.js',
            'whatsapp_evaluation/static/src/chatter/web/chatter_patch.js',
            'whatsapp_evaluation/static/src/chatter/web/chatter_patch.xml',
        ],
    },
    'images': ['static/description/main_screenshot.png'],
    'external_dependencies': {
        'python': ['phonenumbers'],
    },
    'author': 'Azeez <azeez@azeez-tech.com>',
    'website': 'https://github.com/azeezalhajj570-ai/whatsapp_addons',
    'license': 'OPL-1',
    'application': True,
    'installable': True,
    'price': 0.0,
    'currency': 'EUR',
}
