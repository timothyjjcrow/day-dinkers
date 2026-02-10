/**
 * Main application controller — routing, navigation, toast, init.
 */
const App = {
    currentView: 'map',
    friendIds: [],
    friendsList: [],
    locationPrefKey: 'location_tracking_pref',
    // Legacy name retained to avoid broader refactors; this now means admin access.
    isReviewer: false,

    async loadFriendsCache() {
        const token = localStorage.getItem('token');
        if (!token) { App.friendIds = []; App.friendsList = []; return; }
        try {
            const res = await API.get('/api/auth/friends');
            App.friendsList = res.friends || [];
            App.friendIds = App.friendsList.map(f => f.id);
        } catch {
            App.friendIds = [];
            App.friendsList = [];
        }
    },

    showInviteModal(title, onSend) {
        const modal = document.getElementById('invite-modal');
        document.getElementById('invite-modal-title').textContent = title;
        const container = document.getElementById('invite-modal-friends');
        const sendBtn = document.getElementById('invite-modal-send');

        if (App.friendsList.length === 0) {
            container.innerHTML = '<p class="muted">No friends yet. Add friends from player cards at courts or from your profile!</p>';
            sendBtn.style.display = 'none';
        } else {
            container.innerHTML = App.friendsList.map(f => `
                <label class="friend-pick-item">
                    <input type="checkbox" value="${f.id}">
                    <span class="friend-pick-avatar">${(f.name || f.username || '?')[0].toUpperCase()}</span>
                    <span class="friend-pick-name">${f.name || f.username}</span>
                </label>
            `).join('');
            sendBtn.style.display = 'block';
            sendBtn.onclick = () => {
                const selected = Array.from(container.querySelectorAll('input:checked')).map(cb => parseInt(cb.value));
                if (selected.length === 0) { App.toast('Select at least one friend', 'error'); return; }
                onSend(selected);
            };
        }
        modal.style.display = 'flex';
    },

    hideInviteModal() {
        document.getElementById('invite-modal').style.display = 'none';
    },

    init() {
        // Always start on the map view
        App.showView('map');

        MapView.init();
        Chat.init();
        window.addEventListener('resize', () => App.updateTopLayoutOffset());

        // Check auth (async) — validates token, clears stale sessions
        Auth.checkAuth();
        App.refreshReviewerAccess();

        // Location tracking is opt-in; do not auto-request geolocation on app load.
        App.initLocationPrompt();
        App.updateTopLayoutOffset();

        // Mobile search handler (shown on small screens when nav search is hidden)
        const mobileSearchInput = document.getElementById('mobile-search-input');
        if (mobileSearchInput) {
            let mobileSearchTimeout;
            mobileSearchInput.addEventListener('input', () => {
                clearTimeout(mobileSearchTimeout);
                mobileSearchTimeout = setTimeout(() => {
                    MapView.search(mobileSearchInput.value);
                    // Sync with desktop search bar
                    const desktop = document.getElementById('search-input');
                    if (desktop) desktop.value = mobileSearchInput.value;
                }, 400);
            });
        }

        // Refresh court data every 30s for live player counts
        App.refreshInterval = setInterval(() => {
            if (App.currentView === 'map') MapView.loadCourts();
        }, 30000);
    },

    initLocationPrompt() {
        const pref = localStorage.getItem(App.locationPrefKey);
        const banner = document.getElementById('location-consent-banner');
        if (!banner) return;

        if (pref === 'enabled') {
            banner.style.display = 'none';
            LocationService.start();
            App.updateTopLayoutOffset();
            return;
        }
        if (pref === 'disabled') {
            banner.style.display = 'none';
            App.updateTopLayoutOffset();
            return;
        }
        banner.style.display = 'flex';
        App.updateTopLayoutOffset();
    },

    enableLocationTracking() {
        localStorage.setItem(App.locationPrefKey, 'enabled');
        const banner = document.getElementById('location-consent-banner');
        if (banner) banner.style.display = 'none';
        App.updateTopLayoutOffset();
        LocationService.start();
        App.toast('Location enabled. Nearby court and auto check-in are now active.');
    },

    dismissLocationPrompt() {
        localStorage.setItem(App.locationPrefKey, 'disabled');
        const banner = document.getElementById('location-consent-banner');
        if (banner) banner.style.display = 'none';
        App.updateTopLayoutOffset();
    },


    _visibleHeight(el) {
        if (!el) return 0;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return 0;
        return Math.ceil(el.getBoundingClientRect().height || 0);
    },

    updateTopLayoutOffset() {
        window.requestAnimationFrame(() => {
            const consentBanner = document.getElementById('location-consent-banner');
            const locationBanner = document.getElementById('location-banner');
            const consentHeight = App._visibleHeight(consentBanner);
            const locationHeight = App._visibleHeight(locationBanner);
            const total = consentHeight + locationHeight;

            const root = document.documentElement;
            root.style.setProperty('--location-consent-offset', `${consentHeight}px`);
            root.style.setProperty('--location-banner-offset', `${locationHeight}px`);
            root.style.setProperty('--top-banner-offset', `${total}px`);

            if (App.currentView === 'map' && typeof MapView !== 'undefined' && MapView.map) {
                MapView.map.invalidateSize();
            }
        });
    },

    showView(view) {
        document.getElementById('map-container').style.display = view === 'map' ? 'block' : 'none';
        document.getElementById('sessions-view').style.display = view === 'sessions' ? 'block' : 'none';
        document.getElementById('profile-view').style.display = view === 'profile' ? 'block' : 'none';
        document.getElementById('ranked-view').style.display = view === 'ranked' ? 'block' : 'none';
        document.getElementById('admin-view').style.display = view === 'admin' ? 'block' : 'none';
        document.getElementById('court-fullpage-view').style.display = view === 'court-detail' ? 'block' : 'none';

        // Close side panel when switching away (except when expanding to full page)
        if (view !== 'court-detail') App.closePanel();
        else App.closePanel(); // Also close panel when going full-page (we're replacing it)

        document.getElementById('notifications-dropdown').style.display = 'none';

        App.currentView = view;

        // Highlight active nav
        document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));

        if (view === 'sessions') {
            document.getElementById('btn-sessions').classList.add('active');
            Sessions.load();
        }
        if (view === 'profile') {
            document.getElementById('btn-profile').classList.add('active');
            Profile.load();
            setTimeout(() => Profile.loadExtras(), 300);
        }
        if (view === 'ranked') {
            document.getElementById('btn-ranked').classList.add('active');
            Ranked.loadPendingConfirmations();
            Ranked.loadLeaderboard();
            Ranked.loadMatchHistory();
            App._populateRankedCourtFilter();
        }
        if (view === 'admin') {
            if (!App.isReviewer) {
                App.toast('Admin access required', 'error');
                App.showView('map');
                return;
            }
            document.getElementById('btn-admin').classList.add('active');
            AdminPage.load();
        }
        if (view === 'map') {
            setTimeout(() => MapView.map.invalidateSize(), 100);
        }
    },

    async refreshReviewerAccess() {
        const token = localStorage.getItem('token');
        const adminBtn = document.getElementById('btn-admin');
        if (!token) {
            App.isReviewer = false;
            if (adminBtn) adminBtn.style.display = 'none';
            return;
        }

        let isAdmin = false;
        try {
            const res = await API.get('/api/courts/updates/admin-status');
            isAdmin = !!(res.is_admin ?? res.is_reviewer);
        } catch {
            // Backward compatibility: keep working if only the old endpoint exists.
            try {
                const fallback = await API.get('/api/courts/updates/reviewer-status');
                isAdmin = !!(fallback.is_admin ?? fallback.is_reviewer);
            } catch {
                isAdmin = false;
            }
        }
        App.isReviewer = isAdmin;
        if (adminBtn) adminBtn.style.display = App.isReviewer ? 'inline-flex' : 'none';
        if (!App.isReviewer && App.currentView === 'admin') App.showView('map');
    },

    async _populateRankedCourtFilter() {
        const select = document.getElementById('ranked-court-filter');
        if (!select || select.options.length > 1) return;
        try {
            const res = await API.get('/api/courts');
            (res.courts || []).forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.id;
                opt.textContent = `${c.name} — ${c.city}`;
                select.appendChild(opt);
            });
        } catch {}
    },

    closePanel() {
        document.getElementById('court-panel').style.display = 'none';
        // Clear content so stale IDs don't conflict with full-page view
        document.getElementById('court-detail').innerHTML = '';
    },

    toggleNotifications() {
        const dd = document.getElementById('notifications-dropdown');
        dd.style.display = dd.style.display === 'none' ? 'block' : 'none';
        if (dd.style.display === 'block') App._loadNotifications();
    },

    async _loadNotifications() {
        const list = document.getElementById('notifications-list');
        const token = localStorage.getItem('token');
        if (!token) { list.innerHTML = '<p class="muted">Sign in to see notifications</p>'; return; }

        try {
            const res = await API.get('/api/auth/notifications');
            const notifs = res.notifications || [];
            if (!notifs.length) {
                list.innerHTML = '<p class="muted">No notifications</p>';
                return;
            }
            list.innerHTML = notifs.map(n => {
                const isSessionNotif = ['session_invite', 'session_join', 'session_spot_opened'].includes(n.notif_type) && n.reference_id;
                const isCourtNotif = n.notif_type === 'court_invite' && n.reference_id;
                const isFriendNotif = n.notif_type === 'friend_request' && n.reference_id;
                const isMatchNotif = ['match_confirm', 'match_rejected', 'match_result', 'elo_change'].includes(n.notif_type) && n.reference_id;
                const isAdminNotif = ['court_update_review', 'court_report_review'].includes(n.notif_type);
                let onclick = '';
                if (isSessionNotif) {
                    onclick = `onclick="App.showView('sessions'); document.getElementById('notifications-dropdown').style.display='none'; setTimeout(() => Sessions.openDetail(${n.reference_id}), 200);"`;
                }
                else if (isCourtNotif) onclick = `onclick="MapView.openCourtDetail(${n.reference_id}); App.showView('map'); document.getElementById('notifications-dropdown').style.display='none';"`;
                else if (isFriendNotif) onclick = `onclick="App.showView('profile'); document.getElementById('notifications-dropdown').style.display='none';"`;
                else if (isMatchNotif) {
                    if (['match_confirm', 'match_rejected'].includes(n.notif_type)) {
                        onclick = `onclick="App.showView('ranked'); document.getElementById('notifications-dropdown').style.display='none'; setTimeout(() => Ranked.focusPending(${n.reference_id}), 250);"`;
                    } else {
                        onclick = `onclick="App.showView('ranked'); document.getElementById('notifications-dropdown').style.display='none';"`;
                    }
                }
                else if (isAdminNotif) {
                    onclick = `onclick="App.showView('admin'); document.getElementById('notifications-dropdown').style.display='none';"`;
                }
                return `
                <div class="notif-item ${n.read ? '' : 'unread'}" ${onclick} style="${onclick ? 'cursor:pointer' : ''}">
                    <p>${n.content}</p>
                    <span class="notif-time">${new Date(n.created_at).toLocaleString()}</span>
                </div>`;
            }).join('');
            const unread = notifs.filter(n => !n.read).length;
            const badge = document.getElementById('notif-badge');
            if (unread > 0) { badge.textContent = unread; badge.style.display = 'inline'; }
            else badge.style.display = 'none';
        } catch { list.innerHTML = '<p class="muted">Unable to load</p>'; }
    },

    async markNotificationsRead() {
        try {
            await API.post('/api/auth/notifications/read', {});
            document.getElementById('notif-badge').style.display = 'none';
            App._loadNotifications();
        } catch {}
    },

    toggleProfile() {
        App.showView('profile');
    },

    toast(message, type = 'success') {
        const container = document.getElementById('toast-container');
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        container.appendChild(toast);
        setTimeout(() => toast.classList.add('show'), 10);
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    },
};

document.addEventListener('DOMContentLoaded', App.init);
