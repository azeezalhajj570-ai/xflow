# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""GetXAPI user service: user info, search, tweets, followers, follow/unfollow.

All methods return normalized DTOs via GetXAPIEnvelopeParser.
"""

from . import getxapi_envelope


class GetXAPIUserService:
    """User operations over the GetXAPI REST API."""

    def __init__(self, client):
        self._client = client

    def info(self, username):
        """Get user info by username/handle.

        :param username: X username (with or without @).
        :returns: Normalized user DTO.
        """
        username = str(username).lstrip('@')
        data = self._client.get('/twitter/user/info', params={'userName': username})
        return getxapi_envelope.GetXAPIEnvelopeParser.user(data)

    def info_by_id(self, user_id):
        """Get user info by numeric user ID.

        :param user_id: X user ID.
        :returns: Normalized user DTO.
        """
        data = self._client.get('/twitter/user/info_by_id', params={'userId': str(user_id)})
        return getxapi_envelope.GetXAPIEnvelopeParser.user(data)

    def search(self, query, **params):
        """Search for users.

        :param query: Search query string.
        :returns: {users: [...], cursor, has_more}
        """
        params = dict(params)
        params['query'] = query
        data = self._client.get('/twitter/user/search', params=params)
        return getxapi_envelope.GetXAPIEnvelopeParser.followers(data)

    def tweets(self, username, **params):
        """Get a user's tweets.

        :param username: X username.
        :returns: {tweets: [...], cursor, has_more}
        """
        params = dict(params)
        params['userName'] = str(username).lstrip('@')
        data = self._client.get('/twitter/user/tweets', params=params)
        return getxapi_envelope.GetXAPIEnvelopeParser.search_results(data)

    def followers(self, username, **params):
        """Get a user's followers.

        :param username: X username.
        :returns: {users: [...], cursor, has_more}
        """
        params = dict(params)
        params['userName'] = str(username).lstrip('@')
        data = self._client.get('/twitter/user/followers', params=params)
        return getxapi_envelope.GetXAPIEnvelopeParser.followers(data)

    def following(self, username, **params):
        """Get users that a user is following.

        :param username: X username.
        :returns: {users: [...], cursor, has_more}
        """
        params = dict(params)
        params['userName'] = str(username).lstrip('@')
        data = self._client.get('/twitter/user/following', params=params)
        return getxapi_envelope.GetXAPIEnvelopeParser.followers(data)

    def follow(self, username):
        """Follow a user.

        :param username: X username to follow.
        :returns: Normalized follow result DTO.
        """
        username = str(username).lstrip('@')
        data = self._client.post('/twitter/user/follow', json={'userName': username})
        return getxapi_envelope.GetXAPIEnvelopeParser.follow_result(data)

    def unfollow(self, username):
        """Unfollow a user.

        :param username: X username to unfollow.
        :returns: Normalized unfollow result DTO.
        """
        username = str(username).lstrip('@')
        data = self._client.post('/twitter/user/unfollow', json={'userName': username})
        return getxapi_envelope.GetXAPIEnvelopeParser.unfollow_result(data)
