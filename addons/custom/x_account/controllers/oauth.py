# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import werkzeug.urls

from odoo import http
from odoo.http import request
from odoo.addons.auth_oauth.controllers.main import OAuthLogin


class XAccountOAuthLogin(OAuthLogin):
    
    def list_providers(self):
        try:
            providers = request.env['auth.oauth.provider'].sudo().search_read([('enabled', '=', True)])
        except Exception:
            providers = []
        
        for provider in providers:
            # Use web.base.url instead of request.httprequest.url_root
            # This respects the configured base URL and works correctly behind reverse proxies
            base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url')
            if not base_url.endswith('/'):
                base_url += '/'
            return_url = base_url + 'auth_oauth/signin'
            
            state = self.get_state(provider)
            params = dict(
                response_type='token',
                client_id=provider['client_id'],
                redirect_uri=return_url,
                scope=provider['scope'],
                state=json.dumps(state),
            )
            provider['auth_link'] = "%s?%s" % (provider['auth_endpoint'], werkzeug.urls.url_encode(params))
        
        return providers
