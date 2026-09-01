# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Thin HTTP transport for the X API v2.

Owns request/response plumbing only (SRP): builds the URL, obtains the
Authorization header (OAuth 2.0 Bearer when the account has OAuth 2.0 tokens,
otherwise social_twitter's legacy OAuth 1.0a signing via
`social.account._get_twitter_oauth_header`), sends the request, and raises a
classified :class:`TwitterError` on failure.

When an OAuth 2.0 account gets a 401 the access token is force-refreshed once
and the request is retried.

It knows nothing about accounts, automation, or Odoo business logic.
"""

import logging

import requests

from . import twitter_errors

_LOGGER = logging.getLogger(__name__)

# Same base the rest of social_twitter uses (api.twitter.com).
_TWITTER_ENDPOINT = 'https://api.twitter.com'

_TIMEOUT_SECONDS = 15


class TwitterApiClient:
    """Transport for one linked social.account (OAuth 1.0a or OAuth 2.0)."""

    def __init__(self, account, endpoint=_TWITTER_ENDPOINT, timeout=_TIMEOUT_SECONDS):
        self.account = account
        self.endpoint = (endpoint or _TWITTER_ENDPOINT).rstrip('/')
        self.timeout = timeout

    # ------------------------------------------------------------------ public
    def request(self, method, path, params=None, body=None):
        """Send an authenticated X API request.

        Returns the parsed JSON body (dict) on 2xx. Raises TwitterError with a
        normalized code on failure. A 401 on an OAuth 2.0 account triggers one
        token refresh + retry.
        """
        url = self.endpoint + path
        params = params or {}
        response = self._send(method, url, params, body)
        if response.status_code == 401 and self._can_refresh_oauth2():
            _LOGGER.info('X OAuth 2.0 access token rejected; refreshing once')
            self.account._x_oauth2_force_refresh()
            response = self._send(method, url, params, body)

        if response.ok:
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                raise twitter_errors.TwitterTemporaryError('non_json_response')
        raise twitter_errors.classify(response.status_code, self._body_json(response))

    # --------------------------------------------------------------- internals
    def _send(self, method, url, params, body):
        headers = self._oauth_headers(url, method=method, params=params)
        try:
            return requests.request(
                method, url, params=params, json=body, headers=headers,
                timeout=self.timeout)
        except requests.RequestException as exc:
            raise twitter_errors.TwitterTemporaryError('network_error: %s' % exc)

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
    def _body_json(response):
        try:
            return response.json()
        except ValueError:
            return None
