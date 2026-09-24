from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestXAccountStatusBar(XAccountTestBase):
    """The account form carries ONE read-only connection status bar.

    The chat-encryption state used to have a second bar next to it; it is now
    folded into the aggregated connection status and kept only as internal
    detail (``x_chat_status``).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')

    def _form_arch(self, xmlid):
        view = self.env.ref(xmlid)
        result = self.env['social.account'].get_view(
            view_id=view.id, view_type='form')
        return result['arch'] if isinstance(result, dict) else result[0]

    def test_standalone_form_has_one_status_bar(self):
        arch = self._form_arch(
            'x_account.x_account_social_account_view_form_standalone')
        self.assertEqual(arch.count('widget="statusbar"'), 1,
                         'the form must show exactly one status bar')
        self.assertIn('name="x_connection_status"', arch)
        self.assertNotIn('name="x_chat_status"', arch)
        self.assertIn('statusbar_visible="not_configured,active,error"', arch)

    def test_inherited_form_has_one_status_bar(self):
        arch = self.env.ref(
            'x_account.x_account_social_account_view_form').arch
        self.assertEqual(arch.count('widget="statusbar"'), 1)
        self.assertIn('name="x_connection_status"', arch)
        self.assertNotIn('name="x_chat_status"', arch)

    def test_status_field_is_system_controlled_and_tracked(self):
        field = self.env['social.account']._fields['x_connection_status']
        self.assertTrue(field.readonly)
        self.assertTrue(field.tracking)

    def test_default_status_is_not_configured(self):
        account = self.env['social.account'].create({
            'name': 'Status Bar Account',
            'media_id': self.twitter_media.id,
        })
        self.assertEqual(account.x_connection_status, 'not_configured')
        labels = dict(account._fields['x_connection_status'].selection)
        self.assertEqual(labels['not_configured'], 'غير مُهيأ')
        self.assertEqual(labels['active'], 'متصل')
        self.assertEqual(labels['error'], 'خطأ')
