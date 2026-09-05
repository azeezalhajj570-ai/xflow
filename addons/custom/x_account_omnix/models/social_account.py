# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""OmniX webhook lifecycle on social.account.

These fields and actions are OmniX-specific (the OmniX REST API registers
webhooks that deliver DM/tweet/follow events to the Odoo receiver). They live in
this module, not in x_account, so x_account stays OmniX-agnostic and the webhook
surface is only present when the optional OmniX provider is installed.
"""

import logging
import secrets

from odoo import _, fields, models

_logger = logging.getLogger(__name__)


class SocialAccount(models.Model):
    _inherit = 'social.account'

    x_provider = fields.Selection(
        selection_add=[
            ('omnix', 'OmniX REST API'),
        ],
        ondelete={'omnix': 'cascade'},
    )

    x_webhook_id = fields.Char(
        string='X Webhook ID',
        readonly=True,
        help='OmniX webhook id registered for this account.',
    )
    x_webhook_secret = fields.Char(
        string='X Webhook Secret',
        readonly=True,
        groups='base.group_system',
        help='HMAC secret used to verify OmniX webhook deliveries. '
             'Returned once by OmniX at registration.',
    )
    x_webhook_url = fields.Char(
        string='X Webhook URL',
        readonly=True,
        help='Receiver URL this account\'s OmniX webhook posts to.',
    )
    x_webhook_valid = fields.Boolean(
        string='X Webhook Valid',
        readonly=True,
        help='True once the OmniX webhook passed the CRC handshake.',
    )

    def _webhook_receiver_url(self):
        """Public receiver URL for this account's webhook endpoint."""
        base = self.env['ir.config_parameter'].sudo().get_param(
            'x_account.webhook_base_url', '')
        base = (base or '').rstrip('/')
        if not base:
            raise ValueError(
                'Set the X Webhook Base URL in X Account Settings first '
                '(the public https URL of this Odoo instance).')
        return '%s/x_account/webhook/%s' % (base, self.id)

    def _get_webhook_provider(self):
        """Return the account's provider, or None when it is not webhook-capable."""
        from odoo.addons.x_account.services.x_service import XService
        provider = XService.get_provider(self)
        if not callable(getattr(provider, 'register_webhook', None)):
            return None
        return provider

    def action_register_webhook(self):
        """Register an OmniX webhook for this account (and store the secret).

        Requires the XChat Encryption Code: it opts the webhook into DM event
        delivery, which is the purpose of this integration.
        """
        self.ensure_one()
        if not self._filter_x_accounts():
            raise ValueError('Webhooks are only available on X accounts.')
        if not self.x_encryption_code:
            raise ValueError(
                'Set the XChat Encryption Code on this account first — it is '
                'required to receive DM events.')
        provider = self._get_webhook_provider()
        if not provider:
            raise NotImplementedError(
                'Provider %s does not support webhooks' % self.x_provider)
        # If a webhook already exists it was registered without the code;
        # delete it first so OmniX re-registers with DM events enabled.
        if self.x_webhook_id:
            try:
                provider.delete_webhook(self.x_webhook_id)
            except Exception:
                _logger.exception('Failed to delete old webhook %s', self.x_webhook_id)
        secret = self.x_webhook_secret or self.env['ir.config_parameter'].sudo().get_param(
            'x_account.webhook_secret') or secrets.token_hex(32)
        # Persist the secret BEFORE calling OmniX: it runs the CRC handshake
        # (GET ?crc_token) immediately on registration, so the receiver must
        # already know the secret or the handshake fails.
        self.write({'x_webhook_secret': secret})
        result = provider.register_webhook(self._webhook_receiver_url(), secret=secret)
        vals = {
            'x_webhook_id': result.get('id') or self.x_webhook_id,
            'x_webhook_url': result.get('url') or self._webhook_receiver_url(),
            'x_webhook_valid': bool(result.get('valid')),
        }
        if result.get('secret'):
            vals['x_webhook_secret'] = result['secret']
        self.write(vals)
        if self.env.context.get('dialog'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Register Webhook',
                    'message': 'Webhook registered (valid=%s).' % vals['x_webhook_valid'],
                    'type': 'success',
                    'sticky': False,
                },
            }
        return True

    def action_validate_webhook(self):
        """Re-run OmniX's CRC handshake for this account's webhook."""
        self.ensure_one()
        if not self.x_webhook_id:
            raise ValueError('No webhook registered for this account.')
        provider = self._get_webhook_provider()
        if not provider:
            raise NotImplementedError(
                'Provider %s does not support webhooks' % self.x_provider)
        result = provider.validate_webhook(self.x_webhook_id)
        self.write({'x_webhook_valid': bool(result.get('valid'))})
        if self.env.context.get('dialog'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Validate Webhook',
                    'message': 'Webhook CRC result: valid=%s' % self.x_webhook_valid,
                    'type': 'success',
                    'sticky': False,
                },
            }
        return True

    def action_delete_webhook(self):
        """Delete this account's OmniX webhook and clear the local state."""
        self.ensure_one()
        if not self.x_webhook_id:
            raise ValueError('No webhook registered for this account.')
        provider = self._get_webhook_provider()
        if not provider:
            raise NotImplementedError(
                'Provider %s does not support webhooks' % self.x_provider)
        provider.delete_webhook(self.x_webhook_id)
        self.write({
            'x_webhook_id': False,
            'x_webhook_secret': False,
            'x_webhook_url': False,
            'x_webhook_valid': False,
        })
        if self.env.context.get('dialog'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Delete Webhook',
                    'message': 'Webhook deleted.',
                    'type': 'success',
                    'sticky': False,
                },
            }
        return True

    def unlink(self):
        omnix_accounts = self.filtered(
            lambda a: a.x_provider == 'omnix' and a.x_webhook_id)
        for account in omnix_accounts:
            try:
                provider = account._get_webhook_provider()
                if provider and hasattr(provider, 'delete_webhook'):
                    provider.delete_webhook(account.x_webhook_id)
            except Exception:
                _logger.exception(
                    'x_account_omnix: failed to delete webhook for account %s',
                    account.id)
        return super().unlink()
