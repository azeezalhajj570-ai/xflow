# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Safe backfill of orphaned X discuss.channel records.

Background: some X channels (``channel_type`` ``x`` / ``x_group``) exist with
``x_account_id`` NULL because earlier sync paths created them without the
owning account. This module only ever assigns an account when the mapping is
deterministic:

- 1:1 channels (``x``, conversation id ``<uid>-<uid>``) match when one of the
  two id halves equals an account's ``twitter_user_id``.
- legacy group channels (``x_group`` with a numeric conversation id) match when
  exactly one account's ``twitter_user_id`` appears among the channel's
  ``discuss.channel.member`` partner ``x_user_id`` values.

Anything else (``g``-prefixed XChat group ids without a resolvable owner, or
ambiguous multi-account matches) is left untouched and reported, never guessed.

Audit: every assignment is recorded on the ``social.account`` as a lifecycle
message (``_post_lifecycle_message``), so the backfill is traceable.
"""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# XChat (X Groups product) conversation ids.
_CHAT_GROUP_ID_RE = __import__('re').compile(r'^g[0-9]+$')


class OrphanChannelBackfill(models.TransientModel):
    """Transient exposing the deterministic backfill as a model action."""

    _name = 'x.orphan.channel.backfill'
    _description = 'X Orphan Channel Backfill'

    @api.model
    def _accounts_by_user_id(self):
        """Return {twitter_user_id: social.account} for all twitter accounts."""
        accounts = self.env['social.account'].sudo().search([
            ('media_type', '=', 'twitter'),
        ])
        by_uid = {}
        for account in accounts:
            uid = account.twitter_user_id
            if uid:
                by_uid.setdefault(str(uid), account)
        return by_uid

    @api.model
    def backfill_orphans(self, dry_run=True):
        """Backfill orphaned X channels with a deterministic owner.

        Returns a dict:
            {'assigned': N, 'ambiguous': [...], 'unmappable': [...],
             'skipped_owned': N, 'dry_run': bool}
        ``assigned`` counts channels whose owner was deterministically resolved;
        ``ambiguous`` lists channel ids where multiple accounts could own them;
        ``unmappable`` lists channel ids with no resolvable owner.
        """
        channel_model = self.env['discuss.channel'].sudo()
        by_uid = self._accounts_by_user_id()
        orphans = channel_model.search([
            ('channel_type', 'in', ('x', 'x_group')),
            ('x_account_id', '=', False),
        ])
        assigned = 0
        ambiguous = []
        unmappable = []
        skipped_owned = 0
        for channel in orphans:
            conv_id = channel.x_conversation_id or ''
            member_uids = set()
            for member in channel.channel_member_ids:
                partner = member.partner_id
                if partner and partner.x_user_id:
                    member_uids.add(str(partner.x_user_id))
            # 1:1 conversation "uid-uid": owner must be one of the two halves.
            candidate = False
            if channel.channel_type == 'x' and '-' in conv_id and not _CHAT_GROUP_ID_RE.match(conv_id):
                halves = [p for p in conv_id.split('-') if p]
                owners = [by_uid[h] for h in halves if h in by_uid]
                candidate = owners[0] if len(owners) == 1 else False
                if len(owners) > 1:
                    ambiguous.append((channel.id, conv_id, 'multiple_1to1_owners'))
                    continue
            # Group channels: owner is the account whose X uid is a member
            # (legacy numeric groups carry member partners).
            if not candidate and (channel.channel_type == 'x_group'
                                  and not _CHAT_GROUP_ID_RE.match(conv_id)):
                owners = [by_uid[uid] for uid in member_uids if uid in by_uid]
                if len(owners) == 1:
                    candidate = owners[0]
                elif len(owners) > 1:
                    ambiguous.append((channel.id, conv_id, 'multiple_member_owners'))
                    continue
            if not candidate:
                unmappable.append((channel.id, conv_id, channel.channel_type))
                continue
            if dry_run:
                assigned += 1
                continue
            channel.write({'x_account_id': candidate.id})
            try:
                candidate._post_lifecycle_message(
                    'Backfilled orphan X channel #%s (%s) as owned by this account.'
                    % (channel.id, conv_id))
            except Exception:
                _logger.exception('Failed to record backfill audit for account %s', candidate.id)
            assigned += 1
        _logger.info(
            'X orphan backfill%s: assigned=%s ambiguous=%s unmappable=%s',
            ' (dry run)' if dry_run else '', assigned, len(ambiguous), len(unmappable))
        return {
            'assigned': assigned,
            'ambiguous': ambiguous,
            'unmappable': unmappable,
            'skipped_owned': skipped_owned,
            'dry_run': dry_run,
        }
