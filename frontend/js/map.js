/**
 * Map integration — Leaflet.js with county-focused loading, court markers,
 * busyness indicators, open-to-play sessions, and direction links.
 */
const MapView = {
    map: null,
    markers: [],
    clusterGroup: null,
    courts: [],
    lastUpdatedAt: null,
    activeFilters: new Set(),
    courtListOpen: false,
    courtListSort: 'name',
    userLocationMarker: null,
    currentCourtId: null,
    currentCourtData: null,
    currentCourtSessions: [],
    myCheckinStatus: null, // { checked_in: bool, court_id }
    friendsPresence: [],  // [{user, court_id, checked_in_at}]
    friendMarkers: [],
    nearbyModeEnabled: false,
    nearbyRadiusMiles: 25,
    scheduleBannerOpen: false,
    scheduleBannerDays: [],
    scheduleBannerItems: [],
    scheduleBannerLoading: false,
    scheduleBannerError: '',
    scheduleBannerLastLoadedAt: 0,
    scheduleBannerLoadPromise: null,
    scheduleBannerCountySlug: null,
    courtPageScrollHandler: null,

    init() {
        MapView.map = L.map('map', {
            zoomControl: true,
        }).setView([40.83, -124.08], 11);

        L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png', {
            attribution: '&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>, &copy; <a href="https://carto.com">CARTO</a>',
            subdomains: 'abcd',
            maxZoom: 20,
        }).addTo(MapView.map);

        MapView.clusterGroup = L.markerClusterGroup({
            maxClusterRadius: 50,
            spiderfyOnMaxZoom: true,
            showCoverageOnHover: false,
            zoomToBoundsOnClick: true,
            iconCreateFunction(cluster) {
                const count = cluster.getChildCount();
                let size = 'small';
                if (count > 30) size = 'large';
                else if (count > 10) size = 'medium';
                return L.divIcon({
                    html: `<div class="cluster-marker cluster-${size}"><span>${count}</span></div>`,
                    className: 'court-cluster-icon',
                    iconSize: L.point(40, 40),
                });
            },
        });
        MapView.map.addLayer(MapView.clusterGroup);

        MapView._renderScheduleBanner();
        MapView.loadCourts();
        MapView.refreshMyStatus();
        MapView._autoLocate();
    },

    _autoLocate() {
        const pref = localStorage.getItem('location_tracking_pref');
        if (pref !== 'enabled') return;
        navigator.geolocation?.getCurrentPosition(pos => {
            const { latitude, longitude } = pos.coords;
            if (typeof LocationService !== 'undefined') {
                LocationService.lastPosition = { lat: latitude, lng: longitude, accuracy: pos.coords.accuracy || null };
            }
            MapView._placeUserDot(latitude, longitude);
            MapView.loadCourts();
        }, () => {}, { enableHighAccuracy: false, timeout: 8000, maximumAge: 120000 });
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
                ? App.buildCourtsQuery(MapView._courtQueryParams())
                : '/api/courts';
            const [res] = await Promise.all([
                API.get(courtsUrl),
                MapView.loadFriendsPresence(),
            ]);
            MapView.courts = res.courts || [];
            MapView.renderMarkers();
            MapView.renderCourtList();
            MapView._updateCourtCount();
            MapView._syncFilterChips();
            if (fitToCourts && MapView.courts.length) {
                const bounds = L.latLngBounds(MapView.courts.map(c => [c.latitude, c.longitude]));
                MapView.map.flyToBounds(bounds, { padding: [50, 50], maxZoom: 12, duration: 0.8 });
            }
            MapView._setLastUpdated();
            MapView.loadScheduleBanner();
        } catch (err) {
            console.error('Failed to load courts:', err);
        }
    },

    _updateCourtCount() {
        const el = document.getElementById('map-court-count');
        if (!el) return;
        const filtered = MapView._filterCourts(MapView.courts);
        const total = MapView.courts.length;
        const nearbyLabel = MapView.nearbyModeEnabled
            ? ` within ${MapView.nearbyRadiusMiles} mi`
            : '';
        if (MapView.activeFilters.size > 0 && filtered.length !== total) {
            el.textContent = `${filtered.length} of ${total}${nearbyLabel}`;
        } else {
            el.textContent = `${total} courts${nearbyLabel}`;
        }
    },

    _courtQueryParams(extraParams = {}) {
        const params = { ...(extraParams || {}) };
        if (MapView.nearbyModeEnabled) {
            params.radius = MapView.nearbyRadiusMiles;
        }
        return params;
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

    toggleScheduleBanner(forceOpen) {
        const nextState = typeof forceOpen === 'boolean'
            ? forceOpen
            : !MapView.scheduleBannerOpen;
        MapView.scheduleBannerOpen = nextState;
        MapView._renderScheduleBanner();
        if (nextState && !MapView.scheduleBannerDays.length) {
            MapView.loadScheduleBanner({ force: true });
        }
    },

    async loadScheduleBanner(options = {}) {
        const banner = document.getElementById('map-schedule-banner');
        if (!banner) return;

        const forceRequested = !!options.force;
        const countySlug = (typeof App !== 'undefined' && typeof App.getSelectedCountySlug === 'function')
            ? App.getSelectedCountySlug()
            : null;
        const countyChanged = countySlug && countySlug !== MapView.scheduleBannerCountySlug;
        const force = forceRequested || countyChanged;
        const freshnessMs = Date.now() - MapView.scheduleBannerLastLoadedAt;

        if (!force && MapView.scheduleBannerDays.length > 0 && freshnessMs < 45000) {
            MapView._renderScheduleBanner();
            return;
        }
        if (MapView.scheduleBannerLoading && MapView.scheduleBannerLoadPromise) {
            return MapView.scheduleBannerLoadPromise;
        }

        MapView.scheduleBannerLoading = true;
        MapView.scheduleBannerError = '';
        MapView._renderScheduleBanner();

        const token = localStorage.getItem('token');
        MapView.scheduleBannerLoadPromise = (async () => {
            try {
                const fetches = [
                    API.get('/api/sessions?type=scheduled'),
                    API.get('/api/ranked/tournaments/upcoming?days=14'),
                ];
                if (token) {
                    fetches.push(API.get('/api/ranked/lobby/my-lobbies').catch(() => ({ lobbies: [] })));
                }
                const results = await Promise.all(fetches);
                const sessions = Array.isArray(results[0]?.sessions) ? results[0].sessions : [];
                const tournaments = Array.isArray(results[1]?.tournaments) ? results[1].tournaments : [];
                const rankedLobbies = token && results[2]
                    ? (Array.isArray(results[2]?.lobbies) ? results[2].lobbies : [])
                    : [];
                const normalizedTournaments = tournaments.map(t => ({
                    ...t,
                    item_type: 'tournament',
                    session_type: 'scheduled',
                }));
                const normalizedLobbies = rankedLobbies
                    .filter(l => l.scheduled_for || l.status === 'pending_acceptance' || l.status === 'ready')
                    .map(l => ({
                        ...l,
                        item_type: 'ranked_lobby',
                        session_type: 'scheduled',
                        start_time: l.scheduled_for || l.created_at,
                    }));
                const scheduleItems = [...sessions, ...normalizedTournaments, ...normalizedLobbies];
                MapView.scheduleBannerItems = scheduleItems;
                MapView.scheduleBannerDays = MapView._buildScheduleBannerDays(scheduleItems);
                MapView.scheduleBannerLastLoadedAt = Date.now();
                MapView.scheduleBannerCountySlug = countySlug;
            } catch {
                if (!MapView.scheduleBannerDays.length) {
                    MapView.scheduleBannerDays = MapView._buildScheduleBannerDays([]);
                }
                MapView.scheduleBannerError = 'Unable to load upcoming sessions';
            } finally {
                MapView.scheduleBannerLoading = false;
                MapView.scheduleBannerLoadPromise = null;
                MapView._renderScheduleBanner();
            }
        })();

        return MapView.scheduleBannerLoadPromise;
    },

    openScheduledDayFromBanner(dayKey) {
        if (!dayKey) return;
        MapView.openDayPopup(dayKey);
    },

    openDayPopup(dayKey, courtId) {
        if (!dayKey) return;
        const popup = document.getElementById('schedule-day-popup');
        if (!popup) return;

        const date = MapView._dateFromKey(dayKey);
        const titleEl = document.getElementById('schedule-day-popup-title');
        const bodyEl = document.getElementById('schedule-day-popup-body');
        if (!date || !titleEl || !bodyEl) return;

        const dateLabel = date.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
        titleEl.textContent = dateLabel;
        bodyEl.innerHTML = MapView._renderDayPopupBody(dayKey, courtId);
        popup.style.display = 'flex';
    },

    closeDayPopup() {
        const popup = document.getElementById('schedule-day-popup');
        if (popup) popup.style.display = 'none';
    },

    scrollToCourtSection(sectionId, sourceButton = null) {
        MapView._setActiveCourtSection(sectionId, sourceButton);
        const root = document.getElementById('court-page-content');
        const el = document.getElementById(sectionId);
        if (!el) return;
        if (!root) {
            el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            return;
        }
        const stickyNav = root.querySelector('.court-sticky-nav');
        const rootRect = root.getBoundingClientRect();
        const elRect = el.getBoundingClientRect();
        const stickyOffset = stickyNav ? stickyNav.getBoundingClientRect().height + 8 : 0;
        const nextScrollTop = root.scrollTop + (elRect.top - rootRect.top) - stickyOffset;
        root.scrollTop = Math.max(0, nextScrollTop);
    },

    _setActiveCourtSection(sectionId, sourceButton = null) {
        const buttons = document.querySelectorAll('.court-sticky-nav-btn');
        buttons.forEach(btn => {
            const isActive = sourceButton
                ? btn === sourceButton
                : btn.dataset.target === sectionId;
            btn.classList.toggle('active', isActive);
            if (isActive) {
                btn.setAttribute('aria-current', 'true');
                btn.scrollIntoView({ inline: 'center', block: 'nearest' });
            } else {
                btn.removeAttribute('aria-current');
            }
        });
    },

    _initCourtPageUi() {
        MapView._teardownCourtPageUi();
        const root = document.getElementById('court-page-content');
        const navButtons = Array.from(document.querySelectorAll('.court-sticky-nav-btn'));
        if (!root || !navButtons.length) return;

        const sections = navButtons
            .map(btn => document.getElementById(btn.dataset.target))
            .filter(Boolean);
        if (!sections.length) return;

        const updateActiveSection = () => {
            const rootTop = root.getBoundingClientRect().top;
            const threshold = rootTop + 120;
            let activeSectionId = sections[0].id;
            sections.forEach(section => {
                if (section.getBoundingClientRect().top <= threshold) {
                    activeSectionId = section.id;
                }
            });
            MapView._setActiveCourtSection(activeSectionId);
        };

        MapView.courtPageScrollHandler = updateActiveSection;
        root.addEventListener('scroll', updateActiveSection, { passive: true });
        window.addEventListener('resize', updateActiveSection);
        requestAnimationFrame(updateActiveSection);
    },

    _teardownCourtPageUi() {
        const root = document.getElementById('court-page-content');
        if (root && MapView.courtPageScrollHandler) {
            root.removeEventListener('scroll', MapView.courtPageScrollHandler);
        }
        if (MapView.courtPageScrollHandler) {
            window.removeEventListener('resize', MapView.courtPageScrollHandler);
        }
        MapView.courtPageScrollHandler = null;
    },

    _renderDayPopupBody(dayKey, courtId) {
        const items = MapView._getItemsForDay(dayKey, courtId);
        const courtIdNum = courtId ? Number(courtId) : 0;
        const scheduleActions = MapView._scheduleActionsHTML(courtIdNum, dayKey);

        if (!items.length) {
            return `<div class="schedule-empty-day">
                <h4>No games scheduled</h4>
                <p>Be the first to schedule a game for this day.</p>
                ${scheduleActions}
            </div>`;
        }

        const byHour = {};
        items.forEach(item => {
            const start = new Date(item.start_time);
            const hour = start.getHours();
            const key = `${String(hour).padStart(2, '0')}:00`;
            if (!byHour[key]) byHour[key] = [];
            byHour[key].push(item);
        });

        const sortedHours = Object.keys(byHour).sort();
        let html = sortedHours.map(hourKey => {
            const hourNum = parseInt(hourKey, 10);
            const label = hourNum === 0 ? '12 AM' : hourNum < 12 ? `${hourNum} AM` : hourNum === 12 ? '12 PM' : `${hourNum - 12} PM`;
            const cards = byHour[hourKey].map(item => MapView._renderDayPopupEventCard(item)).join('');
            return `<div class="schedule-hour-group">
                <div class="schedule-hour-label">${label}</div>
                ${cards}
            </div>`;
        }).join('');
        html += `<div class="schedule-popup-actions">${scheduleActions}</div>`;
        return html;
    },

    _scheduleActionsHTML(courtId, dayKey) {
        const cArg = courtId ? String(courtId) : 'null';
        const scheduleCourtArg = courtId ? String(courtId) : 'undefined';
        const safeDayKey = String(dayKey || '').replace(/[^0-9-]/g, '');
        const scheduleDayArg = safeDayKey ? `'${safeDayKey}'` : 'null';
        return `<div class="schedule-action-btns">
            <button class="btn-primary btn-sm" onclick="MapView.closeDayPopup(); Sessions.showCreateModal(${scheduleCourtArg}, ${scheduleDayArg})">Open Play</button>
            <button class="btn-secondary btn-sm" onclick="MapView.closeDayPopup(); Ranked.openCourtScheduledChallenge(${cArg})">Ranked Challenge</button>
            <button class="btn-secondary btn-sm" onclick="MapView.closeDayPopup(); Ranked.showCreateTournamentModal(${cArg})">Tournament</button>
        </div>`;
    },

    _getItemsForDay(dayKey, courtId) {
        const date = MapView._dateFromKey(dayKey);
        if (!date) return [];
        const dayStart = MapView._startOfDay(date);
        const dayEnd = new Date(dayStart);
        dayEnd.setDate(dayEnd.getDate() + 1);

        const selectedCounty = (typeof App !== 'undefined' && typeof App.getSelectedCountySlug === 'function')
            ? App.getSelectedCountySlug()
            : '';

        return (MapView.scheduleBannerItems || []).filter(item => {
            if (!item.start_time) return false;
            const start = new Date(item.start_time);
            if (Number.isNaN(start.getTime())) return false;
            if (start < dayStart || start >= dayEnd) return false;
            if (courtId && Number(item.court_id || item.court?.id) !== Number(courtId)) return false;
            if (!courtId && selectedCounty) {
                const itemCounty = String(item.court?.county_slug || '').trim().toLowerCase();
                if (itemCounty && itemCounty !== selectedCounty) return false;
            }
            return true;
        }).sort((a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime());
    },

    _renderDayPopupEventCard(item) {
        const e = MapView._escapeHtml;
        const start = new Date(item.start_time);
        const timeLabel = start.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
        const courtName = e(item.court?.name || 'Court');
        const courtId = Number(item.court_id || item.court?.id || 0);

        if (item.item_type === 'tournament') {
            const name = e(item.name || 'Tournament');
            const players = Number(item.registered_count || item.participants_count || 0);
            const maxP = Number(item.max_players || 0);
            const playerLabel = maxP ? `${players}/${maxP}` : `${players} players`;
            const tId = Number(item.id) || 0;
            return `<div class="schedule-event-card">
                <div class="schedule-event-card-header">
                    <strong>${name}</strong>
                    <span class="schedule-event-type-badge tournament">Tournament</span>
                </div>
                <div class="schedule-event-meta">
                    <span>${timeLabel}</span>
                    <span>${courtName}</span>
                    <span>${playerLabel}</span>
                </div>
                <div class="schedule-event-actions">
                    <button class="btn-primary btn-sm" onclick="MapView.closeDayPopup(); Ranked.openTournament(${tId}, ${courtId})">View Tournament</button>
                    <button class="btn-secondary btn-sm" onclick="MapView.closeDayPopup(); App.openCourtDetails(${courtId})">View Court</button>
                </div>
            </div>`;
        }

        if (item.item_type === 'ranked_lobby') {
            const lobbyId = Number(item.id) || 0;
            const t1 = (item.team1 || []).map(p => e(p.user?.name || p.user?.username || '?')).join(' & ');
            const t2 = (item.team2 || []).map(p => e(p.user?.name || p.user?.username || '?')).join(' & ');
            const matchLabel = t1 && t2 ? `${t1} vs ${t2}` : 'Ranked Match';
            return `<div class="schedule-event-card">
                <div class="schedule-event-card-header">
                    <strong>${matchLabel}</strong>
                    <span class="schedule-event-type-badge ranked">Ranked</span>
                </div>
                <div class="schedule-event-meta">
                    <span>${timeLabel}</span>
                    <span>${courtName}</span>
                </div>
                <div class="schedule-event-actions">
                    <button class="btn-primary btn-sm" onclick="MapView.closeDayPopup(); App.openCourtDetails(${courtId}); setTimeout(() => { const el = document.getElementById('court-ranked-inline'); if (el) el.scrollIntoView({behavior:'smooth'}); }, 400);">View Match</button>
                    <button class="btn-secondary btn-sm" onclick="MapView.closeDayPopup(); App.openCourtDetails(${courtId})">View Court</button>
                </div>
            </div>`;
        }

        const creator = e(item.creator?.name || item.creator?.username || 'Someone');
        const gameType = e(item.game_type || 'open');
        const skillLevel = e(item.skill_level || 'all');
        const playerCount = (item.players || []).length;
        const maxPlayers = Number(item.max_players || 4);
        const sessionId = Number(item.id) || 0;
        const notes = item.notes ? `<p style="font-size:12px;color:var(--text-muted);margin:4px 0 0">${e(item.notes)}</p>` : '';

        const startDate = new Date(item.start_time);
        const dayKey = MapView._dateKey(startDate);

        return `<div class="schedule-event-card">
            <div class="schedule-event-card-header">
                <strong>${creator}'s ${gameType} game</strong>
                <span class="schedule-event-type-badge session">Open to Play</span>
            </div>
            <div class="schedule-event-meta">
                <span>${timeLabel}</span>
                <span>${courtName}</span>
                <span>${playerCount}/${maxPlayers} players</span>
                <span>${skillLevel}</span>
            </div>
            ${notes}
            <div class="schedule-event-actions">
                <button class="btn-primary btn-sm" onclick="MapView.closeDayPopup(); Sessions.openDetailAtCourt(${sessionId}, ${courtId})">Details</button>
                <button class="btn-secondary btn-sm" onclick="MapView.closeDayPopup(); App.openCourtDetails(${courtId}); setTimeout(()=>MapView.openDayPopup('${dayKey}',${courtId}),500)">View at Court</button>
            </div>
        </div>`;
    },

    _buildScheduleBannerDays(scheduleItems) {
        const today = MapView._startOfDay(new Date());
        const endBoundary = new Date(today);
        endBoundary.setDate(today.getDate() + 7);
        const selectedCounty = (typeof App !== 'undefined' && typeof App.getSelectedCountySlug === 'function')
            ? App.getSelectedCountySlug()
            : '';
        const grouped = {};

        (scheduleItems || []).forEach((item) => {
            const isTournament = item?.item_type === 'tournament';
            if (!isTournament && item?.session_type !== 'scheduled') return;
            if (!item?.start_time) return;
            const start = new Date(item.start_time);
            if (Number.isNaN(start.getTime())) return;
            if (start < today || start >= endBoundary) return;

            const sessionCounty = String(item.court?.county_slug || '')
                .trim()
                .toLowerCase();
            if (selectedCounty && sessionCounty && sessionCounty !== selectedCounty) {
                return;
            }

            const key = MapView._dateKey(start);
            if (!grouped[key]) grouped[key] = [];
            grouped[key].push(start);
        });

        const days = [];
        for (let i = 0; i < 7; i += 1) {
            const day = new Date(today);
            day.setDate(today.getDate() + i);
            const key = MapView._dateKey(day);
            const starts = (grouped[key] || []).sort((a, b) => a.getTime() - b.getTime());
            const firstStart = starts[0] || null;
            const firstTimeLabel = firstStart
                ? firstStart.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
                : '';
            days.push({
                key,
                date: day,
                count: starts.length,
                firstTimeLabel,
            });
        }
        return days;
    },

    _renderScheduleBanner() {
        const shell = document.getElementById('map-schedule-banner');
        const toggleBtn = document.getElementById('map-schedule-banner-toggle');
        const daysEl = document.getElementById('map-schedule-banner-days');
        const metaEl = document.getElementById('map-schedule-banner-meta');
        if (!shell || !toggleBtn || !daysEl || !metaEl) return;

        shell.classList.toggle('open', MapView.scheduleBannerOpen);
        toggleBtn.setAttribute('aria-expanded', MapView.scheduleBannerOpen ? 'true' : 'false');

        const days = MapView.scheduleBannerDays.length
            ? MapView.scheduleBannerDays
            : MapView._buildScheduleBannerDays([]);
        const totalGames = days.reduce((sum, day) => sum + day.count, 0);
        const daysWithGames = days.filter(day => day.count > 0).length;

        if (MapView.scheduleBannerLoading) {
            metaEl.textContent = 'Loading upcoming sessions...';
        } else if (MapView.scheduleBannerError) {
            metaEl.textContent = MapView.scheduleBannerError;
        } else if (!totalGames) {
            metaEl.textContent = 'No scheduled games in the next 7 days';
        } else {
            metaEl.textContent = `${totalGames} game${totalGames !== 1 ? 's' : ''} on ${daysWithGames} day${daysWithGames !== 1 ? 's' : ''}`;
        }

        if (!MapView.scheduleBannerOpen) {
            daysEl.hidden = true;
            return;
        }

        const todayKey = MapView._dateKey(new Date());
        daysEl.hidden = false;
        daysEl.innerHTML = days.map((day) => {
            const dayName = day.date.toLocaleDateString('en-US', { weekday: 'short' });
            const dateLabel = day.date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            const hasGames = day.count > 0;
            const countLabel = hasGames
                ? `${day.count} game${day.count > 1 ? 's' : ''}${day.firstTimeLabel ? ` • ${day.firstTimeLabel}` : ''}`
                : 'No games';
            const classes = ['map-schedule-day'];
            if (hasGames) classes.push('has-games');
            if (day.key === todayKey) classes.push('today');
            return `
                <button
                    type="button"
                    class="${classes.join(' ')}"
                    onclick="MapView.openScheduledDayFromBanner('${day.key}')"
                    aria-label="${day.date.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}${hasGames ? `, ${day.count} scheduled game${day.count > 1 ? 's' : ''}` : ', no scheduled games'}"
                >
                    <span class="map-schedule-day-name">${dayName}</span>
                    <span class="map-schedule-day-date">${dateLabel}</span>
                    <span class="map-schedule-day-count">${countLabel}</span>
                </button>
            `;
        }).join('');
    },

    _startOfDay(date) {
        return new Date(date.getFullYear(), date.getMonth(), date.getDate());
    },

    _dateKey(date) {
        const y = date.getFullYear();
        const m = String(date.getMonth() + 1).padStart(2, '0');
        const d = String(date.getDate()).padStart(2, '0');
        return `${y}-${m}-${d}`;
    },

    _dateFromKey(key) {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(key || ''));
        if (!match) return null;
        const year = Number(match[1]);
        const month = Number(match[2]) - 1;
        const day = Number(match[3]);
        if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null;
        const parsed = new Date(year, month, day);
        if (
            parsed.getFullYear() !== year
            || parsed.getMonth() !== month
            || parsed.getDate() !== day
        ) {
            return null;
        }
        return parsed;
    },

    renderMarkers() {
        if (MapView.clusterGroup) {
            MapView.clusterGroup.clearLayers();
        } else {
            MapView.markers.forEach(m => MapView.map.removeLayer(m));
        }
        MapView.markers = [];

        const filtered = MapView._filterCourts(MapView.courts);

        filtered.forEach(court => {
            const players = court.active_players || 0;
            const icon = MapView._courtIcon(court, players);
            const marker = L.marker([court.latitude, court.longitude], { icon });

            marker.bindPopup(MapView._popupContent(court, players));
            marker.on('click', () => marker.openPopup());
            marker.courtId = court.id;
            MapView.markers.push(marker);
        });

        if (MapView.clusterGroup) {
            MapView.clusterGroup.addLayers(MapView.markers);
        } else {
            MapView.markers.forEach(m => m.addTo(MapView.map));
        }
    },

    _courtIcon(court, players) {
        const color = court.indoor ? '#6366f1' : '#22c55e';
        const busy = players > 0 ? '#ef4444' : color;
        const badge = players > 0 ? `<span class="marker-badge">${players}</span>` : '';
        const pulseClass = players > 0 ? ' marker-pulse' : '';
        const verifiedBadge = court.verified ? '<span class="marker-verified">✓</span>' : '';
        const hasFriends = MapView._friendsAtCourt(court.id).length > 0;
        const friendRing = hasFriends ? ' marker-friend-ring' : '';
        const size = (court.num_courts || 1) >= 6 ? 36 : 32;
        const iconChar = court.indoor
            ? '<svg viewBox="0 0 24 24" fill="white" width="14" height="14"><path d="M3 21V7l9-4 9 4v14H3z"/></svg>'
            : '<svg viewBox="0 0 24 24" fill="white" width="14" height="14"><circle cx="12" cy="12" r="5"/><path d="M12 1v2m0 18v2m11-11h-2M3 12H1m16.4-6.4-1.4 1.4M7 7 5.6 5.6m12.8 12.8L17 17M7 17l-1.4 1.4"/></svg>';

        return L.divIcon({
            className: 'court-marker-icon',
            html: `<div class="court-marker${pulseClass}${friendRing}" style="background:${busy};width:${size}px;height:${size}px">${badge}${verifiedBadge}${iconChar}</div>`,
            iconSize: [size, size],
            iconAnchor: [size / 2, size],
            popupAnchor: [0, -size + 2],
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
        if (court.wheelchair_accessible) amenities.push('♿');

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
        const verifiedLabel = court.verified ? '<span class="popup-verified">✓ Verified</span>' : '';

        const photoHtml = court.photo_url
            ? `<div class="popup-photo"><img src="${MapView._escapeAttr(court.photo_url)}" alt="${courtName}" loading="lazy" onerror="this.parentElement.remove()"></div>`
            : '';

        const token = localStorage.getItem('token');
        const myStatus = MapView.myCheckinStatus || {};
        const amCheckedInHere = !!(myStatus.checked_in && Number(myStatus.court_id) === Number(court.id));
        const checkedInElsewhere = !!(myStatus.checked_in && !amCheckedInHere);
        const currentCourtName = checkedInElsewhere
            ? MapView._escapeHtml(MapView._checkedInCourtName(myStatus.court_id))
            : '';
        const checkinBtn = token
            ? (amCheckedInHere
                ? '<button class="btn-secondary btn-sm" disabled>Checked In Here</button>'
                : `<button class="btn-primary btn-sm" onclick="MapView.checkIn(${court.id})">${checkedInElsewhere ? 'Switch Check-In' : 'Check In'}</button>`)
            : '';
        const checkinNote = checkedInElsewhere
            ? `<div class="popup-checkin-note">You're checked in at ${currentCourtName || 'another court'}. Switching courts will end any active Looking to Play session there.</div>`
            : '';

        return `
        <div class="court-popup">
            ${photoHtml}
            <h3>${courtName} ${verifiedLabel} ${distStr}</h3>
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
            ${checkinNote}
            <div class="popup-amenities">${amenities.join(' ')}</div>
            <div class="popup-actions">
                <button class="btn-primary btn-sm" onclick="App.openCourtDetails(${court.id})">Details</button>
                ${checkinBtn}
                <a class="btn-secondary btn-sm" href="${MapView._formatDirectionsUrl(court.latitude, court.longitude)}" target="_blank" rel="noopener noreferrer">Directions</a>
            </div>
        </div>`;
    },

    _checkedInCourtName(courtId) {
        const targetCourtId = Number(courtId) || 0;
        if (!targetCourtId) return 'another court';
        if (typeof LocationService !== 'undefined'
            && LocationService.checkedInCourtSnapshot
            && Number(LocationService.checkedInCourtSnapshot.id) === targetCourtId
        ) {
            return LocationService.checkedInCourtSnapshot.name || 'another court';
        }
        const cachedCourt = (MapView.courts || []).find(c => Number(c.id) === targetCourtId);
        if (cachedCourt) return cachedCourt.name || 'another court';
        if (MapView.currentCourtData && Number(MapView.currentCourtData.id) === targetCourtId) {
            return MapView.currentCourtData.name || 'another court';
        }
        return 'another court';
    },

    _filterCourts(courts) {
        if (MapView.activeFilters.size === 0) return courts;
        return courts.filter(c => {
            for (const f of MapView.activeFilters) {
                if (f === 'indoor' && !c.indoor) return false;
                if (f === 'outdoor' && c.indoor) return false;
                if (f === 'lighted' && !c.lighted) return false;
                if (f === 'free' && !(c.fees || '').toLowerCase().includes('free')) return false;
                if (f === 'dedicated' && c.court_type !== 'dedicated') return false;
                if (f === 'active' && !(c.active_players > 0)) return false;
                if (f === 'restrooms' && !c.has_restrooms) return false;
                if (f === 'parking' && !c.has_parking) return false;
                if (f === 'accessible' && !c.wheelchair_accessible) return false;
            }
            return true;
        });
    },

    setFilter(filter) {
        if (filter === 'all') {
            MapView.activeFilters.clear();
        } else if (MapView.activeFilters.has(filter)) {
            MapView.activeFilters.delete(filter);
        } else {
            MapView.activeFilters.add(filter);
        }
        MapView._syncFilterChips();
        MapView.renderMarkers();
        MapView.renderCourtList();
        MapView._updateCourtCount();
    },

    _syncFilterChips() {
        const filtered = MapView._filterCourts(MapView.courts);
        const nearbyBtn = document.getElementById('nearby-filter');
        if (nearbyBtn) {
            nearbyBtn.classList.toggle('active', MapView.nearbyModeEnabled);
        }
        document.querySelectorAll('.filter-chip').forEach(btn => {
            const f = btn.dataset.filter;
            if (!f) return;
            if (f === 'all') {
                btn.classList.toggle('active', MapView.activeFilters.size === 0);
            } else {
                btn.classList.toggle('active', MapView.activeFilters.has(f));
            }
            const count = MapView._filterCountForChip(f);
            const badge = btn.querySelector('.filter-count');
            if (badge) badge.textContent = count;
            else if (f !== 'all') {
                const span = document.createElement('span');
                span.className = 'filter-count';
                span.textContent = count;
                btn.appendChild(span);
            }
        });
    },

    _filterCountForChip(filter) {
        const courts = MapView.courts;
        if (filter === 'all') return courts.length;
        if (filter === 'indoor') return courts.filter(c => c.indoor).length;
        if (filter === 'outdoor') return courts.filter(c => !c.indoor).length;
        if (filter === 'lighted') return courts.filter(c => c.lighted).length;
        if (filter === 'free') return courts.filter(c => (c.fees || '').toLowerCase().includes('free')).length;
        if (filter === 'dedicated') return courts.filter(c => c.court_type === 'dedicated').length;
        if (filter === 'active') return courts.filter(c => c.active_players > 0).length;
        if (filter === 'restrooms') return courts.filter(c => c.has_restrooms).length;
        if (filter === 'parking') return courts.filter(c => c.has_parking).length;
        if (filter === 'accessible') return courts.filter(c => c.wheelchair_accessible).length;
        return 0;
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

        MapView.map.flyTo([best.latitude, best.longitude], 14, { duration: 0.8 });
        App.openCourtDetails(best.id);

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
        const clearBtn = document.getElementById('search-clear-btn');
        if (clearBtn) clearBtn.style.display = query ? 'flex' : 'none';

        if (!query) {
            MapView.hideSuggestions();
            MapView.loadCourts();
            return;
        }
        MapView._showSuggestions(query);
        try {
            const url = (typeof App !== 'undefined' && typeof App.buildCourtsQuery === 'function')
                ? App.buildCourtsQuery(MapView._courtQueryParams({ search: query }))
                : `/api/courts?search=${encodeURIComponent(query)}`;
            const res = await API.get(url);
            MapView.courts = res.courts || [];
            MapView.renderMarkers();
            MapView.renderCourtList();
            MapView._updateCourtCount();
            if (MapView.courts.length > 0) {
                const bounds = L.latLngBounds(MapView.courts.map(c => [c.latitude, c.longitude]));
                MapView.map.flyToBounds(bounds, { padding: [50, 50], duration: 0.6 });
            }
        } catch {}
    },

    clearSearch() {
        const input = document.getElementById('mobile-search-input');
        if (input) input.value = '';
        const clearBtn = document.getElementById('search-clear-btn');
        if (clearBtn) clearBtn.style.display = 'none';
        MapView.hideSuggestions();
        MapView.loadCourts();
    },

    _showSuggestions(query) {
        const el = document.getElementById('search-suggestions');
        if (!el) return;
        const q = query.toLowerCase().trim();
        if (!q) { el.style.display = 'none'; return; }

        const matches = MapView.courts
            .filter(c =>
                (c.name || '').toLowerCase().includes(q) ||
                (c.city || '').toLowerCase().includes(q) ||
                (c.address || '').toLowerCase().includes(q)
            )
            .slice(0, 6);

        if (!matches.length) {
            el.innerHTML = '<div class="suggestion-empty">No matches</div>';
            el.style.display = 'block';
            return;
        }

        el.innerHTML = matches.map(c => {
            const name = MapView._highlightMatch(c.name || '', q);
            const city = MapView._escapeHtml(c.city || '');
            const players = c.active_players || 0;
            const liveTag = players > 0 ? `<span class="suggestion-live">${players} playing</span>` : '';
            return `<button type="button" class="suggestion-item" onclick="MapView._pickSuggestion(${c.id})">
                <div class="suggestion-name">${name} ${liveTag}</div>
                <div class="suggestion-meta">${city} · ${c.num_courts} court${c.num_courts > 1 ? 's' : ''}</div>
            </button>`;
        }).join('');
        el.style.display = 'block';
    },

    hideSuggestions() {
        const el = document.getElementById('search-suggestions');
        if (el) el.style.display = 'none';
    },

    _pickSuggestion(courtId) {
        MapView.hideSuggestions();
        const court = MapView.courts.find(c => c.id === courtId);
        if (court) {
            MapView.map.flyTo([court.latitude, court.longitude], 15, { duration: 0.6 });
            const marker = MapView.markers.find(m => m.courtId === courtId);
            if (marker) setTimeout(() => marker.openPopup(), 400);
        }
        App.openCourtDetails(courtId);
    },

    _highlightMatch(text, query) {
        const safe = MapView._escapeHtml(text);
        const idx = text.toLowerCase().indexOf(query);
        if (idx === -1) return safe;
        const before = MapView._escapeHtml(text.slice(0, idx));
        const match = MapView._escapeHtml(text.slice(idx, idx + query.length));
        const after = MapView._escapeHtml(text.slice(idx + query.length));
        return `${before}<strong>${match}</strong>${after}`;
    },

    async toggleNearbyMode() {
        if (MapView.nearbyModeEnabled) {
            MapView.nearbyModeEnabled = false;
            MapView._syncFilterChips();
            MapView.loadCourts({ fitToCourts: true });
            return;
        }
        const pos = typeof LocationService !== 'undefined' ? LocationService.lastPosition : null;
        if (pos?.lat && pos?.lng) {
            MapView.nearbyModeEnabled = true;
            MapView._syncFilterChips();
            MapView.loadCourts({ fitToCourts: true });
            return;
        }
        MapView.locateUser({ enableNearbyMode: true });
    },

    locateUser(options = {}) {
        if (!navigator.geolocation) {
            App.toast('Geolocation is not supported on this device.', 'error');
            return;
        }
        navigator.geolocation.getCurrentPosition(pos => {
            const { latitude, longitude } = pos.coords;
            if (typeof LocationService !== 'undefined') {
                LocationService.lastPosition = { lat: latitude, lng: longitude, accuracy: pos.coords.accuracy || null };
            }
            MapView.map.flyTo([latitude, longitude], 13, { duration: 0.8 });
            MapView._placeUserDot(latitude, longitude);
            MapView.loadCourts({ fitToCourts: false });
            if (options.enableNearbyMode) {
                MapView.nearbyModeEnabled = true;
                MapView._syncFilterChips();
                MapView.loadCourts({ fitToCourts: true });
            }
        }, () => {
            App.toast('Location permission was denied.', 'error');
        }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 60000 });
    },

    _placeUserDot(lat, lng) {
        if (MapView.userLocationMarker) {
            MapView.map.removeLayer(MapView.userLocationMarker);
        }
        MapView.userLocationMarker = L.marker([lat, lng], {
            icon: L.divIcon({
                className: 'user-location-icon',
                html: '<div class="user-dot"><div class="user-dot-ring"></div></div>',
                iconSize: [22, 22],
                iconAnchor: [11, 11],
            }),
            zIndexOffset: 1000,
        }).addTo(MapView.map).bindPopup('You are here');
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

    _cacheCurrentCourtBundle(court, sessions) {
        if (!court || !court.id) return;
        MapView.currentCourtData = court;
        MapView.currentCourtSessions = sessions || [];
    },

    /** Legacy: redirect to unified court details screen */
    async openCourtDetail(courtId) {
        App.openCourtDetails(courtId);
    },

    /** Legacy: redirect to unified court details screen */
    async openCourtFullPage(courtId) {
        App.openCourtDetails(courtId);
    },

    /** Open the unified court details screen */
    async openCourt(courtId) {
        MapView.currentCourtId = courtId;
        MapView._teardownCourtPageUi();

        const nameEl = document.getElementById('court-details-name');
        const addrEl = document.getElementById('court-details-address');
        const dirLink = document.getElementById('court-directions-link');
        if (nameEl) nameEl.textContent = 'Loading...';
        if (addrEl) addrEl.textContent = '';

        const pageContainer = document.getElementById('court-page-content');
        if (pageContainer) {
            pageContainer.scrollTop = 0;
            pageContainer.innerHTML = '<div class="loading" style="padding:40px;text-align:center">Loading court details...</div>';
        }

        await MapView.refreshMyStatus();

        try {
            const { court, sessions } = await MapView._fetchCourtBundle(courtId);
            MapView._cacheCurrentCourtBundle(court, sessions);

            if (nameEl) nameEl.textContent = court.name || 'Court';
            if (addrEl) addrEl.textContent = MapView._courtAddressLine(court);
            if (dirLink) dirLink.href = MapView._formatDirectionsUrl(court.latitude, court.longitude);

            if (pageContainer) {
                pageContainer.innerHTML = MapView._courtPageHTML(court, sessions);
                MapView._initCourtPageUi();
            }

            Ranked.loadCourtRanked(court.id);
            Ranked.loadMatchHistory(null, court.id);
            App._loadLeaderboardInline(court.id);
            MapView._loadCourtChat(court.id);

            if (typeof Chat !== 'undefined' && Chat.socket) {
                Chat.joinRoom(`court_${court.id}`);
            }
        } catch {
            if (nameEl) nameEl.textContent = 'Court unavailable';
            if (addrEl) addrEl.textContent = 'Try again in a moment.';
            if (pageContainer) {
                pageContainer.innerHTML = `
                    <div class="court-empty-card">
                        <strong>Unable to load this court right now</strong>
                        <p>There may be a temporary connection issue. Try again, or go back to the map and open another court.</p>
                        <div class="court-empty-actions">
                            <button type="button" class="btn-primary btn-sm" onclick="MapView.openCourt(${Number(courtId) || 0})">Try Again</button>
                            <button type="button" class="btn-secondary btn-sm" onclick="App.backToMain()">Back to Map</button>
                        </div>
                    </div>`;
            }
        }
    },

    async _refreshCourt(courtId) {
        if (App.currentScreen !== 'court-details') return;
        await MapView.refreshMyStatus();
        try {
            const { court, sessions } = await MapView._fetchCourtBundle(courtId);
            MapView._cacheCurrentCourtBundle(court, sessions);
            const pageContainer = document.getElementById('court-page-content');
            const nameEl = document.getElementById('court-details-name');
            const addrEl = document.getElementById('court-details-address');
            const dirLink = document.getElementById('court-directions-link');
            const previousScrollTop = pageContainer ? pageContainer.scrollTop : 0;
            if (nameEl) nameEl.textContent = court.name || 'Court';
            if (addrEl) addrEl.textContent = MapView._courtAddressLine(court);
            if (dirLink) dirLink.href = MapView._formatDirectionsUrl(court.latitude, court.longitude);
            if (pageContainer) {
                pageContainer.innerHTML = MapView._courtPageHTML(court, sessions);
                pageContainer.scrollTop = previousScrollTop;
                MapView._initCourtPageUi();
            }
            Ranked.loadCourtRanked(court.id);
            Ranked.loadMatchHistory(null, court.id, { silent: true });
            App._loadLeaderboardInline(court.id);
            MapView._loadCourtChat(court.id);
        } catch {}
    },

    async refreshCurrentCourtLiveData(courtId) {
        const targetCourtId = Number(courtId || MapView.currentCourtId);
        if (!targetCourtId) return;
        if (App.currentScreen !== 'court-details' || MapView.currentCourtId !== targetCourtId) return;

        const sessionsEl = document.getElementById('court-sessions-live');
        const playersEl = document.getElementById('court-players-live');
        const countEl = document.getElementById('court-player-count');
        const statusEl = document.getElementById('court-live-status');
        const bannerEl = document.getElementById('court-match-banner');

        if (!sessionsEl && !playersEl && !countEl && !statusEl && !bannerEl) return;

        try {
            await MapView.refreshMyStatus();
            const { court, sessions } = await MapView._fetchCourtBundle(targetCourtId);
            MapView._cacheCurrentCourtBundle(court, sessions);
            const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
            const myStatus = MapView.myCheckinStatus || {};
            const amCheckedInHere = myStatus.checked_in
                && Number(myStatus.court_id) === targetCourtId;
            const liveSections = MapView._buildLiveCourtSections(
                court,
                sessions,
                currentUser.id,
                amCheckedInHere,
                { playerCardOptions: { enableChallenge: true } },
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

    /** Load chat messages into the court details chat section */
    async _loadCourtChat(courtId) {
        const container = document.getElementById('court-chat-messages');
        if (!container) return;
        const token = localStorage.getItem('token');
        if (!token) {
            container.innerHTML = `
                <div class="court-empty-card court-empty-card-compact">
                    <strong>Join the court chat</strong>
                    <p>Sign in to coordinate arrivals, open play, and quick court updates.</p>
                </div>`;
            return;
        }
        try {
            const res = await API.get(`/api/chat/court/${courtId}`);
            const msgs = res.messages || [];
            if (!msgs.length) {
                container.innerHTML = `
                    <div class="court-empty-card court-empty-card-compact">
                        <strong>No messages yet</strong>
                        <p>Say hello and let other players know when you'll be there.</p>
                    </div>`;
                return;
            }
            container.innerHTML = msgs.map(m => MapView._renderChatMsg(m)).join('');
            container.scrollTop = container.scrollHeight;
        } catch {
            container.innerHTML = `
                <div class="court-empty-card court-empty-card-compact">
                    <strong>Unable to load chat</strong>
                    <p>Try again in a moment to see recent messages.</p>
                </div>`;
        }
    },

    /** Legacy aliases */
    async _loadFullPageChat(courtId) { MapView._loadCourtChat(courtId); },
    async _refreshFullPage(courtId) { MapView._refreshCourt(courtId); },

    _renderChatMsg(msg) {
        const sender = msg.sender || {};
        const time = new Date(msg.created_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const isMe = sender.id === currentUser.id;
        const senderName = MapView._escapeHtml(isMe ? 'You' : (sender.name || sender.username));
        const content = MapView._escapeHtml(msg.content || '');
        return `
        <div class="chat-msg ${isMe ? 'chat-msg-me' : ''}" data-msg-id="${msg.id}">
            <div class="chat-msg-header">
                <strong>${senderName}</strong>
                <span class="chat-msg-time">${time}</span>
            </div>
            <div class="chat-msg-body">${content}</div>
        </div>`;
    },

    /** Send chat from unified court details screen */
    async sendCourtChat(e) {
        e.preventDefault();
        const input = document.getElementById('court-chat-input');
        const content = input.value.trim();
        if (!content) return;
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        const courtId = MapView.currentCourtId;
        if (!courtId) return;
        try {
            await API.post('/api/chat/send', { content, court_id: courtId, msg_type: 'court' });
            input.value = '';
            MapView._loadCourtChat(courtId);
        } catch { App.toast('Failed to send message', 'error'); }
    },

    /** Legacy alias */
    async sendFullPageChat(e, courtId) {
        e.preventDefault();
        const input = document.getElementById('court-chat-input');
        if (input) {
            const content = input.value.trim();
            if (!content) return;
            const token = localStorage.getItem('token');
            if (!token) { Auth.showModal(); return; }
            try {
                await API.post('/api/chat/send', { content, court_id: courtId, msg_type: 'court' });
                input.value = '';
                MapView._loadCourtChat(courtId);
            } catch { App.toast('Failed to send message', 'error'); }
        }
    },

    _courtInfoHTML(court, sessions) {
        return MapView._courtPageHTML(court, sessions);
    },

    _courtPageHTML(court, sessions) {
        sessions = sessions || [];
        const checkedIn = court.checked_in_users || [];
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const hasToken = !!localStorage.getItem('token');
        const myStatus = MapView.myCheckinStatus || {};
        const amCheckedInHere = myStatus.checked_in
            && Number(myStatus.court_id) === Number(court.id);
        const safeCourtName = MapView._escapeHtml(court.name || '');
        const safeAddressLine = MapView._escapeHtml(MapView._courtAddressLine(court));
        const safeDescription = MapView._escapeHtml(court.description || '');
        const safeSurfaceType = MapView._escapeHtml(court.surface_type || 'Unknown');
        const safeFees = MapView._escapeHtml(court.fees || 'Unknown');
        const safeSkillLevels = MapView._escapeHtml((court.skill_levels || 'all').replace(/,/g, ', '));
        const safeHours = MapView._escapeHtml(court.hours || '');
        const safeOpenPlaySchedule = MapView._escapeHtml(court.open_play_schedule || '');
        const safePhoneLabel = MapView._escapeHtml(court.phone || '');
        const safePhoneHref = MapView._safeTel(court.phone || '');
        const safeWebsiteHref = MapView._safeHttpUrl(court.website || '');
        const safeDirectionsHref = MapView._escapeAttr(MapView._formatDirectionsUrl(court.latitude, court.longitude));
        const safePhotoUrl = MapView._escapeAttr(court.photo_url || '');
        const safePhotoAlt = MapView._escapeAttr(court.name || 'Court');
        const pendingUpdatesCount = Number(court.pending_updates_count) || 0;
        const friendCount = MapView._friendsAtCourt(court.id).length;
        const nowMs = Date.now();

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
        const safeTypeLabel = MapView._escapeHtml(typeLabel);
        const scheduledSessions = sessions
            .filter(session => session && session.session_type === 'scheduled')
            .slice()
            .sort((a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime());
        const futureScheduledSessions = scheduledSessions.filter(session => {
            const startAt = new Date(session.start_time);
            return !Number.isNaN(startAt.getTime()) && startAt.getTime() >= nowMs - 3600000;
        });
        const nextScheduledSession = futureScheduledSessions[0] || null;

        const liveSections = MapView._buildLiveCourtSections(
            court, sessions, currentUser.id, amCheckedInHere,
            { playerCardOptions: { enableChallenge: true } },
        );
        const nowSessions = liveSections.nowSessions;
        const players = liveSections.activePlayers;

        const checkinBtnHTML = MapView._checkinBarHTML({
            courtId: court.id, safeCourtName, amCheckedInHere,
            currentUserId: currentUser.id, nowSessions,
        });

        const heroSummaryParts = [];
        if (nextScheduledSession) {
            heroSummaryParts.push(`Next game ${MapView._escapeHtml(MapView._formatCourtDateTime(nextScheduledSession.start_time))}`);
        } else {
            heroSummaryParts.push('No scheduled games in the next 7 days');
        }
        if (friendCount > 0) {
            heroSummaryParts.push(`${friendCount} friend${friendCount === 1 ? '' : 's'} at this court`);
        } else if (pendingUpdatesCount > 0) {
            heroSummaryParts.push(`${pendingUpdatesCount} community update${pendingUpdatesCount === 1 ? '' : 's'} pending review`);
        }
        const heroSummaryText = heroSummaryParts.join(' · ');

        const heroChips = [
            `<span class="court-hero-chip ${players > 0 ? 'success' : ''}">${players > 0 ? `${players} here now` : 'Quiet right now'}</span>`,
            `<span class="court-hero-chip">${court.indoor ? 'Indoor' : 'Outdoor'}</span>`,
            `<span class="court-hero-chip">${safeTypeLabel}</span>`,
            safeFees && safeFees !== 'Unknown' ? `<span class="court-hero-chip">${safeFees}</span>` : '',
        ].filter(Boolean).join('');

        const heroMediaChips = [
            court.verified ? '<span class="court-hero-chip success">Verified</span>' : '',
            pendingUpdatesCount > 0
                ? `<span class="court-hero-chip warn">${pendingUpdatesCount} pending update${pendingUpdatesCount === 1 ? '' : 's'}</span>`
                : '',
        ].filter(Boolean).join('');

        const heroHtml = `
            <div class="court-page-hero">
                <div class="court-hero-media ${safePhotoUrl ? '' : 'no-photo'}">
                    ${safePhotoUrl
                        ? `<img src="${safePhotoUrl}" alt="${safePhotoAlt}" loading="lazy" onerror="this.remove()">`
                        : `<div class="court-hero-placeholder"><strong>${safeCourtName}</strong><span>Community photos help players confirm they're at the right court.</span></div>`}
                    ${heroMediaChips ? `<div class="court-hero-media-top">${heroMediaChips}</div>` : ''}
                </div>
                <div class="court-hero-panel">
                    ${heroChips ? `<div class="court-hero-chip-row">${heroChips}</div>` : ''}
                    ${safeAddressLine ? `<p class="court-hero-address">${safeAddressLine}</p>` : ''}
                    <p class="court-hero-summary">${heroSummaryText}</p>
                    <div class="court-hero-stats">
                        <div class="court-hero-stat">
                            <strong>${court.num_courts || 0}</strong>
                            <span>Courts</span>
                        </div>
                        <div class="court-hero-stat">
                            <strong>${checkedIn.length}</strong>
                            <span>Checked In</span>
                        </div>
                        <div class="court-hero-stat">
                            <strong>${futureScheduledSessions.length}</strong>
                            <span>Upcoming</span>
                        </div>
                        <div class="court-hero-stat">
                            <strong>${Number(court.recent_visitors) || 0}</strong>
                            <span>Visited Today</span>
                        </div>
                    </div>
                    <div class="court-hero-actions">
                        <a href="${safeDirectionsHref}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-sm">Directions</a>
                        <button type="button" class="btn-secondary btn-sm" onclick="Sessions.showCreateModal(${court.id})">Schedule</button>
                        <button type="button" class="btn-secondary btn-sm" onclick="MapView.shareCurrentCourt()">Share</button>
                    </div>
                </div>
            </div>`;

        const checkedInSection = `
            <div class="court-page-section" id="court-players-section">
                <div class="section-header">
                    <div class="section-header-copy">
                        <h4>Who's Here</h4>
                        <p class="court-section-subtitle">${checkedIn.length
                            ? 'Players who are currently checked in at this court.'
                            : 'No one is checked in yet. Be the first to get the run started.'}</p>
                    </div>
                    <span id="court-player-count" class="player-count-badge">${checkedIn.length}</span>
                </div>
                <div id="court-players-live">${liveSections.playersHTML}</div>
            </div>`;

        const scheduleDaysHTML = MapView._courtScheduleDaysHTML(court.id);
        const communityInfoHTML = MapView._communityInfoHTML(court.community_info || {});
        const communityImagesHTML = MapView._imageGalleryHTML(court.images || []);
        const communityEventsHTML = MapView._eventCardsHTML(court.upcoming_events || []);
        const busynessHTML = MapView._busynessChart(court.busyness || {});
        const communityIsEmpty = !Object.keys(court.busyness || {}).length
            && !Object.keys(court.community_info || {}).length
            && !(court.images || []).length
            && !(court.upcoming_events || []).length;
        const communityPromptHtml = (communityIsEmpty || pendingUpdatesCount > 0) ? `
            <div class="court-community-card court-community-card-wide court-community-prompt">
                <h5>${communityIsEmpty ? 'Help the next player' : 'Community updates in flight'}</h5>
                <p class="muted">${communityIsEmpty
                    ? 'This court page still needs local notes, photos, and crowd patterns. Suggest an update after your next visit.'
                    : `${pendingUpdatesCount} community update${pendingUpdatesCount === 1 ? '' : 's'} ${pendingUpdatesCount === 1 ? 'is' : 'are'} currently pending review.`}</p>
                <div class="court-hero-actions">
                    <button type="button" class="btn-primary btn-sm" onclick="App.toggleUpdatesSheet()">Suggest Court Update</button>
                    <button type="button" class="btn-secondary btn-sm" onclick="MapView.shareCurrentCourt()">Share Court</button>
                </div>
            </div>` : '';
        const schedulePreviewHtml = futureScheduledSessions.length
            ? `<div class="court-section-stack">${Sessions.renderMiniCards(futureScheduledSessions.slice(0, 3))}</div>`
            : `<div class="court-empty-card">
                    <strong>No open sessions scheduled yet</strong>
                    <p>Post a game and this court will show upcoming runs for the week.</p>
                    <div class="court-empty-actions">
                        <button type="button" class="btn-primary btn-sm" onclick="Sessions.showCreateModal(${court.id})">Schedule Game</button>
                        <button type="button" class="btn-secondary btn-sm" onclick="Ranked.openCourtScheduledChallenge(${court.id})">Ranked Challenge</button>
                    </div>
                </div>`;
        const stickyNav = `
            <div class="court-sticky-nav">
                <button type="button" class="court-sticky-nav-btn active" data-target="court-live-inline" aria-current="true" onclick="MapView.scrollToCourtSection('court-live-inline', this)">Live Now</button>
                <button type="button" class="court-sticky-nav-btn" data-target="court-schedule-inline" onclick="MapView.scrollToCourtSection('court-schedule-inline', this)">Schedule</button>
                <button type="button" class="court-sticky-nav-btn" data-target="court-ranked-inline" onclick="MapView.scrollToCourtSection('court-ranked-inline', this)">Competitive</button>
                <button type="button" class="court-sticky-nav-btn" data-target="court-info-inline" onclick="MapView.scrollToCourtSection('court-info-inline', this)">About</button>
                <button type="button" class="court-sticky-nav-btn" data-target="court-community-inline" onclick="MapView.scrollToCourtSection('court-community-inline', this)">Community</button>
                <button type="button" class="court-sticky-nav-btn" data-target="court-chat-inline" onclick="MapView.scrollToCourtSection('court-chat-inline', this)">Chat</button>
            </div>
        `;

        const sections = [
            stickyNav,
            heroHtml,
            `<div class="court-page-section" id="court-live-inline">
                <div class="section-header">
                    <div class="section-header-copy">
                        <h4>Live Now</h4>
                        <p class="court-section-subtitle">${players > 0
                            ? `${players} player${players === 1 ? '' : 's'} currently at the court.`
                            : 'Check in or post a session so other players know this court is active.'}</p>
                    </div>
                    <span class="player-count-badge">${players}</span>
                </div>
                ${checkinBtnHTML}
                <div id="court-live-status" class="court-live-status ${players > 0 ? 'active' : ''}">
                    ${MapView._liveStatusInnerHTML(court, players)}
                </div>
                <div id="court-match-banner">${liveSections.matchBannerHTML}</div>
                <div class="court-live-summary">
                    <div class="court-live-summary-item"><strong>${players}</strong><span>Here now</span></div>
                    <div class="court-live-summary-item"><strong>${nowSessions.length}</strong><span>Looking to play</span></div>
                    <div class="court-live-summary-item"><strong>${futureScheduledSessions.length}</strong><span>Upcoming</span></div>
                    <div class="court-live-summary-item"><strong>${Number(court.recent_visitors) || 0}</strong><span>Visited today</span></div>
                </div>
                <div class="court-sub-section">
                    <div class="section-header section-header-tight">
                        <div class="section-header-copy">
                            <h5>Open-to-Play Sessions</h5>
                            <p class="court-section-subtitle">Current looking-to-play posts and scheduled runs at this court.</p>
                        </div>
                    </div>
                    <div id="court-sessions-live" class="court-section-stack">${liveSections.sessionsHTML}</div>
                </div>
            </div>`,
            checkedInSection,
            `<div class="court-page-section" id="court-schedule-inline">
                <div class="section-header">
                    <div class="section-header-copy">
                        <h4>Schedule</h4>
                        <p class="court-section-subtitle">${nextScheduledSession
                            ? `Next game ${MapView._escapeHtml(MapView._formatCourtDateTime(nextScheduledSession.start_time))}.`
                            : 'No games are scheduled in the next 7 days yet.'}</p>
                    </div>
                    <button class="btn-secondary btn-sm" onclick="Sessions.showCreateModal(${court.id})">Schedule Game</button>
                </div>
                ${scheduleDaysHTML}
                ${court.open_play_schedule ? `<div class="court-inline-note"><strong>Open play notes:</strong> ${safeOpenPlaySchedule}</div>` : ''}
                ${schedulePreviewHtml}
                <div id="court-session-detail-view"></div>
            </div>`,
            `<div class="court-page-section" id="court-ranked-inline">
                <div class="section-header">
                    <div class="section-header-copy">
                        <h4>Competitive Play</h4>
                        <p class="court-section-subtitle">Queue up, challenge players, and see the ranked scene at this court.</p>
                    </div>
                </div>
                <div id="court-ranked-section"><div class="loading">Loading competitive play...</div></div>
            </div>`,
            `<div class="court-page-section" id="court-recent-games-inline">
                <div class="section-header">
                    <div class="section-header-copy">
                        <h4>Recent Games</h4>
                        <p class="court-section-subtitle">Court-level ranked results and recent momentum.</p>
                    </div>
                </div>
                <div id="match-history-content"><div class="loading">Loading recent games...</div></div>
            </div>`,
            `<div class="court-page-section" id="court-leaderboard-inline">
                <div class="section-header">
                    <div class="section-header-copy">
                        <h4>Leaderboard</h4>
                        <p class="court-section-subtitle">See who has the strongest ranked track record at this court.</p>
                    </div>
                    <button class="btn-secondary btn-sm" onclick="App.openLeaderboardPopup(${court.id})">Full Leaderboard</button>
                </div>
                <div id="leaderboard-content" class="compact-leaderboard-list"><div class="loading">Loading...</div></div>
            </div>`,
            `<div class="court-page-section" id="court-info-inline">
                <div class="section-header">
                    <div class="section-header-copy">
                        <h4>About the Court</h4>
                        <p class="court-section-subtitle">Hours, amenities, access notes, and contact details before you head out.</p>
                    </div>
                </div>
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
                ${court.open_play_schedule ? `<div class="court-sub-section"><strong>Open Play Schedule</strong><p class="schedule-text">${safeOpenPlaySchedule}</p></div>` : ''}
                <div class="court-sub-section">
                    <strong>Amenities</strong>
                    <div class="amenities-grid">${amenities.join('') || '<span class="muted">No amenities listed</span>'}</div>
                </div>
                <div class="court-contact">
                    <button type="button" class="btn-secondary btn-sm" onclick="MapView.copyCurrentCourtAddress()">📍 Copy Address</button>
                    <a href="${safeDirectionsHref}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-sm">🧭 Directions</a>
                    ${court.phone ? `<a href="tel:${safePhoneHref}" class="btn-secondary btn-sm">📞 ${safePhoneLabel}</a>` : ''}
                    ${safeWebsiteHref ? `<a href="${safeWebsiteHref}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-sm">🌐 Website</a>` : ''}
                </div>
                ${pendingUpdatesCount > 0
                    ? `<div class="court-inline-note"><strong>Heads up:</strong> ${pendingUpdatesCount} community update${pendingUpdatesCount === 1 ? '' : 's'} ${pendingUpdatesCount === 1 ? 'is' : 'are'} waiting to be reviewed for this court.</div>`
                    : ''}
            </div>`,
            `<div class="court-page-section" id="court-community-inline">
                <div class="section-header">
                    <div class="section-header-copy">
                        <h4>Community</h4>
                        <p class="court-section-subtitle">Photos, notes, events, and crowd patterns shared by local players.</p>
                    </div>
                </div>
                <div class="court-community-grid">
                    <div class="court-community-card">
                        <h5>Best Times</h5>
                        ${busynessHTML}
                    </div>
                    <div class="court-community-card">
                        <h5>Community Notes</h5>
                        ${communityInfoHTML}
                    </div>
                    <div class="court-community-card court-community-card-wide">
                        <h5>Upcoming Events</h5>
                        ${communityEventsHTML}
                    </div>
                    <div class="court-community-card court-community-card-wide">
                        <h5>Community Photos</h5>
                        ${communityImagesHTML}
                    </div>
                    ${communityPromptHtml}
                </div>
            </div>`,
            `<div class="court-page-section court-chat-section" id="court-chat-inline">
                <div class="section-header">
                    <div class="section-header-copy">
                        <h4>Court Chat</h4>
                        <p class="court-section-subtitle">Coordinate arrivals, open play, and quick court updates with players here.</p>
                    </div>
                </div>
                <div id="court-chat-messages" class="court-chat-messages"></div>
                ${hasToken
                    ? `<form class="court-chat-form" onsubmit="MapView.sendCourtChat(event)">
                            <input type="text" id="court-chat-input" placeholder="Message players here..." autocomplete="off">
                            <button type="submit" class="btn-primary btn-sm">Send</button>
                        </form>`
                    : `<div class="court-chat-guest">
                            <p>Sign in to see court chat and coordinate games with other players.</p>
                            <div class="court-empty-actions">
                                <button type="button" class="btn-primary btn-sm" onclick="Auth.showModal()">Sign In to Join Chat</button>
                                <button type="button" class="btn-secondary btn-sm" onclick="MapView.shareCurrentCourt()">Share Court</button>
                            </div>
                        </div>`}
            </div>`,
            `<div class="court-page-section court-help-section">
                <div class="court-help-card">
                    <div class="section-header-copy">
                        <h4>Keep This Court Accurate</h4>
                        <p class="court-section-subtitle">${pendingUpdatesCount > 0
                            ? `${pendingUpdatesCount} community update${pendingUpdatesCount === 1 ? '' : 's'} ${pendingUpdatesCount === 1 ? 'is' : 'are'} already pending review. Add another only if something else changed.`
                            : 'Report closures, add new photos, and suggest better hours or amenities after your next visit.'}</p>
                    </div>
                    <div class="court-bottom-actions">
                        <button class="btn-secondary court-bottom-action-btn court-report-btn" onclick="MapView.reportCurrentCourt()">Report a Problem</button>
                        <button class="btn-primary court-bottom-action-btn" onclick="App.toggleUpdatesSheet()">Suggest a Court Update</button>
                    </div>
                </div>
            </div>`,
        ];

        return sections.filter(Boolean).join('');
    },

    _courtScheduleDaysHTML(courtId) {
        const today = MapView._startOfDay(new Date());
        const days = [];
        for (let i = 0; i < 7; i++) {
            const day = new Date(today);
            day.setDate(today.getDate() + i);
            const key = MapView._dateKey(day);
            const items = MapView._getItemsForDay(key, courtId);
            days.push({ key, date: day, count: items.length });
        }
        const todayKey = MapView._dateKey(new Date());
        return `<div class="court-schedule-days">${days.map(day => {
            const dayName = day.date.toLocaleDateString('en-US', { weekday: 'short' });
            const dateLabel = day.date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            const classes = ['court-schedule-day'];
            if (day.count > 0) classes.push('has-games');
            if (day.key === todayKey) classes.push('today');
            return `<button type="button" class="${classes.join(' ')}"
                onclick="MapView.openDayPopup('${day.key}', ${courtId})"
                aria-label="${dayName} ${dateLabel}, ${day.count} events">
                <span class="court-schedule-day-name">${dayName}</span>
                <span class="court-schedule-day-date">${dateLabel}</span>
                <span class="court-schedule-day-count">${day.count > 0 ? day.count + ' game' + (day.count > 1 ? 's' : '') : '-'}</span>
            </button>`;
        }).join('')}</div>`;
    },

    _playerCard(user, isLookingToPlay, currentUserId, courtId, amCheckedInHere, options = {}) {
        const isMe = user.id === currentUserId;
        const isFriend = App.friendIds.includes(user.id);
        const enableChallenge = options.enableChallenge !== false;
        const initials = (user.name || user.username || '?').split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2);
        const elo = user.elo_rating ? Math.round(user.elo_rating) : 1200;
        const record = `${user.wins || 0}W-${user.losses || 0}L`;
        const timeAgo = user.checked_in_at ? MapView._timeAgo(user.checked_in_at) : '';
        const safeInitials = MapView._escapeHtml(initials);
        const safeName = MapView._escapeHtml(user.name || user.username);
        const safeRecord = MapView._escapeHtml(record);
        const safeTimeAgo = MapView._escapeHtml(timeAgo);

        let socialActionBtn = '';
        if (!isMe && currentUserId) {
            if (isFriend) {
                socialActionBtn = `<button class="btn-add-friend" onclick="event.stopPropagation(); Chat.openDirectByUser(${user.id})" title="Message this player">💬 Message</button>`;
            } else {
                socialActionBtn = `<button class="btn-add-friend" onclick="event.stopPropagation(); MapView.sendFriendRequest(${user.id})" title="Add Friend">➕ Add</button>`;
            }
        }
        const challengeBtn = (enableChallenge && !isMe && currentUserId && amCheckedInHere)
            ? `<button class="btn-add-friend" onclick="event.stopPropagation(); Ranked.challengeCheckedInPlayer(${courtId}, ${user.id})" title="Challenge this player">⚔️ Challenge</button>`
            : '';
        const profileBtn = (!isMe && currentUserId)
            ? `<button class="btn-add-friend" onclick="event.stopPropagation(); Ranked.viewPlayer(${user.id})" title="View player profile">👤 Profile</button>`
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
            ${profileBtn}
            ${socialActionBtn}
        </div>`;
    },

    _splitPlayersByLookingToPlay(checkedInUsers, nowSessions) {
        const nowCreatorIds = new Set((nowSessions || []).map(session => session.creator_id));
        return {
            lookingToPlayPlayers: (checkedInUsers || []).filter(user => nowCreatorIds.has(user.id)),
            otherPlayers: (checkedInUsers || []).filter(user => !nowCreatorIds.has(user.id)),
        };
    },

    _buildLiveCourtSections(court, sessions, currentUserId, amCheckedInHere, options = {}) {
        const allSessions = sessions || [];
        const checkedIn = court.checked_in_users || [];
        const playerCardOptions = options.playerCardOptions || {};
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
                    u, true, currentUserId, court.id, amCheckedInHere, playerCardOptions
                )).join('');
                playersHTML += '</div>';
            }
            if (otherPlayers.length > 0) {
                playersHTML += `<div class="players-group"><h5>🏓 At the Court (${otherPlayers.length})</h5>`;
                playersHTML += otherPlayers.map(u => MapView._playerCard(
                    u, false, currentUserId, court.id, amCheckedInHere, playerCardOptions
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

    _checkinBarHTML({ courtId, safeCourtName, amCheckedInHere, currentUserId, nowSessions }) {
        if (!amCheckedInHere) {
            return `
                <div class="checkin-status-bar">
                    <button class="btn-primary btn-full" onclick="MapView.checkIn(${courtId})">Check In at ${safeCourtName}</button>
                    <p class="checkin-hint">Check in first, then choose how many hours you're looking to play.</p>
                </div>`;
        }

        const myNowSession = MapView._nowSessionByCreator(nowSessions, currentUserId);
        const activeDuration = MapView._nowSessionDurationMinutes(myNowSession);
        const durationButtons = MapView._quickPlayDurations()
            .map(minutes => `
                <button
                    class="session-quick-btn ${activeDuration === minutes ? 'active' : ''}"
                    onclick="MapView.startLookingToPlayNow(${courtId}, ${minutes})"
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
                    <button class="btn-sm btn-secondary" onclick="MapView.checkOut(${courtId})">Check Out</button>
                </div>
                <div class="checkin-play-controls">
                    <div class="checkin-play-header">
                        <span>Looking to Play</span>
                        <button class="btn-secondary btn-sm" onclick="Sessions.showCreateModal(${courtId})">Schedule Game</button>
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
            if (App.currentScreen === 'court-details' && MapView.currentCourtId) {
                MapView._refreshCourt(MapView.currentCourtId);
            }
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
                        <img src="${MapView._escapeAttr(img.image_url || '')}" alt="Court image" onerror="this.closest('.court-image-card').remove()">
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

    _courtAddressLine(court) {
        const details = court || {};
        const state = String(details.state || 'CA').trim();
        const localityParts = [];
        if (details.city) localityParts.push(String(details.city).trim());
        if (state) localityParts.push(state);
        let locality = localityParts.join(', ');
        if (details.zip_code) {
            locality = locality ? `${locality} ${String(details.zip_code).trim()}` : String(details.zip_code).trim();
        }
        return [details.address, locality].filter(Boolean).join(', ');
    },

    _formatCourtDateTime(value) {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        const dateLabel = date.toLocaleDateString('en-US', {
            weekday: 'short',
            month: 'short',
            day: 'numeric',
        });
        const timeLabel = date.toLocaleTimeString('en-US', {
            hour: 'numeric',
            minute: '2-digit',
        });
        return `${dateLabel} at ${timeLabel}`;
    },

    async _copyText(value) {
        const text = String(value || '').trim();
        if (!text) return false;
        try {
            if (navigator.clipboard?.writeText) {
                await navigator.clipboard.writeText(text);
                return true;
            }
        } catch {}

        const input = document.createElement('textarea');
        input.value = text;
        input.setAttribute('readonly', 'true');
        input.style.position = 'absolute';
        input.style.left = '-9999px';
        document.body.appendChild(input);
        input.select();
        let copied = false;
        try {
            copied = document.execCommand('copy');
        } catch {}
        document.body.removeChild(input);
        return copied;
    },

    async copyCurrentCourtAddress() {
        const court = MapView.currentCourtData;
        if (!court) {
            App.toast('Open a court first', 'error');
            return;
        }
        const text = [court.name || 'Court', MapView._courtAddressLine(court)].filter(Boolean).join('\n');
        const copied = await MapView._copyText(text);
        App.toast(copied ? 'Court address copied.' : 'Unable to copy address.', copied ? undefined : 'error');
    },

    async shareCurrentCourt() {
        const court = MapView.currentCourtData;
        if (!court) {
            App.toast('Open a court first', 'error');
            return;
        }

        const title = court.name || 'Court';
        const addressLine = MapView._courtAddressLine(court);
        const directionsUrl = MapView._formatDirectionsUrl(court.latitude, court.longitude);
        const shareText = [title, addressLine].filter(Boolean).join('\n');

        if (navigator.share) {
            try {
                await navigator.share({
                    title,
                    text: shareText,
                    url: directionsUrl,
                });
                return;
            } catch (err) {
                if (err?.name === 'AbortError') return;
            }
        }

        const copied = await MapView._copyText(`${shareText}\n${directionsUrl}`);
        App.toast(copied ? 'Court details copied to clipboard.' : 'Unable to share court.', copied ? undefined : 'error');
    },

    async checkIn(courtId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        await MapView.refreshMyStatus();
        const myStatus = MapView.myCheckinStatus || {};
        if (myStatus.checked_in && Number(myStatus.court_id) === Number(courtId)) {
            App.toast("You're already checked in here.");
            return;
        }
        if (myStatus.checked_in && Number(myStatus.court_id) !== Number(courtId)) {
            const currentCourtName = MapView._checkedInCourtName(myStatus.court_id);
            const nextCourtName = MapView._checkedInCourtName(courtId);
            const confirmed = confirm(
                `You're currently checked in at ${currentCourtName}. Switch your check-in to ${nextCourtName}? This will end any active Looking to Play session at your current court.`
            );
            if (!confirmed) return;
        }
        try {
            await API.post('/api/presence/checkin', { court_id: courtId });
            if (typeof LocationService !== 'undefined' && typeof LocationService.setCheckedInCourt === 'function') {
                LocationService.setCheckedInCourt(courtId);
            }
            App.toast("Checked in! Others can now see you're here.");
            MapView._refreshCourt(courtId);
            MapView.loadCourts();
        } catch {
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
            MapView._refreshCourt(courtId);
            MapView.loadCourts();
        } catch {
            App.toast('Failed to check out', 'error');
        }
    },

    /** Legacy aliases for full-page check-in/check-out */
    async fullPageCheckIn(courtId) { MapView.checkIn(courtId); },
    async fullPageCheckOut(courtId) { MapView.checkOut(courtId); },

    async startLookingToPlayNow(courtId, durationMinutes = 120) {
        await MapView._createLookingToPlaySession(courtId, durationMinutes);
    },

    /** Legacy alias */
    async fullPageStartLookingToPlayNow(courtId, durationMinutes = 120) {
        await MapView._createLookingToPlaySession(courtId, durationMinutes);
    },

    async _createLookingToPlaySession(courtId, durationMinutes) {
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
            MapView._refreshCourt(courtId);
            MapView.loadCourts();
        } catch (err) {
            App.toast(err.message || 'Failed to create looking-to-play session', 'error');
        }
    },

    reportCurrentCourt() {
        if (!MapView.currentCourtId) {
            App.toast('Open a court first', 'error');
            return;
        }
        MapView.reportCourt(MapView.currentCourtId);
    },

    async reportCourt(courtId) {
        const numericCourtId = Number(courtId);
        if (!numericCourtId) {
            App.toast('Open a court first', 'error');
            return;
        }
        const reason = prompt('What\'s the issue? (wrong info, closed, fake, other)');
        if (!reason) return;
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        try {
            await API.post(`/api/courts/${numericCourtId}/report`, { reason, description: reason });
            App.toast('Report submitted. Thank you!');
        } catch { App.toast('Failed to submit report', 'error'); }
    },

    /** Legacy: _courtDetailHTML redirects to _courtInfoHTML */
    _courtDetailHTML(court, sessions) { return MapView._courtInfoHTML(court, sessions); },

    /** Legacy alias: close full page = back to main */
    closeFullPage() { App.backToMain(); },

    renderCourtList() {
        const el = document.getElementById('court-list-items');
        if (!el) return;
        const filtered = MapView._sortCourts(MapView._filterCourts(MapView.courts));
        el.innerHTML = filtered.map(c => {
            const amenityIcons = [];
            if (c.has_restrooms) amenityIcons.push('🚻');
            if (c.has_parking) amenityIcons.push('🅿️');
            if (c.lighted) amenityIcons.push('💡');
            if (c.has_water) amenityIcons.push('💧');
            const amenityStr = amenityIcons.length ? `<span class="court-list-amenities">${amenityIcons.join('')}</span>` : '';
            const friendCount = MapView._friendsAtCourt(c.id).length;
            return `
            <div class="court-list-card" onclick="App.openCourtDetails(${c.id}); MapView.map.flyTo([${c.latitude},${c.longitude}], 14, {duration:0.6});"
                 onmouseenter="MapView._highlightMarker(${c.id})" onmouseleave="MapView._unhighlightMarker(${c.id})">
                <div class="court-list-top">
                    <div class="court-list-name">${MapView._escapeHtml(c.name)}</div>
                    ${c.active_players > 0 ? `<span class="live-badge">${c.active_players} playing</span>` : ''}
                </div>
                <div class="court-list-meta">
                    <span>${c.indoor ? '🏢' : '☀️'} ${MapView._escapeHtml(c.city)}</span>
                    <span>${c.num_courts} court${c.num_courts > 1 ? 's' : ''}</span>
                    ${c.distance !== undefined ? `<span>${c.distance} mi</span>` : ''}
                    ${friendCount > 0 ? `<span class="friend-badge">👥 ${friendCount}</span>` : ''}
                    ${amenityStr}
                </div>
            </div>`;
        }).join('') || '<p class="muted" style="padding:16px">No courts match your filter</p>';
    },

    setSort(sortKey) {
        MapView.courtListSort = sortKey;
        MapView.renderCourtList();
    },

    _sortCourts(courts) {
        const sorted = [...courts];
        switch (MapView.courtListSort) {
            case 'distance': sorted.sort((a, b) => (a.distance ?? 9999) - (b.distance ?? 9999)); break;
            case 'courts': sorted.sort((a, b) => (b.num_courts || 0) - (a.num_courts || 0)); break;
            case 'active': sorted.sort((a, b) => (b.active_players || 0) - (a.active_players || 0)); break;
            default: sorted.sort((a, b) => (a.name || '').localeCompare(b.name || '')); break;
        }
        return sorted;
    },

    _highlightMarker(courtId) {
        const marker = MapView.markers.find(m => m.courtId === courtId);
        if (marker?._icon) marker._icon.classList.add('marker-highlight');
    },

    _unhighlightMarker(courtId) {
        const marker = MapView.markers.find(m => m.courtId === courtId);
        if (marker?._icon) marker._icon.classList.remove('marker-highlight');
    },

    toggleCourtList() {
        MapView.courtListOpen = !MapView.courtListOpen;
        const sidebar = document.getElementById('court-list-sidebar');
        if (sidebar) sidebar.classList.toggle('open', MapView.courtListOpen);
    },
};
