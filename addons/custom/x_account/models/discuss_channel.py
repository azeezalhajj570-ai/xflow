# Part of Odoo. See LICENSE file for full copyright and licensing details.

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
        ondelete='set null',
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
        self = self.sudo()
        if not x_account:
            raise ValueError('x_account is required to resolve an X channel')
        domain = [('channel_type', '=', channel_type), ('x_account_id', '=', x_account.id)]
        if conversation_id:
            domain.append(('x_conversation_id', '=', conversation_id))
        elif partner:
            domain.append(('x_partner_id', '=', partner.id))
        channel = self.search(domain, limit=1)
        if not channel and create_if_not_found:
            member_ids = member_ids or ([partner.id] if partner else []) + [
                self.env.user.partner_id.id
            ]
            # Use try-except with flush for race conditions
            try:
                channel = self.create({
                    'channel_type': channel_type,
                    'x_account_id': x_account.id,
                    'x_partner_id': partner.id if partner else False,
                    'x_conversation_id': conversation_id,
                    'name': conversation_id or getattr(partner, 'name', False) or 'X Conversation',
                })
                self.env.cr.flush()
            except Exception:
                self.env.cr.rollback()
                # Race condition: another thread created it first. Search again.
                channel = self.search(domain, limit=1)
                if not channel:
                    # Re-raise if still not found (different error)
                    raise
            # Add members after creation (mail's discuss.channel.create() does
            # not accept Command.create on channel_member_ids). The creator is
            # auto-added by mail, so only add members not already present.
            if channel:
                try:
                    existing = set(channel.channel_member_ids.partner_id.ids)
                    self.env['discuss.channel.member'].sudo().create([
                        {'channel_id': channel.id, 'partner_id': pid}
                        for pid in dict.fromkeys(member_ids)
                        if pid and pid not in existing
                    ])
                    self.env.cr.flush()
                except Exception:
                    self.env.cr.rollback()
                    # Channel was deleted by another thread, ignore
                    pass
        return channel

    def _save_x_message(self, direction, external_id, body, external_created_at,
                        author_partner=None, **kw):
        self.ensure_one()
        if not body:
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
        from odoo.addons.x_account.services.x_service import XService
        provider = XService.get_provider(account)
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


