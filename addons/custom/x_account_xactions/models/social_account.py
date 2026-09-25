# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Add the XActions option to the account's provider selection.

The XActions API authenticates with a Bearer JWT (global config) and reads the
session stored inside XActions; an optional per-account id selects which linked
XActions account that is.
"""

from odoo import fields, models


class SocialAccount(models.Model):
    _inherit = 'social.account'

    x_provider = fields.Selection(
        selection_add=[
            ('xactions', 'XActions API'),
        ],
        ondelete={'xactions': 'cascade'},
    )

    x_xactions_account_id = fields.Char(
        string='XActions Account ID',
        help='Optional id of the linked XActions account whose stored session '
             'the API should read with. Leave empty to use the API user\'s own '
             'connected session.',
    )
