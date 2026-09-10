# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    channel_type = fields.Selection(
        selection_add=[
            ('x', 'X Conversation'),
            ('x_group', 'X Group Conversation'),
        ],
        ondelete={'x': 'cascade', 'x_group': 'cascade'},
    )
    x_account_id = fields.Many2one(
        'social.account',
        string='X Account',
        index=True,
        ondelete='cascade',
    )
    x_partner_id = fields.Many2one(
        'res.partner',
        string='X Partner',
        index='btree_not_null',
        ondelete='set null',
    )
    x_conversation_id = fields.Char(
        string='X Conversation ID',
        index=True,
        help='External X conversation id.',
    )
    last_x_mail_message_id = fields.Many2one(
        'mail.message',
        string='Last X Mail Message',
        index='btree_not_null',
    )
    x_sync_status = fields.Selection(
        [
            ('ok', 'Synchronized'),
            ('partial', 'Partially Synchronized'),
            ('encrypted', 'Messages Encrypted'),
            ('failed', 'Synchronization Failed'),
        ],
        string='X Sync Status',
        help='Message synchronization state for this X conversation.',
    )
    x_group_member_ids = fields.Many2many(
        'res.partner',
        string='X Group Members',
        compute='_compute_x_group_members',
        help='Member partners of this X group channel.',
    )
    x_group_member_count = fields.Integer(
        string='X Group Member Count',
        compute='_compute_x_group_members',
    )
    x_company_id = fields.Many2one(
        'res.company',
        string='X Company',
        related='x_account_id.company_id',
        store=True,
        index=True,
    )

    @api.depends('channel_member_ids', 'channel_member_ids.partner_id')
    def _compute_x_group_members(self):
        for channel in self:
            partners = channel.channel_member_ids.partner_id
            channel.x_group_member_ids = partners
            channel.x_group_member_count = len(partners)

    _x_conversation_uniq = models.Constraint(
        'UNIQUE(x_account_id, x_conversation_id)',
        'An X conversation id must be unique per account.',
    )

    @api.model
    def _get_x_channel(self, x_account, partner=None, conversation_id=None,
                       channel_type='x', create_if_not_found=False,
                       member_ids=None):
        channel = self._find_x_channel(x_account, partner, conversation_id, channel_type)
        if channel or not create_if_not_found:
            return channel
        self = self.sudo()
        member_ids = member_ids or ([partner.id] if partner else []) + [
            self.env.user.partner_id.id
        ]
        if x_account.create_uid and x_account.create_uid.partner_id:
            member_ids.append(x_account.create_uid.partner_id.id)
        try:
            # Savepoint (not rollback): a concurrent worker may create the
            # same channel first; only this insert is undone and the
            # transaction keeps its earlier work (task claims, event states).
            with self.env.cr.savepoint():
                channel = self.create({
                    'channel_type': channel_type,
                    'x_account_id': x_account.id,
                    'x_partner_id': partner.id if partner else False,
                    'x_conversation_id': conversation_id,
                    'name': conversation_id or getattr(partner, 'name', False) or 'X Conversation',
                })
        except Exception:
            channel = self._find_x_channel(x_account, partner, conversation_id, channel_type)
            if not channel:
                raise
        if channel:
            try:
                with self.env.cr.savepoint():
                    existing = set(channel.channel_member_ids.partner_id.ids)
                    self.env['discuss.channel.member'].sudo().create([
                        {'channel_id': channel.id, 'partner_id': pid}
                        for pid in dict.fromkeys(member_ids)
                        if pid and pid not in existing
                    ])
            except Exception:
                pass
        return channel

    @api.model
    def _find_x_channel(self, x_account, partner=None, conversation_id=None,
                        channel_type='x'):
        # No ormcache here: this method returns a recordset, and a cached
        # recordset stays bound to the cursor that produced it — once that
        # cursor closes, any later use raises "Cursor already closed". A
        # fresh search per call is cheap and always uses the live cursor.
        self = self.sudo()
        if not x_account:
            raise ValueError('x_account is required to resolve an X channel')
        domain = [('channel_type', '=', channel_type), ('x_account_id', '=', x_account.id)]
        if conversation_id:
            domain.append(('x_conversation_id', '=', conversation_id))
        elif partner:
            domain.append(('x_partner_id', '=', partner.id))
        return self.search(domain, limit=1)

    def _save_x_message(self, direction, external_id, body, external_created_at,
                        author_partner=None, **kw):
        self.ensure_one()
        # An empty body is dropped for regular messages (legacy DM payloads are
        # often nothing but escaping artifacts), but encrypted XChat events must
        # still be recorded with the ``encrypted`` marker so undecryptable
        # messages remain visible instead of silently disappearing.
        if not body and not kw.get('encrypted'):
            return self.env['x.message']
        # OmniX delivers timestamps in several shapes: ISO-8601 strings
        # ("2026-08-31T12:00:00Z") or Unix epoch milliseconds (ints). Odoo
        # Datetime fields want "%Y-%m-%d %H:%M:%S". Normalize when needed.
        if external_created_at:
            if isinstance(external_created_at, str):
                ts = external_created_at.strip()
                if ts.isdigit():
                    external_created_at = int(ts)
                elif 'T' in ts or ts.endswith('Z'):
                    ts = ts.replace('T', ' ').replace('Z', '')
                    if '.' in ts:
                        ts = ts.split('.')[0]
                    external_created_at = ts
            if isinstance(external_created_at, (int, float)):
                from datetime import datetime
                external_created_at = fields.Datetime.to_string(
                    datetime.fromtimestamp(external_created_at / 1000))
            if isinstance(external_created_at, str) and not external_created_at.strip():
                external_created_at = False
        existing = self.env['x.message'].sudo().search([
            ('channel_id', '=', self.id),
            ('external_id', '=', external_id),
        ], limit=1)
        if existing:
            return existing
        vals = {
            'channel_id': self.id,
            'account_id': self.x_account_id.id,
            'direction': direction,
            'external_id': external_id,
            'body_plain': body,
            'external_created_at': external_created_at,
            'author_partner_id': author_partner.id if author_partner else False,
            'author_x_id': kw.get('author_x_id'),
            'author_x_username': kw.get('author_x_username'),
            'encrypted': kw.get('encrypted', False),
            'acked': kw.get('acked', False),
            'delivered': kw.get('delivered', False),
            'participant_joined': kw.get('participant_joined', False),
            'participant_left': kw.get('participant_left', False),
        }
        xm = self.env['x.message'].sudo().create(vals)
        if not kw.get('no_mail'):
            msg = self.message_post(
                body=body or '',
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
            xm.write({'mail_message_id': msg.id})
            self.write({'last_x_mail_message_id': msg.id})
        return xm

    def action_fetch_group_messages(self, limit=100):
        """Fetch this group channel's messages via the owning account's
        provider and store them as x.message records in this channel."""
        self.ensure_one()
        if self.channel_type != 'x_group':
            raise ValueError('Fetch group messages is only available on X groups.')
        account = self.x_account_id
        if not account:
            raise ValueError('This group has no linked X account.')
        provider = account.get_action_provider()
        if getattr(provider, '_needs_encryption_code', True) and not account.x_encryption_code:
            raise ValueError(
                'Set the XChat Encryption Code on the account first — it is '
                'required to read encrypted group DMs.')
        get_dms = getattr(provider, 'get_dms', None)
        if not get_dms:
            raise NotImplementedError(
                'Provider %s does not support fetching messages' % account.x_provider)
        conv_id = self.x_conversation_id
        if not conv_id:
            raise ValueError('This group has no conversation id.')
        try:
            result = get_dms(conv_id, limit=int(limit))
        except Exception as exc:
            # A dead/revoked X OAuth 2.0 credential surfaces as an auth failure
            # that would otherwise escape as a raw RPC_ERROR. Show the user a
            # meaningful message instead, and let the account's reauthentication
            # state (already recorded by the provider) commit. Guarded by
            # try/except because x_account does not hard-depend on
            # x_account_twitter, so the import may legitimately be unavailable.
            try:
                from odoo.addons.x_account_twitter.services import twitter_errors
            except Exception:
                raise
            if isinstance(exc, twitter_errors.TwitterError):
                message = (
                    'This X account needs reauthentication — %s. Re-link the '
                    'account from Social Marketing to refresh its credentials.'
                    % exc)
                _logger.exception(
                    'action_fetch_group_messages: auth failure fetching %s',
                    conv_id)
                if self.env.context.get('dialog'):
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': 'Fetch Group Messages',
                            'message': message,
                            'type': 'warning',
                            'sticky': True,
                        },
                    }
            raise
        count = 0
        for msg in result['messages']:
            author_partner = False
            sender_id = msg.get('sender_id')
            if sender_id:
                author_partner = self.env['res.partner'].sudo().search(
                    [('x_user_id', '=', str(sender_id))], limit=1)
            self._save_x_message(
                direction='outbound' if msg.get('from_me') else 'inbound',
                external_id=msg['id'],
                body=msg.get('text', ''),
                external_created_at=msg.get('created_at'),
                author_partner=author_partner,
                author_x_id=sender_id,
            )
            count += 1
        if self.env.context.get('dialog'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fetch Group Messages',
                    'message': 'Stored %s message(s) in this group.' % count,
                    'type': 'success',
                    'sticky': False,
                },
            }
        return {'messages': count}

    def action_fetch_group_info(self):
        """Fetch this conversation's info from X (official Chat API) and update
        the channel name.

        Uses the account's event provider (TwitterProvider/OAuth2) because only
        X's own Chat API can read XChat ``g...`` groups; GetXAPI/SessionWeb
        providers cannot see them. Falls back to the action provider when the
        event provider does not implement the lookup.
        """
        self.ensure_one()
        if self.channel_type not in ('x', 'x_group'):
            raise ValueError('Fetch group info is only available on X conversations.')
        account = self.x_account_id
        if not account:
            raise ValueError('This conversation has no linked X account.')
        if not self.x_conversation_id:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fetch Group Info',
                    'message': 'This conversation has no conversation id to look up.',
                    'type': 'warning',
                    'sticky': True,
                },
            }
        provider = account.get_event_provider()
        fetch = getattr(provider, 'get_group_info', None)
        if not fetch:
            provider = account.get_action_provider()
            fetch = getattr(provider, 'get_group_info', None)
        if not fetch:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fetch Group Info',
                    'message': 'Provider %s does not support fetching '
                               'conversation info.' % account.x_provider,
                    'type': 'warning',
                    'sticky': True,
                },
            }
        try:
            result = fetch(account, self.x_conversation_id)
        except Exception as exc:
            _logger.exception('action_fetch_group_info failed: %s', exc)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fetch Group Info',
                    'message': 'Failed to fetch conversation info: %s' % exc,
                    'type': 'danger',
                    'sticky': True,
                },
            }
        if not result or not result.get('conversation_id'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fetch Group Info',
                    'message': 'Conversation %s not found or no longer '
                               'accessible.' % (self.x_conversation_id or ''),
                    'type': 'warning',
                    'sticky': True,
                },
            }
        if result.get('undecrypted'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fetch Group Info',
                    'message': 'Could not decrypt this group\'s name. Make sure '
                               'the X Chat encryption code is set on the '
                               'account and try again.',
                    'type': 'warning',
                    'sticky': True,
                },
            }
        if result.get('name') and self.name != result['name']:
            self.write({'name': result['name']})
            message = 'Name updated to "%s".' % result['name']
        else:
            message = 'The conversation name is already current.'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Fetch Group Info',
                'message': message,
                'type': 'success',
                'sticky': False,
            },
        }

    @api.model
    def _handle_x_inbound_event(self, event):
        """Route a generic inbound X event into an x.message + discuss channel."""
        self = self.sudo()
        account_id = event.get('account_id')
        conversation_id = event.get('conversation_id')
        if not account_id or not conversation_id:
            return False
        account = self.env['social.account'].browse(account_id)
        channel = self._get_x_channel(
            account,
            conversation_id=conversation_id,
            channel_type='x_group' if event.get('group') else 'x',
            create_if_not_found=True,
        )
        author_x_id = event.get('author_x_id')
        author_partner = False
        if author_x_id:
            author_partner = self.env['res.partner'].sudo().search(
                [('x_user_id', '=', author_x_id)], limit=1)
            if not author_partner:
                author_partner = self.env['res.partner'].sudo().create({
                    'name': event.get('author_name') or author_x_id,
                    'x_user_id': author_x_id,
                    'x_username': event.get('author_x_username'),
                })
        return channel._save_x_message(
            direction='inbound',
            external_id=event.get('message_id'),
            body=event.get('text'),
            external_created_at=event.get('external_created_at'),
            author_partner=author_partner,
            author_x_id=author_x_id,
            author_x_username=event.get('author_x_username'),
        )

    def _enqueue_send_dm(self, text=None):
        """Enqueue an X direct-message task for this conversation (user or group).

        Routes by channel_type: 1:1 ``x`` conversations send via the action
        provider (``send_dm``), ``x_group`` conversations via the event
        provider (``send_group_dm``, the official X API is the only one that
        can write into an existing group conversation). Only enqueues an
        ``x.account.task`` — the task auto-execution rule or the queue worker
        performs the X HTTP call.
        """
        self.ensure_one()
        if self.channel_type not in ('x', 'x_group'):
            raise ValueError(
                'Send DM is only available on X conversations, got %r'
                % self.channel_type)
        text = (text or '').strip()
        if not text:
            text = ('Thanks for your message!' if self.channel_type == 'x'
                    else 'Thanks for the update in our group!')
        account = self.x_account_id
        if not account or not account.active or account.x_connection_status == 'disabled':
            raise ValueError(
                'No valid X account for channel id=%s (account_id=%s)'
                % (self.id, self.x_account_id.id if self.x_account_id else None))
        conversation_id = self.x_conversation_id
        if not conversation_id:
            raise ValueError(
                'This X conversation has no conversation id to send to.')
        if self.channel_type == 'x':
            recipient_id = self.x_partner_id.x_user_id
            if not recipient_id:
                raise ValueError(
                    'Cannot resolve the recipient X user id for channel id=%s'
                    % self.id)
            task_ctx = {
                'recipient_id': recipient_id,
                'text': text,
            }
            operation = 'send_dm'
        else:
            task_ctx = {
                'conversation_id': conversation_id,
                'text': text,
            }
            operation = 'send_group_dm'
        task = self.env['x.account.task'].sudo().create({
            'account_id': account.id,
            'operation': operation,
            'priority': 1,
            'task_context': json.dumps(task_ctx),
        })
        _logger.info(
            'Enqueued DM task id=%s (operation=%s) for channel id=%s, '
            'conversation_id=%s, account_id=%s',
            task.id, operation, self.id, conversation_id, account.id)
        return task


