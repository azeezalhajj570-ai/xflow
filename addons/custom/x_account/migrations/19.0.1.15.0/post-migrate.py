"""Drop the channel Unbookmark automation.

Unbookmark is no longer a channel automation action: it has no per-chat
toggle, so its ``x.message`` rule and the server action it called are removed.
Both records live in the ``noupdate`` block of ``channel_automation.xml``, so
deleting them from the XML does not remove the rows already loaded in an
existing database — do it here.
"""

_REMOVED = [
    'base_automation_x_channel_unbookmark',
    'ir_actions_server_x_channel_unbookmark',
]

_MODEL_TABLE = {
    'base.automation': 'base_automation',
    'ir.actions.server': 'ir_act_server',
}


def migrate(cr, version):
    cr.execute(
        """
        SELECT id, model FROM ir_model_data
         WHERE module = 'x_account'
           AND name = ANY(%s)
        """,
        (_REMOVED,),
    )
    by_model = {}
    for res_id, model in cr.fetchall():
        by_model.setdefault(model, []).append(res_id)
    for model, ids in by_model.items():
        table = _MODEL_TABLE.get(model)
        if not table:
            continue
        cr.execute(
            'DELETE FROM %s WHERE id = ANY(%%s)' % table,
            (ids,),
        )
    # The deletions above are raw SQL, so drop the stale xrefs by hand.
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'x_account'
           AND name = ANY(%s)
        """,
        (_REMOVED,),
    )
