# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Thin HTTP transport for the OmniX REST API.

Owns the request/response plumbing only (SRP): URL building, headers, the
account ``auth_token`` placement (query string for GET, JSON body otherwise, per
the OmniX OpenAPI spec), and raising classified :class:`RuntimeError` on
failure. It knows nothing about conversations, webhooks, or group sync.
"""

import logging

import requests

from . import omnix_errors

_LOGGER = logging.getLogger(__name__)

_OMNIX_BASE = 'https://api.omnixapi.com/api/v1/twitter'

_TIMEOUT_SECONDS = 20


class OmniXHttpClient:
    """Transport for one OmniX API key + one account's ``auth_token``."""

    def __init__(self, api_key, auth_token, base_url=_OMNIX_BASE, timeout=_TIMEOUT_SECONDS):
        self._api_key = api_key or ''
        self._auth_token = auth_token or ''
        self._base_url = (base_url or _OMNIX_BASE).rstrip('/')
        self._timeout = timeout

    # ------------------------------------------------------------------ public
    def request(self, method, path, params=None, body=None, path_args=None):
        """Call an OmniX endpoint and return the parsed envelope (dict).

        Raises RuntimeError with a classified error code on failure.
        """
        if not self._api_key:
            raise RuntimeError('omnix_api_key_missing')
        if not self._auth_token:
            raise RuntimeError('Missing auth_token cookie')

        url = self._base_url + path
        if path_args:
            url = url % path_args
        req_params = dict(params or {})
        req_body = dict(body or {})
        if method == 'GET':
            req_params['auth_token'] = self._auth_token
        else:
            req_body['auth_token'] = self._auth_token

        try:
            resp = requests.request(
                method, url, params=req_params, json=req_body,
                headers=self._headers(), timeout=self._timeout)
        except requests.RequestException as exc:
            raise RuntimeError('network_error: %s' % exc)

        code = omnix_errors.classify_http_status(resp.status_code)
        if code:
            raise RuntimeError(code)
        if resp.status_code >= 400:
            raise RuntimeError('http_%s' % resp.status_code)

        try:
            envelope = resp.json()
        except ValueError:
            raise RuntimeError('non_json_response')
        if not envelope.get('status', True):
            raise RuntimeError(str(envelope.get('error') or 'omnix_request_failed'))
        return envelope

    # --------------------------------------------------------------- internals
    def _headers(self):
        return {
            'Authorization': 'Bearer %s' % self._api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
