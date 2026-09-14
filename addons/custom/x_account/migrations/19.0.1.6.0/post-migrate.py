"""Deactivate the x.account task/channel automation rules by default.

Engagement operations (like/repost/comment/bookmark/unbookmark/follow and DMs)
must not fire automatically out of the box: since 19.0.1.6.0 every shipped
base_automation rule for these operations is shipped inactive, and operators
opt in per rule in the Automation UI. The channel rules on ``x.message`` and
the task rules on ``x.account.task`` were created inside a ``noupdate`` block,
so the XML loader never flips their ``active`` flag on upgrade — deactivate
them here explicitly.

The per-operation task rules (like/repost/comment/bookmark/unbookmark) were
removed entirely in this version; drop those records and their linked server
actions as well.
"""

_MODEL_TABLE = {
    'base.automation': 'base_automation',
    'ir.actions.server': 'ir_act_server',
}

_REMOVED = [
    'base_automation_x_task_like',
    'base_automation_x_task_repost',
    'base_automation_x_task_comment',
    'base_automation_x_task_bookmark',
    'base_automation_x_task_unbookmark',
    'ir_actions_server_x_task_like',
    'ir_actions_server_x_task_repost',
    'ir_actions_server_x_task_comment',
    'ir_actions_server_x_task_bookmark',
    'ir_actions_server_x_task_unbookmark',
]

# Rules that stay but must default to inactive.
_DEACTIVATED = [
    'base_automation_x_channel_bookmark',
    'base_automation_x_channel_unbookmark',
    'base_automation_x_task_follow',
    'base_automation_x_task_send_dm',
    'base_automation_x_task_send_group_dm',
    'base_automation_x_dm_auto_reply_user',
    'base_automation_x_dm_auto_reply_group',
]


def _resolve_model_data(cr, names):
    """Return {model: [res_id]} for the given ir.model.data xml names."""
    cr.execute(
        """
        SELECT id, model FROM ir_model_data
         WHERE module = 'x_account'
           AND name = ANY(%s)
        """,
        (names,),
    )
    by_model = {}
    for res_id, model in cr.fetchall():
        by_model.setdefault(model, []).append(res_id)
    return by_model


def migrate(cr, version):
    # 1. Drop the removed per-operation task rules and their server actions.
    removed = _resolve_model_data(cr, _REMOVED)
    for model, ids in removed.items():
        table = _MODEL_TABLE.get(model)
        if not table:
            continue
        cr.execute(
            'DELETE FROM %s WHERE id = ANY(%%s)' % table,
            (ids,),
        )
    # ir_model_data rows are cleaned up by the ORM on unlink; the removal above
    # is raw SQL, so drop the stale xrefs too.
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'x_account'
           AND name = ANY(%s)
        """,
        (_REMOVED,),
    )

    # 2. Deactivate the rules that remain shipped (opt-in behavior).
    deactivated = _resolve_model_data(cr, _DEACTIVATED)
    for model, ids in deactivated.items():
        if model != 'base.automation':
            continue
        cr.execute(
            'UPDATE base_automation SET active = False WHERE id = ANY(%s)',
            (ids,),
        )