# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'X Account & Session Platform',
    'category': 'Marketing/Social Marketing',
    'summary': 'Native X account & session management replacing XAction',
    'version': '19.0.1.0.0',
    'description': """
X Account & Session Platform for Odoo
=====================================
Native replacement for the XAction runtime. Manages X accounts, encrypted
session persistence/restoration/validation, account lifecycle, provider
integration, tasks, DM / group-DM integration, account grouping, and
automation — all inside Odoo.

Highlights:
- Extends social.account with X session and lifecycle fields
- Encrypted, key-separated session store (x.session.store)
- Isolated, replaceable SessionWebProvider (ported XAction cookie client)
- Durable task queue (x.account.task) with ir.cron worker
- DM / group-DM via discuss.channel + x.message + res.partner
- Account grouping (x.account.group) + base_automation rules
- Non-destructive, staged migration from XAction
- Optional publish/stats path via XOfficialPublishAdapter (OAuth 1.0a)
    """,
    'depends': [
        'social',
        'social_twitter',
        'contacts',
        'base_automation',
        'mail',
    ],
    'data': [
        'security/x_account_security.xml',
        'security/ir.model.access.csv',
        'security/ir_rules.xml',
        'data/cron.xml',
        'data/base_automation.xml',
        'data/server_actions.xml',
        'views/res_config_settings_views.xml',
        'views/social_account_views.xml',
        'views/import_session_views.xml',
        'views/x_group_channel_views.xml',
        'views/x_message_views.xml',
        'views/account_group_views.xml',
        'views/account_task_views.xml',
        'views/menus.xml',
    ],
    'external_dependencies': {
        'python': ['cryptography'],
    },
    'installable': True,
    'application': True,
    'license': 'OEEL-1',
}
