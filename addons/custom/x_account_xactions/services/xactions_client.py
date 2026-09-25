# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Thin HTTP transport for the XActions REST API.

Owns request/response plumbing only (URL building, Bearer JWT auth, bounded
retry, error classification, token refresh). It knows nothing about posts or
Odoo business logic.

Auth is a Bearer JWT. Supply a token, or an identifier/password pair so the
client logs in via ``POST /api/auth/login`` and refreshes on expiry.

Retry policy:
- 5xx: retry up to DEFAULT_RETRIES, honoring ``Retry-After``
- 429: NEVER retried. X budget windows are 15 minutes long, so a prompt retry
  only deepens the limit; the error carries ``reset_at`` instead.
- 401: when credentials are configured, log in again and replay once
- other 4xx: raise (not retryable)
"""

import logging
import time

import requests

_LOGGER = logging.getLogger(__name__)

# No host is baked in: the base URL is configuration
# (`x_account.xactions_base_url`), so the same code points at any environment.
# A request without it configured fails fast with a clear reason.
# A /api/posts/report call with audiences is one HTTP request that fans out to
# `posts` x `types` upstream reads, so it legitimately runs long.
_TIMEOUT_SECONDS = 120


class XActionsError(Exception):
    """A classified XActions API failure."""

    def __init__(self, status_code, message, code=None, retryable=False,
                 retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


class XActionsClient:
    """Transport for the XActions API."""

    DEFAULT_RETRIES = 2
    BACKOFF_BASE = 2
    LOGIN_PATH = '/api/auth/login'

    def __init__(self, env, base_url=None, token=None, identifier=None,
                 password=None, timeout=None):
        icp = env['ir.config_parameter'].sudo()
        self._base_url = (
            base_url or icp.get_param('x_account.xactions_base_url') or ''
        ).rstrip('/')
        self._token = (
            token if token is not None
            else icp.get_param('x_account.xactions_token') or '')
        self._identifier = (
            identifier if identifier is not None
            else icp.get_param('x_account.xactions_identifier') or '')
        self._password = (
            password if password is not None
            else icp.get_param('x_account.xactions_password') or '')
        self._timeout = timeout or _TIMEOUT_SECONDS

    def get(self, path, params=None):
        return self.request('GET', path, params=params)

    def post(self, path, json=None, params=None):
        return self.request('POST', path, json=json, params=params)

    def request(self, method, path, params=None, json=None, retries=None):
        """Send an authenticated request and return the parsed JSON body."""
        retries = self.DEFAULT_RETRIES if retries is None else retries
        attempt = 0
        refreshed = False
        while True:
            self._ensure_token()
            response = self._send(method, path, params, json)

            if response.status_code == 401 and not refreshed and self._identifier:
                # The stored token may have expired; log in fresh and replay.
                self._token = ''
                refreshed = True
                continue
            if response.status_code == 429:
                # Do NOT retry: X's budget windows are 15 minutes, so retrying
                # before `resetAt` turns one limit into a run of them. The
                # error carries reset_at for the caller to schedule around.
                raise self._error(response, path)
            if response.status_code >= 500:
                if attempt >= retries:
                    raise self._error(response, path)
                delay = self._retry_delay(attempt + 1, response)
                _LOGGER.warning(
                    'XActions %s on %s (attempt %s/%s); retrying in %ss',
                    response.status_code, path, attempt + 1, retries, delay)
                time.sleep(delay)
                attempt += 1
                continue
            if response.ok:
                try:
                    return response.json() if response.content else {}
                except ValueError:
                    raise XActionsError(
                        response.status_code, 'non_json_response',
                        code='non_json_response', retryable=True)
            raise self._error(response, path)

    def login(self):
        """Exchange identifier/password for a token. Returns the login DTO."""
        response = self._send(
            'POST', self.LOGIN_PATH,
            json={'identifier': self._identifier, 'password': self._password})
        if not response.ok:
            raise self._error(response, self.LOGIN_PATH)
        data = response.json() or {}
        if not data.get('token'):
            raise XActionsError(
                response.status_code, 'login_returned_no_token',
                code='auth_failed')
        return data

    def _ensure_token(self):
        if self._token or not (self._identifier and self._password):
            return
        self._token = self.login().get('token') or ''

    def _send(self, method, path, params, json):
        if not self._base_url:
            raise XActionsError(
                0, 'base_url_not_configured', code='config_error')
        url = self._base_url + (path if path.startswith('/') else '/' + path)
        headers = {'Accept': 'application/json'}
        if self._token:
            headers['Authorization'] = 'Bearer %s' % self._token
        if json is not None:
            headers['Content-Type'] = 'application/json'
        try:
            return requests.request(
                method, url, params=params, json=json, headers=headers,
                timeout=self._timeout)
        except requests.RequestException as exc:
            raise XActionsError(
                0, 'network_error: %s' % exc, code='network_error',
                retryable=True)

    @staticmethod
    def _retry_delay(attempt, response):
        retry_after = None
        try:
            retry_after = response.headers.get('Retry-After')
        except AttributeError:
            pass
        if retry_after:
            try:
                return max(0.5, min(float(retry_after), 60.0))
            except (TypeError, ValueError):
                pass
        return XActionsClient.BACKOFF_BASE * (2 ** max(attempt - 1, 0))

    @staticmethod
    def _error(response, path):
        code = None
        message = ''
        reset_at = None
        try:
            body = response.json()
            if isinstance(body, dict):
                code = body.get('error')
                message = body.get('message') or body.get('error') or ''
                # A 429 body carries when X's budget window reopens; prefer it
                # over the Retry-After header so a caller can schedule around
                # the limit instead of hammering it.
                reset_at = body.get('resetAt')
        except ValueError:
            pass
        if not message:
            message = 'HTTP %s on %s' % (response.status_code, path)
        retry_after = reset_at
        if not retry_after:
            try:
                retry_after = response.headers.get('Retry-After')
            except AttributeError:
                pass
        return XActionsError(
            response.status_code, message, code=code,
            retryable=response.status_code >= 500,
            retry_after=retry_after)
