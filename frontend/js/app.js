/**
 * Main application controller — routing, navigation, toast, init.
 */
const App = {
    currentView: 'map',
    friendIds: [],
    friendsList: [],
    locationPrefKey: 'location_tracking_pref',
    countyPrefKey: 'selected_county_slug',
    selectedCountySlug: 'humboldt',
    counties: [],
    // Legacy name retained to avoid broader refactors; this now means admin access.
    isReviewer: false,

    _normalizeCountySlug(value) {
        return String(value || '')
            .trim()
            .toLowerCase()
            .replace(/_/g, '-')
            .replace(/\s+/g, '-')
            .replace(/[^a-z0-9-]/g, '')
            .replace(/-+/g, '-')
            .replace(/^-|-$/g, '');
    },

    _countyNameFromSlug(slug) {
        const cleaned = App._normalizeCountySlug(slug) || 'humboldt';
        return cleaned
            .split('-')
            .filter(Boolean)
            .map(part => part.charAt(0).toUpperCase() + part.slice(1))
            .join(' ');
    },

    getSelectedCountySlug() {
        return App.selectedCountySlug || 'humboldt';
    },

    getSelectedCountyName() {
        const slug = App.getSelectedCountySlug();
        const match = App.counties.find(c => c.slug === slug);
        return match?.name || App._countyNameFromSlug(slug);
    },

    _updateRegionLabel() {
        const regionEl = document.querySelector('.nav-region');
        if (!regionEl || regionEl.classList.contains('near-court')) return;
        regionEl.textContent = `${App.getSelectedCountyName()} County`;
    },

    buildCourtsQuery(extraParams = {}) {
        const params = new URLSearchParams();
        const countySlug = App.getSelectedCountySlug();
        if (countySlug) params.set('county_slug', countySlug);
        Object.entries(extraParams || {}).forEach(([key, value]) => {
            if (value === undefined || value === null) return;
            const str = String(value);
            if (!str.trim()) return;
            params.set(key, str);
        });
        const query = params.toString();
        return query ? `/api/courts?${query}` : '/api/courts';
    },

    async initCountyPicker() {
        const select = document.getElementById('county-select');
        if (!select) return;
        select.innerHTML = '<option value="">Loading counties...</option>';

        let counties = [];
        try {
            const res = await API.get('/api/courts/counties');
            counties = res.counties || [];
            if (!App._normalizeCountySlug(localStorage.getItem(App.countyPrefKey))) {
                const defaultSlug = App._normalizeCountySlug(res.default_county_slug);
                if (defaultSlug) App.selectedCountySlug = defaultSlug;
            }
        } catch {
            counties = [];
        }

        if (!counties.length) {
            const fallbackSlug = App.getSelectedCountySlug();
            counties = [{ slug: fallbackSlug, name: App._countyNameFromSlug(fallbackSlug), court_count: 0 }];
        }

        App.counties = counties;
        select.innerHTML = counties.map(c =>
            `<option value="${c.slug}">${c.name} County (${c.court_count})</option>`
        ).join('');

        let selected = App.getSelectedCountySlug();
        if (!counties.some(c => c.slug === selected)) {
            selected = counties[0].slug;
        }
        App.setSelectedCounty(selected, {
            persist: true,
            reloadCourts: selected !== App.getSelectedCountySlug(),
            fitMap: true,
            showToast: false,
        });
        select.value = selected;
    },

    setSelectedCounty(rawSlug, { persist = true, reloadCourts = true, fitMap = false, showToast = false } = {}) {
        const normalized = App._normalizeCountySlug(rawSlug) || 'humboldt';
        const changed = normalized !== App.selectedCountySlug;
        App.selectedCountySlug = normalized;
        if (persist) localStorage.setItem(App.countyPrefKey, normalized);

        const select = document.getElementById('county-select');
        if (select && select.value !== normalized) select.value = normalized;
        App._updateRegionLabel();

        if (!changed) return;
        if (reloadCourts) {
            if (typeof MapView !== 'undefined' && typeof MapView.loadCourts === 'function') {
                MapView.loadCourts({ fitToCourts: fitMap });
            }
            if (typeof LocationService !== 'undefined' && typeof LocationService._refreshCourts === 'function') {
                LocationService._refreshCourts();
            }
            App._populateRankedCourtFilter(true);
        }
        if (showToast) {
            App.toast(`Switched to ${App.getSelectedCountyName()} County`);
        }
    },

    onCountyChange(countySlug) {
        App.setSelectedCounty(countySlug, {
            persist: true,
            reloadCourts: true,
            fitMap: true,
            showToast: true,
        });
    },

    useLocationCounty(options = {}) {
        const showToast = options.showToast !== false;
        const fitMap = options.fitMap !== false;
        const reloadCourts = options.reloadCourts !== false;

        if (!navigator.geolocation) {
            if (showToast) App.toast('Geolocation is not supported on this device.', 'error');
            return;
        }
        navigator.geolocation.getCurrentPosition(async (position) => {
            try {
                const { latitude, longitude } = position.coords;
                const res = await API.get(`/api/courts/resolve-county?lat=${latitude}&lng=${longitude}`);
                if (!res?.county_slug) {
                    if (showToast) App.toast('Could not determine your county from location.', 'error');
                    return;
                }
                App.setSelectedCounty(res.county_slug, {
                    persist: true,
                    reloadCourts,
                    fitMap,
                    showToast,
                });
            } catch (err) {
                if (showToast) App.toast(err.message || 'Could not determine county from location.', 'error');
            }
        }, () => {
            if (showToast) App.toast('Location permission denied. Please choose a county manually.', 'error');
        }, {
            enableHighAccuracy: false,
            timeout: 10000,
            maximumAge: 60000,
        });
    },

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

    _setNotificationBadge(unreadCount) {
        const badge = document.getElementById('notif-badge');
        if (!badge) return;
        const unread = Number(unreadCount) || 0;
        if (unread > 0) {
            badge.textContent = unread;
            badge.style.display = 'inline';
        } else {
            badge.style.display = 'none';
        }
    },

    async refreshNotificationBadge() {
        const token = localStorage.getItem('token');
        if (!token) {
            App._setNotificationBadge(0);
            return;
        }
        try {
            const res = await API.get('/api/auth/notifications');
            const notifs = res.notifications || [];
            const unread = notifs.filter(n => !n.read).length;
            App._setNotificationBadge(unread);
        } catch {}
    },

    startLiveUiRefresh() {
        if (App.liveUiRefreshInterval) {
            clearInterval(App.liveUiRefreshInterval);
            App.liveUiRefreshInterval = null;
        }
        App.liveUiRefreshInterval = setInterval(() => {
            App.refreshLiveUiNow();
        }, 6000);
    },

    refreshLiveUiNow() {
        const token = localStorage.getItem('token');
        if (!token) {
            App._setNotificationBadge(0);
            return;
        }

        App.refreshNotificationBadge();
        const panel = document.getElementById('court-panel');
        const panelOpen = !!(panel && panel.style.display !== 'none');
        const fullPageOpen = App.currentView === 'court-detail';

        if (typeof Ranked !== 'undefined') {
            if (App.currentView === 'ranked') {
                const select = document.getElementById('ranked-court-filter');
                const selectedCourtId = select && select.value
                    ? parseInt(select.value, 10)
                    : null;
                Ranked.loadPendingConfirmations();
                Ranked.loadLeaderboard(selectedCourtId || null, { silent: true });
                Ranked.loadMatchHistory(null, selectedCourtId || null, { silent: true });
            }
            if ((panelOpen || fullPageOpen) && Ranked.currentCourtId) {
                Ranked.loadCourtRanked(Ranked.currentCourtId, { silent: true });
            }
        }

        if (typeof MapView !== 'undefined' && MapView.currentCourtId) {
            if ((App.currentView === 'court-detail' || panelOpen)
                && typeof MapView.refreshCurrentCourtLiveData === 'function'
            ) {
                MapView.refreshCurrentCourtLiveData(MapView.currentCourtId);
            }
        }
    },

    init() {
        const savedCounty = App._normalizeCountySlug(localStorage.getItem(App.countyPrefKey));
        if (savedCounty) App.selectedCountySlug = savedCounty;
        App._updateRegionLabel();

        // Always start on the map view
        App.showView('map');

        MapView.init();
        App.initCountyPicker();
        Chat.init();
        window.addEventListener('resize', () => {
            App.updateTopLayoutOffset();
            const dd = document.getElementById('notifications-dropdown');
            if (dd && dd.style.display === 'block') {
                App._positionNotificationsDropdown();
            }
        });

        // Check auth (async) — validates token, clears stale sessions
        Auth.checkAuth();
        App.refreshReviewerAccess();
        App.refreshNotificationBadge();
        App.startLiveUiRefresh();

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

        // Refresh court data every 15s for live player counts
        App.refreshInterval = setInterval(() => {
            if (App.currentView === 'map') MapView.loadCourts();
        }, 15000);
    },

    initLocationPrompt() {
        const pref = localStorage.getItem(App.locationPrefKey);
        const banner = document.getElementById('location-consent-banner');
        if (!banner) return;

        if (pref === 'enabled') {
            banner.style.display = 'none';
            LocationService.start();
            App.useLocationCounty({ showToast: false, fitMap: false, reloadCourts: true });
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
        App.useLocationCounty({ showToast: false, fitMap: false, reloadCourts: true });
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

    async _populateRankedCourtFilter(force = false) {
        const select = document.getElementById('ranked-court-filter');
        if (!select) return;
        if (force) {
            while (select.options.length > 1) select.remove(1);
        } else if (select.options.length > 1) {
            return;
        }
        try {
            const res = await API.get(App.buildCourtsQuery());
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
        const open = dd.style.display === 'block';
        if (open) {
            dd.style.display = 'none';
            return;
        }
        dd.style.display = 'block';
        App._positionNotificationsDropdown();
        App._loadNotifications();
    },

    _positionNotificationsDropdown() {
        const dd = document.getElementById('notifications-dropdown');
        const bellBtn = document.getElementById('btn-notifications');
        if (!dd || !bellBtn) return;

        const btnRect = bellBtn.getBoundingClientRect();
        const menuWidth = dd.offsetWidth || 320;
        const margin = 12;
        let left = btnRect.right - menuWidth;
        left = Math.max(margin, Math.min(left, window.innerWidth - menuWidth - margin));
        const top = btnRect.bottom + 8;

        dd.style.left = `${Math.round(left)}px`;
        dd.style.right = 'auto';
        dd.style.top = `${Math.round(top)}px`;
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
                const isMatchNotif = ['match_confirm', 'match_rejected', 'match_result', 'match_cancelled', 'elo_change'].includes(n.notif_type) && n.reference_id;
                const isChallengeNotif = ['ranked_challenge_invite', 'ranked_challenge_ready', 'ranked_challenge_declined'].includes(n.notif_type);
                const isAdminNotif = ['court_update_review', 'court_report_review'].includes(n.notif_type);
                let onclick = '';
                if (isSessionNotif) {
                    onclick = `onclick="App.showView('sessions'); document.getElementById('notifications-dropdown').style.display='none'; setTimeout(() => Sessions.openDetail(${n.reference_id}), 200);"`;
                }
                else if (isCourtNotif) onclick = `onclick="MapView.openCourtDetail(${n.reference_id}); App.showView('map'); document.getElementById('notifications-dropdown').style.display='none';"`;
                else if (isFriendNotif) onclick = `onclick="App.showView('profile'); document.getElementById('notifications-dropdown').style.display='none';"`;
                else if (isMatchNotif) {
                    onclick = `onclick="App.openRankedActionFromNotification('${n.notif_type}', ${Number(n.reference_id) || 'null'});"`;
                }
                else if (isChallengeNotif) {
                    onclick = `onclick="App.openRankedActionFromNotification('${n.notif_type}', ${Number(n.reference_id) || 'null'});"`;
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
            App._setNotificationBadge(unread);
        } catch { list.innerHTML = '<p class="muted">Unable to load</p>'; }
    },

    async openRankedActionFromNotification(notifType, referenceId = null) {
        const dropdown = document.getElementById('notifications-dropdown');
        if (dropdown) dropdown.style.display = 'none';

        const focusRankedAction = () => {
            if (typeof Ranked === 'undefined') return;
            if (['match_confirm', 'match_rejected'].includes(notifType) && referenceId) {
                setTimeout(() => Ranked.focusPending(referenceId), 250);
            } else if ((notifType || '').startsWith('ranked_challenge')) {
                setTimeout(() => Ranked.loadPendingConfirmations(), 200);
            }
        };

        const fallbackToRanked = () => {
            App.showView('ranked');
            focusRankedAction();
        };

        try {
            let courtId = null;
            if (['match_confirm', 'match_rejected', 'match_result', 'match_cancelled', 'elo_change'].includes(notifType) && referenceId) {
                const matchRes = await API.get(`/api/ranked/match/${referenceId}`);
                courtId = Number(matchRes.match?.court_id) || null;
            } else if ((notifType || '').startsWith('ranked_challenge') && referenceId) {
                const lobbyRes = await API.get(`/api/ranked/lobby/${referenceId}`);
                courtId = Number(lobbyRes.lobby?.court_id) || null;
            }

            if (!courtId || typeof MapView === 'undefined' || typeof MapView.openCourtFullPage !== 'function') {
                fallbackToRanked();
                return;
            }

            await MapView.openCourtFullPage(courtId);
            focusRankedAction();
        } catch {
            fallbackToRanked();
        }
    },

    async markNotificationsRead() {
        try {
            await API.post('/api/auth/notifications/read', {});
            App._setNotificationBadge(0);
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
