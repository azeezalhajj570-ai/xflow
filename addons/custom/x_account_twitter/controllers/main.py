# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""OAuth 2.0 (PKCE) account-linking controller for X.

Replaces the legacy social_twitter OAuth 1.0a link flow (request_token ->
authorize -> callback), which X no longer offers to Free-tier/new apps:

    /x_account/twitter/oauth2/authorize
        -> generates code_verifier + state, stores them in the session, and
           redirects the user to X's OAuth 2.0 authorize page (PKCE S256)

    /x_account/twitter/oauth2/callback
        -> validates the state, exchanges the code for access/refresh tokens,
           fetches GET /2/users/me, and creates/updates the social.account

The callback path must be registered in the X app's "Callback URI / Redirect
URL" settings (https://<your-odoo>/x_account/twitter/oauth2/callback).
"""

import logging
import secrets

from odoo import _, http
from odoo.exceptions import UserError
from odoo.http import request
from odoo.tools.urls import urljoin as url_join

from odoo.addons.x_account_twitter.services import twitter_errors
from odoo.addons.x_account_twitter.services.twitter_oauth2 import TwitterOAuth2Client

_logger = logging.getLogger(__name__)


class XAccountTwitterOAuth2Controller(http.Controller):
    _FLOW_KEY = 'x_twitter_oauth2_flow'

    # ------------------------------------------------------------- helpers
    def _settings(self):
        return request.env['ir.config_parameter'].sudo()

    def _twitter_media(self):
        return request.env['social.media'].sudo().search(
            [('media_type', '=', 'twitter')], limit=1)

    def _oauth2_credentials(self):
        client_id = self._settings().get_param('social.twitter_oauth2_client_id')
        client_secret = self._settings().get_param('social.twitter_oauth2_client_secret')
        return client_id, client_secret

    def _callback_uri(self, media):
        return url_join(media.get_base_url(), 'x_account/twitter/oauth2/callback')

    def _error(self, message):
        return request.render(
            'social.social_http_error_view', {'error_message': message})

    # ------------------------------------------------------------ authorize
    @http.route('/x_account/twitter/oauth2/authorize', type='http', auth='user',
                methods=['GET'], csrf=False)
    def oauth2_authorize(self, **kwargs):
        if not request.env.user.has_group('social.group_social_manager'):
            return self._error(_('Unauthorized. Please contact your administrator.'))

        media = self._twitter_media()
        client_id, client_secret = self._oauth2_credentials()
        if not media or not client_id or not client_secret:
            return self._error(_(
                'Please configure the X OAuth 2.0 Client ID and Client Secret '
                'in the X Account settings first.'))

        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        client = TwitterOAuth2Client(
            client_id, client_secret, self._callback_uri(media))
        authorize_url = client.build_authorize_url(state, code_verifier)
        request.session[self._FLOW_KEY] = {
            'state': state,
            'code_verifier': code_verifier,
            'media_id': media.id,
        }
        _logger.info(
            'x_account_twitter: starting OAuth 2.0 authorization media=%s',
            media.media_type)
        return request.redirect(authorize_url, local=False)

    # ------------------------------------------------------------- callback
    @http.route('/x_account/twitter/oauth2/callback', type='http', auth='user',
                methods=['GET'], csrf=False)
    def oauth2_callback(self, state=None, code=None, error=None, **kwargs):
        if not request.env.user.has_group('social.group_social_manager'):
            return self._error(_('Unauthorized. Please contact your administrator.'))

        if error:
            return self._error(_('X authorization was denied or failed.'))

        flow = request.session.pop(self._FLOW_KEY, None)
        if not flow or not state or state != flow.get('state'):
            _logger.warning(
                'x_account_twitter: CSRF state mismatch for OAuth 2.0 callback')
            return self._error(_(
                'Invalid state parameter. This could be a CSRF attack – please '
                'try adding the account again.'))

        code_verifier = flow.get('code_verifier')
        media = request.env['social.media'].sudo().browse(flow.get('media_id'))
        if not code or not code_verifier or not media.exists():
            return self._error(_(
                'X did not provide a valid authorization code. Please try again.'))

        client_id, client_secret = self._oauth2_credentials()
        try:
            client = TwitterOAuth2Client(
                client_id, client_secret, self._callback_uri(media))
            tokens = client.exchange_code(code, code_verifier)
            access_token = tokens.get('access_token')
            refresh_token = tokens.get('refresh_token')
            if not access_token or not refresh_token:
                return self._error(_(
                    'X did not return the refresh token. Make sure the '
                    '"offline.access" scope is granted to the app.'))
            expires_in = int(tokens.get('expires_in') or 7200)
            user = client.get_me(access_token)
            if not user.get('id'):
                return self._error(_('X did not return the user information.'))
        except (twitter_errors.TwitterError, UserError) as exc:
            _logger.exception('x_account_twitter: OAuth 2.0 exchange failed')
            return self._error(_(
                'X authorization failed (%s). Please try again.', exc))

        try:
            request.env['social.account'].sudo()._create_or_update_twitter_oauth2(
                media, user, tokens, expires_in)
        except UserError as exc:
            return self._error(str(exc))

        return request.redirect('/odoo/action-social.action_social_stream_post')