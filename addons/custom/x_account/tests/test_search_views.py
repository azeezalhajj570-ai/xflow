import ast

from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestXSearchViews(XAccountTestBase):
    """Every X list view exposes a search view with filters and group-by.

    The assertions run against the RESOLVED arch so the inherited search views
    (``social.account``, ``discuss.channel``) are checked together with their
    base view, not just the record we author.
    """

    def _search_arch(self, model):
        result = self.env[model].get_view(view_type='search')
        arch = result.get('arch') if isinstance(result, dict) else result[0]
        return arch or ''

    def _assert_arch_has(self, arch, names):
        for name in names:
            self.assertIn('name="%s"' % name, arch)

    # ------------------------------------------------------------ owned models
    def test_message_search_view(self):
        self.env.ref('x_account.x_message_view_search')
        arch = self._search_arch('x.message')
        self._assert_arch_has(arch, (
            'direction_inbound', 'direction_outbound', 'acked', 'delivered',
            'participant_joined', 'participant_left',
            'filter_external_created_at', 'filter_received_today',
            'filter_received_last_hour',
            'groupby_direction', 'groupby_account', 'groupby_channel',
            'groupby_author', 'groupby_company', 'groupby_create_date',
        ))

    def test_account_task_search_view(self):
        self.env.ref('x_account.x_account_task_view_search')
        arch = self._search_arch('x.account.task')
        self._assert_arch_has(arch, (
            'status_pending', 'status_running', 'status_failed',
            'status_success', 'status_cancelled', 'overdue', 'retried',
            'filter_next_retry_at', 'filter_created_today',
            'filter_created_last_hour',
            'groupby_status', 'groupby_operation', 'groupby_account',
            'groupby_group', 'groupby_create_date',
        ))

    def test_account_group_search_view(self):
        self.env.ref('x_account.x_account_group_view_search')
        arch = self._search_arch('x.account.group')
        self._assert_arch_has(arch, (
            'auto_execute', 'paused', 'running', 'has_accounts',
            'groupby_actions', 'groupby_auto_execute', 'groupby_paused',
            'groupby_create_date',
        ))

    def test_account_task_action_defaults_to_last_hour_without_groupby(self):
        """Opening Tasks must land on plain list with Last Hour pre-applied,
        with no group-by forced."""
        action = self.env.ref('x_account.action_x_account_task')
        context = action.context or ''
        self.assertIn("'search_default_filter_created_last_hour': 1", context)
        for key in ('groupby_account', 'groupby_operation', 'groupby_status'):
            self.assertNotIn("'search_default_%s'" % key, context)
        # the default must name a filter that really exists in the search view
        self.assertIn('name="filter_created_last_hour"',
                      self._search_arch('x.account.task'))

    # -------------------------------------------------------- inherited models
    def test_social_account_search_view_extends_base(self):
        view = self.env.ref(
            'x_account.view_social_account_search_inherit_x_account')
        self.assertEqual(
            view.inherit_id, self.env.ref('social.social_account_view_search'))
        self._assert_arch_has(self._search_arch('social.account'), (
            'x_connection_active', 'x_connection_reauth', 'x_connection_broken',
            'x_connection_disabled', 'x_chat_not_initialized',
            'x_chat_pin_locked',
            'x_groupby_connection_status', 'x_groupby_chat_key_mode',
            'x_groupby_chat_initialized',
        ))

    def test_discuss_channel_search_view_extends_base(self):
        view = self.env.ref(
            'x_account.view_discuss_channel_search_inherit_x_account')
        self.assertEqual(
            view.inherit_id, self.env.ref('mail.discuss_channel_view_search'))
        self._assert_arch_has(self._search_arch('discuss.channel'), (
            'x_type_direct', 'x_type_group', 'x_sync_ok', 'x_sync_encrypted',
            'x_sync_partial', 'x_sync_failed', 'x_no_account',
            'x_groupby_type', 'x_groupby_account', 'x_groupby_sync_status',
        ))

    # ----------------------------------------------------------------- menus
    def test_account_and_chat_lists_show_the_archive_state(self):
        """Both X lists surface the archive state the same way the Automation
        Rules list does: a toggle leading the columns, without inline editing."""
        for xmlid in ('x_account.x_account_social_account_view_list',
                      'x_account.x_group_channel_view_tree'):
            arch = self.env.ref(xmlid).arch
            self.assertIn('name="active"', arch, xmlid)
            self.assertIn('widget="boolean_toggle"', arch, xmlid)
            self.assertLess(
                arch.index('name="active"'), arch.index('name="name"'), xmlid)
            self.assertNotIn('editable="bottom"', arch, xmlid)

    def test_automation_rule_list_leads_with_the_toggle(self):
        """Reference for the pattern above: the Automation Rules list puts the
        toggle before `name`."""
        arch = self.env['base.automation'].get_view(view_type='list')['arch']
        self.assertIn('name="active"', arch)
        self.assertIn('widget="boolean_toggle"', arch)
        self.assertLess(
            arch.index('name="active"'), arch.index('name="name"'))

    def test_account_and_chat_searches_offer_active_and_archived(self):
        """The archive filter pair must be reachable from both search views.
        The `Archived` half comes from the base social/mail views, so only the
        `Active` half is declared here - assert both are in the final arch."""
        for model in ('social.account', 'discuss.channel'):
            arch = self._search_arch(model)
            self.assertIn('name="x_filter_active"', arch, model)
            self.assertIn("('active', '=', True)", arch, model)
            self.assertIn("('active', '=', False)", arch, model)

    def test_message_action_defaults_to_last_hour_without_groupby(self):
        """Opening Messages must land on a plain list with Last Hour
        pre-applied, with no group-by forced."""
        action = self.env.ref('x_account.action_x_account_messages')
        context = action.context or ''
        self.assertIn("'search_default_filter_received_last_hour': 1", context)
        self.assertNotIn("'search_default_groupby_channel'", context)
        self.assertIn('name="groupby_channel"', self._search_arch('x.message'))

    def test_groups_menu_is_hidden(self):
        """The Groups menu was removed on request; its window action stays (it
        is still reachable from the automation tooling)."""
        self.assertFalse(self.env.ref(
            'x_account.menu_x_account_groups', raise_if_not_found=False))
        self.assertTrue(self.env.ref('x_account.action_x_account_group'))

    def test_automation_lists_include_channel_rules(self):
        """The X Automation / Server Actions lists must cover discuss.channel
        alongside the x.* models. Both leaves in one implicit AND match nothing,
        which is what hid every rule from the list."""
        for xmlid, model in (
                ('x_account.action_x_account_automation_rules', 'base.automation'),
                ('x_account.action_x_account_server_actions', 'ir.actions.server')):
            domain = ast.literal_eval(self.env.ref(xmlid).domain or '[]')
            found = self.env[model].with_context(active_test=False).search(domain)
            models = set(found.mapped('model_id.model'))
            self.assertTrue(
                any(name.startswith('x.') for name in models), (xmlid, models))
            self.assertIn('discuss.channel', models, xmlid)

    def test_chat_action_pins_the_base_search_view(self):
        """The Chat list must resolve deterministically to the search view our
        X filters are attached to (several primaries exist for discuss.channel)."""
        action = self.env.ref('x_account.action_x_account_group_channels')
        self.assertEqual(
            action.search_view_id,
            self.env.ref('mail.discuss_channel_view_search'))

    def test_task_list_hides_target_id_columns(self):
        """Tasks list must not show target_post_id or target_screen_name."""
        arch = self.env.ref('x_account.x_account_task_view_tree').arch
        self.assertNotIn('name="target_post_id"', arch)
        self.assertNotIn('name="target_screen_name"', arch)

    def test_task_list_shows_processing_time(self):
        """Tasks list must expose the processing-time column like Operations."""
        arch = self.env.ref('x_account.x_account_task_view_tree').arch
        self.assertIn('name="processing_time"', arch)

    def test_automation_rules_action_shows_all_rules_by_default(self):
        """Automation Rules list must show every X-related rule — active and
        inactive (no default filter that hides active rules)."""
        action = self.env.ref('x_account.action_x_account_automation_rules')
        self.assertNotIn("'search_default_inactive': 1", action.context or '')
        arch = self._search_arch('base.automation')
        self._assert_arch_has(arch, ['inactive', 'archived'])
