# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging

from datetime import datetime, timedelta
from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SocialAccount(models.Model):
    _inherit = 'social.account'

    reddit_user_id = fields.Char('Reddit User ID', readonly=True,
        help='Reddit user ID provided by the Reddit API. Do not set manually.')
    reddit_access_token = fields.Char('Reddit Access Token', readonly=True,
        help='Access token used to authenticate Reddit API requests.')
    reddit_refresh_token = fields.Char('Reddit Refresh Token', readonly=True,
        help='Refresh token used to renew expired access tokens.')
    reddit_token_expiry = fields.Datetime('Token Expiration', readonly=True,
        help='Expiration date of the Reddit access token.')

    def _compute_stats_link(self):
        reddit_accounts = self._filter_by_media_types(['reddit'])
        super(SocialAccount, (self - reddit_accounts))._compute_stats_link()
        for account in reddit_accounts:
            handle = account.social_account_handle or account.name
            account.stats_link = 'https://www.reddit.com/user/%s/' % handle if handle else False

    def _compute_statistics(self):
        reddit_accounts = self._filter_by_media_types(['reddit'])
        super(SocialAccount, (self - reddit_accounts))._compute_statistics()
        for account in reddit_accounts.filtered('reddit_access_token'):
            client = account._get_reddit_client()
            if not client:
                continue
            karma_resp = client.get_karma()
            if karma_resp.ok:
                karma_data = karma_resp.json().get('data', [])
                total_link_karma = sum(k.get('link_karma', 0) for k in karma_data)
                total_comment_karma = sum(k.get('comment_karma', 0) for k in karma_data)
                account.write({
                    'audience': total_link_karma + total_comment_karma,
                    'engagement': total_comment_karma,
                })

    def _get_reddit_client(self):
        self.ensure_one()
        if not self.reddit_access_token:
            return None
        if self.reddit_token_expiry and self.reddit_token_expiry <= datetime.now():
            self._refresh_reddit_token()
        from odoo.addons.social_reddit.services.reddit_client import RedditClient
        client_id = self.env['ir.config_parameter'].sudo().get_param('social.reddit_client_id')
        client_secret = self.env['ir.config_parameter'].sudo().get_param('social.reddit_client_secret')
        return RedditClient(
            access_token=self.reddit_access_token,
            refresh_token=self.reddit_refresh_token,
            client_id=client_id,
            client_secret=client_secret,
        )

    def _refresh_reddit_token(self):
        client_id = self.env['ir.config_parameter'].sudo().get_param('social.reddit_client_id')
        client_secret = self.env['ir.config_parameter'].sudo().get_param('social.reddit_client_secret')
        from odoo.addons.social_reddit.services.reddit_client import RedditClient
        reddit_client = RedditClient(
            access_token=self.reddit_access_token,
            refresh_token=self.reddit_refresh_token,
            client_id=client_id,
            client_secret=client_secret,
        )
        token_data = reddit_client.refresh_token_request()
        if token_data:
            expires_in = token_data.get('expires_in', 3600)
            self.sudo().write({
                'reddit_access_token': reddit_client.access_token,
                'reddit_token_expiry': datetime.now() + timedelta(seconds=expires_in),
                'is_media_disconnected': False,
            })
            return True
        _logger.warning('Reddit: Failed to refresh token for account %s', self.display_name)
        self._action_disconnect_accounts('Token refresh failed')
        return False

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        res.filtered(lambda a: a.media_type == 'reddit')._create_default_stream_reddit()
        return res

    def _create_default_stream_reddit(self):
        if not self:
            return
        my_posts_stream_type = self.env.ref('social_reddit.stream_type_my_posts')
        streams = [{
            'media_id': account.media_id.id,
            'stream_type_id': my_posts_stream_type.id,
            'account_id': account.id,
        } for account in self]
        if streams:
            self.env['social.stream'].create(streams)
