"""Rename ``social_account.x_getxapi_auth_token`` to ``authtoken``.

The field was renamed in the model, which on its own would leave Odoo adding
an empty ``authtoken`` column next to the old one and every stored GetXAPI
auth token stranded. Rename the column before the registry loads so the
existing values stay in place.
"""


def _has_column(cr, table, column):
    cr.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    if not _has_column(cr, 'social_account', 'x_getxapi_auth_token'):
        return
    if _has_column(cr, 'social_account', 'authtoken'):
        # A previous run already renamed the column; the old one is a leftover.
        return
    cr.execute(
        'ALTER TABLE social_account '
        'RENAME COLUMN x_getxapi_auth_token TO authtoken'
    )
