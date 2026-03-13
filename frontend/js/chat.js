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
                const user = JSON.parse(localStorage.getItem('user') || '{}');
                const userId = Number(user.id) || 0;
                if (userId) {
                    Chat.socket.emit('join', { room: `user_${userId}`, token: localStorage.getItem('token') || '' });
                }
                if (Chat.currentRoom) {
                    Chat.socket.emit('join', { room: Chat.currentRoom, token: localStorage.getItem('token') || '' });
                }
            });

            Chat.socket.on('new_message', (msg) => {
                const sessionChatAll = document.querySelectorAll('#session-chat-messages');
                const sessionChat = Array.from(sessionChatAll).find(c => c.offsetParent !== null) || sessionChatAll[0] || null;
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

                const panel = document.getElementById('chat-panel');
                const panelMessages = document.getElementById('chat-messages');
                const panelType = panel?.dataset?.type || '';
                const panelCourtId = Number(panel?.dataset?.courtId) || 0;
                const panelSessionId = Number(panel?.dataset?.sessionId) || 0;
                const panelUserId = Number(panel?.dataset?.userId) || 0;

                if (panel && panel.style.display !== 'none' && panelMessages) {
                    if (panelType === 'court'
                        && msg.msg_type === 'court'
                        && Number(msg.court_id) === panelCourtId
                    ) {
                        Chat._appendRenderedMessage(panelMessages, Chat._renderMsg(msg), msg.id);
                    } else if (panelType === 'session'
                        && msg.msg_type === 'session'
                        && Number(msg.session_id) === panelSessionId
                    ) {
                        Chat._appendRenderedMessage(panelMessages, Sessions._renderChatMsg(msg), msg.id);
                    } else if (panelType === 'direct'
                        && msg.msg_type === 'direct'
                        && Chat._panelMatchesDirectMessage(msg, panelUserId)
                    ) {
                        Chat._appendRenderedMessage(panelMessages, Chat._renderMsg(msg), msg.id);
                    }
                }

                // Full-page court chat
                const fpChat = document.getElementById('court-chat-messages');
                if (fpChat && msg.msg_type === 'court') {
                    Chat._appendRenderedMessage(fpChat, MapView._renderChatMsg(msg), msg.id);
                }
                if (msg.msg_type === 'direct') {
                    Chat._refreshProfileThreads();
                }
                if ((msg.msg_type === 'direct' || msg.msg_type === 'session')
                    && typeof Inbox !== 'undefined') {
                    Inbox.refreshBadge();
                }
            });

            Chat.socket.on('presence_update', (data) => Chat._queuePresenceRefresh(data));
            Chat.socket.on('ranked_update', (data) => Chat._queueRankedRefresh(data));
            Chat.socket.on('notification_update', (data) => Chat._queueNotificationRefresh(data));
        } catch (err) {
            console.log('Socket.IO not available:', err);
        }
    },

    refreshAuth() {
        if (!Chat.socket) return;
        Chat.socket.auth = localStorage.getItem('token')
            ? { token: localStorage.getItem('token') }
            : {};
        if (Chat.socket.connected) {
            Chat.socket.disconnect();
        }
        Chat.socket.connect();
    },

    _refreshProfileThreads() {
        const threadsEl = document.getElementById('profile-message-threads');
        if (!threadsEl || typeof Profile === 'undefined' || typeof Profile._loadMessageThreads !== 'function') {
            return;
        }
        Profile._loadMessageThreads();
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
            if (typeof Inbox !== 'undefined') {
                const inboxDd = document.getElementById('inbox-dropdown');
                if (inboxDd && inboxDd.style.display === 'block') Inbox._loadThreads();
                else Inbox.refreshBadge();
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

    _panelMatchesDirectMessage(msg, otherUserId) {
        const targetUserId = Number(otherUserId) || 0;
        const currentUserId = Number(JSON.parse(localStorage.getItem('user') || '{}').id) || 0;
        if (!targetUserId || !currentUserId) return false;
        return (
            (Number(msg.sender_id) === targetUserId && Number(msg.recipient_id) === currentUserId)
            || (Number(msg.sender_id) === currentUserId && Number(msg.recipient_id) === targetUserId)
        );
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
        document.getElementById('chat-panel').style.display = 'flex';
        document.getElementById('chat-title').textContent = `💬 ${sessionTitle || 'Session Chat'}`;
        document.getElementById('chat-panel').dataset.sessionId = sessionId;
        document.getElementById('chat-panel').dataset.type = 'session';
        const container = document.getElementById('chat-messages');
        if (container) container.innerHTML = '<div class="loading">Loading messages...</div>';
        Sessions._loadSessionChat?.(sessionId);
        if (typeof Inbox !== 'undefined') Inbox.markRead('session', sessionId);
    },

    async _loadDirectMessages(userId) {
        const container = document.getElementById('chat-messages');
        const token = localStorage.getItem('token');
        if (!container) return;
        if (!token) { container.innerHTML = '<p class="muted">Sign in to chat</p>'; return; }
        try {
            const res = await API.get(`/api/chat/direct/${userId}`);
            container.innerHTML = (res.messages || []).map(m => Chat._renderMsg(m)).join('') || '<p class="muted">No messages yet</p>';
            Chat._trimRenderedMessages(container);
            container.scrollTop = container.scrollHeight;
        } catch (err) {
            container.innerHTML = `<p class="muted">${err.message || 'Unable to load messages'}</p>`;
        }
    },

    openDirect(userId, userDisplayName) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        if (Chat.currentRoom) {
            Chat.socket?.emit('leave', { room: Chat.currentRoom });
            Chat.currentRoom = null;
        }
        const panel = document.getElementById('chat-panel');
        panel.style.display = 'flex';
        panel.dataset.type = 'direct';
        panel.dataset.userId = String(userId);
        delete panel.dataset.courtId;
        delete panel.dataset.sessionId;
        document.getElementById('chat-title').textContent = `💬 ${userDisplayName || 'Direct Message'}`;
        Chat._loadDirectMessages(userId);
        if (typeof Inbox !== 'undefined') Inbox.markRead('direct', userId);
    },

    async openDirectByUser(userId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        const targetUserId = Number(userId) || 0;
        if (!targetUserId) return;
        try {
            const res = await API.get(`/api/auth/profile/${targetUserId}`);
            const user = res.user || {};
            Chat.openDirect(targetUserId, user.name || user.username || 'Direct Message');
        } catch (err) {
            App.toast(err.message || 'Unable to open direct messages', 'error');
        }
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
        if (type === 'direct') data.recipient_id = parseInt(panel.dataset.userId);

        try {
            await API.post('/api/chat/send', data);
            input.value = '';
            if (type === 'court') Chat._loadCourtMessages(parseInt(panel.dataset.courtId));
            if (type === 'direct') {
                Chat._loadDirectMessages(parseInt(panel.dataset.userId));
                Chat._refreshProfileThreads();
            }
        } catch { App.toast('Failed to send', 'error'); }
    },

    close() {
        const restoreRoom = (typeof App !== 'undefined'
            && App.currentScreen === 'court-details'
            && typeof MapView !== 'undefined'
            && MapView.currentCourtId)
            ? `court_${MapView.currentCourtId}`
            : ((typeof Sessions !== 'undefined' && Sessions.currentSessionId)
                ? `session_${Sessions.currentSessionId}`
                : '');
        document.getElementById('chat-panel').style.display = 'none';
        if (Chat.currentRoom) {
            Chat.socket?.emit('leave', { room: Chat.currentRoom });
            Chat.currentRoom = null;
        }
        const panel = document.getElementById('chat-panel');
        if (panel) {
            delete panel.dataset.type;
            delete panel.dataset.courtId;
            delete panel.dataset.sessionId;
            delete panel.dataset.userId;
        }
        if (restoreRoom && localStorage.getItem('token')) {
            Chat.joinRoom(restoreRoom);
        }
    },
};
