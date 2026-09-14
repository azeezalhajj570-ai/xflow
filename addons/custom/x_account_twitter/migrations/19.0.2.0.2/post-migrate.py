"""Backfill ``x_twitter_event`` conversation fields for existing rows.

The key-change lookup in ``TwitterActivity._conversation_key_change_blobs``
used to LIKE-scan the whole ``payload`` text on every missing-key retry, which
dominated the queue sweep on a 146k-row table. The new fields are populated at
create time; this backfills the rows that already exist.

Payloads are written by ``json.dumps``, so the ``jsonb`` cast is safe. A
failure is logged and skipped rather than aborting the upgrade — the lookup
then simply misses pre-existing key-change rows.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    _logger.info(
        'x_account_twitter: backfilling x.twitter.event conversation fields')
    try:
        with cr.savepoint():
            cr.execute("""
                UPDATE x_twitter_event
                SET conversation_id = NULLIF(
                        payload::jsonb #>> '{payload,conversation_id}', ''),
                    has_key_change = (
                        COALESCE(payload::jsonb
                                 #>> '{payload,conversation_key_change_event}',
                                 '') <> '')
                WHERE payload IS NOT NULL AND payload LIKE '{%'
            """)
            backfilled = cr.rowcount
        _logger.info('x_account_twitter: backfilled %s event rows', backfilled)
    except Exception as exc:  # noqa: BLE001 - never abort the upgrade
        _logger.warning(
            'x_account_twitter: conversation-field backfill skipped: %s', exc)
