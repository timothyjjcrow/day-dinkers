/* Third Shot — simple social pickleball app */
(() => {
  'use strict';

  // ---------- State ----------
  const DEFAULT_CENTER = [33.6695, -117.8231]; // Orange County, CA
  const state = {
    token: localStorage.getItem('pp_token') || null,
    me: null,
    presence: null,
    unreadMessages: 0,
    pendingRequests: 0,
    gamesToConfirm: 0,
    lastNotifId: null,
    tab: 'courts',
    playSeg: 'games',
    chatSeg: 'chats',
    nearbySkill: '',
    map: null,
    markers: null,
    mapFilter: 'all',
    listSort: 'distance',
    favIds: null, // Set of favorited court ids, loaded lazily for map stars
    userDot: null,
    geoWatchId: null,
    lastAutoCheckAt: 0,
    userLoc: null,
    areaLoc: null,
    courtsInView: [],
    activeThreadUserId: null,
    threadPollTimer: null,
    mePollTimer: null,
  };

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

  // ---------- API ----------
  async function api(path, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    const res = await fetch(`/api${path}`, { ...options, headers });
    let data = null;
    try { data = await res.json(); } catch { /* empty body */ }
    if (res.status === 401 && state.token && !path.startsWith('/auth')) {
      logout();
      throw new Error('Session expired — please log in again');
    }
    if (!res.ok) {
      const code = (data && data.error) || `error_${res.status}`;
      throw new Error(humanError(code));
    }
    return data;
  }

  const ERROR_TEXT = {
    invalid_email: 'Please enter a valid email.',
    password_too_short: 'Password must be at least 6 characters.',
    display_name_required: 'Please enter a display name.',
    email_taken: 'That email is already registered.',
    invalid_credentials: 'Wrong email or password.',
    game_full: 'That game is already full.',
    scheduled_in_past: 'Pick a time in the future.',
    already_friends: 'You are already friends.',
    request_already_sent: 'Request already sent.',
    nothing_to_confirm: 'This score was already handled.',
    nothing_to_dispute: 'This score was already handled.',
    game_not_open: 'This game is no longer open.',
    game_already_started: 'Too late — the game already has players.',
    already_joined: "You're already in this game.",
    user_blocked: "You can't interact with this player.",
  };
  const humanError = (code) => ERROR_TEXT[code] || code.replace(/_/g, ' ');

  function toast(msg) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.remove('hidden');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add('hidden'), 2600);
  }

  // ---------- Format helpers ----------
  function initials(name) {
    return String(name || '?').split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase();
  }
  // Loading placeholder rows (shimmering skeleton cards).
  function skeletonHtml(rows = 4) {
    const card = `
      <div class="skeleton-card">
        <div class="sk-circle sk-shimmer"></div>
        <div style="flex:1">
          <div class="sk-line sk-shimmer" style="width:55%;margin-bottom:8px"></div>
          <div class="sk-line sk-shimmer" style="width:80%"></div>
        </div>
      </div>`;
    return card.repeat(rows);
  }

  // Inline error with a Retry button wired to re-run the view.
  function renderError(el, message, retryFn) {
    el.innerHTML = `
      <div class="empty-state">
        <span class="big">⚠️</span>
        ${esc(message || 'Something went wrong.')}
        <br><button class="btn btn-secondary" data-retry>Try again</button>
      </div>`;
    const btn = el.querySelector('[data-retry]');
    if (btn && retryFn) btn.addEventListener('click', retryFn);
  }

  function emptyHtml(emoji, title, sub) {
    return `<div class="empty-state"><span class="big">${emoji}</span>${esc(title)}${sub ? `<br>${esc(sub)}` : ''}</div>`;
  }

  function avatarHtml(user, cls = '') {
    const bg = esc(user.avatar_color || '#2f9e44');
    const label = esc(initials(user.display_name));
    if (user.avatar_url) {
      // Photo with graceful fallback to the colored-initials avatar on load error.
      return `<div class="avatar ${cls}" style="background:${bg}">`
        + `<img src="${esc(user.avatar_url)}" alt="" loading="lazy" `
        + `onerror="this.remove()" />${label}</div>`;
    }
    return `<div class="avatar ${cls}" style="background:${bg}">${label}</div>`;
  }
  function fmtDateTime(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    const now = new Date();
    const dayMs = 86400000;
    const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diffDays = Math.round((startOf(d) - startOf(now)) / dayMs);
    const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    if (diffDays === 0) return `Today · ${time}`;
    if (diffDays === 1) return `Tomorrow · ${time}`;
    if (diffDays === -1) return `Yesterday · ${time}`;
    return `${d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })} · ${time}`;
  }
  function fmtTimeShort(isoStr) {
    const d = new Date(isoStr);
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }
  const skillLabel = (s) => ({ beginner: 'Beginner', intermediate: 'Intermediate', advanced: 'Advanced', pro: 'Pro' }[s] || s);

  // "Usually plays" availability slots (mirror of backend AVAILABILITY_SLOTS).
  const AVAIL_DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
  const AVAIL_PARTS = [['am', '🌅', 'Mornings'], ['pm', '☀️', 'Afternoons'], ['eve', '🌆', 'Evenings']];
  function availabilitySummary(slots) {
    if (!slots || !slots.length) return [];
    const short = { mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun' };
    const lines = [];
    for (const [part, emoji, label] of AVAIL_PARTS) {
      const days = AVAIL_DAYS.filter((d) => slots.includes(`${d}-${part}`));
      if (!days.length) continue;
      const key = days.join(',');
      const dayText = days.length === 7 ? 'every day'
        : key === 'mon,tue,wed,thu,fri' ? 'weekdays'
        : key === 'sat,sun' ? 'weekends'
        : days.map((d) => short[d]).join(' · ');
      lines.push(`${emoji} ${label} — ${dayText}`);
    }
    return lines;
  }
  function availabilityOverlap(a, b) {
    if (!a || !b) return 0;
    const set = new Set(a);
    return b.reduce((n, s) => n + (set.has(s) ? 1 : 0), 0);
  }
  function fmtDuration(minutes) {
    if (!minutes || minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes}m`;
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return m ? `${h}h ${m}m` : `${h}h`;
  }

  // ---------- Auth ----------
  let authMode = 'login';

  function setupAuth() {
    $('#auth-toggle').addEventListener('click', () => {
      authMode = authMode === 'login' ? 'register' : 'login';
      $('#auth-name').classList.toggle('hidden', authMode === 'login');
      $('#auth-submit').textContent = authMode === 'login' ? 'Log in' : 'Create account';
      $('#auth-toggle').textContent = authMode === 'login'
        ? 'New here? Create an account' : 'Have an account? Log in';
      $('#auth-error').classList.add('hidden');
    });

    $('#auth-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const submitBtn = $('#auth-submit');
      if (submitBtn.disabled) return; // double-tap = duplicate register attempt
      submitBtn.disabled = true;
      const errEl = $('#auth-error');
      errEl.classList.add('hidden');
      try {
        const body = {
          email: $('#auth-email').value.trim(),
          password: $('#auth-password').value,
        };
        if (authMode === 'register') body.display_name = $('#auth-name').value.trim();
        const data = await api(`/auth/${authMode}`, { method: 'POST', body: JSON.stringify(body) });
        state.token = data.token;
        localStorage.setItem('pp_token', data.token);
        applyMe(data);
        showMain();
        openDeepLink();
        handleInviteRef();
      } catch (err) {
        errEl.textContent = err.message;
        errEl.classList.remove('hidden');
      } finally {
        submitBtn.disabled = false;
      }
    });
  }

  function logout() {
    state.token = null;
    state.me = null;
    localStorage.removeItem('pp_token');
    clearInterval(state.mePollTimer);
    clearInterval(state.threadPollTimer);
    $('#main-screen').classList.add('hidden');
    $('#auth-screen').classList.remove('hidden');
  }

  function applyMe(data) {
    state.me = data.user;
    state.presence = data.presence;
    state.unreadMessages = data.unread_messages || 0;
    state.pendingRequests = data.pending_friend_requests || 0;
    state.gamesToConfirm = data.games_to_confirm || 0;

    // Live updates: pop a toast when something new lands while the app is open.
    state.unreadNotifications = data.unread_notifications || 0;
    state.activeGame = data.active_game || null;
    const latest = data.latest_notification;
    if (latest) {
      if (state.lastNotifId !== null && latest.id > state.lastNotifId && !latest.read) {
        const coveredByBanner = latest.related_game_id && state.activeGame
          && state.activeGame.id === latest.related_game_id;
        if (!coveredByBanner) toast(`🔔 ${latest.title}`);
        if (typeof Notification !== 'undefined' && Notification.permission === 'granted' && document.hidden) {
          try {
            new Notification('Third Shot', { body: latest.title, icon: '/icon-512.png', tag: `pp-${latest.id}` });
          } catch { /* not supported */ }
        }
        if (state.tab === 'play') renderPlay();
      }
      state.lastNotifId = latest.id;
    } else if (state.lastNotifId === null) {
      state.lastNotifId = 0;
    }

    renderBadges();
    renderPresenceBanner();
    renderActiveGameBanner();
  }

  function dismissedInvites() {
    try { return JSON.parse(localStorage.getItem('pp_dismissed_invites') || '[]'); }
    catch { return []; }
  }

  function renderActiveGameBanner() {
    const el = $('#active-game-banner');
    const game = state.activeGame;
    if (!game || (game.banner_state === 'invited' && dismissedInvites().includes(game.id))) {
      el.classList.add('hidden');
      $('#app').classList.remove('has-banner');
      return;
    }
    const court = game.court || {};
    const stateCfg = {
      challenge: {
        icon: '⚔️',
        title: `${esc((game.players[0] || {}).display_name || 'Someone')} challenged you!`,
        sub: `Ranked at ${esc(court.name || 'the court')} · tap to accept or decline`,
      },
      invited: {
        icon: '📨',
        title: `${esc((game.players.find((p) => p.user_id === game.creator_id) || {}).display_name || 'A friend')} invited you to play`,
        sub: `${fmtDateTime(game.scheduled_at)} · ${esc(court.name || '')} · tap to join`,
      },
      live: {
        icon: '<span class="agb-dot"></span>',
        title: `LIVE at ${esc(court.name || 'the court')}`,
        sub: game.players.length >= 2 ? 'Tap to enter the score' : `${game.players.length}/${game.max_players} players — waiting for more`,
      },
      confirm: {
        icon: '⚡',
        title: `Confirm the score: ${game.score_team1}–${game.score_team2}`,
        sub: `${esc(game.score_submitted_by_name || 'Opponent')} reported · ${esc(court.name || '')}`,
      },
      waiting: {
        icon: '⏳',
        title: `${game.score_team1}–${game.score_team2} sent for confirmation`,
        sub: `Waiting on opponents · ${esc(court.name || '')}`,
      },
      upcoming: {
        icon: '📅',
        title: `Next game: ${fmtDateTime(game.scheduled_at)}`,
        sub: `${esc(court.name || '')} · ${game.players.length}/${game.max_players} players`,
      },
    }[game.banner_state] || null;
    if (!stateCfg) { el.classList.add('hidden'); return; }

    el.className = `active-game-banner state-${game.banner_state}`;
    el.innerHTML = `
      ${stateCfg.icon.startsWith('<') ? stateCfg.icon : `<span style="font-size:17px">${stateCfg.icon}</span>`}
      <div class="agb-main">
        <div class="agb-title">${stateCfg.title}</div>
        <div class="agb-sub">${stateCfg.sub}</div>
      </div>
      ${game.banner_state === 'invited' ? '<span class="agb-dismiss" id="agb-dismiss">✕</span>' : '<span class="agb-chev">›</span>'}`;
    const dismissBtn = el.querySelector('#agb-dismiss');
    if (dismissBtn) {
      dismissBtn.onclick = (e) => {
        e.stopPropagation();
        const ids = dismissedInvites();
        if (!ids.includes(game.id)) ids.push(game.id);
        localStorage.setItem('pp_dismissed_invites', JSON.stringify(ids.slice(-30)));
        renderActiveGameBanner();
        toast('Invite dismissed — it stays in your Activity');
      };
    }
    el.onclick = () => {
      if (game.banner_state === 'live' && game.players.length >= 2) {
        api(`/games/${game.id}`).then((fresh) => openScoreModal(fresh, () => refreshMe())).catch((e) => toast(e.message));
      } else {
        openGameScreen(game.id);
      }
    };
    $('#app').classList.add('has-banner');
  }

  function renderBadges() {
    const total = state.unreadMessages + state.pendingRequests;
    const badge = $('#chat-badge');
    badge.textContent = total > 99 ? '99+' : String(total);
    badge.classList.toggle('hidden', total === 0);

    const playBadge = $('#play-badge');
    playBadge.textContent = String(state.gamesToConfirm);
    playBadge.classList.toggle('hidden', state.gamesToConfirm === 0);

    const bellBadge = $('#bell-badge');
    const unread = state.unreadNotifications || 0;
    bellBadge.textContent = unread > 99 ? '99+' : String(unread);
    bellBadge.classList.toggle('hidden', unread === 0);
  }

  async function refreshMe() {
    try { applyMe(await api('/me')); } catch { /* logged out */ }
  }

  // ---------- Tabs ----------
  function setupTabs() {
    document.querySelectorAll('.nav-btn').forEach((btn) => {
      btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
  }

  function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    ['courts', 'play', 'chat', 'profile'].forEach((t) => {
      $(`#tab-${t}`).classList.toggle('hidden', t !== tab);
    });
    if (tab === 'courts' && state.map) setTimeout(() => state.map.invalidateSize(), 60);
    if (tab === 'play') renderPlay();
    if (tab === 'chat') renderChat();
    if (tab === 'profile') renderProfile();
  }

  // Empty-state CTA buttons: any element with data-goto jumps to the right
  // spot in the app (works inside modals too — closes them first).
  function setupEmptyStateCtas() {
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-goto]');
      if (!btn) return;
      document.querySelectorAll('.modal-backdrop').forEach((m) => closeModal(m));
      const target = btn.dataset.goto;
      if (target === 'new-game') {
        switchTab('play');
        openNewGameModal();
      } else if (target === 'courts-list') {
        switchTab('courts');
        $('#court-list').classList.remove('hidden');
        if (state.syncListToggle) state.syncListToggle();
      } else if (target === 'chat-friends') {
        state.chatSeg = 'friends';
        document.querySelectorAll('#chat-segments button').forEach((b) => b.classList.toggle('active', b.dataset.seg === 'friends'));
        switchTab('chat');
      } else {
        switchTab(target);
      }
    });
  }

  // ---------- Map / Courts ----------
  function setupMap() {
    const saved = JSON.parse(localStorage.getItem('pp_mapview') || 'null');
    // Center on the user's saved home area when there's no last-viewed map.
    let center = DEFAULT_CENTER;
    let zoom = 11;
    if (saved) {
      center = saved.center; zoom = saved.zoom;
    } else if (state.me && state.me.home_lat != null) {
      center = [state.me.home_lat, state.me.home_lng]; zoom = 12;
      state.areaLoc = [state.me.home_lat, state.me.home_lng];
    }
    state.map = L.map('map', { zoomControl: false }).setView(center, zoom);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OpenStreetMap &copy; CARTO',
      maxZoom: 19,
    }).addTo(state.map);
    state.markers = (typeof L.markerClusterGroup === 'function')
      ? L.markerClusterGroup({
          maxClusterRadius: 46,
          showCoverageOnHover: false,
          spiderfyOnMaxZoom: true,
          iconCreateFunction: (cluster) => {
            const n = cluster.getChildCount();
            const size = n >= 50 ? 44 : n >= 10 ? 38 : 32;
            return L.divIcon({
              className: '',
              html: `<div class="cluster-icon" style="width:${size}px;height:${size}px">${n}</div>`,
              iconSize: [size, size],
            });
          },
        })
      : L.layerGroup();
    state.markers.addTo(state.map);

    $('#map-filters').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      state.mapFilter = btn.dataset.filter;
      document.querySelectorAll('#map-filters button').forEach((b) => b.classList.toggle('active', b === btn));
      fetchCourtsInView();
    });

    state.map.on('moveend', () => {
      const c = state.map.getCenter();
      localStorage.setItem('pp_mapview', JSON.stringify({ center: [c.lat, c.lng], zoom: state.map.getZoom() }));
      fetchCourtsInView();
    });

    $('#locate-btn').addEventListener('click', locateMe);
    $('#bell-btn').addEventListener('click', openActivity);
    const ICON_LIST = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:15px;height:15px;vertical-align:-2px"><line x1="8" x2="21" y1="6" y2="6"/><line x1="8" x2="21" y1="12" y2="12"/><line x1="8" x2="21" y1="18" y2="18"/><line x1="3" x2="3.01" y1="6" y2="6"/><line x1="3" x2="3.01" y1="12" y2="12"/><line x1="3" x2="3.01" y1="18" y2="18"/></svg>';
    const ICON_X = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:15px;height:15px;vertical-align:-2px"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
    const syncListToggle = () => {
      const open = !$('#court-list').classList.contains('hidden');
      $('#list-toggle').innerHTML = open ? `${ICON_X} Close` : `${ICON_LIST} List`;
    };
    $('#list-toggle').addEventListener('click', () => {
      $('#court-list').classList.toggle('hidden');
      syncListToggle();
    });
    $('#court-list').addEventListener('click', (e) => {
      if (e.target.classList.contains('sheet-handle')) {
        $('#court-list').classList.add('hidden');
        syncListToggle();
      }
    });
    state.syncListToggle = syncListToggle;

    document.querySelectorAll('#list-sort button').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.listSort = btn.dataset.sort;
        document.querySelectorAll('#list-sort button').forEach((b) => b.classList.toggle('active', b === btn));
        fetchCourtsInView();
      });
    });

    let searchTimer;
    $('#court-search').addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      const q = e.target.value.trim();
      searchTimer = setTimeout(() => q ? searchCourts(q) : fetchCourtsInView(), 350);
    });

    // Only auto-locate when we have neither a saved view nor a saved home area.
    if (!saved && !(state.me && state.me.home_lat != null)) locateMe(true);
    fetchCourtsInView();
  }

  function locateMe(silent) {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        state.userLoc = [pos.coords.latitude, pos.coords.longitude];
        state.areaLoc = null; // "my location" takes precedence again
        state.map.setView(state.userLoc, 13);
        updateUserDot();
        startLocationWatch();
        fetchCourtsInView();
      },
      () => { if (!silent) toast('Could not get your location'); },
      { timeout: 8000 },
    );
  }

  // The location the rest of the app's "near me" features follow: an explicitly
  // searched area wins, then GPS, then wherever the map is centered.
  function areaLatLng() {
    if (state.areaLoc) return { lat: state.areaLoc[0], lng: state.areaLoc[1] };
    if (state.userLoc) return { lat: state.userLoc[0], lng: state.userLoc[1] };
    const c = state.map ? state.map.getCenter() : { lat: DEFAULT_CENTER[0], lng: DEFAULT_CENTER[1] };
    return { lat: c.lat, lng: c.lng };
  }

  function jumpToPlace(lat, lng, label) {
    state.areaLoc = [lat, lng];
    if (state.map) state.map.setView([lat, lng], 12);
    $('#court-list').classList.add('hidden');
    if (state.syncListToggle) state.syncListToggle();
    const search = $('#court-search');
    if (search) search.value = '';
    if (label) toast(`📍 ${label}`);
    fetchCourtsInView();
  }

  async function loadFavIds() {
    if (!state.token) { state.favIds = new Set(); return; }
    try {
      const favs = await api('/courts/favorites');
      state.favIds = new Set((favs.items || []).map((c) => c.id));
    } catch { state.favIds = new Set(); }
  }

  async function fetchCourtsInView() {
    if (!state.map) return;
    if (state.favIds === null) await loadFavIds();
    const b = state.map.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(4)).join(',');
    let url = `/courts?bbox=${bbox}&limit=250&sort=${state.listSort}`;
    if (state.userLoc) url += `&lat=${state.userLoc[0]}&lng=${state.userLoc[1]}`;
    if (state.mapFilter === 'lighted') url += '&lighted=1';
    if (state.mapFilter === 'indoor') url += '&indoor=1';
    try {
      const data = await api(url);
      let items = data.items;
      if (state.mapFilter === 'active') items = items.filter((c) => c.players_here > 0);
      state.courtsInView = items;
      drawMarkers(items);
      renderCourtList(items);
    } catch { /* network hiccup */ }
  }

  async function searchCourts(q) {
    try {
      const [courtData, placeData] = await Promise.all([
        api(`/courts?q=${encodeURIComponent(q)}&limit=50`),
        api(`/geocode?q=${encodeURIComponent(q)}`).catch(() => ({ items: [] })),
      ]);
      state.courtsInView = courtData.items;
      drawMarkers(courtData.items);
      renderCourtList(courtData.items, placeData.items || []);
      $('#court-list').classList.remove('hidden');
      if (state.syncListToggle) state.syncListToggle();
      if (courtData.items.length) {
        const pts = courtData.items.filter((c) => c.latitude != null);
        if (pts.length) state.map.fitBounds(pts.map((c) => [c.latitude, c.longitude]), { maxZoom: 13, padding: [40, 40] });
      }
    } catch { /* ignore */ }
  }

  function drawMarkers(courts) {
    state.markers.clearLayers();
    courts.forEach((court) => {
      if (court.latitude == null) return;
      const busy = court.players_here > 0;
      const fav = state.favIds && state.favIds.has(court.id);
      const size = busy ? 34 : 26;
      const gameBadge = court.upcoming_games > 0
        ? `<span class="marker-game-badge">${court.upcoming_games}</span>` : '';
      const favBadge = fav ? '<span class="marker-fav-badge">★</span>' : '';
      const icon = L.divIcon({
        className: '',
        html: `<div class="court-marker ${busy ? 'busy' : ''} ${fav ? 'fav' : ''}" style="width:${size}px;height:${size}px">${busy ? court.players_here + '👤' : court.num_courts}${gameBadge}${favBadge}</div>`,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
      });
      L.marker([court.latitude, court.longitude], { icon })
        .addTo(state.markers)
        .on('click', () => openCourtDetail(court.id));
    });
  }

  // ---------- Live location & auto check-in ----------
  function updateUserDot() {
    if (!state.map || !state.userLoc) return;
    if (!state.userDot) {
      state.userDot = L.circleMarker(state.userLoc, {
        radius: 8, color: '#fff', weight: 3, fillColor: '#1971c2', fillOpacity: 1,
      }).addTo(state.map);
    } else {
      state.userDot.setLatLng(state.userLoc);
    }
  }

  function startLocationWatch() {
    if (!navigator.geolocation || state.geoWatchId != null) return;
    state.geoWatchId = navigator.geolocation.watchPosition(
      (pos) => {
        state.userLoc = [pos.coords.latitude, pos.coords.longitude];
        updateUserDot();
        maybeAutoCheckIn();
      },
      () => { /* permission denied or unavailable */ },
      { enableHighAccuracy: true, maximumAge: 30000, timeout: 20000 },
    );
  }

  const AUTO_CHECKIN_MILES = 0.09;   // ~150 m: you're at the court
  const AUTO_CHECKOUT_MILES = 0.45;  // you've clearly left

  function milesBetween(a, b) {
    const R = 3958.8;
    const dLat = (b[0] - a[0]) * Math.PI / 180;
    const dLng = (b[1] - a[1]) * Math.PI / 180;
    const s = Math.sin(dLat / 2) ** 2
      + Math.cos(a[0] * Math.PI / 180) * Math.cos(b[0] * Math.PI / 180) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(s));
  }

  async function maybeAutoCheckIn() {
    if (!state.me || !state.userLoc) return;
    if (localStorage.getItem('pp_auto_checkin') === 'off') return;
    const now = Date.now();
    if (now - (state.lastAutoCheckAt || 0) < 45000) return;
    state.lastAutoCheckAt = now;

    const presence = state.presence;
    if (presence && presence.checked_in) {
      // Auto check-out once you've clearly left the court
      if (presence.court_latitude != null) {
        const dist = milesBetween(state.userLoc, [presence.court_latitude, presence.court_longitude]);
        if (dist > AUTO_CHECKOUT_MILES) {
          try {
            await api('/checkout', { method: 'POST' });
            toast(`👋 Auto checked out of ${presence.court_name}`);
            await refreshMe();
            fetchCourtsInView();
          } catch { /* ignore */ }
        }
      }
      return;
    }

    try {
      const data = await api(`/courts?lat=${state.userLoc[0]}&lng=${state.userLoc[1]}&radius=1&limit=3`);
      const nearest = data.items[0];
      if (nearest && nearest.distance_miles != null && nearest.distance_miles <= AUTO_CHECKIN_MILES) {
        await api(`/courts/${nearest.id}/checkin`, {
          method: 'POST',
          body: JSON.stringify({ looking_for_game: false }),
        });
        toast(`📍 Auto checked in at ${nearest.name}`);
        await refreshMe();
        fetchCourtsInView();
      }
    } catch { /* offline */ }
  }

  function courtRowHtml(c) {
    const cond = c.condition && COURT_CONDITION_LABELS[c.condition];
    return `
      <div class="card row" data-court="${c.id}" style="cursor:pointer">
        <div class="row-main">
          <div class="row-title">${esc(c.name)}${cond ? ` <span class="tag ${c.condition === 'good' ? 'live' : 'warn'}" style="margin:0 0 0 6px;font-size:10.5px;padding:2px 8px">${cond[0]} ${esc(cond[1].split(' — ')[0].split(' /')[0])}</span>` : ''}</div>
          <div class="row-sub">
            ${esc(c.city)}${c.distance_miles != null ? ` · ${c.distance_miles} mi` : ''}
            · ${c.num_courts} court${c.num_courts === 1 ? '' : 's'}
            ${c.rating_avg ? ` · ⭐ ${c.rating_avg} (${c.rating_count})` : ''}
            ${c.players_here ? ` · <b style="color:var(--green-700)">${c.players_here} playing now</b>` : ''}
            ${c.upcoming_games ? ` · ${c.upcoming_games} game${c.upcoming_games === 1 ? '' : 's'} scheduled` : ''}
          </div>
        </div>
        <span class="chev">›</span>
      </div>
    `;
  }

  // Client-side mirror of the server's sort param — keeps search results and
  // already-fetched lists consistent with the selected chip.
  function sortCourts(courts) {
    const sorted = [...courts];
    if (state.listSort === 'rating') {
      sorted.sort((a, b) => (b.rating_avg ?? -1) - (a.rating_avg ?? -1)
        || (b.rating_count || 0) - (a.rating_count || 0)
        || (b.num_courts || 0) - (a.num_courts || 0));
    } else if (state.listSort === 'courts') {
      sorted.sort((a, b) => (b.num_courts || 0) - (a.num_courts || 0));
    } else if (courts.some((c) => c.distance_miles != null)) {
      sorted.sort((a, b) => (a.distance_miles ?? 1e9) - (b.distance_miles ?? 1e9));
    }
    return sorted;
  }

  async function renderCourtList(courts, places = []) {
    const el = $('#court-list-items');
    courts = sortCourts(courts);
    let html = '';

    if (places.length) {
      html += '<div class="section-label" style="margin-top:4px">📍 Jump to area</div>';
      html += places.map((p, i) => `
        <div class="card row" data-place="${i}" style="cursor:pointer">
          <span style="font-size:18px">📍</span>
          <div class="row-main">
            <div class="row-title">${esc(p.label)}</div>
            <div class="row-sub">${esc((p.detail || '').split(',').slice(1, 4).join(',').trim())}</div>
          </div>
          <span class="chev">›</span>
        </div>`).join('');
      html += '<div class="section-label">Courts</div>';
    }

    if (state.token) {
      try {
        const favs = await api('/courts/favorites');
        if (favs.items.length) {
          html += '<div class="section-label" style="margin-top:4px">⭐ Saved courts</div>';
          html += favs.items.map(courtRowHtml).join('');
          html += '<div class="section-label">In view</div>';
        }
      } catch { /* ignore */ }
    }

    html += courts.length
      ? courts.slice(0, 60).map(courtRowHtml).join('')
      : '<div class="empty-state">No courts here — try zooming out or searching.</div>';

    el.innerHTML = html;
    el.querySelectorAll('[data-court]').forEach((row) => {
      row.addEventListener('click', () => openCourtDetail(Number(row.dataset.court)));
    });
    el.querySelectorAll('[data-place]').forEach((row) => {
      const p = places[Number(row.dataset.place)];
      if (p) row.addEventListener('click', () => jumpToPlace(p.lat, p.lng, p.label));
    });
  }

  function openSuggestEditSheet(court, onApplied) {
    const check = (id, label, value) => `
      <label class="row" style="gap:8px;padding:8px 0;cursor:pointer">
        <input type="checkbox" id="${id}" ${value ? 'checked' : ''} style="width:18px;height:18px" /> ${label}
      </label>`;
    const modal = openModal(`
      ${modalHead('Suggest an edit')}
      <p class="row-sub" style="margin-bottom:12px">Spot something wrong about <b>${esc(court.name)}</b>? Fix it below — changes apply once another player confirms them.</p>
      <div class="form-field">
        <label>Number of courts</label>
        <input type="number" id="se-courts" min="1" max="100" value="${court.num_courts || 1}" />
      </div>
      <div class="form-field">
        ${check('se-indoor', '🏠 Indoor', court.indoor)}
        ${check('se-lighted', '💡 Lighted', court.lighted)}
        ${check('se-nets', '🥅 Nets provided', court.nets_provided)}
        ${check('se-restrooms', '🚻 Restrooms', court.has_restrooms)}
        ${check('se-water', '🚰 Water fountain', court.has_water)}
      </div>
      <div class="form-field">
        <label>Surface</label>
        <input type="text" id="se-surface" maxlength="60" placeholder="e.g. Concrete, Asphalt, Sport court" value="${esc(court.surface_type || '')}" />
      </div>
      <div class="form-field">
        <label>Fees</label>
        <input type="text" id="se-fees" maxlength="200" placeholder="e.g. Free, $5 drop-in" value="${esc(court.fees || '')}" />
      </div>
      <button class="btn btn-primary btn-block" id="se-submit">Submit suggestion</button>
    `);
    modal.querySelector('#se-submit').addEventListener('click', async () => {
      const body = {
        num_courts: Number(modal.querySelector('#se-courts').value) || court.num_courts,
        indoor: modal.querySelector('#se-indoor').checked,
        lighted: modal.querySelector('#se-lighted').checked,
        nets_provided: modal.querySelector('#se-nets').checked,
        has_restrooms: modal.querySelector('#se-restrooms').checked,
        has_water: modal.querySelector('#se-water').checked,
        surface_type: modal.querySelector('#se-surface').value.trim(),
        fees: modal.querySelector('#se-fees').value.trim(),
      };
      try {
        const res = await api(`/courts/${court.id}/suggest`, { method: 'POST', body: JSON.stringify(body) });
        closeModal(modal);
        if (res.applied_fields.length) {
          toast('Court updated — thanks! ✏️');
          if (onApplied) onApplied();
        } else {
          toast('Suggestion recorded — one more confirmation applies it 🙌');
        }
      } catch (e) {
        toast(e.message === 'no changes' ? 'Nothing changed from the current info' : e.message);
      }
    });
  }

  // ---------- Modal helpers ----------
  function openModal(html, opts = {}) {
    const root = $('#overlay-root');
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop'
      + (opts.chat ? ' chat-modal' : '')
      + (opts.court ? ' court-modal' : '');
    backdrop.innerHTML = `<div class="modal">${html}</div>`;
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeModal(backdrop); });
    root.appendChild(backdrop);
    backdrop.querySelectorAll('.modal-close').forEach((b) => b.addEventListener('click', () => closeModal(backdrop)));

    // Mark the element that's actually allowed to scroll so we can block the
    // page/map behind from scrolling when you drag anywhere else on the sheet.
    const scroller = backdrop.querySelector('.thread-msgs, .cd-scroll') || backdrop.querySelector('.modal');
    if (scroller) scroller.setAttribute('data-scroll', '');
    backdrop.addEventListener('touchmove', (e) => {
      if (!e.target.closest('[data-scroll]')) e.preventDefault();
    }, { passive: false });

    // Show a divider under the sticky header once the generic modal scrolls.
    const modalBox = backdrop.querySelector('.modal');
    const head = modalBox && modalBox.querySelector(':scope > .modal-head');
    if (head && !opts.chat && !opts.court) {
      modalBox.addEventListener('scroll', () => {
        head.classList.toggle('scrolled', modalBox.scrollTop > 4);
      });
    }

    document.documentElement.classList.add('modal-open');
    return backdrop;
  }
  function closeModal(el) {
    if (el && el._cleanup) el._cleanup();
    el?.remove();
    if (!$('#overlay-root').querySelector('.modal-backdrop')) {
      document.documentElement.classList.remove('modal-open');
    }
  }

  // Keep a chat sheet pinned to the visible viewport so the mobile keyboard
  // never covers the input — without hijacking the user's scrolling.
  function attachChatViewport(backdrop, msgsEl, inputEl) {
    const stick = () => { msgsEl.scrollTop = msgsEl.scrollHeight; };
    stick();
    const vv = window.visualViewport;
    if (!vv) return;
    // Only reposition the sheet to the visible viewport — never force-scroll.
    const place = () => {
      if (!document.body.contains(backdrop)) { detach(); return; }
      backdrop.style.top = `${vv.offsetTop}px`;
      backdrop.style.height = `${vv.height}px`;
      backdrop.style.bottom = 'auto';
    };
    let lastH = vv.height;
    const onResize = () => {
      place();
      // Keyboard opening (viewport shrank) → keep the latest messages in view.
      if (vv.height < lastH - 80) stick();
      lastH = vv.height;
    };
    function detach() {
      vv.removeEventListener('resize', onResize);
      vv.removeEventListener('scroll', place);
    }
    place();
    vv.addEventListener('resize', onResize);
    vv.addEventListener('scroll', place);
    if (inputEl) inputEl.addEventListener('focus', () => setTimeout(() => { place(); stick(); }, 300));
    backdrop._cleanup = detach;
  }
  const modalHead = (title) => `<div class="modal-head"><h3>${esc(title)}</h3><button class="modal-close" aria-label="Close">✕</button></div>`;

  // ---------- Court detail ----------
  function starsHtml(rating, interactive = false) {
    let out = '';
    for (let i = 1; i <= 5; i++) {
      const filled = i <= rating;
      out += interactive
        ? `<button type="button" class="star-btn ${filled ? 'on' : ''}" data-star="${i}">★</button>`
        : `<span class="star ${filled ? 'on' : ''}">★</span>`;
    }
    return out;
  }

  function renderReviewSection(el, court) {
    const mine = court.my_review;
    let chosen = mine ? mine.rating : 0;
    const reviews = court.reviews || [];
    const formCard = state.me ? `
      <div class="card" id="cd-review-form">
        <div class="row-title" style="font-size:14px;margin-bottom:6px">${mine ? 'Your review' : 'Rate this court'}</div>
        <div class="star-row" id="cd-stars">${starsHtml(chosen, true)}</div>
        <input type="text" id="cd-review-comment" maxlength="500" placeholder="Add a comment (optional)" value="${esc(mine ? mine.comment : '')}" style="margin:8px 0" />
        <button class="btn btn-primary btn-sm" id="cd-review-save">${mine ? 'Update review' : 'Post review'}</button>
      </div>` : '';
    const others = reviews.filter((r) => !state.me || r.user_id !== state.me.id);
    const listHtml = others.length
      ? others.map((r) => `
        <div class="card row" style="align-items:flex-start">
          ${avatarHtml({ display_name: r.user_name, avatar_color: r.avatar_color, avatar_url: r.avatar_url }, 'sm')}
          <div class="row-main">
            <div class="row-title" style="font-size:13.5px">${esc(r.user_name)} <span class="stars-inline">${starsHtml(r.rating)}</span></div>
            ${r.comment ? `<div class="row-sub">${esc(r.comment)}</div>` : ''}
          </div>
        </div>`).join('')
      : (reviews.length ? '' : '<div class="row-sub" style="padding:4px 4px 8px">No reviews yet — be the first!</div>');
    el.innerHTML = formCard + listHtml;

    if (!state.me) return;
    const starRow = el.querySelector('#cd-stars');
    starRow.addEventListener('click', (e) => {
      const b = e.target.closest('[data-star]');
      if (!b) return;
      chosen = Number(b.dataset.star);
      starRow.innerHTML = starsHtml(chosen, true);
    });
    el.querySelector('#cd-review-save').addEventListener('click', async (e) => {
      if (!chosen) { toast('Pick a star rating first'); return; }
      const btn = e.currentTarget;
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        await api(`/courts/${court.id}/reviews`, {
          method: 'POST',
          body: JSON.stringify({ rating: chosen, comment: el.querySelector('#cd-review-comment').value.trim() }),
        });
        toast('Thanks for the review! ⭐');
        const fresh = await api(`/courts/${court.id}`);
        court.my_review = fresh.my_review;
        court.reviews = fresh.reviews;
        court.rating_avg = fresh.rating_avg;
        court.rating_count = fresh.rating_count;
        renderReviewSection(el, court);
      } catch (err) { toast(err.message); btn.disabled = false; }
    });
  }

  const COURT_CONDITION_LABELS = {
    good: ['🟢', 'All good — come play!'],
    busy: ['🚶', 'Busy — expect a wait'],
    wet: ['💦', 'Wet courts'],
    nets_down: ['🚧', 'Nets down / missing'],
    closed: ['⛔', 'Closed right now'],
  };

  function openConditionSheet(court, onDone) {
    const modal = openModal(`
      ${modalHead('📣 Report conditions')}
      <p class="row-sub" style="margin-bottom:12px">How's ${esc(court.name)} right now? Players nearby will see your report for the next few hours.</p>
      ${Object.entries(COURT_CONDITION_LABELS).map(([key, [emoji, label]]) => `
        <button class="btn btn-secondary btn-block" data-cond="${key}" style="margin-bottom:8px;text-align:left">${emoji} ${label}</button>`).join('')}
    `);
    modal.querySelectorAll('[data-cond]').forEach((b) => b.addEventListener('click', async () => {
      try {
        await api(`/courts/${court.id}/condition`, { method: 'POST', body: JSON.stringify({ condition: b.dataset.cond }) });
        closeModal(modal);
        toast('Thanks — players nearby can see it 📣');
        if (onDone) onDone();
      } catch (e) { toast(e.message); }
    }));
  }

  // After a real session (15+ min), ask how the courts were — feeds the
  // live-conditions banner other players rely on. Skipped for quick in/outs.
  function maybeAskConditions(presence) {
    if (!presence || !presence.checked_in || !presence.checked_in_at) return;
    const mins = (Date.now() - new Date(presence.checked_in_at)) / 60000;
    if (mins < 15) return;
    openConditionSheet({ id: presence.court_id, name: presence.court_name });
  }

  // Downscale a picked image file to a JPEG data URL, stepping quality down
  // until it fits the server's 500KB photo limit.
  function imageFileToDataUrl(file, maxDim = 1280) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(img.width * scale));
        canvas.height = Math.max(1, Math.round(img.height * scale));
        canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
        const maxChars = 660000; // ~500KB decoded
        for (const q of [0.82, 0.65, 0.5, 0.35]) {
          const dataUrl = canvas.toDataURL('image/jpeg', q);
          if (dataUrl.length <= maxChars) { resolve(dataUrl); return; }
        }
        reject(new Error('image_too_large'));
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('bad_image')); };
      img.src = url;
    });
  }

  async function openCourtDetail(courtId) {
    let court;
    try { court = await api(`/courts/${courtId}`); } catch (e) { toast(e.message); return; }

    const tags = [];
    if (court.indoor) tags.push('🏠 Indoor'); else tags.push('☀️ Outdoor');
    if (court.lighted) tags.push('💡 Lighted');
    tags.push(`🏟 ${court.num_courts} court${court.num_courts === 1 ? '' : 's'}`);
    if (court.surface_type) tags.push(esc(court.surface_type));
    if (court.nets_provided) tags.push('🥅 Nets provided');
    if (court.has_restrooms) tags.push('🚻 Restrooms');
    if (court.has_water) tags.push('🚰 Water');
    if (court.fees) tags.push(`<span class="tag warn" style="margin:0">💵 ${esc(court.fees)}</span>`);
    if (court.my_record) {
      const r = court.my_record;
      tags.push(`<span class="tag ${r.wins >= r.losses ? 'live' : ''}" style="margin:0">🎯 You're ${r.wins}–${r.losses} here</span>`);
    }

    const mapsUrl = `https://maps.apple.com/?daddr=${encodeURIComponent(`${court.address} ${court.city}`)}&ll=${court.latitude},${court.longitude}`;

    const playersHtml = court.players_here.length
      ? court.players_here.map((p) => {
          const badges = [];
          if (p.is_me) badges.push('<span class="tag" style="margin:0 0 0 6px">You</span>');
          else if (p.is_friend) badges.push('<span class="tag" style="margin:0 0 0 6px">🤝 Friend</span>');
          if (p.looking_for_game) badges.push('<span class="tag live" style="margin:0 0 0 6px">Wants to play</span>');
          const record = (p.ranked_wins + p.ranked_losses) > 0 ? ` · ${p.ranked_wins}W–${p.ranked_losses}L` : '';
          const actions = p.is_me ? '' : `
            <button class="btn btn-sm" data-challenge="${p.id}" title="Challenge to a ranked match" style="background:#ede9fe;color:#5b21b6">⚔️</button>
            <button class="btn btn-secondary btn-sm" data-msg-user="${p.id}" title="Message">💬</button>
            ${!p.is_friend ? `<button class="btn btn-primary btn-sm" data-add-friend-inline="${p.id}" title="Add friend">＋</button>` : ''}`;
          return `
          <div class="card row" style="padding:11px">
            <div data-view-user="${p.id}" style="cursor:pointer">${avatarHtml(p)}</div>
            <div class="row-main" data-view-user="${p.id}" style="cursor:pointer">
              <div class="row-title" style="display:flex;align-items:center;flex-wrap:wrap">${esc(p.display_name)}${badges.join('')}</div>
              <div class="row-sub">${skillLabel(p.skill_level)} · ${p.rating}${record} · here ${fmtDuration(p.minutes_here)}</div>
            </div>
            ${actions}
          </div>`;
        }).join('')
      : '<div class="empty-state" style="padding:14px">No one checked in right now — be the first!</div>';

    let gamesHtml = '';
    if (court.games.length) {
      // Group by day (backend sends them sorted by scheduled_at).
      const byDay = [];
      for (const g of court.games) {
        const label = upcomingDayLabel(g.scheduled_at);
        if (!byDay.length || byDay[byDay.length - 1].label !== label) {
          byDay.push({ label, games: [] });
        }
        byDay[byDay.length - 1].games.push(g);
      }
      if (byDay.length > 1) {
        gamesHtml += `<div class="quick-times" style="margin:0 0 10px">${byDay
          .map((d) => `<button type="button" disabled style="cursor:default">${esc(d.label)} · ${d.games.length}</button>`)
          .join('')}</div>`;
      }
      gamesHtml += byDay.map((d) => `
        <div class="section-label" style="font-size:11px;margin-top:8px">${esc(d.label)}</div>
        ${d.games.map((g) => gameCardHtml(g, { compact: true })).join('')}`).join('');
    } else {
      gamesHtml = '<div class="empty-state" style="padding:14px">No upcoming games here yet.<br><button class="btn btn-secondary btn-sm" id="cd-schedule-empty" style="margin-top:8px">📅 Schedule one</button></div>';
    }

    const checkedIn = court.is_checked_in;
    let isFavorite = court.is_favorite;
    try { history.replaceState(null, '', `#court/${court.id}`); } catch { /* ignore */ }
    const heroImg = court.photo_url
      ? `<img class="cd-hero-img" src="${esc(court.photo_url)}" alt="" onerror="this.outerHTML='<div class=\\'cd-hero-img placeholder\\'>🏓</div>'">`
      : '<div class="cd-hero-img placeholder">🏓</div>';
    const chipsHtml = tags.map((t) => t.startsWith('<span') ? t : `<span class="tag">${t}</span>`).join('');
    const linkParts = [];
    if (court.website) linkParts.push(`<a href="${esc(court.website)}" target="_blank" rel="noopener">🌐 Website</a>`);
    if (court.phone) linkParts.push(`<a href="tel:${esc(court.phone)}">📞 ${esc(court.phone)}</a>`);

    const modal = openModal(`
      <div class="cd-hero">
        ${heroImg}
        <div class="cd-hero-shade"></div>
        <div class="cd-hero-actions">
          <button class="glass-btn" id="cd-share" title="Share" aria-label="Share court"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:17px;height:17px"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" x2="12" y1="2" y2="15"/></svg></button>
          <button class="glass-btn" id="cd-favorite" title="Save" aria-label="Save court">${isFavorite ? '★' : '☆'}</button>
          ${court.photo_count > 0
            ? `<button class="glass-btn" id="cd-gallery" title="Photos" aria-label="View court photos" style="font-size:13px">📷 ${court.photo_count}</button>`
            : '<button class="glass-btn" id="cd-add-photo" title="Add a photo" aria-label="Add a photo">📷</button>'}
          <button class="glass-btn modal-close" aria-label="Close">✕</button>
        </div>
        <div class="cd-hero-title">
          <h2>${esc(court.name)}</h2>
          <div id="cd-address" role="button" title="Copy address" style="cursor:pointer">
            ${esc([court.address, court.city].filter(Boolean).join(', '))}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:13px;height:13px;vertical-align:-2px;opacity:.85"><rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>
          </div>
        </div>
      </div>
      <div class="cd-scroll">
      <button class="btn ${checkedIn ? 'btn-danger' : 'btn-primary'} btn-block" id="cd-checkin" style="padding:15px;margin-bottom:10px">
        ${checkedIn ? 'Check out' : "📍 I'm here — check in"}
      </button>
      <div class="action-grid">
        <button class="action-tile" id="cd-play-now"><span class="tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 3 20 12 6 21 6 3"/></svg></span>Play now</button>
        <button class="action-tile" id="cd-schedule"><span class="tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/></svg></span>Schedule</button>
        <button class="action-tile" id="cd-chat"><span class="tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/></svg></span>Chat</button>
        <a class="action-tile" href="${mapsUrl}" target="_blank" rel="noopener"><span class="tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="3 11 22 2 13 21 11 13 3 11"/></svg></span>Directions</a>
      </div>
      ${court.latest_condition ? (() => {
        const c = COURT_CONDITION_LABELS[court.latest_condition.condition] || ['📣', court.latest_condition.condition];
        const mins = Math.max(1, Math.round((Date.now() - new Date(court.latest_condition.reported_at)) / 60000));
        return `<div class="card row" style="margin-top:12px;padding:10px 14px;background:${court.latest_condition.condition === 'good' ? 'var(--green-50)' : '#fef3c7'}">
          <span style="font-size:18px">${c[0]}</span>
          <div class="row-main">
            <div class="row-title" style="font-size:14px">${c[1]}</div>
            <div class="row-sub">reported ${mins < 60 ? `${mins}m` : `${Math.round(mins / 60)}h`} ago by ${esc(court.latest_condition.user_name)}</div>
          </div>
        </div>`;
      })() : ''}
      <div style="margin-top:14px">${chipsHtml}
        <button id="cd-suggest" class="tag" style="border:1px dashed var(--line);background:transparent;cursor:pointer">✏️ Suggest an edit</button>
        <button id="cd-condition" class="tag" style="border:1px dashed var(--line);background:transparent;cursor:pointer">📣 Report conditions</button>
      </div>
      ${court.open_play_schedule ? `
        <details class="cd-hours">
          <summary>🕑 Open play hours</summary>
          <p>${esc(court.open_play_schedule)}</p>
        </details>` : ''}
      ${linkParts.length ? `<div class="cd-links">${linkParts.join('')}</div>` : ''}
      <div class="section-label">Playing now (${court.players_here.length})${court.friends_here ? ` · ${court.friends_here} friend${court.friends_here === 1 ? '' : 's'} here` : ''}</div>
      ${playersHtml}
      ${(court.regulars || []).length ? `
        <div class="section-label">Court regulars</div>
        ${court.regulars.map((p) => `
          <div class="card row" data-view-user="${p.id}" style="cursor:pointer;padding:11px">
            ${avatarHtml(p, 'sm')}
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(p.display_name)}</div>
              <div class="row-sub">${skillLabel(p.skill_level)} · ${p.rating} · ${p.visits} visit${p.visits === 1 ? '' : 's'} recently</div>
            </div>
            <span class="chev">›</span>
          </div>`).join('')}` : ''}
      <div class="section-label">Upcoming games</div>
      ${gamesHtml}
      ${(court.recent_results || []).length ? `
        <div class="section-label">Recent results here</div>
        ${court.recent_results.map(resultRowHtml).join('')}` : ''}
      <div class="section-label">Reviews${court.rating_avg ? ` · ⭐ ${court.rating_avg} (${court.rating_count})` : ''}</div>
      <div id="cd-reviews"></div>
      </div>
    `, { court: true });

    renderReviewSection(modal.querySelector('#cd-reviews'), court);

    modal.querySelector('#cd-checkin').addEventListener('click', async () => {
      if (checkedIn) {
        const prev = state.presence;
        try {
          await api('/checkout', { method: 'POST' });
          toast('Checked out 👋');
          closeModal(modal);
          await refreshMe();
          fetchCourtsInView();
          maybeAskConditions(prev);
        } catch (e) { toast(e.message); }
        return;
      }
      closeModal(modal);
      openCheckInSheet(court);
    });

    modal.addEventListener('click', (e) => {
      if (e.target === modal) {
        try { history.replaceState(null, '', location.pathname); } catch { /* ignore */ }
      }
    });
    modal.querySelector('#cd-suggest').addEventListener('click', () => {
      openSuggestEditSheet(court, () => { closeModal(modal); openCourtDetail(court.id); });
    });
    modal.querySelector('#cd-condition').addEventListener('click', () => {
      openConditionSheet(court, () => { closeModal(modal); openCourtDetail(court.id); });
    });

    const uploadCourtPhoto = (onDone) => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/*';
      input.addEventListener('change', async () => {
        const file = input.files && input.files[0];
        if (!file) return;
        let photo;
        try { photo = await imageFileToDataUrl(file); }
        catch { toast('Could not read that image'); return; }
        try {
          await api(`/courts/${court.id}/photo`, { method: 'POST', body: JSON.stringify({ photo }) });
          toast('Photo added 📷 Thanks for contributing!');
          onDone();
        } catch (e) { toast(e.message); }
      });
      input.click();
    };
    modal.querySelector('#cd-add-photo')?.addEventListener('click', () => {
      uploadCourtPhoto(() => { closeModal(modal); openCourtDetail(court.id); });
    });
    modal.querySelector('#cd-gallery')?.addEventListener('click', () => {
      openCourtGallery(court, uploadCourtPhoto);
    });

    modal.querySelector('#cd-address').addEventListener('click', async () => {
      const addressText = [court.address, court.city, court.state, court.zip_code]
        .filter(Boolean).join(', ');
      // writeText can reject even on secure contexts (unfocused document,
      // denied permission) — always fall back to the hidden-textarea trick.
      let copied = false;
      if (navigator.clipboard && window.isSecureContext) {
        copied = await navigator.clipboard.writeText(addressText).then(() => true, () => false);
      }
      if (!copied) {
        try {
          const ta = document.createElement('textarea');
          ta.value = addressText;
          ta.style.cssText = 'position:fixed;opacity:0';
          document.body.appendChild(ta);
          ta.select();
          copied = document.execCommand('copy');
          ta.remove();
        } catch { /* fall through */ }
      }
      toast(copied ? 'Address copied 📋' : 'Could not copy address');
    });

    modal.querySelector('#cd-share').addEventListener('click', async () => {
      const url = `${location.origin}/#court/${court.id}`;
      const text = `${court.name} — pickleball at ${court.city || 'this court'}`;
      try {
        if (navigator.share) {
          await navigator.share({ title: 'Third Shot', text, url });
        } else {
          await navigator.clipboard.writeText(url);
          toast('Link copied 📋');
        }
      } catch { /* user cancelled share */ }
    });

    modal.querySelector('#cd-play-now').addEventListener('click', () => {
      closeModal(modal);
      openNewGameModal(court, 'casual', true);
    });
    modal.querySelector('#cd-schedule').addEventListener('click', () => {
      closeModal(modal);
      openNewGameModal(court, 'casual');
    });
    modal.querySelector('#cd-schedule-empty')?.addEventListener('click', () => {
      closeModal(modal);
      openNewGameModal(court, 'casual');
    });

    modal.querySelector('#cd-favorite').addEventListener('click', async (e) => {
      const favBtn = e.currentTarget;
      try {
        const data = await api(`/courts/${court.id}/favorite`, { method: 'POST' });
        isFavorite = data.favorited;
        favBtn.textContent = isFavorite ? '★' : '☆';
        if (state.favIds) state.favIds[isFavorite ? 'add' : 'delete'](court.id);
        fetchCourtsInView(); // restar the map markers
        toast(isFavorite ? 'Court saved ⭐' : 'Removed from saved courts');
      } catch (err) { toast(err.message); }
    });

    modal.querySelector('#cd-chat').addEventListener('click', () => {
      openCourtChat(court);
    });

    modal.querySelectorAll('[data-challenge]').forEach((b) => b.addEventListener('click', () => {
      const player = court.players_here.find((p) => p.id === Number(b.dataset.challenge));
      if (player) openChallengeSheet(player, court);
    }));

    modal.querySelectorAll('[data-msg-user]').forEach((b) => b.addEventListener('click', () => {
      closeModal(modal);
      openThread(Number(b.dataset.msgUser));
    }));
    modal.querySelectorAll('[data-add-friend-inline]').forEach((b) => b.addEventListener('click', async () => {
      try {
        await api('/friends/request', { method: 'POST', body: JSON.stringify({ user_id: Number(b.dataset.addFriendInline) }) });
        toast('Friend request sent!');
        b.remove();
      } catch (e) { toast(e.message); }
    }));

    bindGameButtons(modal, () => { closeModal(modal); openCourtDetail(courtId); });
    bindUserButtons(modal);
  }

  function openChallengeSheet(player, court) {
    const modal = openModal(`
      <div class="checkin-sheet">
        <div class="celebrate-emoji" style="font-size:46px">⚔️</div>
        <h3 style="margin:6px 0 2px">Challenge ${esc(player.display_name)}</h3>
        <p class="row-sub" style="margin-bottom:6px">${skillLabel(player.skill_level)} · ${player.rating} rated</p>
        <p class="row-sub" style="margin-bottom:18px">Ranked singles at ${esc(court.name)}, starting now. Winner takes the rating points. 🏆</p>
        <button class="btn btn-primary btn-block" id="ch-send" style="padding:16px;margin-bottom:8px">⚔️ Send challenge</button>
        <button class="btn-link modal-close btn-block">Maybe later</button>
      </div>
    `);
    modal.querySelector('#ch-send').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        const game = await api(`/users/${player.id}/challenge`, {
          method: 'POST',
          body: JSON.stringify({ court_id: court.id }),
        });
        closeModal(modal);
        toast(`⚔️ Challenge sent to ${player.display_name}!`);
        refreshMe();
        openGameScreen(game.id);
      } catch (err) { toast(err.message); btn.disabled = false; }
    });
  }

  function openCheckInSheet(court) {
    const modal = openModal(`
      <div class="checkin-sheet">
        <div class="celebrate-emoji" style="font-size:46px">📍</div>
        <h3 style="margin:6px 0 2px">Check in at ${esc(court.name)}</h3>
        <p class="row-sub" style="margin-bottom:18px">Friends will see you're here.</p>
        <button class="btn btn-primary btn-block" id="ci-lfg" style="margin-bottom:10px;padding:16px">
          🎾 I'm looking for players
        </button>
        <button class="btn btn-secondary btn-block" id="ci-play" style="padding:16px">
          👍 Just playing with my group
        </button>
        <button class="btn-link modal-close btn-block" style="margin-top:8px">Cancel</button>
      </div>
    `);
    const doCheckIn = async (looking) => {
      try {
        await api(`/courts/${court.id}/checkin`, {
          method: 'POST',
          body: JSON.stringify({ looking_for_game: looking }),
        });
        closeModal(modal);
        toast(looking ? `You're in — players can find you 🎾` : `Checked in at ${court.name}`);
        await refreshMe();
        fetchCourtsInView();
      } catch (e) { toast(e.message); }
    };
    modal.querySelector('#ci-lfg').addEventListener('click', () => doCheckIn(true));
    modal.querySelector('#ci-play').addEventListener('click', () => doCheckIn(false));
  }

  // ---------- Games ----------
  function gameCardHtml(game, { compact = false } = {}) {
    const court = game.court || {};
    const typeTag = game.game_type === 'ranked'
      ? '<span class="tag ranked" style="margin:0 0 0 8px">🏆 Ranked</span>'
      : '<span class="tag" style="margin:0 0 0 8px">Casual</span>';
    const visTag = game.visibility === 'private'
      ? '<span class="tag" style="margin:0 0 0 6px">🔒 Invite</span>'
      : game.visibility === 'friends'
        ? '<span class="tag" style="margin:0 0 0 6px">🤝 Friends</span>'
        : '';
    const recurTag = game.recurrence === 'weekly'
      ? '<span class="tag" style="margin:0 0 0 6px">🔁 Weekly</span>'
      : '';
    // Discovery aid: flag joinable games whose players average near your rating.
    let levelTag = '';
    if (state.me && !game.is_joined && game.status === 'upcoming'
        && game.spots_left > 0 && game.players.length) {
      const avg = game.players.reduce((s, p) => s + (p.rating || 1200), 0) / game.players.length;
      if (Math.abs(avg - state.me.rating) <= 100) {
        levelTag = '<span class="tag live" style="margin:0 0 0 6px">⚖️ Your level</span>';
      }
    }
    const host = game.players.find((p) => p.user_id === game.creator_id);
    const hostLabel = host ? ` · Host: ${esc(host.display_name)}` : '';
    const avatars = game.players.slice(0, 5).map((p) => avatarHtml(p, 'sm')).join('');

    let action = '';
    let banner = '';
    let cardStyle = '';

    if (game.status === 'upcoming') {
      const startMs = new Date(game.scheduled_at).getTime();
      const inProgress = startMs <= Date.now();
      if (game.is_joined) {
        if (inProgress) {
          cardStyle = 'border:2px solid var(--green-600)';
          banner = `<div class="status-banner live-banner">🟢 ${game.players.length >= 2 ? 'Game time! Tap to enter the score.' : 'Live — waiting for players to join.'}</div>`;
        } else {
          const mins = Math.round((startMs - Date.now()) / 60000);
          banner = `<div class="status-banner">⏱ Starts in ${fmtDuration(mins)}</div>`;
        }
      } else if (game.spots_left > 0) {
        action = `<button class="btn btn-primary btn-sm" data-game-join="${game.id}">Join</button>`;
      } else if (game.waitlist_position) {
        action = `<span class="tag" style="margin:0">⏳ #${game.waitlist_position} in line</span>`;
      } else {
        action = `<span class="tag warn" style="margin:0">Full</span>
          <button class="btn btn-secondary btn-sm" data-game-waitlist="${game.id}">⏳ Waitlist</button>`;
      }
    } else if (game.status === 'awaiting_confirmation') {
      const scoreText = `${game.score_team1}–${game.score_team2}`;
      if (game.awaiting_your_confirmation) {
        cardStyle = 'border:2px solid var(--amber-500)';
        banner = `<div class="status-banner confirm-banner">📝 ${esc(game.score_submitted_by_name || 'Opponent')} reported <b>${scoreText}</b> — is that right?</div>`;
        action = `<button class="btn btn-primary btn-sm" data-game-confirm="${game.id}">✓ Confirm</button>
                  <button class="btn btn-danger btn-sm" data-game-dispute="${game.id}">✕</button>`;
      } else {
        banner = `<div class="status-banner">⏳ ${scoreText} reported — waiting for opponents to confirm</div>`;
      }
    } else if (game.status === 'completed') {
      const delta = game.your_rating_delta;
      const deltaHtml = delta != null
        ? ` <span class="${delta >= 0 ? 'delta-up' : 'delta-down'}">${delta >= 0 ? '+' : ''}${delta}</span>` : '';
      if (game.you_won === true) {
        action = `<span class="tag live" style="margin:0">🏆 Won ${game.score_team1}–${game.score_team2}</span>${deltaHtml}`;
      } else if (game.you_won === false) {
        action = `<span class="tag warn" style="margin:0">Lost ${game.score_team1}–${game.score_team2}</span>${deltaHtml}`;
      } else {
        action = `<span class="tag" style="margin:0">${game.score_team1}–${game.score_team2}</span>`;
      }
    }

    return `
      <div class="card" style="${cardStyle};cursor:pointer" data-open-game="${game.id}">
        <div class="row" style="margin-bottom:8px">
          <div class="row-main">
            <div class="row-title">${esc(game.recurrence === 'weekly' && game.status === 'upcoming'
              ? `${new Date(game.scheduled_at).toLocaleDateString([], { weekday: 'long' })}s · ${new Date(game.scheduled_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
              : fmtDateTime(game.scheduled_at))}${typeTag}${visTag}${recurTag}${levelTag}</div>
            <div class="row-sub">${esc(court.name || '')}${!compact && court.city ? ` · ${esc(court.city)}` : ''}${game.distance_miles != null ? ` · ${game.distance_miles} mi` : ''}${hostLabel}</div>
          </div>
          <span class="chev">›</span>
        </div>
        ${banner}
        ${game.notes ? `<div class="row-sub" style="margin-bottom:8px">“${esc(game.notes)}”</div>` : ''}
        <div class="row">
          <div class="avatar-stack">${avatars}</div>
          <span class="row-sub">${game.players.length}/${game.max_players} players${game.spots_left && game.status === 'upcoming' ? ` · ${game.spots_left} spot${game.spots_left === 1 ? '' : 's'} left` : ''}</span>
          <div style="margin-left:auto;display:flex;gap:6px;align-items:center">${action}</div>
        </div>
      </div>`;
  }

  function bindGameButtons(rootEl, refresh) {
    // Tap anywhere on a card to open the game screen; inline buttons stop propagation.
    rootEl.querySelectorAll('[data-open-game]').forEach((card) => card.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      openGameScreen(Number(card.dataset.openGame));
    }));
    rootEl.querySelectorAll('[data-game-join]').forEach((b) => b.addEventListener('click', async (e) => {
      e.stopPropagation();
      try { await api(`/games/${b.dataset.gameJoin}/join`, { method: 'POST' }); toast('You joined the game! \u{1F3BE}'); refreshMe(); refresh(); }
      catch (err) { toast(err.message); }
    }));
    rootEl.querySelectorAll('[data-game-waitlist]').forEach((b) => b.addEventListener('click', async (e) => {
      e.stopPropagation();
      try { await api(`/games/${b.dataset.gameWaitlist}/waitlist`, { method: 'POST' }); toast("You're on the waitlist — we'll ping you if a spot opens ⏳"); refresh(); }
      catch (err) { toast(err.message); }
    }));
    rootEl.querySelectorAll('[data-game-confirm]').forEach((b) => b.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        const game = await api(`/games/${b.dataset.gameConfirm}/confirm`, { method: 'POST' });
        showCelebration(game);
        refreshMe();
        refresh();
      } catch (err) { toast(err.message); }
    }));
    rootEl.querySelectorAll('[data-game-dispute]').forEach((b) => b.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('Dispute this score? It will be cleared so it can be re-entered.')) return;
      try {
        await api(`/games/${b.dataset.gameDispute}/dispute`, { method: 'POST' });
        toast('Score disputed \u2014 enter the correct one together');
        refreshMe();
        refresh();
      } catch (err) { toast(err.message); }
    }));
  }

  // Share text that fits the game's state: brag about results, invite to
  // upcoming games.
  function gameShareText(game) {
    const courtName = game.court ? game.court.name : '';
    if (game.status === 'completed' && game.score_team1 != null) {
      const score = `${game.score_team1}–${game.score_team2}`;
      if (game.you_won === true) return `Just won ${score}${courtName ? ` at ${courtName}` : ''} 🏆 Come play on Third Shot!`;
      if (game.you_won === false) return `Battled to ${score}${courtName ? ` at ${courtName}` : ''} — rematch soon 🎾`;
      return `Final: ${score}${courtName ? ` at ${courtName}` : ''} on Third Shot 🎾`;
    }
    return `Join my pickleball game${courtName ? ` at ${courtName}` : ''} — ${fmtDateTime(game.scheduled_at)}`;
  }

  async function shareGame(game) {
    const url = `${location.origin}/#game/${game.id}`;
    const text = gameShareText(game);
    try {
      if (navigator.share) await navigator.share({ title: 'Third Shot', text, url });
      else { await navigator.clipboard.writeText(`${text} ${url}`); toast('Copied to share 📋'); }
    } catch { /* user cancelled */ }
  }

  function showCelebration(game) {
    const won = game.you_won;
    const delta = game.your_rating_delta;
    const ranked = game.game_type === 'ranked';
    const me = state.me || {};
    const streak = won && ranked ? (me.current_streak || 0) + 1 : 0;
    const emoji = won === true ? '🏆' : won === false ? '🤝' : '🎾';
    const headline = won === true ? 'Victory!' : won === false ? 'Good game!' : 'Game recorded!';
    const sub = won === true
      ? 'That one goes in the books.'
      : won === false ? 'They got you this time — rematch?' : 'Nice playing!';

    const modal = openModal(`
      <div class="celebrate">
        <div class="celebrate-emoji">${emoji}</div>
        <h2>${headline}</h2>
        <div class="celebrate-score">${game.score_team1}–${game.score_team2}</div>
        <p class="row-sub">${esc(game.court ? game.court.name : '')} · ${sub}</p>
        ${ranked && delta != null ? `
          <div class="celebrate-delta ${delta >= 0 ? 'delta-up' : 'delta-down'}">
            ${delta >= 0 ? '+' : ''}${delta} rating
          </div>` : ''}
        ${streak >= 2 ? `<div class="tag live" style="font-size:14px;padding:6px 14px">🔥 ${streak} win streak!</div>` : ''}
        ${won === true ? '<button class="btn btn-secondary btn-block" id="cel-share" style="margin-top:18px">📤 Share the win</button>' : ''}
        <button class="btn btn-primary btn-block modal-close" style="margin-top:${won === true ? '10' : '18'}px">Keep playing</button>
      </div>
    `);
    modal.querySelector('#cel-share')?.addEventListener('click', () => shareGame(game));
  }

  function bindUserButtons(rootEl) {
    rootEl.querySelectorAll('[data-view-user]').forEach((b) => b.addEventListener('click', () => {
      openUserProfile(Number(b.dataset.viewUser));
    }));
  }

  function resultRowHtml(game) {
    const court = game.court || {};
    const t1 = game.players.filter((p) => p.team === 1);
    const t2 = game.players.filter((p) => p.team === 2);
    const t1Won = game.score_team1 > game.score_team2;
    const firstName = (p) => esc(p.display_name.split(' ')[0]);
    const names = (team) => team.map(firstName).join(' & ') || '—';
    const mine = game.you_won === true || game.you_won === false;
    const delta = game.your_rating_delta;

    const meta = [
      esc(court.name || ''),
      fmtDateTime(game.completed_at),
      game.game_type === 'ranked' ? '🏆 Ranked' : 'Casual',
    ];
    if (!mine && game.involves_friend) meta.push('🤝 Friend');

    let badge;
    let line;
    if (mine) {
      const me = game.players.find((p) => p.user_id === (state.me || {}).id);
      const myTeam = me ? me.team : 1;
      const opponents = myTeam === 1 ? t2 : t1;
      const myScore = myTeam === 1 ? game.score_team1 : game.score_team2;
      const oppScore = myTeam === 1 ? game.score_team2 : game.score_team1;
      badge = `<div class="rr-badge ${game.you_won ? 'won' : 'lost'}">${game.you_won ? 'W' : 'L'}</div>`;
      line = `
        <span class="rr-score">${myScore}–${oppScore}</span>
        <span class="rr-vs">vs ${names(opponents)}</span>
        ${delta != null ? `<span class="rr-delta ${delta >= 0 ? 'delta-up' : 'delta-down'}">${delta >= 0 ? '+' : ''}${delta}</span>` : ''}`;
    } else {
      const winners = t1Won ? t1 : t2;
      const losers = t1Won ? t2 : t1;
      const winScore = t1Won ? game.score_team1 : game.score_team2;
      const loseScore = t1Won ? game.score_team2 : game.score_team1;
      badge = '<div class="rr-badge neutral">🏆</div>';
      line = `
        <span class="rr-winner">${names(winners)}</span>
        <span class="rr-score">${winScore}–${loseScore}</span>
        <span class="rr-vs">${names(losers)}</span>`;
    }

    return `
      <div class="result-row" data-open-game="${game.id}">
        ${badge}
        <div class="rr-main">
          <div class="rr-line">${line}</div>
          <div class="rr-meta">${meta.join(' · ')}</div>
        </div>
        <span class="chev">›</span>
      </div>`;
  }

  function upcomingDayLabel(isoStr) {
    if (!isoStr) return 'Upcoming';
    const d = new Date(isoStr);
    const now = new Date();
    const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((startOf(d) - startOf(now)) / 86400000);
    if (diff <= 0) return 'Today';
    if (diff === 1) return 'Tomorrow';
    if (diff < 7) return d.toLocaleDateString([], { weekday: 'long' });
    return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
  }

  function resultDayLabel(isoStr) {
    if (!isoStr) return 'Earlier';
    const d = new Date(isoStr);
    const now = new Date();
    const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((startOf(now) - startOf(d)) / 86400000);
    if (diff === 0) return 'Today';
    if (diff === 1) return 'Yesterday';
    if (diff < 7) return d.toLocaleDateString([], { weekday: 'long' });
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  async function renderPlay() {
    const seg = state.playSeg;
    const el = $('#play-content');
    el.innerHTML = skeletonHtml(5);
    const loc = areaLatLng();
    try {
      if (seg === 'scores') {
        const scope = state.boardScope || 'near';
        const boardUrl = scope === 'near'
          ? `/leaderboard?lat=${loc.lat}&lng=${loc.lng}&radius=50`
          : scope === 'month' ? '/leaderboard?period=month' : '/leaderboard';
        const isMonth = scope === 'month';
        const boardVal = (u) => (isMonth
          ? `<span class="${u.month_delta >= 0 ? 'delta-up' : 'delta-down'}">${u.month_delta >= 0 ? '+' : ''}${u.month_delta}</span>`
          : u.rating);
        const [board, results] = await Promise.all([
          api(boardUrl),
          api(`/games/results?lat=${loc.lat}&lng=${loc.lng}`),
        ]);
        let html = `
          <div class="segmented" id="board-scope" style="margin:2px 0 12px">
            <button data-scope="near" class="${scope === 'near' ? 'active' : ''}">📍 Near me</button>
            <button data-scope="all" class="${scope === 'all' ? 'active' : ''}">🌎 Everyone</button>
            <button data-scope="month" class="${isMonth ? 'active' : ''}">📈 This month</button>
          </div>`;

        if (board.items.length) {
          const top3 = board.items.slice(0, 3);
          // Podium order: 2nd, 1st, 3rd
          const order = [top3[1], top3[0], top3[2]].filter(Boolean);
          const place = (u) => board.items.indexOf(u) + 1;
          html += '<div class="podium">' + order.map((u) => `
            <div class="podium-col place-${place(u)}" data-view-user="${u.id}">
              <div class="podium-medal">${['🥇', '🥈', '🥉'][place(u) - 1]}</div>
              ${avatarHtml(u)}
              <div class="podium-name">${esc(u.display_name.split(' ')[0])}${u.current_streak >= 2 ? ' 🔥' : ''}</div>
              <div class="podium-rating">${boardVal(u)}</div>
              <div class="podium-base"></div>
            </div>`).join('') + '</div>';

          const rest = board.items.slice(3, 10);
          if (rest.length) {
            html += rest.map((u, i) => `
              <div class="card row ${state.me && u.id === state.me.id ? 'you-row' : ''}" data-view-user="${u.id}" style="cursor:pointer;padding:10px 14px">
                <div class="rank-num">${i + 4}</div>
                ${avatarHtml(u, 'sm')}
                <div class="row-main">
                  <div class="row-title" style="font-size:14px">${esc(u.display_name)}${u.current_streak >= 2 ? ` <span title="Win streak">🔥${u.current_streak}</span>` : ''}</div>
                  <div class="row-sub">${isMonth ? `${u.month_games} ranked game${u.month_games === 1 ? '' : 's'} this month` : `${u.ranked_wins}W – ${u.ranked_losses}L`}</div>
                </div>
                <div class="stat-value" style="font-size:16px">${boardVal(u)}</div>
              </div>`).join('');
          }
          const me = state.me;
          if (me && !board.items.some((u) => u.id === me.id)) {
            html += `<div class="card row" style="padding:10px 14px">
              <div class="rank-num">—</div>
              ${avatarHtml(me, 'sm')}
              <div class="row-main">
                <div class="row-title" style="font-size:14px">You</div>
                <div class="row-sub">Win a ranked game to enter the leaderboard</div>
              </div>
              <div class="stat-value" style="font-size:16px">${me.rating}</div>
            </div>`;
          }
        } else {
          html += scope === 'near'
            ? '<div class="empty-state"><span class="big">🏆</span>No ranked players in your area yet.<br>Win a ranked game and claim the local crown!</div>'
            : '<div class="empty-state"><span class="big">🏆</span>No ranked games yet.<br>Win one and claim the podium!</div>';
        }

        if (results.items.length) {
          html += '<div class="section-label" style="margin-top:18px">Recent games</div>';
          let lastLabel = null;
          results.items.forEach((g) => {
            const label = resultDayLabel(g.completed_at);
            if (label !== lastLabel) {
              if (lastLabel !== null) html += `<div class="section-label" style="font-size:11px">${label}</div>`;
              lastLabel = label;
            }
            html += resultRowHtml(g);
          });
        }

        el.innerHTML = html;
        el.querySelector('#board-scope').addEventListener('click', (e) => {
          const btn = e.target.closest('button');
          if (!btn) return;
          state.boardScope = btn.dataset.scope;
          renderPlay();
        });
        bindGameButtons(el, renderPlay);
        bindUserButtons(el);
        return;
      }

      // --- Games: everything actionable + yours + friends + nearby, one scroll ---
      const [mine, friends, nearby] = await Promise.all([
        api('/games?mine=1'),
        api('/games?friends=1').catch(() => ({ items: [] })),
        api(`/games?lat=${loc.lat}&lng=${loc.lng}&radius=60`),
      ]);
      const nowMs = Date.now();
      const toScore = mine.items.filter((g) =>
        g.status === 'upcoming' && new Date(g.scheduled_at).getTime() <= nowMs && g.players.length >= 2);
      const toConfirm = mine.items.filter((g) => g.awaiting_your_confirmation);
      const waiting = mine.items.filter((g) =>
        g.status === 'awaiting_confirmation' && !g.awaiting_your_confirmation);
      const upcoming = mine.items.filter((g) =>
        !toScore.includes(g) && !toConfirm.includes(g) && !waiting.includes(g));
      const mineIds = new Set(mine.items.map((g) => g.id));
      const friendsGames = (friends.items || []).filter((g) => !mineIds.has(g.id));
      const friendsIds = new Set(friendsGames.map((g) => g.id));
      const nearbyOpen = nearby.items.filter((g) => !mineIds.has(g.id) && !friendsIds.has(g.id));

      let html = '';
      if (toScore.length) {
        html += '<div class="section-label" style="margin-top:6px">🎾 Played — enter the score</div>';
        html += toScore.map((g) => gameCardHtml(g)).join('');
      }
      if (toConfirm.length) {
        html += '<div class="section-label">⚡ Confirm the score</div>';
        html += toConfirm.map((g) => gameCardHtml(g)).join('');
      }
      if (waiting.length) {
        html += '<div class="section-label">⏳ Waiting on opponents</div>';
        html += waiting.map((g) => gameCardHtml(g)).join('');
      }
      if (upcoming.length) {
        html += '<div class="section-label">Your upcoming games</div>';
        html += upcoming.map((g) => gameCardHtml(g)).join('');
      }
      // Weekly open-play sessions get their own discovery section, whether a
      // friend hosts them or they're just nearby. Your own stay under "upcoming".
      const isWeekly = (g) => g.recurrence === 'weekly';
      const weeklySessions = [...friendsGames.filter(isWeekly), ...nearbyOpen.filter(isWeekly)];
      const friendsOneOff = friendsGames.filter((g) => !isWeekly(g));
      const nearbyOneOff = nearbyOpen.filter((g) => !isWeekly(g));
      if (friendsOneOff.length) {
        html += '<div class="section-label">🤝 Friends playing</div>';
        html += friendsOneOff.map((g) => gameCardHtml(g)).join('');
      }
      html += '<div class="section-label">Nearby games</div>';
      html += nearbyOneOff.length
        ? nearbyOneOff.map((g) => gameCardHtml(g)).join('')
        : '<div class="empty-state" style="padding:18px">No open games around you right now.<br><button class="btn btn-primary" data-goto="new-game" style="margin-top:10px">🎾 Start a game</button></div>';
      if (weeklySessions.length) {
        html += '<div class="section-label">🔁 Weekly open play</div>';
        html += weeklySessions.map((g) => gameCardHtml(g)).join('');
      }

      el.innerHTML = html;
      bindGameButtons(el, renderPlay);
    } catch (e) {
      renderError(el, e.message, renderPlay);
    }
  }

  function setupPlay() {
    $('#play-segments').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      state.playSeg = btn.dataset.seg;
      document.querySelectorAll('#play-segments button').forEach((b) => b.classList.toggle('active', b === btn));
      renderPlay();
    });
    $('#new-game-fab').addEventListener('click', () => openNewGameModal());
  }

  async function openNewGameModal(court, defaultType = 'casual', startNow = false) {
    // Gather friends (for invites) and court suggestions in parallel
    let friends = [];
    let suggestions = [];
    try {
      const reqs = [api('/friends').catch(() => ({ friends: [] }))];
      if (!court) {
        const c = areaLatLng();
        reqs.push(api('/courts/favorites').catch(() => ({ items: [] })));
        reqs.push(api(`/courts?lat=${c.lat}&lng=${c.lng}&radius=30&limit=6`).catch(() => ({ items: [] })));
      }
      const res = await Promise.all(reqs);
      friends = res[0].friends || [];
      if (!court) {
        const seen = new Set();
        if (state.presence && state.presence.checked_in) {
          suggestions.push({ id: state.presence.court_id, name: state.presence.court_name, city: '', tag: "📍 You're here" });
          seen.add(state.presence.court_id);
        }
        (res[1].items || []).forEach((c) => {
          if (!seen.has(c.id) && suggestions.length < 5) { suggestions.push({ ...c, tag: '⭐ Saved' }); seen.add(c.id); }
        });
        (res[2].items || []).forEach((c) => {
          if (!seen.has(c.id) && suggestions.length < 5) {
            suggestions.push({ ...c, tag: c.distance_miles != null ? `${c.distance_miles} mi` : 'Nearby' });
            seen.add(c.id);
          }
        });
      }
    } catch { /* suggestions are optional */ }

    // Day & time presets
    const days = [];
    for (let i = 0; i < 5; i++) {
      const d = new Date(); d.setDate(d.getDate() + i); d.setHours(0, 0, 0, 0);
      days.push(d);
    }
    const dayLabel = (d, i) => i === 0 ? 'Today' : i === 1 ? 'Tomorrow' : d.toLocaleDateString([], { weekday: 'short' });
    const timePresets = [8, 10, 12, 14, 16, 18, 20];
    const timeLabel = (h) => h === 12 ? '12 PM' : h < 12 ? `${h} AM` : `${h - 12} PM`;

    // Defaults: first preset at least ~1h away today, else tomorrow morning
    let selDayIdx = 0;
    let selHour = timePresets.find((h) => {
      const d = new Date(days[0]); d.setHours(h);
      return d.getTime() > Date.now() + 50 * 60000;
    });
    if (selHour == null) { selDayIdx = 1; selHour = 10; }

    const dayChips = days.map((d, i) =>
      `<button type="button" data-day="${i}" class="${i === selDayIdx ? 'active' : ''}">${dayLabel(d, i)}</button>`).join('');
    const timeChips = timePresets.map((h) =>
      `<button type="button" data-hour="${h}" class="${h === selHour ? 'active' : ''}">${timeLabel(h)}</button>`).join('');

    const friendChips = friends.map((f) => `
      <button type="button" class="invite-chip" data-fid="${f.id}">
        ${avatarHtml(f, 'sm')} ${esc(f.display_name.split(' ')[0])}
      </button>`).join('');

    const suggestionRows = suggestions.map((c) => `
      <button type="button" class="court-suggestion" data-pick-court="${c.id}" data-pick-name="${esc(c.name)}">
        <div class="row-main">
          <div class="row-title" style="font-size:14px">${esc(c.name)}</div>
          <div class="row-sub">${esc(c.city || '')}</div>
        </div>
        <span class="tag" style="margin:0">${esc(c.tag)}</span>
      </button>`).join('');

    const modal = openModal(`
      ${modalHead(startNow ? 'Start a game' : 'Schedule a game')}

      <div class="form-field">
        <label>Court</label>
        <div id="ng-court-selected" class="${court ? '' : 'hidden'} court-selected">
          <div class="row-main">
            <div class="row-title" style="font-size:14.5px" id="ng-court-name">${court ? esc(court.name) : ''}</div>
          </div>
          <button type="button" class="btn btn-secondary btn-sm" id="ng-court-change">Change</button>
        </div>
        <div id="ng-court-picker" class="${court ? 'hidden' : ''}">
          <input type="search" id="ng-court-search" placeholder="Search courts…" />
          <div id="ng-court-results" style="margin-top:8px">${suggestionRows}</div>
        </div>
        <input type="hidden" id="ng-court-id" value="${court ? court.id : ''}" />
      </div>

      <div class="form-field">
        <label>When</label>
        <div class="segmented" id="ng-mode">
          <button type="button" data-mode="now" ${startNow ? 'class="active"' : ''}>▶️ Right now</button>
          <button type="button" data-mode="later" ${startNow ? '' : 'class="active"'}>📅 Schedule</button>
        </div>
      </div>
      <div id="ng-later-fields" class="${startNow ? 'hidden' : ''}">
        <div class="quick-times" id="ng-days" style="margin-bottom:8px">${dayChips}</div>
        <div class="quick-times" id="ng-hours" style="margin-bottom:8px">${timeChips}
          <button type="button" data-hour="custom">Custom…</button>
        </div>
        <input type="datetime-local" id="ng-when" class="hidden" style="margin-bottom:12px" />
      </div>

      <div class="form-grid">
        <div class="form-field">
          <label>Type</label>
          <div class="type-cards" id="ng-type">
            <button type="button" data-val="casual" class="${defaultType === 'casual' ? 'active' : ''}">
              <span style="font-size:20px">🎾</span><b>Casual</b><small>Just for fun</small>
            </button>
            <button type="button" data-val="ranked" class="${defaultType === 'ranked' ? 'active' : ''}">
              <span style="font-size:20px">🏆</span><b>Ranked</b><small>Counts for rating</small>
            </button>
          </div>
        </div>
        <div class="form-field">
          <label>Players needed</label>
          <select id="ng-max">
            <option value="2">2 (singles)</option>
            <option value="4" selected>4 (doubles)</option>
            <option value="6">6</option>
            <option value="8">8</option>
          </select>
        </div>
      </div>

      <div class="form-field">
        <label>Who can join?</label>
        <div class="type-cards vis-cards" id="ng-vis">
          <button type="button" data-vis="open" class="active"><span style="font-size:19px">🌍</span><b>Anyone</b><small>Nearby players</small></button>
          <button type="button" data-vis="friends"><span style="font-size:19px">🤝</span><b>Friends</b><small>All your friends</small></button>
          <button type="button" data-vis="private"><span style="font-size:19px">🔒</span><b>Specific</b><small>Only who you pick</small></button>
        </div>
        <div id="ng-friends-wrap" class="hidden" style="margin-top:10px">
          ${friends.length
            ? `<div class="invite-chips" id="ng-invites">${friendChips}</div>
               <p class="row-sub" id="ng-invite-hint" style="margin-top:6px">Pick who to invite — only they will see this game.</p>`
            : '<p class="row-sub">Add friends first to invite specific people.</p>'}
        </div>
      </div>

      <label class="row" id="ng-recurring-row" style="margin-bottom:14px;cursor:pointer;gap:10px">
        <input type="checkbox" id="ng-recurring" style="width:18px;height:18px;flex:0 0 auto" />
        <span><span style="font-weight:700">🔁 Repeats weekly</span><br><span class="row-sub">Open-play session — players re-RSVP each week</span></span>
      </label>

      <div class="form-field">
        <input type="text" id="ng-notes" maxlength="200" placeholder="Note (optional) — e.g. All levels welcome!" />
      </div>

      <button class="btn btn-primary btn-block" id="ng-submit" style="padding:15px">
        ${startNow ? 'Start game now' : 'Schedule game'}
      </button>
    `);

    // --- Court picking ---
    const setCourt = (id, name) => {
      modal.querySelector('#ng-court-id').value = id || '';
      modal.querySelector('#ng-court-name').textContent = name || '';
      modal.querySelector('#ng-court-selected').classList.toggle('hidden', !id);
      modal.querySelector('#ng-court-picker').classList.toggle('hidden', !!id);
    };
    modal.querySelector('#ng-court-change').addEventListener('click', () => setCourt(null, null));
    const bindCourtPicks = () => {
      modal.querySelectorAll('[data-pick-court]').forEach((row) => row.addEventListener('click', () => {
        setCourt(row.dataset.pickCourt, row.dataset.pickName);
      }));
    };
    bindCourtPicks();
    let searchTimer;
    modal.querySelector('#ng-court-search').addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      const q = e.target.value.trim();
      searchTimer = setTimeout(async () => {
        const resultsEl = modal.querySelector('#ng-court-results');
        if (q.length < 2) { resultsEl.innerHTML = suggestionRows; bindCourtPicks(); return; }
        let url = `/courts?q=${encodeURIComponent(q)}&limit=6`;
        if (state.userLoc) url += `&lat=${state.userLoc[0]}&lng=${state.userLoc[1]}`;
        try {
          const data = await api(url);
          resultsEl.innerHTML = data.items.map((c) => `
            <button type="button" class="court-suggestion" data-pick-court="${c.id}" data-pick-name="${esc(c.name)}">
              <div class="row-main">
                <div class="row-title" style="font-size:14px">${esc(c.name)}</div>
                <div class="row-sub">${esc(c.city || '')}</div>
              </div>
              ${c.distance_miles != null ? `<span class="tag" style="margin:0">${c.distance_miles} mi</span>` : ''}
            </button>`).join('') || '<div class="empty-state" style="padding:10px">No courts found.</div>';
          bindCourtPicks();
        } catch { /* ignore */ }
      }, 300);
    });

    // --- When ---
    let nowMode = startNow;
    let customMode = false;
    modal.querySelector('#ng-mode').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      nowMode = btn.dataset.mode === 'now';
      modal.querySelectorAll('#ng-mode button').forEach((b) => b.classList.toggle('active', b === btn));
      modal.querySelector('#ng-later-fields').classList.toggle('hidden', nowMode);
      modal.querySelector('#ng-submit').textContent = nowMode ? 'Start game now' : 'Schedule game';
    });
    modal.querySelector('#ng-days').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      selDayIdx = Number(btn.dataset.day);
      modal.querySelectorAll('#ng-days button').forEach((b) => b.classList.toggle('active', b === btn));
    });
    modal.querySelector('#ng-hours').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      modal.querySelectorAll('#ng-hours button').forEach((b) => b.classList.toggle('active', b === btn));
      if (btn.dataset.hour === 'custom') {
        customMode = true;
        const whenEl = modal.querySelector('#ng-when');
        whenEl.classList.remove('hidden');
        if (!whenEl.value) {
          const d = new Date(days[selDayIdx]); d.setHours(selHour || 18);
          const pad2 = (n) => String(n).padStart(2, '0');
          whenEl.value = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:00`;
        }
      } else {
        customMode = false;
        selHour = Number(btn.dataset.hour);
        modal.querySelector('#ng-when').classList.add('hidden');
      }
    });

    // --- Type ---
    let gameType = defaultType;
    const recurringRow = modal.querySelector('#ng-recurring-row');
    const recurringBox = modal.querySelector('#ng-recurring');
    const syncRecurring = () => {
      // Recurring weekly sessions are open-play only (ranked games don't repeat).
      const isRanked = gameType === 'ranked';
      recurringRow.classList.toggle('hidden', isRanked);
      if (isRanked) recurringBox.checked = false;
    };
    syncRecurring();
    modal.querySelector('#ng-type').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      gameType = btn.dataset.val;
      modal.querySelectorAll('#ng-type button').forEach((b) => b.classList.toggle('active', b === btn));
      syncRecurring();
    });

    // --- Visibility / invites ---
    let visibility = 'open';
    const inviteIds = new Set();
    const friendsWrap = modal.querySelector('#ng-friends-wrap');
    modal.querySelector('#ng-vis').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      visibility = btn.dataset.vis;
      modal.querySelectorAll('#ng-vis button').forEach((b) => b.classList.toggle('active', b === btn));
      friendsWrap.classList.toggle('hidden', visibility !== 'private');
    });
    const invitesEl = modal.querySelector('#ng-invites');
    if (invitesEl) {
      invitesEl.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-fid]');
        if (!btn) return;
        const fid = Number(btn.dataset.fid);
        if (inviteIds.has(fid)) inviteIds.delete(fid); else inviteIds.add(fid);
        btn.classList.toggle('active', inviteIds.has(fid));
        modal.querySelector('#ng-invite-hint').textContent = inviteIds.size
          ? `${inviteIds.size} invited — only they will see this game.`
          : 'Pick who to invite — only they will see this game.';
      });
    }

    // --- Submit ---
    modal.querySelector('#ng-submit').addEventListener('click', async (e) => {
      const courtId = modal.querySelector('#ng-court-id').value;
      if (!courtId) { toast('Pick a court first'); return; }
      let scheduledAt;
      if (nowMode) {
        scheduledAt = new Date();
      } else if (customMode) {
        const v = modal.querySelector('#ng-when').value;
        if (!v) { toast('Pick a date and time'); return; }
        scheduledAt = new Date(v);
      } else {
        if (selHour == null) { toast('Pick a time'); return; }
        scheduledAt = new Date(days[selDayIdx]);
        scheduledAt.setHours(selHour, 0, 0, 0);
        if (scheduledAt.getTime() < Date.now() - 10 * 60000) { toast('That time already passed today'); return; }
      }
      if (visibility === 'private' && inviteIds.size === 0) {
        toast('Pick at least one person to invite');
        return;
      }
      const btn = e.currentTarget;
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        await api('/games', {
          method: 'POST',
          body: JSON.stringify({
            court_id: Number(courtId),
            scheduled_at: scheduledAt.toISOString(),
            game_type: gameType,
            visibility,
            recurrence: recurringBox.checked ? 'weekly' : 'none',
            max_players: Number(modal.querySelector('#ng-max').value),
            notes: modal.querySelector('#ng-notes').value.trim(),
            invite_user_ids: visibility === 'private' ? [...inviteIds] : [],
          }),
        });
        closeModal(modal);
        toast(nowMode ? "Game on! It's live in My games 🎾" : 'Game scheduled! 🎾');
        if (state.tab === 'play') { state.playSeg = 'games'; renderPlay(); }
        document.querySelectorAll('#play-segments button').forEach((b) => b.classList.toggle('active', b.dataset.seg === state.playSeg));
        refreshMe();
      } catch (err) { toast(err.message); btn.disabled = false; }
    });
  }

  function openScoreModal(game, refresh) {
    const players = game.players;
    const singles = players.length === 2;
    // Default split: first half team 1, second half team 2 (tap a chip to flip it)
    const teams = {};
    const half = Math.ceil(players.length / 2);
    players.forEach((p, i) => { teams[p.user_id] = i < half ? 1 : 2; });

    const court = game.court || {};
    const modal = openModal(`
      <div class="modal-head">
        <div style="flex:1">
          <h3>${game.game_type === 'ranked' ? '🏆 Record ranked score' : '🎾 Record score'}</h3>
          <div class="row-sub">${esc(court.name || '')}</div>
        </div>
        <button class="modal-close" aria-label="Close">✕</button>
      </div>
      ${singles ? '' : '<p class="row-sub" style="margin-bottom:8px">Tap a player to switch their team.</p>'}
      <div id="sc-chips" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:6px"></div>
      <div id="sc-uneven" class="row-sub" style="color:#b45309;font-weight:700;margin-bottom:10px;display:none"></div>
      <div class="score-grid">
        <div class="score-panel" id="sc-panel-1">
          <div class="score-team-label" id="sc-label-1"></div>
          <div class="score-stepper">
            <button type="button" data-step="-1" data-target="sc-1" aria-label="Decrease team 1 score">−</button>
            <input type="number" id="sc-1" min="0" max="99" value="11" inputmode="numeric" />
            <button type="button" data-step="1" data-target="sc-1" aria-label="Increase team 1 score">＋</button>
          </div>
        </div>
        <div class="score-vs">vs</div>
        <div class="score-panel" id="sc-panel-2">
          <div class="score-team-label" id="sc-label-2"></div>
          <div class="score-stepper">
            <button type="button" data-step="-1" data-target="sc-2" aria-label="Decrease team 2 score">−</button>
            <input type="number" id="sc-2" min="0" max="99" value="9" inputmode="numeric" />
            <button type="button" data-step="1" data-target="sc-2" aria-label="Increase team 2 score">＋</button>
          </div>
        </div>
      </div>
      ${game.game_type === 'ranked' ? '<p class="row-sub" style="margin:10px 0 12px;text-align:center">An opponent confirms the score, then ratings update.</p>' : '<div style="height:12px"></div>'}
      <button class="btn btn-primary btn-block" id="sc-submit" style="padding:15px">
        ${game.game_type === 'ranked' ? 'Send for confirmation' : 'Save result'}
      </button>
    `);

    const renderChips = () => {
      modal.querySelector('#sc-chips').innerHTML = players.map((p) => `
        <button type="button" class="team-chip team-${teams[p.user_id]}" data-chip="${p.user_id}" ${singles ? 'disabled' : ''}>
          ${avatarHtml(p, 'sm')} ${esc(p.display_name)}
        </button>`).join('');
      if (!singles) {
        modal.querySelectorAll('[data-chip]').forEach((chip) => chip.addEventListener('click', () => {
          const uid = Number(chip.dataset.chip);
          teams[uid] = teams[uid] === 1 ? 2 : 1;
          renderChips();
          renderLabels();
        }));
      }
    };
    const teamNames = (t) => players.filter((p) => teams[p.user_id] === t).map((p) => esc(p.display_name.split(' ')[0])).join(' & ') || '—';
    const renderLabels = () => {
      modal.querySelector('#sc-label-1').innerHTML = singles ? esc(players[0].display_name) : teamNames(1);
      modal.querySelector('#sc-label-2').innerHTML = singles ? esc(players[1].display_name) : teamNames(2);
      const n1 = players.filter((p) => teams[p.user_id] === 1).length;
      const n2 = players.filter((p) => teams[p.user_id] === 2).length;
      const uneven = modal.querySelector('#sc-uneven');
      const lopsided = !singles && n1 && n2 && Math.abs(n1 - n2) > 1;
      uneven.style.display = lopsided ? '' : 'none';
      if (lopsided) uneven.textContent = `⚠️ Uneven teams (${n1} v ${n2}) — tap players above to rebalance`;
    };
    const highlightWinner = () => {
      const s1 = Number(modal.querySelector('#sc-1').value);
      const s2 = Number(modal.querySelector('#sc-2').value);
      modal.querySelector('#sc-panel-1').classList.toggle('winning', s1 > s2);
      modal.querySelector('#sc-panel-2').classList.toggle('winning', s2 > s1);
    };
    renderChips();
    renderLabels();
    highlightWinner();

    // If someone else reports a score (or the game changes) while this is open,
    // swap to the game screen instead of letting a stale submission overwrite it.
    const originalStatus = game.status;
    const scorePoll = setInterval(async () => {
      if (!document.body.contains(modal)) { clearInterval(scorePoll); return; }
      try {
        const fresh = await api(`/games/${game.id}`);
        const someoneElseReported = fresh.score_submitted_by && fresh.score_submitted_by !== state.me.id
          && fresh.status === 'awaiting_confirmation' && originalStatus !== 'awaiting_confirmation';
        if (fresh.status !== originalStatus || someoneElseReported) {
          clearInterval(scorePoll);
          closeModal(modal);
          toast(`⚡ ${fresh.score_submitted_by_name || 'Your opponent'} already reported a score`);
          refreshMe();
          openGameScreen(game.id);
        }
      } catch { /* offline */ }
    }, 5000);

    modal.querySelectorAll('[data-step]').forEach((btn) => btn.addEventListener('click', () => {
      const input = modal.querySelector(`#${btn.dataset.target}`);
      input.value = Math.max(0, Math.min(99, Number(input.value || 0) + Number(btn.dataset.step)));
      highlightWinner();
    }));
    modal.querySelectorAll('#sc-1, #sc-2').forEach((inp) => inp.addEventListener('input', highlightWinner));

    modal.querySelector('#sc-submit').addEventListener('click', async (e) => {
      const team1 = players.filter((p) => teams[p.user_id] === 1).map((p) => p.user_id);
      const team2 = players.filter((p) => teams[p.user_id] === 2).map((p) => p.user_id);
      if (!team1.length || !team2.length) { toast('Each side needs at least one player'); return; }
      const s1 = Number(modal.querySelector('#sc-1').value);
      const s2 = Number(modal.querySelector('#sc-2').value);
      if (s1 === s2) { toast('Pickleball has no ties — adjust the score'); return; }
      const btn = e.currentTarget;
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        const updated = await api(`/games/${game.id}/complete`, {
          method: 'POST',
          body: JSON.stringify({ team1, team2, score_team1: s1, score_team2: s2 }),
        });
        closeModal(modal);
        if (updated.status === 'awaiting_confirmation') {
          toast('Score sent — waiting for your opponent to confirm ✅');
        } else {
          showCelebration(updated);
        }
        refreshMe();
        refresh();
      } catch (err) { toast(err.message); btn.disabled = false; }
    });
  }

  // ---------- Chat & Friends ----------
  function setupChat() {
    $('#chat-segments').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      state.chatSeg = btn.dataset.seg;
      document.querySelectorAll('#chat-segments button').forEach((b) => b.classList.toggle('active', b === btn));
      renderChat();
    });
  }

  async function renderChat() {
    const el = $('#chat-content');
    el.innerHTML = skeletonHtml(5);
    try {
      if (state.chatSeg === 'chats') {
        const data = await api('/chat');
        el.innerHTML = data.items.length
          ? data.items.map((c) => `
              <div class="card row" data-thread="${c.user.id}" style="cursor:pointer">
                ${avatarHtml(c.user)}
                <div class="row-main">
                  <div class="row-title">${esc(c.user.display_name)}</div>
                  <div class="row-sub">${c.last_message.sender_id === state.me.id ? 'You: ' : ''}${esc(c.last_message.body.slice(0, 60))}</div>
                </div>
                ${c.unread ? `<span class="badge" style="position:static">${c.unread}</span>` : `<span class="row-sub">${fmtTimeShort(c.last_message.created_at)}</span>`}
              </div>`).join('')
          : '<div class="empty-state"><span class="big">💬</span>No chats yet.<br>Add some friends and say hi!<br><button class="btn btn-primary" data-goto="chat-friends" style="margin-top:10px">🤝 Find friends</button></div>';
        el.querySelectorAll('[data-thread]').forEach((row) => row.addEventListener('click', () => openThread(Number(row.dataset.thread))));
      } else if (state.chatSeg === 'nearby') {
        await renderNearbyPlayers(el);
      } else {
        await renderFriends(el);
      }
    } catch (e) {
      renderError(el, e.message, renderChat);
    }
  }

  async function renderNearbyPlayers(el) {
    const loc = areaLatLng();
    const skill = state.nearbySkill || '';
    let data;
    try {
      data = await api(`/players/nearby?lat=${loc.lat}&lng=${loc.lng}&radius=50${skill ? `&skill=${skill}` : ''}`);
    } catch (e) { el.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; return; }

    const skills = [['', 'All levels'], ['beginner', 'Beginner'], ['intermediate', 'Intermediate'], ['advanced', 'Advanced'], ['pro', 'Pro']];
    let html = `
      <div class="form-field" style="margin-top:4px">
        <div class="quick-times" id="nearby-skill">
          ${skills.map(([v, label]) => `<button type="button" data-skill="${v}" class="${v === skill ? 'active' : ''}">${label}</button>`).join('')}
        </div>
      </div>`;

    html += data.items.length
      ? data.items.map((p) => {
          let action;
          if (p.is_friend) action = '<span class="tag" style="margin:0">Friends ✓</span>';
          else if (p.friendship_status === 'pending') action = p.outgoing
            ? '<span class="tag" style="margin:0">Pending</span>'
            : `<button class="btn btn-primary btn-sm" data-respond-inline="${p.friendship_id}">Accept</button>`;
          else action = `<button class="btn btn-primary btn-sm" data-add-friend="${p.id}">＋ Add</button>`;
          let sub = p.checked_in_court
            ? `📍 At ${esc(p.checked_in_court.name)}${p.checked_in_court.looking_for_game ? ' · <b style="color:var(--green-700)">wants to play!</b>' : ''}`
            : `${skillLabel(p.skill_level)} · ${p.rating} · ${p.distance_miles} mi away`;
          if (availabilityOverlap(state.me.availability, p.availability)) {
            sub += ' · <b style="color:var(--green-700)">⏰ plays your times</b>';
          }
          return `
            <div class="card row">
              <div data-view-user="${p.id}" style="cursor:pointer">${avatarHtml(p)}</div>
              <div class="row-main" data-view-user="${p.id}" style="cursor:pointer">
                <div class="row-title">${esc(p.display_name)}${p.current_streak >= 2 ? ' 🔥' : ''}</div>
                <div class="row-sub">${sub}</div>
              </div>
              <button class="btn btn-secondary btn-sm" data-msg="${p.id}">💬</button>
              ${action}
            </div>`;
        }).join('')
      : '<div class="empty-state"><span class="big">📍</span>No players near you yet.<br>Check in at a court so others can find you!<br><button class="btn btn-primary" data-goto="courts" style="margin-top:10px">🗺 Browse courts</button></div>';

    el.innerHTML = html;
    el.querySelector('#nearby-skill').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      state.nearbySkill = btn.dataset.skill;
      renderChat();
    });
    el.querySelectorAll('[data-msg]').forEach((b) => b.addEventListener('click', () => openThread(Number(b.dataset.msg))));
    el.querySelectorAll('[data-add-friend]').forEach((b) => b.addEventListener('click', async () => {
      try { await api('/friends/request', { method: 'POST', body: JSON.stringify({ user_id: Number(b.dataset.addFriend) }) }); toast('Friend request sent!'); renderChat(); }
      catch (e) { toast(e.message); }
    }));
    el.querySelectorAll('[data-respond-inline]').forEach((b) => b.addEventListener('click', async () => {
      try { await api(`/friends/${b.dataset.respondInline}/respond`, { method: 'POST', body: JSON.stringify({ accept: true }) }); toast('Friend added! 🎉'); refreshMe(); renderChat(); }
      catch (e) { toast(e.message); }
    }));
    bindUserButtons(el);
  }

  async function renderFriends(el) {
    const loc = areaLatLng();
    const [data, results] = await Promise.all([
      api('/friends'),
      api(`/games/results?lat=${loc.lat}&lng=${loc.lng}`).catch(() => ({ items: [] })),
    ]);
    let html = `
      <div class="form-field" style="margin-top:4px">
        <input type="search" id="friend-search" placeholder="Find players by name or email…" />
        <div id="friend-search-results"></div>
      </div>`;

    if (data.incoming.length) {
      html += '<div class="section-label">Friend requests</div>';
      html += data.incoming.map((f) => `
        <div class="card row">
          ${avatarHtml(f)}
          <div class="row-main">
            <div class="row-title">${esc(f.display_name)}</div>
            <div class="row-sub">${skillLabel(f.skill_level)} · ${f.rating}</div>
          </div>
          <button class="btn btn-primary btn-sm" data-respond="${f.friendship_id}" data-accept="1">Accept</button>
          <button class="btn btn-secondary btn-sm" data-respond="${f.friendship_id}" data-accept="0">✕</button>
        </div>`).join('');
    }

    html += `<div class="section-label">Friends (${data.friends.length})</div>`;
    html += data.friends.length
      ? data.friends.map((f) => `
          <div class="card row">
            ${avatarHtml(f)}
            <div class="row-main" data-view-user="${f.id}" style="cursor:pointer">
              <div class="row-title">${esc(f.display_name)}</div>
              <div class="row-sub">${f.checked_in_court
                ? `📍 At ${esc(f.checked_in_court.name)}${f.checked_in_court.looking_for_game ? ' · <b style="color:var(--green-700)">wants to play!</b>' : ''}`
                : `${skillLabel(f.skill_level)} · ${f.rating}`}</div>
            </div>
            <button class="btn btn-secondary btn-sm" data-invite="${f.id}" data-invite-court="${f.checked_in_court ? f.checked_in_court.id : ''}" data-invite-court-name="${f.checked_in_court ? esc(f.checked_in_court.name) : ''}" title="Schedule a game">🎾</button>
            <button class="btn btn-secondary btn-sm" data-msg="${f.id}">💬</button>
          </div>`).join('')
      : '<div class="empty-state" style="padding:18px">No friends yet — search above to find players.</div>';

    if (data.outgoing.length) {
      html += '<div class="section-label">Sent requests</div>';
      html += data.outgoing.map((f) => `
        <div class="card row">
          ${avatarHtml(f)}
          <div class="row-main"><div class="row-title">${esc(f.display_name)}</div><div class="row-sub">Pending…</div></div>
        </div>`).join('');
    }

    // What your friends have been playing — results you weren't part of.
    const friendResults = (results.items || []).filter((g) => g.involves_friend && !g.involves_me).slice(0, 5);
    if (friendResults.length) {
      html += '<div class="section-label">🏆 Friend results</div>';
      html += friendResults.map(resultRowHtml).join('');
    }

    el.innerHTML = html;
    bindGameButtons(el, () => renderChat());

    el.querySelectorAll('[data-respond]').forEach((b) => b.addEventListener('click', async () => {
      try {
        await api(`/friends/${b.dataset.respond}/respond`, {
          method: 'POST',
          body: JSON.stringify({ accept: b.dataset.accept === '1' }),
        });
        toast(b.dataset.accept === '1' ? 'Friend added! 🎉' : 'Request declined');
        refreshMe();
        renderChat();
      } catch (e) { toast(e.message); }
    }));
    el.querySelectorAll('[data-msg]').forEach((b) => b.addEventListener('click', () => openThread(Number(b.dataset.msg))));
    el.querySelectorAll('[data-invite]').forEach((b) => b.addEventListener('click', () => {
      const court = b.dataset.inviteCourt
        ? { id: Number(b.dataset.inviteCourt), name: b.dataset.inviteCourtName }
        : null;
      openNewGameModal(court, 'casual');
      toast('Schedule it — your friends get notified 🔔');
    }));
    bindUserButtons(el);

    let timer;
    const search = el.querySelector('#friend-search');
    search.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = search.value.trim();
        const resultsEl = el.querySelector('#friend-search-results');
        if (q.length < 2) { resultsEl.innerHTML = ''; return; }
        const data = await api(`/users/search?q=${encodeURIComponent(q)}`);
        resultsEl.innerHTML = data.items.map((u) => {
          let action;
          if (u.friendship_status === 'accepted') action = '<span class="tag" style="margin:0">Friends ✓</span>';
          else if (u.friendship_status === 'pending') action = u.outgoing ? '<span class="tag" style="margin:0">Pending</span>' : `<button class="btn btn-primary btn-sm" data-respond-inline="${u.friendship_id}">Accept</button>`;
          else action = `<button class="btn btn-primary btn-sm" data-add-friend="${u.id}">＋ Add</button>`;
          return `
            <div class="card row" style="margin:8px 0">
              ${avatarHtml(u)}
              <div class="row-main">
                <div class="row-title">${esc(u.display_name)}</div>
                <div class="row-sub">${skillLabel(u.skill_level)} · ${u.rating}</div>
              </div>
              ${action}
            </div>`;
        }).join('') || '<div class="empty-state" style="padding:12px">No players found.</div>';

        resultsEl.querySelectorAll('[data-add-friend]').forEach((b) => b.addEventListener('click', async () => {
          try {
            await api('/friends/request', { method: 'POST', body: JSON.stringify({ user_id: Number(b.dataset.addFriend) }) });
            toast('Friend request sent!');
            renderChat();
          } catch (e) { toast(e.message); }
        }));
        resultsEl.querySelectorAll('[data-respond-inline]').forEach((b) => b.addEventListener('click', async () => {
          try {
            await api(`/friends/${b.dataset.respondInline}/respond`, { method: 'POST', body: JSON.stringify({ accept: true }) });
            toast('Friend added! 🎉');
            renderChat();
          } catch (e) { toast(e.message); }
        }));
      }, 300);
    });
  }

  async function openThread(userId) {
    state.activeThreadUserId = userId;
    let data;
    try { data = await api(`/chat/${userId}`); } catch (e) { toast(e.message); return; }

    const modal = openModal(`
      <div class="thread">
        <div class="thread-head">
          <button class="modal-close" style="font-size:18px">‹</button>
          ${avatarHtml(data.user, 'sm')}
          <div class="row-main">
            <div class="row-title">${esc(data.user.display_name)}</div>
            <div class="row-sub">${skillLabel(data.user.skill_level)} · ${data.user.rating}</div>
          </div>
        </div>
        <div class="thread-msgs" id="thread-msgs"></div>
        <form class="thread-input" id="thread-form">
          <input type="text" id="thread-text" placeholder="Message…" autocomplete="off" />
          <button type="submit" aria-label="Send">➤</button>
        </form>
      </div>
    `, { chat: true });

    const msgsEl = modal.querySelector('#thread-msgs');
    let lastId = 0;
    const renderMsgs = (items, append) => {
      const html = items.map((m) => `
        <div class="bubble ${m.sender_id === state.me.id ? 'me' : 'them'}">
          ${esc(m.body)}
          <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
        </div>`).join('');
      if (append && !msgsEl.querySelector('.empty-state')) msgsEl.insertAdjacentHTML('beforeend', html);
      else if (append) msgsEl.innerHTML = html;
      else msgsEl.innerHTML = html || '<div class="empty-state" style="padding:20px">Say hi! 👋</div>';
      if (items.length) lastId = items[items.length - 1].id;
      msgsEl.scrollTop = msgsEl.scrollHeight;
    };
    renderMsgs(data.items, false);
    attachChatViewport(modal, msgsEl, modal.querySelector('#thread-text'));
    refreshMe();

    clearInterval(state.threadPollTimer);
    state.threadPollTimer = setInterval(async () => {
      if (!document.body.contains(msgsEl)) { clearInterval(state.threadPollTimer); return; }
      try {
        const fresh = await api(`/chat/${userId}?since_id=${lastId}`);
        if (fresh.items.length) renderMsgs(fresh.items, true);
      } catch { /* offline */ }
    }, 4000);

    modal.querySelector('#thread-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const input = modal.querySelector('#thread-text');
      const body = input.value.trim();
      if (!body) return;
      input.value = '';
      try {
        const msg = await api(`/chat/${userId}`, { method: 'POST', body: JSON.stringify({ body }) });
        renderMsgs([msg], true);
      } catch (err) { toast(err.message); input.value = body; } // don't lose the draft
    });
  }

  async function openCourtChat(court) {
    let data;
    try { data = await api(`/courts/${court.id}/chat`); } catch (e) { toast(e.message); return; }

    const modal = openModal(`
      <div class="thread">
        <div class="thread-head">
          <button class="modal-close" style="font-size:18px">‹</button>
          <span style="font-size:22px">🏟</span>
          <div class="row-main">
            <div class="row-title">${esc(court.name)}</div>
            <div class="row-sub">Court chat — everyone at this court can read it</div>
          </div>
        </div>
        <div class="thread-msgs" id="cc-msgs"></div>
        <form class="thread-input" id="cc-form">
          <input type="text" id="cc-text" placeholder="Message the court…" autocomplete="off" maxlength="500" />
          <button type="submit" aria-label="Send">➤</button>
        </form>
      </div>
    `, { chat: true });

    const msgsEl = modal.querySelector('#cc-msgs');
    let lastId = 0;
    const renderMsgs = (items, append) => {
      const html = items.map((m) => {
        const mine = m.sender_id === state.me.id;
        return `
        <div style="display:flex;gap:8px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%">
          ${mine ? '' : `<div class="avatar sm" style="background:${esc(m.sender_color)}">${esc(initials(m.sender_name))}</div>`}
          <div class="bubble ${mine ? 'me' : 'them'}" style="max-width:100%">
            ${mine ? '' : `<div style="font-size:11px;font-weight:700;opacity:.75;margin-bottom:2px">${esc(m.sender_name)}</div>`}
            ${esc(m.body)}
            <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
          </div>
        </div>`;
      }).join('');
      if (append && !msgsEl.querySelector('.empty-state')) msgsEl.insertAdjacentHTML('beforeend', html);
      else if (append) msgsEl.innerHTML = html;
      else msgsEl.innerHTML = html || '<div class="empty-state" style="padding:20px">No messages yet — say hi to the court! 👋</div>';
      if (items.length) lastId = items[items.length - 1].id;
      msgsEl.scrollTop = msgsEl.scrollHeight;
    };
    renderMsgs(data.items, false);
    attachChatViewport(modal, msgsEl, modal.querySelector('#cc-text'));

    const pollTimer = setInterval(async () => {
      if (!document.body.contains(msgsEl)) { clearInterval(pollTimer); return; }
      try {
        const fresh = await api(`/courts/${court.id}/chat?since_id=${lastId}`);
        if (fresh.items.length) renderMsgs(fresh.items, true);
      } catch { /* offline */ }
    }, 5000);

    modal.querySelector('#cc-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const input = modal.querySelector('#cc-text');
      const body = input.value.trim();
      if (!body) return;
      input.value = '';
      try {
        const msg = await api(`/courts/${court.id}/chat`, { method: 'POST', body: JSON.stringify({ body }) });
        renderMsgs([msg], true);
      } catch (err) { toast(err.message); input.value = body; } // don't lose the draft
    });
  }

  async function openCourtGallery(court, uploadFn) {
    let data;
    try { data = await api(`/courts/${court.id}/photos`); } catch (e) { toast(e.message); return; }
    const modal = openModal(`
      ${modalHead(`📷 ${esc(court.name)}`)}
      <div class="gallery-scroll">
        ${data.items.map((p) => `
          <figure class="gallery-item">
            <img src="${esc(p.url)}" alt="Photo of ${esc(court.name)}" loading="lazy" />
            <figcaption class="row-sub">by ${esc(p.user_name)} · ${resultDayLabel(p.created_at)}</figcaption>
          </figure>`).join('')}
      </div>
      <button class="btn btn-secondary btn-block" id="gal-add" style="margin-top:12px">📷 Add your photo</button>
    `);
    modal.querySelector('#gal-add').addEventListener('click', () => {
      if (uploadFn) uploadFn(() => { closeModal(modal); openCourtGallery(court, uploadFn); });
    });
  }

  async function openGameChat(game) {
    let data;
    try { data = await api(`/games/${game.id}/chat`); } catch (e) { toast(e.message); return; }

    const modal = openModal(`
      <div class="thread">
        <div class="thread-head">
          <button class="modal-close" style="font-size:18px">‹</button>
          <span style="font-size:22px">🎾</span>
          <div class="row-main">
            <div class="row-title">Game chat</div>
            <div class="row-sub">${esc(data.game.court_name)} — only players in this game can read it</div>
          </div>
        </div>
        <div class="thread-msgs" id="gc-msgs"></div>
        <form class="thread-input" id="gc-form">
          <input type="text" id="gc-text" placeholder="Message your game…" autocomplete="off" maxlength="500" />
          <button type="submit" aria-label="Send">➤</button>
        </form>
      </div>
    `, { chat: true });

    const msgsEl = modal.querySelector('#gc-msgs');
    let lastId = 0;
    const renderMsgs = (items, append) => {
      const html = items.map((m) => {
        const mine = m.sender_id === state.me.id;
        return `
        <div style="display:flex;gap:8px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%">
          ${mine ? '' : `<div class="avatar sm" style="background:${esc(m.sender_color)}">${esc(initials(m.sender_name))}</div>`}
          <div class="bubble ${mine ? 'me' : 'them'}" style="max-width:100%">
            ${mine ? '' : `<div style="font-size:11px;font-weight:700;opacity:.75;margin-bottom:2px">${esc(m.sender_name)}</div>`}
            ${esc(m.body)}
            <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
          </div>
        </div>`;
      }).join('');
      if (append && !msgsEl.querySelector('.empty-state')) msgsEl.insertAdjacentHTML('beforeend', html);
      else if (append) msgsEl.innerHTML = html;
      else msgsEl.innerHTML = html || '<div class="empty-state" style="padding:20px">Coordinate with your game — “running late”, “bringing balls” 🎾</div>';
      if (items.length) lastId = items[items.length - 1].id;
      msgsEl.scrollTop = msgsEl.scrollHeight;
    };
    renderMsgs(data.items, false);
    attachChatViewport(modal, msgsEl, modal.querySelector('#gc-text'));

    const pollTimer = setInterval(async () => {
      if (!document.body.contains(msgsEl)) { clearInterval(pollTimer); return; }
      try {
        const fresh = await api(`/games/${game.id}/chat?since_id=${lastId}`);
        if (fresh.items.length) renderMsgs(fresh.items, true);
      } catch { /* offline */ }
    }, 5000);

    modal.querySelector('#gc-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const input = modal.querySelector('#gc-text');
      const body = input.value.trim();
      if (!body) return;
      input.value = '';
      try {
        const msg = await api(`/games/${game.id}/chat`, { method: 'POST', body: JSON.stringify({ body }) });
        renderMsgs([msg], true);
      } catch (err) { toast(err.message); input.value = body; } // don't lose the draft
    });
  }

  // ---------- User profile ----------
  async function openUserProfile(userId) {
    let user;
    try { user = await api(`/users/${userId}`); } catch (e) { toast(e.message); return; }

    let friendAction = '';
    if (userId !== state.me.id) {
      if (user.is_blocked) {
        friendAction = '<span class="tag warn" style="margin:0">🚫 Blocked</span>';
      } else if (user.friendship_status === 'accepted') {
        friendAction = `<button class="btn btn-secondary" id="up-msg">💬 Message</button>
          <button class="btn btn-danger" id="up-remove">Remove friend</button>`;
      } else if (user.friendship_status === 'pending') {
        friendAction = user.outgoing
          ? '<span class="tag" style="margin:0">Request pending…</span>'
          : `<button class="btn btn-primary" id="up-accept">Accept friend request</button>`;
      } else {
        friendAction = `<button class="btn btn-primary" id="up-add">＋ Add friend</button>
          <button class="btn btn-secondary" id="up-msg">💬 Message</button>`;
      }
    }

    const games = user.recent_games || [];
    const upcoming = user.upcoming_games || [];
    const courts = user.courts || [];
    let h2hHtml = '';
    if (userId !== state.me.id && user.head_to_head) {
      const { wins, losses } = user.head_to_head;
      const firstName = (user.display_name || 'They').split(' ')[0];
      const line = wins > losses ? `You lead ${wins}–${losses}`
        : losses > wins ? `${esc(firstName)} leads ${losses}–${wins}`
        : `Tied ${wins}–${wins}`;
      h2hHtml = `
        <div class="card" style="text-align:center;padding:10px 14px;margin:12px 0 0">
          <div style="font-weight:800">🎯 ${line}</div>
          <div class="row-sub">your head-to-head record</div>
        </div>`;
    }
    const availLines = availabilitySummary(user.availability);
    const availHtml = availLines.length ? `
      <div class="card" style="padding:10px 14px;margin:12px 0 0">
        <div class="row-sub" style="font-weight:800;color:var(--ink)">⏰ Usually plays${
          userId !== state.me.id && availabilityOverlap(state.me.availability, user.availability)
            ? ' <span class="tag" style="margin:0 0 0 6px">🤝 your times too</span>' : ''}</div>
        ${availLines.map((l) => `<div class="row-sub">${l}</div>`).join('')}
      </div>` : '';
    const courtRow = (c) => `
      <div class="card row" data-pcourt="${c.id}" style="cursor:pointer">
        <span style="font-size:18px">${c.is_home ? '🏠' : '⭐'}</span>
        <div class="row-main">
          <div class="row-title" style="font-size:14px">${esc(c.name)}</div>
          <div class="row-sub">${esc(c.city || '')}${c.is_home ? ' · Home court' : ''}</div>
        </div>
        <span class="chev">›</span>
      </div>`;
    const modal = openModal(`
      ${modalHead('')}
      <div class="profile-hero">
        ${avatarHtml(user)}
        <div class="profile-name">${esc(user.display_name)}</div>
        <div class="profile-sub">${skillLabel(user.skill_level)}${user.home_court_name ? ` · 🏠 ${esc(user.home_court_name)}` : ''}</div>
        ${user.bio ? `<p class="profile-sub" style="margin-top:8px">${esc(user.bio)}</p>` : ''}
      </div>
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">${user.rating}</div><div class="stat-label">Rating</div></div>
        <div class="stat-card"><div class="stat-value">${user.ranked_wins}</div><div class="stat-label">Wins</div></div>
        <div class="stat-card"><div class="stat-value">${user.ranked_losses}</div><div class="stat-label">Losses</div></div>
      </div>
      ${h2hHtml}
      ${availHtml}
      <div class="action-row">${friendAction}</div>
      ${upcoming.length ? `<div class="section-label">Upcoming games</div>${upcoming.map((g) => gameCardHtml(g, { compact: true })).join('')}` : ''}
      ${courts.length ? `<div class="section-label">Courts</div>${courts.map(courtRow).join('')}` : ''}
      ${games.length ? `<div class="section-label">Recent games</div>${games.map((g) => gameCardHtml(g, { compact: true })).join('')}` : ''}
      ${userId !== state.me.id ? `
        <div style="text-align:center;margin-top:18px">
          <button id="up-block" style="background:transparent;color:${user.is_blocked ? 'var(--ink-soft)' : '#e03131'};font-size:13px;font-weight:600">
            ${user.is_blocked ? 'Unblock user' : '🚫 Block user'}
          </button>
        </div>` : ''}
    `);

    bindGameButtons(modal, () => { closeModal(modal); openUserProfile(userId); });
    modal.querySelectorAll('[data-pcourt]').forEach((row) => row.addEventListener('click', () => {
      closeModal(modal);
      openCourtDetail(Number(row.dataset.pcourt));
    }));

    modal.querySelector('#up-block')?.addEventListener('click', async () => {
      if (user.is_blocked) {
        try {
          await api(`/users/${userId}/unblock`, { method: 'POST' });
          toast('User unblocked');
          closeModal(modal);
          openUserProfile(userId);
        } catch (e) { toast(e.message); }
        return;
      }
      if (!window.confirm(`Block ${user.display_name}? You won't see each other in search or nearby players, and you can't message each other.`)) return;
      try {
        await api(`/users/${userId}/block`, { method: 'POST' });
        toast('User blocked 🚫');
        closeModal(modal);
      } catch (e) { toast(e.message); }
    });

    modal.querySelector('#up-add')?.addEventListener('click', async () => {
      try {
        await api('/friends/request', { method: 'POST', body: JSON.stringify({ user_id: userId }) });
        toast('Friend request sent!');
        closeModal(modal);
      } catch (e) { toast(e.message); }
    });
    modal.querySelector('#up-accept')?.addEventListener('click', async () => {
      try {
        await api(`/friends/${user.friendship_id}/respond`, { method: 'POST', body: JSON.stringify({ accept: true }) });
        toast('Friend added! 🎉');
        closeModal(modal);
        refreshMe();
      } catch (e) { toast(e.message); }
    });
    modal.querySelector('#up-remove')?.addEventListener('click', async () => {
      if (!confirm(`Remove ${user.display_name} as a friend?`)) return;
      try {
        await api(`/friends/${user.friendship_id}`, { method: 'DELETE' });
        toast('Friend removed');
        closeModal(modal);
      } catch (e) { toast(e.message); }
    });
    modal.querySelector('#up-msg')?.addEventListener('click', () => {
      closeModal(modal);
      openThread(userId);
    });
  }

  // ---------- My profile tab ----------
  async function renderProfile() {
    const el = $('#profile-content');
    const me = state.me;
    if (!me) return;
    const total = me.ranked_wins + me.ranked_losses;
    const winPct = total ? Math.round((me.ranked_wins / total) * 100) : 0;

    el.innerHTML = `
      <div class="profile-hero">
        ${avatarHtml(me)}
        <div class="profile-name">${esc(me.display_name)}</div>
        <div class="profile-sub">${skillLabel(me.skill_level)}${me.home_court_name ? ` · 🏠 ${esc(me.home_court_name)}` : ''}</div>
        ${me.bio ? `<p class="profile-sub" style="margin-top:8px">${esc(me.bio)}</p>` : ''}
      </div>
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">${me.rating}</div><div class="stat-label">Rating</div></div>
        <div class="stat-card"><div class="stat-value">${me.ranked_wins}–${me.ranked_losses}</div><div class="stat-label">Record · ${winPct}%</div></div>
        <div class="stat-card"><div class="stat-value">${me.current_streak >= 2 ? '🔥' : ''}${me.current_streak}</div><div class="stat-label">Streak · best ${me.best_streak}</div></div>
      </div>
      <div id="pf-play-stats"></div>
      ${state.presence && state.presence.checked_in ? `
        <div class="card row">
          <span style="font-size:22px">📍</span>
          <div class="row-main">
            <div class="row-title">Checked in at ${esc(state.presence.court_name)}</div>
            <div class="row-sub">${state.presence.looking_for_game ? 'Looking for a game' : 'Just playing'}</div>
          </div>
          <button class="btn btn-secondary btn-sm" id="pf-checkout">Check out</button>
        </div>` : ''}
      <div class="card row" style="margin-bottom:10px">
        <span style="font-size:20px">🏠</span>
        <div class="row-main">
          <div class="row-title" style="font-size:14px">Home area</div>
          <div class="row-sub">${me.home_area ? esc(me.home_area) : 'Where the app opens — courts, games & players near you'}</div>
        </div>
        <button class="btn btn-secondary btn-sm" id="pf-home">${me.home_area ? 'Change' : 'Set'}</button>
      </div>
      <div class="card row" style="margin-bottom:10px">
        <span style="font-size:20px">📍</span>
        <div class="row-main">
          <div class="row-title" style="font-size:14px">Auto check-in</div>
          <div class="row-sub">Checks you in when you arrive at a court (while the app is open)</div>
        </div>
        <button class="btn btn-sm ${localStorage.getItem('pp_auto_checkin') === 'off' ? 'btn-secondary' : 'btn-primary'}" id="pf-auto">
          ${localStorage.getItem('pp_auto_checkin') === 'off' ? 'Off' : 'On'}
        </button>
      </div>
      <div class="card row" style="margin-bottom:10px">
        <span style="font-size:20px">💌</span>
        <div class="row-main">
          <div class="row-title" style="font-size:14px">Invite friends</div>
          <div class="row-sub">Share your link — they'll land right on your profile</div>
        </div>
        <button class="btn btn-primary btn-sm" id="pf-invite">Share</button>
      </div>
      ${!window.matchMedia('(display-mode: standalone)').matches ? `
        <div class="card row" style="margin-bottom:10px">
          <span style="font-size:20px">📱</span>
          <div class="row-main">
            <div class="row-title" style="font-size:14px">Get the app feel</div>
            <div class="row-sub">In your browser menu tap <b>Add to Home Screen</b> — Third Shot installs like an app.</div>
          </div>
        </div>` : ''}
      <button class="btn btn-secondary btn-block" id="pf-edit" style="margin-bottom:10px">✏️ Edit profile</button>
      <button class="btn btn-secondary btn-block" id="pf-activity" style="margin-bottom:10px">🔔 Activity</button>
      <button class="btn btn-danger btn-block" id="pf-logout">Log out</button>
      <div id="pf-upcoming"></div>
      <div id="pf-courts"></div>
      <div id="pf-history"></div>
    `;

    el.querySelector('#pf-invite').addEventListener('click', async () => {
      const url = `${location.origin}/#invite/${me.id}`;
      const text = `Play pickleball with me on Third Shot! 🎾`;
      try {
        if (navigator.share) {
          await navigator.share({ title: 'Third Shot', text, url });
        } else {
          await navigator.clipboard.writeText(`${text} ${url}`);
          toast('Invite link copied 📋');
        }
      } catch { /* user cancelled share */ }
    });
    el.querySelector('#pf-auto').addEventListener('click', (e) => {
      const off = localStorage.getItem('pp_auto_checkin') === 'off';
      localStorage.setItem('pp_auto_checkin', off ? 'on' : 'off');
      e.target.textContent = off ? 'On' : 'Off';
      e.target.classList.toggle('btn-primary', off);
      e.target.classList.toggle('btn-secondary', !off);
      toast(off ? 'Auto check-in on 📍' : 'Auto check-in off');
    });

    el.querySelector('#pf-home').addEventListener('click', async () => {
      const ok = await setHomeAreaFromLocation();
      if (ok) renderProfile();
    });
    el.querySelector('#pf-logout').addEventListener('click', logout);
    el.querySelector('#pf-edit').addEventListener('click', openEditProfile);
    el.querySelector('#pf-activity').addEventListener('click', openActivity);
    el.querySelector('#pf-checkout')?.addEventListener('click', async () => {
      const prev = state.presence;
      await api('/checkout', { method: 'POST' });
      await refreshMe();
      renderProfile();
      maybeAskConditions(prev);
    });

    // My upcoming games (parity with public profiles), tappable into the game screen.
    try {
      const mine = await api('/games?mine=1');
      const nowMs = Date.now();
      const up = (mine.items || []).filter((game) =>
        game.status === 'upcoming' && new Date(game.scheduled_at).getTime() > nowMs);
      if (up.length) {
        const upEl = el.querySelector('#pf-upcoming');
        upEl.innerHTML = '<div class="section-label">My upcoming games</div>'
          + up.map((game) => gameCardHtml(game)).join('');
        bindGameButtons(upEl, renderProfile);
      }
    } catch { /* ignore */ }

    // Personal play stats — quietly skipped for brand-new players.
    try {
      const stats = await api('/me/stats');
      if (stats.games_total > 0) {
        el.querySelector('#pf-play-stats').innerHTML = `
          <div class="stat-grid" style="margin-top:10px">
            <div class="stat-card"><div class="stat-value">${stats.games_this_month}</div><div class="stat-label">Games this month</div></div>
            <div class="stat-card"><div class="stat-value">${stats.week_streak >= 2 ? '🔥' : ''}${stats.week_streak}</div><div class="stat-label">Week streak</div></div>
            <div class="stat-card"${stats.top_court ? ` data-pfcourt-top="${stats.top_court.id}" style="cursor:pointer"` : ''}>
              <div class="stat-value" style="font-size:13px;line-height:1.25;padding-top:4px">${stats.top_court ? esc(stats.top_court.name) : '—'}</div>
              <div class="stat-label">Top court${stats.top_court ? ` · ${stats.top_court.games}` : ''}</div>
            </div>
          </div>`;
        el.querySelector('[data-pfcourt-top]')?.addEventListener('click', (e) => openCourtDetail(Number(e.currentTarget.dataset.pfcourtTop)));
      }
    } catch { /* ignore */ }

    // Saved courts (home court first), tappable into court detail.
    try {
      const favs = await api('/courts/favorites');
      const courtsEl = el.querySelector('#pf-courts');
      const rows = [];
      const seen = new Set();
      if (me.home_court_id) {
        rows.push({ id: me.home_court_id, name: me.home_court_name || 'Home court', city: '', is_home: true });
        seen.add(me.home_court_id);
      }
      (favs.items || []).forEach((c) => { if (!seen.has(c.id)) { rows.push({ ...c, is_home: false }); seen.add(c.id); } });
      courtsEl.innerHTML = '<div class="section-label">Saved courts</div>' + (rows.length
        ? rows.map((c) => `
            <div class="card row" data-pfcourt="${c.id}" style="cursor:pointer">
              <span style="font-size:18px">${c.is_home ? '🏠' : '⭐'}</span>
              <div class="row-main">
                <div class="row-title" style="font-size:14px">${esc(c.name)}</div>
                <div class="row-sub">${esc(c.city || '')}${c.is_home ? ' · Home court' : ''}${c.rating_avg ? ` · ⭐ ${c.rating_avg}` : ''}</div>
              </div>
              <span class="chev">›</span>
            </div>`).join('')
        : '<div class="empty-state" style="padding:16px">No saved courts yet — tap ☆ on a court to save it.<br><button class="btn btn-secondary btn-sm" data-goto="courts-list" style="margin-top:10px">🗺 Browse courts</button></div>');
      courtsEl.querySelectorAll('[data-pfcourt]').forEach((row) =>
        row.addEventListener('click', () => openCourtDetail(Number(row.dataset.pfcourt))));
    } catch { /* ignore */ }

    try {
      const history = await api('/games/history');
      if (history.items.length) {
        const historyEl = el.querySelector('#pf-history');
        const filters = [
          ['all', 'All', () => true],
          ['wins', '🏆 Wins', (g) => g.you_won === true],
          ['losses', 'Losses', (g) => g.you_won === false],
          ['ranked', 'Ranked', (g) => g.game_type === 'ranked'],
          ['casual', 'Casual', (g) => g.game_type === 'casual'],
        ];
        let active = 'all';
        const render = () => {
          const test = filters.find(([k]) => k === active)[2];
          const rows = history.items.filter(test);
          historyEl.innerHTML = `
            <div class="section-label">Match history</div>
            <div class="quick-times" style="margin:0 0 10px">
              ${filters.map(([k, label]) => `<button type="button" data-hf="${k}" class="${k === active ? 'active' : ''}">${label}</button>`).join('')}
            </div>
            ${rows.length ? rows.map(resultRowHtml).join('')
              : '<div class="empty-state" style="padding:14px">No games match this filter yet.</div>'}`;
          historyEl.querySelectorAll('[data-hf]').forEach((b) => b.addEventListener('click', () => {
            active = b.dataset.hf;
            render();
          }));
          bindGameButtons(historyEl, renderProfile);
        };
        render();
      }
    } catch { /* ignore */ }
  }

  function openEditProfile() {
    const me = state.me;
    const colors = ['#2f9e44', '#1971c2', '#e8590c', '#9c36b5', '#0c8599', '#e03131', '#f08c00', '#5f3dc4'];
    const modal = openModal(`
      ${modalHead('Edit profile')}
      <div class="form-field"><label>Display name</label><input type="text" id="ep-name" value="${esc(me.display_name)}" maxlength="60" /></div>
      <div class="form-field"><label>Bio</label><textarea id="ep-bio" rows="2" maxlength="300">${esc(me.bio || '')}</textarea></div>
      <div class="form-field">
        <label>Skill level</label>
        <select id="ep-skill">
          ${['beginner', 'intermediate', 'advanced', 'pro'].map((s) => `<option value="${s}" ${me.skill_level === s ? 'selected' : ''}>${skillLabel(s)}</option>`).join('')}
        </select>
      </div>
      <div class="form-field">
        <label>Profile photo (optional)</label>
        <div class="row" style="gap:10px">
          <div id="ep-avatar-preview">${avatarHtml(me)}</div>
          <input type="url" id="ep-avatar-url" placeholder="Paste an image URL…" value="${esc(me.avatar_url || '')}" style="flex:1" />
        </div>
        <p class="row-sub" style="margin-top:4px">Leave blank to use your colored initials.</p>
      </div>
      <div class="form-field">
        <label>Avatar color</label>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${colors.map((c) => `<button type="button" class="avatar" data-color="${c}" style="background:${c};outline:${me.avatar_color === c ? '3px solid var(--ink)' : 'none'}">${esc(initials(me.display_name))}</button>`).join('')}
        </div>
      </div>
      <div class="form-field">
        <label>Usually plays</label>
        <p class="row-sub" style="margin-bottom:6px">Tap when you typically play — helps players find partners on their schedule.</p>
        ${AVAIL_PARTS.map(([part, emoji]) => `
          <div class="av-row">
            <span class="av-emoji" title="${part}">${emoji}</span>
            ${AVAIL_DAYS.map((d) => `
              <button type="button" class="av-chip ${(me.availability || []).includes(`${d}-${part}`) ? 'active' : ''}" data-av="${d}-${part}">${d[0].toUpperCase()}</button>`).join('')}
          </div>`).join('')}
      </div>
      <div class="form-field">
        <label>Home court</label>
        <input type="text" id="ep-court-search" placeholder="${me.home_court_name ? esc(me.home_court_name) : 'Search courts…'}" />
        <input type="hidden" id="ep-court-id" value="${me.home_court_id || ''}" />
        <div id="ep-court-results"></div>
      </div>
      <button class="btn btn-primary btn-block" id="ep-save">Save</button>
      <details style="margin-top:22px">
        <summary style="font-size:13px;font-weight:600;cursor:pointer">Change password</summary>
        <div class="form-field" style="margin-top:10px">
          <input type="password" id="ep-pw-current" placeholder="Current password" autocomplete="current-password" />
          <input type="password" id="ep-pw-new" placeholder="New password (6+ characters)" autocomplete="new-password" style="margin-top:8px" />
          <button class="btn btn-secondary btn-block" id="ep-pw-save" style="margin-top:8px">Update password</button>
        </div>
      </details>
      <details style="margin-top:22px">
        <summary style="color:#e03131;font-size:13px;font-weight:600;cursor:pointer">Danger zone</summary>
        <div class="form-field" style="margin-top:10px">
          <label>Delete account</label>
          <p class="row-sub" style="margin-bottom:8px">Permanently removes your profile, friends, messages, and check-ins. Completed match results stay for your opponents, shown as “Deleted player”. This cannot be undone.</p>
          <input type="password" id="ep-delete-password" placeholder="Confirm your password" autocomplete="current-password" />
          <button class="btn btn-danger btn-block" id="ep-delete" style="margin-top:8px">Delete my account</button>
        </div>
      </details>
    `);

    modal.querySelectorAll('[data-av]').forEach((chip) =>
      chip.addEventListener('click', () => chip.classList.toggle('active')));

    let color = me.avatar_color;
    const avatarUrlInput = modal.querySelector('#ep-avatar-url');
    const refreshAvatarPreview = () => {
      modal.querySelector('#ep-avatar-preview').innerHTML = avatarHtml({
        display_name: me.display_name, avatar_color: color,
        avatar_url: avatarUrlInput.value.trim(),
      });
    };
    avatarUrlInput.addEventListener('input', refreshAvatarPreview);
    modal.querySelectorAll('[data-color]').forEach((b) => b.addEventListener('click', () => {
      color = b.dataset.color;
      modal.querySelectorAll('[data-color]').forEach((x) => { x.style.outline = x === b ? '3px solid var(--ink)' : 'none'; });
      refreshAvatarPreview();
    }));

    let timer;
    const courtSearch = modal.querySelector('#ep-court-search');
    courtSearch.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = courtSearch.value.trim();
        if (q.length < 2) return;
        let url = `/courts?q=${encodeURIComponent(q)}&limit=5`;
        if (state.userLoc) url += `&lat=${state.userLoc[0]}&lng=${state.userLoc[1]}`;
        const data = await api(url);
        modal.querySelector('#ep-court-results').innerHTML = data.items.map((c) => `
          <div class="card" data-pick="${c.id}" data-name="${esc(c.name)}" style="cursor:pointer;margin:6px 0;padding:10px">
            <div class="row-title" style="font-size:14px">${esc(c.name)}</div>
            <div class="row-sub">${esc(c.city)}</div>
          </div>`).join('');
        modal.querySelectorAll('[data-pick]').forEach((row) => row.addEventListener('click', () => {
          modal.querySelector('#ep-court-id').value = row.dataset.pick;
          courtSearch.value = row.dataset.name;
          modal.querySelector('#ep-court-results').innerHTML = '';
        }));
      }, 300);
    });

    modal.querySelector('#ep-pw-save').addEventListener('click', async () => {
      const current = modal.querySelector('#ep-pw-current').value;
      const next = modal.querySelector('#ep-pw-new').value;
      if (!current || !next) { toast('Fill in both password fields'); return; }
      try {
        await api('/auth/change-password', {
          method: 'POST',
          body: JSON.stringify({ current_password: current, new_password: next }),
        });
        modal.querySelector('#ep-pw-current').value = '';
        modal.querySelector('#ep-pw-new').value = '';
        toast('Password updated 🔒');
      } catch (e) { toast(e.message); }
    });

    modal.querySelector('#ep-delete').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      if (btn.disabled) return;
      const password = modal.querySelector('#ep-delete-password').value;
      if (!password) { toast('Enter your password to confirm'); return; }
      if (!window.confirm('Delete your account forever? This cannot be undone.')) return;
      btn.disabled = true;
      try {
        await api('/me', { method: 'DELETE', body: JSON.stringify({ password }) });
        toast('Account deleted. Goodbye 👋');
        closeModal(modal);
        logout();
      } catch (err) { toast(err.message); btn.disabled = false; }
    });

    modal.querySelector('#ep-save').addEventListener('click', async () => {
      try {
        const body = {
          display_name: modal.querySelector('#ep-name').value.trim(),
          bio: modal.querySelector('#ep-bio').value.trim(),
          skill_level: modal.querySelector('#ep-skill').value,
          avatar_color: color,
          avatar_url: avatarUrlInput.value.trim(),
          availability: [...modal.querySelectorAll('[data-av].active')].map((c) => c.dataset.av),
        };
        const courtId = modal.querySelector('#ep-court-id').value;
        if (courtId) body.home_court_id = Number(courtId);
        const data = await api('/me', { method: 'PATCH', body: JSON.stringify(body) });
        applyMe(data);
        closeModal(modal);
        toast('Profile updated');
        renderProfile();
      } catch (e) { toast(e.message); }
    });
  }

  function gameFingerprint(game) {
    return JSON.stringify([
      game.status, game.score_team1, game.score_team2, game.score_submitted_by,
      game.players.map((p) => [p.user_id, p.team]).sort((x, y) => x[0] - y[0]),
    ]);
  }

  function gameScreenHtml(game) {
    const court = game.court || {};
    const isChallenge = game.notes.startsWith('⚔️');
    const live = game.status === 'upcoming' && new Date(game.scheduled_at).getTime() <= Date.now();

    let emoji = '🎾';
    let headline = fmtDateTime(game.scheduled_at);
    let subline = `${game.players.length}/${game.max_players} players`;
    if (game.status === 'completed') {
      emoji = game.you_won === true ? '🏆' : game.you_won === false ? '🤝' : '✅';
      headline = `Final: ${game.score_team1}–${game.score_team2}`;
      subline = fmtDateTime(game.completed_at);
    } else if (game.status === 'cancelled') {
      emoji = '🚫'; headline = 'Cancelled'; subline = 'This game was called off.';
    } else if (game.status === 'expired') {
      emoji = '🕸'; headline = 'Expired'; subline = 'No score was entered, so this game closed itself.';
    } else if (game.status === 'awaiting_confirmation') {
      emoji = game.awaiting_your_confirmation ? '⚡' : '⏳';
      headline = `Reported: ${game.score_team1}–${game.score_team2}`;
      subline = game.awaiting_your_confirmation
        ? `${esc(game.score_submitted_by_name || 'Opponent')} reported — confirm or dispute`
        : 'Waiting for opponents to confirm';
    } else if (isChallenge && !game.is_joined && game.spots_left > 0) {
      emoji = '⚔️'; headline = "You've been challenged!";
      subline = `Ranked singles vs ${esc((game.players[0] || {}).display_name || 'a player')}`;
    } else if (live) {
      emoji = '🟢'; headline = 'Game on!';
      subline = game.players.length >= 2 ? 'Enter the score when you finish' : 'Waiting for players to join';
    }

    const team1 = game.players.filter((p) => p.team === 1);
    const team2 = game.players.filter((p) => p.team === 2);
    const playerRow = (p) => `
      <div class="row" style="margin-bottom:8px" data-view-user="${p.user_id}">
        ${avatarHtml(p, 'sm')}
        <div class="row-main">
          <div class="row-title" style="font-size:14px">${esc(p.display_name)}${p.user_id === game.creator_id ? ' <span class="tag" style="margin:0 0 0 6px;font-size:10.5px;padding:2px 8px">Host</span>' : ''}</div>
          <div class="row-sub">${skillLabel(p.skill_level)} · ${p.rating}${p.rating_delta != null ? ` · <span class="${p.rating_delta >= 0 ? 'delta-up' : 'delta-down'}">${p.rating_delta >= 0 ? '+' : ''}${p.rating_delta}</span>` : ''}</div>
        </div>
      </div>`;
    const playersHtml = (team1.length && team2.length)
      ? `<div class="form-grid">
          <div><div class="section-label" style="margin-top:0">Team 1</div>${team1.map(playerRow).join('')}</div>
          <div><div class="section-label" style="margin-top:0">Team 2</div>${team2.map(playerRow).join('')}</div>
        </div>`
      : game.players.map(playerRow).join('');

    let actions = '';
    if (game.status === 'upcoming') {
      if (!game.is_joined && game.spots_left > 0) {
        actions = `<button class="btn btn-primary btn-block" id="gs-join" style="padding:16px">${isChallenge ? '⚔️ Accept challenge' : '🎾 Join this game'}</button>`;
        if (isChallenge && game.players.length === 1) {
          actions += '<button class="btn btn-danger btn-block" id="gs-decline" style="margin-top:10px">Decline</button>';
        }
      } else if (!game.is_joined) {
        actions = game.waitlist_position
          ? `<div class="empty-state" style="padding:12px">⏳ You're #${game.waitlist_position} on the waitlist — we'll notify you when a spot opens.</div>
             <button class="btn btn-secondary btn-block" id="gs-waitlist-leave">Leave waitlist</button>`
          : `<button class="btn btn-primary btn-block" id="gs-waitlist" style="padding:16px">⏳ Join waitlist${game.waitlist_count ? ` · ${game.waitlist_count} waiting` : ''}</button>`;
      } else if (game.is_joined) {
        if (game.players.length >= 2) {
          actions = `<button class="btn btn-primary btn-block" id="gs-score" style="padding:16px">📝 Enter the score</button>`;
        }
        actions += `<div class="action-row" style="margin-top:10px">
          <button class="btn btn-secondary" id="gs-leave">Leave game</button>
          ${game.is_creator ? '<button class="btn btn-danger" id="gs-cancel">Cancel game</button>' : ''}
        </div>`;
      }
    } else if (game.status === 'awaiting_confirmation' && game.awaiting_your_confirmation) {
      actions = `
        <button class="btn btn-primary btn-block" id="gs-confirm" style="padding:16px">✓ Confirm ${game.score_team1}–${game.score_team2}</button>
        <button class="btn btn-danger btn-block" id="gs-dispute" style="margin-top:10px">✕ That score is wrong</button>`;
    } else if (game.status === 'completed' && game.is_joined) {
      actions = `<button class="btn btn-secondary btn-block" id="gs-rematch">↺ Rematch at ${esc(court.name || 'this court')}</button>`;
    }

    return `
      <div class="modal-head">
        <div style="flex:1">
          <h3>${emoji} ${headline} ${game.game_type === 'ranked' ? '<span class="tag ranked" style="margin:0 0 0 6px">Ranked</span>' : '<span class="tag" style="margin:0 0 0 6px">Casual</span>'}${game.recurrence === 'weekly' ? '<span class="tag" style="margin:0 0 0 6px">🔁 Weekly</span>' : ''}</h3>
          <div class="row-sub">${subline}</div>
        </div>
        ${game.is_joined ? '<button class="icon-btn" id="gs-chat" title="Game chat" aria-label="Game chat" style="box-shadow:none;font-size:17px">💬</button>' : ''}
        <button class="icon-btn" id="gs-share" title="Share game" aria-label="Share game" style="box-shadow:none;font-size:17px">📤</button>
        <button class="modal-close" aria-label="Close">✕</button>
      </div>
      <div class="card row" id="gs-court" style="cursor:pointer">
        <span style="font-size:20px">📍</span>
        <div class="row-main">
          <div class="row-title" style="font-size:14px">${esc(court.name || 'Court')}</div>
          <div class="row-sub">${esc(court.city || '')}</div>
        </div>
        <span class="chev">›</span>
      </div>
      ${game.notes ? `<div class="row-sub" style="margin:0 0 12px 4px">“${esc(game.notes)}”</div>` : ''}
      <div class="section-label">Players (${game.players.length}/${game.max_players})</div>
      ${playersHtml}
      <div style="margin-top:16px">${actions}</div>`;
  }

  async function openGameScreen(gameId) {
    let game;
    try { game = await api(`/games/${gameId}`); } catch (e) { toast(e.message); return; }

    const modal = openModal('');
    const box = modal.querySelector('.modal');
    let fingerprint = '';
    try { history.replaceState(null, '', `#game/${gameId}`); } catch { /* ignore */ }
    modal.addEventListener('click', (e) => {
      if (e.target === modal) { try { history.replaceState(null, '', location.pathname); } catch { /* ignore */ } }
    });

    const render = (fresh) => {
      game = fresh;
      fingerprint = gameFingerprint(game);
      box.innerHTML = gameScreenHtml(game);
      bind();
    };

    const reopenFresh = async () => {
      try { render(await api(`/games/${gameId}`)); } catch (e) { toast(e.message); }
    };

    function bind() {
      const court = game.court || {};
      const isChallenge = game.notes.startsWith('⚔️');
      const clearHash = () => { try { history.replaceState(null, '', location.pathname); } catch { /* ignore */ } };
      box.querySelectorAll('.modal-close').forEach((b) => { b.onclick = () => { clearHash(); closeModal(modal); }; });
      box.querySelector('#gs-court')?.addEventListener('click', () => { clearHash(); closeModal(modal); openCourtDetail(court.id); });
      box.querySelector('#gs-chat')?.addEventListener('click', () => openGameChat(game));
      box.querySelector('#gs-waitlist')?.addEventListener('click', async () => {
        try {
          await api(`/games/${gameId}/waitlist`, { method: 'POST' });
          toast("You're on the waitlist ⏳");
          reopenFresh();
        } catch (e) { toast(e.message); reopenFresh(); }
      });
      box.querySelector('#gs-waitlist-leave')?.addEventListener('click', async () => {
        try {
          await api(`/games/${gameId}/waitlist/leave`, { method: 'POST' });
          toast('Left the waitlist');
          reopenFresh();
        } catch (e) { toast(e.message); reopenFresh(); }
      });
      box.querySelector('#gs-share')?.addEventListener('click', () => shareGame(game));
      box.querySelector('#gs-join')?.addEventListener('click', async () => {
        try {
          await api(`/games/${gameId}/join`, { method: 'POST' });
          toast(isChallenge ? 'Challenge accepted! ⚔️' : "You're in! 🎾");
          refreshMe(); reopenFresh();
        } catch (e) { toast(e.message); reopenFresh(); }
      });
      box.querySelector('#gs-decline')?.addEventListener('click', async () => {
        try {
          await api(`/games/${gameId}/decline`, { method: 'POST' });
          toast('Challenge declined');
          closeModal(modal); refreshMe();
        } catch (e) { toast(e.message); reopenFresh(); }
      });
      box.querySelector('#gs-score')?.addEventListener('click', async () => {
        const fresh = await api(`/games/${gameId}`);
        closeModal(modal);
        openScoreModal(fresh, () => refreshMe());
      });
      box.querySelector('#gs-confirm')?.addEventListener('click', async () => {
        try {
          const updated = await api(`/games/${gameId}/confirm`, { method: 'POST' });
          closeModal(modal);
          showCelebration(updated);
          refreshMe();
          if (state.tab === 'play') renderPlay();
        } catch (e) { toast(e.message); reopenFresh(); }
      });
      box.querySelector('#gs-dispute')?.addEventListener('click', async () => {
        try {
          await api(`/games/${gameId}/dispute`, { method: 'POST' });
          toast('Score cleared — enter the right one together');
          refreshMe(); reopenFresh();
        } catch (e) { toast(e.message); reopenFresh(); }
      });
      box.querySelector('#gs-rematch')?.addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        if (btn.disabled) return;
        btn.disabled = true;
        const others = game.players.map((p) => p.user_id).filter((id) => id !== state.me.id);
        try {
          const rematch = await api('/games', {
            method: 'POST',
            body: JSON.stringify({
              court_id: court.id,
              scheduled_at: new Date().toISOString(),
              game_type: game.game_type,
              max_players: game.max_players,
              visibility: others.length ? 'private' : 'open',
              invite_user_ids: others,
              notes: '↺ Rematch!',
            }),
          });
          clearHash();
          closeModal(modal);
          toast(others.length ? 'Rematch is on — invites sent ⚔️' : 'Rematch is on ⚔️');
          refreshMe();
          openGameScreen(rematch.id);
        } catch (err) { toast(err.message); btn.disabled = false; }
      });
      box.querySelector('#gs-leave')?.addEventListener('click', async () => {
        try {
          await api(`/games/${gameId}/leave`, { method: 'POST' });
          toast('Left the game');
          closeModal(modal); refreshMe();
          if (state.tab === 'play') renderPlay();
        } catch (e) { toast(e.message); reopenFresh(); }
      });
      box.querySelector('#gs-cancel')?.addEventListener('click', async () => {
        if (!confirm('Cancel this game for everyone?')) return;
        try {
          await api(`/games/${gameId}/cancel`, { method: 'POST' });
          toast('Game cancelled');
          closeModal(modal); refreshMe();
          if (state.tab === 'play') renderPlay();
        } catch (e) { toast(e.message); reopenFresh(); }
      });
      bindUserButtons(box);
    }

    render(game);

    // Live sync: while this screen is open, pick up joins, scores, confirmations…
    const pollTimer = setInterval(async () => {
      if (!document.body.contains(box)) { clearInterval(pollTimer); return; }
      try {
        const fresh = await api(`/games/${gameId}`);
        if (gameFingerprint(fresh) !== fingerprint) {
          render(fresh);
          refreshMe();
        }
      } catch { /* offline */ }
    }, 5000);
  }

  async function openActivity() {
    let data;
    try { data = await api('/notifications'); } catch (e) { toast(e.message); return; }
    const enableBtn = (typeof Notification !== 'undefined' && Notification.permission === 'default')
      ? '<button class="btn btn-secondary btn-block" id="act-enable" style="margin-bottom:12px">🔔 Enable phone notifications</button>'
      : '';
    const icons = { friend_request: '🤝', friend_accept: '🎉', game_join: '🎾', game_cancelled: '🚫', ranked_result: '🏆', game_invite: '📅', game_invite_direct: '📨', score_submitted: '📝', score_confirmed: '✅', score_disputed: '⚠️', challenge: '⚔️', challenge_declined: '🙅', game_reminder: '⏰', game_message: '💬' };
    // Where each notification taps to: game if it references one, else the other user for friend events.
    const targetFor = (n) => {
      if (n.related_game_id) return { type: 'game', id: n.related_game_id };
      if (n.related_user_id && (n.kind === 'friend_request' || n.kind === 'friend_accept')) {
        return { type: 'user', id: n.related_user_id };
      }
      return null;
    };

    let listHtml = '';
    let lastLabel = null;
    data.items.forEach((n) => {
      const label = resultDayLabel(n.created_at);
      if (label !== lastLabel) { listHtml += `<div class="section-label">${label}</div>`; lastLabel = label; }
      const t = targetFor(n);
      const time = new Date(n.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      listHtml += `
        <div class="card row" ${t ? `data-notif-type="${t.type}" data-notif-id="${t.id}" style="cursor:pointer"` : ''}>
          ${n.read ? '' : '<span class="notif-dot"></span>'}
          <span style="font-size:20px">${icons[n.kind] || '🔔'}</span>
          <div class="row-main">
            <div class="row-title" style="font-size:14px;${n.read ? '' : 'font-weight:800'}">${esc(n.title)}</div>
            <div class="row-sub">${time}</div>
          </div>
          ${t ? '<span class="chev">›</span>' : ''}
        </div>`;
    });

    const modal = openModal(`
      ${modalHead('Activity')}
      ${enableBtn}
      ${data.items.length ? listHtml
        : '<div class="empty-state"><span class="big">🔔</span>Nothing yet — go play some pickleball!<br><button class="btn btn-primary" data-goto="play" style="margin-top:10px">🎾 Find a game</button></div>'}
    `);
    modal.querySelector('#act-enable')?.addEventListener('click', async (e) => {
      const result = await Notification.requestPermission();
      e.target.remove();
      toast(result === 'granted' ? 'Notifications on 🔔' : 'Notifications stay off');
    });
    modal.querySelectorAll('[data-notif-type]').forEach((row) => {
      row.addEventListener('click', () => {
        closeModal(modal);
        if (row.dataset.notifType === 'game') openGameScreen(Number(row.dataset.notifId));
        else openUserProfile(Number(row.dataset.notifId));
      });
    });
    if (data.unread) {
      api('/notifications/read', { method: 'POST' }).then(refreshMe).catch(() => {});
    }
  }

  // ---------- Presence banner ----------
  function renderPresenceBanner() {
    const el = $('#presence-banner');
    if (state.presence && state.presence.checked_in) {
      el.innerHTML = `📍 You're at <b>&nbsp;${esc(state.presence.court_name)}&nbsp;</b><span style="opacity:.7">›</span>
        <button id="banner-checkout">Check out</button>`;
      el.classList.remove('hidden');
      el.style.cursor = 'pointer';
      el.onclick = (e) => {
        if (e.target.id !== 'banner-checkout') openCourtDetail(state.presence.court_id);
      };
      $('#banner-checkout').addEventListener('click', async (e) => {
        e.stopPropagation();
        const prev = state.presence;
        await api('/checkout', { method: 'POST' });
        toast('Checked out 👋');
        await refreshMe();
        fetchCourtsInView();
        maybeAskConditions(prev);
      });
    } else {
      el.classList.add('hidden');
      el.onclick = null;
    }
  }

  // ---------- Boot ----------
  // Capture the device location as the user's home area (reverse-geocoded label).
  async function setHomeAreaFromLocation({ silent = false } = {}) {
    if (!navigator.geolocation) {
      if (!silent) toast('Location not available on this device');
      return false;
    }
    return new Promise((resolve) => {
      navigator.geolocation.getCurrentPosition(async (pos) => {
        const lat = pos.coords.latitude;
        const lng = pos.coords.longitude;
        let label = '';
        try {
          const geo = await api(`/geocode/reverse?lat=${lat}&lng=${lng}`);
          label = geo.label || '';
        } catch { /* label is optional */ }
        try {
          const data = await api('/me', {
            method: 'PATCH',
            body: JSON.stringify({ home_lat: lat, home_lng: lng, home_area: label }),
          });
          applyMe(data);
          state.areaLoc = [lat, lng];
          state.userLoc = [lat, lng];
          if (state.map) { state.map.setView([lat, lng], 12); updateUserDot(); }
          toast(label ? `Home area set to ${label} 📍` : 'Home area set 📍');
          resolve(true);
        } catch (e) { if (!silent) toast(e.message); resolve(false); }
      }, () => {
        if (!silent) toast('Could not get your location');
        resolve(false);
      }, { timeout: 10000 });
    });
  }

  function maybeOnboardHomeArea() {
    if (!state.me) return;
    // Returning / already-prompted users skip straight to the tour check.
    if (state.me.home_lat != null || localStorage.getItem('pp_onboarded_home') === '1') {
      maybeShowTour();
      return;
    }
    localStorage.setItem('pp_onboarded_home', '1');
    const modal = openModal(`
      <div class="checkin-sheet">
        <div class="celebrate-emoji" style="font-size:46px">📍</div>
        <h3 style="margin:6px 0 2px">Set your home area</h3>
        <p class="row-sub" style="margin-bottom:18px">So Third Shot opens to courts, games, and players near you — anywhere in the US.</p>
        <button class="btn btn-primary btn-block" id="ob-loc" style="padding:15px;margin-bottom:8px">Use my current location</button>
        <button class="btn-link modal-close btn-block">Maybe later</button>
      </div>
    `);
    modal.querySelector('#ob-loc').addEventListener('click', async () => {
      const ok = await setHomeAreaFromLocation();
      if (ok) { closeModal(modal); fetchCourtsInView(); maybeShowTour(); }
    });
    // Whichever way they leave the home-area step, follow with the quick tour.
    modal.querySelector('.modal-close').addEventListener('click', () => maybeShowTour());
  }

  // One-time 3-step welcome tour for brand-new users.
  function maybeShowTour() {
    if (!state.me || localStorage.getItem('pp_tour_seen') === '1') return;
    localStorage.setItem('pp_tour_seen', '1');
    const steps = [
      { emoji: '🗺️', title: 'Find courts near you', body: "Browse the map, tap a court to see who's playing, and check in when you arrive." },
      { emoji: '🤝', title: 'Meet players', body: 'See players nearby and your friends — then add, message, or challenge them.' },
      { emoji: '🎾', title: 'Play a game', body: 'Start a game now or schedule one — casual, ranked, or a weekly open-play session.' },
    ];
    let i = 0;
    const modal = openModal('');
    const box = modal.querySelector('.modal');
    const render = () => {
      const s = steps[i];
      const last = i === steps.length - 1;
      box.innerHTML = `
        <div class="checkin-sheet">
          <div class="celebrate-emoji" style="font-size:46px">${s.emoji}</div>
          <h3 style="margin:6px 0 2px">${esc(s.title)}</h3>
          <p class="row-sub" style="margin-bottom:14px">${esc(s.body)}</p>
          <div class="tour-dots">${steps.map((_, k) => `<span class="tour-dot ${k === i ? 'on' : ''}"></span>`).join('')}</div>
          <button class="btn btn-primary btn-block" id="tour-next" style="padding:14px;margin-top:14px">${last ? "Let's play 🎾" : 'Next'}</button>
          ${last ? '' : '<button class="btn-link btn-block" id="tour-skip">Skip</button>'}
        </div>`;
      box.querySelector('#tour-next').onclick = () => {
        if (last) closeModal(modal); else { i += 1; render(); }
      };
      box.querySelector('#tour-skip')?.addEventListener('click', () => closeModal(modal));
    };
    render();
  }

  async function showMain() {
    $('#auth-screen').classList.add('hidden');
    $('#main-screen').classList.remove('hidden');
    if (!state.map) setupMap();
    else setTimeout(() => state.map.invalidateSize(), 60);
    startLocationWatch();
    maybeOnboardHomeArea();
    setTimeout(maybeShowUsualTimeNudge, 1200); // after the map/feeds settle
    clearInterval(state.mePollTimer);
    let tick = 0;
    state.mePollTimer = setInterval(() => {
      refreshMe();
      tick += 1;
      if (tick % 3 === 0 && state.presence && state.presence.checked_in) {
        api('/presence/ping', { method: 'POST' }).catch(() => {});
      }
    }, 12000);
  }

  function slotForNow(d = new Date()) {
    const day = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'][d.getDay()];
    const h = d.getHours();
    const part = h >= 5 && h < 12 ? 'am' : h < 17 ? 'pm' : h < 23 ? 'eve' : null;
    return part ? `${day}-${part}` : null;
  }

  // "It's your usual time to play" — shown once per session when the current
  // local time matches one of the player's availability slots.
  async function maybeShowUsualTimeNudge() {
    if (sessionStorage.getItem('pp_usual_nudge')) return;
    const slots = (state.me && state.me.availability) || [];
    const slot = slotForNow();
    if (!slot || !slots.includes(slot)) return;
    if (state.activeGame) return; // the live banner already owns that space
    sessionStorage.setItem('pp_usual_nudge', '1');
    let openGames = 0;
    try {
      const loc = areaLatLng();
      const data = await api(`/games?lat=${loc.lat}&lng=${loc.lng}&radius=60`);
      openGames = data.items.filter((g) => !g.is_joined && g.spots_left > 0).length;
    } catch { return; }
    const el = document.createElement('div');
    el.className = 'usual-nudge';
    el.innerHTML = `
      <span style="font-size:20px">🎾</span>
      <div class="row-main">
        <b>Your usual time to play!</b>
        <div class="row-sub">${openGames ? `${openGames} open game${openGames === 1 ? '' : 's'} near you` : 'No games nearby yet — start one?'}</div>
      </div>
      <button class="btn btn-primary btn-sm" data-goto="${openGames ? 'play' : 'new-game'}">${openGames ? 'See games' : 'Start one'}</button>
      <button class="nudge-x" aria-label="Dismiss">✕</button>`;
    $('#app').appendChild(el);
    const dismiss = () => el.remove();
    el.querySelector('.nudge-x').addEventListener('click', dismiss);
    el.querySelector('[data-goto]').addEventListener('click', dismiss);
  }

  function setupConnectivity() {
    const banner = $('#offline-banner');
    const sync = () => banner.classList.toggle('hidden', navigator.onLine);
    window.addEventListener('offline', sync);
    window.addEventListener('online', () => {
      sync();
      toast('Back online 🎾');
      if (state.token) refreshMe();
    });
    sync();
  }

  function openDeepLink() {
    const courtMatch = location.hash.match(/^#court\/(\d+)$/);
    if (courtMatch) { openCourtDetail(Number(courtMatch[1])); return; }
    const gameMatch = location.hash.match(/^#game\/(\d+)$/);
    if (gameMatch) { openGameScreen(Number(gameMatch[1])); return; }
    const inviteMatch = location.hash.match(/^#invite\/(\d+)$/);
    if (inviteMatch) {
      try { history.replaceState(null, '', location.pathname); } catch { /* ignore */ }
      openUserProfile(Number(inviteMatch[1]));
    }
  }

  // A friend's invite link, remembered through the signup flow.
  async function handleInviteRef() {
    const ref = Number(localStorage.getItem('pp_invite_ref') || 0);
    localStorage.removeItem('pp_invite_ref');
    if (!ref || ref === state.me.id) return;
    try {
      await api('/friends/request', { method: 'POST', body: JSON.stringify({ user_id: ref }) });
      toast('Friend request sent to the player who invited you 🤝');
    } catch { /* already friends / requested — fine */ }
  }

  async function boot() {
    if ('serviceWorker' in navigator && location.protocol === 'https:') {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    }
    // Remember a friend's invite link across the signup flow, and greet the
    // newcomer with who invited them.
    const inviteRef = location.hash.match(/^#invite\/(\d+)$/);
    if (inviteRef && !state.token) {
      localStorage.setItem('pp_invite_ref', inviteRef[1]);
      try { history.replaceState(null, '', location.pathname); } catch { /* ignore */ }
      api(`/invite/${inviteRef[1]}`).then((card) => {
        const tagline = document.querySelector('.auth-tagline');
        if (!tagline || document.querySelector('.invite-hello')) return;
        const el = document.createElement('div');
        el.className = 'invite-hello';
        el.innerHTML = `${avatarHtml(card, 'sm')} <span><b>${esc(card.display_name)}</b> invited you to play 🎾</span>`;
        tagline.after(el);
      }).catch(() => { /* inviter gone — sign up normally */ });
    }
    setupAuth();
    setupTabs();
    setupPlay();
    setupChat();
    setupEmptyStateCtas();
    setupConnectivity();
    if (state.token) {
      try {
        applyMe(await api('/me'));
        showMain();
        openDeepLink();
        return;
      } catch { /* fall through to auth */ }
    }
    $('#auth-screen').classList.remove('hidden');
  }

  boot();
})();
