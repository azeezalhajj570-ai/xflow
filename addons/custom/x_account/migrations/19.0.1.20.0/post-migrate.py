"""Split the X connection status into a detailed state + an aggregated status.

``x_connection_status`` used to be the detailed lifecycle value (NEW /
AUTHENTICATING / ACTIVE / REAUTH_REQUIRED / DISCONNECTED / INVALID / ERROR /
DISABLED).  It is now the single, aggregated status the account form shows
(NOT_CONFIGURED / ACTIVE / ERROR), and the detailed value lives in the new
internal ``x_connection_state`` field.

Odoo keeps the old keys in the column when a selection shrinks, so both columns
are rewritten here:

1. copy the old detailed value into ``x_connection_state``;
2. compute the aggregated ``x_connection_status`` from the detailed state and
   the chat-encryption flags, the same way ``_x_overall_connection_status`` does.

Without this the existing accounts keep a key that is no longer in the
selection and read as blank in the form.
"""

_DETAILED_TO_NEW = (
    'new', 'authenticating', 'active', 'reauth_required',
    'disconnected', 'invalid', 'error', 'disabled',
)


def migrate(cr, version):
    cr.execute(
        """
        UPDATE social_account
           SET x_connection_state = x_connection_status
         WHERE x_connection_status = ANY(%s)
           AND (x_connection_state IS NULL OR x_connection_state = 'new')
        """,
        (list(_DETAILED_TO_NEW),),
    )
    cr.execute(
        """
        UPDATE social_account
           SET x_connection_status = CASE
               WHEN x_connection_state IN ('reauth_required', 'disconnected',
                                           'invalid', 'error')
                 OR (COALESCE(x_chat_initialized, FALSE)
                     AND (COALESCE(x_chat_decrypt_stopped, FALSE)
                          OR COALESCE(x_chat_pin_locked, FALSE)))
                 THEN 'error'
               WHEN x_connection_state = 'active'
                 AND COALESCE(x_chat_initialized, FALSE)
                 AND NOT COALESCE(x_chat_decrypt_stopped, FALSE)
                 AND NOT COALESCE(x_chat_pin_locked, FALSE)
                 THEN 'active'
               ELSE 'not_configured'
             END
        """
    )
