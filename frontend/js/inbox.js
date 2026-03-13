/**
 * Inbox module — unified message center for DMs and session chats.
 */
const Inbox = {
    currentFilter: 'all',
    pollTimer: null,
    POLL_INTERVAL_MS: 15000,

    _setBadge(count) {
        const badge = document.getElementById('inbox-badge');
        if (!badge) return;
        const n = Number(count) || 0;
        badge.textContent = n > 99 ? '99+' : String(n);
        badge.style.display = n > 0 ? 'inline' : 'none';
    },

    async refreshBadge() {
        const token = localStorage.getItem('token');
        if (!token) { Inbox._setBadge(0); return; }
        try {
            const res = await API.get('/api/chat/inbox/unread-count');
            Inbox._setBadge(res.unread_count || 0);
        } catch { /* silent */ }
    },

    toggle() {
        const dd = document.getElementById('inbox-dropdown');
        if (!dd) return;
        if (dd.style.display === 'block') {
            Inbox.hide();
            return;
        }
        if (typeof App !== 'undefined') App.hideNotifications();
        dd.style.display = 'block';
        Inbox._loadThreads();
    },

    hide() {
        const dd = document.getElementById('inbox-dropdown');
        if (dd) dd.style.display = 'none';
    },

    setFilter(filter) {
        Inbox.currentFilter = filter;
        document.querySelectorAll('.inbox-tab').forEach(btn =>
            btn.classList.toggle('active', btn.dataset.filter === filter)
        );
        Inbox._loadThreads();
    },

    async _loadThreads() {
        const list = document.getElementById('inbox-thread-list');
        if (!list) return;
        const token = localStorage.getItem('token');
        if (!token) {
            list.innerHTML = '<div class="dropdown-list-state">Sign in to see messages</div>';
            return;
        }
        list.innerHTML = '<div class="dropdown-list-state">Loading...</div>';
        try {
            const res = await API.get(`/api/chat/inbox?filter=${Inbox.currentFilter}`);
            const threads = res.threads || [];
            if (!threads.length) {
                list.innerHTML = '<div class="dropdown-list-state">No conversations yet</div>';
                return;
            }
            list.innerHTML = threads.map(t => Inbox._renderThread(t)).join('');
            Inbox._setBadge(res.total_unread || 0);
        } catch {
            list.innerHTML = '<div class="dropdown-list-state">Unable to load messages</div>';
        }
    },

    _renderThread(thread) {
        const name = Inbox._e(thread.name || 'Conversation');
        const preview = Inbox._e(thread.last_message_preview || '');
        const time = Inbox._relativeTime(thread.last_message_at);
        const unread = thread.unread_count || 0;
        const isSession = thread.thread_type === 'session';
        const initial = (thread.name || '?')[0].toUpperCase();
        const icon = isSession
            ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>'
            : `<span>${Inbox._e(initial)}</span>`;
        const unreadBadge = unread > 0
            ? `<span class="inbox-thread-unread">${unread > 99 ? '99+' : unread}</span>` : '';

        const onclick = isSession
            ? `Inbox.openThread('session', ${thread.thread_ref_id}, '${Inbox._ea(thread.name)}')`
            : `Inbox.openThread('direct', ${thread.thread_ref_id}, '${Inbox._ea(thread.name)}')`;

        return `
        <button class="inbox-thread ${unread ? 'inbox-thread-unread-row' : ''}" onclick="${onclick}">
            <span class="inbox-thread-avatar ${isSession ? 'inbox-thread-avatar-session' : ''}">${icon}</span>
            <span class="inbox-thread-main">
                <span class="inbox-thread-name">${name}</span>
                <span class="inbox-thread-preview">${preview}</span>
            </span>
            <span class="inbox-thread-meta">
                <span class="inbox-thread-time">${Inbox._e(time)}</span>
                ${unreadBadge}
            </span>
        </button>`;
    },

    openThread(threadType, threadRefId, name) {
        Inbox.hide();
        if (threadType === 'direct') {
            Chat.openDirect(threadRefId, name);
        } else if (threadType === 'session') {
            if (typeof Sessions !== 'undefined' && typeof Sessions.openDetail === 'function') {
                Sessions.openDetail(threadRefId);
            } else {
                Chat.openSession(threadRefId, name);
            }
        }
        Inbox.markRead(threadType, threadRefId);
    },

    async markRead(threadType, threadRefId) {
        if (!threadType || !threadRefId) return;
        try {
            await API.post('/api/chat/inbox/read', {
                thread_type: threadType,
                thread_ref_id: threadRefId,
            });
            Inbox.refreshBadge();
        } catch { /* silent */ }
    },

    startPolling() {
        if (Inbox.pollTimer) clearInterval(Inbox.pollTimer);
        Inbox.pollTimer = setInterval(() => Inbox.refreshBadge(), Inbox.POLL_INTERVAL_MS);
    },

    init() {
        Inbox.refreshBadge();
        Inbox.startPolling();
    },

    _e(val) {
        return String(val || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },

    _ea(val) {
        return Inbox._e(val).replace(/'/g, '\\&#39;');
    },

    _relativeTime(iso) {
        if (!iso) return '';
        const ms = Date.now() - new Date(iso).getTime();
        if (!Number.isFinite(ms) || ms < 0) return '';
        const min = Math.floor(ms / 60000);
        if (min < 1) return 'now';
        if (min < 60) return `${min}m`;
        const hr = Math.floor(min / 60);
        if (hr < 24) return `${hr}h`;
        return `${Math.floor(hr / 24)}d`;
    },
};
