# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Route X streams to the provider-agnostic post sync.

Only streams whose provider actually implements the read contract are
intercepted; official-OAuth `twitter` accounts (whose provider declares no
read operations) keep using `social_twitter`'s own fetch. Detection is by
declared capability, not by a provider-code list, so a new provider (GetXAPI,
OmniX, a custom XActions API, ...) becomes fetchable simply by declaring
``_read_operations`` on its class.
"""

from odoo import models

X_ACCOUNT_POSTS_STREAM_TYPE = 'x_account_posts'
_TIMELINE_READ = 'fetch_user_posts'


def provider_supports_post_reads(account):
    """Whether the account's provider implements the timeline read contract.

    Resolves the provider *class* (no instantiation, no cookies, no network)
    and checks its declared ``_read_operations``.
    """
    from odoo.addons.x_account.services.x_provider import XProviderRegistry
    provider_cls = XProviderRegistry.resolve(account.x_provider)
    if provider_cls is None:
        return False
    return _TIMELINE_READ in (getattr(provider_cls, '_read_operations', None) or ())


class SocialStream(models.Model):
    _inherit = 'social.stream'

    def _is_x_account_stream(self):
        self.ensure_one()
        account = self.account_id
        if not account or self.media_id.media_type != 'twitter':
            return False
        if self.stream_type_id.stream_type == X_ACCOUNT_POSTS_STREAM_TYPE:
            return True
        return provider_supports_post_reads(account)

    def _fetch_stream_data(self):
        self.ensure_one()
        if self.env.context.get('x_skip_stream_fetch'):
            return False
        # X stream fetches are manual-only. The feed's automatic refresh
        # (`social.stream.refresh_all`, run when the feed kanban loads and by
        # its Refresh button) walks every stream, so it would call the metered
        # X API for each X account. Once the app's X credits are depleted that
        # answers 402 Payment Required and surfaces as a feed error. Skip X
        # streams here; the "Fetch X Posts" wizard drives a fetch explicitly.
        if (self.media_id.media_type == 'twitter'
                and not self.env.context.get('x_allow_stream_fetch')):
            return False
        if not self._is_x_account_stream():
            return super()._fetch_stream_data()
        from ..services.post_sync import XPostSync
        XPostSync(self.env, self.account_id).sync_timeline(self)
        return True
