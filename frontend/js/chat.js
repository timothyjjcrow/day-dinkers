/**
 * Chat module — Socket.IO real-time messaging + session/court chat.
 */
const Chat = {
    socket: null,
    currentRoom: null,
    rankedRefreshTimer: null,
    presenceRefreshTimer: null,
    notificationRefreshTimer: null,
    MAX_RENDERED_MESSAGES: 120,
    SCROLL_BOTTOM_THRESHOLD_PX: 48,

    init() {
        try {
            const token = localStorage.getItem('token');
            Chat.socket = io({
                transports: ['websocket', 'polling'],
                auth: token ? { token } : {},
            });

            Chat.socket.on('connect', () => {
                console.log('Socket connected');
            });

            Chat.socket.on('new_message', (msg) => {
                // Session detail chat is scoped to the active session room.
                const sessionChat = document.getElementById('session-chat-messages');
                const inActiveSession = typeof Sessions !== 'undefined'
                    && Sessions.currentSessionId
                    && Number(msg.session_id) === Number(Sessions.currentSessionId);
                if (sessionChat && msg.msg_type === 'session' && inActiveSession) {
                    Chat._appendRenderedMessage(
                        sessionChat,
                        Sessions._renderChatMsg(msg),
                        msg.id,
                    );
                }

                // Court chat panel (bottom popup)
                const courtChat = document.getElementById('chat-messages');
                if (courtChat && courtChat.style.display !== 'none' && msg.msg_type === 'court') {
                    Chat._appendRenderedMessage(courtChat, Chat._renderMsg(msg), msg.id);
                }

                // Full-page court chat
                const fpChat = document.getElementById('fullpage-chat-messages');
                if (fpChat && msg.msg_type === 'court') {
                    Chat._appendRenderedMessage(fpChat, MapView._renderChatMsg(msg), msg.id);
                }
            });

            Chat.socket.on('presence_update', (data) => Chat._queuePresenceRefresh(data));
            Chat.socket.on('ranked_update', (data) => Chat._queueRankedRefresh(data));
            Chat.socket.on('notification_update', (data) => Chat._queueNotificationRefresh(data));
        } catch (err) {
            console.log('Socket.IO not available:', err);
        }
    },

    _queuePresenceRefresh(data = {}) {
        if (Chat.presenceRefreshTimer) clearTimeout(Chat.presenceRefreshTimer);
        Chat.presenceRefreshTimer = setTimeout(() => {
            const courtId = Number(data.court_id) || null;
            const currentView = (typeof App !== 'undefined' && App.currentView) ? App.currentView : '';
            if (typeof MapView !== 'undefined') {
                if (currentView === 'map' && typeof MapView.loadCourts === 'function') {
                    MapView.loadCourts();
                }

                const panel = document.getElementById('court-panel');
                const panelOpen = !!(panel && panel.style.display !== 'none' && MapView.currentCourtId);
                const fullPageOpen = currentView === 'court-detail' && MapView.currentCourtId;
                const shouldRefreshCurrentCourt = !courtId || courtId === MapView.currentCourtId;

                if ((fullPageOpen || panelOpen)
                    && shouldRefreshCurrentCourt
                    && typeof MapView.refreshCurrentCourtLiveData === 'function'
                ) {
                    MapView.refreshCurrentCourtLiveData(MapView.currentCourtId);
                }
            }
            if (typeof Ranked !== 'undefined'
                && Ranked.currentCourtId
                && (!courtId || courtId === Ranked.currentCourtId)
            ) {
                Ranked.loadCourtRanked(Ranked.currentCourtId, { silent: true });
            }
        }, 250);
    },

    _queueRankedRefresh(data = {}) {
        if (Chat.rankedRefreshTimer) clearTimeout(Chat.rankedRefreshTimer);
        Chat.rankedRefreshTimer = setTimeout(() => {
            const courtId = Number(data.court_id) || null;
            const currentView = (typeof App !== 'undefined' && App.currentView) ? App.currentView : '';
            if (typeof Ranked !== 'undefined') {
                if (Ranked.currentCourtId && (!courtId || Ranked.currentCourtId === courtId)) {
                    Ranked.loadCourtRanked(Ranked.currentCourtId, { silent: true });
                }

                if (currentView === 'ranked') {
                    const select = document.getElementById('ranked-court-filter');
                    const selectedCourtId = select && select.value
                        ? parseInt(select.value, 10)
                        : null;
                    Ranked.loadPendingConfirmations();
                    Ranked.loadLeaderboard(selectedCourtId || null, { silent: true });
                    Ranked.loadMatchHistory(null, selectedCourtId || null, { silent: true });
                }
            }

            if (typeof MapView !== 'undefined' && MapView.currentCourtId) {
                const panel = document.getElementById('court-panel');
                const panelOpen = !!(panel && panel.style.display !== 'none' && MapView.currentCourtId);
                const fullPageOpen = currentView === 'court-detail' && MapView.currentCourtId;
                const shouldRefreshCurrentCourt = !courtId || courtId === MapView.currentCourtId;
                if ((fullPageOpen || panelOpen)
                    && shouldRefreshCurrentCourt
                    && typeof MapView.refreshCurrentCourtLiveData === 'function'
                ) {
                    MapView.refreshCurrentCourtLiveData(MapView.currentCourtId);
                }
            }

            if (typeof App !== 'undefined' && typeof App.refreshNotificationBadge === 'function') {
                App.refreshNotificationBadge();
            }
        }, 250);
    },

    _queueNotificationRefresh() {
        if (Chat.notificationRefreshTimer) clearTimeout(Chat.notificationRefreshTimer);
        Chat.notificationRefreshTimer = setTimeout(() => {
            const dropdown = document.getElementById('notifications-dropdown');
            const open = dropdown && dropdown.style.display === 'block';
            if (open && typeof App !== 'undefined' && typeof App._loadNotifications === 'function') {
                App._loadNotifications();
            } else if (typeof App !== 'undefined' && typeof App.refreshNotificationBadge === 'function') {
                App.refreshNotificationBadge();
            }
            if (typeof App !== 'undefined' && App.currentView === 'ranked' && typeof Ranked !== 'undefined') {
                Ranked.loadPendingConfirmations();
            }
        }, 150);
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

    _isNearBottom(container) {
        const delta = container.scrollHeight - (container.scrollTop + container.clientHeight);
        return delta <= Chat.SCROLL_BOTTOM_THRESHOLD_PX;
    },

    _removePlaceholder(container) {
        const first = container.firstElementChild;
        if (!first) return;
        if (first.tagName === 'P' && first.classList.contains('muted')) {
            first.remove();
        }
    },

    _trimRenderedMessages(container) {
        const messages = container.querySelectorAll('.chat-msg');
        const overflow = messages.length - Chat.MAX_RENDERED_MESSAGES;
        if (overflow <= 0) return;
        for (let i = 0; i < overflow; i += 1) {
            messages[i].remove();
        }
    },

    _appendRenderedMessage(container, html, messageId = null) {
        if (!container) return;
        const safeId = Number(messageId);
        if (Number.isFinite(safeId) && container.querySelector(`[data-msg-id="${safeId}"]`)) {
            return;
        }
        const stickToBottom = Chat._isNearBottom(container);
        Chat._removePlaceholder(container);
        container.insertAdjacentHTML('beforeend', html);
        Chat._trimRenderedMessages(container);
        if (stickToBottom) {
            container.scrollTop = container.scrollHeight;
        }
    },

    joinRoom(room) {
        const token = localStorage.getItem('token');
        if (Chat.currentRoom) {
            Chat.socket?.emit('leave', { room: Chat.currentRoom });
        }
        Chat.currentRoom = room;
        Chat.socket?.emit('join', { room, token });
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
            Chat._trimRenderedMessages(container);
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
