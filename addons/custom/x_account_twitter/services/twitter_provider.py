# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""TwitterProvider: X provider via the official X API.

Account credentials live on the linked `social.account`: OAuth 2.0
(`x_oauth2_access_token` / `x_oauth2_refresh_token`, refreshed lazily) for new
accounts, or legacy OAuth 1.0a tokens (`twitter_oauth_token` / _secret) for
pre-existing ones. Signing/headers go through `social_twitter`'s helpers (the
account returns an OAuth 2.0 Bearer header when it has OAuth 2.0 tokens).

SOLID layering:
- transport:    :class:`TwitterApiClient` (auth headers, HTTP, status)
- parsing:      :class:`TwitterEnvelope` (X API v2 -> DTOs)
- error model:  :mod:`twitter_errors` (HTTP status -> normalized lifecycle code)
- link parsing: :class:`TwitterLink` (URL -> post reference)
- composition:  this class wires the above behind the `XProvider` contract
"""

import logging

from . import twitter_envelope
from . import twitter_errors
from .twitter_activity import TwitterActivity
from .twitter_api_client import TwitterApiClient
from .twitter_group_sync import TwitterGroupSync
from .twitter_link import TwitterLink
from .twitter_webhook import TwitterWebhook
from .xchat_decryptor import XChatDecryptor

_LOGGER = logging.getLogger(__name__)


class TwitterProvider:
    """Composition root: XProvider contract over the official X API."""

    # Provider does not need session cookies (OAuth tokens live on the account).
    _needs_cookies = False
    # Official API group DMs are plaintext (no XChat PIN required).
    _needs_encryption_code = False

    def __init__(self, env, account):
        self.env = env
        self.account = account
        self._client = TwitterApiClient(account)
        self._group_sync = TwitterGroupSync(env, self._client)
        self._xchat = XChatDecryptor(env, account, client=self._client)

    # ------------------------------------------------------------- validation
    def validate_session(self):
        """Verify the OAuth credentials can reach the X API (GET /2/users/me).

        Returns the XProvider-compatible ``{valid, user, reason}`` dict. This is
        a real authenticated call, so an auth failure surfaces as invalid.
        """
        account = self.account
        has_oauth1 = account.twitter_oauth_token and account.twitter_oauth_token_secret
        has_oauth2 = account.x_oauth2_access_token and account.x_oauth2_refresh_token
        if not has_oauth1 and not has_oauth2:
            return {'valid': False, 'user': None,
                    'reason': 'twitter_oauth_token_missing'}
        try:
            envelope = self._client.request('GET', '/2/users/me',
                                            params={'user.fields': 'name,username'})
        except Exception as exc:
            # Surface the normalized lifecycle code (e.g. 'rate_limit',
            # 'authentication_failure'), not a raw HTTP status.
            return {'valid': False, 'user': None,
                    'reason': getattr(exc, 'code', None) or str(exc)}
        user = twitter_envelope.TwitterEnvelope.user(envelope)
        if not user:
            return {'valid': False, 'user': None,
                    'reason': 'Response missing user ID'}
        return {'valid': True, 'user': user, 'reason': 'twitter'}

    # --------------------------------------------------------------- repost
    def repost(self, post, **kwargs):
        """Repost (retweet) a post via the X API.

        ``post`` is a normalized post reference dict — either produced by
        :class:`TwitterLink` (``{'platform', 'post_id', ...}``) or any dict
        carrying ``post_id``. Returns the normalized provider result DTO.
        """
        post_id = self._post_id(post)
        if not post_id:
            raise ValueError('post_id is required')
        envelope = self._client.request(
            'POST',
            '/2/users/%s/retweets' % self.account.twitter_user_id,
            body={'tweet_id': post_id},
        )
        return twitter_envelope.TwitterEnvelope.repost(envelope, post_id)

    # --------------------------------------------------------------- groups
    def fetch_groups(self, account, limit=100):
        """Sync the account's X group-DM conversations into discuss channels."""
        return self._group_sync.fetch_groups(account, limit=limit)

    def fetch_group_messages(self, account, limit=100):
        """Fetch messages of the account's X conversations into discuss."""
        return self._group_sync.fetch_group_messages(account, limit=limit)

    def get_dms(self, conversation_id, limit=100):
        """Return normalized messages for one conversation (1:1 or group)."""
        return self._group_sync.get_dms(conversation_id, limit=limit)

    # -------------------------------------------------------------- webhooks
    def process_webhook_event(self, event_uuid=None, **kwargs):
        """Task-queue entry point: process one queued x.twitter.event.

        Loaded by event_uuid (set at enqueue time) so the payload survives retry
        in the queue instead of depending on the original HTTP delivery.
        """
        event = self.env['x.twitter.event'].sudo().search(
            [('event_uuid', '=', event_uuid)], limit=1)
        if not event:
            return {'processed': False, 'reason': 'unknown_event_uuid'}
        return TwitterActivity(self.env).process_event(event)

    def has_app_bearer(self):
        """True when the app-only bearer token is configured (API manage mode)."""
        return bool(TwitterWebhook(self.env).has_app_bearer)

    def initialize_x_chat_encryption(self, account=None):
        """Build + unlock a Chat XDK instance from the account's key source.

        ``key_blob`` mode validates the stored blob via ``import_keys``;
        ``juicebox`` mode recovers keys from X's secure key backup using the
        account PIN (``unlock``). Does not persist any raw key material beyond
        what is already configured. Raises on missing/invalid key material.
        """
        account = account or self.account
        self._xchat = XChatDecryptor(self.env, account, client=self._client)
        self._xchat.initialize()
        return {'initialized': True,
                'key_mode': account.x_chat_key_mode or 'key_blob'}

    def register_webhook(self, safe=True):
        """Register the app webhook with X and persist its state."""
        service = TwitterWebhook(self.env)
        data = service.register_webhook(safe=safe)
        webhook_id = data.get('webhook_id') or data.get('id')
        url = data.get('url') or service.webhook_url()
        hook = self.env['x.twitter.webhook'].sudo().search([
            ('webhook_id', '=', webhook_id),
        ], limit=1)
        if not hook:
            hook = self.env['x.twitter.webhook'].sudo().create({
                'name': url,
                'webhook_id': webhook_id,
                'valid': True,
                'registered_at': self.env.cr.now(),
            })
        else:
            hook.write({'valid': True})
        self._subscribe_all(service, hook)
        return {'webhook_id': (webhook_id or '') and hook.id,
                'registered': True}

    def validate_webhook_registration(self, webhook_id=None):
        """Re-run CRC validation on the stored/app webhook."""
        service = TwitterWebhook(self.env)
        hook = self.env['x.twitter.webhook'].sudo()
        target = hook.browse(webhook_id) if webhook_id else hook.search([], limit=1)
        wid = target.webhook_id if target else ''
        if not wid:
            raise ValueError('No registered webhook to validate')
        service.validate_webhook(wid)
        return {'validated': True, 'webhook_id': wid}

    def unsubscribe_all_events(self, account=None):
        """Delete every XAA subscription (optionally scoped to ``account``)."""
        service = TwitterWebhook(self.env)
        subs = self.env['x.twitter.subscription'].sudo()
        domain = [('account_id', '=', account.id)] if account else []
        result = {'deleted': 0}
        for sub in subs.search(domain):
            if sub.subscription_id:
                try:
                    service.delete_subscription(sub.subscription_id)
                except Exception:
                    pass
            result['deleted'] += 1
            sub.unlink()
        return result

    def delete_webhook_registration(self):
        """Delete the app webhook and its subscriptions from X."""
        service = TwitterWebhook(self.env)
        hook = self.env['x.twitter.webhook'].sudo().search([], limit=1)
        result = self.unsubscribe_all_events()
        if hook and hook.webhook_id:
            service.delete_webhook(hook.webhook_id)
            hook.unlink()
            result['webhook_deleted'] = True
        return result

    def _subscribe_all(self, service, hook):
        """Create XAA subscriptions for each linked X account's event types.

        Subscription *create* requires OAuth 2.0 user-context on the account
        (scoped ``dm.read``), so each account's own access token is used rather
        than the app-only bearer.
        """
        accounts = self.env['social.account'].sudo().search([
            ('media_type', '=', 'twitter'),
            ('twitter_user_id', '!=', False),
            ('x_connection_status', 'in', ('active', 'reauth_required',
                                           'error', False)),
        ])
        subs_model = self.env['x.twitter.subscription'].sudo()
        from .twitter_webhook import SUPPORTED_EVENT_TYPES as _EVENT_TYPES
        for acc in accounts:
            self._subscribe_account(service, hook, acc, subs_model, _EVENT_TYPES)

    def _subscribe_account(self, service, hook, acc, subs_model=None,
                           event_types=None):
        """Create the XAA subscriptions for one linked X account.

        Idempotent: skips event types that already have a subscription. Creates
        subscriptions in ``pending`` state on retryable (temporary) failures so
        the next sweep can retry them. Returns a per-account summary dict.
        """
        from .twitter_webhook import SUPPORTED_EVENT_TYPES as _EVENT_TYPES
        subs_model = subs_model or self.env['x.twitter.subscription'].sudo()
        event_types = event_types or _EVENT_TYPES
        summary = {'account_id': acc.id, 'user_id': str(acc.twitter_user_id),
                   'created': 0, 'pending': 0, 'existing': 0, 'failed': 0}
        access_token = acc.sudo()._x_oauth2_ensure_access_token()
        if not access_token:
            _LOGGER.warning(
                'x_account_twitter: no user access token for account %s; '
                'cannot create XAA subscriptions', acc.id)
            return dict(summary, error='no_access_token')
        for event_type in event_types:
            existing = subs_model.search([
                ('account_id', '=', acc.id),
                ('event_type', '=', event_type),
            ], limit=1)
            if existing:
                if existing.state == 'failed':
                    # A previous attempt was rejected by X; clear it so this
                    # self-heal sweep retries rather than treating it as live.
                    existing.unlink()
                else:
                    summary['existing'] += 1
                    continue
            try:
                data = service.create_subscription(
                    acc.twitter_user_id, event_type,
                    webhook_id=hook and hook.webhook_id or '',
                    access_token=access_token)
                sub_id = (data or {}).get('subscription_id') or (
                    data or {}).get('id')
                subs_model.create({
                    'account_id': acc.id,
                    'webhook_id': hook.id if hook else False,
                    'event_type': event_type,
                    'subscription_id': sub_id or '',
                    'state': 'active',
                    'created_at': self.env.cr.now(),
                })
                summary['created'] += 1
            except twitter_errors.TwitterTemporaryError:
                subs_model.create({
                    'account_id': acc.id,
                    'webhook_id': hook.id if hook else False,
                    'event_type': event_type,
                    'state': 'pending',
                })
                summary['pending'] += 1
            except twitter_errors.TwitterError as exc:
                if (exc.code == 'http_422' and 'unique' in exc.message.lower()) or \
                   (exc.code == 'http_400' and 'duplicatesubscription' in exc.message.lower()):
                    _LOGGER.info(
                        'x_account_twitter: subscription for account %s '
                        'event_type %s already exists on X (idempotent); '
                        'marking as existing', acc.id, event_type)
                    subs_model.create({
                        'account_id': acc.id,
                        'webhook_id': hook.id if hook else False,
                        'event_type': event_type,
                        'state': 'active',
                        'created_at': self.env.cr.now(),
                    })
                    summary['existing'] += 1
                else:
                    # Record the failure and keep going: one rejected event
                    # type (e.g. an X-side restriction on dm.* for this user)
                    # must not abort the remaining event types for the account,
                    # which previously left the account with zero live
                    # subscriptions. The next self-heal sweep can retry these.
                    _LOGGER.warning(
                        'x_account_twitter: failed to create subscription for '
                        'account %s event_type %s: %s', acc.id, event_type, exc)
                    subs_model.create({
                        'account_id': acc.id,
                        'webhook_id': hook.id if hook else False,
                        'event_type': event_type,
                        'state': 'failed',
                        'error': str(exc)[:200],
                    })
                    summary['failed'] = summary.get('failed', 0) + 1
        return summary

    def subscribe_account(self, account=None, event_types=None):
        """Programmatically create XAA subscriptions for one account.

        Ensures the app webhook is registered (safe/idempotent) and then creates
        any missing subscriptions for the given account so real-time DM/chat
        events start flowing as soon as a customer links their X account.
        Returns the per-account summary.
        """
        account = account or self.account
        if not account or account.media_type != 'twitter' or not account.twitter_user_id:
            raise ValueError('A linked X account is required to subscribe.')
        service = TwitterWebhook(self.env)
        hook = self.env['x.twitter.webhook'].sudo().search([], limit=1)
        if not hook:
            if service.has_app_bearer and service.webhook_url():
                data = service.register_webhook(safe=True)
                wid = (data or {}).get('webhook_id') or (data or {}).get('id')
                hook = self.env['x.twitter.webhook'].sudo().search(
                    [('webhook_id', '=', wid)], limit=1)
            if not hook:
                # App bearer/webhook not configured (manual mode): record the
                # intent so self-healing can pick it up later.
                _LOGGER.warning(
                    'x_account_twitter: no app webhook registered; skipping '
                    'auto-subscription for account %s', account.id)
                return {'account_id': account.id, 'managed': 'manual'}
        return self._subscribe_account(service, hook, account.sudo())

    # ------------------------------------------------------- capability model
    # Operations the provider supports. x_account and the task queue dispatch
    # via getattr(provider, operation); unknown operations surface as
    # 'Unknown operation' on the task. Only claim what the X API + this
    # account's OAuth scopes actually permit.

    def supported_operations(self):
        return ('validate_session', 'repost', 'fetch_groups',
                'fetch_group_messages', 'get_dms',
                'process_webhook_event', 'register_webhook',
                'validate_webhook_registration', 'unsubscribe_all_events',
                'delete_webhook_registration')

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _post_id(post):
        if isinstance(post, dict):
            return post.get('post_id') or post.get('tweet_id') or post.get('id')
        return getattr(post, 'post_id', None) or getattr(post, 'id', None)


# Register the provider with x_account's XProviderRegistry at import time (OCP):
# x_account stays closed for modification and open for extension — installing
# this module adds the 'twitter' option.
def _register_twitter_provider():
    try:
        from odoo.addons.x_account.services.x_provider import XProviderRegistry
        XProviderRegistry.register('twitter', __name__ + '.TwitterProvider')
    except (ImportError, AttributeError):
        _LOGGER.exception('Failed to register Twitter provider')


_register_twitter_provider()
