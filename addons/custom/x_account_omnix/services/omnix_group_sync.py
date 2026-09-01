# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Group-DM sync from OmniX into Odoo discuss channels + partners.

Owns the "sync" side of the provider (SRP): given a client, it paginates
``/dm/list``, upserts ``res.partner`` members and ``discuss.channel``
(``channel_type='x_group'``) records, and reports what it did. The provider
itself only forwards these calls.
"""

import logging

from . import omnix_envelope

_LOGGER = logging.getLogger(__name__)


class OmniXGroupSync:
    """Syncs group-DM conversations + members for one account's provider client."""

    def __init__(self, env, client):
        self.env = env
        self.client = client

    # ------------------------------------------------------------------ public
    def fetch_groups(self, account, limit=100):
        """Fetch group-DM conversations + members and sync them into
        discuss.channel (channel_type 'x_group') and res.partner.

        OmniX's /dm/list returns at most 5 participants per group (a hard
        preview cap) and its participant_count is always 5 for groups, so we
        sync exactly the members OmniX returns and report only what we synced.

        Returns a summary dict of groups created/updated.
        """
        count = min(int(limit), 100)
        conversations = []
        cursor_id = graph_snapshot_id = None
        while True:
            params = {'count': count}
            if cursor_id:
                params['cursor_id'] = cursor_id
            if graph_snapshot_id:
                params['graph_snapshot_id'] = graph_snapshot_id
            data = self.client.request('GET', '/dm/list', params=params)
            inbox = (data or {}).get('data') or {}
            page = inbox.get('conversations') or []
            if isinstance(page, dict):
                page = list(page.values())
            conversations.extend(page)
            if not inbox.get('has_more'):
                break
            nxt = inbox.get('next_cursor') or {}
            cursor_id = nxt.get('cursor_id')
            graph_snapshot_id = nxt.get('graph_snapshot_id')
            if not cursor_id:
                break
            if len(conversations) >= int(limit):
                break
        groups = [c for c in conversations if c.get('type') == 'group']

        channel_model = self.env['discuss.channel'].sudo()
        partner_model = self.env['res.partner'].sudo()
        created = updated = members = 0

        for conv in groups:
            conv_id = conv.get('conversation_id') or conv.get('id')
            if not conv_id:
                continue
            # Upsert members -> res.partner (keyed by x_user_id)
            participant_ids = []
            member_names = []
            for p in conv.get('participants') or []:
                x_uid = p.get('id')
                if not x_uid:
                    continue
                partner = partner_model.search([('x_user_id', '=', str(x_uid))], limit=1)
                if not partner:
                    partner = partner_model.create({
                        'name': p.get('name') or p.get('userName') or str(x_uid),
                        'x_user_id': str(x_uid),
                        'x_username': p.get('userName'),
                    })
                    members += 1
                # Keep verification flags fresh on every sync.
                partner.write({
                    'x_is_verified': bool(p.get('isVerified')),
                    'x_is_blue_verified': bool(p.get('isBlueVerified')),
                })
                participant_ids.append(partner.id)
                member_names.append(partner.x_username or partner.name or str(x_uid))
            # Best-effort group name: OmniX does not return a group name, so we
            # derive one from member usernames. Only applied at creation time —
            # existing names (e.g. manually corrected) are never overwritten.
            group_name = conv.get('name') or ', '.join(member_names[:4]) or conv_id
            # Upsert group channel
            channel = channel_model._get_x_channel(
                account,
                conversation_id=conv_id,
                channel_type='x_group',
                create_if_not_found=False,
            )
            if not channel:
                channel = channel_model._get_x_channel(
                    account,
                    conversation_id=conv_id,
                    channel_type='x_group',
                    create_if_not_found=True,
                    member_ids=participant_ids,
                )
                channel.write({'name': group_name})
                created += 1
            else:
                updated += 1
                # Refresh member list (replace members directly via the
                # discuss.channel.member model, not Command on create/write).
                # Name is intentionally left untouched (preserve manual edits).
                member_model = self.env['discuss.channel.member'].sudo()
                member_model.search([
                    ('channel_id', '=', channel.id),
                ]).unlink()
                member_model.create([
                    {'channel_id': channel.id, 'partner_id': pid}
                    for pid in participant_ids
                ])
        return {'groups': len(groups), 'created': created, 'updated': updated,
                'members': members}

    def fetch_group_messages(self, account, limit=100):
        """Fetch messages from X group-DM conversations and store them in the
        discuss.channel (channel_type 'x_group') as x.message records.

        OmniX's /dm/conversation requires the account's XChat encryption code
        to read encrypted group DMs; without it the call fails (authentication
        or 502). Returns a summary of messages stored per group.
        """
        # Resolve the group channels (they must exist; use fetch_groups first).
        channels = self.env['discuss.channel'].sudo().search([
            ('channel_type', '=', 'x_group'),
            ('x_account_id', '=', account.id),
        ])
        per_group = int(limit)
        total = 0
        failures = 0
        for channel in channels:
            conv_id = channel.x_conversation_id
            if not conv_id:
                continue
            try:
                result = self.get_dms(conv_id, limit=per_group)
                for msg in result['messages']:
                    author_partner = False
                    sender_id = msg.get('sender_id')
                    if sender_id:
                        author_partner = self.env['res.partner'].sudo().search(
                            [('x_user_id', '=', str(sender_id))], limit=1)
                    channel._save_x_message(
                        direction='outbound' if msg.get('from_me') else 'inbound',
                        external_id=msg['id'],
                        body=msg.get('text', ''),
                        external_created_at=msg.get('created_at'),
                        author_partner=author_partner,
                        author_x_id=sender_id,
                    )
                    total += 1
            except Exception:
                _LOGGER.exception('Failed to fetch messages for group %s', conv_id)
                failures += 1
        return {'groups': len(channels), 'messages': total, 'failures': failures}

    # --------------------------------------------------------------- helpers
    def get_dms(self, conversation_id, limit=100, cursor=None):
        """Return a page of messages for a conversation via the client."""
        body = {'conversation_id': conversation_id, 'count': int(limit)}
        if cursor:
            body['cursor'] = cursor
        data = self.client.request('POST', '/dm/conversation', body=body)
        return omnix_envelope.OmniXEnvelopeParser.messages(
            data, conversation_id, limit=limit)
