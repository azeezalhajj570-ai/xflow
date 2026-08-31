# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import secrets

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class SocialAccount(models.Model):
    _inherit = 'social.account'

    x_connection_status = fields.Selection(
        [
            ('new', 'New'),
            ('authenticating', 'Authenticating'),
            ('active', 'Active'),
            ('disconnected', 'Disconnected'),
            ('invalid', 'Invalid'),
            ('reauth_required', 'Reauthentication Required'),
            ('error', 'Error'),
            ('disabled', 'Disabled'),
        ],
        string='X Connection Status',
        default='new',
        help='Lifecycle state of the X account connection.',
    )
    last_connected = fields.Datetime(string='Last Connected', readonly=True)
    last_validated = fields.Datetime(string='Last Validated', readonly=True)
    last_error = fields.Text(
        string='Last Error',
        readonly=True,
        help='Last classified error. Never contains credentials.',
    )
    x_provider = fields.Selection(
        [
            ('session_web', 'Session Web'),
            ('official_publish', 'Official Publish'),
            ('omnix', 'OmniX REST API'),
        ],
        string='X Provider',
        default='session_web',
        help='Provider implementation used for this account.',
    )
    x_auth_method = fields.Selection(
        [
            ('session_cookie', 'Session Cookie'),
            ('oauth1', 'OAuth 1.0a'),
        ],
        string='X Auth Method',
        default='session_cookie',
        help='Authentication method. Independent of the provider.',
    )
    x_session_store_id = fields.Many2one(
        'x.session.store',
        string='X Session Store',
        ondelete='set null',
        help='Encrypted session credentials vault record.',
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
    x_encryption_code = fields.Char(
        string='XChat Encryption Code',
        groups='base.group_system',
        help='Your XChat PIN — the code you set when enabling encrypted chats on '
             'X. Used to recover your key so encrypted DM messages can be '
             'decrypted and outgoing events signed.',
    )

    x_migration_status = fields.Selection(
        [
            ('pending', 'Pending'),
            ('migrated', 'Migrated'),
            ('failed', 'Failed'),
        ],
        string='X Migration Status',
        help='Status of migration from XAction.',
    )
    source_account_id = fields.Char(string='Source Account ID', help='XAction Account.id')
    source_user_id = fields.Char(string='Source User ID', help='XAction User.id')
    migration_batch_id = fields.Char(string='Migration Batch ID')
    migration_timestamp = fields.Datetime(string='Migration Timestamp')
    x_group_count = fields.Integer(
        string='X Groups',
        compute='_compute_x_group_count',
        help='Number of X group-DM channels for this account.',
    )

    @api.depends('x_session_store_id')
    def _compute_x_group_count(self):
        group_model = self.env['discuss.channel'].sudo()
        for account in self:
            account.x_group_count = group_model.search_count([
                ('channel_type', '=', 'x_group'),
                ('x_account_id', '=', account.id),
            ])

    def _filter_x_accounts(self):
        return self.filtered(lambda a: a.media_type == 'twitter')

    def action_link_account(self):
        """Open the X link-account wizard (used by the X Accounts list 'New')."""
        return {
            'name': 'Link X Account',
            'type': 'ir.actions.act_window',
            'res_model': 'x.import.session',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_media_id': self.env['social.media'].search(
                [('media_type', '=', 'twitter')], limit=1).id},
        }

    def action_x_account_groups_by_account(self):
        """Open the X Groups list filtered to this account (stat button)."""
        self.ensure_one()
        return {
            'name': 'X Groups',
            'type': 'ir.actions.act_window',
            'res_model': 'discuss.channel',
            'view_mode': 'list,form',
            'domain': [('channel_type', '=', 'x_group'),
                       ('x_account_id', '=', self.id)],
            'context': dict(self.env.context,
                            default_x_account_id=self.id),
        }

    def action_fetch_groups(self):
        """Fetch X group-DM conversations + members via the account's provider."""
        self.ensure_one()
        if not self._filter_x_accounts():
            raise ValueError('Fetch groups is only available on X accounts.')
        from odoo.addons.x_account.services.x_service import XService
        provider = XService.get_provider(self)
        fetch = getattr(provider, 'fetch_groups', None)
        if not fetch:
            raise NotImplementedError(
                'Provider %s does not support fetching groups' % self.x_provider)
        result = fetch(self, limit=100)
        if self.env.context.get('dialog'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fetch Groups',
                    'message': 'Groups: %s, created: %s, updated: %s, members: %s' % (
                        result.get('groups', 0), result.get('created', 0),
                        result.get('updated', 0), result.get('members', 0)),
                    'type': 'success',
                    'sticky': False,
                },
            }
        return result

    def action_fetch_group_messages(self):
        """Fetch group-DM messages via the account's provider into discuss."""
        self.ensure_one()
        if not self._filter_x_accounts():
            raise ValueError('Fetch group messages is only available on X accounts.')
        if not self.x_encryption_code:
            raise ValueError(
                'Set the XChat Encryption Code on this account first — it is '
                'required to read encrypted group DMs.')
        from odoo.addons.x_account.services.x_service import XService
        provider = XService.get_provider(self)
        fetch = getattr(provider, 'fetch_group_messages', None)
        if not fetch:
            raise NotImplementedError(
                'Provider %s does not support fetching group messages' % self.x_provider)
        result = fetch(self, limit=100)
        if self.env.context.get('dialog'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fetch Group Messages',
                    'message': 'Groups: %s, messages: %s, failures: %s' % (
                        result.get('groups', 0), result.get('messages', 0),
                        result.get('failures', 0)),
                    'type': 'success' if not result.get('failures') else 'warning',
                    'sticky': False,
                },
            }
        return result

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
        from odoo.addons.x_account.services.x_service import XService
        provider = XService.get_provider(self)
        register = getattr(provider, 'register_webhook', None)
        if not register:
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
        result = register(self._webhook_receiver_url(), secret=secret)
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
        from odoo.addons.x_account.services.x_service import XService
        provider = XService.get_provider(self)
        validate = getattr(provider, 'validate_webhook', None)
        if not validate:
            raise NotImplementedError(
                'Provider %s does not support webhooks' % self.x_provider)
        result = validate(self.x_webhook_id)
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
        from odoo.addons.x_account.services.x_service import XService
        provider = XService.get_provider(self)
        delete = getattr(provider, 'delete_webhook', None)
        if not delete:
            raise NotImplementedError(
                'Provider %s does not support webhooks' % self.x_provider)
        delete(self.x_webhook_id)
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

    def _skip_oauth_stats(self):
        """Accounts managed by x_account (omnix / session_web) have no OAuth
        tokens and must never call social_twitter's stats endpoints (which fall
        back to the slow IAP signature service when consumer keys are missing)."""
        return self.filtered(
            lambda a: a.media_type == 'twitter'
            and a.x_provider in ('omnix', 'session_web'))

    def _compute_statistics(self):
        """Skip OAuth-based stats for x_account-managed accounts."""
        skip = self._skip_oauth_stats()
        if skip:
            skip.write({'audience': 0, 'engagement': 0, 'stories': 0})
        return super(SocialAccount, self - skip)._compute_statistics()

    @api.model_create_multi
    def create(self, vals_list):
        """Suppress social_twitter's default-stream creation on session import.

        social_twitter.create() creates a 'twitter_follow' social.stream (and a
        matching social.twitter.account) for every new twitter account. Stream
        and stat operations need OAuth signatures that fall back to the IAP
        service when consumer keys are not configured (slow + network-dependent).
        Session imports (session_web / omnix) don't need the stream, so when
        x_no_default_stream is set we no-op the stream creation during create().
        """
        if self.env.context.get('x_no_default_stream'):
            from odoo.addons.social_twitter.models.social_account import (
                SocialAccount as TwitterSocialAccount)
            original = TwitterSocialAccount._create_default_stream_twitter
            TwitterSocialAccount._create_default_stream_twitter = lambda self: None
            try:
                return super().create(vals_list)
            finally:
                TwitterSocialAccount._create_default_stream_twitter = original
        return super().create(vals_list)

    def _transition(self, status):
        self.write({'x_connection_status': status})

    def _set_last_error(self, message):
        self.write({'last_error': message})

    def _post_lifecycle_message(self, body):
        """Record a lifecycle note as a mail.message on the account.

        social.account is a plain model (not a mail.thread), so we create the
        mail.message record explicitly.
        """
        self.ensure_one()
        self.env['mail.message'].sudo().create({
            'model': self._name,
            'res_id': self.id,
            'body': body,
            'message_type': 'comment',
            'subtype_id': self.env['ir.model.data']._xmlid_to_res_id('mail.mt_comment'),
        })

    @api.model
    def _cron_validate_x_sessions(self):
        """Sweep X accounts (media_type twitter) and validate sessions, isolating
        per-account failures."""
        accounts = self.sudo().search([
            ('media_type', '=', 'twitter'),
            ('active', '=', True),
            ('x_session_store_id', '!=', False),
            ('x_connection_status', 'in', ('active', 'reauth_required', 'error')),
        ])
        from odoo.addons.x_account.services.x_service import XService
        for account in accounts:
            try:
                XService.restore_and_validate(account)
            except Exception:
                _logger.exception('Session validation failed for account %s', account.id)

    @api.model
    def _migrate_from_xaction(self, rows, batch_id, source='xaction'):
        """Create/update social.account records from XAction rows.

        rows: list of dicts with keys username, display_name, session_cookie,
        user_id, source_account_id, is_active, auth_token, ct0 (canonical cookies).
        Non-destructive and idempotent (XAction source is never touched here).
        Returns created/updated records.
        """
        from odoo.addons.x_account.services.session_manager import XSessionManager
        twitter_media = self.env.ref('social_twitter.social_media_twitter')
        result = self.env['social.account']
        now = fields.Datetime.now()
        for row in rows:
            cookie = row.get('session_cookie') or (
                'auth_token=%s; ct0=%s' % (row.get('auth_token', ''), row.get('ct0', '')))
            handle = row.get('username')
            existing = self.sudo().search([
                ('social_account_handle', '=', handle),
                ('media_type', '=', 'twitter'),
            ], limit=1)
            vals = {
                'media_id': twitter_media.id,
                'name': row.get('display_name') or handle or 'X Account',
                'social_account_handle': handle or '',
                'twitter_user_id': row.get('user_id'),
                'x_provider': 'session_web',
                'x_auth_method': 'session_cookie',
                'x_migration_status': 'pending',
                'source_account_id': row.get('source_account_id'),
                'source_user_id': row.get('source_user_id'),
                'migration_batch_id': batch_id,
                'migration_timestamp': now,
            }
            if existing:
                existing.write({k: v for k, v in vals.items() if v})
                account = existing
            else:
                account = self.sudo().with_context(
                    x_no_default_stream=True).create(vals)
            if cookie:
                XSessionManager.create_store(account, cookie, source=source)
            result |= account
        return result
