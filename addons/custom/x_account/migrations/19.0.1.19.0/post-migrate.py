"""Delete the two server actions that became bound account actions.

``action_server_fetch_groups`` and ``action_server_delete_x_subscriptions``
were declared unbound in ``data/server_actions.xml`` and targeted by buttons on
the account form.  The buttons are now bound server actions declared in
``data/server_actions_form.xml`` as ``action_form_fetch_groups`` and
``action_form_delete_x_subscriptions``.

Deleting the old records is not optional busywork:

* removing a record from a data file never deletes it, so they would linger as
  same-named, unbound duplicates in Settings / Technical / Server Actions;
* they were declared inside a ``noupdate="1"`` block, so their
  ``ir.model.data.noupdate`` flag is set, and ``IrModelData._build_update_xmlids_query``
  never rewrites that column on conflict.  Their binding could therefore never be
  written, which is exactly why the replacements take new xml ids instead.

Nothing references either record (checked across the repository), so the xml ids
are removed along with the rows.
"""

_OLD_XMLIDS = ('action_server_fetch_groups', 'action_server_delete_x_subscriptions')


def migrate(cr, version):
    cr.execute(
        """
        SELECT res_id
          FROM ir_model_data
         WHERE module = 'x_account'
           AND model = 'ir.actions.server'
           AND name = ANY(%s)
        """,
        (list(_OLD_XMLIDS),),
    )
    ids = [row[0] for row in cr.fetchall()]
    if not ids:
        return

    cr.execute("DELETE FROM ir_act_server WHERE id = ANY(%s)", (ids,))
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'x_account'
           AND model = 'ir.actions.server'
           AND name = ANY(%s)
        """,
        (list(_OLD_XMLIDS),),
    )
