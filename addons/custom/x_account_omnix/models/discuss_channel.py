# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""OmniX webhook event routing into discuss channels.

The OmniX REST API delivers DM / tweet / follow events to the receiver route;
this model owns turning those raw events into ``x.message`` records + discuss
channels. It is OmniX-specific (the event shape and the conversation-id
conventions are OmniX's), so it lives here rather than in x_account.
"""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    @api.model
    def _handle_x_webhook_event(self, account, event):
        """Route an OmniX webhook event (message.*, tweet.*, user.follow) into
        x.message + discuss channels for the owning account."""
        self = self.sudo()
        if not account or not event:
            return False
        etype = event.get('type') or event.get('event') or ''
        _logger.info('Webhook event type=%r full=%r', etype, event)

        # Direct-message events: message.received / message.sent / edited / deleted
        if etype.startswith('message.'):
            conversation_id = event.get('conversation_id')
            if not conversation_id:
                return False
            channel = self._get_x_channel(
                account,
                conversation_id=conversation_id,
                channel_type='x_group' if str(conversation_id).startswith('g') else 'x',
                create_if_not_found=True,
            )
            author_x_id = event.get('sender_id')
            author_partner = False
            if author_x_id:
                author_partner = self.env['res.partner'].sudo().search(
                    [('x_user_id', '=', str(author_x_id))], limit=1)
                if not author_partner:
                    author_partner = self.env['res.partner'].sudo().create({
                        'name': author_x_id,
                        'x_user_id': str(author_x_id),
                    })
            if etype in ('message.received', 'message.sent'):
                return channel._save_x_message(
                    direction='inbound' if etype == 'message.received' else 'outbound',
                    external_id=event.get('message_id') or event.get('seq_id'),
                    body=event.get('text', ''),
                    external_created_at=event.get('created_at'),
                    author_partner=author_partner,
                    author_x_id=author_x_id,
                )
            return True

        # Tweet events: route into a channel keyed by the tweet conversation.
        if etype.startswith('tweet.'):
            conversation_id = event.get('conversationId') or event.get('conversation_id')
            author_x_id = event.get('author_id') or event.get('user_id')
            body = event.get('text', '') or event.get('message', '')
            tweet_id = event.get('tweet_id') or event.get('target_tweet_id')
            if not conversation_id and tweet_id:
                conversation_id = 'tweet-%s' % tweet_id
            if not conversation_id:
                return False
            channel = self._get_x_channel(
                account,
                conversation_id=str(conversation_id),
                channel_type='x',
                create_if_not_found=True,
            )
            return channel._save_x_message(
                direction='inbound',
                external_id=tweet_id or event.get('message_id'),
                body=body or '%s' % etype,
                external_created_at=event.get('created_at') or event.get('time'),
                author_x_id=author_x_id,
                author_x_username=event.get('author_screen_name'),
            )

        # user.follow events: OmniX delivers a batch of actors per delivery.
        # Store one x.message per actor (deduped by actor id).
        if etype == 'user.follow':
            actor_ids = event.get('actor_ids') or []
            actor_names = event.get('actor_screen_names') or []
            if actor_ids:
                for i, actor_id in enumerate(actor_ids):
                    name = actor_names[i] if i < len(actor_names) else actor_id
                    channel = self._get_x_channel(
                        account,
                        conversation_id='follow-%s' % actor_id,
                        channel_type='x',
                        create_if_not_found=True,
                    )
                    channel._save_x_message(
                        direction='inbound',
                        external_id='follow-%s' % actor_id,
                        body='Followed by: %s' % name,
                        external_created_at=event.get('time'),
                        author_x_id=actor_id,
                        author_x_username=name,
                    )
            return True

        return False
