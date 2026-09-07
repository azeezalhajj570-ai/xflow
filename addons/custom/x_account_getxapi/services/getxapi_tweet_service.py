# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""GetXAPI tweet service: read and write operations on tweets.

Reads: search, detail, thread, replies, retweeters.
Writes: create, edit, like (favorite), retweet.

All methods return normalized DTOs via GetXAPIEnvelopeParser.
"""

from . import getxapi_envelope


class GetXAPITweetService:
    """Tweet operations over the GetXAPI REST API."""

    def __init__(self, client):
        self._client = client

    def search(self, query, **params):
        """Advanced tweet search.

        :param query: Search query string.
        :returns: {tweets: [...], cursor, has_more}
        """
        params = dict(params)
        params['query'] = query
        data = self._client.get('/twitter/tweet/advanced_search', params=params)
        return getxapi_envelope.GetXAPIEnvelopeParser.search_results(data)

    def detail(self, tweet_id):
        """Get a single tweet by ID.

        :param tweet_id: The tweet ID.
        :returns: Normalized tweet DTO.
        """
        data = self._client.get('/twitter/tweet/detail', params={'tweet_id': str(tweet_id)})
        return getxapi_envelope.GetXAPIEnvelopeParser.tweet(data)

    def thread(self, tweet_id):
        """Get the full thread containing a tweet.

        :param tweet_id: The tweet ID.
        :returns: {tweets: [...]}
        """
        data = self._client.get('/twitter/tweet/thread', params={'tweet_id': str(tweet_id)})
        return getxapi_envelope.GetXAPIEnvelopeParser.thread(data)

    def replies(self, tweet_id, **params):
        """Get replies to a tweet.

        :param tweet_id: The tweet ID.
        :returns: {tweets: [...], cursor, has_more}
        """
        params = dict(params)
        params['tweet_id'] = str(tweet_id)
        data = self._client.get('/twitter/tweet/replies', params=params)
        return getxapi_envelope.GetXAPIEnvelopeParser.search_results(data)

    def retweeters(self, tweet_id, **params):
        """Get users who retweeted a tweet.

        :param tweet_id: The tweet ID.
        :returns: {users: [...], cursor, has_more}
        """
        params = dict(params)
        params['tweet_id'] = str(tweet_id)
        data = self._client.get('/twitter/tweet/retweeters', params=params)
        return getxapi_envelope.GetXAPIEnvelopeParser.followers(data)

    def create(self, text, **kwargs):
        """Create a new tweet.

        :param text: Tweet text content.
        :param kwargs: Additional parameters (media_ids, reply_to, etc.).
        :returns: Normalized create-tweet result DTO.
        """
        body = {'text': text}
        body.update(kwargs)
        data = self._client.post('/twitter/tweet/create', json=body)
        return getxapi_envelope.GetXAPIEnvelopeParser.create_tweet_result(data)

    def edit(self, tweet_id, text, **kwargs):
        """Edit an existing tweet.

        :param tweet_id: The tweet ID to edit.
        :param text: New tweet text.
        :returns: Normalized create-tweet result DTO.
        """
        body = {'tweet_id': str(tweet_id), 'text': text}
        body.update(kwargs)
        data = self._client.post('/twitter/tweet/edit', json=body)
        return getxapi_envelope.GetXAPIEnvelopeParser.create_tweet_result(data)

    def like(self, tweet_id):
        """Like (favorite) a tweet.

        :param tweet_id: The tweet ID to like.
        :returns: Normalized like result DTO.
        """
        data = self._client.post('/twitter/tweet/favorite', json={'tweet_id': str(tweet_id)})
        return getxapi_envelope.GetXAPIEnvelopeParser.like_result(data, tweet_id)

    def retweet(self, tweet_id):
        """Retweet a tweet.

        :param tweet_id: The tweet ID to retweet.
        :returns: Normalized retweet result DTO.
        """
        data = self._client.post('/twitter/tweet/retweet', json={'tweet_id': str(tweet_id)})
        return getxapi_envelope.GetXAPIEnvelopeParser.retweet_result(data, tweet_id)
