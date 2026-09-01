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
from .twitter_api_client import TwitterApiClient
from .twitter_link import TwitterLink

_LOGGER = logging.getLogger(__name__)


class TwitterProvider:
    """Composition root: XProvider contract over the official X API."""

    # Provider does not need session cookies (OAuth tokens live on the account).
    _needs_cookies = False

    def __init__(self, env, account):
        self.env = env
        self.account = account
        self._client = TwitterApiClient(account)

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

    # ------------------------------------------------------- capability model
    # Operations the provider supports. x_account and the task queue dispatch
    # via getattr(provider, operation); unknown operations surface as
    # 'Unknown operation' on the task. Only claim what the X API + this
    # account's OAuth scopes actually permit.

    def supported_operations(self):
        return ('validate_session', 'repost')

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
