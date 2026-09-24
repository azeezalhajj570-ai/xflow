# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

import pytz
from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


def _to_minute_of_day(value):
    """Convert an hours-since-midnight value (e.g. 22.5) to minutes."""
    try:
        return int(round(float(value) * 60)) % 1440
    except (TypeError, ValueError):
        return 0


def _local_minute_of_day(now_utc, tz_name):
    """Minute of day of a UTC datetime in ``tz_name``.

    Odoo forces the process timezone to UTC, so a bare ``datetime.now()`` is
    not the wall clock the person configuring the window is looking at. An
    unknown or empty ``tz_name`` falls back to UTC (same as Odoo).
    """
    utc_datetime = pytz.utc.localize(now_utc, is_dst=False)
    try:
        localized = utc_datetime.astimezone(pytz.timezone(tz_name))
    except Exception:
        localized = utc_datetime
    return localized.hour * 60 + localized.minute


def _in_daily_window(now_minute, start, end):
    """Whether a minute-of-day falls inside a daily window.

    A window whose end is before its start wraps past midnight
    (e.g. 23:00 -> 01:00); equal ends mean a single minute.
    """
    if start <= end:
        return start <= now_minute <= end
    return now_minute >= start or now_minute <= end


# Fields whose change means the operator reconfigured the account's chat key
# material: writing any of them clears the chat-decryption alert, so a fixed
# account can alert again later instead of staying flagged forever.
_CHAT_KEY_FIELDS = frozenset({
    'x_encryption_code', 'x_chat_key_blob', 'x_chat_key_mode',
    'x_chat_initialized',
})

# Detailed connection states that mean a required X component is failing. They
# all collapse into the 'error' value of the aggregated x_connection_status.
_X_CONNECTION_FAILED_STATES = frozenset({
    'reauth_required', 'disconnected', 'invalid', 'error',
})

# Chat encryption states that mean a required component is failing. Kept next to
# the connection failures because the account form reports one overall status.
_X_CHAT_FAILED_STATES = frozenset({'pin_locked', 'stopped'})

# Writing any of these can move the aggregated x_connection_status, so the write
# hook resyncs it after the change is applied.
_X_STATUS_TRIGGER_FIELDS = frozenset({
    'x_connection_state', 'x_chat_initialized', 'x_chat_pin_locked',
    'x_chat_decrypt_stopped', 'x_encryption_code', 'x_chat_key_blob',
    'x_chat_key_mode',
})

_EVENT_PROVIDER_REGISTRY_MAP = {
    'official': 'twitter',
}

_ACTION_PROVIDER_REGISTRY_MAP = {
    'official': 'twitter',
    'getxapi': 'getxapi',
}

_EVENT_OPERATIONS = frozenset({
    'process_webhook_event', 'process_webhook_events',
    'register_webhook', 'validate_webhook_registration',
    'unsubscribe_all_events', 'delete_webhook_registration',
    'subscribe_account',
    'send_group_dm',
})

_ACTION_OPERATIONS = frozenset({
    'like', 'comment', 'repost', 'follow', 'post_tweet',
    'send_dm', 'bookmark', 'unbookmark', 'validate_session',
    'edit_tweet', 'unfollow',
})


