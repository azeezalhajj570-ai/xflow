# Part of Odoo. See LICENSE file for full copyright and licensing details.
import base64
import json
import logging

import requests

from odoo import http, _
from odoo.http import request
from odoo.tools.urls import urljoin as url_join
from odoo.addons.social.controllers.main import SocialController, SocialValidationException

_logger = logging.getLogger(__name__)


class SocialRedditController(SocialController):

    @http.route(['/social_reddit/callback'], type='http', auth='user')
    def social_reddit_account_callback(self, code=None, state=None, error=None, **kw):
        if not request.env.user.has_group('social.group_social_manager'):
            return request.render(
                'social.social_http_error_view',
                {'error_message': _('Unauthorized. Please contact your administrator.')})

        if error:
            return request.render(
                'social.social_http_error_view',
                {'error_message': _('Reddit authorization was denied.')})

        if not code:
            return request.render(
                'social.social_http_error_view',
                {'error_message': _('Reddit did not provide a valid authorization code.')})

        stored_state = request.env['ir.config_parameter'].sudo().get_param('social.reddit_oauth_state')
        if not state or state != stored_state:
            _logger.warning('Social Reddit: CSRF state mismatch — expected %s, got %s', stored_state, state)
            return request.render(
                'social.social_http_error_view',
                {'error_message': _(
                    'Invalid state parameter. This could be a CSRF attack – '
                    'please try adding the account again.')})

        request.env['ir.config_parameter'].sudo().set_param('social.reddit_oauth_state', '')

        media = request.env.ref('social_reddit.social_media_reddit')
        try:
            self._reddit_create_accounts(code, media)
        except SocialValidationException as e:
            return request.render(
                'social.social_http_error_view',
                {'error_message': e.get_message(), 'documentation_data': e.get_documentation_data()})

        return request.redirect('/odoo/action-social.action_social_stream_post')

    @http.route('/social_reddit/comment', type='http', auth='user', methods=['POST'])
    def social_reddit_add_comment(self, stream_post_id, message=None, comment_id=None, **kw):
        stream_post = self._get_social_stream_post(stream_post_id, 'reddit')
        attachment = None
        files = request.httprequest.files.getlist('attachment')
        if files and files[0]:
            attachment = files[0].read()
        try:
            result = stream_post._reddit_comment_add(message, comment_id, attachment)
        except Exception as e:
            return json.dumps({'error': str(e)})
        return json.dumps(result)

    @http.route('/social_reddit/delete_comment', type='jsonrpc', auth='user')
    def social_reddit_delete_comment(self, stream_post_id, comment_id, **kw):
        stream_post = self._get_social_stream_post(stream_post_id, 'reddit')
        return stream_post._reddit_comment_delete(comment_id)

    @http.route('/social_reddit/get_comments', type='jsonrpc', auth='user')
    def social_reddit_get_comments(self, stream_post_id, **kw):
        stream_post = self._get_social_stream_post(stream_post_id, 'reddit')
        return stream_post._reddit_comment_fetch()

    def _reddit_create_accounts(self, code, media):
        client_id = request.env['ir.config_parameter'].sudo().get_param('social.reddit_client_id')
        client_secret = request.env['ir.config_parameter'].sudo().get_param('social.reddit_client_secret')
        base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url')
        redirect_uri = url_join(base_url, 'social_reddit/callback')

        from odoo.addons.social_reddit.services.reddit_client import RedditClient

        reddit_client = RedditClient(client_id=client_id, client_secret=client_secret)
        token_data = reddit_client.token_exchange(code, redirect_uri)
        if not token_data:
            raise SocialValidationException(_('Reddit token exchange failed. Please try again.'))

        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')
        expires_in = token_data.get('expires_in', 3600)

        reddit_client.access_token = access_token
        me_response = reddit_client.get_me()
        if not me_response.ok:
            raise SocialValidationException(_('Could not fetch Reddit user information.'))

        user_info = me_response.json()
        reddit_user_id = user_info.get('id')
        username = user_info.get('name')
        icon_img = user_info.get('icon_img', '')
        total_karma = user_info.get('total_karma', 0)

        avatar = False
        if icon_img:
            try:
                img_resp = requests.get(icon_img, timeout=10)
                if img_resp.ok:
                    avatar = base64.b64encode(img_resp.content)
            except Exception:
                pass

        existing_account = request.env['social.account'].sudo().with_context(active_test=False).search([
            ('media_id', '=', media.id),
            ('reddit_user_id', '=', reddit_user_id),
        ], limit=1)

        from datetime import datetime, timedelta

        account_vals = {
            'active': True,
            'reddit_access_token': access_token,
            'reddit_refresh_token': refresh_token,
            'reddit_token_expiry': datetime.now() + timedelta(seconds=expires_in),
            'is_media_disconnected': False,
            'audience': total_karma,
        }

        if existing_account:
            error_message = existing_account._get_multi_company_error_message()
            if error_message:
                raise SocialValidationException(error_message)
            existing_account.write(account_vals)
        else:
            account_vals.update({
                'name': username,
                'media_id': media.id,
                'reddit_user_id': reddit_user_id,
                'social_account_handle': username,
                'has_trends': False,
            })
            if avatar:
                account_vals['image'] = avatar
            request.env['social.account'].create(account_vals)
