# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Centralized GetXAPI endpoint pricing table.

Every paid endpoint is listed here with its USD cost per call. The client
uses :func:`estimate_cost` to log usage; services never hard-code prices.
When GetXAPI changes pricing, only this file needs updating.
"""

ENDPOINT_COSTS = {
    'twitter/tweet/advanced_search': 0.001,
    'twitter/tweet/detail': 0.001,
    'twitter/tweet/thread': 0.005,
    'twitter/tweet/replies': 0.001,
    'twitter/tweet/retweeters': 0.001,
    'twitter/tweet/create': 0.002,
    'twitter/tweet/edit': 0.002,
    'twitter/tweet/favorite': 0.001,
    'twitter/tweet/retweet': 0.001,
    'twitter/tweet/bookmark': 0.001,
    'twitter/tweet/unbookmark': 0.001,
    'twitter/user/search': 0.001,
    'twitter/user/status': 0.001,
    'twitter/user/info': 0.001,
    'twitter/user/info_by_id': 0.001,
    'twitter/user/user_about': 0.001,
    'twitter/user/tweets': 0.001,
    'twitter/user/tweets_and_replies': 0.001,
    'twitter/user/tweets/complete': 0.003,
    'twitter/user/media': 0.001,
    'twitter/user/likes': 0.001,
    'twitter/user/followers': 0.001,
    'twitter/user/followers_v2': 0.001,
    'twitter/user/following': 0.001,
    'twitter/user/following_v2': 0.001,
    'twitter/user/verified_followers': 0.001,
    'twitter/user/followers_you_know': 0.001,
    'twitter/user/check_follow_relationship': 0.001,
    'twitter/user/follow': 0.001,
    'twitter/user/unfollow': 0.001,
    'twitter/user/home_timeline': 0.001,
    'twitter/user/bookmark_search': 0.001,
    'twitter/user/affiliates': 0.001,
    'twitter/dm/send': 0.002,
    'twitter/dm/list': 0.002,
    'twitter/media/upload': 0.001,
    'twitter/article/get': 0.001,
    'twitter/article/create': 0.010,
    'twitter/article/update': 0.005,
    'twitter/article/list': 0.005,
    'twitter/article/publish': 0.005,
    'twitter/article/unpublish': 0.005,
    'twitter/article/delete': 0.005,
}


def estimate_cost(path):
    """Return the estimated USD cost for a given API path, or 0.0 if unknown.

    Matches by substring containment, preferring longer (more specific) matches.
    """
    best_match = None
    best_length = 0
    for prefix, cost in ENDPOINT_COSTS.items():
        if prefix in path and len(prefix) > best_length:
            best_match = cost
            best_length = len(prefix)
    return best_match if best_match is not None else 0.0
