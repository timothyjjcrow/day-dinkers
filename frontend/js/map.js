/**
 * Map integration — Leaflet.js with county-focused loading, court markers,
 * busyness indicators, open-to-play sessions, and direction links.
 */
const MapView = {
    map: null,
    markers: [],
    courts: [],
    lastUpdatedAt: null,
    activeFilter: 'all',
    courtListOpen: false,
    currentCourtId: null,
    myCheckinStatus: null, // { checked_in: bool, court_id }
    friendsPresence: [],  // [{user, court_id, checked_in_at}]
    friendMarkers: [],

    init() {
        // Initial California north-coast view; county selection adjusts loaded data.
        MapView.map = L.map('map', {
            zoomControl: true,
        }).setView([40.83, -124.08], 11);

        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; <a href="https://openstreetmap.org">OpenStreetMap</a> contributors',
            maxZoom: 19,
        }).addTo(MapView.map);

        MapView.loadCourts();
        MapView.refreshMyStatus();

        // Search handler
        const searchInput = document.getElementById('search-input');
        let searchTimeout;
        searchInput.addEventListener('input', () => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => MapView.search(searchInput.value), 400);
        });
    },

    async refreshMyStatus() {
        const token = localStorage.getItem('token');
        if (!token) { MapView.myCheckinStatus = null; return; }
        try {
            MapView.myCheckinStatus = await API.get('/api/presence/status');
        } catch {
            MapView.myCheckinStatus = null;
        }
    },

    async loadFriendsPresence() {
        const token = localStorage.getItem('token');
        if (!token) { MapView.friendsPresence = []; return; }
        try {
            const res = await API.get('/api/presence/friends');
            MapView.friendsPresence = res.friends_presence || [];
            MapView.renderFriendMarkers();
        } catch {
            MapView.friendsPresence = [];
        }
    },

    renderFriendMarkers() {
        MapView.friendMarkers.forEach(m => MapView.map.removeLayer(m));
        MapView.friendMarkers = [];

        const placed = new Set();
        MapView.friendsPresence.forEach(fp => {
            const court = MapView.courts.find(c => c.id === fp.court_id);
            if (!court || placed.has(fp.court_id)) return;
            placed.add(fp.court_id);

            const friends = MapView._friendsAtCourt(fp.court_id);
            const names = friends.map(f => f.user.name || f.user.username).join(', ');
            const marker = L.marker([court.latitude, court.longitude], {
                icon: L.divIcon({
                    className: 'friend-marker-icon',
                    html: `<div class="friend-marker">${friends.length > 1 ? friends.length : ''}👥</div>`,
                    iconSize: [24, 24],
                    // Offset badge up/right from the court pin so it never covers the pin.
                    iconAnchor: [-12, 50],
                    popupAnchor: [18, -40],
                }),
                zIndexOffset: 300,
            }).addTo(MapView.map);
            marker.bindPopup(`<strong>Friends here:</strong><br>${names}`);
            MapView.friendMarkers.push(marker);
        });
    },

    _friendsAtCourt(courtId) {
        return MapView.friendsPresence.filter(fp => fp.court_id === courtId);
    },

    async loadCourts(options = {}) {
        const fitToCourts = !!options.fitToCourts;
        try {
            const courtsUrl = (typeof App !== 'undefined' && typeof App.buildCourtsQuery === 'function')
                ? App.buildCourtsQuery()
                : '/api/courts';
            const res = await API.get(courtsUrl);
            MapView.courts = res.courts || [];
            MapView.renderMarkers();
            MapView.renderCourtList();
            await MapView.loadFriendsPresence();
            // Re-render so popups/list can include friend indicators once presence arrives.
            MapView.renderMarkers();
            MapView.renderCourtList();
            if (fitToCourts && MapView.courts.length) {
                const bounds = L.latLngBounds(MapView.courts.map(c => [c.latitude, c.longitude]));
                MapView.map.fitBounds(bounds, { padding: [50, 50], maxZoom: 12 });
            }
            MapView._setLastUpdated();
        } catch (err) {
            console.error('Failed to load courts:', err);
        }
    },

    _setLastUpdated() {
        const label = document.getElementById('map-last-updated');
        if (!label) return;
        MapView.lastUpdatedAt = new Date();
        label.textContent = `Updated ${MapView.lastUpdatedAt.toLocaleTimeString('en-US', {
            hour: 'numeric',
            minute: '2-digit',
        })}`;
    },

    renderMarkers() {
        // Clear existing markers
        MapView.markers.forEach(m => MapView.map.removeLayer(m));
        MapView.markers = [];

        const filtered = MapView._filterCourts(MapView.courts);

        filtered.forEach(court => {
            const players = court.active_players || 0;
            const icon = MapView._courtIcon(court, players);
            const marker = L.marker([court.latitude, court.longitude], { icon })
                .addTo(MapView.map);

            marker.bindPopup(MapView._popupContent(court, players));
            marker.on('click', () => {
                marker.openPopup();
            });
            marker.courtId = court.id;
            MapView.markers.push(marker);
        });
    },

    _courtIcon(court, players) {
        const color = court.indoor ? '#6366f1' : '#22c55e';
        const busy = players > 0 ? '#ef4444' : color;
        const badge = players > 0 ? `<span class="marker-badge">${players}</span>` : '';

        return L.divIcon({
            className: 'court-marker-icon',
            html: `<div class="court-marker" style="background:${busy}">${badge}
                     <svg viewBox="0 0 24 24" fill="white" width="16" height="16"><circle cx="12" cy="12" r="6"/></svg>
                   </div>`,
            iconSize: [32, 32],
            iconAnchor: [16, 32],
            popupAnchor: [0, -30],
        });
    },

    _popupContent(court, players) {
        const amenities = [];
        if (court.has_restrooms) amenities.push('🚻');
        if (court.has_parking) amenities.push('🅿️');
        if (court.has_water) amenities.push('💧');
        if (court.lighted) amenities.push('💡');
        if (court.paddle_rental) amenities.push('🏓');
        if (court.has_pro_shop) amenities.push('🛒');

        const courtName = MapView._escapeHtml(court.name || '');
        const courtAddress = MapView._escapeHtml(court.address || '');
        const courtCity = MapView._escapeHtml(court.city || '');
        const courtFees = MapView._escapeHtml(court.fees || '');
        const courtHours = MapView._escapeHtml(court.hours || '');
        const distValue = Number(court.distance);
        const distStr = Number.isFinite(distValue) ? `<span class="popup-dist">${distValue} mi</span>` : '';
        const playerStr = players > 0
            ? `<div class="popup-players active">🟢 ${players} player${players > 1 ? 's' : ''} here now</div>`
            : `<div class="popup-players">No active players</div>`;

        const typeLabel = court.court_type === 'dedicated' ? '🏟 Dedicated' :
                          court.court_type === 'converted' ? '🔄 Converted' : '🔀 Shared';
        const friendsAtCourt = MapView._friendsAtCourt(court.id);
        const safeFriendNames = friendsAtCourt
            .map(f => MapView._escapeHtml(f.user.name || f.user.username))
            .join(', ');

        return `
        <div class="court-popup">
            <h3>${courtName} ${distStr}</h3>
            <p class="popup-addr">${courtAddress}, ${courtCity}</p>
            <div class="popup-meta">
                <span>${court.indoor ? '🏢 Indoor' : '☀️ Outdoor'}</span>
                <span>${court.num_courts} court${court.num_courts > 1 ? 's' : ''}</span>
                <span>${typeLabel}</span>
            </div>
            ${playerStr}
            ${court.fees ? `<div class="popup-fees">${courtFees}</div>` : ''}
            ${court.hours ? `<div class="popup-hours">🕐 ${courtHours}</div>` : ''}
            ${friendsAtCourt.length > 0 ? `<div class="popup-friends">👥 ${safeFriendNames}</div>` : ''}
            <div class="popup-amenities">${amenities.join(' ')}</div>
            <div class="popup-actions">
                <button class="btn-primary btn-sm" onclick="MapView.openCourtDetail(${court.id})">Details</button>
                <a class="btn-secondary btn-sm" href="${MapView._formatDirectionsUrl(court.latitude, court.longitude)}" target="_blank" rel="noopener noreferrer">Directions</a>
            </div>
        </div>`;
    },

    _filterCourts(courts) {
        const f = MapView.activeFilter;
        if (f === 'all') return courts;
        if (f === 'indoor') return courts.filter(c => c.indoor);
        if (f === 'outdoor') return courts.filter(c => !c.indoor);
        if (f === 'lighted') return courts.filter(c => c.lighted);
        if (f === 'free') return courts.filter(c => (c.fees || '').toLowerCase().includes('free'));
        if (f === 'dedicated') return courts.filter(c => c.court_type === 'dedicated');
        if (f === 'active') return courts.filter(c => c.active_players > 0);
        return courts;
    },

    setFilter(filter) {
        MapView.activeFilter = filter;
        document.querySelectorAll('.filter-chip').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.filter === filter);
        });
        MapView.renderMarkers();
        MapView.renderCourtList();
    },

    async openBestCourtNow() {
        if (!MapView.courts.length) {
            await MapView.loadCourts();
        }
        const candidates = MapView._filterCourts(MapView.courts);
        if (!candidates.length) {
            App.toast('No courts available for your current filters', 'error');
            return;
        }

        let best = null;
        let bestScore = -Infinity;
        for (const court of candidates) {
            const score = MapView._bestCourtScore(court);
            if (score > bestScore) {
                best = court;
                bestScore = score;
            }
        }
        if (!best) {
            App.toast('Could not determine a recommendation right now', 'error');
            return;
        }

        MapView.map.setView([best.latitude, best.longitude], 14);
        MapView.openCourtDetail(best.id);

        const players = best.active_players || 0;
        const sessions = best.open_sessions || 0;
        const friends = MapView._friendsAtCourt(best.id).length;
        const distMeters = MapView._distanceToCourtMeters(best);
        const reasons = [];
        if (players > 0) reasons.push(`${players} active player${players > 1 ? 's' : ''}`);
        if (sessions > 0) reasons.push(`${sessions} open session${sessions > 1 ? 's' : ''}`);
        if (friends > 0) reasons.push(`${friends} friend${friends > 1 ? 's' : ''} there`);
        if (distMeters !== null) {
            const distStr = distMeters < 1000
                ? `${Math.round(distMeters)}m away`
                : `${(distMeters / 1609).toFixed(1)}mi away`;
            reasons.push(distStr);
        }
        App.toast(`Best court now: ${best.name}${reasons.length ? ` — ${reasons.join(', ')}` : ''}`);
    },

    _bestCourtScore(court) {
        const players = court.active_players || 0;
        const sessions = court.open_sessions || 0;
        const friends = MapView._friendsAtCourt(court.id).length;
        const distMeters = MapView._distanceToCourtMeters(court);

        let score = 0;
        score += players * 100;
        score += sessions * 80;
        score += friends * 120;
        score += Math.min(court.num_courts || 0, 12);
        if (court.verified) score += 20;
        if (court.court_type === 'dedicated') score += 10;
        if (court.lighted) score += 4;
        if (distMeters !== null) {
            const miles = distMeters / 1609;
            // Prefer nearby courts when location is available.
            score += Math.max(0, 80 - miles * 12);
        }
        return score;
    },

    _distanceToCourtMeters(court) {
        const pos = typeof LocationService !== 'undefined' ? LocationService.lastPosition : null;
        if (!pos || typeof LocationService._distanceMeters !== 'function') return null;
        return LocationService._distanceMeters(pos.lat, pos.lng, court.latitude, court.longitude);
    },

    async search(query) {
        if (!query) {
            MapView.loadCourts();
            return;
        }
        try {
            const url = (typeof App !== 'undefined' && typeof App.buildCourtsQuery === 'function')
                ? App.buildCourtsQuery({ search: query })
                : `/api/courts?search=${encodeURIComponent(query)}`;
            const res = await API.get(url);
            MapView.courts = res.courts || [];
            MapView.renderMarkers();
            MapView.renderCourtList();
            // If results, fit map to show them
            if (MapView.courts.length > 0) {
                const bounds = L.latLngBounds(MapView.courts.map(c => [c.latitude, c.longitude]));
                MapView.map.fitBounds(bounds, { padding: [50, 50] });
            }
        } catch {}
    },

    locateUser() {
        if (!navigator.geolocation) return;
        navigator.geolocation.getCurrentPosition(pos => {
            const { latitude, longitude } = pos.coords;
            MapView.map.setView([latitude, longitude], 13);
            L.marker([latitude, longitude], {
                icon: L.divIcon({
                    className: 'user-location-icon',
                    html: '<div class="user-dot"></div>',
                    iconSize: [16, 16],
                })
            }).addTo(MapView.map).bindPopup('You are here');
        });
    },

    async _fetchCourtBundle(courtId) {
        const [courtRes, sessionsRes] = await Promise.all([
            API.get(`/api/courts/${courtId}`),
            API.get(`/api/sessions?court_id=${courtId}`),
        ]);
        return {
            court: courtRes.court,
            sessions: sessionsRes.sessions || [],
        };
    },

    async openCourtDetail(courtId) {
        MapView.currentCourtId = courtId;
        const panel = document.getElementById('court-panel');
        const detail = document.getElementById('court-detail');
        panel.style.display = 'block';
        detail.innerHTML = '<div class="loading">Loading court details...</div>';

        // Refresh check-in status
        await MapView.refreshMyStatus();

        try {
            const { court, sessions } = await MapView._fetchCourtBundle(courtId);
            detail.innerHTML = MapView._courtDetailHTML(court, sessions);

            // NOW load competitive play data (after HTML is in DOM)
            Ranked.loadCourtRanked(court.id);
        } catch {
            detail.innerHTML = '<p class="error">Failed to load court details</p>';
        }
    },

    /** Expand the side panel into a full-page court view */
    async openCourtFullPage(courtId) {
        MapView.currentCourtId = courtId;
        App.showView('court-detail');
        const container = document.getElementById('court-fullpage-content');
        container.innerHTML = '<div class="loading">Loading court details...</div>';

        await MapView.refreshMyStatus();

        try {
            const { court, sessions } = await MapView._fetchCourtBundle(courtId);
            container.innerHTML = MapView._courtFullPageHTML(court, sessions);

            // Load competitive play into the full-page section
            Ranked.loadCourtRanked(court.id);

            // Load court chat messages inline
            MapView._loadFullPageChat(court.id);

            // Join socket room for real-time updates
            if (typeof Chat !== 'undefined' && Chat.socket) {
                Chat.joinRoom(`court_${court.id}`);
            }
        } catch {
            container.innerHTML = '<p class="error">Failed to load court details</p>';
        }
    },

    /** Refresh the full-page court view (after check-in/session changes). */
    async _refreshFullPage(courtId) {
        if (App.currentView !== 'court-detail') return;
        await MapView.refreshMyStatus();
        try {
            const { court, sessions } = await MapView._fetchCourtBundle(courtId);
            const container = document.getElementById('court-fullpage-content');
            container.innerHTML = MapView._courtFullPageHTML(court, sessions);
            Ranked.loadCourtRanked(court.id);
            MapView._loadFullPageChat(court.id);
        } catch {}
    },

    async refreshCurrentCourtLiveData(courtId) {
        const targetCourtId = Number(courtId || MapView.currentCourtId);
        if (!targetCourtId) return;
        const currentView = (typeof App !== 'undefined' && App.currentView) ? App.currentView : '';
        const fullPageOpen = currentView === 'court-detail' && MapView.currentCourtId === targetCourtId;
        const panel = document.getElementById('court-panel');
        const panelOpen = !!(panel && panel.style.display !== 'none' && MapView.currentCourtId === targetCourtId);
        if (!fullPageOpen && !panelOpen) return;

        const suffix = fullPageOpen ? 'full' : 'panel';
        const sessionsEl = document.getElementById(`court-sessions-live-${suffix}`);
        const playersEl = document.getElementById(`court-players-live-${suffix}`);
        const countEl = document.getElementById(`court-player-count-${suffix}`);
        const statusEl = document.getElementById(`court-live-status-${suffix}`);
        const bannerEl = document.getElementById(`court-match-banner-${suffix}`);

        if (!sessionsEl && !playersEl && !countEl && !statusEl && !bannerEl) return;

        try {
            await MapView.refreshMyStatus();
            const { court, sessions } = await MapView._fetchCourtBundle(targetCourtId);
            const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
            const myStatus = MapView.myCheckinStatus || {};
            const amCheckedInHere = myStatus.checked_in && myStatus.court_id === targetCourtId;
            const liveSections = MapView._buildLiveCourtSections(
                court,
                sessions,
                currentUser.id,
                amCheckedInHere,
            );

            if (sessionsEl) sessionsEl.innerHTML = liveSections.sessionsHTML;
            if (playersEl) playersEl.innerHTML = liveSections.playersHTML;
            if (countEl) countEl.textContent = String(liveSections.checkedInCount);
            if (statusEl) {
                statusEl.classList.toggle('active', liveSections.activePlayers > 0);
                statusEl.innerHTML = MapView._liveStatusInnerHTML(court, liveSections.activePlayers);
            }
            if (bannerEl) bannerEl.innerHTML = liveSections.matchBannerHTML;
        } catch {}
    },

    async _loadFullPageChat(courtId) {
        const container = document.getElementById('fullpage-chat-messages');
        if (!container) return;
        const token = localStorage.getItem('token');
        if (!token) {
            container.innerHTML = '<p class="muted">Sign in to view and send messages</p>';
            return;
        }
        try {
            const res = await API.get(`/api/chat/court/${courtId}`);
            const msgs = res.messages || [];
            if (!msgs.length) {
                container.innerHTML = '<p class="muted">No messages yet. Say hello to other players!</p>';
                return;
            }
            container.innerHTML = msgs.map(m => MapView._renderChatMsg(m)).join('');
            container.scrollTop = container.scrollHeight;
        } catch {
            container.innerHTML = '<p class="muted">Unable to load messages</p>';
        }
    },

    _renderChatMsg(msg) {
        const sender = msg.sender || {};
        const time = new Date(msg.created_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const isMe = sender.id === currentUser.id;
        const senderName = MapView._escapeHtml(isMe ? 'You' : (sender.name || sender.username));
        const content = MapView._escapeHtml(msg.content || '');
        return `
        <div class="chat-msg ${isMe ? 'chat-msg-me' : ''}">
            <div class="chat-msg-header">
                <strong>${senderName}</strong>
                <span class="chat-msg-time">${time}</span>
            </div>
            <div class="chat-msg-body">${content}</div>
        </div>`;
    },

    async sendFullPageChat(e, courtId) {
        e.preventDefault();
        const input = document.getElementById('fullpage-chat-input');
        const content = input.value.trim();
        if (!content) return;
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        try {
            await API.post('/api/chat/send', {
                content, court_id: courtId, msg_type: 'court',
            });
            input.value = '';
            MapView._loadFullPageChat(courtId);
        } catch { App.toast('Failed to send message', 'error'); }
    },

    _courtDetailHTML(court, sessions) {
        sessions = sessions || [];
        const checkedIn = court.checked_in_users || [];
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const myStatus = MapView.myCheckinStatus || {};
        const amCheckedInHere = myStatus.checked_in && myStatus.court_id === court.id;
        const events = court.upcoming_events || [];
        const images = court.images || [];
        const communityInfo = court.community_info || {};
        const pendingUpdates = court.pending_updates_count || 0;
        const safeCourtName = MapView._escapeHtml(court.name || '');
        const safeCourtAddress = MapView._escapeHtml(court.address || '');
        const safeCourtCity = MapView._escapeHtml(court.city || '');
        const safeCourtZip = MapView._escapeHtml(court.zip_code || '');
        const safeDescription = MapView._escapeHtml(court.description || '');
        const safeSurfaceType = MapView._escapeHtml(court.surface_type || 'Unknown');
        const safeFees = MapView._escapeHtml(court.fees || 'Unknown');
        const safeSkillLevels = MapView._escapeHtml((court.skill_levels || 'all').replace(/,/g, ', '));
        const safeHours = MapView._escapeHtml(court.hours || '');
        const safeOpenPlaySchedule = MapView._escapeHtml(court.open_play_schedule || '');
        const safePhoneLabel = MapView._escapeHtml(court.phone || '');
        const safePhoneHref = MapView._safeTel(court.phone || '');
        const safeWebsiteHref = MapView._safeHttpUrl(court.website || '');
        const directionsHref = MapView._formatDirectionsUrl(court.latitude, court.longitude);
        const escapedCourtName = MapView._escapeAttr(
            MapView._escapeJsSingleQuoted(court.name || '')
        );

        // Amenities
        const amenities = [];
        if (court.has_restrooms) amenities.push('<span class="amenity">🚻 Restrooms</span>');
        if (court.has_parking) amenities.push('<span class="amenity">🅿️ Parking</span>');
        if (court.has_water) amenities.push('<span class="amenity">💧 Water</span>');
        if (court.lighted) amenities.push('<span class="amenity">💡 Lighted</span>');
        if (court.nets_provided) amenities.push('<span class="amenity">🥅 Nets Provided</span>');
        if (court.paddle_rental) amenities.push('<span class="amenity">🏓 Paddle Rental</span>');
        if (court.has_pro_shop) amenities.push('<span class="amenity">🛒 Pro Shop</span>');
        if (court.has_ball_machine) amenities.push('<span class="amenity">⚙️ Ball Machine</span>');
        if (court.wheelchair_accessible) amenities.push('<span class="amenity">♿ Accessible</span>');

        const typeLabel = court.court_type === 'dedicated' ? 'Dedicated Pickleball' :
                          court.court_type === 'converted' ? 'Converted (tennis lines)' : 'Shared Facility';

        const liveSections = MapView._buildLiveCourtSections(
            court,
            sessions,
            currentUser.id,
            amCheckedInHere,
        );
        const nowSessions = liveSections.nowSessions;
        const sessionsHTML = liveSections.sessionsHTML;
        const playersHTML = liveSections.playersHTML;
        const players = liveSections.activePlayers;

        const checkinBtnHTML = MapView._checkinBarHTML({
            courtId: court.id,
            safeCourtName,
            amCheckedInHere,
            currentUserId: currentUser.id,
            nowSessions,
            fullPage: false,
        });

        return `
        <div class="court-detail-page">
            <div class="court-header">
                <h2>${safeCourtName}</h2>
                ${court.verified ? '<span class="verified-badge">✓ Verified</span>' : ''}
                <button class="btn-expand-court" onclick="MapView.openCourtFullPage(${court.id})" title="Expand to full page">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>
                </button>
            </div>
            <p class="court-address">${safeCourtAddress}, ${safeCourtCity}, CA ${safeCourtZip}</p>

            ${checkinBtnHTML}

            <div id="court-live-status-panel" class="court-live-status ${players > 0 ? 'active' : ''}">
                ${MapView._liveStatusInnerHTML(court, players)}
            </div>

            ${court.description ? `<p class="court-desc">${safeDescription}</p>` : ''}

            <!-- Open to Play Sessions -->
            <div class="court-section">
                <div class="section-header">
                    <h4>🎯 Looking to Play</h4>
                    <button class="btn-secondary btn-sm" onclick="Sessions.showCreateModal(${court.id})">📅 Schedule</button>
                </div>
                <div id="court-sessions-live-panel">${sessionsHTML}</div>
            </div>

            <!-- Who's Here — Players Section (prominent) -->
            <div class="court-section players-section">
                <div class="section-header">
                    <h4>Who's Here</h4>
                    <span id="court-player-count-panel" class="player-count-badge">${checkedIn.length}</span>
                </div>
                <div id="court-players-live-panel">${playersHTML}</div>
            </div>

            <!-- Competitive Play Section (loads dynamically) -->
            <div class="court-section" id="court-ranked-section">
                <div class="loading">Loading competitive play...</div>
            </div>

            <!-- Quick Actions -->
            <div class="court-section">
                <h4>Quick Actions</h4>
                <div class="quick-actions-grid">
                    <button class="quick-action-btn" onclick="Sessions.showCreateModal(${court.id})">
                        <span class="quick-action-icon">📅</span>
                        <span>Schedule</span>
                    </button>
                    <button class="quick-action-btn" onclick="MapView.inviteToCourtPlay(${court.id}, '${escapedCourtName}')">
                        <span class="quick-action-icon">👥</span>
                        <span>Invite Friends</span>
                    </button>
                    <button class="quick-action-btn" onclick="CourtUpdates.openModal(${court.id}, '${escapedCourtName}')">
                        <span class="quick-action-icon">📝</span>
                        <span>Suggest Update</span>
                    </button>
                    <button class="quick-action-btn" onclick="MapView.reportCourt(${court.id})">
                        <span class="quick-action-icon">⚠️</span>
                        <span>Report Issue</span>
                    </button>
                </div>
            </div>

            <!-- Court Info -->
            <div class="court-section court-info-collapsible">
                <h4 onclick="MapView._toggleSection(this)">Court Info ▾</h4>
                <div class="collapsible-content">
                    <div class="court-info-grid">
                        <div class="info-item"><span class="info-label">Type</span><span>${court.indoor ? '🏢 Indoor' : '☀️ Outdoor'}</span></div>
                        <div class="info-item"><span class="info-label">Courts</span><span>${court.num_courts}</span></div>
                        <div class="info-item"><span class="info-label">Surface</span><span>${safeSurfaceType}</span></div>
                        <div class="info-item"><span class="info-label">Setup</span><span>${typeLabel}</span></div>
                        <div class="info-item"><span class="info-label">Fees</span><span>${safeFees}</span></div>
                        <div class="info-item"><span class="info-label">Skill Levels</span><span>${safeSkillLevels}</span></div>
                        ${court.hours ? `<div class="info-item info-item-full"><span class="info-label">Hours</span><span>${safeHours}</span></div>` : ''}
                    </div>

                    ${court.open_play_schedule ? `<div class="court-sub-section"><strong>Open Play Schedule</strong><p class="schedule-text">${safeOpenPlaySchedule}</p></div>` : ''}

                    <div class="court-sub-section">
                        <strong>Amenities</strong>
                        <div class="amenities-grid">${amenities.join('') || '<span class="muted">No amenities listed</span>'}</div>
                    </div>
                </div>
            </div>

            <div class="court-section">
                <div class="section-header">
                    <h4>🧾 Community Info</h4>
                    ${pendingUpdates > 0 ? `<span class="player-count-badge">${pendingUpdates} pending</span>` : ''}
                    <button class="btn-secondary btn-sm" onclick="CourtUpdates.openModal(${court.id}, '${escapedCourtName}')">+ Suggest Update</button>
                </div>
                ${MapView._communityInfoHTML(communityInfo)}
            </div>

            <div class="court-section">
                <div class="section-header">
                    <h4>🖼 Court Images</h4>
                    <button class="btn-secondary btn-sm" onclick="CourtUpdates.openModal(${court.id}, '${escapedCourtName}')">+ Add Photo</button>
                </div>
                ${MapView._imageGalleryHTML(images)}
            </div>

            <div class="court-section">
                <div class="section-header">
                    <h4>📅 Upcoming Events</h4>
                    <button class="btn-secondary btn-sm" onclick="CourtUpdates.openModal(${court.id}, '${escapedCourtName}')">+ Add Event</button>
                </div>
                ${MapView._eventCardsHTML(events)}
            </div>

            <div class="court-section">
                <h4>📊 Busyness Patterns</h4>
                ${MapView._busynessChart(court.busyness)}
            </div>

            <div class="court-contact">
                ${court.phone ? `<a href="tel:${safePhoneHref}" class="btn-secondary btn-sm">📞 ${safePhoneLabel}</a>` : ''}
                ${safeWebsiteHref ? `<a href="${safeWebsiteHref}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-sm">🌐 Website</a>` : ''}
                <a href="${directionsHref}" target="_blank" rel="noopener noreferrer" class="btn-primary btn-sm">🗺 Get Directions</a>
            </div>
        </div>`;
    },

    _playerCard(user, isLookingToPlay, currentUserId, courtId, amCheckedInHere) {
        const isMe = user.id === currentUserId;
        const isFriend = App.friendIds.includes(user.id);
        const initials = (user.name || user.username || '?').split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2);
        const elo = user.elo_rating ? Math.round(user.elo_rating) : 1200;
        const record = `${user.wins || 0}W-${user.losses || 0}L`;
        const timeAgo = user.checked_in_at ? MapView._timeAgo(user.checked_in_at) : '';
        const safeInitials = MapView._escapeHtml(initials);
        const safeName = MapView._escapeHtml(user.name || user.username);
        const safeRecord = MapView._escapeHtml(record);
        const safeTimeAgo = MapView._escapeHtml(timeAgo);

        // Add Friend button for non-self, non-friend, logged-in users
        let actionBtn = '';
        if (!isMe && currentUserId) {
            if (isFriend) {
                actionBtn = '<span class="friend-indicator" title="Friend">👥</span>';
            } else {
                actionBtn = `<button class="btn-add-friend" onclick="event.stopPropagation(); MapView.sendFriendRequest(${user.id})" title="Add Friend">➕ Add</button>`;
            }
        }
        const challengeBtn = (!isMe && currentUserId && amCheckedInHere)
            ? `<button class="btn-add-friend" onclick="event.stopPropagation(); Ranked.challengeCheckedInPlayer(${courtId}, ${user.id})" title="Challenge this player">⚔️ Challenge</button>`
            : '';

        return `
        <div class="player-card ${isLookingToPlay ? 'player-lfg' : ''} ${isMe ? 'player-me' : ''}">
            <div class="player-card-avatar ${isLookingToPlay ? 'avatar-lfg' : ''}">${safeInitials}</div>
            <div class="player-card-info">
                <div class="player-card-name">${safeName}${isMe ? ' (You)' : ''}</div>
                <div class="player-card-meta">
                    <span class="player-elo-badge">ELO ${elo}</span>
                    <span class="player-record">${safeRecord}</span>
                    ${timeAgo ? `<span class="player-since">· ${safeTimeAgo}</span>` : ''}
                </div>
            </div>
            ${isLookingToPlay ? '<span class="lfg-badge">🎯 Looking to Play</span>' : ''}
            ${challengeBtn}
            ${actionBtn}
        </div>`;
    },

    _splitPlayersByLookingToPlay(checkedInUsers, nowSessions) {
        const nowCreatorIds = new Set((nowSessions || []).map(session => session.creator_id));
        return {
            lookingToPlayPlayers: (checkedInUsers || []).filter(user => nowCreatorIds.has(user.id)),
            otherPlayers: (checkedInUsers || []).filter(user => !nowCreatorIds.has(user.id)),
        };
    },

    _buildLiveCourtSections(court, sessions, currentUserId, amCheckedInHere) {
        const allSessions = sessions || [];
        const checkedIn = court.checked_in_users || [];
        const nowSessions = allSessions.filter(s => s.session_type === 'now');
        const scheduledSessions = allSessions.filter(s => s.session_type === 'scheduled');
        const playerBuckets = MapView._splitPlayersByLookingToPlay(checkedIn, nowSessions);
        const lookingToPlayPlayers = playerBuckets.lookingToPlayPlayers;
        const otherPlayers = playerBuckets.otherPlayers;

        let sessionsHTML = '';
        if (nowSessions.length > 0) {
            sessionsHTML += '<h5 class="session-sub-heading">🎯 Looking to Play Now</h5>';
            sessionsHTML += Sessions.renderMiniCards(nowSessions);
        }
        if (scheduledSessions.length > 0) {
            sessionsHTML += '<h5 class="session-sub-heading" style="margin-top:10px">📅 Scheduled</h5>';
            sessionsHTML += Sessions.renderMiniCards(scheduledSessions);
        }
        if (!allSessions.length) {
            sessionsHTML = '<p class="muted">No looking-to-play sessions yet. Be the first to start one!</p>';
        }

        let playersHTML = '';
        if (checkedIn.length === 0) {
            playersHTML = '<p class="muted">No one checked in right now. Be the first!</p>';
        } else {
            if (lookingToPlayPlayers.length > 0) {
                playersHTML += `<div class="lfg-group"><h5>🎯 Looking to Play Now (${lookingToPlayPlayers.length})</h5>`;
                playersHTML += lookingToPlayPlayers.map(u => MapView._playerCard(
                    u, true, currentUserId, court.id, amCheckedInHere
                )).join('');
                playersHTML += '</div>';
            }
            if (otherPlayers.length > 0) {
                playersHTML += `<div class="players-group"><h5>🏓 At the Court (${otherPlayers.length})</h5>`;
                playersHTML += otherPlayers.map(u => MapView._playerCard(
                    u, false, currentUserId, court.id, amCheckedInHere
                )).join('');
                playersHTML += '</div>';
            }
        }

        let matchBannerHTML = '';
        if (lookingToPlayPlayers.length >= 4) {
            matchBannerHTML = '<div class="match-ready-banner">🎉 4+ players looking to play now! Start a doubles match!</div>';
        } else if (lookingToPlayPlayers.length >= 2) {
            matchBannerHTML = `<div class="match-ready-banner singles">🎾 ${lookingToPlayPlayers.length} players looking to play now — enough for singles!</div>`;
        }

        return {
            nowSessions,
            sessionsHTML,
            playersHTML,
            checkedInCount: checkedIn.length,
            activePlayers: court.active_players || checkedIn.length,
            matchBannerHTML,
        };
    },

    _liveStatusInnerHTML(court, activePlayers) {
        const players = Number(activePlayers) || 0;
        const visitors = court.recent_visitors
            ? `<span class="muted">(${court.recent_visitors} visited today)</span>`
            : '';
        return `
            <div class="live-dot"></div>
            <span>${players > 0 ? `${players} player${players > 1 ? 's' : ''} here now` : 'No active players'}</span>
            ${visitors}
        `;
    },

    _quickPlayDurations() {
        return [60, 90, 120, 180];
    },

    _formatQuickDuration(minutes) {
        const mins = Number(minutes);
        if (!Number.isFinite(mins) || mins <= 0) return 'Custom';
        const hours = Math.floor(mins / 60);
        const remaining = mins % 60;
        if (!remaining) return `${hours}h`;
        if (remaining === 30) return `${hours}.5h`;
        return `${hours}h ${remaining}m`;
    },

    _nowSessionByCreator(nowSessions, creatorId) {
        if (!creatorId) return null;
        return (nowSessions || []).find(session => session.creator_id === creatorId) || null;
    },

    _nowSessionDurationMinutes(session) {
        if (!session || !session.end_time) return null;
        const startRaw = session.start_time || session.created_at;
        if (!startRaw) return null;
        const startAt = new Date(startRaw);
        const endAt = new Date(session.end_time);
        if (Number.isNaN(startAt.getTime()) || Number.isNaN(endAt.getTime()) || endAt <= startAt) {
            return null;
        }
        return Math.round((endAt.getTime() - startAt.getTime()) / 60000);
    },

    _nowSessionEndsText(session) {
        if (!session || !session.end_time) return '';
        const endAt = new Date(session.end_time);
        if (Number.isNaN(endAt.getTime())) return '';
        const endLabel = endAt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
        return ` until ${endLabel}`;
    },

    _checkinBarHTML({ courtId, safeCourtName, amCheckedInHere, currentUserId, nowSessions, fullPage }) {
        if (!amCheckedInHere) {
            const checkInAction = fullPage ? 'MapView.fullPageCheckIn' : 'MapView.checkIn';
            return `
                <div class="checkin-status-bar">
                    <button class="btn-primary btn-full" onclick="${checkInAction}(${courtId})">📍 Check In at ${safeCourtName}</button>
                    <p class="checkin-hint">Check in first, then choose how many hours you're looking to play now.</p>
                </div>`;
        }

        const checkOutAction = fullPage ? 'MapView.fullPageCheckOut' : 'MapView.checkOut';
        const createAction = fullPage ? 'MapView.fullPageStartLookingToPlayNow' : 'MapView.startLookingToPlayNow';
        const myNowSession = MapView._nowSessionByCreator(nowSessions, currentUserId);
        const activeDuration = MapView._nowSessionDurationMinutes(myNowSession);
        const durationButtons = MapView._quickPlayDurations()
            .map(minutes => `
                <button
                    class="session-quick-btn ${activeDuration === minutes ? 'active' : ''}"
                    onclick="${createAction}(${courtId}, ${minutes})"
                >${MapView._formatQuickDuration(minutes)}</button>
            `)
            .join('');
        const helpText = myNowSession
            ? `You're looking to play now${MapView._nowSessionEndsText(myNowSession)}. Tap a duration to update.`
            : 'Start a Looking to Play session now:';

        return `
            <div class="checkin-status-bar checked-in">
                <div class="checkin-status-info">
                    <span class="checkin-dot"></span>
                    <span>You're checked in here</span>
                </div>
                <div class="checkin-actions">
                    <button class="btn-sm btn-secondary" onclick="${checkOutAction}(${courtId})">Check Out</button>
                </div>
                <div class="checkin-play-controls">
                    <div class="checkin-play-header">
                        <span>🎯 Looking to Play</span>
                        <button class="btn-secondary btn-sm" onclick="Sessions.showCreateModal(${courtId})">📅 Schedule</button>
                    </div>
                    <p class="checkin-play-help">${helpText}</p>
                    <div class="session-duration-buttons checkin-duration-buttons">
                        ${durationButtons}
                    </div>
                </div>
            </div>`;
    },

    async sendFriendRequest(userId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        try {
            await API.post('/api/auth/friends/request', { friend_id: userId });
            App.toast('Friend request sent!');
            // Refresh friends cache
            await App.loadFriendsCache();
        } catch (err) {
            const msg = err.message || '';
            if (msg.includes('already')) App.toast('Friend request already sent');
            else App.toast(msg || 'Failed to send request', 'error');
        }
    },

    inviteToCourtPlay(courtId, courtName) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        App.showInviteModal(`Invite Friends to ${courtName}`, async (friendIds) => {
            try {
                const res = await API.post(`/api/courts/${courtId}/invite`, { friend_ids: friendIds });
                App.toast(res.message || 'Invites sent!');
                App.hideInviteModal();
            } catch (err) {
                App.toast(err.message || 'Failed to send invites', 'error');
            }
        });
    },

    _timeAgo(isoStr) {
        const diff = Date.now() - new Date(isoStr).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return 'just now';
        if (mins < 60) return `${mins}m ago`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h ago`;
        return '';
    },

    _toggleSection(headerEl) {
        const content = headerEl.nextElementSibling;
        const isOpen = content.style.display !== 'none';
        content.style.display = isOpen ? 'none' : 'block';
        headerEl.textContent = headerEl.textContent.replace(/[▾▸]/, isOpen ? '▸' : '▾');
    },

    _busynessChart(data) {
        if (!data || Object.keys(data).length === 0) {
            return '<p class="muted">Not enough data yet — check in to help build busyness patterns!</p>';
        }

        const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
        const hours = [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20];

        let html = '<div class="busyness-chart"><table class="busyness-table"><tr><th></th>';
        hours.forEach(h => { html += `<th>${h > 12 ? h - 12 + 'p' : h + 'a'}</th>`; });
        html += '</tr>';

        days.forEach(day => {
            html += `<tr><td class="day-label">${day}</td>`;
            hours.forEach(h => {
                const val = (data[day] && data[day][h]) || 0;
                const intensity = Math.min(val / 5, 1);
                const bg = intensity > 0
                    ? `rgba(34, 197, 94, ${0.2 + intensity * 0.8})`
                    : '#f3f4f6';
                html += `<td class="busy-cell" style="background:${bg}" title="${day} ${h}:00 — avg ${val} players"></td>`;
            });
            html += '</tr>';
        });
        html += '</table></div>';
        return html;
    },

    _formatEventDate(startIso, endIso) {
        if (!startIso) return '';
        const start = new Date(startIso);
        if (Number.isNaN(start.getTime())) return '';
        const startStr = start.toLocaleString('en-US', {
            weekday: 'short', month: 'short', day: 'numeric',
            hour: 'numeric', minute: '2-digit',
        });
        if (!endIso) return startStr;
        const end = new Date(endIso);
        if (Number.isNaN(end.getTime())) return startStr;
        const endStr = end.toLocaleString('en-US', {
            month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
        });
        return `${startStr} - ${endStr}`;
    },

    _eventCardsHTML(events) {
        if (!events || !events.length) {
            return '<p class="muted">No upcoming events listed yet.</p>';
        }
        return events.map(event => `
            <div class="court-event-card">
                <div class="court-event-header">
                    <strong>${MapView._escapeHtml(event.title || 'Event')}</strong>
                    <span class="court-event-time">${MapView._escapeHtml(MapView._formatEventDate(event.start_time, event.end_time))}</span>
                </div>
                ${event.description ? `<p>${MapView._escapeHtml(event.description)}</p>` : ''}
                <div class="court-event-meta">
                    ${event.organizer ? `<span>Organizer: ${MapView._escapeHtml(event.organizer)}</span>` : ''}
                    ${event.contact ? `<span>Contact: ${MapView._escapeHtml(event.contact)}</span>` : ''}
                    ${event.recurring ? `<span>${MapView._escapeHtml(event.recurring)}</span>` : ''}
                    ${event.link ? `<a href="${MapView._escapeAttr(event.link)}" target="_blank">Event Link</a>` : ''}
                </div>
            </div>
        `).join('');
    },

    _imageGalleryHTML(images) {
        if (!images || !images.length) {
            return '<p class="muted">No approved court images yet.</p>';
        }
        return `
            <div class="court-image-gallery">
                ${images.map(img => `
                    <figure class="court-image-card">
                        <img src="${MapView._escapeAttr(img.image_url || '')}" alt="Court image">
                        <figcaption>${MapView._escapeHtml(img.caption || 'Community photo')}</figcaption>
                    </figure>
                `).join('')}
            </div>
        `;
    },

    _communityInfoHTML(communityInfo) {
        if (!communityInfo || Object.keys(communityInfo).length === 0) {
            return '<p class="muted">No additional community notes yet.</p>';
        }
        const fields = [
            ['location_notes', 'Location Notes'],
            ['parking_notes', 'Parking Notes'],
            ['access_notes', 'Access Notes'],
            ['court_rules', 'Court Rules'],
            ['best_times', 'Best Times'],
            ['closure_notes', 'Closure Notes'],
            ['hours_notes', 'Hours Notes'],
            ['additional_info', 'Additional Info'],
        ];
        const rows = fields
            .map(([key, label]) => {
                const value = communityInfo[key];
                if (!value) return '';
                return `
                    <div class="court-community-row">
                        <strong>${label}</strong>
                        <p>${MapView._escapeHtml(value)}</p>
                    </div>
                `;
            })
            .filter(Boolean)
            .join('');
        return rows || '<p class="muted">No additional community notes yet.</p>';
    },

    _escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    },

    _escapeAttr(value) {
        return MapView._escapeHtml(value).replace(/`/g, '&#96;');
    },

    _escapeJsSingleQuoted(value) {
        return String(value || '')
            .replace(/\\/g, '\\\\')
            .replace(/'/g, "\\'")
            .replace(/\r/g, '\\r')
            .replace(/\n/g, '\\n')
            .replace(/<\/script/gi, '<\\/script');
    },

    _safeHttpUrl(value) {
        const raw = String(value || '').trim();
        if (!raw) return '';
        try {
            const parsed = new URL(raw, window.location.origin);
            if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
                return raw;
            }
        } catch {}
        return '';
    },

    _safeTel(value) {
        const raw = String(value || '').trim();
        if (!raw) return '';
        return raw.replace(/[^0-9+().\-\s]/g, '');
    },

    _formatDirectionsUrl(lat, lng) {
        const latitude = Number(lat);
        const longitude = Number(lng);
        if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
            return 'https://www.google.com/maps';
        }
        return `https://www.google.com/maps/dir/?api=1&destination=${latitude},${longitude}`;
    },

    async checkIn(courtId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        try {
            await API.post('/api/presence/checkin', { court_id: courtId });
            if (typeof LocationService !== 'undefined' && typeof LocationService.setCheckedInCourt === 'function') {
                LocationService.setCheckedInCourt(courtId);
            }
            App.toast('Checked in! Others can now see you\'re here.');
            await MapView.refreshMyStatus();
            MapView.openCourtDetail(courtId);
            MapView.loadCourts();
        } catch (err) {
            App.toast('Failed to check in', 'error');
        }
    },

    async checkOut(courtId) {
        try {
            await API.post('/api/presence/checkout', {});
            if (typeof LocationService !== 'undefined' && typeof LocationService.clearCheckedInCourt === 'function') {
                LocationService.clearCheckedInCourt();
            }
            App.toast('Checked out. See you next time!');
            await MapView.refreshMyStatus();
            MapView.openCourtDetail(courtId);
            MapView.loadCourts();
        } catch (err) {
            App.toast('Failed to check out', 'error');
        }
    },

    async startLookingToPlayNow(courtId, durationMinutes = 120) {
        await MapView._createLookingToPlaySession(courtId, durationMinutes, false);
    },

    async fullPageStartLookingToPlayNow(courtId, durationMinutes = 120) {
        await MapView._createLookingToPlaySession(courtId, durationMinutes, true);
    },

    async _createLookingToPlaySession(courtId, durationMinutes, fullPage) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }

        const parsedDuration = parseInt(durationMinutes, 10);
        if (!Number.isFinite(parsedDuration) || parsedDuration <= 0) {
            App.toast('Pick a valid play duration', 'error');
            return;
        }

        await MapView.refreshMyStatus();
        const myStatus = MapView.myCheckinStatus || {};
        if (!myStatus.checked_in || myStatus.court_id !== courtId) {
            App.toast('Check in at this court first, then choose your play duration.', 'error');
            return;
        }

        try {
            await API.post('/api/sessions', {
                court_id: courtId,
                session_type: 'now',
                game_type: 'open',
                skill_level: 'all',
                max_players: 4,
                visibility: 'all',
                duration_minutes: parsedDuration,
            });
            App.toast(`Looking to Play now for ${MapView._formatQuickDuration(parsedDuration)}.`);
            if (fullPage) {
                await MapView._refreshFullPage(courtId);
            } else {
                MapView.openCourtDetail(courtId);
            }
            MapView.loadCourts();
        } catch (err) {
            App.toast(err.message || 'Failed to create looking-to-play session', 'error');
        }
    },

    async reportCourt(courtId) {
        const reason = prompt('What\'s the issue? (wrong info, closed, fake, other)');
        if (!reason) return;
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        try {
            await API.post(`/api/courts/${courtId}/report`, { reason, description: reason });
            App.toast('Report submitted. Thank you!');
        } catch { App.toast('Failed to submit report', 'error'); }
    },

    /** ── Full-Page Court View HTML ────────────────────────────── */
    _courtFullPageHTML(court, sessions) {
        sessions = sessions || [];
        const checkedIn = court.checked_in_users || [];
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const myStatus = MapView.myCheckinStatus || {};
        const amCheckedInHere = myStatus.checked_in && myStatus.court_id === court.id;
        const events = court.upcoming_events || [];
        const images = court.images || [];
        const communityInfo = court.community_info || {};
        const pendingUpdates = court.pending_updates_count || 0;
        const safeCourtName = MapView._escapeHtml(court.name || '');
        const safeCourtAddress = MapView._escapeHtml(court.address || '');
        const safeCourtCity = MapView._escapeHtml(court.city || '');
        const safeCourtZip = MapView._escapeHtml(court.zip_code || '');
        const safeDescription = MapView._escapeHtml(court.description || '');
        const safeSurfaceType = MapView._escapeHtml(court.surface_type || 'Unknown');
        const safeFees = MapView._escapeHtml(court.fees || 'Unknown');
        const safeSkillLevels = MapView._escapeHtml((court.skill_levels || 'all').replace(/,/g, ', '));
        const safeHours = MapView._escapeHtml(court.hours || '');
        const safeOpenPlaySchedule = MapView._escapeHtml(court.open_play_schedule || '');
        const safePhoneHref = MapView._safeTel(court.phone || '');
        const safeWebsiteHref = MapView._safeHttpUrl(court.website || '');
        const directionsHref = MapView._formatDirectionsUrl(court.latitude, court.longitude);
        const escapedCourtName = MapView._escapeAttr(
            MapView._escapeJsSingleQuoted(court.name || '')
        );

        // Amenities
        const amenities = [];
        if (court.has_restrooms) amenities.push('<span class="amenity">🚻 Restrooms</span>');
        if (court.has_parking) amenities.push('<span class="amenity">🅿️ Parking</span>');
        if (court.has_water) amenities.push('<span class="amenity">💧 Water</span>');
        if (court.lighted) amenities.push('<span class="amenity">💡 Lighted</span>');
        if (court.nets_provided) amenities.push('<span class="amenity">🥅 Nets Provided</span>');
        if (court.paddle_rental) amenities.push('<span class="amenity">🏓 Paddle Rental</span>');
        if (court.has_pro_shop) amenities.push('<span class="amenity">🛒 Pro Shop</span>');
        if (court.has_ball_machine) amenities.push('<span class="amenity">⚙️ Ball Machine</span>');
        if (court.wheelchair_accessible) amenities.push('<span class="amenity">♿ Accessible</span>');

        const typeLabel = court.court_type === 'dedicated' ? 'Dedicated Pickleball' :
                          court.court_type === 'converted' ? 'Converted (tennis lines)' : 'Shared Facility';

        const liveSections = MapView._buildLiveCourtSections(
            court,
            sessions,
            currentUser.id,
            amCheckedInHere,
        );
        const nowSessions = liveSections.nowSessions;
        const sessionsHTML = liveSections.sessionsHTML;
        const playersHTML = liveSections.playersHTML;
        const matchBanner = liveSections.matchBannerHTML;
        const players = liveSections.activePlayers;

        const checkinBtnHTML = MapView._checkinBarHTML({
            courtId: court.id,
            safeCourtName,
            amCheckedInHere,
            currentUserId: currentUser.id,
            nowSessions,
            fullPage: true,
        });

        return `
        <div class="cfp">
            <!-- Top bar -->
            <div class="cfp-topbar">
                <button class="btn-secondary cfp-back-btn" onclick="MapView.closeFullPage()">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
                    Back to Map
                </button>
                <div class="cfp-topbar-info">
                    <h1>${safeCourtName}</h1>
                    ${court.verified ? '<span class="verified-badge">✓ Verified</span>' : ''}
                </div>
                <div class="cfp-topbar-actions">
                    <a href="${directionsHref}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-sm">🗺 Directions</a>
                    ${court.phone ? `<a href="tel:${safePhoneHref}" class="btn-secondary btn-sm">📞 Call</a>` : ''}
                    ${safeWebsiteHref ? `<a href="${safeWebsiteHref}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-sm">🌐 Web</a>` : ''}
                </div>
            </div>

            <p class="cfp-address">${safeCourtAddress}, ${safeCourtCity}, CA ${safeCourtZip}</p>

            ${checkinBtnHTML}

            <div id="court-live-status-full" class="cfp-live-bar ${players > 0 ? 'active' : ''}">
                ${MapView._liveStatusInnerHTML(court, players)}
            </div>

            <div id="court-match-banner-full">${matchBanner}</div>

            <!-- Two-column layout -->
            <div class="cfp-grid">
                <!-- LEFT COLUMN: Sessions + Players + Competitive -->
                <div class="cfp-main">
                    <!-- Open to Play Sessions -->
                    <div class="cfp-card">
                        <div class="section-header">
                            <h3>🎯 Looking to Play</h3>
                            <button class="btn-secondary btn-sm" onclick="Sessions.showCreateModal(${court.id})">📅 Schedule</button>
                        </div>
                        <div id="court-sessions-live-full">${sessionsHTML}</div>
                    </div>

                    <!-- Who's Here -->
                    <div class="cfp-card">
                        <div class="section-header">
                            <h3>Who's Here</h3>
                            <span id="court-player-count-full" class="player-count-badge">${checkedIn.length}</span>
                        </div>
                        <div id="court-players-live-full" class="players-section-inner">
                            ${playersHTML}
                        </div>
                    </div>

                    <!-- Competitive Play (loaded dynamically) -->
                    <div class="cfp-card" id="court-ranked-section">
                        <div class="loading">Loading competitive play...</div>
                    </div>

                    <!-- Quick Actions -->
                    <div class="cfp-card">
                        <h3>Quick Actions</h3>
                        <div class="quick-actions-grid cfp-actions-grid">
                            <button class="quick-action-btn" onclick="Ranked.joinQueue(${court.id})">
                                <span class="quick-action-icon">⚔️</span>
                                <span>Join Queue</span>
                            </button>
                            <button class="quick-action-btn" onclick="MapView.inviteToCourtPlay(${court.id}, '${escapedCourtName}')">
                                <span class="quick-action-icon">👥</span>
                                <span>Invite Friends</span>
                            </button>
                            <button class="quick-action-btn" onclick="CourtUpdates.openModal(${court.id}, '${escapedCourtName}')">
                                <span class="quick-action-icon">📝</span>
                                <span>Suggest Update</span>
                            </button>
                            <button class="quick-action-btn" onclick="MapView.reportCourt(${court.id})">
                                <span class="quick-action-icon">⚠️</span>
                                <span>Report Issue</span>
                            </button>
                        </div>
                    </div>

                    <!-- Court Info -->
                    <div class="cfp-card">
                        <h3>Court Info</h3>
                        ${court.description ? `<p class="court-desc">${safeDescription}</p>` : ''}
                        <div class="court-info-grid">
                            <div class="info-item"><span class="info-label">Type</span><span>${court.indoor ? '🏢 Indoor' : '☀️ Outdoor'}</span></div>
                            <div class="info-item"><span class="info-label">Courts</span><span>${court.num_courts}</span></div>
                            <div class="info-item"><span class="info-label">Surface</span><span>${safeSurfaceType}</span></div>
                            <div class="info-item"><span class="info-label">Setup</span><span>${typeLabel}</span></div>
                            <div class="info-item"><span class="info-label">Fees</span><span>${safeFees}</span></div>
                            <div class="info-item"><span class="info-label">Skill Levels</span><span>${safeSkillLevels}</span></div>
                            ${court.hours ? `<div class="info-item info-item-full"><span class="info-label">Hours</span><span>${safeHours}</span></div>` : ''}
                        </div>
                        ${court.open_play_schedule ? `<div class="court-sub-section" style="margin-top:12px"><strong>Open Play Schedule</strong><p class="schedule-text">${safeOpenPlaySchedule}</p></div>` : ''}
                        <div class="court-sub-section" style="margin-top:12px">
                            <strong>Amenities</strong>
                            <div class="amenities-grid">${amenities.join('') || '<span class="muted">No amenities listed</span>'}</div>
                        </div>
                    </div>

                    <div class="cfp-card">
                        <div class="section-header">
                            <h3>🧾 Community Info</h3>
                            ${pendingUpdates > 0 ? `<span class="player-count-badge">${pendingUpdates} pending</span>` : ''}
                            <button class="btn-secondary btn-sm" onclick="CourtUpdates.openModal(${court.id}, '${escapedCourtName}')">+ Suggest Update</button>
                        </div>
                        ${MapView._communityInfoHTML(communityInfo)}
                    </div>

                    <div class="cfp-card">
                        <div class="section-header">
                            <h3>🖼 Court Images</h3>
                            <button class="btn-secondary btn-sm" onclick="CourtUpdates.openModal(${court.id}, '${escapedCourtName}')">+ Add Photo</button>
                        </div>
                        ${MapView._imageGalleryHTML(images)}
                    </div>

                    <div class="cfp-card">
                        <div class="section-header">
                            <h3>📅 Upcoming Events</h3>
                            <button class="btn-secondary btn-sm" onclick="CourtUpdates.openModal(${court.id}, '${escapedCourtName}')">+ Add Event</button>
                        </div>
                        ${MapView._eventCardsHTML(events)}
                    </div>

                    <!-- Busyness Patterns -->
                    <div class="cfp-card">
                        <h3>📊 Busyness Patterns</h3>
                        ${MapView._busynessChart(court.busyness)}
                    </div>
                </div>

                <!-- RIGHT COLUMN: Chat -->
                <div class="cfp-sidebar">
                    <div class="cfp-chat-card">
                        <div class="cfp-chat-header">
                            <h3>💬 Court Chat</h3>
                            <span class="muted">${safeCourtName}</span>
                        </div>
                        <div id="fullpage-chat-messages" class="cfp-chat-messages"></div>
                        <form class="cfp-chat-input" onsubmit="MapView.sendFullPageChat(event, ${court.id})">
                            <input type="text" id="fullpage-chat-input" placeholder="Message players at this court..." autocomplete="off">
                            <button type="submit" class="btn-primary btn-sm">Send</button>
                        </form>
                    </div>
                </div>
            </div>
        </div>`;
    },

    /** Close full page and return to map */
    closeFullPage() {
        App.showView('map');
        // Leave chat room
        if (MapView.currentCourtId && Chat.socket) {
            Chat.socket.emit('leave', { room: `court_${MapView.currentCourtId}` });
        }
    },

    /** Full-page check-in (refreshes full page instead of side panel) */
    async fullPageCheckIn(courtId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        try {
            await API.post('/api/presence/checkin', { court_id: courtId });
            if (typeof LocationService !== 'undefined' && typeof LocationService.setCheckedInCourt === 'function') {
                LocationService.setCheckedInCourt(courtId);
            }
            App.toast("Checked in! Others can now see you're here.");
            MapView._refreshFullPage(courtId);
            MapView.loadCourts();
        } catch (err) {
            App.toast('Failed to check in', 'error');
        }
    },

    /** Full-page check-out */
    async fullPageCheckOut(courtId) {
        try {
            await API.post('/api/presence/checkout', {});
            if (typeof LocationService !== 'undefined' && typeof LocationService.clearCheckedInCourt === 'function') {
                LocationService.clearCheckedInCourt();
            }
            App.toast('Checked out. See you next time!');
            MapView._refreshFullPage(courtId);
            MapView.loadCourts();
        } catch (err) {
            App.toast('Failed to check out', 'error');
        }
    },

    renderCourtList() {
        const el = document.getElementById('court-list-items');
        if (!el) return;
        const filtered = MapView._filterCourts(MapView.courts);
        el.innerHTML = filtered.map(c => `
            <div class="court-list-card" onclick="MapView.openCourtDetail(${c.id}); MapView.map.setView([${c.latitude},${c.longitude}], 14);">
                <div class="court-list-name">${MapView._escapeHtml(c.name)}</div>
                <div class="court-list-meta">
                    <span>${c.indoor ? '🏢' : '☀️'} ${MapView._escapeHtml(c.city)}</span>
                    <span>${c.num_courts} court${c.num_courts > 1 ? 's' : ''}</span>
                    ${c.distance !== undefined ? `<span>📍 ${c.distance} mi</span>` : ''}
                    ${c.active_players > 0 ? `<span class="live-badge">🟢 ${c.active_players}</span>` : ''}
                    ${MapView._friendsAtCourt(c.id).length > 0 ? `<span class="friend-badge">👥 ${MapView._friendsAtCourt(c.id).length}</span>` : ''}
                </div>
                ${c.hours ? `<div class="court-list-hours">🕐 ${MapView._escapeHtml(c.hours)}</div>` : ''}
            </div>
        `).join('') || '<p class="muted">No courts match your filter</p>';
    },

    toggleCourtList() {
        MapView.courtListOpen = !MapView.courtListOpen;
        document.getElementById('court-list-sidebar').style.display = MapView.courtListOpen ? 'block' : 'none';
    },
};
