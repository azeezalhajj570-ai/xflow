
import base64
import logging
import requests

from werkzeug.urls import url_join

from odoo import _, models, tools
from odoo.addons.iap.tools import iap_tools
from odoo.addons.social.controllers.main import SocialValidationException
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SocialAccountPushNotificationsCustomRelay(models.Model):
    _inherit = 'social.account'

    def _firebase_send_message_from_iap(self, data, visitors):
        endpoint = (
            self.env['ir.config_parameter'].sudo().get_param('social.custom_relay_endpoint')
            or self.env['ir.config_parameter'].sudo().get_param('social.social_iap_endpoint')
            or self.env['social.media']._DEFAULT_SOCIAL_IAP_ENDPOINT
        )
        batch_size = 100
        tokens = visitors.mapped('push_subscription_ids.push_token')
        data.update({'db_uuid': self.env['ir.config_parameter'].sudo().get_param('database.uuid')})
        for tokens_batch in tools.split_every(batch_size, tokens, piece_maker=list):
            batch_data = dict(data)
            batch_data['tokens'] = tokens_batch
            iap_tools.iap_jsonrpc(
                url_join(endpoint, '/iap/social_push_notifications/firebase_send_message'),
                params=batch_data,
            )
        return []

    def _get_linkedin_accounts(self, linkedin_access_token):
        response = requests.get(
            'https://api.linkedin.com/v2/userinfo',
            headers={
                'Authorization': 'Bearer %s' % linkedin_access_token,
            },
            timeout=5,
        )
        if not response.ok:
            raise SocialValidationException(_('An error occurred when fetching your profile: “%s”.', response.text))
        profile = response.json()

        account_id = profile.get('sub', 'me')
        name = profile.get('name', 'LinkedIn Profile')
        given_name = profile.get('given_name', '')
        family_name = profile.get('family_name', '')
        name = f'{given_name} {family_name}'.strip() or name
        picture_url = profile.get('picture', '')

        image = False
        if picture_url:
            try:
                img_response = requests.get(picture_url, timeout=10)
                if img_response.ok:
                    image = base64.b64encode(img_response.content)
            except Exception:
                _logger.exception('Failed to fetch LinkedIn profile picture')

        return [{
            'name': name,
            'linkedin_account_urn': f'urn:li:person:{account_id}',
            'linkedin_access_token': linkedin_access_token,
            'social_account_handle': name,
            'image': image or False,
        }]

    def _compute_stats_link(self):
        linkedin_accounts = self._filter_by_media_types(['linkedin'])
        super(SocialAccountPushNotificationsCustomRelay, self - linkedin_accounts)._compute_stats_link()
        for account in linkedin_accounts:
            if 'person' in (account.linkedin_account_urn or ''):
                account.stats_link = False
            else:
                account.stats_link = 'https://www.linkedin.com/company/%s/admin/analytics/visitors/' % account.linkedin_account_id

    def _create_default_stream_linkedin(self):
        company_streams = self.filtered(lambda a: 'organization' in (a.linkedin_account_urn or ''))
        person_accounts = self - company_streams
        super(SocialAccountPushNotificationsCustomRelay, company_streams)._create_default_stream_linkedin()
        stream_type = self.env.ref('social_linkedin.stream_type_linkedin_company_post')
        for account in person_accounts:
            self.env['social.stream'].create({
                'media_id': account.media_id.id,
                'stream_type_id': stream_type.id,
                'account_id': account.id,
            })
