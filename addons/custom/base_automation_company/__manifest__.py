{
    'name': 'Automation Rules - Multi-Company',
    'version': '19.0.1.0.0',
    'category': 'Technical',
    'summary': 'Add company-based access control to automation rules',
    'description': """
        Adds company_id field to base.automation and implements record rules
        for multi-company access control.
    """,
    'depends': ['base_automation'],
    'data': [
        'security/ir_rules.xml',
        'data/base_automation_company_data.xml',
    ],
    'installable': True,
    'auto_install': False,
    'license': 'OEEL-1',
}
