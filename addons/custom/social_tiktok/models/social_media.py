# -*- coding: utf-8 -*-
import uuid

from werkzeug.urls import url_encode

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools.urls import urljoin as url_join


class SocialMedia(models.Model):
    _inherit = 'social.media'

    _TIKTOK_OAUTH_ENDPOINT = 'https://www.tiktok.com/v2/auth/authorize/'
    _TIKTOK_TOKEN_ENDPOINT = 'https://open.tiktokapis.com/v2/oauth/token/'
    _TIKTOK_API_ENDPOINT = 'https://open.tiktokapis.com/v2/'

    media_type = fields.Selection(selection_add=[('tiktok', 'TikTok')])

    def _action_add_account(self):
        self.ensure_one()

        if self.media_type != 'tiktok':
            return super()._action_add_account()

        tiktok_client_key = self.env['ir.config_parameter'].sudo().get_param('social.tiktok_client_key')
        tiktok_client_secret = self.env['ir.config_parameter'].sudo().get_param('social.tiktok_client_secret')

        if not tiktok_client_key or not tiktok_client_secret:
            raise UserError(_(
                "Please configure your TikTok Client Key and Client Secret in "
                "Settings > Social Marketing before adding a TikTok account."
            ))

        # Generate a CSRF state token stored temporarily in system parameters
        state = str(uuid.uuid4())
        self.env['ir.config_parameter'].sudo().set_param('social.tiktok_oauth_state', state)

        scopes = ['user.info.basic', 'user.info.profile', 'user.info.stats', 'video.list', 'video.publish']
        params = {
            'client_key': tiktok_client_key,
            'redirect_uri': url_join(self.get_base_url(), 'social_tiktok/callback'),
            'response_type': 'code',
            'scope': ','.join(scopes),
            'state': state,
        }

        return {
            'type': 'ir.actions.act_url',
            'url': '%s?%s' % (self._TIKTOK_OAUTH_ENDPOINT, url_encode(params)),
            'target': 'self',
        }
