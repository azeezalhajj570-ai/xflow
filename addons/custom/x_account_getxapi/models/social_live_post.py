# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Intercept posting on social.live.post for GetXAPI action-provider accounts.

Standard Odoo's ``social_live_post._post_twitter()`` routes all Twitter/X posts
through OAuth 1.0a signing. Accounts using GetXAPI as their action provider
don't carry OAuth tokens for posting, so the signing call crashes. This override
detects accounts whose action provider is GetXAPI and posts via
``get_action_provider().post_tweet()`` instead.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class SocialLivePostGetXAPI(models.Model):
    _inherit = 'social.live.post'

    def _refresh_statistics(self):
        """Skip GetXAPI action-provider accounts in OAuth-based statistics refresh."""
        getxapi_posts = self.filtered(
            lambda lp: lp.account_id._resolve_provider_code(
                lp.account_id.x_action_provider,
                {'official': 'twitter', 'getxapi': 'getxapi'}) == 'getxapi')
        non_getxapi = self - getxapi_posts
        if non_getxapi:
            super(SocialLivePostGetXAPI, non_getxapi)._refresh_statistics()

    def _post_twitter(self):
        """Override posting for GetXAPI action-provider accounts.

        Only handles records where the linked account uses GetXAPI as its action
        provider; delegates all other accounts to the standard super
        implementation.
        """
        getxapi_posts = self.filtered(
            lambda lp: lp.account_id._resolve_provider_code(
                lp.account_id.x_action_provider,
                {'official': 'twitter', 'getxapi': 'getxapi'}) == 'getxapi')
        non_getxapi = self - getxapi_posts
        if getxapi_posts:
            getxapi_posts._post_twitter_getxapi()
        if non_getxapi:
            return super(SocialLivePostGetXAPI, non_getxapi)._post_twitter()

    def _post_twitter_getxapi(self):
        """Publish tweets via the GetXAPI provider for each live post."""
        for live_post in self:
            account = live_post.account_id
            message = live_post.message or ''
            try:
                provider = account.get_action_provider()
                result = provider.post_tweet(message)
                if not result.get('success'):
                    raise RuntimeError('GetXAPI returned no tweet_id')
                tweet_id = result.get('tweet_id', '')
                live_post.write({
                    'state': 'posted',
                    'twitter_tweet_id': tweet_id,
                    'failure_reason': False,
                })
                _logger.info(
                    'Social GetXAPI: post succeeded — account=%s, tweet_id=%s',
                    account.id, tweet_id)
            except Exception as exc:
                live_post.write({
                    'state': 'failed',
                    'failure_reason': str(exc),
                })
                _logger.exception(
                    'Social GetXAPI: post failed — account=%s', account.id)
