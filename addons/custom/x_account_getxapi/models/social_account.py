# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Extend social.account with the GetXAPI provider option.

Adds 'getxapi' to the x_provider selection so accounts can use GetXAPI
as their X API provider.
"""

from odoo import fields, models


class SocialAccount(models.Model):
    _inherit = 'social.account'

    x_provider = fields.Selection(
        selection_add=[
            ('getxapi', 'GetXAPI REST API'),
        ],
        ondelete={'getxapi': 'cascade'},
    )
