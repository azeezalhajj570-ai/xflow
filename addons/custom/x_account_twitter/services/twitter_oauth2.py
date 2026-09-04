# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""OAuth 2.0 (Authorization Code + PKCE) client for X user-context auth.

X deprecated OAuth 1.0a for Free-tier/new apps (request_token returns 401), so
account linking uses OAuth 2.0 with PKCE against the X API v2 endpoints:

    authorize -> https://twitter.com/i/oauth2/authorize
    token     -> https://api.x.com/2/oauth2/token
    user      -> https://api.x.com/2/users/me

Scopes requested: ``tweet.read tweet.write users.read offline.access
dm.read dm.write``. ``offline.access`` grants refresh tokens (access tokens
expire after 2 hours). ``dm.read``/``dm.write`` grant DM + group-DM access
(Group conversations are read via ``GET /2/dm_events``).

This class owns the HTTP + PKCE plumbing only (SRP); the controller owns the
session state/CSRF handling and the account model owns token storage/refresh.
"""

import base64
import hashlib

import requests
from werkzeug.urls import url_encode

from . import twitter_errors

SCOPES = 'tweet.read tweet.write users.read offline.access dm.read dm.write'


class TwitterOAuth2Client:
    """Stateless-ish OAuth 2.0 client for a single X app configuration."""

    AUTH_URL = 'https://twitter.com/i/oauth2/authorize'
    TOKEN_URL = 'https://api.x.com/2/oauth2/token'
    ME_URL = 'https://api.x.com/2/users/me'
    SCOPES = SCOPES

    def __init__(self, client_id, client_secret='', redirect_uri='', timeout=15):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.timeout = timeout

    # ------------------------------------------------------------ authorize
    def build_authorize_url(self, state, code_verifier):
        """Build the X authorization URL with a PKCE S256 code challenge."""
        params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'scope': self.SCOPES,
            'state': state,
            'code_challenge': self._code_challenge(code_verifier),
            'code_challenge_method': 'S256',
        }
        return '%s?%s' % (self.AUTH_URL, url_encode(params))

    # --------------------------------------------------------------- tokens
    def exchange_code(self, code, code_verifier):
        """Exchange an authorization code for {access_token, refresh_token, ...}."""
        return self._token_request({
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': self.redirect_uri,
            'client_id': self.client_id,
            'code_verifier': code_verifier,
        })

    def refresh(self, refresh_token):
        """Refresh an expired access token using the app's refresh token."""
        return self._token_request({
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': self.client_id,
        })

    # --------------------------------------------------------------- user
    def get_me(self, access_token, user_fields='id,name,username,profile_image_url'):
        """Return the authenticated user dict from GET /2/users/me."""
        headers = {'Authorization': 'Bearer %s' % access_token}
        try:
            response = requests.get(
                self.ME_URL,
                params={'user.fields': user_fields},
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise twitter_errors.TwitterTemporaryError('network_error: %s' % exc)
        if not response.ok:
            raise twitter_errors.classify(response.status_code, self._body_json(response))
        return (response.json() or {}).get('data') or {}

    # ------------------------------------------------------------ internals
    def _token_request(self, data):
        headers = {}
        if self.client_secret:
            basic = base64.b64encode(
                ('%s:%s' % (self.client_id, self.client_secret)).encode()).decode()
            headers['Authorization'] = 'Basic %s' % basic
        try:
            response = requests.post(
                self.TOKEN_URL, data=data, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise twitter_errors.TwitterTemporaryError('network_error: %s' % exc)
        if response.status_code != 200:
            raise twitter_errors.classify(response.status_code, self._body_json(response))
        try:
            return response.json()
        except ValueError:
            raise twitter_errors.TwitterTemporaryError('non_json_response')

    @staticmethod
    def _code_challenge(code_verifier):
        digest = hashlib.sha256(code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b'=').decode()

    @staticmethod
    def _body_json(response):
        try:
            return response.json()
        except ValueError:
            return None