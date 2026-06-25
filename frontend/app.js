/* Picklepals — simple social pickleball app */
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
  function avatarHtml(user, cls = '') {
    return `<div class="avatar ${cls}" style="background:${esc(user.avatar_color || '#2f9e44')}">${esc(initials(user.display_name))}</div>`;
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
      } catch (err) {
        errEl.textContent = err.message;
        errEl.classList.remove('hidden');
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

  async function fetchCourtsInView() {
    if (!state.map) return;
    const b = state.map.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(4)).join(',');
    let url = `/courts?bbox=${bbox}&limit=250`;
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
      const size = busy ? 34 : 26;
      const gameBadge = court.upcoming_games > 0
        ? `<span class="marker-game-badge">${court.upcoming_games}</span>` : '';
      const icon = L.divIcon({
        className: '',
        html: `<div class="court-marker ${busy ? 'busy' : ''}" style="width:${size}px;height:${size}px">${busy ? court.players_here + '👤' : court.num_courts}${gameBadge}</div>`,
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
    return `
      <div class="card row" data-court="${c.id}" style="cursor:pointer">
        <div class="row-main">
          <div class="row-title">${esc(c.name)}</div>
          <div class="row-sub">
            ${esc(c.city)}${c.distance_miles != null ? ` · ${c.distance_miles} mi` : ''}
            · ${c.num_courts} court${c.num_courts === 1 ? '' : 's'}
            ${c.players_here ? ` · <b style="color:var(--green-700)">${c.players_here} playing now</b>` : ''}
            ${c.upcoming_games ? ` · ${c.upcoming_games} game${c.upcoming_games === 1 ? '' : 's'} scheduled` : ''}
          </div>
        </div>
        <span class="chev">›</span>
      </div>
    `;
  }

  async function renderCourtList(courts, places = []) {
    const el = $('#court-list-items');
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
  const modalHead = (title) => `<div class="modal-head"><h3>${esc(title)}</h3><button class="modal-close">✕</button></div>`;

  // ---------- Court detail ----------
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

    const gamesHtml = court.games.length
      ? court.games.map((g) => gameCardHtml(g, { compact: true })).join('')
      : '<div class="empty-state" style="padding:14px">No upcoming games here yet.</div>';

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
          <button class="glass-btn" id="cd-share" title="Share"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:17px;height:17px"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" x2="12" y1="2" y2="15"/></svg></button>
          <button class="glass-btn" id="cd-favorite" title="Save">${isFavorite ? '★' : '☆'}</button>
          <button class="glass-btn modal-close">✕</button>
        </div>
        <div class="cd-hero-title">
          <h2>${esc(court.name)}</h2>
          <div>${esc([court.address, court.city].filter(Boolean).join(', '))}</div>
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
      <div style="margin-top:14px">${chipsHtml}</div>
      ${court.open_play_schedule ? `
        <details class="cd-hours">
          <summary>🕑 Open play hours</summary>
          <p>${esc(court.open_play_schedule)}</p>
        </details>` : ''}
      ${linkParts.length ? `<div class="cd-links">${linkParts.join('')}</div>` : ''}
      <div class="section-label">Playing now (${court.players_here.length})${court.friends_here ? ` · ${court.friends_here} friend${court.friends_here === 1 ? '' : 's'} here` : ''}</div>
      ${playersHtml}
      <div class="section-label">Upcoming games</div>
      ${gamesHtml}
      ${(court.recent_results || []).length ? `
        <div class="section-label">Recent results here</div>
        ${court.recent_results.map(resultRowHtml).join('')}` : ''}
      </div>
    `, { court: true });

    modal.querySelector('#cd-checkin').addEventListener('click', async () => {
      if (checkedIn) {
        try {
          await api('/checkout', { method: 'POST' });
          toast('Checked out 👋');
          closeModal(modal);
          await refreshMe();
          fetchCourtsInView();
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

    modal.querySelector('#cd-favorite').addEventListener('click', async (e) => {
      const favBtn = e.currentTarget;
      try {
        const data = await api(`/courts/${court.id}/favorite`, { method: 'POST' });
        isFavorite = data.favorited;
        favBtn.textContent = isFavorite ? '★' : '☆';
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
      } else {
        action = '<span class="tag warn" style="margin:0">Full</span>';
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
            <div class="row-title">${esc(fmtDateTime(game.scheduled_at))}${typeTag}${visTag}</div>
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
        <button class="btn btn-primary btn-block modal-close" style="margin-top:18px">Keep playing</button>
      </div>
    `);
    void modal;
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
    el.innerHTML = '<div class="empty-state">Loading…</div>';
    const loc = areaLatLng();
    try {
      if (seg === 'scores') {
        const [board, results] = await Promise.all([
          api('/leaderboard'),
          api(`/games/results?lat=${loc.lat}&lng=${loc.lng}`),
        ]);
        let html = '';

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
              <div class="podium-rating">${u.rating}</div>
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
                  <div class="row-sub">${u.ranked_wins}W – ${u.ranked_losses}L</div>
                </div>
                <div class="stat-value" style="font-size:16px">${u.rating}</div>
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
          html += '<div class="empty-state"><span class="big">🏆</span>No ranked games yet.<br>Win one and claim the podium!</div>';
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
        bindGameButtons(el, renderPlay);
        bindUserButtons(el);
        return;
      }

      // --- Games: everything actionable + yours + nearby, one scroll ---
      const [mine, nearby] = await Promise.all([
        api('/games?mine=1'),
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
      const nearbyOpen = nearby.items.filter((g) => !mineIds.has(g.id));

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
      html += '<div class="section-label">Nearby games</div>';
      html += nearbyOpen.length
        ? nearbyOpen.map((g) => gameCardHtml(g)).join('')
        : '<div class="empty-state" style="padding:18px">No open games around you right now.<br>Tap + to start one!</div>';

      el.innerHTML = html;
      bindGameButtons(el, renderPlay);
    } catch (e) {
      el.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
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
    modal.querySelector('#ng-type').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      gameType = btn.dataset.val;
      modal.querySelectorAll('#ng-type button').forEach((b) => b.classList.toggle('active', b === btn));
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
        <button class="modal-close">✕</button>
      </div>
      ${singles ? '' : '<p class="row-sub" style="margin-bottom:8px">Tap a player to switch their team.</p>'}
      <div id="sc-chips" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px"></div>
      <div class="score-grid">
        <div class="score-panel" id="sc-panel-1">
          <div class="score-team-label" id="sc-label-1"></div>
          <div class="score-stepper">
            <button type="button" data-step="-1" data-target="sc-1">−</button>
            <input type="number" id="sc-1" min="0" max="99" value="11" inputmode="numeric" />
            <button type="button" data-step="1" data-target="sc-1">＋</button>
          </div>
        </div>
        <div class="score-vs">vs</div>
        <div class="score-panel" id="sc-panel-2">
          <div class="score-team-label" id="sc-label-2"></div>
          <div class="score-stepper">
            <button type="button" data-step="-1" data-target="sc-2">−</button>
            <input type="number" id="sc-2" min="0" max="99" value="9" inputmode="numeric" />
            <button type="button" data-step="1" data-target="sc-2">＋</button>
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
    el.innerHTML = '<div class="empty-state">Loading…</div>';
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
          : '<div class="empty-state"><span class="big">💬</span>No chats yet.<br>Add some friends and say hi!</div>';
        el.querySelectorAll('[data-thread]').forEach((row) => row.addEventListener('click', () => openThread(Number(row.dataset.thread))));
      } else if (state.chatSeg === 'nearby') {
        await renderNearbyPlayers(el);
      } else {
        await renderFriends(el);
      }
    } catch (e) {
      el.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
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
          const sub = p.checked_in_court
            ? `📍 At ${esc(p.checked_in_court.name)}${p.checked_in_court.looking_for_game ? ' · <b style="color:var(--green-700)">wants to play!</b>' : ''}`
            : `${skillLabel(p.skill_level)} · ${p.rating} · ${p.distance_miles} mi away`;
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
      : '<div class="empty-state"><span class="big">📍</span>No players near you yet.<br>Check in at a court so others can find you!</div>';

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
    const data = await api('/friends');
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

    el.innerHTML = html;

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
          <button type="submit">➤</button>
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
      } catch (err) { toast(err.message); }
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
          <button type="submit">➤</button>
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
      } catch (err) { toast(err.message); }
    });
  }

  // ---------- User profile ----------
  async function openUserProfile(userId) {
    let user;
    try { user = await api(`/users/${userId}`); } catch (e) { toast(e.message); return; }

    let friendAction = '';
    if (userId !== state.me.id) {
      if (user.friendship_status === 'accepted') {
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
      <div class="action-row">${friendAction}</div>
      ${games.length ? `<div class="section-label">Recent games</div>${games.map((g) => gameCardHtml(g, { compact: true })).join('')}` : ''}
    `);

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
      ${!window.matchMedia('(display-mode: standalone)').matches ? `
        <div class="card row" style="margin-bottom:10px">
          <span style="font-size:20px">📱</span>
          <div class="row-main">
            <div class="row-title" style="font-size:14px">Get the app feel</div>
            <div class="row-sub">In your browser menu tap <b>Add to Home Screen</b> — Picklepals installs like an app.</div>
          </div>
        </div>` : ''}
      <button class="btn btn-secondary btn-block" id="pf-edit" style="margin-bottom:10px">✏️ Edit profile</button>
      <button class="btn btn-secondary btn-block" id="pf-activity" style="margin-bottom:10px">🔔 Activity</button>
      <button class="btn btn-danger btn-block" id="pf-logout">Log out</button>
      <div id="pf-history"></div>
    `;

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
      await api('/checkout', { method: 'POST' });
      await refreshMe();
      renderProfile();
    });

    try {
      const history = await api('/games/history');
      if (history.items.length) {
        const historyEl = el.querySelector('#pf-history');
        historyEl.innerHTML =
          '<div class="section-label">Match history</div>' +
          history.items.map(resultRowHtml).join('');
        bindGameButtons(historyEl, renderProfile);
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
        <label>Avatar color</label>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${colors.map((c) => `<button type="button" class="avatar" data-color="${c}" style="background:${c};outline:${me.avatar_color === c ? '3px solid var(--ink)' : 'none'}">${esc(initials(me.display_name))}</button>`).join('')}
        </div>
      </div>
      <div class="form-field">
        <label>Home court</label>
        <input type="text" id="ep-court-search" placeholder="${me.home_court_name ? esc(me.home_court_name) : 'Search courts…'}" />
        <input type="hidden" id="ep-court-id" value="${me.home_court_id || ''}" />
        <div id="ep-court-results"></div>
      </div>
      <button class="btn btn-primary btn-block" id="ep-save">Save</button>
    `);

    let color = me.avatar_color;
    modal.querySelectorAll('[data-color]').forEach((b) => b.addEventListener('click', () => {
      color = b.dataset.color;
      modal.querySelectorAll('[data-color]').forEach((x) => { x.style.outline = x === b ? '3px solid var(--ink)' : 'none'; });
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

    modal.querySelector('#ep-save').addEventListener('click', async () => {
      try {
        const body = {
          display_name: modal.querySelector('#ep-name').value.trim(),
          bio: modal.querySelector('#ep-bio').value.trim(),
          skill_level: modal.querySelector('#ep-skill').value,
          avatar_color: color,
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
          <h3>${emoji} ${headline} ${game.game_type === 'ranked' ? '<span class="tag ranked" style="margin:0 0 0 6px">Ranked</span>' : '<span class="tag" style="margin:0 0 0 6px">Casual</span>'}</h3>
          <div class="row-sub">${subline}</div>
        </div>
        <button class="modal-close">✕</button>
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
      box.querySelectorAll('.modal-close').forEach((b) => { b.onclick = () => closeModal(modal); });
      box.querySelector('#gs-court')?.addEventListener('click', () => { closeModal(modal); openCourtDetail(court.id); });
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
    const modal = openModal(`
      ${modalHead('Activity')}
      ${enableBtn}
      ${data.items.length
        ? data.items.map((n) => `
            <div class="card row" data-notif-game="${n.related_game_id || ''}" style="${n.read ? 'opacity:.65' : ''}${n.related_game_id ? ';cursor:pointer' : ''}">
              <span style="font-size:20px">${{ friend_request: '🤝', friend_accept: '🎉', game_join: '🎾', game_cancelled: '🚫', ranked_result: '🏆', game_invite: '📅', game_invite_direct: '📨', score_submitted: '📝', score_confirmed: '✅', score_disputed: '⚠️', challenge: '⚔️', challenge_declined: '🙅' }[n.kind] || '🔔'}</span>
              <div class="row-main">
                <div class="row-title" style="font-size:14px">${esc(n.title)}</div>
                <div class="row-sub">${fmtDateTime(n.created_at)}</div>
              </div>
              ${n.related_game_id ? '<span class="chev">›</span>' : ''}
            </div>`).join('')
        : '<div class="empty-state"><span class="big">🔔</span>Nothing yet — go play some pickleball!</div>'}
    `);
    modal.querySelector('#act-enable')?.addEventListener('click', async (e) => {
      const result = await Notification.requestPermission();
      e.target.remove();
      toast(result === 'granted' ? 'Notifications on 🔔' : 'Notifications stay off');
    });
    modal.querySelectorAll('[data-notif-game]').forEach((row) => {
      const gameId = row.dataset.notifGame;
      if (!gameId) return;
      row.addEventListener('click', () => {
        closeModal(modal);
        openGameScreen(Number(gameId));
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
        await api('/checkout', { method: 'POST' });
        toast('Checked out 👋');
        await refreshMe();
        fetchCourtsInView();
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
    if (!state.me || state.me.home_lat != null) return;
    if (localStorage.getItem('pp_onboarded_home') === '1') return;
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
      if (ok) { closeModal(modal); fetchCourtsInView(); }
    });
  }

  async function showMain() {
    $('#auth-screen').classList.add('hidden');
    $('#main-screen').classList.remove('hidden');
    if (!state.map) setupMap();
    else setTimeout(() => state.map.invalidateSize(), 60);
    startLocationWatch();
    maybeOnboardHomeArea();
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

  function openDeepLink() {
    const match = location.hash.match(/^#court\/(\d+)$/);
    if (match) openCourtDetail(Number(match[1]));
  }

  async function boot() {
    if ('serviceWorker' in navigator && location.protocol === 'https:') {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    }
    setupAuth();
    setupTabs();
    setupPlay();
    setupChat();
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
