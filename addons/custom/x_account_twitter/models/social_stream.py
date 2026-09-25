# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Keep one reauth-needed OAuth 2.0 account from breaking the social feed.

``social.stream.refresh_all()`` walks every stream and calls
``_fetch_stream_data()``. For an OAuth 2.0 account that needs reauthorization
(``x_connection_state == 'reauth_required'``: no mintable token and no OAuth
1.0a fallback), ``_get_twitter_oauth_header`` raises a typed
``TwitterAuthenticationError`` on purpose, so interactive callers fail cleanly.

But the stream refresh is a background sweep, not a user action, and
``refresh_all`` only guards connection/timeout errors — so that one account's
stream aborted the entire feed refresh (RPC_ERROR for everyone). A stream fetch
that cannot authenticate must be skipped and logged, not allowed to fail the
refresh for the rest of the feed.
"""

import logging

from odoo import models

from odoo.addons.x_account_twitter.services import twitter_errors

_logger = logging.getLogger(__name__)


class SocialStream(models.Model):
    _inherit = 'social.stream'

    def _fetch_stream_data(self):
        self.ensure_one()
        if self.account_id.media_type != 'twitter':
            return super()._fetch_stream_data()
        try:
            return super()._fetch_stream_data()
        except twitter_errors.TwitterAuthenticationError as exc:
            _logger.warning(
                'x_account_twitter: skipping stream %s refresh — account %s '
                'cannot authenticate (%s); reauthorize the account.',
                self.id, self.account_id.id, exc)
            return False
