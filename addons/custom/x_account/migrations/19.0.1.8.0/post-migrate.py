"""Bind "Fetch Group Messages" to the Chat model's Action menu.

``data/server_actions.xml`` ships inside a ``noupdate`` block, so on an
existing database the XML loader never rewrites these records. The action was
left with a NULL ``binding_model_id``, which is why it never appeared in the
Action menu of a chat, and its code called the single-conversation method
through a ``records`` loop — that method raises on any non-X chat, so exposing
it model-wide without rewiring it would have surfaced a raw error for every
ordinary conversation.

Rewrite the code to the bulk entry point (which filters non-X chats out and
aggregates one notification) and bind the action to ``discuss.channel``.
"""

_SERVER_ACTION = 'action_server_fetch_group_messages'
_CODE = 'action = records.action_fetch_group_messages_bulk()'


def migrate(cr, version):
    cr.execute(
        """
        SELECT res_id FROM ir_model_data
         WHERE module = 'x_account' AND name = %s
        """,
        (_SERVER_ACTION,),
    )
    row = cr.fetchone()
    if not row:
        return
    (action_id,) = row

    cr.execute("SELECT id FROM ir_model WHERE model = 'discuss.channel'")
    model = cr.fetchone()
    if not model:
        return
    (model_id,) = model

    # ``name`` is a translated field, so it is deliberately left untouched.
    cr.execute(
        """
        UPDATE ir_act_server
           SET binding_model_id = %s,
               binding_type = 'action',
               code = %s
         WHERE id = %s
        """,
        (model_id, _CODE, action_id),
    )
