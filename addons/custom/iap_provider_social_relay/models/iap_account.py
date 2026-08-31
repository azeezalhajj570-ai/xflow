
import logging

import requests
from werkzeug.urls import url_encode, url_join

from odoo import fields, models

_logger = logging.getLogger(__name__)


class IapAccount(models.Model):
    _inherit = 'iap.account'

    provider = fields.Selection(
        selection_add=[('social_relay', 'Social Relay')],
        ondelete={'social_relay': 'set default'},
    )

    def _get_social_relay_endpoint(self):
        return self.env['ir.config_parameter'].sudo().get_param('iap_provider_social_relay.endpoint', '')

    def _get_social_relay_default_service(self):
        technical_name = self.env['ir.config_parameter'].sudo().get_param('iap_provider_social_relay.default_service', '')
        if not technical_name:
            return self.env['iap.service']
        return self.env['iap.service'].search([('technical_name', '=', technical_name)], limit=1)

    def _get_service_from_provider(self):
        self.ensure_one()
        if self.provider == 'social_relay':
            return self._get_social_relay_default_service()
        return super()._get_service_from_provider()

    def _get_account_information_from_iap(self):
        odoo_accounts = self.filtered(lambda account: account.provider == 'odoo')
        relay_accounts = self - odoo_accounts

        if odoo_accounts:
            super(IapAccount, odoo_accounts)._get_account_information_from_iap()

        if relay_accounts:
            for account in relay_accounts:
                # Keep non-odoo providers out of Odoo IAP sync to avoid wrong endpoint calls.
                account.with_context(disable_iap_update=True, tracking_disable=True).write({
                    'service_locked': False,
                    'state': 'registered',
                })

    def get_credits_url(self, service_name, base_url='', credit=0, trial=False, account_token=False):
        account = self.get(service_name, force_create=False)
        if not account or account.provider != 'social_relay':
            return super().get_credits_url(
                service_name,
                base_url=base_url,
                credit=credit,
                trial=trial,
                account_token=account_token,
            )

        endpoint = self._get_social_relay_endpoint()
        if not endpoint:
            return super().get_credits_url(
                service_name,
                base_url=base_url,
                credit=credit,
                trial=trial,
                account_token=account_token,
            )

        dbuuid = self.env['ir.config_parameter'].sudo().get_param('database.uuid')
        if not base_url:
            base_url = url_join(endpoint, '/iap/1/credit')
        if not account_token:
            account_token = account.account_token

        params = {
            'dbuuid': dbuuid,
            'service_name': service_name,
            'account_token': account_token,
            'credit': credit,
            'provider': 'social_relay',
        }
        if trial:
            params['trial'] = trial
        return '%s?%s' % (base_url, url_encode(params))

    def get_credits(self, service_name):
        account = self.get(service_name, force_create=False)
        if not account or account.provider != 'social_relay':
            return super().get_credits(service_name)

        endpoint = self._get_social_relay_endpoint()
        if not endpoint:
            return -1

        url = url_join(endpoint, '/api/iap/1/balance')
        params = {
            'dbuuid': self.env['ir.config_parameter'].sudo().get_param('database.uuid'),
            'account_token': account.account_token,
            'service_name': service_name,
        }
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            payload = response.json()
            # Accept both {'result': <num>} and {'credit': <num>} response styles.
            if isinstance(payload, dict):
                if 'result' in payload:
                    return payload['result']
                if 'credit' in payload:
                    return payload['credit']
            return payload
        except (requests.RequestException, ValueError, TypeError) as err:
            _logger.warning('Social relay credit fetch failed: %s', err)
            return -1

