# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'X Account Social Posts',
    'category': 'Marketing/Social Marketing',
    'summary': 'Fetch X account posts and their interactions into Social Marketing streams',
    'version': '19.0.1.0.1',
    'author': 'Azeez Tech',
    'description': """
X Account Social Posts
======================
Fetches an X account's own posts into the Odoo Social Marketing feed
(``social.stream`` / ``social.stream.post``) and stores each post's
interactions — comments, retweets and likes — as related records
(``x.post.interaction``) carrying the engager's metadata.

Reads go through the provider-agnostic ``XProvider`` contract, so the feature
works for any provider the account uses (GetXAPI today, others as they
implement the read methods). Refreshes are manual (a wizard / button), which
keeps paid-per-call providers under the operator's control.

Note: X exposes no public "who liked this post" endpoint, so likes are stored
as the post's ``favorite_count``; the like-interaction hook exists for the day
a likers source is available.
    """,
    'depends': [
        'x_account',
        'social',
        'social_twitter',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/social_media_data.xml',
        'views/social_stream_post_views.xml',
        'views/x_post_interaction_views.xml',
        'views/social_account_views.xml',
        'views/x_post_fetch_wizard_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'OEEL-1',
}
