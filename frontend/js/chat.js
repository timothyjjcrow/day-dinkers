/**
 * Chat module — Socket.IO real-time messaging + session/court chat.
 */
const Chat = {
    socket: null,
    currentRoom: null,

    init() {
        try {
            Chat.socket = io({ transports: ['websocket', 'polling'] });

            Chat.socket.on('connect', () => {
                console.log('Socket connected');
            });

            Chat.socket.on('new_message', (msg) => {
                // Session detail chat uses the same underlying court room stream.
                const sessionChat = document.getElementById('session-chat-messages');
                const inSessionCourt = typeof Sessions !== 'undefined'
                    && Sessions.currentSessionCourtId
                    && msg.court_id === Sessions.currentSessionCourtId;
                if (sessionChat && msg.msg_type === 'court' && inSessionCourt) {
                    const existing = sessionChat.querySelector(`[data-msg-id="${msg.id}"]`);
                    if (!existing) {
                        sessionChat.insertAdjacentHTML('beforeend', Sessions._renderChatMsg(msg));
                        sessionChat.scrollTop = sessionChat.scrollHeight;
                    }
                }

                // Court chat panel (bottom popup)
                const courtChat = document.getElementById('chat-messages');
                if (courtChat && courtChat.style.display !== 'none' && msg.msg_type === 'court') {
                    courtChat.insertAdjacentHTML('beforeend', Chat._renderMsg(msg));
                    courtChat.scrollTop = courtChat.scrollHeight;
                }

                // Full-page court chat
                const fpChat = document.getElementById('fullpage-chat-messages');
                if (fpChat && msg.msg_type === 'court') {
                    fpChat.insertAdjacentHTML('beforeend', MapView._renderChatMsg(msg));
                    fpChat.scrollTop = fpChat.scrollHeight;
                }
            });

            Chat.socket.on('presence_update', (data) => {
                // Refresh court data when someone checks in/out
                if (typeof MapView !== 'undefined') {
                    MapView.loadCourts();
                    // Also refresh full-page court view if open
                    if (App.currentView === 'court-detail' && MapView.currentCourtId) {
                        MapView._refreshFullPage(MapView.currentCourtId);
                    }
                }
            });
        } catch (err) {
            console.log('Socket.IO not available:', err);
        }
    },

    _renderMsg(msg) {
        const sender = msg.sender || {};
        const time = new Date(msg.created_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const isMe = sender.id === currentUser.id;
        return `
        <div class="chat-msg ${isMe ? 'chat-msg-me' : ''}" data-msg-id="${msg.id}">
            <strong>${isMe ? 'You' : (sender.name || sender.username)}:</strong> ${msg.content}
            <span class="chat-msg-time">${time}</span>
        </div>`;
    },

    joinRoom(room) {
        if (Chat.currentRoom) {
            Chat.socket?.emit('leave', { room: Chat.currentRoom });
        }
        Chat.currentRoom = room;
        Chat.socket?.emit('join', { room });
    },

    // Open the bottom chat panel for a court
    openCourt(courtId, courtName) {
        Chat.joinRoom(`court_${courtId}`);
        document.getElementById('chat-panel').style.display = 'flex';
        document.getElementById('chat-title').textContent = `💬 ${courtName}`;
        document.getElementById('chat-panel').dataset.courtId = courtId;
        document.getElementById('chat-panel').dataset.type = 'court';
        Chat._loadCourtMessages(courtId);
    },

    // Open session chat
    openSession(sessionId, sessionTitle) {
        Chat.joinRoom(`session_${sessionId}`);
        // Could open a bottom panel or navigate to session view
        App.showView('sessions');
    },

    async _loadCourtMessages(courtId) {
        const container = document.getElementById('chat-messages');
        const token = localStorage.getItem('token');
        if (!token) { container.innerHTML = '<p class="muted">Sign in to chat</p>'; return; }
        try {
            const res = await API.get(`/api/chat/court/${courtId}`);
            container.innerHTML = (res.messages || []).map(m => Chat._renderMsg(m)).join('') || '<p class="muted">No messages yet</p>';
            container.scrollTop = container.scrollHeight;
        } catch { container.innerHTML = '<p class="muted">Unable to load messages</p>'; }
    },

    async send(e) {
        e.preventDefault();
        const input = document.getElementById('chat-input');
        const content = input.value.trim();
        if (!content) return;
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }

        const panel = document.getElementById('chat-panel');
        const type = panel.dataset.type;
        const data = { content, msg_type: type };
        if (type === 'court') data.court_id = parseInt(panel.dataset.courtId);
        if (type === 'session') data.session_id = parseInt(panel.dataset.sessionId);

        try {
            await API.post('/api/chat/send', data);
            input.value = '';
            if (type === 'court') Chat._loadCourtMessages(parseInt(panel.dataset.courtId));
        } catch { App.toast('Failed to send', 'error'); }
    },

    close() {
        document.getElementById('chat-panel').style.display = 'none';
        if (Chat.currentRoom) {
            Chat.socket?.emit('leave', { room: Chat.currentRoom });
            Chat.currentRoom = null;
        }
    },
};
