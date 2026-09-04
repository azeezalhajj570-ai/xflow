# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Thin HTTP transport for the X API v2.

Owns request/response plumbing only (SRP): builds the URL, obtains the
Authorization header (OAuth 2.0 Bearer when the account has OAuth 2.0 tokens,
otherwise social_twitter's legacy OAuth 1.0a signing via
`social.account._get_twitter_oauth_header`), sends the request, and raises a
classified :class:`TwitterError` on failure.

Hosts (kept explicit per API family):
- ``https://api.x.com`` — the official X API v2 host. Used for the Chat API
  (``/2/chat/conversations``, ``/2/chat/conversations/{id}/events``) and the
  OAuth 2.0 token/user endpoints. X's official docs and the Developer Console
  route these through ``api.x.com``.
- ``https://api.twitter.com`` — the legacy host used by ``social_twitter`` for
  the OAuth 1.0a endpoints and the legacy DM endpoints (``/2/dm_events``,
  ``/2/dm_conversations/...``). Kept separate so the legacy family is
  untouched; the two hosts are intentionally not merged.

When an OAuth 2.0 account gets a 401 the access token is force-refreshed once
and the request is retried.

It knows nothing about accounts, automation, or Odoo business logic.
"""

import logging

import requests

from . import twitter_errors

_LOGGER = logging.getLogger(__name__)

# Official X API v2 host (Chat API, OAuth 2.0 token/user endpoints).
X_API_ENDPOINT = 'https://api.x.com'
# Legacy host used by social_twitter (OAuth 1.0a + legacy DM endpoints).
LEGACY_TWITTER_ENDPOINT = 'https://api.twitter.com'

_TIMEOUT_SECONDS = 15

# Paths served by the legacy host. Everything else goes to api.x.com.
_LEGACY_PATHS = (
    '/oauth/',
    '/2/dm_events',
    '/2/dm_conversations/',
    '/2/users/',
    '/2/media/upload',
)


def _endpoint_for_path(path):
    if any(path.startswith(prefix) for prefix in _LEGACY_PATHS):
        return LEGACY_TWITTER_ENDPOINT
    return X_API_ENDPOINT


class TwitterApiClient:
    """Transport for one linked social.account (OAuth 1.0a or OAuth 2.0)."""

    # Bounded retry policy for temporary failures (network + 5xx).
    # - 2 retries per request, honoring Retry-After when present, otherwise
    #   exponential backoff (2s, 4s) — short enough to not block workers.
    # - 429 is NOT retried here: it is classified as a retryable rate_limit
    #   error and the caller/task queue decides whether to back off.
    # - 401/403/404 are permanent and never retried.
    DEFAULT_RETRIES = 2
    BACKOFF_BASE_SECONDS = 2

    def __init__(self, account, endpoint=None, timeout=_TIMEOUT_SECONDS):
        self.account = account
        # endpoint=None -> per-path host selection; explicit endpoint overrides.
        self.endpoint = (endpoint or '').rstrip('/')
        self.timeout = timeout

    # ------------------------------------------------------------------ public
    def request(self, method, path, params=None, body=None, retries=None):
        """Send an authenticated X API request.

        Returns the parsed JSON body (dict) on 2xx. Raises TwitterError with a
        normalized code on failure. A 401 on an OAuth 2.0 account triggers one
        token refresh + retry.

        ``retries`` controls the bounded retry policy for temporary failures
        (network errors and 5xx, honoring ``Retry-After``). ``None`` uses the
        class default ``DEFAULT_RETRIES``; pass 0 to disable retries.
        """
        url = self._url_for_path(path)
        params = params or {}
        response = self._send(method, url, params, body, retries=retries)
        if response.status_code == 401 and self._can_refresh_oauth2():
            _LOGGER.info('X OAuth 2.0 access token rejected; refreshing once')
            # If the refresh token is itself invalid/expired,
            # _x_oauth2_force_refresh marks the account for reauthentication
            # and returns None (no new token). Don't retry with the stale
            # access token in that case — let the original 401 classify as an
            # authentication failure so callers surface a clear message.
            if self.account._x_oauth2_force_refresh():
                response = self._send(method, url, params, body, retries=retries)

        if response.ok:
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                raise twitter_errors.TwitterTemporaryError('non_json_response')
        raise twitter_errors.classify(response.status_code, self._body_json(response))

    # --------------------------------------------------------------- internals
    def _url_for_path(self, path):
        if self.endpoint:
            return self.endpoint + path
        return _endpoint_for_path(path) + path

    def _send(self, method, url, params, body, retries=None):
        retries = self.DEFAULT_RETRIES if retries is None else max(int(retries or 0), 0)
        attempt = 0
        while True:
            try:
                headers = self._oauth_headers(url, method=method, params=params)
                response = requests.request(
                    method, url, params=params, json=body, headers=headers,
                    timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt < retries:
                    attempt += 1
                    _LOGGER.warning(
                        'X API network error on %s (attempt %s/%s); retrying in %ss',
                        url, attempt, retries, self._retry_delay(attempt, None))
                    self._sleep(self._retry_delay(attempt, None))
                    continue
                raise twitter_errors.TwitterTemporaryError('network_error: %s' % exc)
            if response.status_code < 500 or attempt >= retries:
                return response
            delay = self._retry_delay(attempt + 1, response.headers.get('Retry-After'))
            attempt += 1
            _LOGGER.warning(
                'X API temporary failure %s on %s (attempt %s/%s); retrying in %ss',
                response.status_code, url, attempt, retries, delay)
            self._sleep(delay)

    def _can_refresh_oauth2(self):
        """Only OAuth 2.0 accounts (refresh token present) can recover a 401."""
        return bool(getattr(self.account, 'x_oauth2_refresh_token', False))

    def _oauth_headers(self, url, method='POST', params=None):
        """Delegate to the account's header builder (OAuth 2.0 Bearer or the
        legacy OAuth 1.0a signing) — the same path the rest of the module uses.
        """
        return self.account._get_twitter_oauth_header(
            url, params=params or {}, method=method)

    @staticmethod
    def _retry_delay(attempt, retry_after):
        """Seconds to wait before retry ``attempt`` (1-based).

        Honors the server's ``Retry-After`` header when provided; otherwise
        exponential backoff: ``BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)``.
        """
        if retry_after:
            try:
                return max(0.5, min(float(retry_after), 60.0))
            except (TypeError, ValueError):
                pass
        return TwitterApiClient.BACKOFF_BASE_SECONDS * (2 ** max(attempt - 1, 0))

    @staticmethod
    def _sleep(seconds):
        """Sleep hook (overridable in tests to avoid real delays)."""
        import time
        time.sleep(seconds)

    @staticmethod
    def _body_json(response):
        try:
            return response.json()
        except ValueError:
            return None
