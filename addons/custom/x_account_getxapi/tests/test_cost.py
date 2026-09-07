from odoo.tests import tagged

from odoo.addons.x_account_getxapi.services.getxapi_cost import estimate_cost, ENDPOINT_COSTS

from .common import XAccountGetXAPITestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPICost(XAccountGetXAPITestBase):

    def test_retweet_cost_is_001(self):
        self.assertAlmostEqual(estimate_cost('twitter/tweet/retweet'), 0.001)

    def test_like_cost_is_001(self):
        self.assertAlmostEqual(estimate_cost('twitter/tweet/favorite'), 0.001)

    def test_create_tweet_cost_is_002(self):
        self.assertAlmostEqual(estimate_cost('twitter/tweet/create'), 0.002)

    def test_edit_tweet_cost_is_002(self):
        self.assertAlmostEqual(estimate_cost('twitter/tweet/edit'), 0.002)

    def test_dm_send_cost_is_002(self):
        self.assertAlmostEqual(estimate_cost('twitter/dm/send'), 0.002)

    def test_dm_list_cost_is_002(self):
        self.assertAlmostEqual(estimate_cost('twitter/dm/list'), 0.002)

    def test_thread_cost_is_005(self):
        self.assertAlmostEqual(estimate_cost('twitter/tweet/thread'), 0.005)

    def test_tweets_complete_cost_is_003(self):
        self.assertAlmostEqual(estimate_cost('twitter/user/tweets/complete'), 0.003)

    def test_tweet_detail_cost_is_001(self):
        self.assertAlmostEqual(estimate_cost('twitter/tweet/detail'), 0.001)

    def test_user_info_cost_is_001(self):
        self.assertAlmostEqual(estimate_cost('twitter/user/info'), 0.001)

    def test_media_upload_cost_is_001(self):
        self.assertAlmostEqual(estimate_cost('twitter/media/upload'), 0.001)

    def test_follow_cost_is_001(self):
        self.assertAlmostEqual(estimate_cost('twitter/user/follow'), 0.001)

    def test_unfollow_cost_is_001(self):
        self.assertAlmostEqual(estimate_cost('twitter/user/unfollow'), 0.001)

    def test_article_create_cost_is_010(self):
        self.assertAlmostEqual(estimate_cost('twitter/article/create'), 0.010)

    def test_article_get_cost_is_001(self):
        self.assertAlmostEqual(estimate_cost('twitter/article/get'), 0.001)

    def test_unknown_endpoint_returns_zero(self):
        self.assertAlmostEqual(estimate_cost('twitter/unknown/endpoint'), 0.0)

    def test_full_path_matches(self):
        self.assertAlmostEqual(estimate_cost('/twitter/tweet/retweet'), 0.001)

    def test_all_endpoints_have_positive_cost(self):
        for path, cost in ENDPOINT_COSTS.items():
            self.assertGreater(cost, 0, 'Endpoint %s has non-positive cost' % path)
