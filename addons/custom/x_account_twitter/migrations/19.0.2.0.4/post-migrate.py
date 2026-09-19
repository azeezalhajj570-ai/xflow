"""Move conversation key-change blobs out of ``x_twitter_event.payload``.

Before this version every webhook delivery stored the conversation's whole
key-change blob inside its payload, so the same few keys were duplicated across
hundreds of thousands of rows and ``x_twitter_event`` grew to dominate the
database. The blob now lives once per ``(account, conversation, key)`` in
``x.twitter.key.change``.

Order matters: the distinct blobs are copied out first, then the payloads of
processed deliveries are cleared. Clearing first would lose the key chain
needed to decrypt a later message in the conversation. Both steps are batched
so a 170k-row table is never detoasted in a single statement.
"""

import logging

_logger = logging.getLogger(__name__)

_BATCH = 2000


def migrate(cr, version):
    if not version:
        return
    _backfill_key_changes(cr)
    _clear_terminal_payloads(cr)


def _backfill_key_changes(cr):
    cr.execute("""
        SELECT min(id), max(id) FROM x_twitter_event
        WHERE has_key_change AND account_id IS NOT NULL
          AND conversation_id IS NOT NULL
    """)
    low, high = cr.fetchone()
    if low is None:
        _logger.info('x_account_twitter: no key-change rows to backfill')
        return
    _logger.info(
        'x_account_twitter: backfilling conversation key changes (ids %s-%s)',
        low, high)
    stored = 0
    start = low - 1
    while start < high:
        end = start + _BATCH
        try:
            with cr.savepoint():
                cr.execute("""
                    INSERT INTO x_twitter_key_change
                        (account_id, conversation_id, blob_hash, blob,
                         create_date, write_date)
                    SELECT DISTINCT ON (account_id, conversation_id, blob_hash)
                        account_id, conversation_id, blob_hash, blob,
                        now() AT TIME ZONE 'UTC', now() AT TIME ZONE 'UTC'
                    FROM (
                        SELECT
                            account_id,
                            conversation_id,
                            md5(blob) AS blob_hash,
                            blob
                        FROM (
                            SELECT
                                account_id,
                                conversation_id,
                                payload::jsonb
                                    #>> '{payload,conversation_key_change_event}'
                                    AS blob
                            FROM x_twitter_event
                            WHERE id > %s AND id <= %s
                              AND has_key_change
                              AND account_id IS NOT NULL
                              AND conversation_id IS NOT NULL
                              AND left(payload, 1) = '{'
                        ) AS parsed
                        WHERE blob IS NOT NULL AND blob <> ''
                    ) AS batch
                    ON CONFLICT (account_id, conversation_id, blob_hash)
                    DO NOTHING
                """, (start, end))
                stored += cr.rowcount
        except Exception as exc:  # noqa: BLE001 - never abort the upgrade
            _logger.warning(
                'x_account_twitter: key-change backfill skipped ids %s-%s: %s',
                start + 1, end, exc)
        start = end
    _logger.info(
        'x_account_twitter: stored %s distinct key-change blob(s)', stored)


def _clear_terminal_payloads(cr):
    """Drop the payload of processed deliveries.

    A key-change row whose blob could have been backfilled (it has both an
    account and a conversation) is only cleared because its key now lives in
    ``x.twitter.key.change``. Rows without an account or conversation were never
    eligible for key recovery, so their payload is dead weight.
    """
    with cr.savepoint():
        cr.execute("""
            UPDATE x_twitter_event
            SET payload = NULL
            WHERE payload IS NOT NULL
              AND state IN ('done', 'skipped')
              AND (
                  NOT has_key_change
                  OR (account_id IS NOT NULL AND conversation_id IS NOT NULL)
              )
        """)
        cleared = cr.rowcount
    _logger.info(
        'x_account_twitter: cleared the payload of %s processed event(s)',
        cleared)
