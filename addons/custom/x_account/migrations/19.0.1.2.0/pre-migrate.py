"""Remove the Groups menu introduced in 19.0.1.1.0.

The menu is no longer defined in ``views/menus.xml``, but Odoo does not delete
records that disappear from a data file, so databases that already installed
19.0.1.1.0 keep a dangling ``x_account.menu_x_account_groups``. Unlink it here.

The window action (``action_x_account_group``) is intentionally kept: it is still
referenced by the automation tooling and can be opened from code.
"""


def migrate(cr, version):
    cr.execute(
        """
        DELETE FROM ir_ui_menu
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 'x_account'
              AND name = 'menu_x_account_groups'
              AND model = 'ir.ui.menu'
        )
        """
    )
    cr.execute(
        """
        DELETE FROM ir_model_data
        WHERE module = 'x_account'
          AND name = 'menu_x_account_groups'
          AND model = 'ir.ui.menu'
        """
    )
