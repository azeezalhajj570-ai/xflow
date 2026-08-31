# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


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

    @api.depends('channel_member_ids', 'channel_member_ids.partner_id')
    def _compute_x_group_members(self):
        for channel in self:
            partners = channel.channel_member_ids.partner_id
            channel.x_group_member_ids = partners
            channel.x_group_member_count = len(partners)

    _sql_constraints = [
        (
            'x_conversation_uniq',
            'UNIQUE(x_account_id, x_conversation_id)',
            'An X conversation id must be unique per account.',
        ),
    ]

    @api.model
    def _get_x_channel(self, x_account, partner=None, conversation_id=None,
                       channel_type='x', create_if_not_found=False,
                       member_ids=None):
        self = self.sudo()
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
            channel = self.create({
                'channel_type': channel_type,
                'x_account_id': x_account.id,
                'x_partner_id': partner.id if partner else False,
                'x_conversation_id': conversation_id,
                'name': conversation_id or getattr(partner, 'name', False) or 'X Conversation',
            })
            # Add members after creation (mail's discuss.channel.create() does
            # not accept Command.create on channel_member_ids).
            self.env['discuss.channel.member'].sudo().create([
                {'channel_id': channel.id, 'partner_id': pid}
                for pid in member_ids if pid
            ])
        return channel

    def _save_x_message(self, direction, external_id, body, external_created_at,
                        author_partner=None, **kw):
        self.ensure_one()
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

    @api.model
    def _handle_x_webhook_event(self, account, event):
        """Route an OmniX webhook event (message.*, tweet.*, user.follow) into
        x.message + discuss channels for the owning account."""
        self = self.sudo()
        if not account or not event:
            return False
        etype = event.get('type') or event.get('event') or ''

        # Direct-message events: message.received / message.sent / edited / deleted
        if etype.startswith('message.'):
            conversation_id = event.get('conversation_id')
            if not conversation_id:
                return False
            channel = self._get_x_channel(
                account,
                conversation_id=conversation_id,
                channel_type='x_group' if str(conversation_id).startswith('g') else 'x',
                create_if_not_found=True,
            )
            author_x_id = event.get('sender_id')
            author_partner = False
            if author_x_id:
                author_partner = self.env['res.partner'].sudo().search(
                    [('x_user_id', '=', str(author_x_id))], limit=1)
                if not author_partner:
                    author_partner = self.env['res.partner'].sudo().create({
                        'name': author_x_id,
                        'x_user_id': str(author_x_id),
                    })
            if etype in ('message.received', 'message.sent'):
                return channel._save_x_message(
                    direction='inbound' if etype == 'message.received' else 'outbound',
                    external_id=event.get('message_id') or event.get('seq_id'),
                    body=event.get('text', ''),
                    external_created_at=event.get('created_at'),
                    author_partner=author_partner,
                    author_x_id=author_x_id,
                )
            return True

        # Tweet events: route into a channel keyed by the tweet conversation.
        if etype.startswith('tweet.'):
            conversation_id = event.get('conversationId') or event.get('conversation_id')
            author_x_id = event.get('author_id') or event.get('user_id')
            body = event.get('text', '')
            if not conversation_id and event.get('tweet_id'):
                conversation_id = 'tweet-%s' % event['tweet_id']
            if not conversation_id:
                return False
            channel = self._get_x_channel(
                account,
                conversation_id=str(conversation_id),
                channel_type='x',
                create_if_not_found=True,
            )
            return channel._save_x_message(
                direction='inbound',
                external_id=event.get('tweet_id') or event.get('message_id'),
                body=body or '%s' % etype,
                external_created_at=event.get('created_at'),
                author_x_id=author_x_id,
                author_x_username=event.get('author_screen_name'),
            )

        # user.follow events: log to the account's chatter.
        if etype == 'user.follow':
            actor_names = event.get('actor_screen_names') or []
            if actor_names:
                account.message_post(
                    body='Followed by: %s' % ', '.join(actor_names),
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                )
            return True

        return False

