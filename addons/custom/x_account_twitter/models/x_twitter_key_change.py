# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Store X Chat conversation key-change blobs once instead of per delivery.

Every delivery of a group-chat conversation repeats the conversation's whole
``conversation_key_change_event`` blob in its payload — ~97 kB of the ~105 kB
payload stored per event in production. Keeping that inside
``x.twitter.event.payload`` stored the same handful of blobs tens of thousands
of times: one conversation held 4,639 deliveries carrying only 9 distinct
blobs, and 20,000 sampled events carried 164 distinct blobs between them. That
duplication is what made ``x_twitter_event`` dominate the database.

Blobs are keyed by ``(account, conversation, md5(blob))`` so each distinct key
is stored once. ``x.twitter.event.payload`` can then be dropped as soon as a
delivery is processed, because the key chain needed to decrypt a later message
lives here rather than in the event row.
"""

import hashlib

from odoo import api, fields, models


class XTwitterKeyChange(models.Model):
    _name = 'x.twitter.key.change'
    _description = 'X Chat Conversation Key Change'
    _rec_name = 'conversation_id'
    _order = 'id desc'

    account_id = fields.Many2one(
        'social.account',
        string='X Account',
        required=True,
        index=True,
        ondelete='cascade',
    )
    conversation_id = fields.Char(
        string='Conversation ID',
        required=True,
        index=True,
    )
    blob_hash = fields.Char(
        string='Blob Hash',
        required=True,
        help='md5 of the key-change blob: the dedup key that keeps each '
             'distinct conversation key stored once.',
    )
    blob = fields.Text(
        string='Key Change Blob',
        required=True,
    )

    _blob_uniq = models.Constraint(
        'UNIQUE(account_id, conversation_id, blob_hash)',
        'A conversation key-change blob is stored once per account.',
    )

    @staticmethod
    def blob_digest(blob):
        return hashlib.md5(blob.encode('utf-8')).hexdigest()

    @api.model
    def store(self, key_changes):
        """Persist key-change blobs, skipping the ones already stored.

        ``key_changes`` is an iterable of ``(account_id, conversation_id,
        blob)``. One statement keeps this cheap on the webhook hot path; the
        unique constraint plus ``ON CONFLICT`` turns a concurrent delivery of
        the same blob into a no-op instead of an error.
        """
        rows = {}
        for account_id, conversation_id, blob in key_changes:
            if not (account_id and conversation_id and blob):
                continue
            digest = self.blob_digest(blob)
            rows[(account_id, str(conversation_id), digest)] = blob
        if not rows:
            return 0
        values = ', '.join(
            "(%s, %s, %s, %s, now() AT TIME ZONE 'UTC', "
            "now() AT TIME ZONE 'UTC')" for _ in rows)
        params = []
        for (account_id, conversation_id, digest), blob in rows.items():
            params += [account_id, conversation_id, digest, blob]
        self.env.cr.execute(
            "INSERT INTO x_twitter_key_change "
            "(account_id, conversation_id, blob_hash, blob, create_date, "
            "write_date) VALUES %s "
            "ON CONFLICT (account_id, conversation_id, blob_hash) "
            "DO NOTHING" % values,
            params,
        )
        return self.env.cr.rowcount
