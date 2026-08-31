
import logging
from urllib.parse import urlparse

import requests
from werkzeug.urls import url_encode, url_join

from odoo import _, models, tools
from odoo.addons.iap.tools import iap_tools
from odoo.exceptions import AccessError, UserError
from odoo.tools.urls import urljoin as url_join

_logger = logging.getLogger(__name__)


class SocialMediaCustomRelay(models.Model):
    _inherit = 'social.media'

    _DEFAULT_SOCIAL_IAP_ENDPOINT = 'http://localhost:8069'

    @staticmethod
    def _sanitize_endpoint(endpoint):
        endpoint = (endpoint or '').strip()
        if endpoint.startswith('http://') or endpoint.startswith('https://'):
            return endpoint
        return ''

    def _get_custom_relay_endpoint(self):
        icp = self.env['ir.config_parameter'].sudo()
        endpoint = (
            icp.get_param('social.custom_relay_endpoint')
            or icp.get_param('social.social_iap_endpoint')
            or self.env['social.media']._DEFAULT_SOCIAL_IAP_ENDPOINT
        )
        return self._sanitize_endpoint(endpoint)

    def _relay_get(self, route, params=None, timeout=5):
        endpoint = self._get_custom_relay_endpoint()
        if not endpoint:
            raise UserError(_(
                "Invalid relay endpoint. Please configure 'social.custom_relay_endpoint' or "
                "'social.social_iap_endpoint' with a full URL (including http:// or https://)."
            ))
        try:
            return requests.get(url_join(endpoint, route), params=params or {}, timeout=timeout)
        except requests.RequestException as err:
            raise UserError(_("Failed to contact relay endpoint: %s", err))

    @staticmethod
    def _is_valid_external_url(value):
        parsed = urlparse((value or '').strip())
        return parsed.scheme in ('http', 'https') and bool(parsed.netloc)

    def _relay_add_accounts(self, media, route, callback_path, error_tokens):
        db_uuid = self.env['ir.config_parameter'].sudo().get_param('database.uuid')
        endpoint = self._get_custom_relay_endpoint()
        callback_url = url_join(self.get_base_url(), callback_path)
        _logger.info(
            'social_custom_relay: add account requested media=%s endpoint=%s db_uuid=%s callback=%s',
            media,
            endpoint,
            db_uuid,
            callback_url,
        )
        relay_response = self._relay_get(
            route,
            params={
                'returning_url': callback_url,
                'db_uuid': db_uuid,
            },
        )
        response = (relay_response.text or '').strip()
        _logger.info(
            'social_custom_relay: add account response media=%s status=%s content_type=%s value=%s',
            media,
            relay_response.status_code,
            relay_response.headers.get('Content-Type'),
            response,
        )
        if relay_response.status_code >= 400:
            raise UserError(_("Relay endpoint returned HTTP %s. Please check your relay URL configuration.", relay_response.status_code))
        if response == 'unauthorized':
            raise UserError(_("You don't have an active subscription. Please buy one here: %s", 'https://www.odoo.com/buy'))
        if response in error_tokens:
            raise UserError(_("The url that this service requested returned an error. Please contact the author of the app."))
        if not self._is_valid_external_url(response):
            raise UserError(_(
                "Relay endpoint returned an invalid OAuth URL. "
                "Expected an absolute http(s) URL, got: %s. "
                "Please configure 'social.custom_relay_endpoint' to a reachable relay service.",
                response[:200],
            ))
        return {'type': 'ir.actions.act_url', 'url': response, 'target': 'self'}

    def _add_facebook_accounts_from_configuration(self, facebook_app_id):
        base_facebook_url = 'https://www.facebook.com/v17.0/dialog/oauth?%s'
        scopes = [
            'pages_manage_metadata',
            'pages_read_engagement',
            'pages_read_user_content',
            'pages_manage_engagement',
            'pages_manage_posts',
            'read_insights',
        ]
        if not self.env['ir.config_parameter'].sudo().get_param('social.facebook_no_business_management'):
            scopes.append('business_management')
        params = {
            'client_id': facebook_app_id,
            'redirect_uri': url_join(self.get_base_url(), 'social_facebook/callback'),
            'response_type': 'token',
            'scope': ','.join(scopes),
        }
        return {
            'type': 'ir.actions.act_url',
            'url': base_facebook_url % url_encode(params),
            'target': 'self',
        }

    def _add_facebook_accounts_from_iap(self):
        return self._relay_add_accounts(
            media='facebook',
            route='api/social/facebook/1/add_accounts',
            callback_path='social_facebook/callback',
            error_tokens=('facebook_missing_configuration', 'missing_parameters'),
        )

    def _add_instagram_accounts_from_configuration(self, instagram_app_id):
        base_url = self.get_base_url()
        base_instagram_url = 'https://www.facebook.com/v17.0/dialog/oauth?%s'
        params = {
            'client_id': instagram_app_id,
            'redirect_uri': url_join(base_url, 'social_instagram/callback'),
            'response_type': 'token',
            'state': self.csrf_token,
            'scope': ','.join([
                'instagram_basic',
                'instagram_content_publish',
                'instagram_manage_comments',
                'instagram_manage_insights',
                'pages_show_list',
                'pages_manage_metadata',
                'pages_read_engagement',
                'pages_read_user_content',
                'pages_manage_engagement',
                'pages_manage_posts',
                'read_insights',
                'business_management',
            ])
        }
        return {
            'type': 'ir.actions.act_url',
            'url': base_instagram_url % url_encode(params),
            'target': 'self',
        }

    def _add_instagram_accounts_from_iap(self):
        return self._relay_add_accounts(
            media='instagram',
            route='api/social/instagram/1/add_accounts',
            callback_path='social_instagram/callback',
            error_tokens=('instagram_missing_configuration', 'missing_parameters'),
        )

    def _add_linkedin_accounts_from_configuration(self, linkedin_app_id):
        linkedin_scope = 'r_verify openid profile r_events w_member_social email r_profile_basicinfo rw_events'
        params = {
            'response_type': 'code',
            'client_id': linkedin_app_id,
            'redirect_uri': self._get_linkedin_redirect_uri(),
            'state': self.csrf_token,
            'scope': linkedin_scope,
        }
        return {
            'type': 'ir.actions.act_url',
            'url': 'https://www.linkedin.com/oauth/v2/authorization?%s' % url_encode(params),
            'target': 'self',
        }

    def _add_linkedin_accounts_from_iap(self):
        linkedin_scope = 'r_verify openid profile r_events w_member_social email r_profile_basicinfo rw_events'
        o_redirect_uri = url_join(self.get_base_url(), 'social_linkedin/callback')
        social_iap_endpoint = self._get_custom_relay_endpoint()
        db_uuid = self.env['ir.config_parameter'].sudo().get_param('database.uuid')
        relay_response = requests.get(
            url_join(social_iap_endpoint, 'api/social/linkedin/1/add_accounts'),
            params={
                'state': self.csrf_token,
                'scope': linkedin_scope,
                'o_redirect_uri': o_redirect_uri,
                'db_uuid': db_uuid,
                'returning_url': o_redirect_uri,
            },
            timeout=5,
        )
        response = relay_response.text.strip()
        if relay_response.status_code >= 400:
            raise UserError(_("Relay endpoint returned HTTP %s.", relay_response.status_code))
        if response == 'unauthorized':
            raise UserError(_("You don't have an active subscription. Please buy one here: %s", 'https://www.odoo.com/buy'))
        if response in ('linkedin_missing_configuration', 'missing_parameters'):
            raise UserError(_("The url that this service requested returned an error. Please contact the author of the app."))
        return {'type': 'ir.actions.act_url', 'url': response, 'target': 'self'}

    def _add_youtube_accounts_from_iap(self):
        return self._relay_add_accounts(
            media='youtube',
            route='api/social/youtube/1/add_accounts',
            callback_path='social_youtube/callback',
            error_tokens=('youtube_missing_configuration',),
        )

    def _add_twitter_accounts_from_iap(self):
        return self._relay_add_accounts(
            media='twitter',
            route='api/social/twitter/1/add_accounts',
            callback_path='social_twitter/callback',
            error_tokens=('wrong_configuration',),
        )

    def _get_twitter_oauth_signature_from_iap(self, method, url, params, oauth_token_secret=''):
        params['oauth_nonce'] = str(params['oauth_nonce'])
        payload = {
            'method': method,
            'url': url,
            'params': params,
            'oauth_token_secret': oauth_token_secret,
            'db_uuid': self.env['ir.config_parameter'].sudo().get_param('database.uuid'),
        }
        endpoint = self._get_custom_relay_endpoint()
        try:
            return iap_tools.iap_jsonrpc(url_join(endpoint, 'api/social/twitter/1/get_signature'), params=payload)
        except AccessError:
            return None
