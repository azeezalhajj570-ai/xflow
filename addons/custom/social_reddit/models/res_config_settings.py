# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    reddit_client_id = fields.Char(
        string='Reddit Client ID',
        config_parameter='social.reddit_client_id',
        help='Client ID from your Reddit Web App (prefs/apps).')
    reddit_client_secret = fields.Char(
        string='Reddit Client Secret',
        config_parameter='social.reddit_client_secret',
        help='Client Secret from your Reddit Web App (prefs/apps).')
