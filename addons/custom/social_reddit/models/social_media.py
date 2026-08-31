# Part of Odoo. See LICENSE file for full copyright and licensing details.
import uuid
import logging

from werkzeug.urls import url_encode

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools.urls import urljoin as url_join

_logger = logging.getLogger(__name__)


class SocialMedia(models.Model):
    _inherit = 'social.media'

    _REDDIT_AUTH_ENDPOINT = 'https://www.reddit.com/api/v1'
    _REDDIT_OAUTH_SCOPE = 'identity read submit edit mysubreddits history subscribe modflair'

    media_type = fields.Selection(selection_add=[('reddit', 'Reddit')])

    def _action_add_account(self):
        self.ensure_one()
        if self.media_type != 'reddit':
            return super()._action_add_account()

        client_id = self.env['ir.config_parameter'].sudo().get_param('social.reddit_client_id')
        client_secret = self.env['ir.config_parameter'].sudo().get_param('social.reddit_client_secret')
        if not client_id or not client_secret:
            raise UserError(_(
                'Please configure your Reddit Client ID and Client Secret in '
                'Settings > Social Marketing before adding a Reddit account.'
            ))

        state = str(uuid.uuid4())
        self.env['ir.config_parameter'].sudo().set_param('social.reddit_oauth_state', state)

        params = {
            'client_id': client_id,
            'response_type': 'code',
            'state': state,
            'redirect_uri': url_join(self.get_base_url(), 'social_reddit/callback'),
            'duration': 'permanent',
            'scope': self._REDDIT_OAUTH_SCOPE,
        }

        return {
            'type': 'ir.actions.act_url',
            'url': '%s/authorize?%s' % (self._REDDIT_AUTH_ENDPOINT, url_encode(params)),
            'target': 'self',
        }
