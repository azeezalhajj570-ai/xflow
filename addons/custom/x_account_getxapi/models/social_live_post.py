# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Intercept posting on social.live.post for GetXAPI-provider accounts.

Standard Odoo's ``social_live_post._post_twitter()`` routes all Twitter/X posts
through OAuth 1.0a signing. GetXAPI accounts don't carry OAuth tokens, so the
signing call crashes with ``TypeError: sequence item 1: expected str instance,
bool found``. This override detects GetXAPI accounts and posts via
``GetXAPIProvider.post_tweet()`` instead.
"""

import logging

from odoo import models
from odoo.addons.x_account.services.x_service import XService

_logger = logging.getLogger(__name__)


class SocialLivePostGetXAPI(models.Model):
    _inherit = 'social.live.post'

    def _refresh_statistics(self):
        """Skip GetXAPI accounts in OAuth-based statistics refresh."""
        getxapi_posts = self.filtered(
            lambda lp: lp.account_id.x_provider == 'getxapi')
        non_getxapi = self - getxapi_posts
        if non_getxapi:
            super(SocialLivePostGetXAPI, non_getxapi)._refresh_statistics()

    def _post_twitter(self):
        """Override posting for GetXAPI-provider accounts.

        Only handles records where the linked account uses the ``getxapi``
        provider; delegates all other accounts to the standard super
        implementation.
        """
        getxapi_posts = self.filtered(
            lambda lp: lp.account_id.x_provider == 'getxapi')
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
                provider = XService.get_provider(account)
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
