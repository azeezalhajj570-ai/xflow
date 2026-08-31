# Copyright 2020 Akretion (https://www.akretion.com).
# @author Sébastien BEAU <sebastien.beau@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "IAP Alternative Provider",
    "summary": "Base module for providing alternative provider for iap apps",
    "version": "19.0.1.0.0",
    "category": "Tools",
    "description": """
IAP Alternative Provider
========================
Base module for providing alternative providers for In-App Purchasing applications.

Extends the IAP framework to allow custom/alternative service providers instead
of the default Odoo IAP service. Useful for on-premise deployments or when
using self-hosted relay services.

Features:
- Pluggable alternative provider architecture
- Seamless integration with existing IAP-based modules
- Configurable via system settings
    """,
    "website": "https://github.com/OCA/server-tools",
    "author": "Akretion, Odoo Community Association (OCA)",
    "maintainers": ["sebastienbeau"],
    "license": "AGPL-3",
    "application": False,
    "installable": True,
    "depends": ["iap"],
    "data": ["views/iap_account_view.xml"],
    "images": ["static/description/banner.png"],
    "external_dependencies": {},
    "price": 0.0,
    "currency": "EUR",
}
