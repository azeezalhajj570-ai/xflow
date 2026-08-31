import { ChatWindow } from "@mail/core/common/chat_window";
import { rpc } from "@web/core/network/rpc";
import { patch } from "@web/core/utils/patch";

patch(ChatWindow.prototype, {
    get showTakeOverButton() {
        const thread = this.props.chatWindow.thread;
        return thread.channel_type === 'whatsapp' && thread.current_handler_type && thread.current_handler_type !== 'human';
    },

    async takeOver(ev) {
        const thread = this.props.chatWindow.thread;
        if (thread.channel_type !== 'whatsapp') {
            return;
        }
        const result = await rpc("/ai_whatsapp/forward_operator", {
            channel_id: thread.id,
        });
        if (result.store_data) {
            this.store.insert(result.store_data);
        }
        if (result.notification) {
            this.store.env.services.notification.add(result.notification, { type: result.notification_type });
        }
    }
});
