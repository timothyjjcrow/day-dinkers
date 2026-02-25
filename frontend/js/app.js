/**
 * Main application controller — screen/tab navigation, county, notifications, init.
 */
const App = {
    currentScreen: 'main',   // 'main' | 'court-details' | 'admin'
    currentMainTab: 'map',   // 'map' | 'sessions' | 'profile'
    currentCourtTab: 'court-info', // 'court-info' | 'ranked' | 'leaderboard'

    /** Legacy getter: maps new screen/tab system to old view names */
    get currentView() {
        if (App.currentScreen === 'court-details') return 'court-detail';
        if (App.currentScreen === 'admin') return 'admin';
        return App.currentMainTab; // 'map', 'sessions', 'profile'
    },
    friendIds: [],
    friendsList: [],
    locationPrefKey: 'location_tracking_pref',
    countyPrefKey: 'selected_county_slug',
    selectedCountySlug: 'humboldt',
    counties: [],
    isReviewer: false,

    // ── County Helpers ──────────────────────────────────────────

    _normalizeCountySlug(value) {
        return String(value || '')
            .trim().toLowerCase()
            .replace(/_/g, '-').replace(/\s+/g, '-')
            .replace(/[^a-z0-9-]/g, '').replace(/-+/g, '-')
            .replace(/^-|-$/g, '');
    },

    _countyNameFromSlug(slug) {
        const cleaned = App._normalizeCountySlug(slug) || 'humboldt';
        return cleaned.split('-').filter(Boolean)
            .map(part => part.charAt(0).toUpperCase() + part.slice(1))
            .join(' ');
    },

    getSelectedCountySlug() { return App.selectedCountySlug || 'humboldt'; },

    getSelectedCountyName() {
        const slug = App.getSelectedCountySlug();
        const match = App.counties.find(c => c.slug === slug);
        return match?.name || App._countyNameFromSlug(slug);
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

    // ── County Picker ───────────────────────────────────────────

    async initCountyPicker() {
        const select = document.getElementById('county-select');
        if (select) select.innerHTML = '<option value="">Loading...</option>';

        let counties = [];
        try {
            const res = await API.get('/api/courts/counties');
            counties = res.counties || [];
            if (!App._normalizeCountySlug(localStorage.getItem(App.countyPrefKey))) {
                const defaultSlug = App._normalizeCountySlug(res.default_county_slug);
                if (defaultSlug) App.selectedCountySlug = defaultSlug;
            }
        } catch { counties = []; }

        if (!counties.length) {
            const fallbackSlug = App.getSelectedCountySlug();
            counties = [{ slug: fallbackSlug, name: App._countyNameFromSlug(fallbackSlug), court_count: 0 }];
        }

        App.counties = counties;

        if (select) {
            select.innerHTML = counties.map(c =>
                `<option value="${c.slug}">${c.name} County (${c.court_count})</option>`
            ).join('');
        }

        let selected = App.getSelectedCountySlug();
        if (!counties.some(c => c.slug === selected)) selected = counties[0].slug;
        App.setSelectedCounty(selected, {
            persist: true,
            reloadCourts: selected !== App.getSelectedCountySlug(),
            fitMap: true, showToast: false,
        });
        if (select) select.value = selected;

        App._renderCountyPickerList();
        App._updateCountyPickerLabel();
    },

    toggleCountyPicker() {
        const dd = document.getElementById('county-picker-dropdown');
        if (!dd) return;
        const isOpen = dd.style.display !== 'none';
        dd.style.display = isOpen ? 'none' : 'block';
        if (!isOpen) {
            const input = document.getElementById('county-picker-search');
            if (input) { input.value = ''; input.focus(); }
            App._renderCountyPickerList();
        }
    },

    closeCountyPicker() {
        const dd = document.getElementById('county-picker-dropdown');
        if (dd) dd.style.display = 'none';
    },

    filterCountyList(query) {
        App._renderCountyPickerList(query);
    },

    _renderCountyPickerList(query) {
        const list = document.getElementById('county-picker-list');
        if (!list) return;
        const q = (query || '').toLowerCase().trim();
        const filtered = q
            ? App.counties.filter(c => c.name.toLowerCase().includes(q) || c.slug.includes(q))
            : App.counties;
        const selectedSlug = App.getSelectedCountySlug();
        list.innerHTML = filtered.map(c => {
            const active = c.slug === selectedSlug ? ' county-item-active' : '';
            const hasCourtsBadge = c.court_count > 0
                ? `<span class="county-count">${c.court_count}</span>`
                : '<span class="county-count county-count-zero">0</span>';
            return `<button type="button" class="county-item${active}" onclick="App.selectCountyFromPicker('${c.slug}')">
                <span class="county-item-name">${c.name} County</span>
                ${hasCourtsBadge}
            </button>`;
        }).join('') || '<div class="county-empty">No counties match</div>';
    },

    _updateCountyPickerLabel() {
        const label = document.getElementById('county-picker-label');
        if (label) label.textContent = App.getSelectedCountyName() + ' County';
    },

    selectCountyFromPicker(slug) {
        App.closeCountyPicker();
        App.onCountyChange(slug);
        App._updateCountyPickerLabel();
        App._renderCountyPickerList();
    },

    setSelectedCounty(rawSlug, { persist = true, reloadCourts = true, fitMap = false, showToast = false } = {}) {
        const normalized = App._normalizeCountySlug(rawSlug) || 'humboldt';
        const changed = normalized !== App.selectedCountySlug;
        App.selectedCountySlug = normalized;
        if (persist) localStorage.setItem(App.countyPrefKey, normalized);

        const select = document.getElementById('county-select');
        if (select && select.value !== normalized) select.value = normalized;
        App._updateCountyPickerLabel();

        if (!changed) return;
        if (reloadCourts) {
            if (typeof MapView !== 'undefined' && typeof MapView.loadCourts === 'function') {
                MapView.loadCourts({ fitToCourts: fitMap });
            }
            if (typeof LocationService !== 'undefined' && typeof LocationService._refreshCourts === 'function') {
                LocationService._refreshCourts();
            }
        }
        if (showToast) App.toast(`Switched to ${App.getSelectedCountyName()} County`);
    },

    onCountyChange(countySlug) {
        App.setSelectedCounty(countySlug, { persist: true, reloadCourts: true, fitMap: true, showToast: true });
    },

    useLocationCounty(options = {}) {
        const showToast = options.showToast !== false;
        const fitMap = options.fitMap !== false;
        const reloadCourts = options.reloadCourts !== false;

        if (!navigator.geolocation) {
            if (showToast) App.toast('Geolocation not supported.', 'error');
            return;
        }
        navigator.geolocation.getCurrentPosition(async (position) => {
            try {
                const { latitude, longitude } = position.coords;
                const res = await API.get(`/api/courts/resolve-county?lat=${latitude}&lng=${longitude}`);
                if (!res?.county_slug) {
                    if (showToast) App.toast('Could not determine county.', 'error');
                    return;
                }
                App.setSelectedCounty(res.county_slug, { persist: true, reloadCourts, fitMap, showToast });
            } catch (err) {
                if (showToast) App.toast(err.message || 'Could not determine county.', 'error');
            }
        }, () => {
            if (showToast) App.toast('Location permission denied.', 'error');
        }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 60000 });
    },

    // ── Screen & Tab Navigation ─────────────────────────────────

    showScreen(screen) {
        document.getElementById('main-screen').style.display = screen === 'main' ? 'flex' : 'none';
        document.getElementById('court-details-screen').style.display = screen === 'court-details' ? 'flex' : 'none';
        document.getElementById('admin-view').style.display = screen === 'admin' ? 'flex' : 'none';
        App.currentScreen = screen;

        // Close dropdowns
        const dd = document.getElementById('notifications-dropdown');
        if (dd) dd.style.display = 'none';

        if (screen === 'main' && App.currentMainTab === 'map') {
            setTimeout(() => { if (MapView.map) MapView.map.invalidateSize(); }, 100);
        }
    },

    setMainTab(tab) {
        if (App.currentScreen !== 'main') App.showScreen('main');

        const tabs = document.querySelectorAll('#main-tab-bar .tab');
        tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === tab));

        document.getElementById('map-tab').classList.toggle('active', tab === 'map');
        document.getElementById('profile-tab').classList.toggle('active', tab === 'profile');

        App.currentMainTab = tab;

        if (tab === 'map') {
            setTimeout(() => { if (MapView.map) MapView.map.invalidateSize(); }, 100);
            if (typeof MapView !== 'undefined' && typeof MapView.loadScheduleBanner === 'function') {
                MapView.loadScheduleBanner();
            }
        }
        if (tab === 'sessions') {
            App.setMainTab('map');
            return;
        }
        if (tab === 'profile') {
            Profile.load();
            setTimeout(() => Profile.loadExtras(), 300);
        }
    },

    setCourtTab(tab) {
        if (tab === 'ranked' && MapView.currentCourtId) {
            const el = document.getElementById('court-ranked-inline');
            if (el) el.scrollIntoView({ behavior: 'smooth' });
        }
        if (tab === 'leaderboard' && MapView.currentCourtId) {
            const el = document.getElementById('court-leaderboard-inline');
            if (el) el.scrollIntoView({ behavior: 'smooth' });
        }
    },

    async _loadLeaderboardTab(courtId) {
        App._loadLeaderboardInline(courtId);
    },

    async _loadLeaderboardInline(courtId) {
        const courtLb = document.getElementById('leaderboard-content');
        if (!courtLb) return;
        courtLb.innerHTML = '<div class="loading">Loading...</div>';
        try {
            const res = await API.get(`/api/ranked/leaderboard?court_id=${courtId}`);
            const lb = res.leaderboard || [];
            if (!lb.length) {
                courtLb.innerHTML = '<p class="muted">No ranked players at this court yet.</p>';
                const section = document.getElementById('court-leaderboard-inline');
                if (section) section.classList.add('court-page-section-empty');
            } else {
                const top5 = lb.slice(0, 5);
                courtLb.innerHTML = top5.map(player =>
                    Ranked._leaderboardCardHTML(player, {
                        currentUserId: Number(Ranked._currentUser().id) || 0,
                        showChallenge: false,
                        compact: true,
                    })
                ).join('');
            }
        } catch { courtLb.innerHTML = '<p class="muted">Unable to load.</p>'; }
    },

    async openLeaderboardPopup(courtId) {
        const popup = document.getElementById('leaderboard-popup');
        const body = document.getElementById('leaderboard-popup-body');
        if (!popup || !body) return;
        body.innerHTML = '<div class="loading">Loading leaderboard...</div>';
        popup.style.display = 'flex';

        try {
            const [courtRes, countyRes, historyRes] = await Promise.all([
                API.get(`/api/ranked/leaderboard?court_id=${courtId}`),
                API.get(`/api/ranked/leaderboard?county_slug=${encodeURIComponent(App.getSelectedCountySlug())}`),
                API.get(`/api/ranked/history?court_id=${courtId}&limit=30`),
            ]);
            const courtLb = courtRes.leaderboard || [];
            const countyLb = countyRes.leaderboard || [];
            const matches = historyRes.matches || [];

            let html = '';
            html += '<h4 style="margin:0 0 8px">Court Standings</h4>';
            html += courtLb.length
                ? Ranked._renderLeaderboard(courtLb, { scopeLabel: 'Court' })
                : '<p class="muted">No ranked players at this court yet.</p>';

            html += '<h4 style="margin:16px 0 8px">County Standings</h4>';
            html += countyLb.length
                ? Ranked._renderLeaderboard(countyLb, { scopeLabel: 'County' })
                : '<p class="muted">No ranked players in this county yet.</p>';

            if (matches.length) {
                html += '<h4 style="margin:16px 0 8px">Recent Games</h4>';
                html += matches.slice(0, 10).map(m => {
                    const t1 = Ranked._teamNames(m.team1);
                    const t2 = Ranked._teamNames(m.team2);
                    const score = `${m.team1_score}-${m.team2_score}`;
                    return `<div class="schedule-event-card" style="padding:8px">
                        <div style="display:flex;justify-content:space-between;align-items:center">
                            <span>${t1} vs ${t2}</span>
                            <strong>${score}</strong>
                        </div>
                    </div>`;
                }).join('');
            }

            body.innerHTML = html;
        } catch {
            body.innerHTML = '<p class="muted">Unable to load leaderboard.</p>';
        }
    },

    closeLeaderboardPopup() {
        const popup = document.getElementById('leaderboard-popup');
        if (popup) popup.style.display = 'none';
    },

    openCourtDetails(courtId) {
        App.showScreen('court-details');
        MapView.openCourt(courtId);
    },

    /** Go back from court details to main screen */
    backToMain() {
        // Leave chat room
        if (MapView.currentCourtId && typeof Chat !== 'undefined' && Chat.socket) {
            Chat.socket.emit('leave', { room: `court_${MapView.currentCourtId}` });
        }
        App.showScreen('main');
    },

    /** Legacy compatibility: maps old showView calls to new navigation */
    showView(view) {
        if (view === 'map') { App.setMainTab('map'); }
        else if (view === 'sessions') { App.setMainTab('sessions'); }
        else if (view === 'profile') { App.setMainTab('profile'); }
        else if (view === 'ranked') {
            // If we have a court open, switch to its ranked tab
            if (App.currentScreen === 'court-details') {
                App.setCourtTab('ranked');
            } else {
                // No court context — go to profile (leaderboard accessible from courts)
                App.setMainTab('profile');
            }
        }
        else if (view === 'admin') { App.showScreen('admin'); }
        else if (view === 'court-detail') {
            // Legacy full-page view — handled by openCourtDetails now
            App.showScreen('court-details');
        }
    },

    closePanel() {
        // Legacy: side panel is removed in new design. No-op.
    },

    // ── Friends Cache ───────────────────────────────────────────

    async loadFriendsCache() {
        const token = localStorage.getItem('token');
        if (!token) { App.friendIds = []; App.friendsList = []; return; }
        try {
            const res = await API.get('/api/auth/friends');
            App.friendsList = res.friends || [];
            App.friendIds = App.friendsList.map(f => f.id);
        } catch { App.friendIds = []; App.friendsList = []; }
    },

    // ── Invite Modal ────────────────────────────────────────────

    showInviteModal(title, onSend) {
        const modal = document.getElementById('invite-modal');
        document.getElementById('invite-modal-title').textContent = title;
        const container = document.getElementById('invite-modal-friends');
        const sendBtn = document.getElementById('invite-modal-send');

        if (App.friendsList.length === 0) {
            container.innerHTML = '<p class="muted">No friends yet. Add friends from player cards!</p>';
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

    // ── Notifications ───────────────────────────────────────────

    _setNotificationBadge(unreadCount) {
        const badge = document.getElementById('notif-badge');
        if (!badge) return;
        const unread = Number(unreadCount) || 0;
        if (unread > 0) { badge.textContent = unread; badge.style.display = 'inline'; }
        else { badge.style.display = 'none'; }
    },

    async refreshNotificationBadge() {
        const token = localStorage.getItem('token');
        if (!token) { App._setNotificationBadge(0); return; }
        try {
            const res = await API.get('/api/auth/notifications');
            const notifs = res.notifications || [];
            App._setNotificationBadge(notifs.filter(n => !n.read).length);
        } catch {}
    },

    toggleNotifications() {
        const dd = document.getElementById('notifications-dropdown');
        const open = dd.style.display === 'block';
        dd.style.display = open ? 'none' : 'block';
        if (!open) App._loadNotifications();
    },

    async _loadNotifications() {
        const list = document.getElementById('notifications-list');
        list.innerHTML = '<div class="loading">Loading...</div>';
        try {
            const res = await API.get('/api/auth/notifications');
            const notifs = res.notifications || [];
            if (!notifs.length) { list.innerHTML = '<p class="muted" style="padding:16px">No notifications</p>'; return; }
            list.innerHTML = notifs.map(n => {
                const isFriendNotif = ['friend_request', 'friend_accepted'].includes(n.notif_type);
                const isSessionNotif = ['session_invite', 'session_join'].includes(n.notif_type);
                const isCourtNotif = ['court_invite', 'checkin_notify'].includes(n.notif_type);
                const isMatchNotif = ['match_confirm', 'match_rejected', 'match_result', 'match_cancelled', 'elo_change'].includes(n.notif_type);
                const isChallengeNotif = (n.notif_type || '').startsWith('ranked_challenge');
                const isTournamentNotif = (n.notif_type || '').startsWith('tournament_');
                const isAdminNotif = ['court_update_approved', 'court_update_rejected'].includes(n.notif_type);
                let onclick = '';
                if (isSessionNotif && n.reference_id) {
                    onclick = `onclick="document.getElementById('notifications-dropdown').style.display='none'; Sessions.openDetail(${n.reference_id});"`;
                }
                else if (isCourtNotif && n.reference_id) onclick = `onclick="App.openCourtDetails(${n.reference_id}); document.getElementById('notifications-dropdown').style.display='none';"`;
                else if (isFriendNotif) onclick = `onclick="App.setMainTab('profile'); document.getElementById('notifications-dropdown').style.display='none';"`;
                else if (isMatchNotif || isChallengeNotif) {
                    onclick = `onclick="App.openRankedActionFromNotification('${n.notif_type}', ${Number(n.reference_id) || 'null'});"`;
                }
                else if (isTournamentNotif) {
                    onclick = `onclick="App.openTournamentActionFromNotification('${n.notif_type}', ${Number(n.reference_id) || 'null'});"`;
                }
                else if (isAdminNotif) onclick = `onclick="App.showScreen('admin'); document.getElementById('notifications-dropdown').style.display='none';"`;
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

        try {
            let courtId = null;
            if (['match_confirm', 'match_rejected', 'match_result', 'match_cancelled', 'elo_change'].includes(notifType) && referenceId) {
                const matchRes = await API.get(`/api/ranked/match/${referenceId}`);
                courtId = Number(matchRes.match?.court_id) || null;
            } else if ((notifType || '').startsWith('ranked_challenge') && referenceId) {
                const lobbyRes = await API.get(`/api/ranked/lobby/${referenceId}`);
                courtId = Number(lobbyRes.lobby?.court_id) || null;
            }

            if (courtId) {
                App.openCourtDetails(courtId);
                setTimeout(() => App.setCourtTab('ranked'), 300);
                return;
            }
        } catch {}

        // Fallback: go to profile
        App.setMainTab('profile');
    },

    async openTournamentActionFromNotification(notifType, referenceId = null) {
        const dropdown = document.getElementById('notifications-dropdown');
        if (dropdown) dropdown.style.display = 'none';

        let tournamentId = null;
        let courtId = null;
        try {
            if (notifType === 'tournament_match_ready' && referenceId) {
                const matchRes = await API.get(`/api/ranked/match/${referenceId}`);
                tournamentId = Number(matchRes.match?.tournament_id) || null;
                courtId = Number(matchRes.match?.court_id) || null;
            }
            if (!tournamentId && referenceId) {
                const tournamentRes = await API.get(`/api/ranked/tournaments/${referenceId}`);
                tournamentId = Number(tournamentRes.tournament?.id) || null;
                courtId = Number(tournamentRes.tournament?.court_id) || null;
            }
            if (tournamentId && courtId) {
                if (typeof Ranked !== 'undefined' && typeof Ranked.openTournamentFromSchedule === 'function') {
                    Ranked.openTournamentFromSchedule(courtId, tournamentId);
                } else {
                    App.openCourtDetails(courtId);
                    App.setCourtTab('ranked');
                }
                return;
            }
        } catch {}

        App.setMainTab('profile');
    },

    async markNotificationsRead() {
        try {
            await API.post('/api/auth/notifications/read', {});
            App._setNotificationBadge(0);
            App._loadNotifications();
        } catch {}
    },

    // ── Updates Bottom Sheet ────────────────────────────────────

    toggleUpdatesSheet() {
        const sheet = document.getElementById('updates-bottom-sheet');
        if (!sheet) return;
        const isOpen = sheet.style.display !== 'none';
        if (isOpen) {
            sheet.style.display = 'none';
        } else {
            // Open the court update modal instead for now
            const courtId = MapView.currentCourtId;
            if (courtId) {
                const court = MapView.courts.find(c => c.id === courtId);
                const courtName = court ? court.name : 'Court';
                CourtUpdates.openModal(courtId, courtName);
            }
        }
    },

    // ── Reviewer / Admin Access ─────────────────────────────────

    async refreshReviewerAccess() {
        const token = localStorage.getItem('token');
        if (!token) { App.isReviewer = false; return; }
        let isAdmin = false;
        try {
            const res = await API.get('/api/courts/updates/admin-status');
            isAdmin = !!(res.is_admin ?? res.is_reviewer);
        } catch {
            try {
                const fallback = await API.get('/api/courts/updates/reviewer-status');
                isAdmin = !!(fallback.is_admin ?? fallback.is_reviewer);
            } catch { isAdmin = false; }
        }
        App.isReviewer = isAdmin;
    },

    // ── Live UI Refresh ─────────────────────────────────────────

    startLiveUiRefresh() {
        if (App.liveUiRefreshInterval) clearInterval(App.liveUiRefreshInterval);
        App.liveUiRefreshInterval = setInterval(() => App.refreshLiveUiNow(), 6000);
    },

    refreshLiveUiNow() {
        const token = localStorage.getItem('token');
        if (!token) { App._setNotificationBadge(0); return; }

        App.refreshNotificationBadge();

        const courtOpen = App.currentScreen === 'court-details';
        if (typeof Ranked !== 'undefined' && courtOpen && MapView.currentCourtId) {
            Ranked.loadCourtRanked(MapView.currentCourtId, { silent: true });
        }
    },

    // ── Location Prompt ─────────────────────────────────────────

    initLocationPrompt() {
        const pref = localStorage.getItem(App.locationPrefKey);
        if (pref === 'enabled' || pref === 'dismissed') return;
        const banner = document.getElementById('location-consent-banner');
        if (banner) banner.style.display = 'flex';
    },

    enableLocationTracking() {
        localStorage.setItem(App.locationPrefKey, 'enabled');
        document.getElementById('location-consent-banner').style.display = 'none';
        if (typeof LocationService !== 'undefined' && typeof LocationService.start === 'function') {
            LocationService.start();
        }
        App.useLocationCounty({ showToast: true, fitMap: true });
    },

    dismissLocationPrompt() {
        localStorage.setItem(App.locationPrefKey, 'dismissed');
        document.getElementById('location-consent-banner').style.display = 'none';
    },

    updateTopLayoutOffset() {
        // No longer needed in new design — kept as no-op for compatibility
    },

    // ── View Profile (legacy compat) ────────────────────────────

    viewProfile() {
        App.setMainTab('profile');
    },

    // ── Toast ───────────────────────────────────────────────────

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

    // ── Init ────────────────────────────────────────────────────

    init() {
        const savedCounty = App._normalizeCountySlug(localStorage.getItem(App.countyPrefKey));
        if (savedCounty) App.selectedCountySlug = savedCounty;

        // Start on main screen, map tab
        App.showScreen('main');
        App.setMainTab('map');

        MapView.init();
        App.initCountyPicker();
        Chat.init();

        // Auth check
        Auth.checkAuth();
        App.refreshReviewerAccess();
        App.refreshNotificationBadge();
        App.startLiveUiRefresh();
        App.initLocationPrompt();

        // Mobile search handler
        const mobileSearchInput = document.getElementById('mobile-search-input');
        if (mobileSearchInput) {
            let mobileSearchTimeout;
            mobileSearchInput.addEventListener('input', () => {
                clearTimeout(mobileSearchTimeout);
                mobileSearchTimeout = setTimeout(() => MapView.search(mobileSearchInput.value), 400);
            });
            mobileSearchInput.addEventListener('focus', () => {
                if (mobileSearchInput.value.trim()) MapView._showSuggestions(mobileSearchInput.value);
            });
        }

        // Close dropdowns on outside click
        document.addEventListener('click', (e) => {
            const picker = document.getElementById('county-picker');
            if (picker && !picker.contains(e.target)) App.closeCountyPicker();
            const suggestions = document.getElementById('search-suggestions');
            const searchInput = document.getElementById('mobile-search-input');
            if (suggestions && !suggestions.contains(e.target) && e.target !== searchInput) {
                if (typeof MapView !== 'undefined') MapView.hideSuggestions();
            }
        });

        // Update user avatar in header
        App._updateHeaderAvatar();

        // Apply enhanced datetime pickers to static scheduling inputs.
        if (typeof DateTimePicker !== 'undefined') {
            DateTimePicker.init(document);
        }
    },

    _updateHeaderAvatar() {
        const user = JSON.parse(localStorage.getItem('user') || 'null');
        const initialEl = document.getElementById('header-user-initial');
        if (initialEl && user) {
            const name = user.name || user.username || '?';
            initialEl.textContent = name[0].toUpperCase();
        }
    },
};

document.addEventListener('DOMContentLoaded', App.init);
