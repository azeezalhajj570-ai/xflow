# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'X Account XActions Provider',
    'category': 'Marketing/Social Marketing',
    'summary': 'XActions REST provider (own posts + engagement) for the X Account platform',
    'version': '19.0.1.0.3',
    'author': 'Azeez Tech',
    'description': """
X Account XActions Provider
============================
Optional provider for the `x_account` platform that reads X data through a
running XActions API (the Node service at `http://xactions-api-1:3001`). It
implements the `XProvider` read contract and self-registers with
`XProviderRegistry` as `xactions`.

What it exposes:

- `fetch_user_posts` -> `POST /api/posts/report`: the account's own posts with
  metrics, and — because XActions returns them in the same report — the people
  behind each engagement type (likers, commenters, retweeters) inline.
- `fetch_post_comments` -> `GET /api/commenters`: the commenters of a post.
- `validate_session` -> `GET /api/user/me`.

Authentication is a Bearer JWT: either set a token, or an identifier/password
pair so the provider logs in and refreshes the token itself. Configuration
lives on res.config.settings (never in x.session.store).

Unlike GetXAPI, XActions *does* return likers, so the "X Posts" fetch stores
comment, retweet and like interactions as records.
    """,
    'depends': [
        'x_account',
    ],
    'data': [
        'views/res_config_settings_views.xml',
        'views/social_account_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'OEEL-1',
}
