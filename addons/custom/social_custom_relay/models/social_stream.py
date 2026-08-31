
from odoo import models


class SocialStreamCustomRelay(models.Model):
    _inherit = 'social.stream'

    def _fetch_stream_data(self):
        if self.stream_type_id.stream_type != 'linkedin_company_post':
            return super()._fetch_stream_data()
        if not self.account_id.linkedin_account_urn:
            return super()._fetch_stream_data()

        if 'organization' not in (self.account_id.linkedin_account_urn or ''):
            return True

        return super()._fetch_stream_data()


class SocialStreamPostCustomRelay(models.Model):
    _inherit = 'social.stream.post'

    def _compute_author_link(self):
        linkedin_posts = self._filter_by_media_types(['linkedin'])
        super(SocialStreamPostCustomRelay, self - linkedin_posts)._compute_author_link()
        for post in linkedin_posts:
            if post.linkedin_author_urn:
                if 'person' in (post.linkedin_author_urn or ''):
                    post.author_link = 'https://www.linkedin.com/in/%s' % post.linkedin_author_id
                else:
                    post.author_link = 'https://linkedin.com/company/%s' % post.linkedin_author_id
            else:
                post.author_link = False
