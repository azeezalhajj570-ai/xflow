import logging
import time
import base64

import requests

from odoo import _

_logger = logging.getLogger(__name__)


class RedditClient:
    _REDDIT_API_ENDPOINT = 'https://oauth.reddit.com'
    _REDDIT_AUTH_ENDPOINT = 'https://www.reddit.com/api/v1'
    _USER_AGENT = 'OdooERP:SocialReddit:v1.0 (by /u/odoo_social)'
    _MAX_RETRIES = 3
    _BACKOFF_BASE = 1.0

    def __init__(self, access_token=None, refresh_token=None, client_id=None, client_secret=None):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret

    def _get_basic_auth_header(self):
        credentials = '%s:%s' % (self.client_id, self.client_secret or '')
        encoded = base64.b64encode(credentials.encode()).decode()
        return {'Authorization': 'Basic %s' % encoded}

    def _get_bearer_header(self):
        return {'Authorization': 'Bearer %s' % self.access_token}

    def _get_headers(self, auth_type='bearer'):
        headers = {
            'User-Agent': self._USER_AGENT,
        }
        if auth_type == 'basic':
            headers.update(self._get_basic_auth_header())
        else:
            headers.update(self._get_bearer_header())
        return headers

    def refresh_token_request(self):
        response = requests.post(
            '%s/access_token' % self._REDDIT_AUTH_ENDPOINT,
            headers=self._get_headers('basic'),
            data={
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token,
            },
            timeout=10,
        )
        if response.ok:
            data = response.json()
            self.access_token = data['access_token']
            return data
        _logger.warning('Reddit token refresh failed: %s', response.text)
        return None

    def _request(self, method, endpoint, auth_type='bearer', **kwargs):
        if endpoint.startswith('http'):
            url = endpoint
        else:
            url = '%s%s' % (self._REDDIT_API_ENDPOINT, endpoint)

        for attempt in range(self._MAX_RETRIES):
            if not self.access_token and auth_type == 'bearer':
                raise Exception(_('No access token available. Please reconnect your Reddit account.'))

            headers = self._get_headers(auth_type)
            if 'headers' in kwargs:
                headers.update(kwargs.pop('headers'))

            response = requests.request(method, url, headers=headers, timeout=15, **kwargs)

            if response.status_code == 401 and self.refresh_token and auth_type == 'bearer':
                token_data = self.refresh_token_request()
                if token_data:
                    headers.update(self._get_bearer_header())
                    response = requests.request(method, url, headers=headers, timeout=15, **kwargs)
                else:
                    raise Exception(_('Reddit access token expired and could not be refreshed. Please reconnect your account.'))

            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', self._BACKOFF_BASE * (2 ** attempt)))
                _logger.warning('Reddit rate limited. Retrying after %d seconds.', retry_after)
                time.sleep(retry_after)
                continue

            if response.status_code >= 500 and attempt < self._MAX_RETRIES - 1:
                time.sleep(self._BACKOFF_BASE * (2 ** attempt))
                continue

            break

        return response

    def _get(self, endpoint, **kwargs):
        return self._request('GET', endpoint, **kwargs)

    def _post(self, endpoint, **kwargs):
        return self._request('POST', endpoint, **kwargs)

    def _put(self, endpoint, **kwargs):
        return self._request('PUT', endpoint, **kwargs)

    def _delete(self, endpoint, **kwargs):
        return self._request('DELETE', endpoint, **kwargs)

    def token_exchange(self, code, redirect_uri):
        response = requests.post(
            '%s/access_token' % self._REDDIT_AUTH_ENDPOINT,
            headers=self._get_headers('basic'),
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': redirect_uri,
            },
            timeout=10,
        )
        if response.ok:
            data = response.json()
            self.access_token = data.get('access_token')
            return data
        _logger.warning('Reddit token exchange failed: %s', response.text)
        return None

    def revoke_token(self):
        requests.post(
            '%s/revoke_token' % self._REDDIT_AUTH_ENDPOINT,
            headers=self._get_headers('basic'),
            data={
                'token': self.access_token,
                'token_type_hint': 'access_token',
            },
            timeout=5,
        )

    def get_me(self):
        return self._get('/api/v1/me')

    def get_karma(self):
        return self._get('/api/v1/me/karma')

    def get_subscribed_subreddits(self, limit=100):
        return self._get('/subreddits/mine/subscriber', params={'limit': limit, 'show': 'all'})

    def search_subreddits(self, query, limit=25):
        return self._get('/subreddits/search', params={'q': query, 'limit': limit, 'show': 'all'})

    def get_subreddit_info(self, subreddit):
        return self._get('/r/%s/about' % subreddit)

    def get_user_posts(self, username, limit=100):
        return self._get('/user/%s/submitted' % username, params={'limit': limit, 'show': 'all'})

    def get_subreddit_posts(self, subreddit, listing='hot', limit=100):
        return self._get('/r/%s/%s' % (subreddit, listing), params={'limit': limit, 'show': 'all'})

    def get_post_info(self, post_fullname):
        return self._get('/api/info', params={'id': post_fullname})

    def get_post_info_batch(self, post_fullnames):
        ids = ','.join(post_fullnames)
        return self._get('/api/info', params={'id': ids})

    def get_comments(self, article, limit=100):
        return self._get('/r/%s/comments/%s' % (article.split('_', 1)[-1] if '_' in article else article, article), params={'limit': limit, 'show': 'all'})

    def upload_media(self, filepath, mimetype, file_data):
        response = self._post('/api/media/asset.json', auth_type='bearer', json={
            'filepath': filepath,
            'mimetype': mimetype,
        })
        if not response.ok:
            return None
        asset_data = response.json()
        upload_url = asset_data.get('args', {}).get('action')
        upload_fields = asset_data.get('args', {}).get('fields', {})
        if upload_url and upload_fields:
            files = {'file': (filepath, file_data, mimetype)}
            upload_response = requests.post(upload_url, data=upload_fields, files=files, timeout=60)
            if upload_response.ok:
                return asset_data.get('asset', {}).get('asset_id')
        return None

    def submit_post(self, kind, subreddit, title, **kwargs):
        data = {
            'kind': kind,
            'sr': subreddit,
            'title': title,
            'api_type': 'json',
        }
        data.update(kwargs)
        return self._post('/api/submit', data=data)

    def submit_comment(self, parent_fullname, text):
        return self._post('/api/comment', data={
            'thing_id': parent_fullname,
            'text': text,
            'api_type': 'json',
        })

    def delete_post(self, post_fullname):
        return self._post('/api/del', data={'id': post_fullname})

    def edit_post(self, post_fullname, text):
        return self._post('/api/editusertext', data={
            'thing_id': post_fullname,
            'text': text,
            'api_type': 'json',
        })

    def subscribe(self, subreddit_fullname, action='sub'):
        return self._post('/api/subscribe', data={
            'sr': subreddit_fullname,
            'action': action,
        })

    def get_post_flair(self, subreddit):
        return self._get('/r/%s/api/link_flair' % subreddit)
