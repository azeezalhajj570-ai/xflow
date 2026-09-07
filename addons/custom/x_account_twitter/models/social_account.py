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
from odoo.addons.x_account_twitter.services.twitter_webhook import TwitterWebhook


class SocialAccount(models.Model):
    _inherit = 'social.account'

    x_subscription_event_ids = fields.Many2many(
        'x.subscription.event.type',
        'social_account_subscription_event_type_rel',
        'account_id',
        'event_type_id',
        string='Subscription Events',
        help='Event types to subscribe to for this account. '
             'Changes will sync subscriptions on next save.',
    )

    @api.model
    def _get_default_subscription_events(self):
        """Return default subscription event types (dm.received, chat.received)."""
        return self.env['x.subscription.event.type'].search([
            ('name', 'in', ('dm.received', 'chat.received')),
        ])

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

        Also keeps each account on its canonical utm.medium: base social always
        creates a fresh '[media] account' medium on create, and once an account
        with the same display name was deleted (its medium stays orphaned) the
        fresh medium ends up suffixed ('[2]', '[3]', ...). Relinking such an
        account then fails with 'The name must be unique' when base social
        renames the suffixed medium back to the canonical form. New accounts
        are re-aligned to the canonical medium after create
        (see ``_x_align_account_medium``).
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
            if (vals.get('media_id') and vals.get('name')
                    and record.media_type == 'twitter'):
                self._x_align_account_medium(record, vals['name'])
        for record, vals in zip(records, vals_list):
            if record.media_type != 'twitter' or vals.get('x_provider'):
                continue
            if record.twitter_oauth_token and not vals.get('x_oauth2_access_token'):
                record.write(self._get_oauth1_defaults())
            elif record.x_oauth2_access_token:
                record.write(self._get_oauth2_defaults())
        return records

    def write(self, vals):
        """Sync subscriptions when subscription events are changed.

        Also re-aligns the linked utm.medium before base social renames it to
        the canonical '[media] account' form. Renaming a suffixed medium
        ('[X] name [2]', ...) back to the canonical name raises 'The name must
        be unique' when an orphaned medium already holds the canonical name
        (left behind by a previously deleted/archived account). Realigning to
        the existing canonical medium avoids the collision (see
        ``_x_align_account_medium``).
        """
        if vals.get('name'):
            for account in self:
                if account.media_type == 'twitter':
                    self._x_align_account_medium(account, vals['name'])
        res = super().write(vals)
        if 'x_subscription_event_ids' in vals and not self.env.context.get('x_skip_subscription_sync'):
            for account in self:
                if account.media_type == 'twitter' and account.twitter_user_id:
                    try:
                        account._sync_subscriptions()
                    except Exception:
                        _logger.exception(
                            'x_account_twitter: subscription sync failed for account %s',
                            account.id)
        return res

    @api.model
    def _x_medium_name_for(self, media_name, account_name):
        """Return the canonical utm.medium name for a social account."""
        return "[%(media_name)s] %(account_name)s" % {
            "media_name": media_name,
            "account_name": account_name,
        }

    def _x_align_account_medium(self, account, account_name):
        """Re-point ``account`` at the canonical '[media] account' utm.medium.

        Called on create/write of twitter accounts because base social blindly
        creates (create) or renames (name-write) a per-account utm.medium.
        Once a medium holding the canonical name is orphaned (a previous
        account with the same display name was deleted), those operations
        produce suffixed duplicates ('[2]', '[3]', ...) or crash with
        'The name must be unique' when one is renamed back to the canonical
        name. This reuses the canonical medium and drops the suffixed duplicate
        whenever no other account depends on it.
        """
        account.ensure_one()
        if (account.media_type != 'twitter' or not account.media_id
                or not account_name):
            return account
        canonical = self._x_medium_name_for(
            account.media_id.name, account_name)
        current = account.utm_medium_id
        if current and current.name == canonical:
            return account
        holder = self.env['utm.medium'].sudo().with_context(
            active_test=False).search([('name', '=', canonical)], limit=1)
        if holder and holder != current:
            # The canonical name is already taken (normally by an orphaned
            # medium). Reuse it only when no other account depends on it.
            others = self.env['social.account'].sudo().with_context(
                active_test=False).search_count([
                    ('utm_medium_id', '=', holder.id),
                    ('id', '!=', account.id),
                ])
            if others:
                return account
            account.write({'utm_medium_id': holder.id})
            holder.sudo().write({'active': True})
            if current:
                shared = self.env['social.account'].sudo().with_context(
                    active_test=False).search_count([
                        ('utm_medium_id', '=', current.id),
                        ('id', '!=', account.id),
                    ])
                if not shared:
                    try:
                        current.sudo().unlink()
                    except Exception:
                        _logger.warning(
                            'x_account_twitter: could not remove duplicate '
                            'utm.medium %s (%s); leaving it orphaned',
                            current.id, current.name, exc_info=True)
            return account
        # Canonical name is free: rename our own (suffixed) medium to it
        # instead of letting base social create/rename another row.
        if current:
            current.sudo().write({'name': canonical, 'active': True})
        return account

    def _sync_subscriptions(self):
        """Sync XAA subscriptions to match the configured event types.

        Creates subscriptions for newly added events and deactivates/deletes
        subscriptions for removed events.
        """
        self.ensure_one()
        if not self.twitter_user_id or self.media_type != 'twitter':
            return {'skipped': True}
        configured_events = set(self.x_subscription_event_ids.mapped('name'))
        subs_model = self.env['x.twitter.subscription'].sudo()
        existing_subs = subs_model.search([('account_id', '=', self.id)])
        existing_event_map = {sub.event_type: sub for sub in existing_subs}
        to_create = configured_events - set(existing_event_map.keys())
        to_delete = set(existing_event_map.keys()) - configured_events
        result = {'created': 0, 'deleted': 0}
        for event_type in to_delete:
            sub = existing_event_map[event_type]
            if sub.subscription_id:
                try:
                    from odoo.addons.x_account.services.x_service import XService
                    provider = XService.get_provider(self)
                    webhook_service = TwitterWebhook(self.env)
                    webhook_service.delete_subscription(sub.subscription_id)
                except Exception:
                    _logger.warning(
                        'x_account_twitter: failed to delete subscription %s '
                        'for event_type %s on account %s',
                        sub.subscription_id, event_type, self.id)
            sub.unlink()
            result['deleted'] += 1
        if to_create:
            from odoo.addons.x_account.services.x_service import XService
            provider = XService.get_provider(self)
            webhook_service = TwitterWebhook(self.env)
            hook = self.env['x.twitter.webhook'].sudo().search([], limit=1)
            for event_type in to_create:
                try:
                    access_token = self._x_oauth2_ensure_access_token()
                    if not access_token:
                        _logger.warning(
                            'x_account_twitter: no access token for account %s; '
                            'cannot create subscription for %s', self.id, event_type)
                        continue
                    data = webhook_service.create_subscription(
                        self.twitter_user_id, event_type,
                        webhook_id=hook and hook.webhook_id or '',
                        access_token=access_token)
                    sub_id = (data or {}).get('subscription_id') or (data or {}).get('id')
                    subs_model.create({
                        'account_id': self.id,
                        'webhook_id': hook.id if hook else False,
                        'event_type': event_type,
                        'subscription_id': sub_id or '',
                        'state': 'active',
                        'created_at': self.env.cr.now(),
                    })
                    result['created'] += 1
                except Exception:
                    _logger.exception(
                        'x_account_twitter: failed to create subscription for '
                        'account %s event_type %s', self.id, event_type)
        return result

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
        # A dead refresh token is a permanent condition: do not keep sending
        # it to X until the account is re-authorized. Webhook/API traffic may
        # call this many times a second; short-circuit so we never resubmit the
        # same revoked token.
        if self.x_connection_status == 'reauth_required':
            return None
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
        # If another request already marked the account reauth_required (dead
        # refresh token), do not resubmit it to X.  The status could only be
        # reset to 'active' by a successful re-authorization, at which point
        # the fresh tokens make refresh viable again.
        if self.x_connection_status == 'reauth_required':
            return None
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
        except twitter_errors.TwitterInvalidTokenError as exc:
            # The token endpoint explicitly rejected the token
            # (invalid/expired/revoked).  Permanent: requires re-authorization.
            # Surface a UI-friendly reason instead of a generic HTTP 400.
            self.transition_to_reauth(
                'X OAuth2 re-authorization required: %s' % exc)
            return None
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
        """Override unlink to handle cleanup efficiently.

        - External API calls (unsubscribe) are non-blocking and timeout quickly.
        - Massive child datasets (x_account_task) are deleted via raw SQL to
          bypass ORM overhead.
        - Discuss channels are cascaded via DB-level rules.
        """
        twitter_accounts = self.filtered(
            lambda a: a.media_type == 'twitter' and a.twitter_user_id)
        for account in twitter_accounts:
            # 1. External API cleanup (best-effort, strict timeout)
            try:
                import signal

                def _timeout_handler(signum, frame):
                    raise TimeoutError('unsubscribe_all_events timed out')

                signal.signal(signal.SIGALRM, _timeout_handler)
                signal.alarm(10)  # 10 second hard timeout
                try:
                    from odoo.addons.x_account.services.x_service import XService
                    provider = XService.get_provider(account)
                    if hasattr(provider, 'unsubscribe_all_events'):
                        provider.unsubscribe_all_events(account)
                finally:
                    signal.alarm(0)
            except Exception:
                _logger.exception(
                    'x_account_twitter: skipped/fail delete X subscriptions for account %s',
                    account.id)
            # 2. Delete massive x_account_task records via raw SQL to skip ORM
            # overhead for 30k+ records.
            self.env.cr.execute(
                'DELETE FROM x_account_task WHERE account_id = %s',
                (account.id,))
            # 3. Discuss channels: rely on DB-level cascade (ON DELETE SET NULL
            # or CASCADE depending on schema) to avoid ORM overhead.
            # Explicitly invalidate cache if needed.
        return super().unlink()

    def action_relink(self):
        """Re-run the OAuth 2.0 authorization flow for this account.

        The callback matches the returning account by ``twitter_user_id``, so a
        successful authorization refreshes the tokens on this very record.
        """
        self.ensure_one()
        auth_method = self.env['ir.config_parameter'].sudo().get_param(
            'x_account.auth_method', 'session_cookie')
        if auth_method in ('oauth1', 'oauth2'):
            return {
                'name': 'Relink X Account',
                'type': 'ir.actions.act_url',
                'url': '/x_account/twitter/oauth2/authorize',
                'target': 'self',
            }
        return self._display_notification(
            'Relink',
            'Relinking is only available for OAuth 2.0 accounts.',
            kind='warning')

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
