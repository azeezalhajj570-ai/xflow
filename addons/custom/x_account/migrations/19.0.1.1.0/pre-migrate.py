import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Delete body-less ``x.message`` rows.

    They only ever existed as invisible "encrypted" markers: never posted to
    Discuss (every caller passed ``no_mail``), never matched by the DM
    automations (they filter on ``body_plain != False``) and with no field in
    the list/form views. They are no longer created.
    """
    cr.execute(
        "SELECT count(*) FROM x_message "
        "WHERE coalesce(btrim(body_plain), '') = ''")
    total = cr.fetchone()[0]
    if not total:
        return
    cr.execute(
        "SELECT count(*) FROM x_message "
        "WHERE coalesce(btrim(body_plain), '') = '' "
        "AND mail_message_id IS NOT NULL")
    linked = cr.fetchone()[0]
    if linked:
        # The bubble itself is a mail.message and is left intact; only the
        # mirror row goes away.
        _logger.warning(
            'x_account: %s of %s body-less x.message row(s) reference a '
            'mail.message; deleting the x.message, keeping the bubble',
            linked, total)
    cr.execute(
        "DELETE FROM x_message WHERE coalesce(btrim(body_plain), '') = ''")
    _logger.info('x_account: deleted %s body-less x.message row(s)', total)
