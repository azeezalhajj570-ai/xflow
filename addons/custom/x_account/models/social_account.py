# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

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
        ],
        string='X Provider',
        default='session_web',
        help='Provider implementation used for this account. Additional providers '
             '(e.g. OmniX REST API) are provided by optional modules that register '
             'themselves with XProviderRegistry.',
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
    x_encryption_code = fields.Char(
        string='XChat Encryption Code',
        groups='base.group_system',
        help='Your XChat PIN — the code you set when enabling encrypted chats on '
             'X. Used to recover your key so encrypted DM messages can be '
             'decrypted and outgoing events signed.',
    )
    x_chat_key_blob = fields.Text(
        string='X Chat Key Blob',
        groups='base.group_system',
        help='Opaque private-key blob exported from the official Chat XDK '
             '(chatxdk export_keys), stored base64-encoded (hex or a Python '
             'bytes repr are also accepted). Imported with import_keys to '
             'decrypt XChat encrypted message events. Treat as a password: '
             'never log it.',
    )
    x_chat_signing_key_version = fields.Char(
        string='X Chat Signing Key Version',
        groups='base.group_system',
        help='public_key_version of the account\'s registered Chat public key; '
             'passed to chatxdk set_identity.',
    )
    x_chat_key_mode = fields.Selection(
        [
            ('key_blob', 'Imported Key Blob'),
            ('juicebox', 'Secure Backup / PIN'),
        ],
        string='X Chat Key Source',
        default='key_blob',
        groups='base.group_system',
        help='Where the account\'s XChat private keys come from. "Imported Key '
             'Blob" stores the native export_keys() blob on the account; '
             '"Secure Backup / PIN" recovers keys from X\'s secure key backup '
             '(Juicebox) with the XChat encryption code and never stores a key '
             'blob server-side.',
    )
    x_chat_initialized = fields.Boolean(
        string='X Chat Encryption Initialized',
        groups='base.group_system',
        help='True once the Chat XDK has successfully imported/recovered the '
             'account\'s keys (via the configured key source) and set the '
             'identity. Cleared whenever the PIN/blob changes.',
    )
    x_chat_pin_locked = fields.Boolean(
        string='X Chat PIN Rejected',
        groups='base.group_system',
        copy=False,
        readonly=True,
        help='Set when X rejected the configured X Chat PIN. Unlock attempts '
             'are paused until a different PIN is entered — each wrong attempt '
             'consumes one of the limited guesses X allows before locking the '
             'secure backup permanently.',
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

    def _display_notification(self, title, message, kind='success'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': kind,
                'sticky': False,
            },
        }

    def _groups_not_supported(self, title, message):
        """Respond when the account's provider cannot fetch group DMs.

        UI clicks (dialog context, e.g. the 'Fetch Groups' server action) get a
        warning notification instead of a 500; programmatic callers keep the
        NotImplementedError signal.
        """
        if self.env.context.get('dialog'):
            return self._display_notification(title, message, kind='warning')
        raise NotImplementedError(message)

    def action_fetch_groups(self):
        """Fetch X group-DM conversations + members via the account's provider."""
        self.ensure_one()
        if not self._filter_x_accounts():
            raise ValueError('Fetch groups is only available on X accounts.')
        from odoo.addons.x_account.services.x_service import XService
        provider = XService.get_provider(self)
        fetch = getattr(provider, 'fetch_groups', None)
        if not fetch:
            return self._groups_not_supported(
                'Fetch Groups',
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
        from odoo.addons.x_account.services.x_service import XService
        provider = XService.get_provider(self)
        if getattr(provider, '_needs_encryption_code', True) and not self.x_encryption_code:
            raise ValueError(
                'Set the XChat Encryption Code on this account first — it is '
                'required to read encrypted group DMs.')
        fetch = getattr(provider, 'fetch_group_messages', None)
        if not fetch:
            return self._groups_not_supported(
                'Fetch Group Messages',
                'Provider %s does not support fetching group messages' % self.x_provider)
        result = fetch(self, limit=100)
        if self.env.context.get('dialog'):
            parts = ['Groups: %s, messages: %s, failures: %s' % (
                result.get('groups', 0), result.get('messages', 0),
                result.get('failures', 0))]
            if result.get('encrypted_skipped'):
                parts.append('encrypted skipped: %s' % result.get('encrypted_skipped'))
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fetch Group Messages',
                    'message': ', '.join(parts),
                    'type': 'success' if not result.get('failures') else 'warning',
                    'sticky': False,
                },
            }
        return result

    def action_initialize_x_chat_encryption(self):
        """Initialize the account's XChat encryption via its provider.

        Dispatches to the provider's ``initialize_x_chat_encryption`` so the
        official-X (blob import / Juicebox unlock) and any other provider can
        implement it with their own key material. Marks ``x_chat_initialized``
        on success and clears it on failure. Returns a dialog/notification
        result.
        """
        self.ensure_one()
        if not self._filter_x_accounts():
            raise ValueError('X Chat encryption is only available on X accounts.')
        from odoo.addons.x_account.services.x_service import XService
        provider = XService.get_provider(self)
        initialize = getattr(provider, 'initialize_x_chat_encryption', None)
        if not initialize:
            return self._display_notification(
                'X Chat Encryption',
                'Provider %s does not support initializing X Chat encryption'
                % self.x_provider, kind='warning')
        try:
            initialize(self)
        except Exception as exc:
            self.write({'x_chat_initialized': False})
            return self._display_notification(
                'X Chat Encryption',
                'Initialization failed: %s' % exc, kind='danger')
        self.write({'x_chat_initialized': True})
        mode = self.x_chat_key_mode or 'key_blob'
        source = 'PIN' if mode == 'juicebox' else 'key blob'
        return self._display_notification(
            'X Chat Encryption',
            'Initialized (key source: %s).' % source, kind='success')

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

    def write(self, vals):
        """Clear the PIN-lock flag when the operator changes the PIN.

        Each wrong PIN attempt consumes one of X's limited guesses before the
        secure backup is permanently locked. When X rejects the PIN we stamp
        ``x_chat_pin_locked`` so further attempts short-circuit without hitting
        X. Changing the PIN value means the operator is trying a different
        code, so the lock is lifted for the new attempt.
        """
        if 'x_encryption_code' in vals:
            new_pin = vals.get('x_encryption_code')
            to_unlock = self.filtered(
                lambda a: a.x_chat_pin_locked and a.x_encryption_code != new_pin)
            if to_unlock:
                super(SocialAccount, to_unlock).write({'x_chat_pin_locked': False})
        return super().write(vals)

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
