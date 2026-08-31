from unittest.mock import patch

from odoo.addons.mail.tests.common import MailCommon

from odoo.addons.social_twitter.models.social_account import SocialAccount as TwitterSocialAccount


class XAccountTestBase(MailCommon):
    """Shared test base for the x_account module.

    Patching `_compute_statistics` prevents `social_twitter` from making real
    HTTP calls to api.twitter.com (both `_get_account_stats` and
    `_get_last_tweets_stats`) whenever a social.account is created. This is the
    same Odoo-19 "external requests verboten" test-framework guard that the
    ai_whatsapp suite handles by mocking `_send_message`.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # social_twitter issues real HTTP calls when a social.account is created:
        #   - _compute_statistics -> _get_account_stats + _get_last_tweets_stats
        #   - create() -> _create_default_stream_twitter() -> social.stream
        #     creation -> _fetch_tweets (real api.twitter.com call)
        # Patching the concrete methods (not _compute_statistics, which is a
        # stored-field compute and is not reliably intercepted) blocks them all.
        cls._stats_patcher = patch.object(
            TwitterSocialAccount, '_get_account_stats', return_value={})
        cls._stats_patcher.start()
        cls._tweets_patcher = patch.object(
            TwitterSocialAccount, '_get_last_tweets_stats', return_value={})
        cls._tweets_patcher.start()
        cls._stream_patcher = patch.object(
            TwitterSocialAccount, '_create_default_stream_twitter')
        cls._stream_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls._stream_patcher.stop()
        cls._tweets_patcher.stop()
        cls._stats_patcher.stop()
        super().tearDownClass()