class SocialAccount(models.Model):
    _name = 'social.account'
    _inherit = ['social.account', 'mail.thread', 'mail.activity.mixin']

    # Consecutive encrypted chat deliveries that can be read as nothing before
    # the account is considered to have stopped decrypting and its users are
    # told. A working account stores a message long before this; an account
    # whose key material is wrong (missing secure-backup config, throttled
    # public-key fetch, rejected PIN) crosses it within a minute of traffic.
    _CHAT_DECRYPT_ALERT_THRESHOLD = 50

    active = fields.Boolean(
        string='Active',
        default=True,
        tracking=True,
        help='Unchecking archives the account: its X subscriptions are pruned, '
             'its queued tasks are cancelled and no automation runs for it '
             'until it is unarchived.',
    )
    x_connection_state = fields.Selection(
        [
            ('new', 'New'),
            ('authenticating', 'Authenticating'),
            ('active', 'Active'),
            ('reauth_required', 'Reauthentication Required'),
            ('disconnected', 'Disconnected'),
            ('invalid', 'Invalid'),
            ('error', 'Error'),
            ('disabled', 'Disabled'),
        ],
        string='X Connection State',
        default='new',
        copy=False,
        help='Detailed X connection lifecycle, kept for diagnosis and for the '
             'automation guards. It is never shown in a view: the account form '
             'reports the aggregated "X Connection Status" instead.',
    )
    x_connection_status = fields.Selection(
        [
            ('not_configured', 'Not Configured'),
            ('active', 'Connected'),
            ('error', 'Error'),
        ],
        string='X Connection Status',
        default='not_configured',
        tracking=True,
        readonly=True,
        help='Overall X account health, in one value: not configured until the X '
             'account setup and the required Chat Encryption setup are complete, '
             'connected while X authentication is valid and Chat Encryption is '
             'ready, and error when a required X connection/encryption component '
             'is failing. System-controlled.',
    )
    last_connected = fields.Datetime(string='Last Connected', readonly=True)
    last_validated = fields.Datetime(string='Last Validated', readonly=True)
    last_error = fields.Text(
        string='Last Error',
        readonly=True,
        help='Last classified error. Never contains credentials.',
    )
    x_auto_archive = fields.Boolean(
        string='Archive Daily',
        tracking=True,
        help='When enabled, the scheduled action archives (deactivates) this '
             'account inside the window below. Archiving prunes the account\'s '
             'X Activity API subscriptions and cancels its queued tasks, so '
             'none of its automation runs anymore.',
    )
    x_auto_archive_start = fields.Float(
        string='Archive From',
        tracking=True,
        help='Start of this account\'s daily archive window, read on the '
             'timezone of the account\'s company (e.g. 22:30). Required when '
             '"Archive Daily" is enabled.',
    )
    x_auto_archive_end = fields.Float(
        string='Archive Until',
        tracking=True,
        help='End of this account\'s daily archive window, read on the '
             'timezone of the account\'s company. If earlier than "Archive '
             'From" the window wraps past midnight (e.g. 23:00 -> 01:00). '
             'Required when "Archive Daily" is enabled.',
    )
    x_provider = fields.Selection(
        [
            ('session_web', 'Session Web'),
            ('official_publish', 'Official Publish'),
        ],
        string='X Provider',
        compute='_compute_x_provider',
        inverse='_inverse_x_provider',
        help='Provider implementation used for this account. Additional providers '
             '(e.g. OmniX REST API) are provided by optional modules that register '
             'themselves with XProviderRegistry.',
    )
    x_event_provider = fields.Selection(
        [
            ('official', 'Official X API'),
        ],
        string='Event Provider',
        compute='_compute_x_event_provider',
        inverse='_inverse_x_event_provider',
        help='Provider for webhooks, event subscriptions, and incoming event '
             'processing. When empty, falls back to x_provider.',
    )
    x_action_provider = fields.Selection(
        [
            ('getxapi', 'REST API'),
            ('official', 'Official X API'),
        ],
        string='Action Provider',
        compute='_compute_x_action_provider',
        inverse='_inverse_x_action_provider',
        help='Provider for mutations (retweet, reply, like, follow, send DM, etc.) '
             'and data reads. When empty, falls back to x_provider.',
    )
    x_auth_method = fields.Selection(
        [
            ('session_cookie', 'Session Cookie'),
            ('oauth1', 'OAuth 1.0a'),
        ],
        string='X Auth Method',
        default='session_cookie',
        tracking=True,
        help='Authentication method. Independent of the provider.',
    )
    x_session_store_id = fields.Many2one(
        'x.session.store',
        string='X Session Store',
        ondelete='set null',
        tracking=True,
        help='Encrypted session credentials vault record.',
    )
    x_encryption_code = fields.Char(
        string='XChat Encryption Code',
        groups='social.group_social_user',
        help='Your XChat PIN — the code you set when enabling encrypted chats on '
             'X. Used to recover your key so encrypted DM messages can be '
             'decrypted and outgoing events signed.',
    )
    x_chat_key_blob = fields.Text(
        string='X Chat Key Blob',
        groups='social.group_social_user',
        help='Opaque private-key blob exported from the official Chat XDK '
             '(chatxdk export_keys), stored base64-encoded (hex or a Python '
             'bytes repr are also accepted). Imported with import_keys to '
             'decrypt XChat encrypted message events. Treat as a password: '
             'never log it.',
    )
    x_chat_signing_key_version = fields.Char(
        string='X Chat Signing Key Version',
        groups='social.group_social_user',
        help='public_key_version of the account\'s registered Chat public key; '
             'passed to chatxdk set_identity.',
    )
    x_chat_key_mode = fields.Selection(
        [
            ('key_blob', 'Imported Key Blob'),
            ('juicebox', 'Secure Backup / PIN'),
        ],
        string='X Chat Key Source',
        default='juicebox',
        groups='social.group_social_user',
        tracking=True,
        help='Where the account\'s XChat private keys come from. "Imported Key '
             'Blob" stores the native export_keys() blob on the account; '
             '"Secure Backup / PIN" recovers keys from X\'s secure key backup '
             '(Juicebox) with the XChat encryption code and never stores a key '
             'blob server-side.',
    )
    x_chat_conversation_keys = fields.Json(
        string='X Chat Conversation Keys',
        groups='social.group_social_user',
        help='Recovered chat conversation keys per conversation '
             '{conversation_id: {public_key_version: base64 key}}. Cached so '
             'encrypted group metadata/messages can be decrypted without '
             're-scanning the events feed on every sync. Treat as a password: '
             'never log it.',
    )
    x_chat_initialized = fields.Boolean(
        string='X Chat Encryption Initialized',
        groups='social.group_social_user',
        tracking=True,
        help='True once the Chat XDK has successfully imported/recovered the '
             'account\'s keys (via the configured key source) and set the '
             'identity. Cleared whenever the PIN/blob changes.',
    )
    x_chat_pin_locked = fields.Boolean(
        string='X Chat PIN Rejected',
        groups='social.group_social_user',
        copy=False,
        readonly=True,
        tracking=True,
        help='Set when X rejected the configured X Chat PIN. Unlock attempts '
             'are paused until a different PIN is entered — each wrong attempt '
             'consumes one of the limited guesses X allows before locking the '
             'secure backup permanently.',
    )
    x_chat_decrypt_fail_streak = fields.Integer(
        string='X Chat Unread Deliveries',
        copy=False,
        readonly=True,
        groups='social.group_social_user',
        help='Consecutive encrypted chat deliveries that produced no readable '
             'text since the last stored message. Restarts whenever a message '
             'is stored, or when the key configuration changes.',
    )
    x_chat_decrypt_stopped = fields.Boolean(
        string='X Chat Decryption Stopped',
        copy=False,
        readonly=True,
        tracking=True,
        groups='social.group_social_user',
        help='Set when the account stopped turning inbound encrypted chats '
             'into messages (the unread streak crossed the alert threshold), so '
             'its conversations silently receive nothing. Cleared by a stored '
             'message or a key reconfiguration.',
    )
    x_chat_status = fields.Selection(
        [
            ('not_configured', 'Not Configured'),
            ('ready', 'Ready'),
            ('pin_locked', 'PIN Rejected'),
            ('stopped', 'Decryption Stopped'),
        ],
        string='X Chat Status',
        compute='_compute_x_chat_status',
        groups='social.group_social_user',
        help='Where this account stands with X Chat encryption, in one value: '
             'not configured until its keys are registered, ready while '
             'encrypted messages are being read, PIN rejected when X refused '
             'the configured PIN, and decryption stopped when the account kept '
             'receiving encrypted chats it could no longer read.',
    )

    @api.depends('x_chat_initialized', 'x_chat_pin_locked',
                 'x_chat_decrypt_stopped')
    def _compute_x_chat_status(self):
        for account in self:
            if not account.x_chat_initialized:
                account.x_chat_status = 'not_configured'
            elif account.x_chat_decrypt_stopped:
                account.x_chat_status = 'stopped'
            elif account.x_chat_pin_locked:
                account.x_chat_status = 'pin_locked'
            else:
                account.x_chat_status = 'ready'
    x_chat_decrypt_notified_at = fields.Datetime(
        string='X Chat Decryption Alert Sent At',
        copy=False,
        readonly=True,
        groups='social.group_social_user',
        help='When the "decryption stopped" notice was last sent for this '
             'account.',
    )
    x_chat_key_fetch_failed_at = fields.Datetime(
        string='X Chat Key Fetch Failed At',
        copy=False,
        readonly=True,
        groups='social.group_social_user',
        help='When reading the account\'s registered Chat public keys last '
             'failed. The read is held back briefly after a failure instead of '
             're-hitting the endpoint on every batch, because that endpoint\'s '
             '24h budget is shared with key registration. Cleared by a '
             'successful read or a key reconfiguration.',
    )

    x_migration_status = fields.Selection(
        [
            ('pending', 'Pending'),
            ('migrated', 'Migrated'),
            ('failed', 'Failed'),
        ],
        string='X Migration Status',
        tracking=True,
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
    x_following_ids = fields.Many2many(
        'res.partner',
        relation='x_account_following_rel',
        column1='account_id',
        column2='partner_id',
        string='X Following',
        help='Partners (X users) that this account already follows.',
    )

    @api.depends('x_session_store_id')
    def _compute_x_group_count(self):
        group_model = self.env['discuss.channel'].sudo()
        for account in self:
            account.x_group_count = group_model.search_count([
                ('channel_type', '=', 'x_group'),
                ('x_account_id', '=', account.id),
            ])

    @api.depends_context('force_company')
    def _compute_x_provider(self):
        param = self.env['ir.config_parameter'].sudo().get_param
        default_provider = param('x_account.provider', 'session_web')
        for account in self:
            account.x_provider = default_provider

    def _inverse_x_provider(self):
        pass

    @api.depends_context('force_company')
    def _compute_x_event_provider(self):
        param = self.env['ir.config_parameter'].sudo().get_param
        default_provider = param('x_account.event_provider')
        for account in self:
            account.x_event_provider = default_provider

    def _inverse_x_event_provider(self):
        pass

    @api.depends_context('force_company')
    def _compute_x_action_provider(self):
        param = self.env['ir.config_parameter'].sudo().get_param
        default_provider = param('x_account.action_provider')
        for account in self:
            account.x_action_provider = default_provider

    def _inverse_x_action_provider(self):
        pass

    def _filter_x_accounts(self):
        return self.filtered(lambda a: a.media_type == 'twitter')

    def _resolve_provider_code(self, field_value, registry_map):
        self.ensure_one()
        code = field_value or self.x_provider
        return registry_map.get(code, code)

    def get_event_provider(self):
        self.ensure_one()
        registry_code = self._resolve_provider_code(
            self.x_event_provider, _EVENT_PROVIDER_REGISTRY_MAP)
        if registry_code == 'twitter':
            has_oauth2 = getattr(self, 'x_oauth2_access_token', False)
            has_oauth1 = getattr(self, 'twitter_oauth_token', False)
            if not (has_oauth2 or has_oauth1):
                raise UserError(
                    'Official X API credentials are not configured for this account. '
                    'Link the account via OAuth to enable event processing.')
        from odoo.addons.x_account.services.x_service import XService
        return XService.get_provider_by_code(self, registry_code)

    def get_action_provider(self):
        self.ensure_one()
        registry_code = self._resolve_provider_code(
            self.x_action_provider, _ACTION_PROVIDER_REGISTRY_MAP)
        if registry_code == 'getxapi':
            api_key = self.env['ir.config_parameter'].sudo().get_param(
                'x_account.getxapi_api_key')
            if not api_key:
                raise UserError(
                    'GetXAPI is not configured for this X account. '
                    'Set the GetXAPI API key in Settings > X Account.')
        from odoo.addons.x_account.services.x_service import XService
        return XService.get_provider_by_code(self, registry_code)

    def get_provider_for_operation(self, operation):
        self.ensure_one()
        if operation in _EVENT_OPERATIONS:
            return self.get_event_provider()
        if operation in _ACTION_OPERATIONS:
            return self.get_action_provider()
        from odoo.addons.x_account.services.x_service import XService
        return XService.get_provider(self)

    def _x_group_read_provider(self, method):
        """Provider used for XChat/group-conversation reads (``method``).

        XChat (``g``-prefixed) groups are only enumerated by the official X
        Chat API, which is the event provider's API family. GetXAPI's DM
        endpoints never return them, so routing group reads through the action
        provider silently yields zero groups. Prefer the event provider when it
        implements ``method``; otherwise fall back to the action provider (and
        to its own error when that provider is misconfigured) — the same
        event-then-action order used for conversation info and members.
        """
        self.ensure_one()
        try:
            event_provider = self.get_event_provider()
        except Exception:
            event_provider = None
        if getattr(event_provider, method, None):
            return event_provider
        return self.get_action_provider()

    def _x_action_blocked_reason(self):
        """Reason the account's paid action queue is halted, or False.

        Generic extension point: provider modules override it to suspend
        paid work when retrying cannot help (e.g. GetXAPI credit exhaustion).
        The task queue skips blocked accounts so no provider call is made
        until the condition is cleared.
        """
        return False

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
        provider = self._x_group_read_provider('fetch_groups')
        fetch = getattr(provider, 'fetch_groups', None)
        if not fetch:
            return self._groups_not_supported(
                'Fetch Groups',
                'Provider %s does not support fetching groups' % self.x_provider)
        try:
            result = fetch(self, limit=100)
        except Exception as exc:
            _logger.exception(
                'action_fetch_groups failed for account %s', self.id)
            if not self.env.context.get('dialog'):
                raise
            return self._display_notification(
                'Fetch Groups', 'Fetch failed: %s' % exc, kind='danger')
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
        provider = self._x_group_read_provider('fetch_group_messages')
        if getattr(provider, '_needs_encryption_code', True) and not self.x_encryption_code:
            raise ValueError(
                'Set the XChat Encryption Code on this account first — it is '
                'required to read encrypted group DMs.')
        fetch = getattr(provider, 'fetch_group_messages', None)
        if not fetch:
            return self._groups_not_supported(
                'Fetch Group Messages',
                'Provider %s does not support fetching group messages' % self.x_provider)
        try:
            result = fetch(self, limit=100)
        except Exception as exc:
            _logger.exception(
                'action_fetch_group_messages failed for account %s', self.id)
            if not self.env.context.get('dialog'):
                raise
            return self._display_notification(
                'Fetch Group Messages', 'Fetch failed: %s' % exc,
                kind='danger')
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

    def action_sync_chat_names(self):
        """Synchronize X chat channels (names + missing channel creation).

        Uses the action provider (e.g. GetXAPI) to list the FULL account DM
        inbox (pages walked via cursor) and update the ``name`` of the
        discuss channels that already exist; conversations that were never
        seen before get their channels created in the same run.
        """
        self.ensure_one()
        if not self._filter_x_accounts():
            raise ValueError('Chat name sync is only available on X accounts.')
        provider = self.get_action_provider()
        sync = getattr(provider, 'sync_chat_names', None)
        if not sync:
            return self._display_notification(
                'Sync Chat Names',
                'Provider %s does not support syncing chat names'
                % self.x_provider, kind='warning')
        try:
            result = sync(self, limit=200)
        except Exception as exc:
            _logger.exception(
                'action_sync_chat_names failed for account %s', self.id)
            return self._display_notification(
                'Sync Chat Names', 'Sync failed: %s' % exc, kind='danger')
        kind = 'success' if (result.get('created') or result.get('updated')
                             or result.get('unchanged')) else 'warning'
        message = 'Conversations: %s%s, updated: %s, unchanged: %s, no channel: %s' % (
            result.get('conversations', 0),
            ', created: %s' % result.get('created', 0)
            if result.get('created') else '',
            result.get('updated', 0),
            result.get('unchanged', 0),
            result.get('missing', 0))
        return self._display_notification(
            'Sync Chat Names', message, kind=kind)

    def _check_chat_key_setup_state(self, expect_initialized):
        """Refuse a Chat key registration that does not match the account state.

        The form used to hide the wrong half of the pair (Setup Chat Keys vs
        Reconfigure Encryption Key) behind an invisible condition. A bound
        server action cannot express that condition, and registering again
        writes a fresh identity to X and consumes one of the limited daily
        slots, so the state is checked here instead of trusted to the view.
        """
        self.ensure_one()
        if self.x_chat_initialized == expect_initialized:
            return
        if expect_initialized:
            raise UserError(_(
                'Chat keys are not set up yet. Use "Setup Chat Keys" to '
                'register the first identity.'))
        raise UserError(_(
            'Chat keys are already registered. Use "Reconfigure Encryption '
            'Key" to register a new identity.'))

    def action_initialize_x_chat_encryption(self):
        """Initialize the account's XChat encryption via its provider.

        Dispatches to the event provider's ``initialize_x_chat_encryption`` so the
        official-X (blob import / Juicebox unlock) and any other provider can
        implement it with their own key material. Marks ``x_chat_initialized``
        on success and clears it on failure. Returns a dialog/notification
        result.
        """
        self.ensure_one()
        if not self._filter_x_accounts():
            raise ValueError('X Chat encryption is only available on X accounts.')
        provider = self.get_event_provider()
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

    def action_register_x_chat_public_keys(self):
        """First-time X Chat key setup: generate keypairs, register the public
        key with X, and (PIN mode) back the keys up in X's secure backup.

        Dispatches to the event provider's ``register_x_chat_public_keys``.
        Public-key registration is a rate-limited, one-time write, so this is
        an explicit, user-confirmed action. Marks ``x_chat_initialized`` on
        success and clears it on failure. Returns a dialog/notification
        result.
        """
        self.ensure_one()
        if not self._filter_x_accounts():
            raise ValueError('X Chat encryption is only available on X accounts.')
        provider = self.get_event_provider()
        register = getattr(provider, 'register_x_chat_public_keys', None)
        if not register:
            return self._display_notification(
                'X Chat Encryption',
                'Provider %s does not support X Chat key setup'
                % self.x_provider, kind='warning')
        try:
            register(self)
        except Exception as exc:
            self.write({'x_chat_initialized': False})
            return self._display_notification(
                'X Chat Encryption',
                'Key setup failed: %s' % exc, kind='danger')
        self.write({'x_chat_initialized': True})
        mode = self.x_chat_key_mode or 'key_blob'
        source = 'X secure backup (PIN)' if mode == 'juicebox' else 'key blob'
        return self._display_notification(
            'X Chat Encryption',
            'Keys registered; backups enabled (%s).' % source, kind='success')

    def action_delete_x_subscriptions(self):
        """Delete X Activity API subscriptions for this account.

        Stub method to be overridden by provider-specific modules (e.g.
        x_account_twitter). Returns a notification result.
        """
        self.ensure_one()
        return self._display_notification(
            'Delete X Subscriptions',
            'Provider %s does not support subscription management'
            % self.x_provider, kind='warning')

    def action_resubscribe_x_subscriptions(self):
        """Re-create X Activity API subscriptions for this account.

        Stub method to be overridden by provider-specific modules (e.g.
        x_account_twitter). Returns a notification result.
        """
        self.ensure_one()
        return self._display_notification(
            'Resubscribe',
            'Provider %s does not support subscription management'
            % self.x_provider, kind='warning')

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
        """Clear the PIN-lock flag when the operator changes the PIN, and keep
        the aggregated connection status in sync with the detailed state.

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
        if _CHAT_KEY_FIELDS & set(vals):
            # Reconfiguring the key material is the operator's fix for an
            # account that stopped decrypting: clear the health counters so the
            # alert is armed again and fires if the new key still reads nothing.
            vals = dict(vals)
            vals.update({
                'x_chat_decrypt_fail_streak': 0,
                'x_chat_decrypt_stopped': False,
                'x_chat_decrypt_notified_at': False,
                'x_chat_key_fetch_failed_at': False,
            })
        if vals.get('active') in (False, 0):
            # Archiving an account turns its task queue off: cancel queued work
            # so nothing (like/repost/comment/bookmark/DM/webhook ...) runs for
            # it anymore. The claim path and the execution guard also refuse
            # archived accounts (race safety).
            self.env['x.account.task'].sudo().search([
                ('account_id', 'in', self.ids),
                ('status', 'in', ('pending', 'running')),
            ]).write({
                'status': 'cancelled',
                'error': 'Skipped: account archived',
            })
        res = super().write(vals)
        # The detailed state moved: refresh the aggregated status the form shows.
        # The resync writes x_connection_status only, which is not a trigger, so
        # this cannot recurse.
        if _X_STATUS_TRIGGER_FIELDS & set(vals) and 'x_connection_status' not in vals:
            self._sync_x_connection_status()
        return res

    def _x_overall_connection_status(self):
        """Aggregate the detailed connection state and the chat state.

        One value for the account form: a failing component wins over an
        incomplete setup, which wins over a healthy account.
        """
        self.ensure_one()
        if (self.x_connection_state in _X_CONNECTION_FAILED_STATES
                or self.x_chat_status in _X_CHAT_FAILED_STATES):
            return 'error'
        if self.x_connection_state == 'active' and self.x_chat_status == 'ready':
            return 'active'
        return 'not_configured'

    def _sync_x_connection_status(self):
        """Recompute the aggregated status and persist it when it changed.

        Called after any write that can move it, so the status bar and its
        chatter trail follow the detailed state without every caller having to
        know the aggregation rules.
        """
        for account in self:
            status = account._x_overall_connection_status()
            if account.x_connection_status != status:
                account.write({'x_connection_status': status})

    def _transition(self, status):
        self.write({'x_connection_state': status})

    def _set_last_error(self, message):
        self.write({'last_error': message})

    def _post_lifecycle_message(self, body):
        """Record a lifecycle note as a mail.message on the account.

        social.account is a plain model (not a mail.thread), so we create the
        mail.message record explicitly.
        """
        self.ensure_one()
        return self.env['mail.message'].sudo().create({
            'model': self._name,
            'res_id': self.id,
            'body': body,
            'message_type': 'comment',
            'subtype_id': self.env['ir.model.data']._xmlid_to_res_id('mail.mt_comment'),
        })

    def _x_notify_users(self):
        """Internal users who manage X accounts in this account's company.

        The recipients of every account lifecycle notice: reauthentication and
        stopped chat decryption are addressed to whoever can act on the account,
        in the company that owns it.
        """
        self.ensure_one()
        group = self.env.ref('social.group_social_user')
        return self.env['res.users'].sudo().search([
            ('group_ids', 'in', group.id),
            ('company_ids', 'in', self.company_id.id),
        ])

    def _notify_x_users(self, body, template_xmlid):
        """Post a lifecycle note, notify the account's users in-app and by mail.

        social.account is a plain model (not a mail.thread), so the mail.message
        and its inbox notifications are created explicitly: the bell badge in
        Odoo shows it immediately. Those same users are also emailed, so the
        notice reaches someone who is not currently logged in. Returns the
        recorded ``mail.message``.
        """
        self.ensure_one()
        message = self._post_lifecycle_message(body)
        users = self._x_notify_users()
        for user in users:
            if user.partner_id:
                self.env['mail.notification'].sudo().create({
                    'mail_message_id': message.id,
                    'res_partner_id': user.partner_id.id,
                    'author_id': user.partner_id.id,
                    'notification_type': 'inbox',
                    'notification_status': 'ready',
                })
        self._email_x_users(users, template_xmlid)
        return message

    def _email_x_users(self, users, template_xmlid):
        """Email the users who manage X accounts for this account's company.

        Only partners carrying an email address are targeted. These notices run
        from processing paths (token refresh, webhook processing), so a mail
        problem (no outgoing server, SMTP refused) is logged instead of raised:
        it must not roll back the state change that triggered it or surface as
        an RPC_ERROR.
        """
        self.ensure_one()
        partners = users.partner_id.filtered('email')
        if not partners:
            return
        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        if not template:
            return
        try:
            template.sudo().send_mail(
                self.id,
                force_send=True,
                email_values={'recipient_ids': [(6, 0, partners.ids)]},
            )
        except Exception as exc:
            _logger.warning(
                'x_account: could not email %s for account %s: %s',
                template_xmlid, self.id, exc)

    def _notify_reauth_required(self, message):
        """Push a system notification when the account needs reauthentication.

        Open the account and complete the OAuth 2.0 flow before its automation
        (webhooks, likes, reposts, DMs...) can run again.
        """
        self.ensure_one()
        body = (
            'This X account requires reauthentication. Open it and complete the '
            'OAuth 2.0 flow before its automation (webhooks, likes, reposts, '
            'DMs...) can run again.<br/><br/><pre>%s</pre>'
            % escape(message)
        )
        return self._notify_x_users(
            body, 'x_account.mail_template_x_account_reauth_required')

    def _record_chat_decrypt_outcome(self, stored=0, dropped=0):
        """Track this account's chat-decryption health and alert once.

        Called by the webhook processor once per batch with how many encrypted
        chat deliveries it turned into messages (``stored``) and how many it had
        to drop for lack of readable text (``dropped``). A stored message proves
        decryption works, so the streak restarts from this batch's drops;
        dropped deliveries only grow it. At
        ``_CHAT_DECRYPT_ALERT_THRESHOLD`` the account is flagged and its users
        are told — once per episode, re-armed when a message is stored again or
        the key material is reconfigured.
        """
        for account in self:
            streak = dropped + (
                0 if stored else account.x_chat_decrypt_fail_streak)
            vals = {'x_chat_decrypt_fail_streak': streak}
            if stored and account.x_chat_decrypt_stopped:
                vals.update({
                    'x_chat_decrypt_stopped': False,
                    'x_chat_decrypt_notified_at': False,
                })
            account.write(vals)
            if (not stored
                    and streak >= self._CHAT_DECRYPT_ALERT_THRESHOLD
                    and not account.x_chat_decrypt_stopped):
                account.write({
                    'x_chat_decrypt_stopped': True,
                    'x_chat_decrypt_notified_at': fields.Datetime.now(),
                })
                account._notify_chat_decrypt_stopped(streak)

    def _notify_chat_decrypt_stopped(self, dropped):
        """Tell the account's users its inbound chats stopped decrypting.

        The events keep arriving — the account just cannot read them, so its
        conversations silently stop growing. Flagged on the account next to the
        key source, with the same in-app + email notice the reauthentication
        flow uses.
        """
        self.ensure_one()
        mode = self.x_chat_key_mode or 'unset'
        source = dict(self._fields['x_chat_key_mode'].selection).get(mode, mode)
        body = (
            'This X account stopped decrypting incoming chats: %s consecutive '
            'encrypted deliveries could not be read, so no new message is '
            'being stored in its conversations. The events are arriving '
            'normally — the key material is the problem.'
            '<br/><br/>Key source: %s<br/>'
            'Re-run the encryption key setup on the account (or fix its PIN / '
            'imported key blob) to resume.'
            % (dropped, escape(source))
        )
        return self._notify_x_users(
            body, 'x_account.mail_template_x_account_decrypt_stopped')

    @api.constrains('x_auto_archive', 'x_auto_archive_start', 'x_auto_archive_end')
    def _check_auto_archive_window(self):
        """A flagged account must carry an explicit archive window.

        Floats cannot be NULL in the ORM (an unset one reads as 0.0), so the
        window counts as unset only when both bounds are zero.
        """
        for account in self.filtered('x_auto_archive'):
            if not account.x_auto_archive_start and not account.x_auto_archive_end:
                raise ValidationError(_(
                    'Set the archive window (Archive From / Archive Until) on '
                    '"%s" before enabling "Archive Daily".', account.display_name))

    @api.model
    def _cron_archive_flagged_accounts(self):
        """Archive accounts flagged "Archive Daily" inside their window.

        Runs from ir.cron every few minutes and archives each flagged account
        while the current time falls inside the window set on the account. The
        window is read on the wall clock of the account's company (falling back
        to the cron user's timezone, then UTC) — Odoo runs with TZ forced to
        UTC, so comparing against the process clock would make an account
        configured in local hours miss its window. A window whose end is before
        its start wraps past midnight (e.g. 23:00 -> 01:00); equal ends mean a
        single minute. Only X accounts (twitter media) that are still active
        and flagged ``x_auto_archive`` are archived; archiving prunes their X
        Activity API subscriptions and cancels their queued tasks. Accounts
        left flagged without a window (e.g. flagged before this field existed)
        are skipped, and each account is isolated so one failure cannot stop
        the rest.
        """
        now = fields.Datetime.now()
        flagged = self.sudo().search([
            ('media_type', '=', 'twitter'),
            ('active', '=', True),
            ('x_auto_archive', '=', True),
        ])
        archived = 0
        for account in flagged:
            if not account.x_auto_archive_start and not account.x_auto_archive_end:
                continue
            start = _to_minute_of_day(account.x_auto_archive_start)
            end = _to_minute_of_day(account.x_auto_archive_end)
            now_minute = _local_minute_of_day(
                now, account.company_id.partner_id.tz or self.env.user.tz)
            if not _in_daily_window(now_minute, start, end):
                continue
            try:
                account.write({'active': False})
                archived += 1
            except Exception:
                _logger.exception(
                    'Daily auto-archive failed for account %s', account.id)
        return archived

    @api.model
    def _cron_validate_x_sessions(self):
        """Sweep X accounts (media_type twitter) and validate sessions, isolating
        per-account failures."""
        accounts = self.sudo().search([
            ('media_type', '=', 'twitter'),
            ('active', '=', True),
            ('x_session_store_id', '!=', False),
            ('x_connection_state', 'in', ('active', 'reauth_required', 'error')),
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
