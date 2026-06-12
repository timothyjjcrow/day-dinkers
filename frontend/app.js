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
    playSeg: 'nearby',
    chatSeg: 'chats',
    map: null,
    markers: null,
    mapFilter: 'all',
    userLoc: null,
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
    const latest = data.latest_notification;
    if (latest) {
      if (state.lastNotifId !== null && latest.id > state.lastNotifId && !latest.read) {
        toast(`🔔 ${latest.title}`);
        if (state.tab === 'play') renderPlay();
      }
      state.lastNotifId = latest.id;
    } else if (state.lastNotifId === null) {
      state.lastNotifId = 0;
    }

    renderBadges();
    renderPresenceBanner();
  }

  function renderBadges() {
    const total = state.unreadMessages + state.pendingRequests;
    const badge = $('#chat-badge');
    badge.textContent = total > 99 ? '99+' : String(total);
    badge.classList.toggle('hidden', total === 0);

    const playBadge = $('#play-badge');
    playBadge.textContent = String(state.gamesToConfirm);
    playBadge.classList.toggle('hidden', state.gamesToConfirm === 0);
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
    state.map = L.map('map', { zoomControl: false })
      .setView(saved ? saved.center : DEFAULT_CENTER, saved ? saved.zoom : 11);
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
    $('#list-toggle').addEventListener('click', () => {
      $('#court-list').classList.toggle('hidden');
    });

    let searchTimer;
    $('#court-search').addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      const q = e.target.value.trim();
      searchTimer = setTimeout(() => q ? searchCourts(q) : fetchCourtsInView(), 350);
    });

    if (!saved) locateMe(true);
    fetchCourtsInView();
  }

  function locateMe(silent) {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        state.userLoc = [pos.coords.latitude, pos.coords.longitude];
        state.map.setView(state.userLoc, 12);
      },
      () => { if (!silent) toast('Could not get your location'); },
      { timeout: 8000 },
    );
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
      const data = await api(`/courts?q=${encodeURIComponent(q)}&limit=50`);
      state.courtsInView = data.items;
      drawMarkers(data.items);
      renderCourtList(data.items);
      $('#court-list').classList.remove('hidden');
      if (data.items.length) {
        const pts = data.items.filter((c) => c.latitude != null);
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
      const icon = L.divIcon({
        className: '',
        html: `<div class="court-marker ${busy ? 'busy' : ''}" style="width:${size}px;height:${size}px">${busy ? court.players_here + '👤' : court.num_courts}</div>`,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
      });
      L.marker([court.latitude, court.longitude], { icon })
        .addTo(state.markers)
        .on('click', () => openCourtDetail(court.id));
    });
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

  async function renderCourtList(courts) {
    const el = $('#court-list-items');
    let html = '';

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
  }

  // ---------- Modal helpers ----------
  function openModal(html) {
    const root = $('#overlay-root');
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.innerHTML = `<div class="modal">${html}</div>`;
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeModal(backdrop); });
    root.appendChild(backdrop);
    backdrop.querySelectorAll('.modal-close').forEach((b) => b.addEventListener('click', () => closeModal(backdrop)));
    return backdrop;
  }
  function closeModal(el) { el?.remove(); }
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
    const photoHtml = court.photo_url
      ? `<img class="court-photo" src="${esc(court.photo_url)}" alt="" onerror="this.outerHTML='<div class=\\'court-photo placeholder\\'>🥒</div>'">`
      : '<div class="court-photo placeholder">🥒</div>';
    const modal = openModal(`
      ${photoHtml}
      <div class="modal-head" style="padding-top:0">
        <div style="flex:1">
          <div class="detail-title">${esc(court.name)}</div>
          <div class="detail-sub">${esc([court.address, court.city].filter(Boolean).join(', '))}</div>
        </div>
        <button class="icon-btn" id="cd-share" title="Share court" style="box-shadow:none;font-size:17px">📤</button>
        <button class="modal-close">✕</button>
      </div>
      <div>${tags.map((t) => t.startsWith('<span') ? t : `<span class="tag">${t}</span>`).join('')}</div>
      <div class="action-row">
        <button class="btn ${checkedIn ? 'btn-danger' : 'btn-primary'}" id="cd-checkin">
          ${checkedIn ? 'Check out' : "I'm here — check in"}
        </button>
        <button class="btn btn-secondary" id="cd-favorite" style="flex:0 0 56px;font-size:19px">${isFavorite ? '★' : '☆'}</button>
      </div>
      <div class="action-row" style="margin-top:0">
        <button class="btn btn-secondary" id="cd-schedule">📅 Schedule game</button>
        <button class="btn" id="cd-schedule-ranked" style="background:#ede9fe;color:#5b21b6">🏆 Ranked game</button>
      </div>
      <div class="action-row" style="margin-top:0">
        <a class="btn btn-secondary" style="text-align:center;text-decoration:none" href="${mapsUrl}" target="_blank" rel="noopener">🧭 Directions</a>
        ${court.website ? `<a class="btn btn-secondary" style="text-align:center;text-decoration:none" href="${esc(court.website)}" target="_blank" rel="noopener">🌐 Website</a>` : ''}
      </div>
      ${court.open_play_schedule ? `<div class="section-label">Open play</div><div class="card" style="font-size:13.5px;color:var(--ink-soft)">${esc(court.open_play_schedule)}</div>` : ''}
      <div class="section-label">Playing now (${court.players_here.length})${court.friends_here ? ` · ${court.friends_here} friend${court.friends_here === 1 ? '' : 's'} here` : ''}</div>
      ${playersHtml}
      <div class="section-label">Upcoming games</div>
      ${gamesHtml}
      ${(court.recent_results || []).length ? `
        <div class="section-label">Recent results here</div>
        ${court.recent_results.map(resultCardHtml).join('')}` : ''}
    `);

    modal.querySelector('#cd-checkin').addEventListener('click', async () => {
      try {
        if (checkedIn) {
          await api('/checkout', { method: 'POST' });
          toast('Checked out');
        } else {
          const lfg = confirm('Looking for a game? OK = yes, Cancel = just playing');
          await api(`/courts/${court.id}/checkin`, {
            method: 'POST',
            body: JSON.stringify({ looking_for_game: lfg }),
          });
          toast(`Checked in at ${court.name}`);
        }
        closeModal(modal);
        await refreshMe();
        fetchCourtsInView();
      } catch (e) { toast(e.message); }
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
          await navigator.share({ title: 'Picklepals', text, url });
        } else {
          await navigator.clipboard.writeText(url);
          toast('Link copied 📋');
        }
      } catch { /* user cancelled share */ }
    });

    modal.querySelector('#cd-schedule').addEventListener('click', () => {
      closeModal(modal);
      openNewGameModal(court, 'casual');
    });
    modal.querySelector('#cd-schedule-ranked').addEventListener('click', () => {
      closeModal(modal);
      openNewGameModal(court, 'ranked');
    });

    modal.querySelector('#cd-favorite').addEventListener('click', async (e) => {
      try {
        const data = await api(`/courts/${court.id}/favorite`, { method: 'POST' });
        isFavorite = data.favorited;
        e.currentTarget.textContent = isFavorite ? '★' : '☆';
        toast(isFavorite ? 'Court saved ⭐' : 'Removed from saved courts');
      } catch (err) { toast(err.message); }
    });

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

  // ---------- Games ----------
  function gameCardHtml(game, { compact = false } = {}) {
    const court = game.court || {};
    const typeTag = game.game_type === 'ranked'
      ? '<span class="tag ranked" style="margin:0 0 0 8px">🏆 Ranked</span>'
      : '<span class="tag" style="margin:0 0 0 8px">Casual</span>';
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
        action = `<button class="btn btn-secondary btn-sm" data-game-leave="${game.id}">Leave</button>`;
        if (game.players.length >= 2) {
          action += ` <button class="btn btn-primary btn-sm" data-game-score="${game.id}">Enter score</button>`;
        }
        if (inProgress) {
          banner = '<div class="status-banner live-banner">🟢 Game time! Enter the score when you\'re done.</div>';
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
      if (game.is_joined) {
        action += ` <button class="btn btn-secondary btn-sm" data-game-rematch="${game.id}" data-rematch-type="${game.game_type}">↺ Rematch</button>`;
      }
    }

    return `
      <div class="card" style="${cardStyle}">
        <div class="row" style="margin-bottom:8px">
          <div class="row-main">
            <div class="row-title">${esc(fmtDateTime(game.scheduled_at))}${typeTag}</div>
            <div class="row-sub">${esc(court.name || '')}${!compact && court.city ? ` · ${esc(court.city)}` : ''}${game.distance_miles != null ? ` · ${game.distance_miles} mi` : ''}${hostLabel}</div>
          </div>
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
    rootEl.querySelectorAll('[data-game-join]').forEach((b) => b.addEventListener('click', async () => {
      try { await api(`/games/${b.dataset.gameJoin}/join`, { method: 'POST' }); toast('You joined the game! 🎾'); refresh(); }
      catch (e) { toast(e.message); }
    }));
    rootEl.querySelectorAll('[data-game-leave]').forEach((b) => b.addEventListener('click', async () => {
      try { await api(`/games/${b.dataset.gameLeave}/leave`, { method: 'POST' }); toast('Left the game'); refresh(); }
      catch (e) { toast(e.message); }
    }));
    rootEl.querySelectorAll('[data-game-score]').forEach((b) => b.addEventListener('click', async () => {
      try {
        const game = await api(`/games/${b.dataset.gameScore}`);
        openScoreModal(game, refresh);
      } catch (e) { toast(e.message); }
    }));
    rootEl.querySelectorAll('[data-game-confirm]').forEach((b) => b.addEventListener('click', async () => {
      try {
        const game = await api(`/games/${b.dataset.gameConfirm}/confirm`, { method: 'POST' });
        showCelebration(game);
        refreshMe();
        refresh();
      } catch (e) { toast(e.message); }
    }));
    rootEl.querySelectorAll('[data-game-rematch]').forEach((b) => b.addEventListener('click', async () => {
      try {
        const game = await api(`/games/${b.dataset.gameRematch}`);
        if (game.court) openNewGameModal(game.court, b.dataset.rematchType || 'casual');
      } catch (e) { toast(e.message); }
    }));
    rootEl.querySelectorAll('[data-game-dispute]').forEach((b) => b.addEventListener('click', async () => {
      if (!confirm('Dispute this score? It will be cleared so it can be re-entered.')) return;
      try {
        await api(`/games/${b.dataset.gameDispute}/dispute`, { method: 'POST' });
        toast('Score disputed — enter the correct one together');
        refreshMe();
        refresh();
      } catch (e) { toast(e.message); }
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

  function resultCardHtml(game) {
    const court = game.court || {};
    const team1 = game.players.filter((p) => p.team === 1);
    const team2 = game.players.filter((p) => p.team === 2);
    const team1Won = game.score_team1 > game.score_team2;
    const names = (team) => team.map((p) =>
      `<span data-view-user="${p.user_id}" style="cursor:pointer">${esc(p.display_name)}${p.rating_delta != null ? ` <span class="${p.rating_delta >= 0 ? 'delta-up' : 'delta-down'}" style="font-size:12px">${p.rating_delta >= 0 ? '+' : ''}${p.rating_delta}</span>` : ''}</span>`
    ).join(' & ') || '—';
    const sideHtml = (team, score, won) => `
      <div class="result-side ${won ? 'won' : ''}">
        <div class="result-names">${won ? '🏆 ' : ''}${names(team)}</div>
        <div class="result-score">${score}</div>
      </div>`;
    const tags = [];
    if (game.game_type === 'ranked') tags.push('<span class="tag ranked" style="margin:0">🏆 Ranked</span>');
    if (game.involves_me) tags.push('<span class="tag" style="margin:0">You played</span>');
    else if (game.involves_friend) tags.push('<span class="tag" style="margin:0">🤝 Friend</span>');
    return `
      <div class="card">
        <div class="row-sub" style="margin-bottom:8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          ${tags.join('')}
          <span>${esc(court.name || '')} · ${fmtDateTime(game.completed_at)}</span>
        </div>
        ${sideHtml(team1, game.score_team1, team1Won)}
        ${sideHtml(team2, game.score_team2, !team1Won)}
      </div>`;
  }

  async function renderPlay() {
    const seg = state.playSeg;
    const el = $('#play-content');
    el.innerHTML = '<div class="empty-state">Loading…</div>';
    try {
      if (seg === 'rankings') {
        const data = await api('/leaderboard');
        el.innerHTML = data.items.length
          ? data.items.map((u, i) => `
              <div class="card row">
                <div class="rank-num">${i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : i + 1}</div>
                ${avatarHtml(u)}
                <div class="row-main">
                  <div class="row-title">${esc(u.display_name)}${u.current_streak >= 2 ? ` <span title="Win streak">🔥${u.current_streak}</span>` : ''}</div>
                  <div class="row-sub">${u.ranked_wins}W – ${u.ranked_losses}L · ${skillLabel(u.skill_level)}</div>
                </div>
                <div class="stat-value">${u.rating}</div>
              </div>`).join('')
          : '<div class="empty-state"><span class="big">🏆</span>No ranked games yet.<br>Schedule a ranked game and record the score to get on the board!</div>';
        bindUserButtons(el);
        return;
      }

      if (seg === 'results') {
        let url = '/games/results';
        const c = state.userLoc
          ? { lat: state.userLoc[0], lng: state.userLoc[1] }
          : (state.map ? state.map.getCenter() : { lat: DEFAULT_CENTER[0], lng: DEFAULT_CENTER[1] });
        url += `?lat=${c.lat}&lng=${c.lng}`;
        const data = await api(url);
        el.innerHTML = data.items.length
          ? data.items.map(resultCardHtml).join('')
          : '<div class="empty-state"><span class="big">📋</span>No finished games around here yet.<br>Play one and it\'ll show up!</div>';
        bindUserButtons(el);
        return;
      }

      let url = '/games';
      if (seg === 'mine') url += '?mine=1';
      else if (state.userLoc) url += `?lat=${state.userLoc[0]}&lng=${state.userLoc[1]}&radius=60`;
      else {
        const c = state.map ? state.map.getCenter() : { lat: DEFAULT_CENTER[0], lng: DEFAULT_CENTER[1] };
        url += `?lat=${c.lat}&lng=${c.lng}&radius=60`;
      }
      const data = await api(url);
      let html;

      if (seg === 'mine') {
        // Action first: scores to confirm, then live/upcoming, then history.
        const needsAction = data.items.filter((g) => g.awaiting_your_confirmation);
        const rest = data.items.filter((g) => !g.awaiting_your_confirmation);
        html = '';
        if (needsAction.length) {
          html += '<div class="section-label" style="margin-top:6px">⚡ Needs your confirmation</div>';
          html += needsAction.map((g) => gameCardHtml(g)).join('');
        }
        if (rest.length) {
          if (needsAction.length) html += '<div class="section-label">Upcoming</div>';
          html += rest.map((g) => gameCardHtml(g)).join('');
        }
        const history = await api('/games/history');
        if (history.items.length) {
          html += '<div class="section-label">Past games</div>';
          html += history.items.map((g) => gameCardHtml(g)).join('');
        }
      } else {
        html = data.items.map((g) => gameCardHtml(g)).join('');
      }
      el.innerHTML = html || `<div class="empty-state"><span class="big">🎾</span>${
        seg === 'mine'
          ? 'You haven\'t joined any games yet.<br>Find one nearby or tap ＋ to schedule your own!'
          : 'No upcoming games in your area yet.<br>Tap ＋ to schedule the first one!'
      }</div>`;
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

  function openNewGameModal(court, defaultType = 'casual') {
    const now = new Date();
    now.setHours(now.getHours() + 2, 0, 0, 0);
    const pad = (n) => String(n).padStart(2, '0');
    const defaultDt = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:00`;

    const modal = openModal(`
      ${modalHead('Schedule a game')}
      <div class="form-field">
        <label>Court</label>
        <input type="text" id="ng-court-search" placeholder="Search for a court…" value="${court ? esc(court.name) : ''}" ${court ? 'disabled' : ''} />
        <input type="hidden" id="ng-court-id" value="${court ? court.id : ''}" />
        <div id="ng-court-results"></div>
      </div>
      <div class="form-grid">
        <div class="form-field">
          <label>When</label>
          <input type="datetime-local" id="ng-when" value="${defaultDt}" />
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
      <div class="quick-times" id="ng-quick" style="margin:-6px 0 14px">
        <button type="button" data-q="tonight">Tonight 6 PM</button>
        <button type="button" data-q="tomorrow">Tomorrow 9 AM</button>
        <button type="button" data-q="weekend">Saturday 9 AM</button>
      </div>
      <div class="form-field">
        <label>Type</label>
        <div class="segmented" id="ng-type">
          <button ${defaultType === 'casual' ? 'class="active"' : ''} data-val="casual">Casual</button>
          <button ${defaultType === 'ranked' ? 'class="active"' : ''} data-val="ranked">🏆 Ranked</button>
        </div>
        <p class="row-sub" id="ng-type-hint" style="margin-top:6px${defaultType === 'ranked' ? '' : ';display:none'}">Ranked games count toward player ratings.</p>
      </div>
      <div class="form-field">
        <label>Note (optional)</label>
        <input type="text" id="ng-notes" maxlength="200" placeholder="e.g. All levels welcome!" />
      </div>
      <label class="row" style="margin-bottom:14px;cursor:pointer">
        <input type="checkbox" id="ng-notify" checked style="width:auto" />
        <span style="font-size:14px">Let my friends know 🔔</span>
      </label>
      <button class="btn btn-primary btn-block" id="ng-submit">Schedule ${defaultType === 'ranked' ? 'ranked ' : ''}game</button>
    `);

    modal.querySelector('#ng-quick').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      const d = new Date();
      if (btn.dataset.q === 'tonight') {
        if (d.getHours() >= 18) d.setDate(d.getDate() + 1);
        d.setHours(18, 0, 0, 0);
      } else if (btn.dataset.q === 'tomorrow') {
        d.setDate(d.getDate() + 1);
        d.setHours(9, 0, 0, 0);
      } else {
        const daysToSat = ((6 - d.getDay()) + 7) % 7 || 7;
        d.setDate(d.getDate() + daysToSat);
        d.setHours(9, 0, 0, 0);
      }
      const pad2 = (n) => String(n).padStart(2, '0');
      modal.querySelector('#ng-when').value =
        `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
    });

    let gameType = defaultType;
    modal.querySelector('#ng-type').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      gameType = btn.dataset.val;
      modal.querySelectorAll('#ng-type button').forEach((b) => b.classList.toggle('active', b === btn));
      modal.querySelector('#ng-type-hint').style.display = gameType === 'ranked' ? '' : 'none';
    });

    if (!court) {
      let timer;
      const searchInput = modal.querySelector('#ng-court-search');
      searchInput.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          const q = searchInput.value.trim();
          if (q.length < 2) return;
          let url = `/courts?q=${encodeURIComponent(q)}&limit=6`;
          if (state.userLoc) url += `&lat=${state.userLoc[0]}&lng=${state.userLoc[1]}`;
          const data = await api(url);
          modal.querySelector('#ng-court-results').innerHTML = data.items.map((c) => `
            <div class="card row" data-pick-court="${c.id}" data-pick-name="${esc(c.name)}" style="cursor:pointer;margin:6px 0;padding:10px">
              <div class="row-main">
                <div class="row-title" style="font-size:14px">${esc(c.name)}</div>
                <div class="row-sub">${esc(c.city)}${c.distance_miles != null ? ` · ${c.distance_miles} mi` : ''}</div>
              </div>
            </div>`).join('');
          modal.querySelectorAll('[data-pick-court]').forEach((row) => row.addEventListener('click', () => {
            modal.querySelector('#ng-court-id').value = row.dataset.pickCourt;
            searchInput.value = row.dataset.pickName;
            modal.querySelector('#ng-court-results').innerHTML = '';
          }));
        }, 300);
      });
    }

    modal.querySelector('#ng-submit').addEventListener('click', async (e) => {
      const courtId = modal.querySelector('#ng-court-id').value;
      const when = modal.querySelector('#ng-when').value;
      if (!courtId) { toast('Pick a court first'); return; }
      if (!when) { toast('Pick a date and time'); return; }
      const btn = e.currentTarget;
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        await api('/games', {
          method: 'POST',
          body: JSON.stringify({
            court_id: Number(courtId),
            scheduled_at: new Date(when).toISOString(),
            game_type: gameType,
            max_players: Number(modal.querySelector('#ng-max').value),
            notes: modal.querySelector('#ng-notes').value.trim(),
            notify_friends: modal.querySelector('#ng-notify').checked,
          }),
        });
        closeModal(modal);
        toast('Game scheduled! 🎾');
        if (state.tab === 'play') renderPlay();
      } catch (err) { toast(err.message); btn.disabled = false; }
    });
  }

  function openScoreModal(game, refresh) {
    const players = game.players;
    const singles = players.length === 2;
    const half = Math.ceil(players.length / 2);
    const checkboxes = (team) => players.map((p, i) => `
      <label class="row" style="margin-bottom:6px;cursor:pointer">
        <input type="checkbox" style="width:auto" name="team${team}" value="${p.user_id}" ${team === 1 && i < half ? 'checked' : ''}${team === 2 && i >= half ? 'checked' : ''} />
        ${avatarHtml(p, 'sm')} <span>${esc(p.display_name)}</span>
      </label>`).join('');

    // Singles: no team picking — it's just player vs player.
    const teamsHtml = singles
      ? `<input type="checkbox" class="hidden" name="team1" value="${players[0].user_id}" checked />
         <input type="checkbox" class="hidden" name="team2" value="${players[1].user_id}" checked />`
      : `<div class="form-grid">
          <div class="form-field"><label>Team 1</label>${checkboxes(1)}</div>
          <div class="form-field"><label>Team 2</label>${checkboxes(2)}</div>
        </div>`;
    const scoreLabel1 = singles ? esc(players[0].display_name) : 'Team 1 score';
    const scoreLabel2 = singles ? esc(players[1].display_name) : 'Team 2 score';

    const modal = openModal(`
      ${modalHead(`Record score${game.game_type === 'ranked' ? ' (ranked)' : ''}`)}
      ${teamsHtml}
      <div class="form-grid">
        <div class="form-field"><label>${scoreLabel1}</label><input type="number" id="sc-1" min="0" max="99" value="11" /></div>
        <div class="form-field"><label>${scoreLabel2}</label><input type="number" id="sc-2" min="0" max="99" value="9" /></div>
      </div>
      ${game.game_type === 'ranked' ? '<p class="row-sub" style="margin-bottom:12px">🏆 Ranked: an opponent confirms the score, then ratings update.</p>' : ''}
      <button class="btn btn-primary btn-block" id="sc-submit">Save result</button>
    `);

    modal.querySelector('#sc-submit').addEventListener('click', async (e) => {
      const team1 = [...modal.querySelectorAll('input[name="team1"]:checked')].map((i) => Number(i.value));
      const team2 = [...modal.querySelectorAll('input[name="team2"]:checked')].map((i) => Number(i.value));
      if (!team1.length || !team2.length) { toast('Each team needs at least one player'); return; }
      if (team1.some((id) => team2.includes(id))) { toast('A player can\'t be on both teams'); return; }
      const btn = e.currentTarget;
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        const updated = await api(`/games/${game.id}/complete`, {
          method: 'POST',
          body: JSON.stringify({
            team1, team2,
            score_team1: Number(modal.querySelector('#sc-1').value),
            score_team2: Number(modal.querySelector('#sc-2').value),
          }),
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
      } else {
        await renderFriends(el);
      }
    } catch (e) {
      el.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
    }
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
      <div class="thread" style="height:84dvh;margin:-10px -18px -28px">
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
    `);

    const msgsEl = modal.querySelector('#thread-msgs');
    let lastId = 0;
    const renderMsgs = (items, append) => {
      const html = items.map((m) => `
        <div class="bubble ${m.sender_id === state.me.id ? 'me' : 'them'}">
          ${esc(m.body)}
          <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
        </div>`).join('');
      if (append) msgsEl.insertAdjacentHTML('beforeend', html);
      else msgsEl.innerHTML = html || '<div class="empty-state" style="padding:20px">Say hi! 👋</div>';
      if (items.length) lastId = items[items.length - 1].id;
      msgsEl.scrollTop = msgsEl.scrollHeight;
    };
    renderMsgs(data.items, false);
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
      <button class="btn btn-secondary btn-block" id="pf-edit" style="margin-bottom:10px">✏️ Edit profile</button>
      <button class="btn btn-secondary btn-block" id="pf-activity" style="margin-bottom:10px">🔔 Activity</button>
      <button class="btn btn-danger btn-block" id="pf-logout">Log out</button>
      <div id="pf-history"></div>
    `;

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
        el.querySelector('#pf-history').innerHTML =
          '<div class="section-label">Match history</div>' +
          history.items.map((g) => {
            const myPlayer = g.players.find((p) => p.user_id === me.id);
            const delta = myPlayer && myPlayer.rating_delta != null
              ? ` <span class="${myPlayer.rating_delta >= 0 ? 'delta-up' : 'delta-down'}">${myPlayer.rating_delta >= 0 ? '+' : ''}${myPlayer.rating_delta}</span>`
              : '';
            return `
              <div class="card row">
                <div class="row-main">
                  <div class="row-title">${esc(g.court ? g.court.name : 'Game')}${delta}</div>
                  <div class="row-sub">${fmtDateTime(g.completed_at)} · ${g.score_team1}–${g.score_team2} · ${g.game_type === 'ranked' ? '🏆 Ranked' : 'Casual'}</div>
                </div>
              </div>`;
          }).join('');
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

  async function openActivity() {
    let data;
    try { data = await api('/notifications'); } catch (e) { toast(e.message); return; }
    const modal = openModal(`
      ${modalHead('Activity')}
      ${data.items.length
        ? data.items.map((n) => `
            <div class="card row" style="${n.read ? 'opacity:.65' : ''}">
              <span style="font-size:20px">${{ friend_request: '🤝', friend_accept: '🎉', game_join: '🎾', game_cancelled: '🚫', ranked_result: '🏆', game_invite: '📅', score_submitted: '📝', score_confirmed: '✅', score_disputed: '⚠️' }[n.kind] || '🔔'}</span>
              <div class="row-main">
                <div class="row-title" style="font-size:14px">${esc(n.title)}</div>
                <div class="row-sub">${fmtDateTime(n.created_at)}</div>
              </div>
            </div>`).join('')
        : '<div class="empty-state"><span class="big">🔔</span>Nothing yet — go play some pickleball!</div>'}
    `);
    if (data.unread) {
      api('/notifications/read', { method: 'POST' }).then(refreshMe).catch(() => {});
    }
    void modal;
  }

  // ---------- Presence banner ----------
  function renderPresenceBanner() {
    const el = $('#presence-banner');
    if (state.presence && state.presence.checked_in) {
      el.innerHTML = `📍 You're at <b>&nbsp;${esc(state.presence.court_name)}</b>
        <button id="banner-checkout">Check out</button>`;
      el.classList.remove('hidden');
      $('#banner-checkout').addEventListener('click', async () => {
        await api('/checkout', { method: 'POST' });
        await refreshMe();
        fetchCourtsInView();
      });
    } else {
      el.classList.add('hidden');
    }
  }

  // ---------- Boot ----------
  async function showMain() {
    $('#auth-screen').classList.add('hidden');
    $('#main-screen').classList.remove('hidden');
    if (!state.map) setupMap();
    else setTimeout(() => state.map.invalidateSize(), 60);
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
