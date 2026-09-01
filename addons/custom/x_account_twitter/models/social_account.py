# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Make account linking reuse an OAuth 2.0 (PKCE) flow for the official X API.

The OAuth 1.0a flow X used for years is no longer offered to Free-tier/new
apps (request_token returns 401), so linking goes through OAuth 2.0 with PKCE:

    Social Marketing > Social Accounts > Twitter/X > Link Account
        -> OAuth 2.0 authorize on x.com (PKCE)
        -> callback exchanges the code for access/refresh tokens
        -> creates a social.account carrying the OAuth 2.0 tokens
        -> the account is marked as the 'twitter' X provider

OAuth 2.0 access tokens expire after ~2 hours, so the account also stores a
refresh token and refreshes lazily before a call (or on 401).
"""

from datetime import timedelta

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from odoo.addons.x_account_twitter.services import twitter_errors
from odoo.addons.x_account_twitter.services.twitter_oauth2 import TwitterOAuth2Client


class SocialAccount(models.Model):
    _inherit = 'social.account'

    x_provider = fields.Selection(
        selection_add=[
            ('twitter', 'Twitter (Official API)'),
        ],
        ondelete={'twitter': 'cascade'},
    )

    x_auth_method = fields.Selection(
        selection_add=[('oauth2', 'OAuth 2.0 (Official API)')],
    )

    x_oauth2_access_token = fields.Char(
        string='X OAuth 2.0 Access Token',
        help='OAuth 2.0 access token (user context). Expires after ~2 hours; '
             'automatically refreshed from x_oauth2_refresh_token.',
    )
    x_oauth2_refresh_token = fields.Char(
        string='X OAuth 2.0 Refresh Token',
        help='OAuth 2.0 refresh token used to mint new access tokens '
             '(granted by the offline.access scope).',
    )
    x_oauth2_token_expires_at = fields.Datetime(
        string='X OAuth 2.0 Token Expiry',
        help='UTC datetime after which the access token must be refreshed.',
    )

    def _get_oauth1_defaults(self):
        """Provider defaults applied to accounts linked through the legacy
        OAuth 1.0a flow (twitter_oauth_token present)."""
        return {
            'x_provider': 'twitter',
            'x_auth_method': 'oauth1',
        }

    def _get_oauth2_defaults(self):
        """Provider defaults applied to accounts linked through OAuth 2.0."""
        return {
            'x_provider': 'twitter',
            'x_auth_method': 'oauth2',
        }

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-assign the twitter provider to accounts created by the OAuth
        callback (OAuth 1.0a or OAuth 2.0).

        OAuth-linked accounts (tokens present) are real accounts and keep the
        default follow-stream creation. Accounts without tokens (e.g. a
        'twitter'-provider account not yet linked) must not trigger the default
        stream, which would call the real X API and fail — the same suppression
        x_account uses for session imports.
        """
        for vals in vals_list:
            media_type = vals.get('media_type')
            if not media_type and vals.get('media_id'):
                media_type = self.env['social.media'].browse(vals['media_id']).media_type
            if (media_type == 'twitter' and not vals.get('twitter_oauth_token')
                    and not vals.get('x_oauth2_access_token')
                    and not self.env.context.get('x_no_default_stream')):
                self = self.with_context(x_no_default_stream=True)
                break
        records = super().create(vals_list)
        for record, vals in zip(records, vals_list):
            if record.media_type != 'twitter' or vals.get('x_provider'):
                continue
            if record.twitter_oauth_token and not vals.get('x_oauth2_access_token'):
                record.write(self._get_oauth1_defaults())
            elif record.x_oauth2_access_token:
                record.write(self._get_oauth2_defaults())
        return records

    def _skip_oauth_stats(self):
        """Skip OAuth stats for twitter-provider accounts that have no tokens.

        Real OAuth-linked accounts (tokens present) keep their stats; a
        'twitter'-provider account without tokens must not hit social_twitter's
        slow IAP signing path.
        """
        return super()._skip_oauth_stats() | self.filtered(
            lambda a: a.media_type == 'twitter'
            and a.x_provider == 'twitter'
            and not (a.twitter_oauth_token and a.twitter_oauth_token_secret)
            and not (a.x_oauth2_access_token and a.x_oauth2_refresh_token))

    # -------------------------------------------------------------- OAuth 2.0
    def _x_oauth2_client(self):
        """Client used for refresh; requires configured client credentials."""
        icp = self.env['ir.config_parameter'].sudo()
        client_id = icp.get_param('social.twitter_oauth2_client_id')
        client_secret = icp.get_param('social.twitter_oauth2_client_secret')
        return client_id, client_secret

    def _x_oauth2_ensure_access_token(self):
        """Return a valid access token, refreshing lazily when expired.

        Returns the current access token when it is still valid, refreshes
        otherwise. Raises TwitterAuthenticationError on refresh failure (e.g.
        revoked refresh token or missing client configuration).
        """
        self.ensure_one()
        if not self.x_oauth2_access_token:
            if not self.x_oauth2_refresh_token:
                return None
            return self._x_oauth2_force_refresh()
        if self.x_oauth2_token_expires_at:
            if fields.Datetime.now() < self.x_oauth2_token_expires_at:
                return self.x_oauth2_access_token
        return self._x_oauth2_force_refresh()

    def _x_oauth2_force_refresh(self):
        """Exchange the refresh token for fresh tokens and persist them."""
        self.ensure_one()
        if not self.x_oauth2_refresh_token:
            raise twitter_errors.TwitterAuthenticationError(
                'oauth2_refresh_token_missing')
        client_id, client_secret = self._x_oauth2_client()
        if not client_id or not client_secret:
            raise twitter_errors.TwitterAuthenticationError(
                'oauth2_configuration_missing')
        client = TwitterOAuth2Client(client_id, client_secret)
        tokens = client.refresh(self.x_oauth2_refresh_token)
        access_token = tokens.get('access_token')
        if not access_token:
            raise twitter_errors.TwitterAuthenticationError(
                'oauth2_refresh_failed')
        self.write({
            'x_oauth2_access_token': access_token,
            'x_oauth2_refresh_token': tokens.get('refresh_token', self.x_oauth2_refresh_token),
            'x_oauth2_token_expires_at': fields.Datetime.now() + timedelta(
                seconds=int(tokens.get('expires_in') or 7200)),
            'x_connection_status': 'active',
        })
        return access_token

    def _get_twitter_oauth_header(self, url, headers={}, params={}, method='POST'):
        """Return an Authorization header for an X API call.

        Uses the OAuth 2.0 Bearer token when the account carries OAuth 2.0
        credentials (refreshing lazily); otherwise falls back to social_twitter's
        legacy OAuth 1.0a signing for pre-existing accounts.
        """
        self.ensure_one()
        if self.x_oauth2_access_token or self.x_oauth2_refresh_token:
            access_token = self._x_oauth2_ensure_access_token()
            if access_token:
                return {'Authorization': 'Bearer %s' % access_token}
        return super()._get_twitter_oauth_header(
            url, headers=headers, params=params, method=method)

    @api.model
    def _create_or_update_twitter_oauth2(self, media, user, tokens, expires_in):
        """Create/update the social.account from an OAuth 2.0 authorization.

        ``user`` is the /2/users/me payload (id, name, username,
        profile_image_url). ``tokens`` carries access_token/refresh_token.
        Returns the created or updated account.
        """
        user_id = str(user.get('id') or '')
        if not user_id:
            raise UserError(_(
                'X did not return a valid user ID during account linking.'))
        existing = self.sudo().with_context(active_test=False).search([
            ('media_id', '=', media.id),
            ('twitter_user_id', '=', user_id),
        ], limit=1)
        try:
            error_message = existing._get_multi_company_error_message()
        except (RuntimeError, AttributeError):
            error_message = False
        if error_message:
            raise UserError(error_message)
        handle = user.get('username') or ''
        name = user.get('name') or handle or 'X Account'
        vals = {
            'active': True,
            'media_id': media.id,
            'is_media_disconnected': False,
            'name': name,
            'social_account_handle': handle,
            'twitter_user_id': user_id,
            'x_oauth2_access_token': tokens.get('access_token'),
            'x_oauth2_refresh_token': tokens.get('refresh_token'),
            'x_oauth2_token_expires_at': fields.Datetime.now() + timedelta(
                seconds=int(tokens.get('expires_in') or expires_in or 7200)),
            'x_auth_method': 'oauth2',
        }
        avatar = user.get('profile_image_url')
        if avatar:
            try:
                avatar_resp = requests.get(avatar, timeout=10)
                if avatar_resp.ok and avatar_resp.content:
                    import base64
                    vals['image'] = base64.b64encode(avatar_resp.content)
            except requests.RequestException:
                pass

        if existing:
            existing.write(vals)
            return existing
        return self.create(vals)
