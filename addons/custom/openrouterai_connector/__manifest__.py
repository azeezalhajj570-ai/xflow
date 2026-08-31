{
    'name': 'OpenRouterAI Connector',
    'version': '19.0.1.0.0',
    'category': 'AI',
    'summary': 'OpenRouter AI provider - sync 700+ models, chat completions, usage tracking',
    'description': """
        Seamlessly integrate OpenRouter's AI model catalog with Odoo.

        Features:
        - Adds OpenRouter as an AI provider in Odoo's AI framework
        - Syncs the complete OpenRouter model catalog (700+ models)
        - Provides chat completion with usage tracking and cost logging
        - Reconciles token usage and costs via OpenRouter generation API
        - Supports tool calling, multi-modal models, and provider routing
        - Automatically tracks prompt/completion tokens, reasoning tokens, cached tokens
        - Logs all requests with full payload inspection (system users only)
        - Handles large payloads efficiently with attachment offloading
        - Compatible with Odoo's AI agents and chat interface
        - Monitors model pricing (prompt, completion, image, request)
        - Tracks input/output modalities per model

        Configuration:
        1. Go to OpenRouter AI > Configurations
        2. Add your OpenRouter API key
        3. Click "Sync Models" to fetch the latest model catalog
        4. Start chatting with any available model
    """,
    'author': 'Azeez T.',
    'author_email': 'support@azeez-tech.com',
    'website': 'https://azeez-tech.com',
    'depends': [
        'base',
        'mail',
        'ai',
        'ai_app',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/openrouter_cron_data.xml',
        'views/openrouter_provider_views.xml',
        'views/openrouter_company_views.xml',
        'views/openrouter_model_views.xml',
        'views/openrouter_request_log_views.xml',
        'views/openrouter_modality_views.xml',
        'views/openrouter_sync_wizard_views.xml',
        'views/res_config_settings_views.xml',
        'views/openrouter_menu.xml',
    ],
    'images': [
        'static/description/cover.png',
        'static/description/screenshots/chat_screenshot.png',
        'static/description/screenshots/models_list.png',
        'static/description/screenshots/provider_config.png',
    ],
    'external_dependencies': {
        'python': ['requests'],
    },
    'application': False,
    'installable': True,
    'license': 'LGPL-3',
    'currency': 'EUR',
}
