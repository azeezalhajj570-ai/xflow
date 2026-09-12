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
        # A body-less event carries nothing to store: the ``encrypted`` marker
        # rows were never rendered (every caller passes ``no_mail``), never
        # matched by the DM automations (they filter on ``body_plain != False``)
        # and had no UI field, so they were pure noise. The event/task layer
        # filters these out before they reach here; this guard keeps every
        # provider honest.
        if not (body or '').strip():
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
            if self._save_x_message(
                direction='outbound' if msg.get('from_me') else 'inbound',
                external_id=msg['id'],
                body=msg.get('text', ''),
                external_created_at=msg.get('created_at'),
                author_partner=author_partner,
                author_x_id=sender_id,
            ):
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
        data = self._fetch_group_info_single()
        if not data.get('ok'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fetch Group Info',
                    'message': data['message'],
                    'type': data['type'],
                    'sticky': True,
                },
            }
        if data['updated']:
            message = 'Name updated to "%s".' % data['name']
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

    def _fetch_group_info_single(self):
        """Pull one X conversation's raw info and update its name if needed.

        ``self`` must be a single X conversation that already carries a
        linked account and a conversation id (the caller validates this).
        Returns a dict:

        - success: ``{'ok': True, 'name': str|None, 'updated': bool}``
        - failure: ``{'ok': False, 'type': 'warning'|'danger',
          'message': str}``
        """
        self.ensure_one()
        account = self.x_account_id
        provider = account.get_event_provider()
        fetch = getattr(provider, 'get_group_info', None)
        if not fetch:
            provider = account.get_action_provider()
            fetch = getattr(provider, 'get_group_info', None)
        if not fetch:
            return {
                'ok': False,
                'type': 'warning',
                'message': 'Provider %s does not support fetching '
                           'conversation info.' % account.x_provider,
            }
        try:
            result = fetch(account, self.x_conversation_id)
        except Exception as exc:
            _logger.exception('action_fetch_group_info failed: %s', exc)
            return {
                'ok': False,
                'type': 'danger',
                'message': 'Failed to fetch conversation info: %s' % exc,
            }
        if not result or not result.get('conversation_id'):
            return {
                'ok': False,
                'type': 'warning',
                'message': 'Conversation %s not found or no longer '
                           'accessible.' % (self.x_conversation_id or ''),
            }
        if result.get('undecrypted'):
            reason = result.get('undecrypted_reason')
            detail = result.get('undecrypted_detail') or ''
            if reason == 'no_key':
                return {
                    'ok': False,
                    'type': 'warning',
                    'message': 'Could not decrypt this group\'s name. Make sure '
                               'the X Chat encryption code is set on the '
                               'account and try again.',
                }
            if reason == 'rate_limited':
                # Surface X's own numbers: the scan for the group-name key is
                # throttled, which has nothing to do with the encryption code.
                return {
                    'ok': False,
                    'type': 'warning',
                    'message': 'X rate limit reached while looking up this '
                               'group\'s name key: %s. Wait for the limit to '
                               'reset and try again.' % (detail or 'HTTP 429'),
                }
            return {
                'ok': False,
                'type': 'warning',
                'message': 'Could not decrypt this group\'s name: %s'
                           % (detail or 'no conversation key found'),
            }
        name = result.get('name')
        updated = bool(name) and self.name != name
        if updated:
            self.write({'name': name})
        return {'ok': True, 'name': name, 'updated': updated}

    def action_fetch_group_info_bulk(self):
        """Fetch group info for several X conversations at once.

        Server-action entry point for the Chat list view: iterates the
        selected records, skips the ones that cannot host a conversation
        lookup (non-X chats, missing account or conversation id), reuses the
        single-conversation logic for the rest, and returns one aggregated
        notification while continuing on any per-conversation failure.
        """
        records = self.filtered(
            lambda ch: ch.channel_type in ('x', 'x_group')
            and ch.x_account_id and ch.x_conversation_id)
        skipped = len(self) - len(records)
        processed = updated = 0
        failures = []
        for channel in records:
            data = channel._fetch_group_info_single()
            if not data.get('ok'):
                failures.append((channel.name or str(channel.id),
                                 data['message']))
                continue
            processed += 1
            if data.get('updated'):
                updated += 1
        parts = []
        if processed:
            parts.append('%s chat(s)' % processed)
            parts.append('%s name(s) updated' % updated
                         if updated else 'no name changes')
        if skipped:
            parts.append('%s skipped' % skipped)
        if failures:
            parts.append('%s failed' % len(failures))
        if not processed and not failures:
            message = 'No X conversations selected.'
        else:
            message = ', '.join(parts) if parts else 'No X conversations selected.'
        if failures:
            preview = '; '.join('%s: %s' % (name, error)
                                for name, error in failures[:5])
            if len(failures) > 5:
                preview += '; and %s more' % (len(failures) - 5)
            message += ' | ' + preview
        if processed == 0:
            ntype = 'danger' if failures else 'info'
        elif failures:
            ntype = 'warning'
        else:
            ntype = 'success'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Fetch Group Info',
                'message': message,
                'type': ntype,
                'sticky': True,
            },
        }

    def action_fetch_group_members(self):
        """Fetch this conversation's members and their usernames from X.

        Uses the same provider lookup and endpoint as
        :meth:`action_fetch_group_info` (the official X Chat API is the only
        one that returns group member ids and usernames); falls back to the
        action provider when the event provider does not implement the lookup.

        Upserts the members as ``res.partner`` records (``x_user_id`` +
        ``x_username``) and ensures each one is a member of the channel.
        """
        self.ensure_one()
        if self.channel_type not in ('x', 'x_group'):
            raise ValueError(
                'Fetch group members is only available on X conversations.')
        account = self.x_account_id
        if not account:
            raise ValueError('This conversation has no linked X account.')
        if not self.x_conversation_id:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fetch Group Members',
                    'message': 'This conversation has no conversation id to look up.',
                    'type': 'warning',
                    'sticky': True,
                },
            }
        data = self._fetch_group_members_single()
        if not data.get('ok'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fetch Group Members',
                    'message': data['message'],
                    'type': data['type'],
                    'sticky': True,
                },
            }
        member_ids = data['member_ids']
        by_uid = dict(zip([str(uid) for uid in member_ids],
                          data['member_names']))
        usernames = [by_uid.get(str(uid)) or str(uid) for uid in member_ids]
        preview = ', '.join('@%s' % u for u in usernames[:10])
        if len(usernames) > 10:
            preview += ' and %s more' % (len(usernames) - 10)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Fetch Group Members',
                'message': 'Fetched %s member(s) (%s new, %s updated): %s'
                           % (len(usernames), data['created'],
                              data['updated'], preview),
                'type': 'success',
                'sticky': False,
            },
        }

    def _fetch_group_members_single(self):
        """Pull one X conversation's members and persist them in Odoo.

        ``self`` must be a single X conversation that already carries a
        linked account and a conversation id (the caller validates this).
        Returns a dict:

        - success: ``{'ok': True, 'member_ids': [...], 'member_names': [...],
          'created': n, 'updated': n}``
        - failure: ``{'ok': False, 'type': 'warning'|'danger',
          'message': str}``
        """
        self.ensure_one()
        account = self.x_account_id
        provider = account.get_event_provider()
        fetch = getattr(provider, 'get_group_info', None)
        if not fetch:
            provider = account.get_action_provider()
            fetch = getattr(provider, 'get_group_info', None)
        if not fetch:
            return {
                'ok': False,
                'type': 'warning',
                'message': 'Provider %s does not support fetching '
                           'conversation members.' % account.x_provider,
            }
        try:
            result = fetch(account, self.x_conversation_id)
        except Exception as exc:
            _logger.exception('action_fetch_group_members failed: %s', exc)
            return {
                'ok': False,
                'type': 'danger',
                'message': 'Failed to fetch conversation members: %s' % exc,
            }
        if not result or not result.get('conversation_id'):
            return {
                'ok': False,
                'type': 'warning',
                'message': 'Conversation %s not found or no longer '
                           'accessible.' % (self.x_conversation_id or ''),
            }
        member_ids = result.get('member_ids') or []
        member_names = result.get('member_names') or []
        by_uid = dict(zip([str(uid) for uid in member_ids], member_names))
        partner_model = self.env['res.partner'].sudo()
        member_model = self.env['discuss.channel.member'].sudo()
        created = updated = 0
        partners = partner_model.browse()
        for x_uid in member_ids:
            partner = partner_model.search(
                [('x_user_id', '=', str(x_uid))], limit=1)
            username = by_uid.get(str(x_uid)) or ''
            name = username or str(x_uid)
            if not partner:
                partner = partner_model.create({
                    'name': name,
                    'x_user_id': str(x_uid),
                    'x_username': username,
                })
                created += 1
            elif partner.x_username != username:
                partner.write({'x_username': username})
                updated += 1
            partners |= partner
        existing = member_model.search([
            ('channel_id', '=', self.id),
            ('partner_id', 'in', partners.ids),
        ]).mapped('partner_id')
        member_model.create([
            {'channel_id': self.id, 'partner_id': pid}
            for pid in (partners - existing).ids
        ])
        return {
            'ok': True,
            'member_ids': member_ids,
            'member_names': member_names,
            'created': created,
            'updated': updated,
        }

    def action_fetch_group_members_bulk(self):
        """Fetch group members for several X conversations at once.

        Server-action entry point for the Chat list view: iterates the
        selected records, skips the ones that cannot host a conversation
        lookup (non-X chats, missing account or conversation id), reuses the
        single-conversation logic for the rest, and returns one aggregated
        notification while continuing on any per-conversation failure.
        """
        records = self.filtered(
            lambda ch: ch.channel_type in ('x', 'x_group')
            and ch.x_account_id and ch.x_conversation_id)
        skipped = len(self) - len(records)
        processed = member_count = created = updated = 0
        failures = []
        for channel in records:
            data = channel._fetch_group_members_single()
            if not data.get('ok'):
                failures.append((channel.name or str(channel.id),
                                 data['message']))
                continue
            processed += 1
            member_count += len(data['member_ids'])
            created += data['created']
            updated += data['updated']
        parts = []
        if processed:
            parts.append('%s chat(s)' % processed)
            parts.append('%s member(s) (%s new, %s refreshed)'
                         % (member_count, created, updated))
        if skipped:
            parts.append('%s skipped' % skipped)
        if failures:
            parts.append('%s failed' % len(failures))
        if not processed and not failures:
            message = 'No X conversations selected.'
        else:
            message = ', '.join(parts) if parts else 'No X conversations selected.'
        if failures:
            preview = '; '.join('%s: %s' % (name, error)
                                for name, error in failures[:5])
            if len(failures) > 5:
                preview += '; and %s more' % (len(failures) - 5)
            message += ' | ' + preview
        if processed == 0:
            ntype = 'danger' if failures else 'info'
        elif failures:
            ntype = 'warning'
        else:
            ntype = 'success'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Fetch Group Members',
                'message': message,
                'type': ntype,
                'sticky': True,
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

    def _x_is_group_conversation(self):
        """Whether this X channel is a real group conversation.

        1:1 conversations pair two user ids — GetXAPI uses ``<id>:<id>`` and
        the official API ``<id>-<id>`` — and can be stored as ``x_group`` when
        created from a GetXAPI conversation id. Group ids are ``g``-prefixed or
        a single numeric id, so a numeric pair identifies a 1:1 conversation.
        """
        self.ensure_one()
        if self.channel_type != 'x_group':
            return False
        conversation_id = self.x_conversation_id or ''
        for separator in (':', '-'):
            if separator not in conversation_id:
                continue
            parts = [p for p in conversation_id.split(separator) if p]
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                return False
        return True

    def _x_dm_recipient_user_id(self):
        """Recipient X user id for a 1:1 conversation, or '' when unknown.

        Prefers the linked partner, then falls back to a ``<id>:<id>`` /
        ``<id>-<id>`` conversation id: the half that is not the account's own
        user id. When the account id is unknown, only a conversation id with a
        single distinct participant (a self conversation) is accepted.
        """
        self.ensure_one()
        if self.x_partner_id and self.x_partner_id.x_user_id:
            return self.x_partner_id.x_user_id
        conversation_id = self.x_conversation_id or ''
        own_id = str(self.x_account_id.twitter_user_id or '')
        for separator in (':', '-'):
            if separator not in conversation_id:
                continue
            parts = [p for p in conversation_id.split(separator) if p]
            if own_id:
                others = [p for p in parts if p != own_id]
                if len(others) == 1:
                    return others[0]
            unique = list(dict.fromkeys(parts))
            if len(unique) == 1:
                return unique[0]
            return ''
        return ''

    def _enqueue_send_dm(self, text=None):
        """Enqueue an X direct-message task for this conversation (user or group).

        Routes by conversation shape: real group conversations send via the
        event provider (``send_group_dm``, the official X API is the only one
        that can write into an existing group conversation); everything else is
        a 1:1 conversation and sends via the action provider (``send_dm``,
        GetXAPI), even when it was stored as ``x_group`` from a
        ``<user_id>:<user_id>`` GetXAPI conversation id. Only enqueues an
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
        if self._x_is_group_conversation():
            task_ctx = {
                'conversation_id': conversation_id,
                'text': text,
            }
            operation = 'send_group_dm'
        else:
            recipient_id = self._x_dm_recipient_user_id()
            if not recipient_id:
                raise ValueError(
                    'Cannot resolve the recipient X user id for channel id=%s'
                    % self.id)
            task_ctx = {
                'recipient_id': recipient_id,
                'text': text,
            }
            operation = 'send_dm'
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

    def action_send_message(self):
        """Open the Send Message wizard for this X conversation.

        The wizard sends a message directly (synchronously) to the 1:1 user
        conversation (``x``) or the group conversation (``x_group``).
        """
        self.ensure_one()
        if self.channel_type not in ('x', 'x_group'):
            raise ValueError(
                'Send Message is only available on X conversations, got %r'
                % self.channel_type)
        return {
            'name': 'Send Message',
            'type': 'ir.actions.act_window',
            'res_model': 'x.message.composer',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_channel_id': self.id,
                'active_model': 'discuss.channel',
                'active_id': self.id,
            },
        }

    def action_bulk_follow(self):
        """Open the bulk-follow wizard for this X conversation's members."""
        self.ensure_one()
        if self.channel_type not in ('x', 'x_group'):
            raise ValueError(
                'Bulk follow is only available on X conversations, got %r'
                % self.channel_type)
        return {
            'name': 'Follow Members',
            'type': 'ir.actions.act_window',
            'res_model': 'x.follow.composer',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_channel_id': self.id,
                'active_model': 'discuss.channel',
                'active_id': self.id,
            },
        }


