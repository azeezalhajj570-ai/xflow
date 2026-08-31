# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo.tools.urls import urljoin as url_join

_logger = logging.getLogger(__name__)

_TWITTER_API_ENDPOINT = 'https://api.x.com'


class XOfficialPublishAdapter:
    """Optional publish/stats path via social_twitter OAuth 1.0a.

    This is a publish-only adapter; it does NOT replace session-based DM/group-DM
    functionality. Kept separate from authentication and from SessionWebProvider.
    All calls are signed with the account's OAuth 1.0a tokens through the
    social_twitter helpers, so no undocumented web-session endpoints are used.
    """

    # Publish-only provider: no session cookies; OAuth tokens live on the account.
    _needs_cookies = False

    def __init__(self, env, account):
        self.env = env
        self.account = account

    # ------------------------------------------------------------------ config
    def _check_oauth_configured(self):
        """Return True when local OAuth 1.0a signing keys are configured.

        social_twitter signs requests locally when the consumer key/secret are
        configured in ir.config_parameter; otherwise it falls back to an IAP
        remote signing call. This adapter requires the local keys so it never
        makes a silent network call to Odoo's IAP service.
        """
        params = self.env['ir.config_parameter'].sudo()
        return bool(params.get_param('social.twitter_consumer_key') and
                    params.get_param('social.twitter_consumer_secret_key'))

    def _oauth_headers(self, url, method='POST', params=None):
        return self.account._get_twitter_oauth_header(
            url, params=params or {}, method=method)

    # ------------------------------------------------------------- validation
    def validate_session(self):
        """OAuth 1.0a has no browser session to validate.

        Returns valid when the account carries OAuth tokens and the local signing
        keys are configured (the publish path is usable). Actual token validity is
        proven by a live publish/stats operation.
        """
        account = self.account
        if not (account.twitter_oauth_token and account.twitter_oauth_token_secret):
            return {'valid': False, 'user': None, 'reason': 'oauth1_token_missing'}
        if not self._check_oauth_configured():
            return {
                'valid': False, 'user': None,
                'reason': 'social_twitter_consumer_keys_missing',
            }
        return {
            'valid': True,
            'user': {
                'id': account.twitter_user_id or '',
                'username': account.social_account_handle or '',
                'name': account.name or '',
            },
            'reason': 'oauth1_adapter',
        }

    # ------------------------------------------------------------- publishing
    def post_tweet(self, text, media_ids=None, **kwargs):
        """Publish a tweet (X API v2 /2/tweets). Returns the created tweet id."""
        if not text:
            raise ValueError('text is required')
        self._require_oauth()
        endpoint = url_join(_TWITTER_API_ENDPOINT, '/2/tweets')
        body = {'text': text}
        if media_ids:
            body['media'] = {'media_ids': media_ids}
        headers = self._oauth_headers(endpoint, method='POST')
        result = self._request(endpoint, 'POST', json=body, headers=headers)
        data = result.json()
        if not result.ok:
            raise RuntimeError('publish_failed: %s' % result.text[:500])
        return {'tweet_id': data.get('data', {}).get('id', '')}

    # ------------------------------------------------------------- stats
    def get_account_stats(self, **kwargs):
        """Return current public metrics for the account's own handle."""
        self._require_oauth()
        endpoint = url_join(_TWITTER_API_ENDPOINT, '/2/users/by')
        params = {
            'user.fields': 'public_metrics',
            'usernames': self.account.social_account_handle,
        }
        headers = self._oauth_headers(endpoint, method='GET', params=params)
        result = self._request(endpoint, 'GET', params=params, headers=headers)
        data = result.json()
        if not result.ok:
            raise RuntimeError('stats_failed: %s' % result.text[:500])
        metrics = (data.get('data') or [{}])[0].get('public_metrics') or {}
        return {
            'username': self.account.social_account_handle or '',
            'followers_count': metrics.get('followers_count', 0),
            'following_count': metrics.get('following_count', 0),
            'tweet_count': metrics.get('tweet_count', 0),
            'listed_count': metrics.get('listed_count', 0),
        }

    def get_last_tweets_stats(self, count=10, **kwargs):
        """Return aggregate engagement for the last `count` tweets of the account."""
        self._require_oauth()
        endpoint = url_join(_TWITTER_API_ENDPOINT, '/2/users/by/username/%s/tweets'
                            % self.account.social_account_handle)
        params = {
            'max_results': int(count),
            'tweet.fields': 'public_metrics',
        }
        headers = self._oauth_headers(endpoint, method='GET', params=params)
        result = self._request(endpoint, 'GET', params=params, headers=headers)
        data = result.json()
        if not result.ok:
            raise RuntimeError('tweets_stats_failed: %s' % result.text[:500])
        tweets = data.get('data') or []
        engagement = sum((t.get('public_metrics') or {}).get('like_count', 0)
                         for t in tweets)
        stories = sum((t.get('public_metrics') or {}).get('retweet_count', 0)
                      for t in tweets)
        return {'count': len(tweets), 'engagement': engagement, 'stories': stories}

    # --------------------------------------------------------------- internals
    def _require_oauth(self):
        if not self._check_oauth_configured():
            raise RuntimeError(
                'social_twitter_consumer_keys_missing: configure '
                'social.twitter_consumer_key / social.twitter_consumer_secret_key '
                'to use the official publish adapter.')

    def _request(self, url, method, json=None, params=None, headers=None):
        import requests
        return requests.request(method, url, json=json, params=params,
                                headers=headers, timeout=15)
