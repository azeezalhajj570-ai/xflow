"""Rewire the legacy single task-queue cron into the sharded scheme.

``cron_process_x_task_queue`` was created inside a ``noupdate`` block, so the
XML loader never updates it (``ir_model_data.noupdate`` blocks the update query
regardless of the data file). Its payload lives in a linked ``ir.actions.server``
in Odoo 19, so rewrite that action's code to the shard-0 variant here. The
shard-1 job is created normally by ``data/cron.xml``.
"""


def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_act_server
           SET code = %s
         WHERE id IN (
            SELECT c.ir_actions_server_id
              FROM ir_cron c
              JOIN ir_model_data d
                ON d.res_id = c.id AND d.model = 'ir.cron'
             WHERE d.module = 'x_account'
               AND d.name = 'cron_process_x_task_queue'
         )
        """,
        ('model._process_queue(shard=0, shards=2, commit=True)',),
    )
    cr.execute(
        """
        UPDATE ir_cron
           SET cron_name = %s
         WHERE id IN (
            SELECT res_id FROM ir_model_data
             WHERE module = 'x_account'
               AND name = 'cron_process_x_task_queue'
               AND model = 'ir.cron'
         )
        """,
        ('X: Process Task Queue (shard 0)',),
    )
