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
            'filter_external_created_at',
            'groupby_direction', 'groupby_account', 'groupby_channel',
            'groupby_author', 'groupby_company', 'groupby_create_date',
        ))

    def test_account_task_search_view(self):
        self.env.ref('x_account.x_account_task_view_search')
        arch = self._search_arch('x.account.task')
        self._assert_arch_has(arch, (
            'status_pending', 'status_running', 'status_failed',
            'status_success', 'status_cancelled', 'overdue', 'retried',
            'filter_next_retry_at',
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

    def test_account_task_action_groups_by_account_operation_status(self):
        """Opening Tasks must land on Account > Operation > Status."""
        action = self.env.ref('x_account.action_x_account_task')
        context = action.context or ''
        expected = {
            'search_default_groupby_account': 1,
            'search_default_groupby_operation': 2,
            'search_default_groupby_status': 3,
        }
        for key, order in expected.items():
            self.assertIn("'%s': %s" % (key, order), context)
        # every default must name a filter that really exists in the search view
        arch = self._search_arch('x.account.task')
        for key in expected:
            self.assertIn('name="%s"' % key[len('search_default_'):], arch)

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
    def test_message_action_groups_by_chat(self):
        """Opening Messages must land grouped by chat."""
        action = self.env.ref('x_account.action_x_account_messages')
        self.assertIn("'search_default_groupby_channel': 1", action.context or '')
        self.assertIn('name="groupby_channel"', self._search_arch('x.message'))

    def test_chat_action_pins_the_base_search_view(self):
        """The Chat list must resolve deterministically to the search view our
        X filters are attached to (several primaries exist for discuss.channel)."""
        action = self.env.ref('x_account.action_x_account_group_channels')
        self.assertEqual(
            action.search_view_id,
            self.env.ref('mail.discuss_channel_view_search'))

    def test_groups_menu_points_at_the_group_action(self):
        menu = self.env.ref('x_account.menu_x_account_groups')
        self.assertEqual(
            menu.action.id, self.env.ref('x_account.action_x_account_group').id)
