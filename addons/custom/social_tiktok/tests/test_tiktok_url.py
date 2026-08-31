from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTikTokURL(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tiktok_media = cls.env.ref('social_tiktok.social_media_tiktok')

    def test_stats_link_with_handle(self):
        account = self.env['social.account'].create({
            'name': 'TikTok Account',
            'media_id': self.tiktok_media.id,
            'social_account_handle': 'theazeeztech',
        })
        account._compute_stats_link()
        self.assertEqual(
            account.stats_link,
            'https://www.tiktok.com/@theazeeztech',
        )

    def test_stats_link_without_handle_fallback(self):
        account = self.env['social.account'].create({
            'name': 'TikTok Account',
            'media_id': self.tiktok_media.id,
        })
        account._compute_stats_link()
        self.assertEqual(
            account.stats_link,
            'https://www.tiktok.com/@TikTok Account',
        )

    def test_stats_link_no_name(self):
        account = self.env['social.account'].create({
            'name': 'Test User',
            'media_id': self.tiktok_media.id,
        })
        account.social_account_handle = False
        account.name = False
        account._compute_stats_link()
        self.assertFalse(account.stats_link)

    def test_author_link_with_handle(self):
        account = self.env['social.account'].create({
            'name': 'TikTok Account',
            'media_id': self.tiktok_media.id,
            'social_account_handle': 'theazeeztech',
        })
        stream = self.env['social.stream'].create({
            'name': 'Test Stream',
            'media_id': self.tiktok_media.id,
            'account_id': account.id,
            'stream_type_id': self.env.ref('social_tiktok.stream_type_user_videos').id,
        })
        post = self.env['social.stream.post'].create({
            'stream_id': stream.id,
            'message': 'Test video',
            'author_name': 'TikTok Account',
            'tiktok_video_id': '12345',
        })
        post._compute_author_link()
        self.assertEqual(
            post.author_link,
            'https://www.tiktok.com/@theazeeztech',
        )

    def test_author_link_without_handle_fallback(self):
        account = self.env['social.account'].create({
            'name': 'TikTok Account',
            'media_id': self.tiktok_media.id,
        })
        stream = self.env['social.stream'].create({
            'name': 'Test Stream',
            'media_id': self.tiktok_media.id,
            'account_id': account.id,
            'stream_type_id': self.env.ref('social_tiktok.stream_type_user_videos').id,
        })
        post = self.env['social.stream.post'].create({
            'stream_id': stream.id,
            'message': 'Test video',
            'author_name': 'TikTok Account',
            'tiktok_video_id': '12345',
        })
        post._compute_author_link()
        self.assertEqual(
            post.author_link,
            'https://www.tiktok.com/@TikTok Account',
        )

    def test_post_link_with_handle(self):
        account = self.env['social.account'].create({
            'name': 'TikTok Account',
            'media_id': self.tiktok_media.id,
            'social_account_handle': 'theazeeztech',
        })
        stream = self.env['social.stream'].create({
            'name': 'Test Stream',
            'media_id': self.tiktok_media.id,
            'account_id': account.id,
            'stream_type_id': self.env.ref('social_tiktok.stream_type_user_videos').id,
        })
        post = self.env['social.stream.post'].create({
            'stream_id': stream.id,
            'message': 'Test video',
            'author_name': 'TikTok Account',
            'tiktok_video_id': '12345',
        })
        post._compute_post_link()
        self.assertEqual(
            post.post_link,
            'https://www.tiktok.com/@theazeeztech/video/12345',
        )

    def test_post_link_without_handle_fallback(self):
        account = self.env['social.account'].create({
            'name': 'TikTok Account',
            'media_id': self.tiktok_media.id,
        })
        stream = self.env['social.stream'].create({
            'name': 'Test Stream',
            'media_id': self.tiktok_media.id,
            'account_id': account.id,
            'stream_type_id': self.env.ref('social_tiktok.stream_type_user_videos').id,
        })
        post = self.env['social.stream.post'].create({
            'stream_id': stream.id,
            'message': 'Test video',
            'author_name': 'TikTok Account',
            'tiktok_video_id': '12345',
        })
        post._compute_post_link()
        self.assertEqual(
            post.post_link,
            'https://www.tiktok.com/@TikTok Account/video/12345',
        )
