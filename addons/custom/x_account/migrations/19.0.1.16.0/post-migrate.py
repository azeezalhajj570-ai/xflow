"""Move the task-queue worker crons to a 10s secondary cadence.

``cron_process_x_task_queue`` was created inside a ``noupdate`` block, so the
XML loader never updates it (``ir_model_data.noupdate`` blocks the update query
regardless of the data file) — the same reason ``19.0.1.4.0`` rewired its linked
server action here. The shard-1 record ships non-noupdate, so ``data/cron.xml``
updates it by itself; this migration is belt-and-braces for both.

``seconds`` is only a valid ``interval_type`` once models/ir_cron.py is loaded,
which always happens before migrations run on upgrade.
"""

_CRONS = ['cron_process_x_task_queue', 'cron_process_x_task_queue_1']


def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_cron
           SET interval_number = 10,
               interval_type = 'seconds'
         WHERE id IN (
            SELECT res_id FROM ir_model_data
             WHERE module = 'x_account'
               AND name = ANY(%s)
               AND model = 'ir.cron'
         )
        """,
        (_CRONS,),
    )