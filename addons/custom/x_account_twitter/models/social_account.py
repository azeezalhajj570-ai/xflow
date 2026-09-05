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

import logging
from datetime import timedelta

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

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
        """Exchange the refresh token for fresh tokens and persist them.

        X rotates refresh tokens.  A burst of webhook/task requests can observe
        the same expired token; without serialization, one successful refresh
        invalidates the token that a second request is about to submit.  Lock
        the account row and, after acquiring it, reuse a token another request
        already rotated instead of submitting the stale refresh token.
        """
        self.ensure_one()
        seen_access_token = self.x_oauth2_access_token
        seen_refresh_token = self.x_oauth2_refresh_token
        self.env.cr.execute(
            'SELECT id FROM social_account WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset([
            'x_oauth2_access_token', 'x_oauth2_refresh_token',
            'x_oauth2_token_expires_at',
        ])
        if (self.x_oauth2_access_token
                and (self.x_oauth2_access_token != seen_access_token
                     or self.x_oauth2_refresh_token != seen_refresh_token)):
            return self.x_oauth2_access_token
        if not self.x_oauth2_refresh_token:
            raise twitter_errors.TwitterAuthenticationError(
                'oauth2_refresh_token_missing')
        client_id, client_secret = self._x_oauth2_client()
        if not client_id or not client_secret:
            raise twitter_errors.TwitterAuthenticationError(
                'oauth2_configuration_missing')
        client = TwitterOAuth2Client(client_id, client_secret)
        try:
            tokens = client.refresh(self.x_oauth2_refresh_token)
        except twitter_errors.TwitterAuthenticationError:
            # A typed authentication failure from the token endpoint is a
            # permanent, expected condition that callers already handle; let it
            # propagate unchanged.
            raise
        except twitter_errors.TwitterError as exc:
            # A generic non-retryable error from the token endpoint (typically
            # http_400) means the refresh token was revoked/expired. This is a
            # permanent failure. Mark the account for reauthentication and
            # signal "no token available" by returning None instead of raising:
            # a fatal exception escaping to the HTTP layer would roll back the
            # state change and surface as a cryptic RPC_ERROR to the user.
            self.transition_to_reauth(str(exc))
            return None
        access_token = tokens.get('access_token')
        if not access_token:
            self.transition_to_reauth('oauth2_refresh_failed')
            return None
        self.write({
            'x_oauth2_access_token': access_token,
            'x_oauth2_refresh_token': tokens.get('refresh_token', self.x_oauth2_refresh_token),
            'x_oauth2_token_expires_at': fields.Datetime.now() + timedelta(
                seconds=int(tokens.get('expires_in') or 7200)),
            'x_connection_status': 'active',
        })
        return access_token

    def transition_to_reauth(self, message):
        """Mark the account as needing reauthentication and record the reason.

        Used when the OAuth 2.0 refresh token can no longer be exchanged (it was
        revoked, expired, or the client credentials are misconfigured). Safe to
        call from a locked row (we already hold ``FOR UPDATE`` here).
        """
        self.ensure_one()
        self.write({
            'x_connection_status': 'reauth_required',
            'last_error': message,
        })
        _logger.warning(
            'x_account_twitter: account %s requires reauthentication: %s',
            self.id, message)

    def _get_twitter_oauth_header(self, url, headers={}, params={}, method='POST'):
        """Return an Authorization header for an X API call.

        Uses the OAuth 2.0 Bearer token when the account carries OAuth 2.0
        credentials (refreshing lazily); otherwise falls back to social_twitter's
        legacy OAuth 1.0a signing for pre-existing accounts.

        When the account carries OAuth 2.0 credentials but no token could be
        minted (revoked/expired refresh token, marked ``reauth_required``), do
        NOT fall through to OAuth 1.0a: such accounts have no
        ``twitter_oauth_token_secret`` (an unset Char is ``False``), and
        social_twitter would crash signing with it (``TypeError: sequence item
        1: expected str instance, bool found``) or ship the bool to the IAP.
        Surface a typed, non-retryable authentication error instead so callers
        (task queue, fetch actions, XChat key fetches) fail cleanly.
        """
        self.ensure_one()
        if self.x_oauth2_access_token or self.x_oauth2_refresh_token:
            access_token = self._x_oauth2_ensure_access_token()
            if access_token:
                return {'Authorization': 'Bearer %s' % access_token}
            if not (self.twitter_oauth_token and self.twitter_oauth_token_secret):
                raise twitter_errors.TwitterAuthenticationError(
                    'oauth2_access_token_unavailable')
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
            # The callback just exchanged tokens and fetched /users/me, which
            # proves the credentials work: mark the account live immediately.
            # A fresh create defaults to 'new' and nothing later promotes
            # OAuth2 accounts (the session-validation cron only handles
            # session-cookie accounts), so without this a re-link that ends up
            # creating a new record would stay 'new' forever.
            'x_connection_status': 'active',
            'last_connected': fields.Datetime.now(),
            'last_error': False,
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

    def unlink(self):
        twitter_accounts = self.filtered(
            lambda a: a.media_type == 'twitter' and a.twitter_user_id)
        for account in twitter_accounts:
            try:
                from odoo.addons.x_account.services.x_service import XService
                provider = XService.get_provider(account)
                if hasattr(provider, 'unsubscribe_all_events'):
                    provider.unsubscribe_all_events(account)
            except Exception:
                _logger.exception(
                    'x_account_twitter: failed to delete X subscriptions for account %s',
                    account.id)
            channels = self.env['discuss.channel'].sudo().search([
                ('x_account_id', '=', account.id)])
            if channels:
                channels.unlink()
        return super().unlink()

    def action_delete_x_subscriptions(self):
        """Delete XAA subscriptions for this account via the X API."""
        self.ensure_one()
        if not self.twitter_user_id:
            return {'account_id': self.id, 'skipped': True}
        from odoo.addons.x_account.services.x_service import XService
        provider = XService.get_provider(self)
        if not hasattr(provider, 'unsubscribe_all_events'):
            return {'account_id': self.id, 'skipped': True}
        result = provider.unsubscribe_all_events(self)
        return self._display_notification(
            'Delete X Subscriptions',
            'Deleted %d subscription(s)' % result.get('deleted', 0),
            kind='success')

    def action_resubscribe_x_subscriptions(self):
        """Re-create XAA subscriptions for this account via the X API."""
        self.ensure_one()
        if not self.twitter_user_id:
            return self._display_notification(
                'Resubscribe',
                'Account has no twitter_user_id',
                kind='warning')
        result = self._ensure_x_account_subscriptions()
        if result.get('skipped'):
            return self._display_notification(
                'Resubscribe',
                'Skipped (no provider or not an X account)',
                kind='warning')
        if result.get('error'):
            return self._display_notification(
                'Resubscribe',
                'Failed: %s' % result.get('error'),
                kind='danger')
        return self._display_notification(
            'Resubscribe',
            'Created %d, existing %d, pending %d, failed %d' % (
                result.get('created', 0),
                result.get('existing', 0),
                result.get('pending', 0),
                result.get('failed', 0)),
            kind='success')

    # -------------------------------------------------------------- webhooks
    @api.model
    def _ensure_x_webhook_subscriptions(self):
        """Self-heal: ensure the app webhook + XAA subscriptions exist.

        Called by ``cron_x_twitter_ensure_webhook_subscriptions``. Idempotent —
        safe to run on every cron tick. Does nothing when webhooks are disabled
        via ``x_account_twitter.webhook_enabled``.
        """
        icp = self.env['ir.config_parameter'].sudo()
        if icp.get_param('x_account_twitter.webhook_enabled', 'False') not in (
                'True', 'true', '1'):
            return {'enabled': False}
        first = self.sudo().search([
            ('media_type', '=', 'twitter'),
            ('twitter_user_id', '!=', False),
        ], limit=1)
        if not first:
            return {'enabled': True, 'accounts': 0}
        from odoo.addons.x_account.services.x_service import XService
        provider = XService.get_provider(first)
        if not provider.has_app_bearer():
            # App-Only Bearer Token not configured: the webhook + subscriptions
            # are being managed manually in the X Developer Portal, so there is
            # nothing to self-heal via the API. Skip quietly instead of failing.
            return {'enabled': True, 'managed': 'manual'}
        return provider.register_webhook(safe=True)

    def _ensure_x_account_subscriptions(self):
        """Programmatically create the XAA subscriptions for this account.

        Called right after an X account is linked (OAuth 2.0 callback) so each
        customer's account gets its DM/chat subscriptions automatically instead
        of waiting for the next cron sweep. Idempotent. Best-effort: never
        breaks account linking on a webhook/subscription failure.
        """
        self.ensure_one()
        if not self._filter_x_accounts() or not self.twitter_user_id:
            return {'account_id': self.id, 'skipped': True}
        from odoo.addons.x_account.services.x_service import XService
        try:
            provider = XService.get_provider(self)
            subscribe = getattr(provider, 'subscribe_account', None)
            if not subscribe:
                return {'account_id': self.id, 'skipped': True}
            return subscribe(self)
        except Exception:
            _logger.exception(
                'x_account_twitter: auto-subscription failed for account %s',
                self.id)
            return {'account_id': self.id, 'error': 'subscription_failed'}

    x_subscription_status = fields.Char(
        string='Subscription Status',
        compute='_compute_x_subscription_status',
        help='Current X Activity API subscription status for this account.',
    )

    @api.depends('twitter_user_id')
    def _compute_x_subscription_status(self):
        """Compute the subscription status from x.twitter.subscription records."""
        for account in self:
            if not account.twitter_user_id or account.media_type != 'twitter':
                account.x_subscription_status = 'N/A'
                continue
            subs = self.env['x.twitter.subscription'].sudo().search([
                ('account_id', '=', account.id),
            ])
            if not subs:
                account.x_subscription_status = 'Not subscribed'
            elif all(sub.state == 'active' for sub in subs):
                account.x_subscription_status = 'Active'
            elif any(sub.state == 'failed' for sub in subs):
                account.x_subscription_status = 'Failed'
            elif any(sub.state == 'pending' for sub in subs):
                account.x_subscription_status = 'Pending'
            else:
                account.x_subscription_status = 'Unknown'
