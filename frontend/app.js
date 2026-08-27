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
    communityRoomUnread: 0,
    gamesToConfirm: 0,
    lastNotifId: null,
    tab: 'play',
    playSeg: 'games',
    chatSeg: 'chats',
    nearbySkill: '',
    map: null,
    markers: null,
    courtFilters: {
      saved: false,
      players: false,
      games: false,
      indoor: false,
      lighted: false,
      nets: false,
      restrooms: false,
      water: false,
    },
    listSort: 'distance',
    courtSheetSnap: 'peek',
    courtListLimit: 20,
    courtListSignature: '',
    courtListExpandedScrollTop: 0,
    courtListPlaces: [],
    courtListSavedOnly: false,
    selectedCourtId: null,
    courtMarkers: new Map(),
    courtFetchSeq: 0,
    suppressCourtMoveFetch: false,
    courtMoveSuppressSeq: 0,
    favIds: null, // Set of favorited court ids, loaded lazily for map stars
    userDot: null,
    geoWatchId: null,
    lastAutoCheckAt: 0,
    userLoc: null,
    areaLoc: null,
    areaLabel: null,
    snapshotAreaProvisional: false,
    courtsInView: [],
    activeThreadUserId: null,
    threadPollTimer: null,
    mePollTimer: null,
    playRenderSeq: 0,
    chatRenderSeq: 0,
    connectionState: navigator.onLine ? 'online' : 'offline',
    playGamesCache: null,
    chatFriendsCache: null,
  };
  const pageNotifications = new Set();

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

  // Game plans are deliberately session-only: enough continuity for an
  // accidental close/reload, without leaving social plans on a shared device.
  const GAME_DRAFT_VERSION = 1;
  const GAME_DRAFT_TTL = 24 * 60 * 60 * 1000;
  const gameDraftKey = (userId = state.me && state.me.id) => userId ? `pp_game_draft_v1:${userId}` : null;
  function newGameAttemptId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') return globalThis.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    if (globalThis.crypto && typeof globalThis.crypto.getRandomValues === 'function') globalThis.crypto.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
    return [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('');
  }
  function readGameDraft() {
    const key = gameDraftKey();
    if (!key) return null;
    try {
      const raw = JSON.parse(sessionStorage.getItem(key) || 'null');
      if (!raw || raw.v !== GAME_DRAFT_VERSION || !Number.isFinite(raw.updatedAt)
          || raw.updatedAt > Date.now() + 60000 || Date.now() - raw.updatedAt > GAME_DRAFT_TTL) {
        sessionStorage.removeItem(key);
        return null;
      }
      const allowed = (value, values, fallback) => values.includes(value) ? value : fallback;
      const id = (value) => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : null;
      return {
        v: GAME_DRAFT_VERSION,
        updatedAt: raw.updatedAt,
        status: raw.status === 'submitting' ? 'submitting' : 'editing',
        submitStartedAt: Number.isFinite(raw.submitStartedAt) ? raw.submitStartedAt : null,
        clientAttemptId: typeof raw.clientAttemptId === 'string' && /^[a-zA-Z0-9_-]{16,80}$/.test(raw.clientAttemptId)
          ? raw.clientAttemptId : null,
        mode: allowed(raw.mode, ['now', 'later'], 'later'),
        courtId: id(raw.courtId),
        scheduledAt: typeof raw.scheduledAt === 'string' ? raw.scheduledAt : null,
        timeKind: allowed(raw.timeKind, ['preset', 'custom'], 'preset'),
        visibility: allowed(raw.visibility, ['open', 'friends', 'private'], 'open'),
        inviteUserIds: [...new Set(Array.isArray(raw.inviteUserIds) ? raw.inviteUserIds.map(id).filter(Boolean) : [])].slice(0, 20),
        gameType: allowed(raw.gameType, ['casual', 'ranked'], 'casual'),
        maxPlayers: [2, 4, 6, 8].includes(Number(raw.maxPlayers)) ? Number(raw.maxPlayers) : 4,
        preferredLevel: allowed(raw.preferredLevel, ['any', 'beginner', 'intermediate', 'advanced', 'pro'], 'any'),
        clubId: id(raw.clubId),
        recurrence: allowed(raw.recurrence, ['none', 'weekly'], 'none'),
        notes: String(raw.notes || '').slice(0, 200),
        advancedOpen: !!raw.advancedOpen,
      };
    } catch {
      try { sessionStorage.removeItem(key); } catch { /* storage unavailable */ }
      return null;
    }
  }
  function writeGameDraft(draft) {
    const key = gameDraftKey();
    if (!key) return;
    try { sessionStorage.setItem(key, JSON.stringify({ ...draft, v: GAME_DRAFT_VERSION, updatedAt: Date.now() })); } catch { /* planner still works */ }
  }
  function clearGameDraft(userId = state.me && state.me.id) {
    const key = gameDraftKey(userId);
    if (!key) return;
    try { sessionStorage.removeItem(key); } catch { /* storage unavailable */ }
  }

  // ---------- API ----------
  async function api(path, options = {}) {
    const { timeoutMs = 15000, ...requestOptions } = options;
    const headers = { 'Content-Type': 'application/json', ...(requestOptions.headers || {}) };
    const requestToken = state.token;
    if (requestToken) headers.Authorization = `Bearer ${requestToken}`;
    const assertCurrentSession = () => {
      if (!requestToken || state.token === requestToken) return;
      const stale = new Error('Ignored a response from an earlier session');
      stale.code = 'stale_session';
      stale.isStaleSession = true;
      throw stale;
    };
    const controller = requestOptions.signal ? null : new AbortController();
    const timeout = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
    let res;
    try {
      res = await fetch(`/api${path}`, { ...requestOptions, headers, signal: requestOptions.signal || controller.signal });
      setConnectionState('online');
    } catch (cause) {
      assertCurrentSession();
      const timedOut = cause && cause.name === 'AbortError';
      if (!timedOut) setConnectionState('offline');
      const err = new Error(timedOut ? 'That took too long — try again.' : navigator.onLine
        ? 'The connection is taking a break — try again.'
        : "You're offline — reconnect to continue.");
      err.isNetworkError = true;
      err.cause = cause;
      throw err;
    } finally {
      if (timeout) clearTimeout(timeout);
    }
    assertCurrentSession();
    let data = null;
    try { data = await res.json(); } catch { /* empty body */ }
    if (res.status === 401 && requestToken && state.token === requestToken && !path.startsWith('/auth')) {
      logout();
      throw new Error('Session expired — please log in again');
    }
    if (!res.ok) {
      const code = (data && data.error) || `error_${res.status}`;
      const err = new Error(humanError(code));
      err.code = code;
      err.status = res.status;
      err.data = data;
      throw err;
    }
    assertCurrentSession();
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
    stale_result: 'This result changed on another device. Review the latest version and try again.',
    reason_required: 'Add a short reason so everyone understands the decision.',
    invalid_result_version: 'This result needs a refresh before it can be changed.',
    result_not_reportable: 'This result is already being reviewed or finalized.',
    result_not_awaiting_confirmation: 'This score is no longer waiting for confirmation.',
    unresolved_results: 'Confirm, resolve, or void the submitted results before continuing.',
    next_match_played: 'This result cannot change because the next match has already been played.',
    opponent_confirmation_required: 'Someone from the other side must confirm this score.',
    not_allowed: 'You do not have permission to change this result.',
    not_organizer: 'Only the organizer can make that decision.',
    players_only: 'Only players in this match can report its score.',
    round_closed: 'This round is already closed.',
    tournament_not_active: 'This tournament is no longer accepting match results.',
    nothing_to_resolve: 'This result no longer needs an organizer decision.',
    invalid_score: 'Enter two different scores from 0 to 99.',
    invalid_scores: 'Enter two different scores from 0 to 99.',
    scores_required: 'Enter both scores.',
    score_missing: 'The submitted score is missing. Refresh and try again.',
    match_not_ready: 'This match is not ready for a score yet.',
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

  // Shared, persistent validation and pending state for modal forms. Errors stay
  // beside the action until the invalid control is edited, and every async
  // submission gets the same double-submit guard and screen-reader busy state.
  let modalFormErrorSeq = 0;
  function bindModalFormUX(modal, submitSelector, { draftKey = null } = {}) {
    const submitButton = typeof submitSelector === 'string'
      ? modal.querySelector(submitSelector) : submitSelector;
    if (!submitButton) throw new Error('Modal form submit button not found');
    const form = submitButton.closest('form');

    // Long mobile forms keep a session-only, non-sensitive draft. Hidden
    // selector IDs and passwords are intentionally excluded: after restoring,
    // people review location/chip choices instead of submitting a stale value.
    const draftStorageKey = draftKey && state.me
      ? `pp_form_draft_v1:${state.me.id}:${draftKey}` : null;
    const draftControls = () => form ? [...form.querySelectorAll('input[id], textarea[id], select[id]')]
      .filter((control) => !['password', 'file', 'hidden', 'submit', 'button'].includes(control.type)) : [];
    const collectDraftFields = () => Object.fromEntries(draftControls().map((control) => [
      control.id,
      control.type === 'checkbox' || control.type === 'radio' ? control.checked : control.value,
    ]));
    const applyDraftFields = (fields) => {
      draftControls().forEach((control) => {
        if (!Object.prototype.hasOwnProperty.call(fields, control.id)) return;
        if (control.type === 'checkbox' || control.type === 'radio') control.checked = !!fields[control.id];
        else control.value = String(fields[control.id] ?? '');
      });
    };
    const initialDraftFields = collectDraftFields();
    let draftRecovery = null;
    let draftTimer = null;
    let draftDisabled = false;
    const clearDraft = ({ disable = false } = {}) => {
      clearTimeout(draftTimer);
      draftTimer = null;
      if (disable) draftDisabled = true;
      if (draftStorageKey) {
        try { sessionStorage.removeItem(draftStorageKey); } catch { /* storage unavailable */ }
      }
      draftRecovery?.remove();
      draftRecovery = null;
    };
    const writeDraftNow = () => {
      draftTimer = null;
      if (draftDisabled || !draftStorageKey || !form) return;
      const fields = collectDraftFields();
      try {
        if (JSON.stringify(fields) === JSON.stringify(initialDraftFields)) sessionStorage.removeItem(draftStorageKey);
        else sessionStorage.setItem(draftStorageKey, JSON.stringify({ v: 1, updatedAt: Date.now(), fields }));
      } catch { /* private mode/storage pressure must never block a form */ }
    };
    const persistDraft = () => {
      if (draftDisabled || !draftStorageKey || !form) return;
      clearTimeout(draftTimer);
      draftTimer = setTimeout(writeDraftNow, 120);
    };
    if (draftStorageKey && form) {
      try {
        const saved = JSON.parse(sessionStorage.getItem(draftStorageKey) || 'null');
        if (saved && saved.v === 1 && saved.fields && typeof saved.fields === 'object'
            && Number.isFinite(saved.updatedAt) && saved.updatedAt <= Date.now() + 60000
            && Date.now() - saved.updatedAt < 24 * 60 * 60 * 1000) {
          applyDraftFields(saved.fields);
          draftRecovery = document.createElement('div');
          draftRecovery.className = 'form-draft-recovery';
          draftRecovery.setAttribute('role', 'status');
          draftRecovery.innerHTML = '<span><b>Draft restored.</b> Review choices, then continue.</span><button type="button" class="btn btn-secondary btn-sm">Start over</button>';
          form.prepend(draftRecovery);
          draftRecovery.querySelector('button').addEventListener('click', () => {
            applyDraftFields(initialDraftFields);
            clearDraft();
            draftControls().forEach((control) => {
              control.dispatchEvent(new Event('input', { bubbles: true }));
              control.dispatchEvent(new Event('change', { bubbles: true }));
            });
          });
          queueMicrotask(() => draftControls().forEach((control) => {
            control.dispatchEvent(new Event('input', { bubbles: true }));
            control.dispatchEvent(new Event('change', { bubbles: true }));
          }));
        } else if (saved) {
          sessionStorage.removeItem(draftStorageKey);
        }
      } catch {
        try { sessionStorage.removeItem(draftStorageKey); } catch { /* storage unavailable */ }
      }
      form.addEventListener('input', persistDraft);
      form.addEventListener('change', persistDraft);
      modal._cleanupFns?.push(() => {
        if (draftTimer != null) {
          clearTimeout(draftTimer);
          writeDraftNow();
        }
      });
    }

    const error = document.createElement('p');
    error.id = `modal-form-error-${++modalFormErrorSeq}`;
    error.className = 'form-error modal-form-error hidden';
    error.dataset.modalFormError = 'true';
    error.setAttribute('role', 'alert');
    error.setAttribute('aria-live', 'assertive');
    error.tabIndex = -1;
    submitButton.insertAdjacentElement('beforebegin', error);

    const invalidTargets = new Set();
    const clearTarget = (target) => {
      if (!target || target.dataset.modalFormInvalid !== error.id) return;
      target.removeAttribute('aria-invalid');
      delete target.dataset.modalFormInvalid;
      const describedBy = (target.getAttribute('aria-describedby') || '')
        .split(/\s+/).filter((id) => id && id !== error.id);
      if (describedBy.length) target.setAttribute('aria-describedby', describedBy.join(' '));
      else target.removeAttribute('aria-describedby');
      invalidTargets.delete(target);
    };
    const clearError = () => {
      [...invalidTargets].forEach(clearTarget);
      error.textContent = '';
      error.classList.add('hidden');
    };
    const showError = (message, target = null) => {
      clearError();
      const field = target?.closest('.form-field');
      if (field) field.appendChild(error);
      else submitButton.insertAdjacentElement('beforebegin', error);
      error.textContent = message;
      error.classList.remove('hidden');
      if (target) {
        target.dataset.modalFormInvalid = error.id;
        target.setAttribute('aria-invalid', 'true');
        const describedBy = new Set((target.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
        describedBy.add(error.id);
        target.setAttribute('aria-describedby', [...describedBy].join(' '));
        invalidTargets.add(target);
        target.scrollIntoView({ block: 'center', behavior: 'auto' });
        target.focus({ preventScroll: true });
      } else {
        error.scrollIntoView({ block: 'center', behavior: 'auto' });
        error.focus({ preventScroll: true });
      }
    };
    const clearEditedError = (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement) || target.dataset.modalFormInvalid !== error.id) return;
      clearTarget(target);
      error.textContent = '';
      error.classList.add('hidden');
    };
    modal.addEventListener('input', clearEditedError);
    modal.addEventListener('change', clearEditedError);

    const startSubmitting = (pendingLabel, activeButton = submitButton) => {
      const actionButton = activeButton || submitButton;
      if (actionButton.disabled || actionButton.dataset.submitting === 'true') return null;
      clearError();
      const busyRegion = form || modal.querySelector('.modal');
      const originalLabel = actionButton.textContent;
      actionButton.dataset.submitting = 'true';
      actionButton.disabled = true;
      actionButton.setAttribute('aria-busy', 'true');
      actionButton.textContent = pendingLabel;
      busyRegion?.setAttribute('aria-busy', 'true');
      let finished = false;
      return () => {
        if (finished) return;
        finished = true;
        delete actionButton.dataset.submitting;
        actionButton.disabled = false;
        actionButton.removeAttribute('aria-busy');
        actionButton.textContent = originalLabel;
        busyRegion?.removeAttribute('aria-busy');
      };
    };

    return { clearDraft, clearError, showError, startSubmitting };
  }

  // Keep usable content on-screen during a refresh. First loads get an
  // announced skeleton; later failures retain the last successful view with a
  // persistent retry rather than replacing everything with an error card.
  const VIEW_FRESH_MS = 20 * 1000;
  function viewIsFresh(el, key) {
    return el.dataset.viewKey === key
      && Date.now() - Number(el.dataset.viewReadyAt || 0) < VIEW_FRESH_MS;
  }
  function beginViewRender(el, key, rows) {
    const hasUsableContent = el.dataset.viewKey === key && el.childElementCount > 0;
    el.setAttribute('aria-busy', 'true');
    el.classList.toggle('view-refreshing', hasUsableContent);
    if (!hasUsableContent) el.innerHTML = skeletonHtml(rows);
    return hasUsableContent;
  }
  function commitViewRender(el, stage, key) {
    el.replaceChildren(...stage.childNodes);
    el.dataset.viewKey = key;
    el.dataset.viewReadyAt = String(Date.now());
    el.setAttribute('aria-busy', 'false');
    el.classList.remove('view-refreshing');
  }
  function retainViewAfterError(el, message, retryFn) {
    el.setAttribute('aria-busy', 'false');
    el.classList.remove('view-refreshing');
    el.querySelector('.view-refresh-note')?.remove();
    const note = document.createElement('div');
    note.className = 'view-refresh-note';
    note.setAttribute('role', 'status');
    note.innerHTML = `<span>${esc(message || "Couldn't refresh — showing the last update.")}</span><button type="button">Retry</button>`;
    note.querySelector('button').addEventListener('click', retryFn);
    el.prepend(note);
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
    return `<div class="loading-state" role="status" aria-live="polite">
      <span class="sr-only">Loading…</span>
      <div aria-hidden="true">${card.repeat(rows)}</div>
    </div>`;
  }

  // Inline error with a Retry button wired to re-run the view.
  function renderError(el, message, retryFn) {
    el.innerHTML = `
      <div class="empty-state" role="alert">
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

  function avatarHtml(user, cls = '', tag = 'div') {
    tag = tag === 'span' ? 'span' : 'div';
    const bg = esc(user.avatar_color || '#2f9e44');
    const label = esc(initials(user.display_name));
    if (user.avatar_url) {
      // Photo with graceful fallback to the colored-initials avatar on load error.
      return `<${tag} class="avatar ${cls}" style="background:${bg}">`
        + `<img src="${esc(user.avatar_url)}" alt="" loading="lazy" `
        + `onerror="this.remove()" />${label}</${tag}>`;
    }
    return `<${tag} class="avatar ${cls}" style="background:${bg}">${label}</${tag}>`;
  }

  // Make existing card-style rows work for keyboards and switch controls
  // without changing their visual layout. Native controls remain untouched.
  function makePressable(el, activate, label) {
    if (!el) return;
    if (['BUTTON', 'A'].includes(el.tagName)) {
      el.addEventListener('click', activate);
      return;
    }
    el.setAttribute('role', 'button');
    el.tabIndex = 0;
    if (label) el.setAttribute('aria-label', label);
    el.addEventListener('click', activate);
    el.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      if (e.target !== el) return;
      e.preventDefault();
      activate(e);
    });
  }

  // Complete the keyboard half of every declared ARIA tablist. Existing click
  // handlers continue to own the feature state; arrows/Home/End simply move
  // and activate the roving tab.
  function setupTablistKeyboard(root) {
    if (!root || root.dataset.keyboardTabs === '1') return;
    root.dataset.keyboardTabs = '1';
    const tabs = () => [...root.querySelectorAll('[role="tab"]')].filter((tab) => !tab.disabled);
    const sync = () => tabs().forEach((tab) => {
      const selected = tab.getAttribute('aria-selected') === 'true';
      tab.tabIndex = selected ? 0 : -1;
      const panelId = tab.getAttribute('aria-controls');
      if (selected && panelId && tab.id) document.getElementById(panelId)?.setAttribute('aria-labelledby', tab.id);
    });
    root.addEventListener('click', () => queueMicrotask(sync));
    root.addEventListener('keydown', (e) => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return;
      const items = tabs();
      const current = items.indexOf(e.target.closest('[role="tab"]'));
      if (current < 0 || !items.length) return;
      e.preventDefault();
      const backward = e.key === 'ArrowLeft' || e.key === 'ArrowUp';
      const next = e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1
        : (current + (backward ? -1 : 1) + items.length) % items.length;
      items[next].focus();
      items[next].click();
    });
    sync();
  }
  function fmtDateTime(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    const now = new Date();
    const dayMs = 86400000;
    const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diffDays = Math.round((startOf(d) - startOf(now)) / dayMs);
    // Non-breaking space keeps "10:00 AM" together when a title wraps.
    const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }).replace(' ', ' ');
    if (diffDays === 0) return `Today · ${time}`;
    if (diffDays === 1) return `Tomorrow · ${time}`;
    if (diffDays === -1) return `Yesterday · ${time}`;
    return `${d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })} · ${time}`;
  }
  function fmtTimeShort(isoStr) {
    const d = new Date(isoStr);
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }).replace(' ', ' ');
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
  // Short natural summary of the slots two players share: "Sat AM · Wed PM".
  function sharedAvailabilityText(a, b) {
    if (!a || !b) return '';
    const set = new Set(a);
    const shared = b.filter((s) => set.has(s));
    if (!shared.length) return '';
    const shortDay = { mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun' };
    const shortPart = { am: 'AM', pm: 'PM', eve: 'eve' };
    const order = (s) => AVAIL_DAYS.indexOf(s.split('-')[0]) * 3 + ['am', 'pm', 'eve'].indexOf(s.split('-')[1]);
    return shared
      .sort((x, y) => order(x) - order(y))
      .slice(0, 4)
      .map((s) => { const [d, p] = s.split('-'); return `${shortDay[d]} ${shortPart[p]}`; })
      .join(' · ');
  }
  function fmtDuration(minutes) {
    if (!minutes || minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes}m`;
    // Past two days, "124h 22m" stops being readable — roll into days.
    if (minutes >= 2880) {
      const d = Math.floor(minutes / 1440);
      const rh = Math.round((minutes % 1440) / 60);
      return rh ? `${d}d ${rh}h` : `${d}d`;
    }
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
      $('#auth-name').required = authMode === 'register';
      $('#auth-password').autocomplete = authMode === 'register' ? 'new-password' : 'current-password';
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
        if (!rebuildReloadedMatchRouteIfNeeded() && !openDeepLink()) maybeOnboardHomeArea();
        handleInviteRef();
        syncPushSubscription();
      } catch (err) {
        errEl.textContent = err.message;
        errEl.classList.remove('hidden');
        errEl.focus();
      } finally {
        submitBtn.disabled = false;
      }
    });
  }

  function purgeAccountChatDrafts(accountId) {
    if (!accountId) return;
    try {
      const marker = `:${accountId}:`;
      const keys = [];
      for (let index = 0; index < sessionStorage.length; index += 1) {
        const key = sessionStorage.key(index);
        if (key?.startsWith('pp_chat_draft_v') && key.includes(marker)) keys.push(key);
      }
      keys.forEach((key) => sessionStorage.removeItem(key));
    } catch { /* storage unavailable */ }
  }

  function resetPrivateUiForLogout(accountId) {
    purgeAccountChatDrafts(accountId);
    purgeAccountChatOutbox(accountId);
    state.playRenderSeq += 1;
    state.chatRenderSeq += 1;
    profileRenderGeneration += 1;
    profileDashboardCache = { userId: null, promise: null, data: null, readyAt: 0 };
    state.playGamesCache = null;
    state.chatFriendsCache = null;
    state.activeThreadUserId = null;
    state.lastNotifId = null;
    state.unreadMessages = 0;
    state.pendingRequests = 0;
    state.communityRoomUnread = 0;
    state.gamesToConfirm = 0;
    state.activeGame = null;
    state.presence = null;
    state.favIds = null;
    state.lastAutoCheckAt = 0;
    state.userLoc = null;
    state.areaLoc = null;
    state.areaLabel = null;
    state.snapshotAreaProvisional = false;
    state.courtsInView = [];
    state.selectedCourtId = null;
    state.courtMarkers.clear();
    state.courtListSignature = '';
    state.courtListExpandedScrollTop = 0;
    state.courtListLimit = 20;
    state.courtListPlaces = [];
    state.courtListSavedOnly = false;
    state.listSort = 'distance';
    Object.keys(state.courtFilters).forEach((key) => { state.courtFilters[key] = false; });
    state.courtFetchSeq += 1;
    state.nearbySkill = '';
    state.searchQ = '';
    state.tab = 'play';
    state.playSeg = 'games';
    state.chatSeg = 'chats';

    if (state.userDot && state.map) state.map.removeLayer(state.userDot);
    state.userDot = null;
    state.markers?.clearLayers?.();

    clearTimeout(reusableOverlayTimer);
    reusableOverlayTimer = null;
    reusableOverlayEntry = null;
    pendingReusableTraversal = null;
    pendingDeepMatchRebuild = null;
    suppressNativeHashRoute = null;
    adoptOverlayEntry = null;
    activeRoutedOverlayLoad = null;
    routedOverlayLoadSeq += 1;
    overlayHistoryRevision += 1;
    dismissAllRequested = false;
    dismissAllCallbacks = [];
    while (overlayStack.length) {
      const entry = overlayStack.pop();
      destroyModal(entry.el, { restoreFocus: false });
    }
    syncModalStack();
    try {
      history.replaceState(overlayHistoryState(null, 0, null), '', baseAppUrl());
    } catch { /* history can be unavailable */ }

    ['#play-content', '#chat-content', '#profile-content'].forEach((selector) => {
      const panel = $(selector);
      if (!panel) return;
      panel.replaceChildren();
      delete panel.dataset.viewKey;
      delete panel.dataset.viewReadyAt;
      panel.setAttribute('aria-busy', 'false');
      panel.classList.remove('view-refreshing');
    });
    $('#court-preview')?.replaceChildren();
    $('#court-preview')?.classList.add('hidden');
    $('#court-list-items')?.replaceChildren();
    const courtSearch = $('#court-search');
    if (courtSearch) courtSearch.value = '';
    hideSearchSuggest();
    syncSearchClear();
    syncCourtFilterControls();
    const banner = $('#active-game-banner');
    if (banner) { banner.replaceChildren(); banner.classList.add('hidden'); }
    document.querySelectorAll('#play-segments button').forEach((button) => {
      const active = button.dataset.seg === 'games';
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
    document.querySelectorAll('#chat-segments button').forEach((button) => {
      const active = button.dataset.seg === 'chats';
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
  }

  function logout() {
    const accountId = state.me && state.me.id;
    revokePushSubscription(state.token);
    clearGameDraft(accountId);
    resetPrivateUiForLogout(accountId);
    stopLocationWatch();
    state.token = null;
    state.me = null;
    localStorage.removeItem('pp_token');
    localStorage.removeItem('pp_me_snapshot_v1');
    clearInterval(state.mePollTimer);
    clearInterval(state.threadPollTimer);
    state.mePollTimer = null;
    state.threadPollTimer = null;
    if ('clearAppBadge' in navigator) navigator.clearAppBadge().catch(() => { /* fine */ });
    $('#boot-screen')?.classList.add('hidden');
    $('#main-screen').classList.add('hidden');
    $('#auth-screen').classList.remove('hidden');
    $('#auth-email').value = '';
    $('#auth-password').value = '';
    $('#auth-name').value = '';
    $('#auth-error').classList.add('hidden');
  }

  function tokenHint() {
    return state.token ? state.token.slice(-16) : '';
  }

  function saveMeSnapshot(data) {
    if (!state.token || !data || !data.user) return;
    try {
      localStorage.setItem('pp_me_snapshot_v1', JSON.stringify({
        v: 1,
        tokenHint: tokenHint(),
        savedAt: Date.now(),
        data,
      }));
    } catch { /* private mode/storage pressure must never block the app */ }
  }

  function readMeSnapshot() {
    try {
      const snapshot = JSON.parse(localStorage.getItem('pp_me_snapshot_v1') || 'null');
      if (!snapshot || snapshot.v !== 1 || snapshot.tokenHint !== tokenHint()
          || !snapshot.data || !snapshot.data.user
          || Date.now() - Number(snapshot.savedAt || 0) > 7 * 86400000) return null;
      return snapshot;
    } catch { return null; }
  }

  function applyMe(data, {
    persist = true,
    provisional = false,
    reconcileSnapshot = false,
  } = {}) {
    const hadProvisionalArea = state.snapshotAreaProvisional;
    const previousArea = state.areaLoc ? [...state.areaLoc] : null;
    state.me = data.user;
    // Catalog of muteable kinds rides alongside the user for the settings UI.
    if (data.muteable_notifications) state.me.muteable_notifications = data.muteable_notifications;
    state.presence = data.presence;
    state.unreadMessages = data.unread_messages || 0;
    if (data.community_room_unread != null) {
      state.communityRoomUnread = Number(data.community_room_unread) || 0;
    }
    state.pendingRequests = data.pending_friend_requests || 0;
    state.gamesToConfirm = data.games_to_confirm || 0;

    // Live updates: pop a toast when something new lands while the app is open.
    state.unreadNotifications = data.unread_notifications || 0;
    state.activeGame = data.active_game || null;
    state.activeTournament = data.active_tournament || null;
    const latest = data.latest_notification;
    if (latest) {
      if (state.lastNotifId !== null && latest.id > state.lastNotifId && !latest.read) {
        const coveredByBanner = latest.related_game_id && state.activeGame
          && state.activeGame.id === latest.related_game_id;
        if (!coveredByBanner) toast(`🔔 ${latest.title}`);
        if (typeof Notification !== 'undefined' && Notification.permission === 'granted' && document.hidden) {
          try {
            const notification = new Notification('Third Shot', {
              body: latest.title, icon: '/icon-512.png', tag: `pp-${latest.id}`,
            });
            pageNotifications.add(notification);
            notification.addEventListener('close', () => pageNotifications.delete(notification), { once: true });
          } catch { /* not supported */ }
        }
        // Snapshot-first boot already has a fresh initial feed in flight.
        if (state.tab === 'play' && !reconcileSnapshot) renderPlay();
      }
      state.lastNotifId = latest.id;
    } else if (state.lastNotifId === null) {
      state.lastNotifId = 0;
    }

    renderBadges();
    renderPresenceBanner();
    renderActiveGameBanner();
    let areaChanged = false;
    if (provisional && !state.userLoc) {
      state.snapshotAreaProvisional = true;
      state.areaLoc = state.me.home_lat != null
        ? [state.me.home_lat, state.me.home_lng] : null;
    } else if (reconcileSnapshot && hadProvisionalArea && !state.userLoc) {
      const liveArea = state.me.home_lat != null
        ? [state.me.home_lat, state.me.home_lng] : null;
      areaChanged = JSON.stringify(previousArea) !== JSON.stringify(liveArea);
      state.areaLoc = liveArea;
      state.snapshotAreaProvisional = false;
    } else if (state.me.home_lat != null && !state.areaLoc && !state.userLoc) {
      state.areaLoc = [state.me.home_lat, state.me.home_lng];
    }
    updatePlayHeader();
    if (reconcileSnapshot) {
      if (areaChanged) {
        state.playGamesCache = null;
        state.chatFriendsCache = null;
        if (state.map && state.areaLoc) {
          moveCourtMapWithoutRefresh(() => state.map.setView(state.areaLoc, 12, { animate: false }));
        }
        if (state.tab === 'courts' && state.map) {
          beginCourtContextRefresh('Updating courts for your current home area…');
          fetchCourtsInView({ surfaceError: true });
        } else if (state.tab === 'play') renderPlay();
        else if (state.tab === 'chat') renderChat();
      }
      if (state.tab === 'profile') renderProfile({ reuseDashboard: true });
    }
    if (persist) saveMeSnapshot(data);
  }

  function dismissedInvites() {
    try { return JSON.parse(localStorage.getItem('pp_dismissed_invites') || '[]'); }
    catch { return []; }
  }

  function renderActiveGameBanner() {
    const el = $('#active-game-banner');
    const game = state.activeGame;
    // A merely-upcoming game yields the slot to a live tournament, or to one
    // starting before it — action states (live/challenge/confirm…) always win.
    const t = state.activeTournament;
    const tournamentWins = t && game && game.banner_state === 'upcoming'
      && (t.banner_state === 'live'
          || new Date(t.starts_at) < new Date(game.scheduled_at));
    if (!game || tournamentWins
        || (game.banner_state === 'invited' && dismissedInvites().includes(game.id))) {
      // No game to surface — an imminent or in-progress tournament gets the slot.
      if (renderTournamentBanner(el)) return;
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
      <button type="button" class="agb-open">
        ${stateCfg.icon.startsWith('<') ? stateCfg.icon : `<span style="font-size:17px">${stateCfg.icon}</span>`}
        <span class="agb-main">
          <span class="agb-title">${stateCfg.title}</span>
          <span class="agb-sub">${stateCfg.sub}</span>
        </span>
        ${game.banner_state === 'invited' ? '' : '<span class="agb-chev">›</span>'}
      </button>
      ${game.banner_state === 'invited' ? '<button type="button" class="agb-dismiss" id="agb-dismiss" aria-label="Decline game invite">✕</button>' : ''}`;
    const dismissBtn = el.querySelector('#agb-dismiss');
    if (dismissBtn) {
      dismissBtn.onclick = (e) => {
        e.stopPropagation();
        const ids = dismissedInvites();
        if (!ids.includes(game.id)) ids.push(game.id);
        localStorage.setItem('pp_dismissed_invites', JSON.stringify(ids.slice(-30)));
        renderActiveGameBanner();
        // Tell the host too — best effort; the local dismissal already stuck.
        api(`/games/${game.id}/invites/decline`, { method: 'POST' })
          .then(() => toast("Declined — the host knows you can't make it"))
          .catch(() => toast('Invite dismissed'));
      };
    }
    el.querySelector('.agb-open').onclick = () => {
      if (game.banner_state === 'live' && game.players.length >= 2) {
        const modalLoad = beginRoutedOverlayLoad(null);
        api(`/games/${game.id}`).then((fresh) => {
          if (!routedOverlayLoadIsCurrent(modalLoad)) return;
          openScoreModal(fresh, () => refreshMe());
        }).catch((e) => {
          if (routedOverlayLoadIsCurrent(modalLoad)) toast(e.message);
        });
      } else {
        openGameScreen(game.id);
      }
    };
    $('#app').classList.add('has-banner');
  }

  // Returns true when it drew a tournament banner into the shared slot.
  function renderTournamentBanner(el) {
    const t = state.activeTournament;
    if (!t) return false;
    const live = t.banner_state === 'live';
    const sub = live
      ? (t.my_next_opponent
          ? `Next up: vs ${esc(t.my_next_opponent)} — tap to score`
          : 'Bracket in progress — tap for scores')
      : (t.my_entry_id && !t.my_checked_in)
        ? `${esc((t.court || {}).name || '')} · tap to check in`
        : `${esc((t.court || {}).name || '')} · tap for details`;
    el.className = `active-game-banner state-${live ? 'live' : 'upcoming'}`;
    el.innerHTML = `
      <button type="button" class="agb-open">
        ${live ? '<span class="agb-dot"></span>' : '<span style="font-size:17px">🏆</span>'}
        <span class="agb-main">
          <span class="agb-title">${live ? `LIVE: ${esc(t.name)}` : `🏆 ${esc(t.name)} · ${fmtDateTime(t.starts_at)}`}</span>
          <span class="agb-sub">${sub}</span>
        </span>
        <span class="agb-chev">›</span>
      </button>`;
    el.querySelector('.agb-open').onclick = () => openTournamentScreen(t.id);
    el.classList.remove('hidden');
    $('#app').classList.add('has-banner');
    return true;
  }

  function renderBadges() {
    const inboxTotal = state.unreadMessages + state.communityRoomUnread;
    const total = inboxTotal + state.pendingRequests;
    const badge = $('#chat-badge');
    badge.textContent = total > 99 ? '99+' : String(total);
    badge.classList.toggle('hidden', total === 0);

    const inboxBadge = $('#chat-inbox-badge');
    if (inboxBadge) {
      inboxBadge.textContent = inboxTotal > 99 ? '99+' : String(inboxTotal);
      inboxBadge.classList.toggle('hidden', inboxTotal === 0);
      $('#chat-tab-chats')?.setAttribute('aria-label', inboxTotal
        ? `Inbox, ${inboxTotal} unread` : 'Inbox');
    }
    const friendsBadge = $('#chat-friends-badge');
    if (friendsBadge) {
      friendsBadge.textContent = state.pendingRequests > 99 ? '99+' : String(state.pendingRequests);
      friendsBadge.classList.toggle('hidden', state.pendingRequests === 0);
      $('#chat-tab-friends')?.setAttribute('aria-label', state.pendingRequests
        ? `Friends, ${state.pendingRequests} pending request${state.pendingRequests === 1 ? '' : 's'}` : 'Friends');
    }

    const playBadge = $('#play-badge');
    playBadge.textContent = String(state.gamesToConfirm);
    playBadge.classList.toggle('hidden', state.gamesToConfirm === 0);

    const bellBadge = $('#bell-badge');
    const unread = state.unreadNotifications || 0;
    [bellBadge, $('#play-bell-badge')].filter(Boolean).forEach((el) => {
      el.textContent = unread > 99 ? '99+' : String(unread);
      el.classList.toggle('hidden', unread === 0);
    });

    // Installed-app icon badge (iOS 16.4+/Chrome): everything that begs a look.
    if ('setAppBadge' in navigator) {
      const appTotal = total + state.gamesToConfirm + unread;
      (appTotal ? navigator.setAppBadge(appTotal) : navigator.clearAppBadge())
        .catch(() => { /* permission or platform says no — fine */ });
    }
  }

  async function refreshMe() {
    try {
      const data = await api('/me');
      applyMe(data, { reconcileSnapshot: state.snapshotAreaProvisional });
    } catch { /* logged out */ }
  }

  // ---------- Tabs ----------
  function setupTabs() {
    document.querySelectorAll('.nav-btn').forEach((btn) => {
      btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
    setupTablistKeyboard($('#play-segments'));
    setupTablistKeyboard($('#chat-segments'));
  }

  function switchTab(tab, { preserveOverlayIntent = false } = {}) {
    if (!preserveOverlayIntent) cancelPendingOverlayLoadForNavigation();
    state.tab = tab;
    document.querySelectorAll('.nav-btn').forEach((b) => {
      const active = b.dataset.tab === tab;
      b.classList.toggle('active', active);
      if (active) b.setAttribute('aria-current', 'page');
      else b.removeAttribute('aria-current');
    });
    ['courts', 'play', 'chat', 'profile'].forEach((t) => {
      $(`#tab-${t}`).classList.toggle('hidden', t !== tab);
    });
    renderActiveGameBanner();
    if (tab === 'courts') {
      if (state.map) { setTimeout(() => state.map.invalidateSize(), 60); refreshLookingBanner(); }
      else ensureMapReady().catch(() => { /* inline retry owns the failure */ });
    }
    if (tab === 'play') { syncPlayFab(); renderPlay({ reuseFresh: true }); }
    if (tab === 'chat') renderChat({ reuseFresh: true });
    if (tab === 'profile') renderProfile();
  }

  // One share sheet for every "invite friends" button in the app.
  async function shareInviteLink() {
    if (!state.me) return;
    const url = `${location.origin}/u/${state.me.id}`; // short link → OG preview in chat apps
    const text = 'Play pickleball with me on Third Shot! 🏓';
    try {
      if (navigator.share) {
        await navigator.share({ title: 'Third Shot', text, url });
      } else {
        await navigator.clipboard.writeText(`${text} ${url}`);
        toast('Invite link copied 📋');
      }
    } catch { /* user cancelled share */ }
  }

  // Pull-to-refresh on the scrolling tabs — installed PWAs don't get the
  // browser's reload gesture, and these feeds otherwise wait on slow polls.
  function setupPullToRefresh() {
    const configs = [
      ['#tab-play', '#play-content', () => renderPlay()],
      ['#tab-chat', '#chat-content', () => renderChat()],
      ['#tab-profile', '#profile-content', () => renderProfile()],
    ];
    configs.forEach(([panelSel, scrollSel, refresh]) => {
      const panel = document.querySelector(panelSel);
      const el = document.querySelector(scrollSel);
      if (!panel || !el) return;
      const spinner = document.createElement('div');
      spinner.className = 'ptr-spinner';
      panel.appendChild(spinner);
      let startY = null;
      let armed = false;
      el.addEventListener('touchstart', (e) => {
        startY = el.scrollTop <= 0 ? e.touches[0].clientY : null;
        armed = false;
      }, { passive: true });
      el.addEventListener('touchmove', (e) => {
        if (startY == null) return;
        const dy = e.touches[0].clientY - startY;
        if (dy <= 0 || el.scrollTop > 0) { spinner.style.opacity = '0'; armed = false; return; }
        const pull = Math.min(dy, 110);
        spinner.style.opacity = String(Math.min(pull / 70, 1));
        spinner.style.transform = `translateX(-50%) translateY(${pull * 0.5}px) rotate(${pull * 3}deg)`;
        armed = pull >= 70;
      }, { passive: true });
      el.addEventListener('touchend', async () => {
        startY = null;
        if (!armed) { spinner.style.opacity = '0'; return; }
        armed = false;
        spinner.classList.add('spin');
        try { await refresh(); await refreshMe(); } catch { /* offline */ }
        spinner.classList.remove('spin');
        spinner.style.opacity = '0';
      }, { passive: true });
    });
  }

  // Empty-state CTA buttons: any element with data-goto jumps to the right
  // spot in the app (works inside modals too — closes them first).
  function setupEmptyStateCtas() {
    document.addEventListener('click', (e) => {
      if (e.target.closest('[data-invite-share]')) shareInviteLink();
    });
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-goto]');
      if (!btn) return;
      const target = btn.dataset.goto;
      dismissAllModals(() => {
        if (target === 'play-now') {
          if (state.tab !== 'play') switchTab('play');
          openNewGameModal(null, 'casual', true);
        } else if (target === 'new-ranked-game') {
          if (state.tab !== 'play') switchTab('play');
          openNewGameModal(null, 'ranked');
        } else if (target === 'new-game') {
          if (state.tab !== 'play') switchTab('play');
          openNewGameModal();
        } else if (target === 'courts-list') {
          switchTab('courts');
          setCourtSheetSnap('half');
        } else if (target === 'chat-friends') {
          state.chatSeg = 'friends';
          document.querySelectorAll('#chat-segments button').forEach((b) => {
            const active = b.dataset.seg === 'friends';
            b.classList.toggle('active', active);
            b.setAttribute('aria-selected', String(active));
          });
          switchTab('chat');
        } else if (target === 'chat-nearby') {
          state.chatSeg = 'nearby';
          document.querySelectorAll('#chat-segments button').forEach((b) => {
            const active = b.dataset.seg === 'nearby';
            b.classList.toggle('active', active);
            b.setAttribute('aria-selected', String(active));
          });
          switchTab('chat');
        } else {
          switchTab(target);
        }
      });
    });
  }

  // ---------- Map / Courts ----------
  const LEAFLET_ASSETS = {
    css: [
      ['https://unpkg.com/leaflet@1.9.4/dist/leaflet.css', 'sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY='],
      ['https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css', ''],
    ],
    js: [
      ['https://unpkg.com/leaflet@1.9.4/dist/leaflet.js', 'sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo='],
      ['https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js', ''],
    ],
  };
  let mapAssetsPromise = null;
  let mapReadyPromise = null;

  function loadStylesheet(src, integrity) {
    if (document.querySelector(`link[href="${src}"]`)) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = src;
      link.crossOrigin = '';
      if (integrity) link.integrity = integrity;
      link.onload = resolve;
      link.onerror = () => reject(new Error('Could not load the court map'));
      document.head.appendChild(link);
    });
  }

  function loadScript(src, integrity) {
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing && existing.dataset.loaded === '1') return Promise.resolve();
    return new Promise((resolve, reject) => {
      const script = existing || document.createElement('script');
      script.src = src;
      script.crossOrigin = '';
      if (integrity) script.integrity = integrity;
      script.onload = () => { script.dataset.loaded = '1'; resolve(); };
      script.onerror = () => reject(new Error('Could not load the court map'));
      if (!existing) document.head.appendChild(script);
    });
  }

  function ensureMapAssets() {
    if (window.L) return Promise.resolve();
    if (!mapAssetsPromise) {
      mapAssetsPromise = Promise.all(LEAFLET_ASSETS.css.map(([src, integrity]) => loadStylesheet(src, integrity)))
        .then(() => loadScript(...LEAFLET_ASSETS.js[0]))
        .then(() => loadScript(...LEAFLET_ASSETS.js[1]))
        .catch((err) => { mapAssetsPromise = null; throw err; });
    }
    return mapAssetsPromise;
  }

  async function ensureMapReady() {
    if (state.map) return state.map;
    if (mapReadyPromise) return mapReadyPromise;
    const mapEl = $('#map');
    mapEl.setAttribute('aria-busy', 'true');
    mapEl.innerHTML = '<div class="map-load-state" role="status"><span class="map-load-spinner"></span><b>Opening the court finder…</b><small>Loading the map only when you need it saves battery and data.</small></div>';
    mapReadyPromise = ensureMapAssets().then(() => {
      mapEl.innerHTML = '';
      setupMap();
      mapEl.setAttribute('aria-busy', 'false');
      setupTablistKeyboard($('#court-view-switch'));
      return state.map;
    }).catch((err) => {
      mapEl.setAttribute('aria-busy', 'false');
      mapEl.innerHTML = `<div class="map-load-state" role="alert"><b>${esc(err.message)}</b><small>Check your connection, then try again.</small><button type="button" class="btn btn-primary">Retry</button></div>`;
      mapEl.querySelector('button').addEventListener('click', () => { mapReadyPromise = null; ensureMapReady(); });
      throw err;
    }).finally(() => {
      if (!state.map) mapReadyPromise = null;
    });
    return mapReadyPromise;
  }

  function moveCourtMapWithoutRefresh(move) {
    const seq = ++state.courtMoveSuppressSeq;
    state.suppressCourtMoveFetch = true;
    try { move(); } finally {
      // Leaflet normally emits moveend synchronously when animation is off.
      // The guard also expires so a no-op move cannot swallow the next drag.
      setTimeout(() => {
        if (seq === state.courtMoveSuppressSeq) state.suppressCourtMoveFetch = false;
      }, 250);
    }
  }

  function beginCourtContextRefresh(label = 'Updating courts in this area…') {
    state.courtFetchSeq += 1; // cancel any response owned by the previous area
    state.courtsInView = [];
    state.courtListPlaces = [];
    state.courtListSavedOnly = false;
    state.courtListSignature = '';
    state.courtListLimit = 20;
    state.courtListExpandedScrollTop = 0;
    state.selectedCourtId = null;
    state.courtMarkers.clear();
    state.markers?.clearLayers?.();
    $('#court-preview')?.classList.add('hidden');
    const title = document.querySelector('#court-list .sheet-title');
    const context = $('#court-list-context');
    const count = $('#court-result-count');
    if (title) title.textContent = 'Finding the best courts';
    if (context) context.textContent = label;
    if (count) count.textContent = '…';
    const list = $('#court-list-items');
    if (list) list.innerHTML = `<div class="court-result-loading" role="status" aria-live="polite">
      <span class="map-load-spinner" aria-hidden="true"></span><b>${esc(label)}</b>
      <small>Refreshing distance, activity, and open games.</small>
    </div>`;
  }

  function renderCourtContextError(error, retry) {
    const title = document.querySelector('#court-list .sheet-title');
    const context = $('#court-list-context');
    const count = $('#court-result-count');
    if (title) title.textContent = 'Courts need a refresh';
    if (context) context.textContent = 'No old-area results are being shown';
    if (count) count.textContent = '!';
    const list = $('#court-list-items');
    if (!list) return;
    renderError(list, error?.message || 'Could not update this area yet.', retry);
  }

  function mapViewStorageKey(userId = state.me && state.me.id) {
    return userId ? `pp_mapview:${userId}` : null;
  }

  function readSavedMapView() {
    // The legacy key was origin-global and could expose another account's last
    // precise center. Never import it into an account-scoped session.
    try { localStorage.removeItem('pp_mapview'); } catch { /* storage unavailable */ }
    const key = mapViewStorageKey();
    if (!key) return null;
    try {
      const saved = JSON.parse(localStorage.getItem(key) || 'null');
      const center = saved && saved.center;
      if (!Array.isArray(center) || center.length !== 2
          || !center.every(Number.isFinite) || !Number.isFinite(saved.zoom)) return null;
      return {
        center: [Math.max(-90, Math.min(90, center[0])), Math.max(-180, Math.min(180, center[1]))],
        zoom: Math.max(2, Math.min(19, saved.zoom)),
      };
    } catch { return null; }
  }

  function accountMapStart() {
    const saved = readSavedMapView();
    if (saved) return saved;
    if (state.me && state.me.home_lat != null) {
      return { center: [state.me.home_lat, state.me.home_lng], zoom: 12 };
    }
    return { center: DEFAULT_CENTER, zoom: 11 };
  }

  function restoreAccountMapView() {
    if (!state.map || !state.me) return;
    const { center, zoom } = accountMapStart();
    state.searchQ = '';
    const search = $('#court-search');
    if (search) search.value = '';
    hideSearchSuggest();
    syncSearchClear();
    beginCourtContextRefresh('Loading your court area…');
    moveCourtMapWithoutRefresh(
      () => state.map.setView(center, zoom, { animate: false }),
    );
    fetchCourtsInView({ surfaceError: true });
  }

  function setupMap() {
    const saved = readSavedMapView();
    const start = saved || accountMapStart();
    const { center, zoom } = start;
    // Center on the user's saved home area when there's no last-viewed map.
    if (!saved && state.me && state.me.home_lat != null) {
      state.areaLoc = [state.me.home_lat, state.me.home_lng];
    }
    state.map = L.map('map', { zoomControl: false }).setView(center, zoom);
    state.tileLayer = L.tileLayer(themeTileUrl(), {
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
              html: `<div class="cluster-icon" role="img" aria-label="${n} courts in this area. Activate to zoom in" style="width:${size}px;height:${size}px">${n}</div>`,
              iconSize: [size, size],
            });
          },
        })
      : L.layerGroup();
    state.markers.addTo(state.map);

    $('#map-filters').addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-court-filter]');
      if (!btn) return;
      const key = btn.dataset.courtFilter;
      state.courtFilters[key] = !state.courtFilters[key];
      syncCourtFilterControls();
      await refreshCourtResults();
      if (!(state.courtsInView || []).length) {
        toast(key === 'saved'
          ? 'No saved courts match — save a court or clear a filter'
          : 'No courts match yet — clear a filter or move the map');
      }
      // Saved courts can be outside the current map. Fit them once selected so
      // the map and decision list tell the same story.
      if (key === 'saved' && state.courtFilters.saved && state.courtsInView.length) {
        const pts = state.courtsInView.filter((c) => c.latitude != null).map((c) => [c.latitude, c.longitude]);
        if (pts.length) moveCourtMapWithoutRefresh(() => state.map.fitBounds(
          pts, { maxZoom: 13, padding: [50, 50], animate: false },
        ));
      }
    });
    $('#court-more-filters').addEventListener('click', openCourtFilterSheet);

    state.map.on('moveend', () => {
      const c = state.map.getCenter();
      const key = mapViewStorageKey();
      if (key) localStorage.setItem(key, JSON.stringify({
        center: [c.lat, c.lng], zoom: state.map.getZoom(),
      }));
      if (state.suppressCourtMoveFetch) {
        state.suppressCourtMoveFetch = false;
        return;
      }
      beginCourtContextRefresh();
      fetchCourtsInView({ surfaceError: true });
    });
    state.map.on('dragend', () => $('#use-map-area')?.classList.remove('hidden'));
    $('#use-map-area')?.addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      const c = state.map.getCenter();
      state.areaLoc = [c.lat, c.lng];
      state.areaLabel = 'Selected map area';
      state.snapshotAreaProvisional = false;
      state.playGamesCache = null;
      state.chatFriendsCache = null;
      btn.classList.add('hidden');
      updatePlayHeader();
      toast('Games and players now follow this map area 📍');
      try {
        const geo = await api(`/geocode/reverse?lat=${c.lat}&lng=${c.lng}`);
        if (geo.label && state.areaLoc && state.areaLoc[0] === c.lat && state.areaLoc[1] === c.lng) {
          state.areaLabel = geo.label;
          updatePlayHeader();
        }
      } catch { /* the committed coordinates still work */ }
    });

    // NB: don't pass the click event through — locateMe's arg is the `silent` flag.
    $('#locate-btn').addEventListener('click', () => locateMe(false));
    $('#bell-btn').addEventListener('click', openActivity);
    $('#looking-banner').addEventListener('click', () => {
      state.chatSeg = 'nearby';
      document.querySelectorAll('#chat-segments button').forEach((b) => b.classList.toggle('active', b.dataset.seg === 'nearby'));
      switchTab('chat');
    });
    $('#court-view-switch').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-court-view]');
      if (!btn) return;
      setCourtSheetSnap(btn.dataset.courtView === 'map' ? 'peek' : 'half');
    });
    $('#court-sheet-cycle').addEventListener('click', () => {
      if (state.courtSheetJustDragged) { state.courtSheetJustDragged = false; return; }
      setCourtSheetSnap(state.courtSheetSnap === 'peek' ? 'half'
        : state.courtSheetSnap === 'half' ? 'full' : 'peek');
    });
    $('#court-sheet-expand').addEventListener('click', () => {
      setCourtSheetSnap(state.courtSheetSnap === 'full' ? 'half' : 'full');
    });
    $('#court-sort').addEventListener('change', (e) => {
      state.listSort = e.target.value;
      refreshCourtResults();
    });
    setupCourtSheetDrag();
    syncCourtFilterControls();
    setCourtSheetSnap('peek', { announce: false });

    let searchTimer;
    const searchInput = $('#court-search');
    searchInput.addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      const q = e.target.value.trim();
      // While a search is active, map moves must not clobber its results
      // (fitBounds below fires moveend → fetchCourtsInView).
      state.searchQ = q;
      syncSearchClear();
      if (!q) hideSearchSuggest();
      searchTimer = setTimeout(() => q ? searchCourts(q) : fetchCourtsInView(), 350);
    });
    // Keyboard combobox behavior keeps focus in the search field while the
    // highlighted suggestion moves underneath it. Enter without a highlight
    // keeps the useful mobile behavior of showing every match.
    searchInput.addEventListener('keydown', async (e) => {
      const rows = [...$('#search-suggest').querySelectorAll('[role="option"]')];
      if (e.key === 'Escape') { hideSearchSuggest(); return; }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key) && rows.length) {
        e.preventDefault();
        const current = rows.findIndex((row) => row.getAttribute('aria-selected') === 'true');
        const next = e.key === 'Home' ? 0 : e.key === 'End' ? rows.length - 1
          : e.key === 'ArrowDown' ? (current + 1) % rows.length
            : (current <= 0 ? rows.length - 1 : current - 1);
        rows.forEach((row, i) => row.setAttribute('aria-selected', String(i === next)));
        searchInput.setAttribute('aria-activedescendant', rows[next].id);
        rows[next].scrollIntoView({ block: 'nearest' });
        return;
      }
      if (e.key !== 'Enter') return;
      const active = rows.find((row) => row.getAttribute('aria-selected') === 'true');
      if (active) {
        e.preventDefault();
        active.click();
        return;
      }
      clearTimeout(searchTimer);
      searchInput.blur();
      if (!state.searchQ) return;
      await searchCourts(state.searchQ); // don't fit to stale, pre-debounce results
      hideSearchSuggest();
      fitSearchResults();
      openCourtListPanel();
    });
    // Refocusing a non-empty search brings its suggestions back.
    searchInput.addEventListener('focus', () => {
      if (state.searchQ) searchCourts(state.searchQ);
    });
    $('#search-clear').addEventListener('click', () => {
      clearTimeout(searchTimer);
      searchInput.value = '';
      state.searchQ = '';
      hideSearchSuggest();
      syncSearchClear();
      searchInput.focus();
      fetchCourtsInView();
    });
    // Touching the map puts it back in charge: drop the suggestion overlay.
    state.map.getContainer().addEventListener('pointerdown', hideSearchSuggest);

    // Only auto-locate when we have neither a saved view nor a saved home area.
    if (!saved && !(state.me && state.me.home_lat != null)) locateMe(true);
    beginCourtContextRefresh();
    fetchCourtsInView({ surfaceError: true });
  }

  // ---------- Theme ----------
  // 'auto' follows the OS; 'light'/'dark' pin it. Stored per device.
  function themePref() { return localStorage.getItem('pp_theme') || 'auto'; }
  function themeIsDark() {
    const pref = themePref();
    return pref === 'dark'
      || (pref === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
  }
  function themeTileUrl() {
    return themeIsDark()
      ? 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
      : 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png';
  }
  function applyTheme() {
    const pref = themePref();
    if (pref === 'auto') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.dataset.theme = pref;
    const dark = themeIsDark();
    document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', dark ? 'dark' : 'light');
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#111614' : '#14532d');
    if (state.tileLayer) state.tileLayer.setUrl(themeTileUrl());
  }
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (themePref() === 'auto') applyTheme();
  });

  function locateMe(silent) {
    if (!navigator.geolocation) {
      if (!silent) toast('Location is not available on this device');
      return;
    }
    const btn = $('#locate-btn');
    if (!silent && btn) btn.classList.add('locating');
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        if (btn) btn.classList.remove('locating');
        state.userLoc = [pos.coords.latitude, pos.coords.longitude];
        state.areaLoc = null; // "my location" takes precedence again
        state.areaLabel = 'Near me';
        state.snapshotAreaProvisional = false;
        state.playGamesCache = null;
        state.chatFriendsCache = null;
        state.searchQ = '';
        const search = $('#court-search');
        if (search) search.value = '';
        hideSearchSuggest();
        syncSearchClear();
        beginCourtContextRefresh('Finding courts near your location…');
        moveCourtMapWithoutRefresh(() => state.map.setView(state.userLoc, 13, { animate: false }));
        updateUserDot();
        if (autoCheckInEnabled()) startLocationWatch();
        fetchCourtsInView({ surfaceError: true });
        if (!silent) toast('📍 Centered on your location');
      },
      (err) => {
        if (btn) btn.classList.remove('locating');
        if (!silent) {
          toast(err && err.code === 1
            ? 'Location is blocked — allow it in your browser settings'
            : 'Could not get your location right now');
        }
      },
      { timeout: 8000 },
    );
  }

  // The location the rest of the app's "near me" features follow: an explicitly
  // searched area wins, then GPS, then wherever the map is centered.
  function areaLatLng() {
    if (state.areaLoc) return { lat: state.areaLoc[0], lng: state.areaLoc[1] };
    if (state.userLoc) return { lat: state.userLoc[0], lng: state.userLoc[1] };
    if (state.me && state.me.home_lat != null) return { lat: state.me.home_lat, lng: state.me.home_lng };
    const c = state.map ? state.map.getCenter() : { lat: DEFAULT_CENTER[0], lng: DEFAULT_CENTER[1] };
    return { lat: c.lat, lng: c.lng };
  }

  function jumpToPlace(lat, lng, label) {
    state.areaLoc = [lat, lng];
    state.areaLabel = label || 'Selected area';
    state.snapshotAreaProvisional = false;
    state.playGamesCache = null;
    state.chatFriendsCache = null;
    const search = $('#court-search');
    if (search) { search.value = ''; search.blur(); }
    state.searchQ = '';
    hideSearchSuggest();
    syncSearchClear();
    beginCourtContextRefresh(`Finding courts near ${label || 'this area'}…`);
    if (state.map) moveCourtMapWithoutRefresh(
      () => state.map.setView([lat, lng], 12, { animate: false }),
    );
    setCourtSheetSnap('peek', { announce: false });
    updatePlayHeader();
    if (label) toast(`📍 ${label}`);
    fetchCourtsInView({ surfaceError: true });
  }

  async function loadFavIds() {
    if (!state.token) { state.favIds = new Set(); return; }
    try {
      const favs = await api('/courts/favorites');
      state.favIds = new Set((favs.items || []).map((c) => c.id));
    } catch (err) {
      if (!err.isStaleSession) state.favIds = new Set();
    }
  }

  const COURT_AMENITY_FILTERS = ['indoor', 'lighted', 'nets', 'restrooms', 'water'];

  function activeCourtFilterCount() {
    return Object.values(state.courtFilters).filter(Boolean).length;
  }

  function syncCourtFilterControls() {
    document.querySelectorAll('[data-court-filter]').forEach((btn) => {
      const active = !!state.courtFilters[btn.dataset.courtFilter];
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', String(active));
    });
    const amenityCount = COURT_AMENITY_FILTERS.filter((key) => state.courtFilters[key]).length;
    const more = $('#court-more-filters');
    const badge = $('#court-filter-count');
    if (more) more.classList.toggle('active', amenityCount > 0);
    if (badge) {
      badge.textContent = String(amenityCount);
      badge.classList.toggle('hidden', amenityCount === 0);
    }
  }

  function courtAmenityQuery() {
    return COURT_AMENITY_FILTERS
      .filter((key) => state.courtFilters[key])
      .map((key) => `&${key}=1`)
      .join('');
  }

  function applyCourtFilters(items) {
    return (items || []).filter((court) => {
      if (state.courtFilters.saved && !(state.favIds && state.favIds.has(court.id))) return false;
      if (state.courtFilters.players && !(court.players_here > 0)) return false;
      if (state.courtFilters.games && !(court.upcoming_games > 0)) return false;
      if (state.courtFilters.indoor && !court.indoor) return false;
      if (state.courtFilters.lighted && !court.lighted) return false;
      if (state.courtFilters.nets && !court.nets_provided) return false;
      if (state.courtFilters.restrooms && !court.has_restrooms) return false;
      if (state.courtFilters.water && !court.has_water) return false;
      return true;
    });
  }

  function addCourtDistances(items, reference) {
    return (items || []).map((court) => {
      if (court.distance_miles != null || court.latitude == null || !reference) return court;
      return {
        ...court,
        distance_miles: Number(milesBetween([reference.lat, reference.lng], [court.latitude, court.longitude]).toFixed(1)),
      };
    });
  }

  function refreshCourtResults() {
    return state.searchQ ? searchCourts(state.searchQ) : fetchCourtsInView();
  }

  function clearCourtFilters() {
    Object.keys(state.courtFilters).forEach((key) => { state.courtFilters[key] = false; });
    syncCourtFilterControls();
    refreshCourtResults();
  }

  function openCourtFilterSheet() {
    const draft = { ...state.courtFilters };
    const options = [
      ['indoor', '🏠', 'Indoor'],
      ['lighted', '💡', 'Lighted'],
      ['nets', '🥅', 'Nets provided'],
      ['restrooms', '🚻', 'Restrooms'],
      ['water', '🚰', 'Drinking water'],
    ];
    const modal = openModal(`
      ${modalHead('Filter courts')}
      <p class="row-sub" style="margin:-4px 0 14px">Choose everything you need. Filters work together, including while you search.</p>
      <div class="section-label" style="margin-top:0">Amenities</div>
      <div class="court-filter-grid">
        ${options.map(([key, icon, label]) => `
          <button type="button" class="court-filter-option ${draft[key] ? 'active' : ''}" data-filter-option="${key}" aria-pressed="${draft[key]}">
            <span style="font-size:18px;margin-right:5px">${icon}</span>${label}
          </button>`).join('')}
      </div>
      <div class="court-filter-actions">
        <button type="button" class="btn btn-secondary" id="court-filter-clear">Clear all</button>
        <button type="button" class="btn btn-primary" id="court-filter-apply">Show matches</button>
      </div>
    `, { label: 'Court filters' });
    $('#court-more-filters').setAttribute('aria-expanded', 'true');
    modal._cleanupFns.push(() => $('#court-more-filters')?.setAttribute('aria-expanded', 'false'));
    const syncDraft = () => {
      modal.querySelectorAll('[data-filter-option]').forEach((btn) => {
        const active = !!draft[btn.dataset.filterOption];
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', String(active));
      });
    };
    modal.querySelector('.court-filter-grid').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-filter-option]');
      if (!btn) return;
      draft[btn.dataset.filterOption] = !draft[btn.dataset.filterOption];
      syncDraft();
    });
    modal.querySelector('#court-filter-clear').addEventListener('click', () => {
      Object.keys(draft).forEach((key) => { draft[key] = false; });
      syncDraft();
    });
    modal.querySelector('#court-filter-apply').addEventListener('click', () => {
      state.courtFilters = draft;
      syncCourtFilterControls();
      closeModal(modal);
      refreshCourtResults();
    });
  }

  async function fetchCourtsInView({ surfaceError = false } = {}) {
    if (!state.map) return;
    if (state.searchQ) {
      // Search results own the list and markers — but data changes (check-ins,
      // favorites) still need to reach them, so re-run the search instead.
      return searchCourts(state.searchQ);
    }

    const seq = ++state.courtFetchSeq;
    if (state.favIds === null && !state.courtFilters.saved) await loadFavIds();
    const reference = areaLatLng();

    // Saved composes with activity and amenity filters, and ignores the bbox —
    // a player's saved courts remain useful even when they are off-screen.
    if (state.courtFilters.saved) {
      try {
        const favs = await api(`/courts/favorites?lat=${reference.lat}&lng=${reference.lng}`);
        if (seq !== state.courtFetchSeq || state.searchQ) return;
        state.favIds = new Set((favs.items || []).map((court) => court.id));
        const items = applyCourtFilters(addCourtDistances(favs.items, reference));
        state.courtsInView = items;
        drawMarkers(items);
        renderCourtList(items, [], { savedOnly: true });
      } catch (err) {
        if (surfaceError && seq === state.courtFetchSeq && !state.searchQ) {
          renderCourtContextError(err, () => {
            beginCourtContextRefresh();
            fetchCourtsInView({ surfaceError: true });
          });
        }
      }
      return;
    }
    const b = state.map.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(4)).join(',');
    let url = `/courts?bbox=${bbox}&limit=250&sort=${state.listSort}`;
    url += `&lat=${reference.lat}&lng=${reference.lng}${courtAmenityQuery()}`;
    try {
      const data = await api(url);
      if (seq !== state.courtFetchSeq || state.searchQ) return;
      const items = applyCourtFilters(data.items);
      state.courtsInView = items;
      drawMarkers(items);
      renderCourtList(items);
    } catch (err) {
      if (surfaceError && seq === state.courtFetchSeq && !state.searchQ) {
        renderCourtContextError(err, () => {
          beginCourtContextRefresh();
          fetchCourtsInView({ surfaceError: true });
        });
      }
    }
    refreshLookingBanner();
  }

  // "N players near you want to play" — a nudge toward a spontaneous game.
  async function refreshLookingBanner() {
    const el = $('#looking-banner');
    if (!el || !state.token) return;
    const c = areaLatLng();
    try {
      const data = await api(`/players/looking?lat=${c.lat}&lng=${c.lng}&radius=25`);
      if (!data.count) { el.classList.add('hidden'); return; }
      const names = data.players.map((p) => esc(p.display_name.split(' ')[0]));
      const who = data.count === 1 ? `${names[0]} wants` : `${data.count} players near you want`;
      el.innerHTML = `<svg class="pb-ic"><use href="#pb"/></svg> ${who} to play now <span class="chev">›</span>`;
      el.classList.remove('hidden');
      el.classList.toggle('below', !$('#presence-banner').classList.contains('hidden'));
    } catch { el.classList.add('hidden'); }
  }

  // ---------- Search suggestions (typeahead under the search bar) ----------
  function hideSearchSuggest() {
    const el = $('#search-suggest');
    if (el) { el.classList.add('hidden'); el.innerHTML = ''; }
    const input = $('#court-search');
    input?.setAttribute('aria-expanded', 'false');
    input?.removeAttribute('aria-activedescendant');
  }

  // Clear (✕) shows whenever there's text and no request in flight.
  function syncSearchClear() {
    const input = $('#court-search');
    const clear = $('#search-clear');
    const spin = $('#search-spin');
    if (!input || !clear || !spin) return;
    const busy = !spin.classList.contains('hidden');
    clear.classList.toggle('hidden', busy || !input.value.trim());
  }

  function setCourtSheetSnap(snap, { announce = true } = {}) {
    if (!['peek', 'half', 'full'].includes(snap)) return;
    const sheet = $('#court-list');
    if (!sheet) return;
    const previousSnap = state.courtSheetSnap;
    const listItems = $('#court-list-items');
    if (previousSnap !== 'peek' && snap === 'peek' && listItems) {
      state.courtListExpandedScrollTop = listItems.scrollTop;
    }
    state.courtSheetSnap = snap;
    sheet.dataset.snap = snap;
    sheet.style.removeProperty('transform');
    sheet.classList.remove('is-dragging');
    const listOpen = snap !== 'peek';
    document.querySelectorAll('#court-view-switch [data-court-view]').forEach((btn) => {
      const active = btn.dataset.courtView === (listOpen ? 'list' : 'map');
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-selected', String(active));
    });
    const cycle = $('#court-sheet-cycle');
    if (cycle) {
      cycle.setAttribute('aria-expanded', String(listOpen));
      cycle.setAttribute('aria-label', snap === 'peek' ? 'Expand court results'
        : snap === 'half' ? 'Show full court results' : 'Collapse court results to map');
    }
    const expand = $('#court-sheet-expand');
    if (expand) expand.setAttribute('aria-label', snap === 'full' ? 'Collapse court list' : 'Show full court list');
    if (listOpen) hideSearchSuggest();
    const main = $('#main-screen');
    if (main) {
      main.scrollTop = 0;
      requestAnimationFrame(() => { main.scrollTop = 0; });
    }
    if (announce) {
      const title = sheet.querySelector('.sheet-title')?.textContent || 'Court results';
      sheet.setAttribute('aria-label', `${title}, ${listOpen ? 'list view' : 'map view'}`);
    }
    if ((previousSnap === 'peek') !== (snap === 'peek') && state.courtsInView.length) {
      renderCourtList(state.courtsInView, state.courtListPlaces, {
        savedOnly: state.courtListSavedOnly,
        preserveLimit: true,
      });
      if (previousSnap === 'peek' && snap !== 'peek') {
        requestAnimationFrame(() => {
          const currentList = $('#court-list-items');
          if (currentList) currentList.scrollTop = state.courtListExpandedScrollTop;
        });
      }
    }
  }

  function setupCourtSheetDrag() {
    const handle = $('#court-sheet-cycle');
    const sheet = $('#court-list');
    if (!handle || !sheet || !window.PointerEvent) return;
    let pointerId = null;
    let startY = 0;
    let startShift = 0;
    let lastY = 0;
    let lastAt = 0;
    let velocity = 0;
    let moved = false;
    const shiftForSnap = (snap) => {
      const h = sheet.getBoundingClientRect().height;
      if (snap === 'full') return 0;
      if (snap === 'half') {
        const bannerOffset = $('#app')?.classList.contains('has-banner') ? 58 : 0;
        return window.innerHeight < 500 ? 0 : Math.max(0, h * 0.42 - bannerOffset);
      }
      return Math.max(0, h - 146);
    };
    handle.addEventListener('pointerdown', (e) => {
      if (pointerId != null) return;
      pointerId = e.pointerId;
      startY = lastY = e.clientY;
      lastAt = performance.now();
      startShift = shiftForSnap(state.courtSheetSnap);
      velocity = 0;
      moved = false;
      handle.setPointerCapture(pointerId);
      sheet.classList.add('is-dragging');
    });
    handle.addEventListener('pointermove', (e) => {
      if (e.pointerId !== pointerId) return;
      const now = performance.now();
      const dt = Math.max(1, now - lastAt);
      velocity = (e.clientY - lastY) / dt;
      lastY = e.clientY;
      lastAt = now;
      const maxShift = shiftForSnap('peek');
      const shift = Math.max(0, Math.min(maxShift, startShift + e.clientY - startY));
      if (Math.abs(e.clientY - startY) > 5) moved = true;
      sheet.style.transform = `translateY(${shift}px)`;
    });
    const finish = (e) => {
      if (e.pointerId !== pointerId) return;
      try { handle.releasePointerCapture(pointerId); } catch { /* already released */ }
      pointerId = null;
      const h = sheet.getBoundingClientRect().height;
      const projected = Math.max(0, Math.min(h, startShift + e.clientY - startY + velocity * 160));
      const ratio = projected / Math.max(1, h);
      state.courtSheetJustDragged = moved;
      setCourtSheetSnap(ratio < 0.22 ? 'full' : ratio < 0.72 ? 'half' : 'peek');
    };
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
  }

  function syncCourtSheetSummary(courts, { savedOnly = false, searching = false } = {}) {
    const title = document.querySelector('#court-list .sheet-title');
    const context = $('#court-list-context');
    const count = $('#court-result-count');
    const n = courts.length;
    if (count) count.textContent = n ? String(n) : '0';
    if (title) title.textContent = savedOnly ? 'Saved courts'
      : searching ? 'Search results' : n ? `${n} court${n === 1 ? '' : 's'} nearby` : 'No matching courts';
    if (context) {
      const active = [];
      if (state.courtFilters.saved) active.push('saved');
      if (state.courtFilters.players) active.push('players here');
      if (state.courtFilters.games) active.push('open games');
      const amenities = COURT_AMENITY_FILTERS.filter((key) => state.courtFilters[key]).length;
      if (amenities) active.push(`${amenities} ${amenities === 1 ? 'amenity' : 'amenities'}`);
      context.textContent = active.length ? `${searching ? `For “${state.searchQ}” · ` : ''}Matching ${active.join(' · ')}`
        : searching ? `For “${state.searchQ}”` : 'Tap a court to compare and act';
    }
  }

  function openCourtListPanel() {
    setCourtSheetSnap('half');
  }

  function fitSearchResults() {
    const pts = (state.courtsInView || []).filter((c) => c.latitude != null);
    if (pts.length) {
      moveCourtMapWithoutRefresh(() => state.map.fitBounds(
        pts.map((c) => [c.latitude, c.longitude]),
        { maxZoom: 13, padding: [40, 40], animate: false },
      ));
    }
  }

  function renderSearchSuggest(courts, places, q) {
    const el = $('#search-suggest');
    if (!el) return;
    // Only surface suggestions while this query is still what's typed.
    if (!q || state.searchQ !== q) { hideSearchSuggest(); return; }
    let html = '';
    if (places.length) {
      html += '<div class="sug-label">📍 Jump to area</div>';
      html += places.slice(0, 4).map((p, i) => `
        <button class="sug-row" role="option" aria-selected="false" data-sug-place="${i}">
          <span class="sug-ico">📍</span>
          <span class="sug-main">
            <span class="sug-title" style="display:block">${esc(p.label)}</span>
            <span class="sug-sub" style="display:block">${esc((p.detail || '').split(',').slice(1, 4).join(',').trim())}</span>
          </span>
          <span class="chev">›</span>
        </button>`).join('');
    }
    if (courts.length) {
      html += '<div class="sug-label">🏓 Courts</div>';
      html += courts.slice(0, 5).map((c) => `
        <button class="sug-row" role="option" aria-selected="false" data-sug-court="${c.id}">
          <span class="sug-ico">🏓</span>
          <span class="sug-main">
            <span class="sug-title" style="display:block">${esc(c.name)}</span>
            <span class="sug-sub" style="display:block">${[
              esc(c.city || ''),
              c.distance_miles != null ? `${c.distance_miles} mi` : '',
              `${c.num_courts} court${c.num_courts === 1 ? '' : 's'}`,
              c.rating_avg ? `⭐ ${c.rating_avg}` : '',
            ].filter(Boolean).join(' · ')}</span>
          </span>
          <span class="chev">›</span>
        </button>`).join('');
      if (courts.length > 5) {
        html += `<button class="sug-row sug-all" role="option" aria-selected="false" data-sug-all>See all ${courts.length} courts</button>`;
      }
    }
    if (!html) {
      html = `<div class="sug-empty">🔎 Nothing matches “${esc(q)}”.<br>Try a court name or a city.</div>`;
    }
    el.innerHTML = html;
    el.classList.remove('hidden');
    [...el.querySelectorAll('[role="option"]')].forEach((row, i) => { row.id = `court-suggestion-${i}`; });
    $('#court-search')?.setAttribute('aria-expanded', String(!!el.querySelector('[role="option"]')));
    el.querySelectorAll('[data-sug-place]').forEach((row) => {
      const p = places[Number(row.dataset.sugPlace)];
      if (p) row.addEventListener('click', () => { hideSearchSuggest(); jumpToPlace(p.lat, p.lng, p.label); });
    });
    el.querySelectorAll('[data-sug-court]').forEach((row) => {
      row.addEventListener('click', () => { hideSearchSuggest(); openCourtDetail(Number(row.dataset.sugCourt)); });
    });
    el.querySelector('[data-sug-all]')?.addEventListener('click', () => {
      hideSearchSuggest();
      $('#court-search').blur();
      fitSearchResults();
      openCourtListPanel();
    });
  }

  let searchSeq = 0;
  async function searchCourts(q) {
    const seq = ++searchSeq;
    state.courtFetchSeq += 1; // invalidate any older map-bounds response
    const spin = $('#search-spin');
    if (spin) spin.classList.remove('hidden');
    syncSearchClear();
    try {
      if (state.favIds === null) await loadFavIds();
      const reference = areaLatLng();
      const [courtData, placeData] = await Promise.all([
        api(`/courts?q=${encodeURIComponent(q)}&limit=50&lat=${reference.lat}&lng=${reference.lng}${courtAmenityQuery()}`),
        api(`/geocode?q=${encodeURIComponent(q)}`).catch(() => ({ items: [] })),
      ]);
      // A newer keystroke owns the UI now — drop this stale response.
      if (seq !== searchSeq || state.searchQ !== q) return;
      const items = applyCourtFilters(courtData.items);
      state.courtsInView = items;
      drawMarkers(items);
      renderCourtList(items, placeData.items || []);
      // Only surface the dropdown while the user is actually in the search box —
      // background refreshes (map pans, check-ins) must not pop it open.
      if (document.activeElement === $('#court-search')) {
        renderSearchSuggest(items, placeData.items || [], q);
      }
      // The map stays put while you type — it only jumps once you commit
      // (Enter / "See all"), via fitSearchResults().
    } catch { /* ignore */ } finally {
      if (seq === searchSeq && spin) { spin.classList.add('hidden'); syncSearchClear(); }
    }
  }

  function courtMarkerIcon(court, selected = state.selectedCourtId === court.id) {
    const busy = court.players_here > 0;
    const fav = state.favIds && state.favIds.has(court.id);
    const size = busy ? 34 : 26;
    const gameBadge = court.upcoming_games > 0
      ? `<span class="marker-game-badge">${court.upcoming_games}</span>` : '';
    const favBadge = fav ? '<span class="marker-fav-badge">★</span>' : '';
    // A fresh problem report (wet, nets down, closed, busy) rides on the
    // marker so players see it before driving out. "All good" stays quiet.
    const condBadge = court.condition && court.condition !== 'good' && COURT_CONDITION_LABELS[court.condition]
      ? `<span class="marker-cond-badge" title="${COURT_CONDITION_LABELS[court.condition][1]}">${COURT_CONDITION_LABELS[court.condition][0]}</span>` : '';
    const markerLabel = `${court.name}. ${busy ? `${court.players_here} playing now` : `${court.num_courts} court${court.num_courts === 1 ? '' : 's'}`}${court.upcoming_games ? `. ${court.upcoming_games} open game${court.upcoming_games === 1 ? '' : 's'}` : ''}`;
    return L.divIcon({
      className: '',
      html: `<div class="court-marker ${busy ? 'busy' : ''} ${fav ? 'fav' : ''} ${selected ? 'selected' : ''}" role="img" aria-label="${esc(markerLabel)}" style="width:${size}px;height:${size}px">${busy ? court.players_here + '👤' : court.num_courts}${gameBadge}${favBadge}${condBadge}</div>`,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    });
  }

  function drawMarkers(courts) {
    state.markers.clearLayers();
    state.courtMarkers.clear();
    courts.forEach((court) => {
      if (court.latitude == null) return;
      const marker = L.marker([court.latitude, court.longitude], {
        icon: courtMarkerIcon(court),
      }).addTo(state.markers).on('click', () => selectCourtOnMap(court));
      state.courtMarkers.set(court.id, { marker, court });
    });
  }

  function setCourtMarkerSelected(courtId, selected) {
    const entry = state.courtMarkers.get(courtId);
    if (entry) entry.marker.setIcon(courtMarkerIcon(entry.court, selected));
  }

  function courtDirectionsUrl(court) {
    const address = [court.address, court.city].filter(Boolean).join(' ');
    return /iPhone|iPad|Macintosh/.test(navigator.userAgent)
      ? `https://maps.apple.com/?daddr=${encodeURIComponent(address || `${court.latitude},${court.longitude}`)}`
      : `https://www.google.com/maps/dir/?api=1&destination=${court.latitude},${court.longitude}`;
  }

  function selectCourtOnMap(court, { preserveList = false } = {}) {
    if (!court) return;
    const previousCourtId = state.selectedCourtId;
    state.selectedCourtId = court.id;
    if (previousCourtId !== court.id) {
      setCourtMarkerSelected(previousCourtId, false);
      setCourtMarkerSelected(court.id, true);
    }
    const preview = $('#court-preview');
    if (preview) {
      const live = court.players_here
        ? `${court.players_here} playing now`
        : court.upcoming_games ? `${court.upcoming_games} open game${court.upcoming_games === 1 ? '' : 's'}` : 'Quiet right now';
      preview.innerHTML = `
        <div class="row">
          <div class="row-main">
            <div class="row-title">${esc(court.name)}</div>
            <div class="row-sub">${[court.distance_miles != null ? `${court.distance_miles} mi` : '', esc(court.city || ''), live].filter(Boolean).join(' · ')}</div>
          </div>
          ${court.rating_avg ? `<span class="tag" style="margin:0">⭐ ${court.rating_avg}</span>` : ''}
        </div>
        <div class="court-preview-actions">
          <button type="button" class="btn btn-secondary" data-preview-detail>Details</button>
          <button type="button" class="btn btn-primary" data-preview-play>Play here</button>
          <a class="btn btn-secondary" data-preview-directions href="${courtDirectionsUrl(court)}" target="_blank" rel="noopener" style="display:flex;align-items:center;justify-content:center">Directions</a>
        </div>`;
      preview.classList.remove('hidden');
      preview.querySelector('[data-preview-detail]').addEventListener('click', () => openCourtDetail(court.id));
      preview.querySelector('[data-preview-play]').addEventListener('click', () => {
        openNewGameModal({ id: court.id, name: court.name }, 'casual', true);
      });
    }
    document.querySelectorAll('#court-list-items [data-court]').forEach((row) => {
      row.classList.toggle('selected', Number(row.dataset.court) === court.id);
    });
    if (!preserveList || state.courtSheetSnap === 'peek') setCourtSheetSnap('half');
    if (state.map && court.latitude != null) {
      try {
        const bottomPadding = Math.min(330, window.innerHeight * 0.48);
        const point = state.map.latLngToContainerPoint([court.latitude, court.longitude]);
        const size = state.map.getSize();
        const needsPan = point.x < 24 || point.x > size.x - 24
          || point.y < 24 || point.y > size.y - bottomPadding;
        if (needsPan) moveCourtMapWithoutRefresh(() => state.map.panInside(
          [court.latitude, court.longitude], {
            paddingTopLeft: [24, 24], paddingBottomRight: [24, bottomPadding], animate: false,
          },
        ));
      } catch { /* Leaflet version fallback: selection still works */ }
    }
  }

  // ---------- Live location & auto check-in ----------
  function autoCheckInStorageKey(userId = state.me && state.me.id) {
    return userId ? `pp_auto_checkin:${userId}` : null;
  }

  function autoCheckInEnabled() {
    // The old origin-global preference is intentionally not migrated: on a
    // shared browser, a newly signed-in account must grant its own consent.
    try { localStorage.removeItem('pp_auto_checkin'); } catch { /* unavailable */ }
    const key = autoCheckInStorageKey();
    return !!key && localStorage.getItem(key) === 'on';
  }

  function setAutoCheckInEnabled(enabled) {
    const key = autoCheckInStorageKey();
    if (!key) return;
    localStorage.setItem(key, enabled ? 'on' : 'off');
  }

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
    if (!autoCheckInEnabled() || document.hidden || !navigator.geolocation || state.geoWatchId != null) return;
    state.geoWatchId = navigator.geolocation.watchPosition(
      (pos) => {
        state.userLoc = [pos.coords.latitude, pos.coords.longitude];
        updateUserDot();
        maybeAutoCheckIn();
      },
      () => { /* permission denied or unavailable */ },
      { enableHighAccuracy: false, maximumAge: 60000, timeout: 20000 },
    );
  }

  function stopLocationWatch() {
    if (state.geoWatchId == null || !navigator.geolocation) return;
    navigator.geolocation.clearWatch(state.geoWatchId);
    state.geoWatchId = null;
  }

  function openAutoCheckInConsent(onChange) {
    const modal = openModal(`
      ${modalHead('Auto check-in')}
      <div class="consent-hero" aria-hidden="true">📍</div>
      <p style="font-weight:800;margin-bottom:6px">Arrive, play, and let the app handle check-in.</p>
      <p class="row-sub" style="margin-bottom:12px">When Third Shot is open, it can use your location to check you in near a court and check you out after you leave.</p>
      <div class="privacy-note">
        <b>Who can see it?</b>
        <span>Players viewing that court can see that you're there. Your precise live location is never shown.</span>
      </div>
      <button type="button" class="btn btn-primary btn-block" id="auto-checkin-enable" style="margin-top:14px">Allow while the app is open</button>
      <button type="button" class="btn btn-secondary btn-block modal-close" style="margin-top:8px">Not now</button>
    `);
    modal.querySelector('#auto-checkin-enable').addEventListener('click', () => {
      setAutoCheckInEnabled(true);
      closeModal(modal);
      startLocationWatch();
      toast('Auto check-in on 📍');
      if (onChange) onChange();
    });
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
    if (!autoCheckInEnabled()) return;
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
    const reason = state.listSort === 'active'
      ? (c.players_here ? `${c.players_here} playing now`
        : c.upcoming_games ? `${c.upcoming_games} game${c.upcoming_games === 1 ? '' : 's'} coming up` : 'Ready when you are')
      : state.listSort === 'rating'
        ? (c.rating_avg ? `Rated ${c.rating_avg} by ${c.rating_count} player${c.rating_count === 1 ? '' : 's'}` : 'Not rated yet')
        : state.listSort === 'courts'
          ? `${c.num_courts} court${c.num_courts === 1 ? '' : 's'} at this location`
          : (c.distance_miles != null ? `${c.distance_miles} miles away` : esc(c.city || 'In this map area'));
    const tags = [];
    if (state.favIds && state.favIds.has(c.id)) tags.push('⭐ Saved');
    tags.push(c.indoor ? '🏠 Indoor' : '☀️ Outdoor');
    if (c.lighted) tags.push('💡 Lights');
    if (c.nets_provided) tags.push('🥅 Nets');
    if (c.has_restrooms) tags.push('🚻 Restrooms');
    if (c.has_water) tags.push('🚰 Water');
    return `
      <button type="button" class="court-decision-card ${state.selectedCourtId === c.id ? 'selected' : ''}" data-court="${c.id}" aria-label="Select ${esc(c.name)}">
        <span class="court-card-head">
          <span class="court-card-name">${esc(c.name)}${cond ? ` <span class="tag ${c.condition === 'good' ? 'live' : 'warn'}" style="margin:0 0 0 5px;font-size:10px;padding:2px 7px">${cond[0]} ${esc(cond[1].split(' — ')[0].split(' /')[0])}</span>` : ''}</span>
          <span class="court-card-distance">${c.distance_miles != null ? `${c.distance_miles} mi` : esc(c.city || '')}</span>
        </span>
        <span class="court-card-reason">${reason}</span>
        <span class="court-card-metrics">
          <span class="court-card-metric"><b>${c.players_here || 0}</b><span>here now</span></span>
          <span class="court-card-metric"><b>${c.upcoming_games || 0}</b><span>open games</span></span>
          <span class="court-card-metric"><b>${c.rating_avg ? `⭐ ${c.rating_avg}` : '—'}</b><span>${c.rating_count || 0} ratings</span></span>
        </span>
        <span class="court-card-tags">
          <span class="tag">${c.num_courts} court${c.num_courts === 1 ? '' : 's'}</span>
          ${tags.slice(0, 4).map((tag) => `<span class="tag">${tag}</span>`).join('')}
        </span>
      </button>
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
    } else if (state.listSort === 'active') {
      const nowScore = (c) => (c.players_here || 0) * 6
        + (c.upcoming_games || 0) * 3
        + (c.rating_avg || 0) * 0.7
        + Math.min(c.num_courts || 0, 12) * 0.15
        + (c.condition === 'good' ? 1 : ['wet', 'closed', 'nets_down'].includes(c.condition) ? -4 : 0)
        - Math.min(c.distance_miles || 0, 30) * 0.12;
      sorted.sort((a, b) => nowScore(b) - nowScore(a)
        || (a.distance_miles ?? 1e9) - (b.distance_miles ?? 1e9));
    } else if (courts.some((c) => c.distance_miles != null)) {
      sorted.sort((a, b) => (a.distance_miles ?? 1e9) - (b.distance_miles ?? 1e9));
    }
    return sorted;
  }

  function renderCourtList(courts, places = [], { savedOnly = false, preserveLimit = false } = {}) {
    const el = $('#court-list-items');
    courts = sortCourts(courts);
    state.courtListPlaces = places;
    state.courtListSavedOnly = savedOnly;
    const resultSignature = JSON.stringify({
      query: state.searchQ || '',
      sort: state.listSort,
      savedOnly,
      filters: state.courtFilters,
      courts: courts.map((court) => court.id),
      places: places.map((place) => [place.lat, place.lng]),
    });
    if (!preserveLimit && resultSignature !== state.courtListSignature) {
      state.courtListLimit = 20;
      state.courtListExpandedScrollTop = 0;
    }
    state.courtListSignature = resultSignature;
    let html = '';
    const searching = !!state.searchQ && !savedOnly;
    const hasFilters = activeCourtFilterCount() > 0;
    const hasNarrowingFilters = Object.entries(state.courtFilters)
      .some(([key, active]) => key !== 'saved' && active);
    const emptyResultHtml = () => {
      const icon = savedOnly ? '⭐' : searching ? '🔎' : hasFilters ? '⚙️' : '🗺️';
      const message = savedOnly
        ? (hasNarrowingFilters ? 'No saved courts match all of these filters.' : 'No saved courts yet. Tap ☆ on a court to keep it handy.')
        : searching && places.length && hasFilters ? 'No courts in this area match all of your filters.'
          : searching ? `Nothing matches “${esc(state.searchQ)}”. Try a court name or city.`
          : hasFilters ? 'No courts match all of these filters in this area.'
            : 'No courts here yet. Move the map, zoom out, or search another area.';
      return `<div class="court-result-empty"><span class="big">${icon}</span>${message}
        ${hasFilters ? '<button type="button" class="btn btn-secondary" id="court-clear-results">Clear filters</button>' : ''}</div>`;
    };
    const recoveryBeforePlaces = !courts.length && places.length && hasFilters;
    syncCourtSheetSummary(courts, { savedOnly, searching });

    // Filter recovery is the primary answer; related place jumps remain just
    // below it instead of pushing the action behind the active-game banner.
    if (recoveryBeforePlaces) html += emptyResultHtml();
    if (places.length) {
      html += '<div class="section-label" style="margin-top:4px">📍 Jump to area</div>';
      html += places.map((p, i) => `
        <button type="button" class="card row" data-place="${i}" style="cursor:pointer;width:100%;text-align:left;color:var(--ink)">
          <span style="font-size:18px">📍</span>
          <div class="row-main">
            <div class="row-title">${esc(p.label)}</div>
            <div class="row-sub">${esc((p.detail || '').split(',').slice(1, 4).join(',').trim())}</div>
          </div>
          <span class="chev">›</span>
        </button>`).join('');
      html += '<div class="section-label">Courts</div>';
    }

    if (courts.length) {
      const visibleLimit = state.courtSheetSnap === 'peek' ? 8 : state.courtListLimit;
      const visibleCourts = courts.slice(0, visibleLimit);
      html += visibleCourts.map(courtRowHtml).join('');
      if (visibleCourts.length < courts.length) {
        const remaining = courts.length - visibleCourts.length;
        const label = state.courtSheetSnap === 'peek'
          ? `Browse all ${courts.length} courts`
          : `Show ${Math.min(20, remaining)} more · ${remaining} remaining`;
        html += `<button type="button" class="btn btn-primary btn-block" id="court-show-more" style="margin:2px 0 10px">${label}</button>`;
      }
    } else if (!recoveryBeforePlaces) html += emptyResultHtml();
    html += `<button class="btn btn-secondary btn-block" id="list-add-court" style="margin-top:10px">➕ Missing a court? Add it</button>`;

    el.innerHTML = html;
    el.querySelector('#list-add-court').addEventListener('click', openAddCourtSheet);
    el.querySelector('#court-clear-results')?.addEventListener('click', clearCourtFilters);
    el.querySelector('#court-show-more')?.addEventListener('click', () => {
      if (state.courtSheetSnap === 'peek') {
        const firstNewIndex = 8;
        setCourtSheetSnap('half');
        el.querySelectorAll('[data-court]')[firstNewIndex]?.focus({ preventScroll: true });
        return;
      }
      const scrollTop = el.scrollTop;
      const firstNewIndex = state.courtListLimit;
      state.courtListLimit += 20;
      renderCourtList(courts, places, { savedOnly, preserveLimit: true });
      el.scrollTop = scrollTop;
      el.querySelectorAll('[data-court]')[firstNewIndex]?.focus({ preventScroll: true });
    });
    const byId = new Map(courts.map((court) => [court.id, court]));
    el.querySelectorAll('[data-court]').forEach((row) => {
      row.addEventListener('click', () => selectCourtOnMap(
        byId.get(Number(row.dataset.court)),
        { preserveList: state.courtSheetSnap !== 'peek' },
      ));
    });
    el.querySelectorAll('[data-place]').forEach((row) => {
      const p = places[Number(row.dataset.place)];
      if (p) row.addEventListener('click', () => jumpToPlace(p.lat, p.lng, p.label));
    });
    if (state.selectedCourtId && !byId.has(state.selectedCourtId)) {
      state.selectedCourtId = null;
      $('#court-preview')?.classList.add('hidden');
    }
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
      <div class="form-field">
        <label>Hours</label>
        <input type="text" id="se-hours" maxlength="120" placeholder="e.g. Daily 6am–10pm, Dawn to dusk" value="${esc(court.hours || '')}" />
      </div>
      <div class="form-field" style="border-top:1px solid var(--line);padding-top:12px">
        ${check('se-closed', '🚫 This court is permanently closed / gone', court.closed)}
        <p class="row-sub" style="margin-top:4px">Once another player confirms, it's hidden from the map.</p>
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
        hours: modal.querySelector('#se-hours').value.trim(),
        closed: modal.querySelector('#se-closed').checked,
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
  const OVERLAY_NAV_KEY = 'ppOverlayV1';
  const OVERLAY_ROUTE_KINDS = new Set(['court', 'game', 'tournament', 'club', 'league']);
  const previousOverlayNav = history.state && history.state[OVERLAY_NAV_KEY];
  const overlaySession = previousOverlayNav && previousOverlayNav.v === 1 && previousOverlayNav.session
    ? previousOverlayNav.session
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
  const overlayStack = [];
  let reusableOverlayEntry = null;
  let reusableOverlayTimer = null;
  let dismissAllCallbacks = [];
  let dismissAllRequested = false;
  let overlayHistoryRevision = 0;
  let routedOverlayLoadSeq = 0;
  let activeRoutedOverlayLoad = null;
  let pendingReusableTraversal = null;
  let suppressNativeHashRoute = null;
  let pendingDeepMatchRebuild = null;
  const baseAppUrl = () => `${location.pathname}${location.search}`;
  const normalizeOverlayRoute = (route) => {
    if (!route) return null;
    if (typeof route === 'string') {
      const match = route.match(/^#(court|game|tournament|club|league)\/(\d+)(?:\/match\/(\d+))?$/);
      if (!match) return null;
      const normalized = { kind: match[1], id: Number(match[2]) };
      if (match[3] && (normalized.kind === 'league' || normalized.kind === 'tournament')) {
        normalized.matchId = Number(match[3]);
      }
      return normalized;
    }
    const id = Number(route.id);
    if (!OVERLAY_ROUTE_KINDS.has(route.kind) || !Number.isSafeInteger(id) || id <= 0) return null;
    const normalized = { kind: route.kind, id };
    const matchId = Number(route.matchId);
    if ((route.kind === 'league' || route.kind === 'tournament')
        && Number.isSafeInteger(matchId) && matchId > 0) normalized.matchId = matchId;
    return normalized;
  };
  const overlayRouteHash = (route) => route
    ? `#${route.kind}/${route.id}${route.matchId ? `/match/${route.matchId}` : ''}` : '';
  const sameOverlayRoute = (left, right) => !!left && !!right
    && left.kind === right.kind && left.id === right.id
    && (left.matchId || null) === (right.matchId || null);
  const overlayUrl = (route) => `${baseAppUrl()}${overlayRouteHash(route)}`;
  const overlayHistoryState = (id, depth, route) => ({
    ...(history.state || {}),
    [OVERLAY_NAV_KEY]: { v: 1, session: overlaySession, id, depth, route: route || null },
  });
  const previousRoute = normalizeOverlayRoute(previousOverlayNav && previousOverlayNav.route);
  let adoptOverlayEntry = previousOverlayNav && previousOverlayNav.v === 1
    && previousOverlayNav.session === overlaySession && previousOverlayNav.id && previousRoute
    && location.hash === overlayRouteHash(previousRoute)
    ? { ...previousOverlayNav, route: previousRoute } : null;
  if (!adoptOverlayEntry) {
    try { history.replaceState(overlayHistoryState(null, 0, null), '', location.href); } catch { /* history can be unavailable */ }
  }

  function currentOverlayEntry() { return overlayStack[overlayStack.length - 1] || null; }
  function liveOverlayHistoryDepth() {
    return overlayStack.reduce((depth, entry) => depth + (entry.historyPops || 1), 0);
  }

  // Shared routes can take a network round-trip to render. Remember whether a
  // load is adopting the current history entry so Back during that wait cannot
  // resurrect the sheet after the user has already left it. For ordinary UI
  // taps, a newer routed tap wins without disrupting intentional close→open
  // transitions that finish after their old history slot unwinds.
  function beginRoutedOverlayLoad(route) {
    const normalized = normalizeOverlayRoute(route);
    const pendingAdoption = normalized && adoptOverlayEntry
      && adoptOverlayEntry.route.kind === normalized.kind
      && adoptOverlayEntry.route.id === normalized.id
      && (adoptOverlayEntry.route.matchId || null) === (normalized.matchId || null)
      ? adoptOverlayEntry : null;
    const load = {
      seq: ++routedOverlayLoadSeq,
      route: normalized,
      adoptionId: pendingAdoption ? pendingAdoption.id : null,
      historyRevision: overlayHistoryRevision,
      originOverlayId: currentOverlayEntry()?.id || null,
      expectedReusableId: reusableOverlayEntry?.id || null,
    };
    activeRoutedOverlayLoad = load;
    return load;
  }

  function routedOverlayLoadIsCurrent(load) {
    if (!load) return true;
    if (load.seq !== routedOverlayLoadSeq || load.historyRevision !== overlayHistoryRevision) return false;
    if ((currentOverlayEntry()?.id || null) !== load.originOverlayId) return false;
    if (!load.adoptionId) return true;
    const nav = history.state && history.state[OVERLAY_NAV_KEY];
    return overlayHistoryRevision === load.historyRevision
      && adoptOverlayEntry && adoptOverlayEntry.id === load.adoptionId
      && nav && nav.session === overlaySession && nav.id === load.adoptionId
      && load.route && location.hash === overlayRouteHash(load.route);
  }

  function cancelPendingOverlayLoadForNavigation() {
    routedOverlayLoadSeq += 1;
    activeRoutedOverlayLoad = null;
    if (adoptOverlayEntry && !overlayStack.length) {
      adoptOverlayEntry = null;
      try { history.replaceState(overlayHistoryState(null, 0, null), '', baseAppUrl()); } catch { /* ignore */ }
    }
  }

  function syncModalStack() {
    const top = currentOverlayEntry();
    overlayStack.forEach((entry) => {
      const active = entry === top;
      entry.el.toggleAttribute('inert', !active);
      entry.el.setAttribute('aria-hidden', String(!active));
      const dialog = entry.el.querySelector('.modal');
      if (dialog) dialog.setAttribute('aria-modal', String(active));
    });
    const main = $('#main-screen');
    if (top) {
      document.documentElement.classList.add('modal-open');
      if (main) { main.setAttribute('inert', ''); main.setAttribute('aria-hidden', 'true'); }
    } else {
      document.documentElement.classList.remove('modal-open');
      if (main) { main.removeAttribute('inert'); main.removeAttribute('aria-hidden'); }
    }
  }

  function focusAfterModalChange(removed, restoreFocus = true) {
    if (!restoreFocus) return;
    const top = currentOverlayEntry();
    if (top) {
      const candidate = removed && removed._returnFocus;
      const target = candidate && candidate.isConnected && top.el.contains(candidate)
        ? candidate : top.el.querySelector('.modal');
      requestAnimationFrame(() => target?.focus({ preventScroll: true }));
      return;
    }
    const target = removed && removed._returnFocus;
    if (target && target.isConnected && !target.closest('[inert]')) {
      requestAnimationFrame(() => target.focus({ preventScroll: true }));
    }
  }

  function destroyModal(el, { restoreFocus = true } = {}) {
    if (!el || el._destroyed) return;
    el._destroyed = true;
    (el._cleanupFns || []).forEach((fn) => { try { fn(); } catch { /* cleanup must not block close */ } });
    if (el._cleanup) { try { el._cleanup(); } catch { /* backwards compatibility */ } }
    el.remove();
    syncModalStack();
    focusAfterModalChange(el, restoreFocus);
  }

  function normalizeHistoryToLiveOverlay() {
    const top = currentOverlayEntry();
    const route = top ? top.route : null;
    try {
      history.replaceState(
        overlayHistoryState(top ? top.id : null, liveOverlayHistoryDepth(), route),
        '',
        top ? overlayUrl(route) : baseAppUrl(),
      );
    } catch { /* history can be unavailable */ }
  }

  function dismissModal(el, afterClose) {
    const top = currentOverlayEntry();
    if (!top || top.el !== el || top.closing) return;
    if (afterClose) top.afterClose.push(afterClose);
    top.closing = true;
    const nav = history.state && history.state[OVERLAY_NAV_KEY];
    if (nav && nav.session === overlaySession && nav.id === top.id) {
      try { history.go(-(top.historyPops || 1)); return; } catch { /* fall through */ }
    }
    overlayStack.pop();
    destroyModal(el);
    normalizeHistoryToLiveOverlay();
    top.afterClose.splice(0).forEach((fn) => { try { fn(); } catch { /* callback isolation */ } });
  }

  function dismissAllModals(afterClose) {
    if (afterClose) dismissAllCallbacks.push(afterClose);
    const nav = history.state && history.state[OVERLAY_NAV_KEY];
    const reusable = reusableOverlayEntry;
    const reusableOwnsCurrent = reusable && nav && nav.session === overlaySession && nav.id === reusable.id;
    const reusablePops = reusableOwnsCurrent ? Math.max(1, reusable.pops || 1) : 0;
    if (reusable) {
      clearTimeout(reusableOverlayTimer);
      reusableOverlayTimer = null;
      reusableOverlayEntry = null;
      pendingReusableTraversal = null;
    }
    if (!overlayStack.length) {
      if (reusableOwnsCurrent) {
        dismissAllRequested = true;
        try { history.go(-reusablePops); return; } catch { normalizeHistoryToLiveOverlay(); }
      } else if (reusable) {
        normalizeHistoryToLiveOverlay();
      }
      dismissAllRequested = false;
      dismissAllCallbacks.splice(0).forEach((fn) => { try { fn(); } catch { /* callback isolation */ } });
      return;
    }
    dismissAllRequested = true;
    const top = currentOverlayEntry();
    if (top.closing && !reusableOwnsCurrent) return;
    const currentOwnsStack = nav && nav.session === overlaySession && nav.id === top.id;
    if (!currentOwnsStack && !reusableOwnsCurrent) {
      while (overlayStack.length) destroyModal(overlayStack.pop().el, { restoreFocus: false });
      normalizeHistoryToLiveOverlay();
      dismissAllRequested = false;
      dismissAllCallbacks.splice(0).forEach((fn) => { try { fn(); } catch { /* callback isolation */ } });
      return;
    }
    top.closing = true;
    try { history.go(-(liveOverlayHistoryDepth() + reusablePops)); }
    catch {
      while (overlayStack.length) destroyModal(overlayStack.pop().el, { restoreFocus: false });
      normalizeHistoryToLiveOverlay();
      dismissAllRequested = false;
      dismissAllCallbacks.splice(0).forEach((fn) => { try { fn(); } catch { /* callback isolation */ } });
    }
  }

  function setDialogLabel(modalBox, fallback = 'Dialog') {
    if (!modalBox) return;
    const candidates = [...modalBox.querySelectorAll('.modal-head h3, .thread-head .row-title, h2, h3')];
    const title = candidates.find((node) => node.textContent.trim());
    modalBox.removeAttribute('aria-labelledby');
    modalBox.removeAttribute('aria-label');
    if (title) {
      if (!title.id) title.id = `modal-title-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      modalBox.setAttribute('aria-labelledby', title.id);
    } else {
      modalBox.setAttribute('aria-label', fallback);
    }
    modalBox.querySelectorAll('.modal-close:not([aria-label])').forEach((btn) => {
      btn.setAttribute('aria-label', btn.textContent.includes('‹') ? 'Back' : 'Close');
    });
  }

  function openModal(html, opts = {}) {
    const root = $('#overlay-root');
    const previousFocus = document.activeElement;
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop'
      + (opts.chat ? ' chat-modal' : '')
      + (opts.court ? ' court-modal' : '');
    backdrop.innerHTML = `<div class="modal">${html}</div>`;
    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop || (e.target.closest('.modal-close') && e.target.closest('.modal-backdrop') === backdrop)) {
        dismissModal(backdrop);
      }
    });
    root.appendChild(backdrop);

    // Every sheet is a real modal dialog: announce it, keep keyboard focus
    // inside it, close the top sheet with Escape, then restore the trigger.
    const modalBox = backdrop.querySelector('.modal');
    modalBox.setAttribute('role', 'dialog');
    modalBox.setAttribute('aria-modal', 'true');
    modalBox.tabIndex = -1;
    setDialogLabel(modalBox, opts.label || 'Dialog');
    backdrop._returnFocus = previousFocus;
    backdrop._cleanupFns = [];
    const focusable = () => [...modalBox.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
    )].filter((el) => !el.closest('.hidden') && el.getClientRects().length);
    const onKeyDown = (e) => {
      if (!document.body.contains(backdrop)) return;
      const open = [...root.querySelectorAll('.modal-backdrop')];
      if (open[open.length - 1] !== backdrop) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        dismissModal(backdrop);
        return;
      }
      if (e.key !== 'Tab') return;
      const items = focusable();
      if (!items.length) { e.preventDefault(); modalBox.focus(); return; }
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === modalBox)) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    backdrop._cleanupFns.push(() => document.removeEventListener('keydown', onKeyDown));
    requestAnimationFrame(() => {
      if (currentOverlayEntry()?.el === backdrop) modalBox.focus({ preventScroll: true });
    });

    // Mark the element that's actually allowed to scroll so we can block the
    // page/map behind from scrolling when you drag anywhere else on the sheet.
    const scroller = backdrop.querySelector('.thread-msgs, .cd-scroll') || backdrop.querySelector('.modal');
    if (scroller) scroller.setAttribute('data-scroll', '');
    backdrop.addEventListener('touchmove', (e) => {
      if (!e.target.closest('[data-scroll]')) e.preventDefault();
    }, { passive: false });

    // Show a divider under the sticky header once the generic modal scrolls.
    const head = modalBox && modalBox.querySelector(':scope > .modal-head');
    if (head && !opts.chat && !opts.court) {
      modalBox.addEventListener('scroll', () => {
        const currentHead = modalBox.querySelector(':scope > .modal-head');
        currentHead?.classList.toggle('scrolled', modalBox.scrollTop > 4);
      });
    }

    const explicitRoute = normalizeOverlayRoute(opts.route);
    const route = explicitRoute || (currentOverlayEntry() && currentOverlayEntry().route) || null;
    let id = `${overlaySession}:${newGameAttemptId()}`;
    let historyPops = 1;
    const currentNav = history.state && history.state[OVERLAY_NAV_KEY];
    const canAdopt = adoptOverlayEntry && route
      && currentNav && currentNav.session === overlaySession && currentNav.id === adoptOverlayEntry.id
      && location.hash === overlayRouteHash(route)
      && adoptOverlayEntry.route.kind === route.kind && adoptOverlayEntry.route.id === route.id
      && (adoptOverlayEntry.route.matchId || null) === (route.matchId || null)
      && !overlayStack.some((entry) => entry.id === adoptOverlayEntry.id);
    if (canAdopt) {
      id = adoptOverlayEntry.id;
      historyPops = Math.max(
        1,
        Number(adoptOverlayEntry.historyPops ?? adoptOverlayEntry.depth) || 1,
      );
      adoptOverlayEntry = null;
    } else if (reusableOverlayEntry) {
      clearTimeout(reusableOverlayTimer);
      reusableOverlayTimer = null;
      historyPops = Math.max(1, reusableOverlayEntry.pops || 1);
      try { history.replaceState(overlayHistoryState(id, liveOverlayHistoryDepth() + historyPops, route), '', overlayUrl(route)); } catch { /* ignore */ }
      reusableOverlayEntry = null;
    } else {
      try { history.pushState(overlayHistoryState(id, liveOverlayHistoryDepth() + 1, route), '', overlayUrl(route)); } catch { /* dismiss falls back safely */ }
    }
    backdrop._overlayId = id;
    overlayStack.push({
      id, el: backdrop, route, ownsRoute: !!explicitRoute,
      historyPops, closing: false, afterClose: [],
    });
    syncModalStack();
    return backdrop;
  }

  // Programmatic transitions in the existing UI are synchronous close→open.
  // Keep the current history slot briefly reusable so those transitions still
  // cost one Back press; if no replacement opens, unwind the stale slot.
  function armReusableOverlayUnwind() {
    clearTimeout(reusableOverlayTimer);
    reusableOverlayTimer = setTimeout(() => {
      const reusable = reusableOverlayEntry;
      if (!reusable) return;
      const pops = reusable.pops || 1;
      const target = currentOverlayEntry();
      const preservingLoad = activeRoutedOverlayLoad
        && activeRoutedOverlayLoad.seq === routedOverlayLoadSeq
        && activeRoutedOverlayLoad.expectedReusableId === reusable.id
        ? activeRoutedOverlayLoad.seq : null;
      pendingReusableTraversal = {
        id: reusable.id,
        routeLoadSeq: preservingLoad,
        targetId: target ? target.id : null,
        targetDepth: liveOverlayHistoryDepth(),
      };
      reusableOverlayEntry = null;
      reusableOverlayTimer = null;
      try { history.go(-pops); }
      catch {
        pendingReusableTraversal = null;
        normalizeHistoryToLiveOverlay();
      }
    }, 0);
  }

  function closeModal(el) {
    if (!el) return false;
    const index = overlayStack.findIndex((entry) => entry.el === el);
    if (index < 0) { destroyModal(el); return false; }
    // dismissModal has already committed this sheet to the browser Back
    // traversal. An async success landing in that short window must not splice
    // it again, open a replacement, or schedule a second history.go().
    if (overlayStack[index].closing) return false;
    if (index !== overlayStack.length - 1) {
      // Preserve a newer child (and any typed draft) when an older async
      // parent action resolves. The parent closes only after that child is
      // intentionally dismissed.
      if (!el._deferredCloseQueued) {
        el._deferredCloseQueued = true;
        currentOverlayEntry().afterClose.push(() => {
          if (!el._deferredCloseQueued) return;
          el._deferredCloseQueued = false;
          closeModal(el);
        });
      }
      return false;
    }

    const [entry] = overlayStack.splice(index, 1);
    destroyModal(el);

    const nav = history.state && history.state[OVERLAY_NAV_KEY];
    if (nav && nav.session === overlaySession && nav.id === entry.id) {
      reusableOverlayEntry = { ...entry, pops: entry.historyPops || 1 };
      armReusableOverlayUnwind();
    } else if (reusableOverlayEntry && nav && nav.session === overlaySession
        && nav.id === reusableOverlayEntry.id) {
      // A child was closed and its callback immediately closed the parent too.
      // Traverse both history entries together instead of leaving a ghost Back.
      reusableOverlayEntry.pops = (reusableOverlayEntry.pops || 1) + (entry.historyPops || 1);
      armReusableOverlayUnwind();
    } else {
      normalizeHistoryToLiveOverlay();
    }
    entry.afterClose.splice(0).forEach((fn) => { try { fn(); } catch { /* callback isolation */ } });
    return true;
  }

  function transitionModal(el, openNext) {
    const index = overlayStack.findIndex((entry) => entry.el === el);
    if (index < 0 || typeof openNext !== 'function') return false;
    if (index !== overlayStack.length - 1) {
      // Replace only after the user's newer child is done. Repeated refreshes
      // coalesce to the latest result instead of stacking duplicate screens.
      el._deferredCloseQueued = false;
      el._deferredTransition = openNext;
      if (!el._deferredTransitionQueued) {
        el._deferredTransitionQueued = true;
        currentOverlayEntry().afterClose.push(() => {
          el._deferredTransitionQueued = false;
          const next = el._deferredTransition;
          el._deferredTransition = null;
          if (next) transitionModal(el, next);
        });
      }
      return true;
    }
    el._deferredCloseQueued = false;
    if (!closeModal(el)) return false;
    openNext();
    return true;
  }

  // A successful modal action can need a second, optional prompt after a
  // refresh. Only carry that intent forward when the action still owns the
  // visible sheet; closing it first lets the history-aware load token follow
  // the app back to its parent/base screen without reviving over newer UI.
  function beginFollowupAfterClosingModal(el) {
    const entry = overlayStack.find((candidate) => candidate.el === el);
    if (!entry || entry.closing) return null;
    const top = currentOverlayEntry();
    if (top !== entry) {
      // A newer child owns the screen. Preserve it, but still queue the stale
      // source sheet to close once that child is intentionally dismissed.
      closeModal(el);
      return null;
    }
    if (!closeModal(el)) return null;
    return beginRoutedOverlayLoad(null);
  }

  window.addEventListener('popstate', (e) => {
    const nav = e.state && e.state[OVERLAY_NAV_KEY];
    const nativeRoute = normalizeOverlayRoute(location.hash);
    const stateRoute = normalizeOverlayRoute(nav && nav.route);
    const ownsNativeDestination = nav && nav.session === overlaySession && nav.id
      && sameOverlayRoute(stateRoute, nativeRoute);
    // A browser/client fragment navigation creates its own history entry and
    // fires popstate before hashchange. Adopt that entry in place so opening a
    // shared link cannot create a ghost base stop. Installed-app pushes use a
    // service-worker message (below); this is the defensive link/navigation
    // fallback for already-open tabs.
    if (state.token && nativeRoute && !ownsNativeDestination) {
      clearTimeout(reusableOverlayTimer);
      reusableOverlayTimer = null;
      reusableOverlayEntry = null;
      pendingReusableTraversal = null;
      overlayHistoryRevision += 1;
      routedOverlayLoadSeq += 1;
      activeRoutedOverlayLoad = null;
      suppressNativeHashRoute = location.hash;
      const suppressedHash = suppressNativeHashRoute;
      setTimeout(() => {
        if (suppressNativeHashRoute === suppressedHash) suppressNativeHashRoute = null;
      }, 250);

      const existingOwner = [...overlayStack].reverse().find(
        (entry) => entry.ownsRoute && sameOverlayRoute(entry.route, nativeRoute),
      );
      if (existingOwner) {
        // Identical native navigation has already added a duplicate browser
        // entry. Keep the live screen intact; push notifications never take
        // this path, so ordinary Back semantics remain unaffected.
        try {
          history.replaceState(
            overlayHistoryState(existingOwner.id, liveOverlayHistoryDepth(), existingOwner.route),
            '', overlayUrl(existingOwner.route),
          );
        } catch { /* history can be unavailable */ }
        return;
      }

      const parentRoute = nativeRoute.matchId
        ? { kind: nativeRoute.kind, id: nativeRoute.id } : nativeRoute;
      const adoptionId = `${overlaySession}:${newGameAttemptId()}`;
      adoptOverlayEntry = {
        v: 1,
        session: overlaySession,
        id: adoptionId,
        depth: liveOverlayHistoryDepth() + 1,
        historyPops: 1,
        route: parentRoute,
      };
      try {
        history.replaceState(
          overlayHistoryState(adoptionId, adoptOverlayEntry.depth, parentRoute),
          '', overlayUrl(parentRoute),
        );
      } catch { /* openModal still has a safe push fallback */ }
      queueMicrotask(() => navigateOverlayRoute(nativeRoute));
      return;
    }
    const expectedRouteTraversal = pendingReusableTraversal
      && pendingReusableTraversal.routeLoadSeq != null
      && activeRoutedOverlayLoad
      && activeRoutedOverlayLoad.seq === pendingReusableTraversal.routeLoadSeq
      && nav && nav.session === overlaySession
      && nav.id === pendingReusableTraversal.targetId
      && Number(nav.depth) === pendingReusableTraversal.targetDepth;
    pendingReusableTraversal = null;
    clearTimeout(reusableOverlayTimer);
    reusableOverlayTimer = null;
    reusableOverlayEntry = null;
    overlayHistoryRevision += 1;
    adoptOverlayEntry = null;
    if (expectedRouteTraversal) {
      activeRoutedOverlayLoad.historyRevision = overlayHistoryRevision;
      activeRoutedOverlayLoad.expectedReusableId = null;
    } else {
      routedOverlayLoadSeq += 1;
      activeRoutedOverlayLoad = null;
    }
    const targetId = nav && nav.session === overlaySession ? nav.id : null;
    const targetIndex = targetId ? overlayStack.findIndex((entry) => entry.id === targetId) : -1;
    const staleTarget = !!targetId && targetIndex < 0;
    if (staleTarget) {
      const top = currentOverlayEntry();
      const liveDepth = liveOverlayHistoryDepth();
      const targetDepth = nav && Number.isFinite(Number(nav.depth)) ? Number(nav.depth) : null;
      const priorLiveDepth = top ? liveDepth - (top.historyPops || 1) : liveDepth;
      // After reloading a nested route, Back can first land on an orphaned
      // parent entry. Skip the remainder to the last actually-live screen, so
      // the route closes in one gesture and no duplicate base stop remains.
      if (top && targetDepth != null && targetDepth > priorLiveDepth && targetDepth < liveDepth) {
        try { history.go(-(targetDepth - priorLiveDepth)); return; } catch { /* repair below */ }
      }
      // Forward into an already-destroyed overlay must return to the actual
      // live history slot. Replacing the stale slot would create a duplicate
      // entry, swallow the next Back gesture, and could lock a closing sheet.
      const bounceDistance = targetDepth != null && targetDepth > liveDepth
        ? Math.max(1, targetDepth - liveDepth) : 1;
      try { history.go(-bounceDistance); } catch { normalizeHistoryToLiveOverlay(); }
      return;
    }
    const callbackQueue = [];
    let removed = null;
    const unwindTo = targetIndex;
    while (overlayStack.length - 1 > unwindTo) {
      const entry = overlayStack.pop();
      removed = entry.el;
      destroyModal(entry.el, { restoreFocus: false });
      callbackQueue.push(...entry.afterClose.splice(0));
    }
    syncModalStack();
    focusAfterModalChange(removed, true);
    const survivingTop = currentOverlayEntry();
    if (survivingTop) survivingTop.closing = false;
    // Callbacks may open the next onboarding sheet, so wait until the old stack
    // is fully reconciled before running any of them.
    callbackQueue.forEach((fn) => { try { fn(); } catch { /* callback isolation */ } });
    if (overlayStack.length && dismissAllRequested) {
      dismissAllModals();
      return;
    }
    if (!overlayStack.length && dismissAllRequested) {
      dismissAllRequested = false;
      dismissAllCallbacks.splice(0).forEach((fn) => { try { fn(); } catch { /* callback isolation */ } });
    } else if (!overlayStack.length) {
      dismissAllRequested = false;
    }
    if (!overlayStack.length && pendingDeepMatchRebuild) {
      const route = pendingDeepMatchRebuild;
      pendingDeepMatchRebuild = null;
      adoptOverlayEntry = null;
      try {
        history.replaceState(overlayHistoryState(null, 0, null), '', baseAppUrl());
      } catch { /* openModal still has a safe push fallback */ }
      queueMicrotask(() => navigateOverlayRoute(route));
    }
  });

  // Durable, account-scoped chat outbox. A message is stored before its
  // composer is cleared, then replayed with the same idempotency key until the
  // server acknowledges it. IndexedDB keeps photos out of localStorage's small
  // quota; localStorage is only used when IndexedDB cannot be opened at all.
  const CHAT_OUTBOX_DB_NAME = 'thirdshot-chat-outbox';
  const CHAT_OUTBOX_STORE_NAME = 'messages';
  const CHAT_OUTBOX_FALLBACK_KEY = 'pp_chat_outbox_v1';
  const CHAT_OUTBOX_ATTEMPT_RE = /^[a-zA-Z0-9_-]{16,64}$/;
  const CHAT_OUTBOX_MAX_PER_ACCOUNT = 50;
  let chatOutboxDbPromise = null;
  const chatOutboxBindings = new Map();
  const chatOutboxSends = new Map();
  const chatOutboxFlushes = new Map();
  const chatOutboxRetryTimers = new Map();
  const chatOutboxAcknowledgedAttempts = new Set();
  const chatOutboxCancelledAttempts = new Set();
  let chatOutboxSessionRevision = 0;

  function chatOutboxAccountId(value) {
    const id = Number(value);
    return Number.isSafeInteger(id) && id > 0 ? id : null;
  }

  function chatEndpointForChannel(channelKey) {
    const match = String(channelKey || '').match(/^(dm|court|game|tournament|club|league):([1-9]\d*)$/);
    if (!match) return null;
    const [, kind, id] = match;
    if (kind === 'dm') return `/chat/${id}`;
    const collections = {
      court: 'courts', game: 'games', tournament: 'tournaments', club: 'clubs', league: 'leagues',
    };
    return `/${collections[kind]}/${id}/chat`;
  }

  function chatOutboxRecordId(accountId, attemptId) {
    return `${accountId}:${attemptId}`;
  }

  function normalizeChatOutboxRecord(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const accountId = chatOutboxAccountId(raw.accountId);
    const attemptId = typeof raw.attemptId === 'string' && CHAT_OUTBOX_ATTEMPT_RE.test(raw.attemptId)
      ? raw.attemptId : null;
    const channelKey = typeof raw.channelKey === 'string' && chatEndpointForChannel(raw.channelKey)
      ? raw.channelKey : null;
    const body = typeof raw.body === 'string' ? raw.body.slice(0, 2000) : '';
    const image = typeof raw.image === 'string'
      && raw.image.length <= 700000
      && /^data:image\/[a-zA-Z0-9.+-]+;base64,/.test(raw.image) ? raw.image : null;
    if (!accountId || !attemptId || !channelKey || (!body.trim() && !image)) return null;
    const createdAt = Number(raw.createdAt);
    const status = ['queued', 'sending', 'failed'].includes(raw.status) ? raw.status : 'queued';
    return {
      id: chatOutboxRecordId(accountId, attemptId),
      accountId,
      channelKey,
      attemptId,
      body,
      image,
      createdAt: Number.isFinite(createdAt) && createdAt > 0 && createdAt <= Date.now() + 60000
        ? createdAt : Date.now(),
      status,
      retryCount: Math.max(0, Math.min(20, Number(raw.retryCount) || 0)),
      error: typeof raw.error === 'string' ? raw.error.slice(0, 180) : '',
      nextRetryAt: Number.isFinite(Number(raw.nextRetryAt)) ? Number(raw.nextRetryAt) : null,
    };
  }

  function openChatOutboxDb() {
    if (chatOutboxDbPromise) return chatOutboxDbPromise;
    chatOutboxDbPromise = new Promise((resolve) => {
      if (!('indexedDB' in window)) { resolve(null); return; }
      let settled = false;
      const finish = (value) => {
        if (settled) {
          if (value) value.close();
          return;
        }
        settled = true;
        resolve(value);
      };
      let request;
      try { request = indexedDB.open(CHAT_OUTBOX_DB_NAME, 1); }
      catch { finish(null); return; }
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(CHAT_OUTBOX_STORE_NAME)) {
          const store = db.createObjectStore(CHAT_OUTBOX_STORE_NAME, { keyPath: 'id' });
          store.createIndex('accountId', 'accountId', { unique: false });
        }
      };
      request.onsuccess = () => {
        const db = request.result;
        db.onversionchange = () => db.close();
        const fallback = readChatOutboxFallback();
        if (!fallback.length) { finish(db); return; }
        try {
          const transaction = db.transaction(CHAT_OUTBOX_STORE_NAME, 'readwrite');
          const store = transaction.objectStore(CHAT_OUTBOX_STORE_NAME);
          fallback.map(normalizeChatOutboxRecord).filter(Boolean).forEach((item) => store.put(item));
          transaction.oncomplete = () => {
            try { localStorage.removeItem(CHAT_OUTBOX_FALLBACK_KEY); } catch { /* harmless duplicate fallback */ }
            finish(db);
          };
          const fallBackToLocal = () => { db.close(); finish(null); };
          transaction.onerror = fallBackToLocal;
          transaction.onabort = fallBackToLocal;
        } catch { db.close(); finish(null); }
      };
      request.onerror = () => finish(null);
      request.onblocked = () => finish(null);
    });
    return chatOutboxDbPromise;
  }

  function readChatOutboxFallback() {
    try {
      const items = JSON.parse(localStorage.getItem(CHAT_OUTBOX_FALLBACK_KEY) || '[]');
      return Array.isArray(items) ? items : [];
    } catch { return []; }
  }

  function writeChatOutboxFallback(items) {
    localStorage.setItem(CHAT_OUTBOX_FALLBACK_KEY, JSON.stringify(items));
  }

  const chatOutboxStore = {
    async all() {
      const db = await openChatOutboxDb();
      if (!db) return readChatOutboxFallback();
      return new Promise((resolve, reject) => {
        const request = db.transaction(CHAT_OUTBOX_STORE_NAME, 'readonly')
          .objectStore(CHAT_OUTBOX_STORE_NAME).getAll();
        request.onsuccess = () => resolve(request.result || []);
        request.onerror = () => reject(request.error || new Error('outbox_read_failed'));
      });
    },
    async get(id) {
      const db = await openChatOutboxDb();
      if (!db) return readChatOutboxFallback().find((item) => item && item.id === id) || null;
      return new Promise((resolve, reject) => {
        const request = db.transaction(CHAT_OUTBOX_STORE_NAME, 'readonly')
          .objectStore(CHAT_OUTBOX_STORE_NAME).get(id);
        request.onsuccess = () => resolve(request.result || null);
        request.onerror = () => reject(request.error || new Error('outbox_read_failed'));
      });
    },
    async put(item) {
      const db = await openChatOutboxDb();
      if (!db) {
        const items = readChatOutboxFallback().filter((entry) => entry && entry.id !== item.id);
        items.push(item);
        writeChatOutboxFallback(items);
        return;
      }
      await new Promise((resolve, reject) => {
        const transaction = db.transaction(CHAT_OUTBOX_STORE_NAME, 'readwrite');
        transaction.objectStore(CHAT_OUTBOX_STORE_NAME).put(item);
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error || new Error('outbox_write_failed'));
        transaction.onabort = () => reject(transaction.error || new Error('outbox_write_failed'));
      });
    },
    async remove(id) {
      const db = await openChatOutboxDb();
      if (!db) {
        writeChatOutboxFallback(readChatOutboxFallback().filter((item) => item && item.id !== id));
        return;
      }
      await new Promise((resolve, reject) => {
        const transaction = db.transaction(CHAT_OUTBOX_STORE_NAME, 'readwrite');
        transaction.objectStore(CHAT_OUTBOX_STORE_NAME).delete(id);
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error || new Error('outbox_delete_failed'));
        transaction.onabort = () => reject(transaction.error || new Error('outbox_delete_failed'));
      });
    },
    async purge(accountId, cutoff = Date.now()) {
      const db = await openChatOutboxDb();
      if (!db) {
        writeChatOutboxFallback(readChatOutboxFallback()
          .filter((raw) => {
            if (chatOutboxAccountId(raw && raw.accountId) !== accountId) return true;
            const createdAt = Number(raw && raw.createdAt);
            return !!normalizeChatOutboxRecord(raw)
              && Number.isFinite(createdAt) && createdAt > cutoff
              && createdAt <= Date.now() + 60000;
          }));
        return;
      }
      const items = await this.all();
      const ids = items.filter((raw) => {
        if (chatOutboxAccountId(raw && raw.accountId) !== accountId) return false;
        const createdAt = Number(raw && raw.createdAt);
        const createdAfterLogout = !!normalizeChatOutboxRecord(raw)
          && Number.isFinite(createdAt) && createdAt > cutoff
          && createdAt <= Date.now() + 60000;
        return !createdAfterLogout;
      }).map((item) => item.id).filter(Boolean);
      if (!ids.length) return;
      await new Promise((resolve, reject) => {
        const transaction = db.transaction(CHAT_OUTBOX_STORE_NAME, 'readwrite');
        const store = transaction.objectStore(CHAT_OUTBOX_STORE_NAME);
        ids.forEach((id) => store.delete(id));
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error || new Error('outbox_purge_failed'));
        transaction.onabort = () => reject(transaction.error || new Error('outbox_purge_failed'));
      });
    },
  };

  async function listChatOutbox(accountId, channelKey = null) {
    accountId = chatOutboxAccountId(accountId);
    if (!accountId) return [];
    const raw = await chatOutboxStore.all();
    return raw.map(normalizeChatOutboxRecord).filter((item) => item
      && item.accountId === accountId
      && !chatOutboxAcknowledgedAttempts.has(item.id)
      && (!channelKey || item.channelKey === channelKey))
      .sort((a, b) => a.createdAt - b.createdAt);
  }

  function chatOutboxBindingKey(accountId, channelKey) {
    return `${accountId}:${channelKey}`;
  }

  function notifyChatOutboxBindings(accountId, channelKey, { delivered = null, announcement = '' } = {}) {
    const bindings = chatOutboxBindings.get(chatOutboxBindingKey(accountId, channelKey));
    if (!bindings) return;
    bindings.forEach((binding) => {
      if (delivered) binding.onDelivered?.(delivered);
      binding.refresh();
      if (announcement) binding.announce(announcement);
    });
  }

  function clearChatOutboxRetry(recordId) {
    const timer = chatOutboxRetryTimers.get(recordId);
    if (timer) clearTimeout(timer);
    chatOutboxRetryTimers.delete(recordId);
  }

  function scheduleChatOutboxRetry(item) {
    clearChatOutboxRetry(item.id);
    if (item.status !== 'queued' || !navigator.onLine) return;
    const delay = Math.max(2500, Math.min(30000, (item.nextRetryAt || Date.now()) - Date.now()));
    const sessionRevision = chatOutboxSessionRevision;
    chatOutboxRetryTimers.set(item.id, setTimeout(async () => {
      chatOutboxRetryTimers.delete(item.id);
      if (sessionRevision !== chatOutboxSessionRevision
          || !state.token || state.me?.id !== item.accountId || !navigator.onLine) return;
      const latest = normalizeChatOutboxRecord(await chatOutboxStore.get(item.id).catch(() => null));
      if (sessionRevision === chatOutboxSessionRevision
          && latest && latest.status === 'queued') sendChatOutboxItem(latest);
    }, delay));
  }

  function transientChatSendError(error) {
    const status = Number(error && error.status) || 0;
    return !!(error && error.isNetworkError) || status === 408 || status === 425
      || status === 429 || status >= 500;
  }

  async function sendChatOutboxItem(rawItem, { manual = false } = {}) {
    const item = normalizeChatOutboxRecord(rawItem);
    if (!item || !state.token || state.me?.id !== item.accountId) return null;
    if (chatOutboxCancelledAttempts.has(item.id)) return null;
    if (!manual && item.status === 'failed') return null;
    const existingSend = chatOutboxSends.get(item.id);
    if (existingSend) return existingSend;
    const sessionRevision = chatOutboxSessionRevision;
    const task = (async () => {
      clearChatOutboxRetry(item.id);
      if (!navigator.onLine) {
        const queued = { ...item, status: 'queued', error: "You're offline. This will send when you reconnect." };
        await chatOutboxStore.put(queued);
        if (sessionRevision !== chatOutboxSessionRevision
            || !state.token || state.me?.id !== item.accountId) {
          await chatOutboxStore.remove(item.id).catch(() => {});
          return null;
        }
        notifyChatOutboxBindings(item.accountId, item.channelKey, {
          announcement: 'Message saved. It will send when you reconnect.',
        });
        return null;
      }
      const sending = {
        ...item,
        status: 'sending',
        retryCount: manual ? 0 : item.retryCount,
        error: '',
        nextRetryAt: null,
      };
      await chatOutboxStore.put(sending);
      if (sessionRevision !== chatOutboxSessionRevision
          || !state.token || state.me?.id !== item.accountId
          || chatOutboxCancelledAttempts.has(item.id)) {
        await chatOutboxStore.remove(item.id).catch(() => {});
        return null;
      }
      notifyChatOutboxBindings(item.accountId, item.channelKey, { announcement: 'Sending message.' });
      try {
        let delivered = await api(chatEndpointForChannel(item.channelKey), {
          method: 'POST',
          body: JSON.stringify({
            body: item.body,
            image: item.image,
            client_attempt_id: item.attemptId,
          }),
        });
        const terminalDeleted = delivered && delivered.deleted === true;
        delivered = { ...delivered, client_attempt_id: delivered.client_attempt_id || item.attemptId };
        if (sessionRevision !== chatOutboxSessionRevision) {
          await chatOutboxStore.remove(item.id).catch(() => {});
          return null;
        }
        chatOutboxAcknowledgedAttempts.add(item.id);
        // Delivery is authoritative even if local cleanup is briefly blocked;
        // rendering the acknowledged attempt below also reconciles the record.
        await chatOutboxStore.remove(item.id).catch(() => {});
        clearChatOutboxRetry(item.id);
        notifyChatOutboxBindings(item.accountId, item.channelKey, {
          delivered: terminalDeleted ? null : delivered,
          announcement: terminalDeleted ? 'Removed message cleared from the outbox.' : 'Message sent.',
        });
        return terminalDeleted ? null : delivered;
      } catch (error) {
        // logout() purges this account while an in-flight request unwinds; never
        // recreate private data after that purge.
        if (sessionRevision !== chatOutboxSessionRevision
            || !state.token || state.me?.id !== item.accountId) {
          await chatOutboxStore.remove(item.id).catch(() => {});
          return null;
        }
        const retryCount = sending.retryCount + 1;
        const transient = transientChatSendError(error);
        const exhausted = transient && navigator.onLine && retryCount >= 4;
        const failed = !transient || exhausted;
        const delay = Number(error && error.status) === 429
          ? 30000 : Math.min(30000, 2500 * (2 ** Math.max(0, retryCount - 1)));
        const next = {
          ...sending,
          status: failed ? 'failed' : 'queued',
          retryCount,
          error: failed
            ? `${error.message || 'Could not send'}. Tap Retry to try again.`
            : (navigator.onLine ? 'Connection interrupted. Retrying soon…' : 'Waiting for connection…'),
          nextRetryAt: failed ? null : Date.now() + delay,
        };
        await chatOutboxStore.put(next);
        if (sessionRevision !== chatOutboxSessionRevision
            || !state.token || state.me?.id !== item.accountId) {
          await chatOutboxStore.remove(item.id).catch(() => {});
          return null;
        }
        notifyChatOutboxBindings(item.accountId, item.channelKey, {
          announcement: failed
            ? 'Message not sent. Use Retry to try again.'
            : 'Message queued. It will retry automatically.',
        });
        if (!failed) scheduleChatOutboxRetry(next);
        return null;
      }
    })();
    chatOutboxSends.set(item.id, task);
    try { return await task; }
    finally {
      if (chatOutboxSends.get(item.id) === task) chatOutboxSends.delete(item.id);
    }
  }

  async function flushChatOutboxForAccount(rawAccountId, channelKey = null) {
    const accountId = chatOutboxAccountId(rawAccountId);
    if (!accountId || !state.token || state.me?.id !== accountId
        || !navigator.onLine) return;
    const flushKey = `${accountId}:${channelKey || '*'}`;
    if (chatOutboxFlushes.has(flushKey)) return chatOutboxFlushes.get(flushKey);
    const sessionRevision = chatOutboxSessionRevision;
    const task = (async () => {
      const attempted = new Set();
      while (sessionRevision === chatOutboxSessionRevision
          && state.token && state.me?.id === accountId && navigator.onLine) {
        const items = await listChatOutbox(accountId, channelKey);
        if (sessionRevision !== chatOutboxSessionRevision
            || !state.token || state.me?.id !== accountId) break;
        const item = items.find(
          (candidate) => candidate.status !== 'failed' && !attempted.has(candidate.id),
        );
        if (!item) break;
        attempted.add(item.id);
        await sendChatOutboxItem(item);
      }
    })();
    chatOutboxFlushes.set(flushKey, task);
    try { await task; }
    catch { /* each item remains durably queued */ }
    finally {
      if (chatOutboxFlushes.get(flushKey) === task) chatOutboxFlushes.delete(flushKey);
    }
  }

  async function enqueueChatOutboxMessage(accountId, channelKey, body, image = null) {
    accountId = chatOutboxAccountId(accountId);
    if (!accountId || accountId !== state.me?.id || !chatEndpointForChannel(channelKey)) {
      throw new Error('This conversation is no longer available.');
    }
    const sessionRevision = chatOutboxSessionRevision;
    const existing = await listChatOutbox(accountId);
    if (sessionRevision !== chatOutboxSessionRevision || state.me?.id !== accountId) {
      throw new Error('This conversation is no longer available.');
    }
    if (existing.length >= CHAT_OUTBOX_MAX_PER_ACCOUNT) {
      throw new Error('Your outbox is full. Retry or remove an unsent message first.');
    }
    const item = normalizeChatOutboxRecord({
      accountId,
      channelKey,
      attemptId: newGameAttemptId(),
      body,
      image,
      createdAt: Date.now(),
      status: 'queued',
      retryCount: 0,
    });
    if (!item) throw new Error('Add a message or photo before sending.');
    try { await chatOutboxStore.put(item); }
    catch {
      throw new Error('Could not save this message yet. Your draft is still here—try again.');
    }
    if (sessionRevision !== chatOutboxSessionRevision || state.me?.id !== accountId) {
      await chatOutboxStore.remove(item.id).catch(() => {});
      throw new Error('This conversation is no longer available.');
    }
    notifyChatOutboxBindings(accountId, channelKey, {
      announcement: navigator.onLine ? 'Message saved and ready to send.' : 'Message saved for when you reconnect.',
    });
    flushChatOutboxForAccount(accountId, channelKey);
    return item;
  }

  async function retryChatOutboxMessage(accountId, attemptId) {
    const sessionRevision = chatOutboxSessionRevision;
    const id = chatOutboxRecordId(accountId, attemptId);
    const item = normalizeChatOutboxRecord(await chatOutboxStore.get(id));
    if (sessionRevision !== chatOutboxSessionRevision
        || !item || item.accountId !== state.me?.id) return;
    await sendChatOutboxItem({ ...item, status: 'queued', retryCount: 0 }, { manual: true });
  }

  async function removeChatOutboxMessage(accountId, channelKey, attemptId) {
    const id = chatOutboxRecordId(accountId, attemptId);
    if (chatOutboxSends.has(id)) throw new Error('message_is_sending');
    chatOutboxCancelledAttempts.add(id);
    clearChatOutboxRetry(id);
    try { await chatOutboxStore.remove(id); }
    catch (error) {
      chatOutboxCancelledAttempts.delete(id);
      throw error;
    }
    if (state.me?.id === accountId) notifyChatOutboxBindings(accountId, channelKey, {
      announcement: 'Unsent message removed.',
    });
  }

  function purgeAccountChatOutbox(rawAccountId) {
    const accountId = chatOutboxAccountId(rawAccountId);
    if (!accountId) return;
    const cutoff = Date.now();
    chatOutboxSessionRevision += 1;
    [...chatOutboxFlushes.keys()].forEach((key) => {
      if (key.startsWith(`${accountId}:`)) chatOutboxFlushes.delete(key);
    });
    [...chatOutboxSends.keys()].forEach((id) => {
      if (id.startsWith(`${accountId}:`)) chatOutboxSends.delete(id);
    });
    [...chatOutboxAcknowledgedAttempts].forEach((id) => {
      if (id.startsWith(`${accountId}:`)) chatOutboxAcknowledgedAttempts.delete(id);
    });
    [...chatOutboxCancelledAttempts].forEach((id) => {
      if (id.startsWith(`${accountId}:`)) chatOutboxCancelledAttempts.delete(id);
    });
    [...chatOutboxRetryTimers.keys()].forEach((id) => {
      if (id.startsWith(`${accountId}:`)) clearChatOutboxRetry(id);
    });
    chatOutboxStore.purge(accountId, cutoff).catch(() => { /* logout UI must not block on storage */ });
  }

  function reconcileChatOutboxMessages(msgsEl, items) {
    const accountId = state.me && state.me.id;
    if (!accountId) return;
    items.forEach((message) => {
      const attemptId = message && message.client_attempt_id;
      if (!CHAT_OUTBOX_ATTEMPT_RE.test(String(attemptId || ''))) return;
      msgsEl.querySelectorAll(`[data-client-attempt-id="${attemptId}"]`).forEach((node) => node.remove());
      const recordId = chatOutboxRecordId(accountId, attemptId);
      chatOutboxAcknowledgedAttempts.add(recordId);
      chatOutboxStore.get(recordId).then((raw) => {
        const item = normalizeChatOutboxRecord(raw);
        if (!item) return;
        return chatOutboxStore.remove(recordId).then(() => {
          clearChatOutboxRetry(recordId);
          notifyChatOutboxBindings(item.accountId, item.channelKey, { announcement: 'Message sent.' });
        });
      }).catch(() => { /* a later idempotent replay can reconcile it */ });
    });
  }

  // Chat continuity is deliberately session-only and scoped to both the signed-
  // in account and exact room. An accidental close/reload restores the composer,
  // while another account or channel can never inherit that text.
  const CHAT_DRAFT_VERSION = 1;
  const CHAT_DRAFT_TTL = 24 * 60 * 60 * 1000;
  const CHAT_NEAR_BOTTOM_PX = 96;
  function prepareChatRenderBatch(msgsEl, rawItems, append) {
    const items = Array.isArray(rawItems) ? rawItems : [];
    reconcileChatOutboxMessages(msgsEl, items);
    const newestId = items.reduce(
      (latest, message) => Math.max(latest, Number(message?.id) || 0), 0,
    );
    const seen = append
      ? new Set([...msgsEl.querySelectorAll('[data-message-id]')]
        .map((node) => Number(node.dataset.messageId)).filter(Boolean))
      : new Set();
    const unique = [];
    items.forEach((message) => {
      const id = Number(message?.id) || 0;
      if (!id || seen.has(id)) return;
      seen.add(id);
      unique.push(message);
    });
    return { items: unique, newestId };
  }
  function chatIsNearBottom(msgsEl) {
    return msgsEl.scrollHeight - msgsEl.clientHeight - msgsEl.scrollTop <= CHAT_NEAR_BOTTOM_PX;
  }

  function chatScrollSnapshot(msgsEl) {
    const bounds = msgsEl.getBoundingClientRect();
    const anchor = [...msgsEl.children].find((child) => child.getBoundingClientRect().bottom > bounds.top + 1) || null;
    return {
      nearBottom: chatIsNearBottom(msgsEl),
      scrollTop: msgsEl.scrollTop,
      anchor,
      anchorTop: anchor ? anchor.getBoundingClientRect().top : null,
    };
  }

  function attachChatViewport(backdrop, msgsEl, inputEl, isFollowing) {
    const stick = () => { msgsEl.scrollTop = msgsEl.scrollHeight; };
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
      // Keyboard opening (viewport shrank) follows the latest message only when
      // the person was already following it. Reading history never gets yanked.
      if (vv.height < lastH - 80 && isFollowing()) stick();
      lastH = vv.height;
    };
    function detach() {
      vv.removeEventListener('resize', onResize);
      vv.removeEventListener('scroll', place);
    }
    place();
    vv.addEventListener('resize', onResize);
    vv.addEventListener('scroll', place);
    const onInputFocus = () => {
      const follow = isFollowing();
      setTimeout(() => { place(); if (follow) stick(); }, 300);
    };
    if (inputEl) inputEl.addEventListener('focus', onInputFocus);
    if (!backdrop._cleanupFns) backdrop._cleanupFns = [];
    backdrop._cleanupFns.push(() => {
      detach();
      inputEl?.removeEventListener('focus', onInputFocus);
    });
  }

  function bindChatContinuity(modal, msgsEl, inputEl, channelKey) {
    const accountId = state.me && state.me.id;
    const outboxSessionRevision = chatOutboxSessionRevision;
    const storageKey = accountId
      ? `pp_chat_draft_v${CHAT_DRAFT_VERSION}:${accountId}:${encodeURIComponent(channelKey)}` : null;
    const thread = msgsEl.closest('.thread');
    const form = inputEl.closest('form');
    const newMessages = document.createElement('button');
    newMessages.type = 'button';
    newMessages.className = 'chat-new-messages hidden';
    newMessages.textContent = 'New messages';
    newMessages.setAttribute('aria-label', 'New messages. Scroll to the latest message.');
    const newMessageStatus = document.createElement('span');
    newMessageStatus.className = 'sr-only';
    newMessageStatus.setAttribute('role', 'status');
    newMessageStatus.setAttribute('aria-live', 'polite');
    newMessageStatus.setAttribute('aria-atomic', 'true');
    const draftStatus = document.createElement('span');
    draftStatus.className = 'sr-only';
    draftStatus.setAttribute('role', 'status');
    draftStatus.setAttribute('aria-live', 'polite');
    draftStatus.setAttribute('aria-atomic', 'true');
    const outboxStatus = document.createElement('span');
    outboxStatus.className = 'sr-only';
    outboxStatus.setAttribute('role', 'status');
    outboxStatus.setAttribute('aria-live', 'polite');
    outboxStatus.setAttribute('aria-atomic', 'true');
    if (thread && form) {
      thread.insertBefore(newMessages, form);
      thread.insertBefore(newMessageStatus, form);
      thread.insertBefore(draftStatus, form);
      thread.insertBefore(outboxStatus, form);
    }

    let following = true;
    let pendingNew = 0;
    let draftTimer = null;
    let draftRevision = newGameAttemptId();
    const clearNewMessages = () => {
      pendingNew = 0;
      newMessageStatus.textContent = '';
      if (document.activeElement === newMessages) {
        newMessages.textContent = 'Latest messages';
        newMessages.setAttribute('aria-label', 'Latest messages shown.');
        newMessages.dataset.hideOnBlur = 'true';
      } else {
        newMessages.classList.add('hidden');
      }
    };
    const showNewMessages = (count) => {
      pendingNew += count;
      newMessages.textContent = 'New messages';
      newMessages.setAttribute(
        'aria-label',
        `${pendingNew} new message${pendingNew === 1 ? '' : 's'}. Scroll to the latest message.`,
      );
      delete newMessages.dataset.hideOnBlur;
      newMessages.classList.remove('hidden');
      newMessageStatus.textContent = `${pendingNew} new message${pendingNew === 1 ? '' : 's'} available.`;
    };
    const scrollToLatest = ({ smooth = false } = {}) => {
      const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      msgsEl.scrollTo({
        top: msgsEl.scrollHeight,
        behavior: smooth && !reduceMotion ? 'smooth' : 'auto',
      });
      following = true;
      clearNewMessages();
    };
    const syncFollowing = () => {
      following = chatIsNearBottom(msgsEl);
      if (following && pendingNew) clearNewMessages();
    };
    msgsEl.addEventListener('scroll', syncFollowing, { passive: true });
    newMessages.addEventListener('click', () => scrollToLatest({ smooth: true }));
    newMessages.addEventListener('blur', () => {
      if (!newMessages.dataset.hideOnBlur) return;
      delete newMessages.dataset.hideOnBlur;
      newMessages.classList.add('hidden');
    });

    const persistDraftNow = () => {
      draftTimer = null;
      if (!storageKey) return;
      try {
        sessionStorage.setItem(storageKey, JSON.stringify({
          v: CHAT_DRAFT_VERSION,
          body: inputEl.value,
          revision: draftRevision,
          updatedAt: Date.now(),
        }));
      } catch { /* chat remains usable when storage is unavailable */ }
    };
    const persistDraft = () => {
      draftStatus.textContent = '';
      draftRevision = newGameAttemptId();
      clearTimeout(draftTimer);
      draftTimer = setTimeout(persistDraftNow, 100);
    };
    if (storageKey) {
      try {
        const saved = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
        if (saved && saved.v === CHAT_DRAFT_VERSION && typeof saved.body === 'string'
            && Number.isFinite(saved.updatedAt) && saved.updatedAt <= Date.now() + 60000
            && Date.now() - saved.updatedAt <= CHAT_DRAFT_TTL) {
          if (typeof saved.revision === 'string' && saved.revision) draftRevision = saved.revision;
          inputEl.value = saved.body.slice(0, Number(inputEl.maxLength) > 0 ? inputEl.maxLength : 2000);
          if (inputEl.value) requestAnimationFrame(() => { draftStatus.textContent = 'Message draft restored.'; });
        } else if (saved) {
          sessionStorage.removeItem(storageKey);
        }
      } catch {
        try { sessionStorage.removeItem(storageKey); } catch { /* storage unavailable */ }
      }
    }
    inputEl.addEventListener('input', persistDraft);

    const completeSend = (submittedBody, submittedRevision) => {
      // If more was typed while the request was in flight, that is a new draft
      // and must survive this successful send.
      if (draftRevision === submittedRevision && inputEl.value.trim() === submittedBody) {
        inputEl.value = '';
        draftStatus.textContent = '';
        clearTimeout(draftTimer);
        draftTimer = null;
        if (storageKey) {
          try {
            const saved = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
            if (!saved || saved.revision === submittedRevision) sessionStorage.removeItem(storageKey);
          } catch { /* storage unavailable */ }
        }
      } else {
        persistDraftNow();
      }
    };
    let sending = false;
    const send = async (submittedBody, { image = null } = {}) => {
      if (outboxSessionRevision !== chatOutboxSessionRevision || state.me?.id !== accountId) {
        throw new Error('This conversation is no longer available.');
      }
      if (sending) return null;
      sending = true;
      clearTimeout(draftTimer);
      persistDraftNow();
      const submittedRevision = draftRevision;
      const buttons = form ? [...form.querySelectorAll('button')] : [];
      const priorDisabled = buttons.map((button) => button.disabled);
      buttons.forEach((button) => { button.disabled = true; button.setAttribute('aria-busy', 'true'); });
      form?.setAttribute('aria-busy', 'true');
      try {
        const result = await enqueueChatOutboxMessage(accountId, channelKey, submittedBody, image);
        completeSend(submittedBody, submittedRevision);
        return result;
      } finally {
        sending = false;
        buttons.forEach((button, index) => {
          button.disabled = priorDisabled[index];
          button.removeAttribute('aria-busy');
        });
        form?.removeAttribute('aria-busy');
      }
    };
    const restoreScroll = (snapshot, { forceBottom = false, newMessageCount = 0 } = {}) => {
      if (!snapshot || forceBottom || snapshot.nearBottom) {
        scrollToLatest();
        return;
      }
      if (snapshot.anchor && snapshot.anchor.isConnected && snapshot.anchorTop != null) {
        msgsEl.scrollTop = snapshot.scrollTop + (snapshot.anchor.getBoundingClientRect().top - snapshot.anchorTop);
      } else {
        msgsEl.scrollTop = snapshot.scrollTop;
      }
      following = false;
      if (newMessageCount > 0) showNewMessages(newMessageCount);
    };

    let outboxBinding = null;
    let outboxRenderRevision = 0;
    const renderOutbox = async () => {
      const revision = ++outboxRenderRevision;
      let items;
      try { items = await listChatOutbox(accountId, channelKey); }
      catch {
        if (revision === outboxRenderRevision) {
          outboxStatus.textContent = 'The outbox is temporarily unavailable. Your composer draft is unchanged.';
        }
        return;
      }
      if (revision !== outboxRenderRevision || !document.body.contains(msgsEl)) return;
      const snapshot = chatScrollSnapshot(msgsEl);
      msgsEl.querySelectorAll('[data-client-attempt-id]').forEach((node) => node.remove());
      if (items.length) msgsEl.querySelector('.empty-state')?.remove();
      const html = items.map((item) => {
        const sendingNow = item.status === 'sending';
        const stateText = sendingNow ? 'Sending…'
          : item.status === 'failed' ? (item.error || 'Not sent')
            : (item.error || (navigator.onLine ? 'Waiting to send…' : 'Saved · sends when online'));
        return `
          <div class="chat-outbox-item is-${item.status}" data-client-attempt-id="${item.attemptId}" role="group" aria-label="Unsent message">
            <div class="bubble me chat-outbox-bubble">
              ${item.image ? `<img class="chat-outbox-image" src="${esc(item.image)}" alt="Photo awaiting send" />` : ''}
              ${item.body ? `<div>${esc(item.body)}</div>` : ''}
              <div class="bubble-time chat-outbox-state">${esc(stateText)}</div>
            </div>
            ${sendingNow ? '' : `<div class="chat-outbox-actions">
              <button type="button" data-outbox-retry="${item.attemptId}">Retry</button>
              <button type="button" data-outbox-remove="${item.attemptId}">Remove</button>
            </div>`}
          </div>`;
      }).join('');
      if (html) msgsEl.insertAdjacentHTML('beforeend', html);
      else if (!msgsEl.querySelector('[data-message-id], .empty-state')) {
        msgsEl.innerHTML = '<div class="empty-state" style="padding:20px">No messages yet.</div>';
      }
      restoreScroll(snapshot, { forceBottom: snapshot.nearBottom });
    };

    const onOutboxAction = async (event) => {
      const retry = event.target.closest('[data-outbox-retry]');
      const remove = event.target.closest('[data-outbox-remove]');
      if ((!retry && !remove) || state.me?.id !== accountId) return;
      const button = retry || remove;
      const attemptId = button.dataset.outboxRetry || button.dataset.outboxRemove;
      if (!CHAT_OUTBOX_ATTEMPT_RE.test(attemptId || '')) return;
      button.disabled = true;
      try {
        if (retry) await retryChatOutboxMessage(accountId, attemptId);
        else await removeChatOutboxMessage(accountId, channelKey, attemptId);
      } catch {
        button.disabled = false;
        outboxStatus.textContent = 'That action did not finish. Try again.';
      }
    };
    msgsEl.addEventListener('click', onOutboxAction);

    const activateOutbox = (onDelivered) => {
      if (outboxBinding || outboxSessionRevision !== chatOutboxSessionRevision
          || state.me?.id !== accountId) return;
      const key = chatOutboxBindingKey(accountId, channelKey);
      outboxBinding = {
        refresh: renderOutbox,
        onDelivered,
        announce: (message) => { outboxStatus.textContent = message; },
      };
      if (!chatOutboxBindings.has(key)) chatOutboxBindings.set(key, new Set());
      chatOutboxBindings.get(key).add(outboxBinding);
      renderOutbox();
      if (navigator.onLine) flushChatOutboxForAccount(accountId, channelKey);
    };

    attachChatViewport(modal, msgsEl, inputEl, () => following);
    modal._cleanupFns?.push(() => {
      msgsEl.removeEventListener('scroll', syncFollowing);
      msgsEl.removeEventListener('click', onOutboxAction);
      if (outboxBinding) {
        const key = chatOutboxBindingKey(accountId, channelKey);
        const bindings = chatOutboxBindings.get(key);
        bindings?.delete(outboxBinding);
        if (bindings && !bindings.size) chatOutboxBindings.delete(key);
        outboxBinding = null;
      }
      if (draftTimer != null) persistDraftNow();
    });
    return {
      captureScroll: () => chatScrollSnapshot(msgsEl),
      restoreScroll,
      send,
      activateOutbox,
    };
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

  // Build a calendar event for a game (90 min block, court as location).
  function gameToIcs(game) {
    const court = game.court || {};
    const pad = (n) => String(n).padStart(2, '0');
    const stamp = (d) => `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}00Z`;
    const start = new Date(game.scheduled_at);
    const end = new Date(start.getTime() + 90 * 60000);
    const escIcs = (s) => String(s || '').replace(/\\/g, '\\\\').replace(/[,;]/g, (m) => '\\' + m).replace(/\n/g, '\\n');
    return [
      'BEGIN:VCALENDAR',
      'VERSION:2.0',
      'PRODID:-//Third Shot//EN',
      'BEGIN:VEVENT',
      `UID:thirdshot-game-${game.id}@thirdshot.app`,
      `DTSTAMP:${stamp(new Date())}`,
      `DTSTART:${stamp(start)}`,
      `DTEND:${stamp(end)}`,
      `SUMMARY:${escIcs(`Pickleball${game.game_type === 'ranked' ? ' (ranked)' : ''} at ${court.name || 'the court'}`)}`,
      `LOCATION:${escIcs([court.name, court.city].filter(Boolean).join(', '))}`,
      `DESCRIPTION:${escIcs(`${game.players.length}/${game.max_players} players · ${location.origin}/#game/${game.id}`)}`,
      'END:VEVENT',
      'END:VCALENDAR',
    ].join('\r\n');
  }

  function downloadIcs(game) {
    const blob = new Blob([gameToIcs(game)], { type: 'text/calendar' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `pickleball-game-${game.id}.ics`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    toast('Calendar event downloaded 📅');
  }

  function downloadTournamentIcs(t) {
    const court = t.court || {};
    const pad = (n) => String(n).padStart(2, '0');
    const stamp = (d) => `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}00Z`;
    const start = new Date(t.starts_at);
    const end = new Date(start.getTime() + 4 * 3600e3);
    const escIcs = (s) => String(s || '').replace(/\\/g, '\\\\').replace(/[,;]/g, (m) => '\\' + m).replace(/\n/g, '\\n');
    const ics = [
      'BEGIN:VCALENDAR',
      'VERSION:2.0',
      'PRODID:-//Third Shot//EN',
      'BEGIN:VEVENT',
      `UID:thirdshot-tournament-${t.id}@thirdshot.app`,
      `DTSTAMP:${stamp(new Date())}`,
      `DTSTART:${stamp(start)}`,
      `DTEND:${stamp(end)}`,
      `SUMMARY:${escIcs(`🏆 ${t.name}`)}`,
      `LOCATION:${escIcs([court.name, court.city].filter(Boolean).join(', '))}`,
      `DESCRIPTION:${escIcs(`Pickleball tournament · ${location.origin}/#tournament/${t.id}`)}`,
      'END:VEVENT',
      'END:VCALENDAR',
    ].join('\r\n');
    const blob = new Blob([ics], { type: 'text/calendar' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `pickleball-tournament-${t.id}.ics`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    toast('Calendar event downloaded 📅');
  }

  // Tiny SVG sparkline of the ranked-rating trajectory.
  function ratingSparklineHtml(history) {
    if (!history || history.length < 2) return '';
    const w = 280, h = 44, pad = 3;
    const vals = history.map((p) => p.rating);
    const min = Math.min(...vals), max = Math.max(...vals);
    const span = Math.max(1, max - min);
    const pts = vals.map((v, i) =>
      `${(pad + (i * (w - 2 * pad)) / (vals.length - 1)).toFixed(1)},${(h - pad - ((v - min) * (h - 2 * pad)) / span).toFixed(1)}`);
    const [lastX, lastY] = pts[pts.length - 1].split(',');
    const delta = vals[vals.length - 1] - vals[0];
    return `
      <div style="margin-top:12px">
        <div class="row-sub" style="display:flex;justify-content:space-between;margin-bottom:2px">
          <span>📈 Rating history</span>
          <span>${delta === 0 ? `${max}` : `${delta > 0 ? '+' : ''}${delta} · now ${vals[vals.length - 1]}`}</span>
        </div>
        <svg viewBox="0 0 ${w} ${h}" style="width:100%;height:${h}px;display:block" preserveAspectRatio="none" role="img" aria-label="Rating over your last ranked games">
          <polyline points="${pts.join(' ')}" fill="none" stroke="var(--green-600)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
          <circle cx="${lastX}" cy="${lastY}" r="3" fill="var(--green-600)"/>
        </svg>
      </div>`;
  }

  function formStripHtml(form) {
    if (!form || !form.length) return '';
    return `
      <div style="display:flex;gap:5px;justify-content:center;align-items:center;margin-top:10px">
        <span class="row-sub" style="margin-right:2px">Last ${form.length}:</span>
        ${form.map((r) => `<span style="width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;color:#fff;background:${r === 'W' ? 'var(--green-600)' : '#e03131'}">${r}</span>`).join('')}
      </div>`;
  }

  function weatherEmoji(shortForecast) {
    const t = (shortForecast || '').toLowerCase();
    if (/thunder|storm/.test(t)) return '⛈';
    if (/snow|sleet|ice|flurr/.test(t)) return '❄️';
    if (/rain|shower|drizzle/.test(t)) return '🌧';
    if (/fog|haze|smoke/.test(t)) return '🌫';
    if (/cloud|overcast/.test(t)) return '⛅';
    if (/clear|sunny/.test(t)) return '☀️';
    return '🌤';
  }

  const COURT_CONDITION_LABELS = {
    good: ['🟢', 'All good — come play!'],
    busy: ['🚶', 'Busy — expect a wait'],
    wet: ['💦', 'Wet courts'],
    nets_down: ['🚧', 'Nets down / missing'],
    closed: ['⛔', 'Closed right now'],
  };

  function openAddCourtSheet() {
    const center = state.map ? state.map.getCenter() : { lat: null, lng: null };
    const modal = openModal(`
      ${modalHead('➕ Add a missing court')}
      <form id="ac-form" novalidate>
      <p class="row-sub" style="margin-bottom:12px">Center the map on the court first — we'll pin it right where the map is looking now.</p>
      <div class="form-field">
        <label for="ac-name">Court name</label>
        <input type="text" id="ac-name" maxlength="255" placeholder="e.g. Riverside Park Courts" />
      </div>
      <div class="form-field">
        <label for="ac-courts">Number of courts</label>
        <input type="number" id="ac-courts" min="1" max="100" value="2" inputmode="numeric" />
      </div>
      <div class="form-field">
        <label class="row" style="gap:8px;padding:6px 0;cursor:pointer"><input type="checkbox" id="ac-indoor" style="width:18px;height:18px" /> 🏠 Indoor</label>
        <label class="row" style="gap:8px;padding:6px 0;cursor:pointer"><input type="checkbox" id="ac-lighted" style="width:18px;height:18px" /> 💡 Lighted</label>
      </div>
      <button type="submit" class="btn btn-primary btn-block" id="ac-submit" style="padding:15px">Add court</button>
      </form>
    `);
    const formUX = bindModalFormUX(modal, '#ac-submit', { draftKey: 'add-court' });
    modal.querySelector('#ac-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      formUX.clearError();
      const name = modal.querySelector('#ac-name').value.trim();
      if (name.length < 3) {
        formUX.showError('Give the court a name (3+ characters).', modal.querySelector('#ac-name'));
        return;
      }
      const finishSubmitting = formUX.startSubmitting('Adding court…');
      if (!finishSubmitting) return;
      try {
        const court = await api('/courts', {
          method: 'POST',
          body: JSON.stringify({
            name,
            latitude: center.lat,
            longitude: center.lng,
            num_courts: Number(modal.querySelector('#ac-courts').value) || 2,
            indoor: modal.querySelector('#ac-indoor').checked,
            lighted: modal.querySelector('#ac-lighted').checked,
          }),
        });
        formUX.clearDraft({ disable: true });
        toast('Court added — thanks for growing the map! 🏓');
        if (state.favIds) state.favIds.add(court.id);
        fetchCourtsInView();
        transitionModal(modal, () => openCourtDetail(court.id));
      } catch (err) {
        finishSubmitting();
        formUX.showError(err.message);
      }
    });
  }

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

  // Lazy-load photo attachments into any chat thread (payloads only carry
  // has_image; the image endpoint enforces per-thread permissions).
  function hydrateChatImages(msgsEl, chatUX) {
    msgsEl.querySelectorAll('[data-img-id]:not([data-loaded])').forEach(async (slot) => {
      slot.dataset.loaded = '1';
      try {
        const { image } = await api(`/messages/${slot.dataset.imgId}/image`);
        const img = new Image();
        img.alt = 'Photo';
        img.style.cssText = 'max-width:100%;border-radius:10px;display:block';
        img.src = image;
        if (typeof img.decode === 'function') await img.decode();
        else if (!img.complete) {
          await new Promise((resolve, reject) => {
            img.addEventListener('load', resolve, { once: true });
            img.addEventListener('error', reject, { once: true });
          });
        }
        const snapshot = chatUX?.captureScroll();
        slot.replaceChildren(img);
        if (chatUX) chatUX.restoreScroll(snapshot);
        else msgsEl.scrollTop = msgsEl.scrollHeight;
      } catch {
        const snapshot = chatUX?.captureScroll();
        slot.remove();
        if (chatUX) chatUX.restoreScroll(snapshot);
      }
    });
  }

  // Add a 📷 button + hidden file input to a chat composer form.
  function addPhotoToComposer(modal, formSel, textSel, chatUX) {
    const form = modal.querySelector(formSel);
    if (!form) return;
    const file = document.createElement('input');
    file.type = 'file';
    file.accept = 'image/*';
    file.className = 'hidden';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = '📷';
    btn.setAttribute('aria-label', 'Send a photo');
    btn.style.cssText = 'background:transparent;font-size:19px;padding:0 2px';
    form.prepend(file);
    form.prepend(btn);
    btn.addEventListener('click', () => {
      if (file.value) file.value = '';
      file.click();
    });
    file.addEventListener('change', async (e) => {
      const picked = e.target.files[0];
      if (!picked) return;
      try {
        const image = await imageFileToDataUrl(picked, 1024);
        const textEl = modal.querySelector(textSel);
        const body = textEl.value.trim();
        await chatUX.send(body, { image });
        e.target.value = '';
      } catch (err) {
        if (err.message === 'image_too_large' || err.message === 'bad_image') e.target.value = '';
        toast(err.message === 'image_too_large' ? 'That photo is too large — try a smaller one' : err.message);
      }
    });
  }

  // A dead shared link shouldn't re-toast its error on every reload.
  function clearDeadDeepLink(hash) {
    if (location.hash !== hash) return;
    routedOverlayLoadSeq += 1;
    overlayHistoryRevision += 1;
    activeRoutedOverlayLoad = null;
    pendingReusableTraversal = null;
    adoptOverlayEntry = null;
    try { history.replaceState(overlayHistoryState(null, 0, null), '', baseAppUrl()); } catch { /* ignore */ }
  }

  async function openCourtDetail(courtId) {
    const routeLoad = beginRoutedOverlayLoad({ kind: 'court', id: courtId });
    let court;
    try { court = await api(`/courts/${courtId}`); } catch (e) {
      if (!routedOverlayLoadIsCurrent(routeLoad)) return;
      toast(e.message);
      clearDeadDeepLink(`#court/${courtId}`);
      return;
    }
    if (!routedOverlayLoadIsCurrent(routeLoad)) return;

    const tags = [];
    if (court.indoor) tags.push('🏠 Indoor'); else tags.push('☀️ Outdoor');
    if (court.lighted) tags.push('💡 Lighted');
    tags.push(`🏟 ${court.num_courts} court${court.num_courts === 1 ? '' : 's'}`);
    if (court.surface_type) tags.push(esc(court.surface_type));
    if (court.nets_provided) tags.push('🥅 Nets provided');
    if (court.has_restrooms) tags.push('🚻 Restrooms');
    if (court.has_water) tags.push('🚰 Water');
    if (court.hours) tags.push(`🕐 ${esc(court.hours)}`);
    if (court.fees) tags.push(`<span class="tag warn" style="margin:0">💵 ${esc(court.fees)}</span>`);
    if (court.my_record) {
      const r = court.my_record;
      tags.push(`<span class="tag ${r.wins >= r.losses ? 'live' : ''}" style="margin:0">🎯 You're ${r.wins}–${r.losses} here</span>`);
    }

    // Apple devices get Apple Maps; everyone else gets Google directions.
    // Community-added courts often have no street address — navigate by
    // coordinates then, never by a "null Austin" string.
    const addrForMaps = [court.address, court.city].filter(Boolean).join(' ');
    const mapsUrl = /iPhone|iPad|Macintosh/.test(navigator.userAgent)
      ? `https://maps.apple.com/?daddr=${encodeURIComponent(addrForMaps || `${court.latitude},${court.longitude}`)}&ll=${court.latitude},${court.longitude}`
      : `https://www.google.com/maps/dir/?api=1&destination=${court.latitude},${court.longitude}`;

    const playersHtml = court.players_here.length
      ? court.players_here.map((p) => {
          const badges = [];
          if (p.is_me) badges.push('<span class="tag" style="margin:0 0 0 6px">You</span>');
          else if (p.is_friend) badges.push('<span class="tag" style="margin:0 0 0 6px">🤝 Friend</span>');
          if (p.looking_for_game) badges.push('<span class="tag live" style="margin:0 0 0 6px">Wants to play</span>');
          const record = (p.ranked_wins + p.ranked_losses) > 0 ? ` · ${p.ranked_wins}W–${p.ranked_losses}L` : '';
          const actions = p.is_me ? '' : `
            <button class="btn btn-sm" data-challenge="${p.id}" title="Challenge to a ranked match" style="background:var(--violet-100);color:var(--violet-700)">⚔️</button>
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
    // Group by day (backend sends them sorted by scheduled_at).
    const gamesByDay = [];
    if (court.games.length) {
      for (const g of court.games) {
        const label = upcomingDayLabel(g.scheduled_at);
        if (!gamesByDay.length || gamesByDay[gamesByDay.length - 1].label !== label) {
          gamesByDay.push({ label, games: [] });
        }
        gamesByDay[gamesByDay.length - 1].games.push(g);
      }
      // Day chips filter the list below; tap again to see the whole week.
      if (gamesByDay.length > 1) {
        gamesHtml += `<div class="quick-times" id="cd-day-chips" style="margin:0 0 10px">${gamesByDay
          .map((d, i) => `<button type="button" data-cd-day="${i}">${esc(d.label)} · ${d.games.length}</button>`)
          .join('')}</div>`;
      }
      gamesHtml += '<div id="cd-games-list"></div>';
    } else {
      gamesHtml = '<div class="empty-state" style="padding:14px">No upcoming games here yet.<br><button class="btn btn-secondary btn-sm" id="cd-schedule-empty" style="margin-top:8px">📅 Schedule one</button></div>';
    }

    const checkedIn = court.is_checked_in;
    let isFavorite = court.is_favorite;
    const heroImg = court.photo_url
      ? `<img class="cd-hero-img" src="${esc(court.photo_url)}" alt="" onerror="this.outerHTML='<div class=\\'cd-hero-img placeholder\\'>🏓</div>'">`
      : '<div class="cd-hero-img placeholder">🏓</div>';
    const chipsHtml = tags.map((t) => t.startsWith('<span') ? t : `<span class="tag">${t}</span>`).join('');
    const linkParts = [];
    if (court.website) linkParts.push(`<a href="${esc(court.website)}" target="_blank" rel="noopener">🌐 Website</a>`);
    if (court.phone) linkParts.push(`<a href="tel:${esc(court.phone)}">📞 ${esc(court.phone)}</a>`);

    // At-a-glance strip: each cell jumps to its section further down the sheet.
    const distMi = state.userLoc && court.latitude != null
      ? milesBetween(state.userLoc, [court.latitude, court.longitude]) : null;
    const nGames = court.games.length;
    const nHere = court.players_here.length;
    const statCells = [
      {
        v: court.rating_avg ? `⭐ ${court.rating_avg}` : '☆ —',
        l: court.rating_avg ? `${court.rating_count} rating${court.rating_count === 1 ? '' : 's'}` : 'rate it first',
        to: 'cd-sec-reviews',
      },
      { v: String(nHere), l: 'playing now', to: 'cd-sec-players', hot: nHere > 0 },
      { v: String(nGames), l: `game${nGames === 1 ? '' : 's'} coming up`, to: 'cd-sec-games', hot: nGames > 0 },
    ];
    if (distMi != null) {
      statCells.push({ v: distMi < 10 ? distMi.toFixed(1) : String(Math.round(distMi)), l: 'miles away' });
    }
    const statsHtml = `
      <div class="cd-stats" style="grid-template-columns:repeat(${statCells.length},1fr)">
        ${statCells.map((s) => `
          <button class="cd-stat${s.hot ? ' hot' : ''}"${s.to ? ` data-scroll-to="${s.to}"` : ' disabled style="cursor:default"'}>
            <span class="cd-stat-v">${s.v}</span><span class="cd-stat-l">${s.l}</span>
          </button>`).join('')}
      </div>`;

    if (!routedOverlayLoadIsCurrent(routeLoad)) return;
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
            ${esc([court.address, court.city].filter(Boolean).join(', ')
              || (court.latitude != null ? `${court.latitude.toFixed(5)}, ${court.longitude.toFixed(5)}` : ''))}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:13px;height:13px;vertical-align:-2px;opacity:.85"><rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>
          </div>
        </div>
      </div>
      <div class="cd-scroll">
      ${court.closed ? '<div class="card" style="background:var(--red-50);color:var(--red-700);text-align:center;padding:10px 14px;margin-bottom:10px;font-weight:700">🚫 This court is reported permanently closed</div>' : ''}
      ${statsHtml}
      <button class="btn ${checkedIn ? 'btn-danger' : 'btn-primary'} btn-block" id="cd-checkin" style="padding:15px;margin-bottom:10px">
        ${checkedIn ? 'Check out' : "📍 I'm here — check in"}
      </button>
      <div class="action-grid">
        <button class="action-tile" id="cd-play-now"><span class="tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 3 20 12 6 21 6 3"/></svg></span>Play now</button>
        <button class="action-tile" id="cd-schedule"><span class="tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/></svg></span>Schedule</button>
        <button class="action-tile" id="cd-chat" style="position:relative"><span class="tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/></svg></span>Chat${court.chat_unread ? `<span class="badge" style="top:6px;right:10px">${court.chat_unread > 9 ? '9+' : court.chat_unread}</span>` : ''}</button>
        <a class="action-tile" href="${mapsUrl}" target="_blank" rel="noopener"><span class="tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="3 11 22 2 13 21 11 13 3 11"/></svg></span>Directions</a>
      </div>
      <div id="cd-weather"></div>
      ${court.latest_condition ? (() => {
        const c = COURT_CONDITION_LABELS[court.latest_condition.condition] || ['📣', court.latest_condition.condition];
        const mins = Math.max(1, Math.round((Date.now() - new Date(court.latest_condition.reported_at)) / 60000));
        return `<div class="card row" style="margin-top:12px;padding:10px 14px;background:${court.latest_condition.condition === 'good' ? 'var(--green-50)' : 'var(--amber-50)'}">
          <span style="font-size:18px">${c[0]}</span>
          <div class="row-main">
            <div class="row-title" style="font-size:14px">${c[1]}</div>
            <div class="row-sub">reported ${mins < 60 ? `${mins}m` : `${Math.round(mins / 60)}h`} ago by ${esc(court.latest_condition.user_name)}</div>
          </div>
        </div>`;
      })() : ''}
      <div style="margin-top:14px">${chipsHtml}
        ${state.token && state.me && state.me.home_court_id !== court.id
          ? '<button id="cd-sethome" class="tag" style="border:1px dashed var(--line);background:transparent;cursor:pointer">🏠 Make home court</button>'
          : (state.me && state.me.home_court_id === court.id ? '<span class="tag" style="margin:0">🏠 Your home court</span>' : '')}
        <button id="cd-suggest" class="tag" style="border:1px dashed var(--line);background:transparent;cursor:pointer">✏️ Suggest an edit</button>
        <button id="cd-condition" class="tag" style="border:1px dashed var(--line);background:transparent;cursor:pointer">📣 Report conditions</button>
      </div>
      ${court.busy_times && court.busy_times.length
        ? `<div class="row-sub" style="margin-top:10px">📊 Popular here: ${court.busy_times.map((b) => esc(b.label)).join(' · ')}</div>`
        : ''}
      ${court.open_play_schedule ? `
        <details class="cd-hours">
          <summary>🕑 Open play hours</summary>
          <p>${esc(court.open_play_schedule)}</p>
        </details>` : ''}
      ${linkParts.length ? `<div class="cd-links">${linkParts.join('')}</div>` : ''}
      <div class="section-label" id="cd-sec-players">Playing now (${court.players_here.length})${court.friends_here ? ` · ${court.friends_here} friend${court.friends_here === 1 ? '' : 's'} here` : ''}</div>
      ${playersHtml}
      ${(court.regulars || []).length ? `
        <div class="section-label">Court regulars</div>
        ${court.regulars.map((p, i) => `
          <div class="card row" data-view-user="${p.id}" style="cursor:pointer;padding:11px">
            ${avatarHtml(p, 'sm')}
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(p.display_name)}${i === 0 && p.visits >= 3 ? ' <span class="tag" style="margin:0 0 0 4px;background:var(--amber-50);color:var(--amber-800)">👑 Mayor</span>' : ''}</div>
              <div class="row-sub">${skillLabel(p.skill_level)} · ${p.rating} · ${p.visits} visit${p.visits === 1 ? '' : 's'} recently</div>
            </div>
            <span class="chev">›</span>
          </div>`).join('')}` : ''}
      ${(court.court_leaders || []).length ? `
        <div class="section-label">🏆 Court champions</div>
        ${court.court_leaders.map((p, i) => `
          <div class="card row" data-view-user="${p.id}" style="cursor:pointer;padding:11px">
            <span style="font-size:18px;width:24px;text-align:center">${['🥇', '🥈', '🥉'][i] || (i + 1)}</span>
            ${avatarHtml(p, 'sm')}
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(p.display_name)}</div>
              <div class="row-sub">${p.wins}–${p.losses} here · ${skillLabel(p.skill_level)}</div>
            </div>
            <span class="chev">›</span>
          </div>`).join('')}` : ''}
      ${(court.tournaments || []).length ? `
        <div class="section-label">🏆 Tournaments here</div>
        ${court.tournaments.map(tournamentCardHtml).join('')}
        <button class="btn btn-secondary btn-block btn-sm" id="cd-host-tournament" style="margin-top:4px">🏆 Host a tournament here</button>` : `
        <button class="btn btn-secondary btn-block btn-sm" id="cd-host-tournament" style="margin-top:10px">🏆 Host a tournament here</button>`}
      ${(court.past_champions || []).length ? `
        <div class="section-label">👑 Past champions here</div>
        ${court.past_champions.map((pc) => `
          <div class="card row" data-open-tournament="${pc.tournament_id}" style="cursor:pointer;padding:10px 14px">
            <span style="font-size:20px">👑</span>
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(pc.champion_name)}</div>
              <div class="row-sub">${esc(pc.tournament_name)}${pc.completed_at ? ` · ${new Date(pc.completed_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}` : ''}</div>
            </div>
            <span class="chev">›</span>
          </div>`).join('')}` : ''}
      <div id="cd-clubs"></div>
      <div id="cd-leagues"></div>
      <div class="section-label" id="cd-sec-games">Upcoming games</div>
      ${gamesHtml}
      ${(court.recent_results || []).length ? `
        <div class="section-label">Recent results here</div>
        ${court.recent_results.map(resultRowHtml).join('')}` : ''}
      <div class="section-label" id="cd-sec-reviews">Reviews${court.rating_avg ? ` · ⭐ ${court.rating_avg} (${court.rating_count})` : ''}</div>
      <div id="cd-reviews"></div>
      </div>
    `, { court: true, route: { kind: 'court', id: court.id } });

    renderReviewSection(modal.querySelector('#cd-reviews'), court);

    // Stat cells scroll the sheet to their section.
    modal.querySelectorAll('[data-scroll-to]').forEach((btn) => {
      btn.addEventListener('click', () => {
        modal.querySelector(`#${btn.dataset.scrollTo}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });

    // Playability at a glance — loads after the sheet so it never blocks.
    api(`/courts/${court.id}/weather`).then((w) => {
      const el = modal.querySelector('#cd-weather');
      if (!el || w.error || w.temp_f == null) return;
      el.innerHTML = `<div class="row-sub" style="text-align:center;margin-top:10px">
        ${weatherEmoji(w.short)} ${w.temp_f}°F${w.short ? ` · ${esc(w.short)}` : ''} · ${w.rain_soon ? '🌧 rain likely soon' : 'dry for the next few hours'}
      </div>`;
    }).catch(() => { /* forecast is a nicety */ });

    // Clubs that call this court home — loads after the sheet, never blocks.
    api(`/clubs?court_id=${court.id}`).then((data) => {
      const clubsEl = modal.querySelector('#cd-clubs');
      if (!clubsEl || !(data.items || []).length) return;
      clubsEl.innerHTML = `
        <div class="section-label">🏛 Clubs based here</div>
        ${data.items.map((cl) => `
          <div class="card row" data-open-club="${cl.id}" style="cursor:pointer;padding:11px">
            <span style="font-size:20px">🏛</span>
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(cl.name)}</div>
              <div class="row-sub">${cl.member_count} member${cl.member_count === 1 ? '' : 's'}${cl.description ? ` · ${esc(cl.description.slice(0, 60))}` : ''}</div>
            </div>
            ${cl.joined ? '<span class="tag" style="margin:0">Member ✓</span>' : '<span class="chev">›</span>'}
          </div>`).join('')}`;
      clubsEl.querySelectorAll('[data-open-club]').forEach((row) => row.addEventListener('click', () => {
        transitionModal(modal, () => openClubScreen(Number(row.dataset.openClub)));
      }));
    }).catch(() => { /* clubs section is a nicety */ });

    // Box leagues running at this court — same lazy pattern as clubs.
    api(`/leagues?court_id=${court.id}`).then((data) => {
      const leaguesEl = modal.querySelector('#cd-leagues');
      if (!leaguesEl || !(data.items || []).length) return;
      leaguesEl.innerHTML = `
        <div class="section-label">📦 Leagues here</div>
        ${data.items.map((lg) => `
          <div class="card row" data-open-league="${lg.id}" style="cursor:pointer;padding:11px">
            <span style="font-size:20px">📦</span>
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(lg.name)}</div>
              <div class="row-sub">${lg.status === 'registration' ? `Signups open · ${lg.member_count}/${lg.max_players}` : lg.status === 'completed' ? `🏁 Season complete${lg.champion_name ? ` · 👑 ${esc(lg.champion_name)}` : ''}` : `Round ${lg.current_round} · ${lg.member_count} players`}${lg.joined && lg.status !== 'completed' ? ' · ✓ in' : ''}</div>
            </div>
            <span class="chev">›</span>
          </div>`).join('')}`;
      leaguesEl.querySelectorAll('[data-open-league]').forEach((row) => row.addEventListener('click', () => {
        transitionModal(modal, () => openLeagueScreen(Number(row.dataset.openLeague)));
      }));
    }).catch(() => { /* leagues section is a nicety */ });

    modal.querySelector('#cd-checkin').addEventListener('click', async () => {
      if (checkedIn) {
        const prev = state.presence;
        try {
          await api('/checkout', { method: 'POST' });
          const followupLoad = beginFollowupAfterClosingModal(modal);
          toast('Checked out 👋');
          await refreshMe();
          fetchCourtsInView();
          if (followupLoad && routedOverlayLoadIsCurrent(followupLoad)) {
            maybeAskConditions(prev);
          }
        } catch (e) { toast(e.message); }
        return;
      }
      transitionModal(modal, () => openCheckInSheet(court));
    });

    modal.querySelector('#cd-sethome')?.addEventListener('click', async (e) => {
      e.target.disabled = true;
      try {
        applyMe(await api('/me', { method: 'PATCH', body: JSON.stringify({ home_court_id: court.id }) }));
        toast(`🏠 ${court.name} is now your home court`);
        transitionModal(modal, () => openCourtDetail(court.id));
      } catch (err) { toast(err.message); e.target.disabled = false; }
    });
    modal.querySelector('#cd-suggest').addEventListener('click', () => {
      openSuggestEditSheet(court, () => transitionModal(modal, () => openCourtDetail(court.id)));
    });
    modal.querySelector('#cd-condition').addEventListener('click', () => {
      openConditionSheet(court, () => transitionModal(modal, () => openCourtDetail(court.id)));
    });

    const uploadCourtPhoto = (onDone) => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/*';
      input.addEventListener('change', async () => {
        const file = input.files && input.files[0];
        if (!file) return;
        const modalLoad = beginRoutedOverlayLoad(null);
        let photo;
        try { photo = await imageFileToDataUrl(file); }
        catch {
          if (!routedOverlayLoadIsCurrent(modalLoad)) return;
          toast('Could not read that image'); return;
        }
        if (!routedOverlayLoadIsCurrent(modalLoad)) return;
        // Optional caption before it goes up.
        const sheet = openModal(`
          ${modalHead('Add a caption?')}
          <img src="${photo}" alt="Your photo" style="width:100%;border-radius:12px;margin-bottom:10px" />
          <input type="text" id="cap-text" maxlength="140" placeholder="e.g. Fresh nets on courts 1–2! (optional)" />
          <button class="btn btn-primary btn-block" id="cap-save" style="margin-top:12px">📷 Add photo</button>
        `);
        sheet.querySelector('#cap-save').addEventListener('click', async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          try {
            await api(`/courts/${court.id}/photo`, {
              method: 'POST',
              body: JSON.stringify({ photo, caption: sheet.querySelector('#cap-text').value.trim() }),
            });
            closeModal(sheet);
            toast('Photo added 📷 Thanks for contributing!');
            onDone();
          } catch (err) { toast(err.message); btn.disabled = false; }
        });
      });
      input.click();
    };
    modal.querySelector('#cd-add-photo')?.addEventListener('click', () => {
      uploadCourtPhoto(() => transitionModal(modal, () => openCourtDetail(court.id)));
    });
    modal.querySelector('#cd-gallery')?.addEventListener('click', () => {
      openCourtGallery(court, uploadCourtPhoto);
    });

    modal.querySelector('#cd-address').addEventListener('click', async () => {
      const addressText = [court.address, court.city, court.state, court.zip_code]
        .filter(Boolean).join(', ')
        || (court.latitude != null ? `${court.latitude.toFixed(5)}, ${court.longitude.toFixed(5)}` : '');
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
      const url = `${location.origin}/c/${court.id}`; // short link → OG preview in chat apps
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
      transitionModal(modal, () => openNewGameModal(court, 'casual', true));
    });
    modal.querySelector('#cd-schedule').addEventListener('click', () => {
      transitionModal(modal, () => openNewGameModal(court, 'casual'));
    });
    modal.querySelector('#cd-schedule-empty')?.addEventListener('click', () => {
      transitionModal(modal, () => openNewGameModal(court, 'casual'));
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
      transitionModal(modal, () => openThread(Number(b.dataset.msgUser)));
    }));
    modal.querySelectorAll('[data-add-friend-inline]').forEach((b) => b.addEventListener('click', async () => {
      try {
        await api('/friends/request', { method: 'POST', body: JSON.stringify({ user_id: Number(b.dataset.addFriendInline) }) });
        toast('Friend request sent!');
        b.remove();
      } catch (e) { toast(e.message); }
    }));

    bindGameButtons(modal, () => transitionModal(modal, () => openCourtDetail(courtId)));
    bindUserButtons(modal);

    // Upcoming-games day filter: one chip narrows the list to that day,
    // tapping it again brings the whole week back. Rendered after the
    // modal-wide bind above so each card is only ever bound once.
    let cdDayFilter = null;
    const renderCdGames = () => {
      const listEl = modal.querySelector('#cd-games-list');
      if (!listEl) return;
      const days = cdDayFilter == null ? gamesByDay : [gamesByDay[cdDayFilter]];
      listEl.innerHTML = days.map((d) => `
        <div class="section-label" style="font-size:11px;margin-top:8px">${esc(d.label)}</div>
        ${d.games.map((g) => gameCardHtml(g, { compact: true })).join('')}`).join('');
      modal.querySelectorAll('#cd-day-chips [data-cd-day]').forEach((b) =>
        b.classList.toggle('active', Number(b.dataset.cdDay) === cdDayFilter));
      bindGameButtons(listEl, () => transitionModal(modal, () => openCourtDetail(courtId)));
      bindUserButtons(listEl);
    };
    renderCdGames();
    modal.querySelectorAll('#cd-day-chips [data-cd-day]').forEach((b) => {
      b.addEventListener('click', () => {
        const i = Number(b.dataset.cdDay);
        cdDayFilter = cdDayFilter === i ? null : i;
        renderCdGames();
      });
    });
    modal.querySelectorAll('[data-open-tournament]').forEach((card) => {
      card.addEventListener('click', () => openTournamentScreen(Number(card.dataset.openTournament)));
    });
    modal.querySelector('#cd-host-tournament')?.addEventListener('click', () => {
      openCreateTournamentSheet(court);
    });
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
        toast(`⚔️ Challenge sent to ${player.display_name}!`);
        refreshMe();
        transitionModal(modal, () => openGameScreen(game.id));
      } catch (err) { toast(err.message); btn.disabled = false; }
    });
  }

  // Courts without posted hours get one gentle ask per court per device —
  // the check-in moment is when players actually know if the gates are open.
  function maybeAskHours(court) {
    if (court.hours) return;
    const askedKey = `pp_hours_asked_${court.id}`;
    if (localStorage.getItem(askedKey)) return;
    localStorage.setItem(askedKey, '1');
    const modal = openModal(`
      ${modalHead('🕐 Know the hours here?')}
      <p class="row-sub" style="margin-bottom:12px">${esc(court.name)} has no posted hours yet — help players plan their visit.</p>
      <div class="form-field">
        <input type="text" id="hp-hours" maxlength="120" placeholder="e.g. Daily 6am–10pm, Dawn to dusk" />
      </div>
      <button class="btn btn-primary btn-block" id="hp-save">Submit</button>
      <button class="btn-link modal-close btn-block">Skip</button>
    `);
    modal.querySelector('#hp-save').addEventListener('click', async () => {
      const hours = modal.querySelector('#hp-hours').value.trim();
      if (!hours) { closeModal(modal); return; }
      try {
        const res = await api(`/courts/${court.id}/suggest`, { method: 'POST', body: JSON.stringify({ hours }) });
        closeModal(modal);
        toast(res.applied_fields.length ? 'Hours added — thanks! 🕐' : 'Thanks! It applies once another player confirms 🙌');
      } catch (e) { toast(e.message); }
    });
  }

  function openCheckInSheet(court) {
    const modal = openModal(`
      <div class="checkin-sheet">
        <div class="celebrate-emoji" style="font-size:46px">📍</div>
        <h3 style="margin:6px 0 2px">Check in at ${esc(court.name)}</h3>
        <p class="row-sub" style="margin-bottom:18px">Friends will see you're here.</p>
        <button class="btn btn-primary btn-block" id="ci-lfg" style="margin-bottom:10px;padding:16px">
          <svg class="pb-ic"><use href="#pb"/></svg> I'm looking for players
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
        const followupLoad = beginFollowupAfterClosingModal(modal);
        toast(looking ? `You're in — players can find you 🏓` : `Checked in at ${court.name}`);
        await refreshMe();
        fetchCourtsInView();
        if (followupLoad && routedOverlayLoadIsCurrent(followupLoad)) {
          maybeAskHours(court);
        }
      } catch (e) { toast(e.message); }
    };
    modal.querySelector('#ci-lfg').addEventListener('click', () => doCheckIn(true));
    modal.querySelector('#ci-play').addEventListener('click', () => doCheckIn(false));
  }

  // ---------- Games ----------
  // Why a joinable game suits this player: their level, their usual slot.
  function gameMatchReasons(game) {
    if (!state.me || game.is_joined || game.status !== 'upcoming' || game.spots_left <= 0) return [];
    const reasons = [];
    if (game.players.length) {
      const avg = game.players.reduce((s, p) => s + (p.rating || 1200), 0) / game.players.length;
      if (Math.abs(avg - state.me.rating) <= 100) reasons.push('skill');
    }
    const slot = slotForNow(new Date(game.scheduled_at));
    if (slot && (state.me.availability || []).includes(slot)) reasons.push('time');
    return reasons;
  }

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
    const clubTag = game.club_name
      ? `<span class="tag" style="margin:0 0 0 6px">🏛 ${esc(game.club_name)}</span>`
      : '';
    const chatTag = game.is_joined && game.chat_unread
      ? `<span class="tag live" style="margin:0 0 0 6px">💬 ${game.chat_unread > 9 ? '9+' : game.chat_unread} new</span>`
      : '';
    // Discovery aids: flag joinable games near your rating or at your usual slot.
    const reasons = gameMatchReasons(game);
    let levelTag = '';
    // Host's stated level hint comes first; the personal match badges follow.
    if (game.preferred_level && game.preferred_level !== 'any') {
      levelTag += `<span class="tag" style="margin:0 0 0 6px">🎚 ${skillLabel(game.preferred_level)}</span>`;
    }
    if (reasons.includes('skill')) {
      levelTag += '<span class="tag live" style="margin:0 0 0 6px">⚖️ Your level</span>';
    }
    if (reasons.includes('time')) {
      levelTag += '<span class="tag live" style="margin:0 0 0 6px">⏰ Your usual time</span>';
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
      // Hours past its start a game isn't "live" anymore — nag for the score
      // (2+ players) or admit it never happened.
      const stale = Date.now() - startMs > 4 * 3600e3;
      if (game.is_joined) {
        if (inProgress && stale) {
          banner = game.players.length >= 2
            ? '<div class="status-banner">📝 Played? Tap to enter the score.</div>'
            : '<div class="status-banner">😴 This one never filled up.</div>';
          if (game.is_creator) {
            action = `<button class="btn btn-secondary btn-sm" data-game-dismiss="${game.id}">Didn't happen</button>`;
          }
        } else if (inProgress) {
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
              : fmtDateTime(game.scheduled_at))}${typeTag}${visTag}${recurTag}${clubTag}${levelTag}${chatTag}</div>
            <div class="row-sub">${esc(court.name || '')}${!compact && court.city ? ` · ${esc(court.city)}` : ''}${game.distance_miles != null ? ` · ${game.distance_miles} mi` : ''}${hostLabel}</div>
          </div>
          <span class="chev">›</span>
        </div>
        ${banner}
        ${game.notes ? `<div class="row-sub" style="margin-bottom:8px">“${esc(game.notes)}”</div>` : ''}
        <div class="row">
          <div class="avatar-stack">${avatars}</div>
          <span class="row-sub">${game.players.length}/${game.max_players} players${game.spots_left && game.status === 'upcoming' ? ` · ${game.spots_left} spot${game.spots_left === 1 ? '' : 's'} left` : ''}${(() => { const n = game.status === 'upcoming' ? game.players.filter((p) => p.attending).length : 0; return n ? ` · 👋 ${n} coming` : ''; })()}</span>
          <div style="margin-left:auto;display:flex;gap:6px;align-items:center">${action}</div>
        </div>
      </div>`;
  }

  function bindGameButtons(rootEl, refresh) {
    // Tap anywhere on a card to open the game screen; inline buttons stop propagation.
    rootEl.querySelectorAll('[data-open-game]').forEach((card) => makePressable(card, (e) => {
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
      const modalLoad = beginRoutedOverlayLoad(null);
      try {
        const game = await api(`/games/${b.dataset.gameConfirm}/confirm`, { method: 'POST' });
        if (routedOverlayLoadIsCurrent(modalLoad)) showCelebration(game);
        refreshMe();
        refresh();
      } catch (err) {
        if (routedOverlayLoadIsCurrent(modalLoad)) toast(err.message);
      }
    }));
    rootEl.querySelectorAll('[data-game-dismiss]').forEach((b) => b.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm("Mark this game as never played? It'll be cleared from everyone's feed.")) return;
      try {
        await api(`/games/${b.dataset.gameDismiss}/cancel`, { method: 'POST' });
        toast('Cleared 😴');
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
      if (game.you_won === false) return `Battled to ${score}${courtName ? ` at ${courtName}` : ''} — rematch soon 🏓`;
      return `Final: ${score}${courtName ? ` at ${courtName}` : ''} on Third Shot 🏓`;
    }
    return `Join my pickleball game${courtName ? ` at ${courtName}` : ''} — ${fmtDateTime(game.scheduled_at)}`;
  }

  async function shareGame(game) {
    const url = `${location.origin}/g/${game.id}`; // short link → OG preview in chat apps
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
    const emoji = won === true ? '🏆' : won === false ? '🤝' : '🏓';
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
    rootEl.querySelectorAll('[data-view-user]').forEach((b) => makePressable(b, () => {
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

  function rallyLauncherHtml() {
    const here = state.presence && state.presence.checked_in;
    const title = here ? `Ready at ${esc(state.presence.court_name)}` : 'How do you want to play?';
    const sub = here
      ? 'Start a pickup game in seconds, or make a plan for later.'
      : 'Jump into play now or build a plan around your court, time, and people.';
    return `
      <section class="rally-launch" aria-labelledby="rally-title">
        <div class="rally-kicker">Get on court</div>
        <h3 id="rally-title">${title}</h3>
        <p>${sub}</p>
        <div class="rally-actions">
          <button type="button" class="rally-action primary" data-goto="play-now">
            <span class="rally-action-icon">⚡</span>
            <span><b>Play now</b><small>Start a live game</small></span>
          </button>
          <button type="button" class="rally-action" data-goto="new-game">
            <span class="rally-action-icon">📅</span>
            <span><b>Plan ahead</b><small>Pick a time & crew</small></span>
          </button>
        </div>
      </section>`;
  }

  async function renderPlay({ reuseFresh = false, useCachedData = false } = {}) {
    const seg = state.playSeg;
    const liveEl = $('#play-content');
    const viewKey = `${state.me?.id || 'signed-out'}:play:${seg}`;
    if (reuseFresh && viewIsFresh(liveEl, viewKey)) return;
    const renderSeq = ++state.playRenderSeq;
    const hadUsableContent = beginViewRender(liveEl, viewKey, 5);
    const el = document.createElement('div');
    const commit = () => {
      if (renderSeq !== state.playRenderSeq || state.playSeg !== seg) return false;
      commitViewRender(liveEl, el, viewKey);
      return true;
    };
    const loc = areaLatLng();
    if (seg === 'brackets') { await renderTournaments(el, () => renderPlay()); commit(); return; }
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
              <div class="podium-name">${esc(u.display_name.split(' ')[0])}${u.tournament_titles ? ' 👑' : ''}${u.current_streak >= 2 ? ' 🔥' : ''}</div>
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
                  <div class="row-title" style="font-size:14px">${esc(u.display_name)}${u.tournament_titles ? ` <span title="Tournament titles">👑${u.tournament_titles > 1 ? u.tournament_titles : ''}</span>` : ''}${u.current_streak >= 2 ? ` <span title="Win streak">🔥${u.current_streak}</span>` : ''}</div>
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
              <button type="button" class="btn btn-primary btn-sm" data-goto="new-ranked-game">Play ranked</button>
              <div class="stat-value" style="font-size:16px">${me.rating}</div>
            </div>`;
          }
        } else {
          html += scope === 'near'
            ? '<div class="empty-state"><span class="big">🏆</span><b>Claim the local crown.</b><br>No ranked players are on the board here yet.<br><button class="btn btn-primary" data-goto="new-ranked-game">⚔️ Start a ranked game</button></div>'
            : '<div class="empty-state"><span class="big">🏆</span><b>Be first on the podium.</b><br>No ranked games have been recorded yet.<br><button class="btn btn-primary" data-goto="new-ranked-game">⚔️ Start a ranked game</button></div>';
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

        if (state.playSeg !== seg) return; // a newer segment render owns the panel
        el.innerHTML = html;
        el.querySelector('#board-scope').addEventListener('click', (e) => {
          const btn = e.target.closest('button');
          if (!btn) return;
          state.boardScope = btn.dataset.scope;
          renderPlay();
        });
        bindGameButtons(el, renderPlay);
        bindUserButtons(el);
        commit();
        return;
      }

      // --- Games: everything actionable + yours + friends + nearby, one scroll ---
      let gameBundle = useCachedData && state.playGamesCache;
      if (!gameBundle) {
        gameBundle = await Promise.all([
          api('/games?mine=1'),
          api('/games?friends=1').catch(() => ({ items: [] })),
          api(`/games?lat=${loc.lat}&lng=${loc.lng}&radius=60`),
          api('/tournaments?mine=1').catch(() => ({ items: [] })),
          api(`/tournaments?lat=${loc.lat}&lng=${loc.lng}&radius=60`).catch(() => ({ items: [] })),
        ]);
        state.playGamesCache = gameBundle;
      }
      const [mine, friends, nearby, tMine, tNear] = gameBundle;
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

      // --- "This week" planner: one strip answering "when is there play?" ---
      const dayKey = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      const week = [];
      for (let i = 0; i < 7; i++) {
        const d = new Date(); d.setDate(d.getDate() + i); d.setHours(0, 0, 0, 0);
        week.push(d);
      }
      const events = [];
      const evSeen = new Set();
      const addGameEvent = (g) => {
        if (g.status !== 'upcoming' || evSeen.has(`g${g.id}`)) return;
        evSeen.add(`g${g.id}`);
        events.push({ when: new Date(g.scheduled_at), type: 'game', item: g });
      };
      mine.items.forEach(addGameEvent);
      friendsGames.forEach(addGameEvent);
      nearbyOpen.forEach(addGameEvent);
      [...(tMine.items || []), ...(tNear.items || [])].forEach((t) => {
        if (evSeen.has(`t${t.id}`) || (t.status !== 'registration' && t.status !== 'active')) return;
        evSeen.add(`t${t.id}`);
        events.push({ when: new Date(t.starts_at), type: 'tournament', item: t });
      });
      const countsByDay = week.map((d) => events.filter((ev) => dayKey(ev.when) === dayKey(d)).length);
      const sel = state.weekDayFilter;
      const dayLabelShort = (d, i) => (i === 0 ? 'Today' : i === 1 ? 'Tmrw' : d.toLocaleDateString([], { weekday: 'short' }));

      let html = rallyLauncherHtml() + `<div class="quick-times" id="week-strip" style="margin:2px 0 10px">${week.map((d, i) => `
        <button type="button" data-day-i="${i}" class="${sel === i ? 'active' : ''}">${dayLabelShort(d, i)}${countsByDay[i] ? ` · ${countsByDay[i]}` : ''}</button>`).join('')}</div>`;
      const bindWeekStrip = () => el.querySelector('#week-strip')?.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-day-i]');
        if (!btn) return;
        const i = Number(btn.dataset.dayI);
        state.weekDayFilter = state.weekDayFilter === i ? null : i;
        renderPlay({ useCachedData: true });
      });

      if (sel != null) {
        // Day view: just that day's play, in time order.
        const dayEvents = events
          .filter((ev) => dayKey(ev.when) === dayKey(week[sel]))
          .sort((a, b) => a.when - b.when);
        html += `<div class="section-label">📅 ${sel === 0 ? 'Today' : week[sel].toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' })}</div>`;
        html += dayEvents.length
          ? dayEvents.map((ev) => (ev.type === 'tournament' ? tournamentCardHtml(ev.item) : gameCardHtml(ev.item))).join('')
          : '<div class="empty-state" style="padding:18px">Nothing on the calendar yet.<br><button class="btn btn-primary" data-goto="new-game" style="margin-top:10px"><svg class="pb-ic"><use href="#pb"/></svg> Start a game</button></div>';
        if (state.playSeg !== seg) return; // a newer segment render owns the panel
        el.innerHTML = html;
        bindWeekStrip();
        bindGameButtons(el, renderPlay);
        el.querySelectorAll('[data-open-tournament]').forEach((card) => {
          card.addEventListener('click', () => openTournamentScreen(Number(card.dataset.openTournament)));
        });
        commit();
        return;
      }

      if (toScore.length) {
        html += '<div class="section-label" style="margin-top:6px"><svg class="pb-ic"><use href="#pb"/></svg> Played — enter the score</div>';
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
      // Best skill/time fits get pulled out of the nearby list into their own rail.
      const picked = nearbyOneOff
        .filter((g) => gameMatchReasons(g).length)
        .sort((a, b) => gameMatchReasons(b).length - gameMatchReasons(a).length
          || (a.distance_miles ?? 1e9) - (b.distance_miles ?? 1e9))
        .slice(0, 3);
      const pickedIds = new Set(picked.map((g) => g.id));
      const restNearby = nearbyOneOff.filter((g) => !pickedIds.has(g.id));
      if (picked.length) {
        html += '<div class="section-label">⭐ Picked for you</div>';
        html += picked.map((g) => gameCardHtml(g)).join('');
      }
      if (restNearby.length || !picked.length) {
        html += '<div class="section-label">Nearby games</div>';
        html += restNearby.length
          ? restNearby.map((g) => gameCardHtml(g)).join('')
          : '<div class="empty-state" style="padding:18px">No open games around you right now.<br><button class="btn btn-primary" data-goto="new-game" style="margin-top:10px"><svg class="pb-ic"><use href="#pb"/></svg> Start a game</button><br><button class="btn btn-secondary btn-sm" data-invite-share style="margin-top:8px">💌 Invite friends to play</button></div>';
      }
      if (weeklySessions.length) {
        html += '<div class="section-label">🔁 Weekly open play</div>';
        html += weeklySessions.map((g) => gameCardHtml(g)).join('');
      }
      // Capture spontaneous pickup games that never got scheduled here.
      html += '<button class="btn btn-secondary btn-block" id="pl-log-game" style="margin-top:14px">✍️ Log a game you already played</button>';

      if (state.playSeg !== seg) return; // a newer segment render owns the panel
      el.innerHTML = html;
      bindWeekStrip();
      el.querySelector('#pl-log-game')?.addEventListener('click', openLogGameSheet);
      bindGameButtons(el, renderPlay);
      commit();
    } catch (e) {
      if (renderSeq !== state.playRenderSeq || state.playSeg !== seg) return;
      if (hadUsableContent) {
        retainViewAfterError(liveEl, `${e.message} Showing your last update.`, () => renderPlay());
      } else {
        renderError(el, e.message, () => renderPlay());
        commit();
      }
    }
  }

  function updatePlayHeader() {
    const me = state.me;
    if (!me) return;
    const hour = new Date().getHours();
    const hello = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
    const first = String(me.display_name || '').split(/\s+/)[0] || 'player';
    const greeting = $('#play-greeting');
    const context = $('#play-context');
    const avatar = $('#play-avatar-button');
    if (greeting) greeting.textContent = `${hello}, ${first}`;
    if (context) context.textContent = state.areaLabel || me.home_area || me.home_court_name || 'Your game plan';
    if (avatar) avatar.innerHTML = avatarHtml(me, 'sm');
  }

  function setupPlay() {
    $('#play-segments').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      const changed = state.playSeg !== btn.dataset.seg;
      state.playSeg = btn.dataset.seg;
      document.querySelectorAll('#play-segments button').forEach((b) => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-selected', String(active));
      });
      syncPlayFab();
      renderPlay({ reuseFresh: !changed });
    });
    $('#new-game-fab').addEventListener('click', () => {
      if (state.playSeg === 'scores') openNewGameModal(null, 'ranked');
      else if (state.playSeg === 'brackets') openCompetitionCreateSheet();
      else openNewGameModal();
    });
    syncPlayFab();
    $('#play-activity').addEventListener('click', openActivity);
    $('#play-avatar-button').addEventListener('click', () => switchTab('profile'));
  }

  function syncPlayFab() {
    const fab = $('#new-game-fab');
    if (!fab) return;
    const label = state.playSeg === 'scores' ? 'Start a ranked game'
      : state.playSeg === 'brackets' ? 'Create a competition' : 'Start a new game';
    fab.setAttribute('aria-label', label);
    fab.title = label;
  }

  function openCompetitionCreateSheet() {
    const modal = openModal(`
      ${modalHead('Create a competition')}
      <p class="row-sub" style="margin:-4px 0 14px">Choose the format that fits your crew.</p>
      <button type="button" class="card row inbox-row" id="create-tournament-choice">
        <span class="inbox-room-icon">🏆</span>
        <span class="row-main"><span class="row-title" style="display:block">Tournament</span><span class="row-sub">Bracket or round robin · singles or doubles</span></span><span class="chev">›</span>
      </button>
      <button type="button" class="card row inbox-row" id="create-league-choice">
        <span class="inbox-room-icon">📦</span>
        <span class="row-main"><span class="row-title" style="display:block">Box league</span><span class="row-sub">A recurring season with promotion and relegation</span></span><span class="chev">›</span>
      </button>
    `, { label: 'Create a competition' });
    modal.querySelector('#create-tournament-choice').addEventListener('click', () => {
      transitionModal(modal, openCreateTournamentSheet);
    });
    modal.querySelector('#create-league-choice').addEventListener('click', () => {
      transitionModal(modal, openCreateLeagueSheet);
    });
  }

  // Log a spontaneous singles game already played, against a friend.
  async function openLogGameSheet() {
    const modalLoad = beginRoutedOverlayLoad(null);
    let friends = [];
    try { friends = (await api('/friends')).friends || []; } catch { /* offline */ }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;
    if (!friends.length) { toast('Add a friend first to log a game with them'); return; }
    const loc = areaLatLng();
    let nearby = [];
    try { nearby = ((await api(`/courts?lat=${loc.lat}&lng=${loc.lng}&radius=40&limit=8`)).items) || []; } catch { /* ignore */ }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;
    const courtOptions = [];
    const seen = new Set();
    if (state.presence && state.presence.checked_in) { courtOptions.push({ id: state.presence.court_id, name: state.presence.court_name }); seen.add(state.presence.court_id); }
    if (state.me && state.me.home_court_id && !seen.has(state.me.home_court_id)) { courtOptions.push({ id: state.me.home_court_id, name: state.me.home_court_name }); seen.add(state.me.home_court_id); }
    nearby.forEach((c) => { if (!seen.has(c.id) && courtOptions.length < 8) { courtOptions.push({ id: c.id, name: c.name }); seen.add(c.id); } });

    const modal = openModal(`
      ${modalHead('✍️ Log a past game')}
      <p class="row-sub" style="margin-bottom:10px">Record a game you already played. It counts toward stats and court records (casual — no rating change).</p>
      <div class="form-field">
        <div class="segmented" id="lg-mode">
          <button type="button" data-lg-mode="singles" class="active">Singles</button>
          <button type="button" data-lg-mode="doubles">Doubles</button>
        </div>
      </div>
      <div class="form-field" id="lg-partner-wrap" style="display:none">
        <label>Your partner</label>
        <select id="lg-partner">${friends.map((f) => `<option value="${f.id}">${esc(f.display_name)}</option>`).join('')}</select>
      </div>
      <div class="form-field">
        <label id="lg-opp-heading">Opponent</label>
        <select id="lg-opp">${friends.map((f) => `<option value="${f.id}">${esc(f.display_name)}</option>`).join('')}</select>
        <select id="lg-opp2" style="display:none;margin-top:8px">${friends.map((f) => `<option value="${f.id}">${esc(f.display_name)}</option>`).join('')}</select>
      </div>
      <div class="form-field">
        <label>Court</label>
        <select id="lg-court">${courtOptions.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select>
        <input type="search" id="lg-court-search" placeholder="Or search another court…" style="margin-top:8px" />
        <div id="lg-court-results"></div>
      </div>
      <div class="score-grid">
        <div class="score-panel"><div class="score-team-label">You</div>
          <div class="score-stepper"><button type="button" data-lg-step="-1" data-lg-target="lg-s1">−</button><input type="number" id="lg-s1" min="0" max="99" value="11" inputmode="numeric" /><button type="button" data-lg-step="1" data-lg-target="lg-s1">＋</button></div>
        </div>
        <div class="score-vs">vs</div>
        <div class="score-panel"><div class="score-team-label" id="lg-opp-label">Them</div>
          <div class="score-stepper"><button type="button" data-lg-step="-1" data-lg-target="lg-s2">−</button><input type="number" id="lg-s2" min="0" max="99" value="9" inputmode="numeric" /><button type="button" data-lg-step="1" data-lg-target="lg-s2">＋</button></div>
        </div>
      </div>
      <button class="btn btn-primary btn-block" id="lg-submit" style="padding:15px;margin-top:12px">Save result</button>
    `);
    const oppSel = modal.querySelector('#lg-opp');
    let mode = 'singles';
    const syncOppLabel = () => {
      modal.querySelector('#lg-opp-label').textContent = mode === 'doubles' ? 'Opponents' : oppSel.options[oppSel.selectedIndex].text.split(' ')[0];
    };
    oppSel.addEventListener('change', syncOppLabel);
    modal.querySelector('#lg-mode').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      mode = btn.dataset.lgMode;
      modal.querySelectorAll('#lg-mode button').forEach((b) => b.classList.toggle('active', b === btn));
      const doubles = mode === 'doubles';
      modal.querySelector('#lg-partner-wrap').style.display = doubles ? '' : 'none';
      modal.querySelector('#lg-opp2').style.display = doubles ? '' : 'none';
      modal.querySelector('#lg-opp-heading').textContent = doubles ? 'Opponents' : 'Opponent';
      syncOppLabel();
    });
    syncOppLabel();
    let chosenCourtId = courtOptions.length ? courtOptions[0].id : null;
    modal.querySelector('#lg-court').addEventListener('change', (e) => { chosenCourtId = Number(e.target.value); });
    modal.querySelectorAll('[data-lg-step]').forEach((b) => b.addEventListener('click', () => {
      const el = modal.querySelector('#' + b.dataset.lgTarget);
      el.value = Math.max(0, Math.min(99, (Number(el.value) || 0) + Number(b.dataset.lgStep)));
    }));
    let searchTimer;
    modal.querySelector('#lg-court-search').addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      const q = e.target.value.trim();
      if (q.length < 2) { modal.querySelector('#lg-court-results').innerHTML = ''; return; }
      searchTimer = setTimeout(async () => {
        try {
          const data = await api(`/courts?q=${encodeURIComponent(q)}&limit=5`);
          modal.querySelector('#lg-court-results').innerHTML = data.items.map((c) => `
            <div class="card" data-lg-pick="${c.id}" data-lg-name="${esc(c.name)}" style="cursor:pointer;margin:6px 0;padding:10px">
              <div class="row-title" style="font-size:14px">${esc(c.name)}</div><div class="row-sub">${esc(c.city || '')}</div>
            </div>`).join('');
          modal.querySelectorAll('[data-lg-pick]').forEach((row) => row.addEventListener('click', () => {
            chosenCourtId = Number(row.dataset.lgPick);
            const sel = modal.querySelector('#lg-court');
            if (![...sel.options].some((o) => Number(o.value) === chosenCourtId)) {
              sel.insertAdjacentHTML('beforeend', `<option value="${chosenCourtId}">${esc(row.dataset.lgName)}</option>`);
            }
            sel.value = String(chosenCourtId);
            modal.querySelector('#lg-court-search').value = row.dataset.lgName;
            modal.querySelector('#lg-court-results').innerHTML = '';
          }));
        } catch { /* ignore */ }
      }, 300);
    });
    modal.querySelector('#lg-submit').addEventListener('click', async (e) => {
      const btn = e.target;
      const s1 = Number(modal.querySelector('#lg-s1').value);
      const s2 = Number(modal.querySelector('#lg-s2').value);
      if (!chosenCourtId) { toast('Pick a court'); return; }
      if (s1 === s2) { toast('Scores can\'t be tied'); return; }
      const team1 = [state.me.id];
      const team2 = [Number(oppSel.value)];
      if (mode === 'doubles') {
        team1.push(Number(modal.querySelector('#lg-partner').value));
        team2.push(Number(modal.querySelector('#lg-opp2').value));
      }
      const everyone = [...team1, ...team2];
      if (new Set(everyone).size !== everyone.length) { toast('Each player can only be on one team once'); return; }
      btn.disabled = true;
      try {
        await api('/games/log', { method: 'POST', body: JSON.stringify({
          court_id: chosenCourtId,
          team1,
          team2,
          score_team1: s1,
          score_team2: s2,
        }) });
        closeModal(modal);
        toast(s1 > s2 ? 'Logged — nice win! 🏓' : 'Game logged 🏓');
        refreshMe();
        if (state.tab === 'play') renderPlay();
      } catch (err) { toast(err.message); btn.disabled = false; }
    });
  }

  async function openNewGameModal(court, defaultType = 'casual', startNow = false, preferredSlot = null, presetFriendId = null) {
    const plannerTitle = startNow ? 'Play now' : 'Plan a game';
    const plannerShell = openModal(`
      ${modalHead(plannerTitle)}
      <div class="planner-loading">
        <p class="row-sub" style="margin-bottom:12px">Getting your courts and crew ready…</p>
        ${skeletonHtml(3)}
      </div>
    `, { label: plannerTitle });
    const modalLoad = beginRoutedOverlayLoad(null);
    const explicitPlannerIntent = !!court || defaultType !== 'casual' || startNow || !!preferredSlot || presetFriendId != null;
    const savedDraft = readGameDraft();
    const restoredDraft = !explicitPlannerIntent ? savedDraft : null;
    let restoredCourt = null;
    let restoredCourtMissing = false;
    const restoredCourtRequest = restoredDraft && restoredDraft.courtId
      ? api(`/courts/${restoredDraft.courtId}`)
        .then((item) => { restoredCourt = item; })
        .catch(() => { restoredCourtMissing = true; })
      : Promise.resolve();
    // Gather friends (for invites), clubs, and court suggestions in parallel
    let friends = [];
    let suggestions = [];
    let myClubs = [];
    try {
      const reqs = [api('/friends').catch(() => ({ friends: [] }))];
      if (!court) {
        const c = areaLatLng();
        reqs.push(api('/courts/favorites').catch(() => ({ items: [] })));
        reqs.push(api(`/courts?lat=${c.lat}&lng=${c.lng}&radius=30&limit=6`).catch(() => ({ items: [] })));
      }
      reqs.push(api('/clubs/mine').catch(() => ({ items: [] })));
      const res = await Promise.all(reqs);
      friends = res[0].friends || [];
      myClubs = res[res.length - 1].items || [];
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
    await restoredCourtRequest;
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;

    // Smart default: a player already at a court should not have to pick it
    // again. Otherwise use their home court, while keeping Change available.
    if (!court && restoredCourt) {
      court = { id: restoredCourt.id, name: restoredCourt.name, busy_times: restoredCourt.busy_times };
    } else if (!court && !(restoredDraft && restoredDraft.courtId) && state.presence && state.presence.checked_in) {
      court = { id: state.presence.court_id, name: state.presence.court_name };
    } else if (!court && !(restoredDraft && restoredDraft.courtId) && state.me && state.me.home_court_id) {
      court = { id: state.me.home_court_id, name: state.me.home_court_name || 'Home court' };
    }

    // Day & time presets
    const days = [];
    for (let i = 0; i < 7; i++) {
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

    // A shared-availability slot ("sat-am") pre-selects the next matching day
    // in range and a representative hour for that part of day.
    if (preferredSlot) {
      const [slotDay, slotPart] = preferredSlot.split('-');
      const targetDow = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 }[slotDay];
      const dayIdx = days.findIndex((d) => d.getDay() === targetDow);
      if (dayIdx >= 0) {
        selDayIdx = dayIdx;
        selHour = { am: 10, pm: 14, eve: 18 }[slotPart] || selHour;
      }
    }

    const dayChips = days.map((d, i) =>
      `<button type="button" data-day="${i}" aria-pressed="${i === selDayIdx}" class="${i === selDayIdx ? 'active' : ''}">${dayLabel(d, i)}</button>`).join('');
    const timeChips = timePresets.map((h) =>
      `<button type="button" data-hour="${h}" aria-pressed="${h === selHour}" class="${h === selHour ? 'active' : ''}">${timeLabel(h)}</button>`).join('');

    const friendChips = friends.map((f) => `
      <button type="button" class="invite-chip ${Number(presetFriendId) === f.id ? 'active' : ''}" data-fid="${f.id}" aria-pressed="${Number(presetFriendId) === f.id}">
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

    const pickedFriend = friends.find((f) => f.id === Number(presetFriendId));
    const plannerRecoveryHtml = restoredDraft
      ? `<div class="planner-recovery ${restoredDraft.status === 'submitting' ? 'warn' : ''}" role="status">
          <div class="row-main">
            <b>${restoredDraft.status === 'submitting' ? 'Was this game created?' : 'Continuing your saved plan'}</b>
            <div class="row-sub">${restoredDraft.status === 'submitting'
              ? 'We lost the confirmation. Check My games before you try again.'
              : 'Review the time and people, then schedule when ready.'}</div>
          </div>
          ${restoredDraft.status === 'submitting'
            ? '<button type="button" class="btn btn-secondary btn-sm" id="ng-check-games">Check</button><button type="button" class="btn btn-primary btn-sm" id="ng-review-retry">Retry</button>'
            : '<button type="button" class="btn btn-secondary btn-sm" id="ng-start-over">Start over</button>'}
        </div>`
      : (savedDraft && explicitPlannerIntent
        ? `<div class="planner-recovery" role="status">
            <div class="row-main"><b>You have a saved plan</b><div class="row-sub">This new plan will stay separate until you edit it.</div></div>
            <button type="button" class="btn btn-secondary btn-sm" id="ng-resume-draft">Resume</button>
          </div>` : '');
    const modal = plannerShell;
    const plannerBox = modal.querySelector('.modal');
    plannerBox.innerHTML = `
      ${modalHead(plannerTitle)}
      ${plannerRecoveryHtml}

      <section class="planner-step" aria-labelledby="planner-where-title">
        <div class="planner-step-head">
          <span class="planner-step-num">1</span>
          <div><div class="planner-step-title" id="planner-where-title">Where are you playing?</div><div class="planner-step-sub">Current, saved, and nearby courts come first.</div></div>
        </div>
        ${restoredCourtMissing ? '<div class="planner-inline-warning" id="ng-court-warning">Your saved court is not available right now. Pick another court before scheduling.</div>' : ''}
        <div id="ng-court-selected" class="${court ? '' : 'hidden'} court-selected">
          <div class="row-main">
            <div class="row-title" style="font-size:14.5px" id="ng-court-name">${court ? esc(court.name) : ''}</div>
          </div>
          <button type="button" class="btn btn-secondary btn-sm" id="ng-court-change">Change</button>
        </div>
        <div id="ng-court-picker" class="${court ? 'hidden' : ''}">
          <input type="search" id="ng-court-search" aria-label="Search courts" placeholder="Search courts…" />
          <div id="ng-court-results" style="margin-top:8px">${suggestionRows}</div>
        </div>
        <input type="hidden" id="ng-court-id" value="${court ? court.id : ''}" />
      </section>

      <section class="planner-step" aria-labelledby="planner-when-title">
        <div class="planner-step-head">
          <span class="planner-step-num">2</span>
          <div><div class="planner-step-title" id="planner-when-title">When?</div><div class="planner-step-sub">Start a live pickup game or choose a time this week.</div></div>
        </div>
        <div class="segmented" id="ng-mode" role="group" aria-label="Game timing">
          <button type="button" data-mode="now" aria-pressed="${startNow}" ${startNow ? 'class="active"' : ''}>⚡ Right now</button>
          <button type="button" data-mode="later" aria-pressed="${!startNow}" ${startNow ? '' : 'class="active"'}>📅 Plan ahead</button>
        </div>
        <div id="ng-later-fields" class="${startNow ? 'hidden' : ''}">
          <div class="quick-times" id="ng-days" style="margin-bottom:8px">${dayChips}</div>
          <div class="quick-times" id="ng-hours" style="margin-bottom:8px">${timeChips}
            <button type="button" data-hour="custom">Custom…</button>
          </div>
          <input type="datetime-local" id="ng-when" aria-label="Custom game date and time" class="hidden" style="margin-bottom:12px" />
          <div id="ng-busy-hint" class="row-sub" style="margin-bottom:4px"></div>
        </div>
      </section>

      <section class="planner-step" aria-labelledby="planner-who-title">
        <div class="planner-step-head">
          <span class="planner-step-num">3</span>
          <div><div class="planner-step-title" id="planner-who-title">Who should see it?</div><div class="planner-step-sub">Keep it open, share with friends, or invite your crew.</div></div>
        </div>
        <div class="type-cards vis-cards" id="ng-vis" role="group" aria-label="Who can join">
          <button type="button" data-vis="open" aria-pressed="${!pickedFriend}" class="${pickedFriend ? '' : 'active'}"><span style="font-size:19px">🌍</span><b>Anyone</b><small>Nearby players</small></button>
          <button type="button" data-vis="friends" aria-pressed="false"><span style="font-size:19px">🤝</span><b>Friends</b><small>All your friends</small></button>
          <button type="button" data-vis="private" aria-pressed="${!!pickedFriend}" class="${pickedFriend ? 'active' : ''}"><span style="font-size:19px">🔒</span><b>Specific</b><small>Only who you pick</small></button>
        </div>
        <div id="ng-friends-wrap" class="${pickedFriend ? '' : 'hidden'}" style="margin-top:10px">
          ${friends.length
            ? `<div class="invite-chips" id="ng-invites">${friendChips}</div>
               <p class="row-sub" id="ng-invite-hint" style="margin-top:6px">${pickedFriend ? `${esc(pickedFriend.display_name.split(' ')[0])} is invited — only selected players will see this game.` : 'Pick who to invite — only they will see this game.'}</p>`
            : '<p class="row-sub">Add friends first to invite specific people.</p>'}
        </div>
      </section>

      <details class="planner-advanced" id="ng-advanced">
        <summary><span>Game options</span><span class="planner-advanced-copy" id="ng-options-summary">${defaultType === 'ranked' ? 'Ranked' : 'Casual'} · Doubles · Any level</span></summary>
        <div class="planner-advanced-body">
          <div class="form-grid">
            <div class="form-field">
              <label>Type</label>
              <div class="type-cards" id="ng-type">
                <button type="button" data-val="casual" aria-pressed="${defaultType === 'casual'}" class="${defaultType === 'casual' ? 'active' : ''}">
                  <span style="font-size:20px"><svg class="pb-ic"><use href="#pb"/></svg></span><b>Casual</b><small>Just for fun</small>
                </button>
                <button type="button" data-val="ranked" aria-pressed="${defaultType === 'ranked'}" class="${defaultType === 'ranked' ? 'active' : ''}">
                  <span style="font-size:20px">🏆</span><b>Ranked</b><small>Counts for rating</small>
                </button>
              </div>
            </div>
            <div class="form-field">
              <label for="ng-max">Players needed</label>
              <select id="ng-max">
                <option value="2">2 (singles)</option>
                <option value="4" selected>4 (doubles)</option>
                <option value="6">6</option>
                <option value="8">8</option>
              </select>
            </div>
          </div>

          <div class="form-field">
            <label>Level <span class="row-sub">(a hint, not a gate)</span></label>
            <div class="quick-times" id="ng-level" style="margin-top:2px">
              <button type="button" data-level="any" class="active" aria-pressed="true">Anyone</button>
              <button type="button" data-level="beginner" aria-pressed="false">Beginner</button>
              <button type="button" data-level="intermediate" aria-pressed="false">Intermediate</button>
              <button type="button" data-level="advanced" aria-pressed="false">Advanced</button>
              <button type="button" data-level="pro" aria-pressed="false">Pro</button>
            </div>
          </div>

          ${myClubs.length ? `
          <div class="form-field">
            <label>Host under a club banner?</label>
            <div class="quick-times" id="ng-club">
              <button type="button" data-club-id="" class="active">Just me</button>
              ${myClubs.map((cl) => `<button type="button" data-club-id="${cl.id}">🏛 ${esc(cl.name)}</button>`).join('')}
            </div>
            <div class="row-sub" id="ng-club-hint" style="margin-top:6px"></div>
          </div>` : ''}

          <label class="row" id="ng-recurring-row" style="margin-bottom:14px;cursor:pointer;gap:10px">
            <input type="checkbox" id="ng-recurring" style="width:22px;height:22px;flex:0 0 auto" />
            <span><span style="font-weight:700">🔁 Repeats weekly</span><br><span class="row-sub">Open-play session — players re-RSVP each week</span></span>
          </label>

          <div class="form-field">
            <label for="ng-notes">Note <span class="row-sub">(optional)</span></label>
            <input type="text" id="ng-notes" maxlength="200" placeholder="e.g. All levels welcome!" />
          </div>
        </div>
      </details>

      <div class="planner-submit-bar">
        <div class="form-error hidden" id="ng-submit-error" role="alert" tabindex="-1"></div>
        <div class="planner-summary" id="ng-summary">${court ? esc(court.name) : 'Choose a court'} · ${startNow ? 'Right now' : `${dayLabel(days[selDayIdx], selDayIdx)} at ${timeLabel(selHour)}`}</div>
        <button class="btn btn-primary btn-block" id="ng-submit" style="padding:15px">
          ${startNow ? 'Start game now' : 'Schedule game'}
        </button>
      </div>
    `;
    setDialogLabel(plannerBox, plannerTitle);

    let plannerDirty = false;
    let plannerSubmitted = false;
    let plannerSubmitting = !!(restoredDraft && restoredDraft.status === 'submitting');
    let plannerSubmitStartedAt = plannerSubmitting ? restoredDraft.submitStartedAt : null;
    const plannerAttemptId = (restoredDraft && restoredDraft.clientAttemptId) || newGameAttemptId();
    let plannerSaveTimer = null;
    let ambiguousDraftAccepted = !(restoredDraft && restoredDraft.status === 'submitting');
    const plannerScheduledIso = () => {
      if (nowMode) return null;
      let value;
      if (customMode) {
        const raw = modal.querySelector('#ng-when').value;
        value = raw ? new Date(raw) : null;
      } else {
        value = new Date(days[selDayIdx]);
        value.setHours(selHour ?? 18, 0, 0, 0);
      }
      return value && Number.isFinite(value.getTime()) ? value.toISOString() : null;
    };
    const plannerSnapshot = (status = 'editing') => ({
      status,
      submitStartedAt: status === 'submitting' ? (plannerSubmitStartedAt || Date.now()) : null,
      clientAttemptId: plannerAttemptId,
      mode: nowMode ? 'now' : 'later',
      courtId: Number(modal.querySelector('#ng-court-id').value) || null,
      scheduledAt: plannerScheduledIso(),
      timeKind: customMode ? 'custom' : 'preset',
      visibility,
      inviteUserIds: [...inviteIds],
      gameType,
      maxPlayers: Number(modal.querySelector('#ng-max').value),
      preferredLevel,
      clubId,
      recurrence: recurringBox.checked ? 'weekly' : 'none',
      notes: modal.querySelector('#ng-notes').value.trim(),
      advancedOpen: modal.querySelector('#ng-advanced').open,
    });
    const flushPlannerDraft = (status = 'editing') => {
      clearTimeout(plannerSaveTimer);
      plannerSaveTimer = null;
      writeGameDraft(plannerSnapshot(status));
    };
    const markPlannerDirty = () => {
      modal.querySelector('#ng-resume-draft')?.closest('.planner-recovery')?.remove();
      plannerDirty = true;
      clearTimeout(plannerSaveTimer);
      plannerSaveTimer = setTimeout(() => flushPlannerDraft(plannerSubmitting ? 'submitting' : 'editing'), 320);
    };
    const onPlannerPageHide = () => {
      if ((plannerDirty || plannerSubmitting) && !plannerSubmitted) {
        flushPlannerDraft(plannerSubmitting ? 'submitting' : 'editing');
      }
    };
    window.addEventListener('pagehide', onPlannerPageHide);
    modal._cleanupFns.push(() => {
      clearTimeout(plannerSaveTimer);
      window.removeEventListener('pagehide', onPlannerPageHide);
      if ((plannerDirty || plannerSubmitting) && !plannerSubmitted) {
        flushPlannerDraft(plannerSubmitting ? 'submitting' : 'editing');
        toast(plannerSubmitting
          ? 'Couldn’t confirm the game — check My games before retrying'
          : 'Plan saved — finish it anytime');
      }
    });

    // --- Busy-time hint: nudge scheduling toward when players actually show up ---
    let busyTimes = null; // for the currently selected court
    const partOfHour = (h) => (h >= 5 && h < 12 ? 'mornings' : h < 17 ? 'afternoons' : h < 23 ? 'evenings' : null);
    const updateBusyHint = () => {
      const el = modal.querySelector('#ng-busy-hint');
      if (!busyTimes || !busyTimes.length || nowMode) { el.innerHTML = ''; return; }
      let when;
      if (customMode) {
        const raw = modal.querySelector('#ng-when').value;
        when = raw ? new Date(raw) : null;
      } else {
        when = new Date(days[selDayIdx]);
        when.setHours(selHour ?? 18);
      }
      const labels = busyTimes.map((b) => b.label);
      const slot = when
        ? `${['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][when.getDay()]} ${partOfHour(when.getHours())}`
        : null;
      el.innerHTML = slot && labels.includes(slot)
        ? `👍 Good pick — ${esc(slot)} are popular at this court`
        : `📊 Popular here: ${labels.map(esc).join(' · ')}`;
    };
    const loadBusyHint = async (courtId) => {
      busyTimes = null;
      updateBusyHint();
      if (!courtId) return;
      try {
        busyTimes = (await api(`/courts/${courtId}`)).busy_times || null;
        updateBusyHint();
      } catch { /* hint is optional */ }
    };

    const updatePlannerSummary = () => {
      const summary = modal.querySelector('#ng-summary');
      if (!summary) return;
      const courtName = modal.querySelector('#ng-court-name').textContent || 'Choose a court';
      let whenText = 'Right now';
      if (!nowMode) {
        if (customMode) {
          const raw = modal.querySelector('#ng-when').value;
          const parsed = raw ? new Date(raw) : null;
          whenText = parsed && Number.isFinite(parsed.getTime()) ? fmtDateTime(parsed.toISOString()) : 'Choose a time';
        } else {
          whenText = `${dayLabel(days[selDayIdx], selDayIdx)} at ${timeLabel(selHour)}`;
        }
      }
      summary.textContent = `${courtName} · ${whenText}`;
    };

    // --- Court picking ---
    const setCourt = (id, name, { dirty = true } = {}) => {
      modal.querySelector('#ng-court-id').value = id || '';
      modal.querySelector('#ng-court-name').textContent = name || '';
      modal.querySelector('#ng-court-selected').classList.toggle('hidden', !id);
      modal.querySelector('#ng-court-picker').classList.toggle('hidden', !!id);
      modal.querySelector('#ng-court-warning')?.remove();
      loadBusyHint(id);
      updatePlannerSummary();
      if (dirty) markPlannerDirty();
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
    updatePlannerSummary();
    modal.querySelector('#ng-mode').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      nowMode = btn.dataset.mode === 'now';
      modal.querySelectorAll('#ng-mode button').forEach((b) => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-pressed', String(active));
      });
      modal.querySelector('#ng-later-fields').classList.toggle('hidden', nowMode);
      modal.querySelector('#ng-submit').textContent = nowMode ? 'Start game now' : 'Schedule game';
      if (nowMode) modal.querySelector('#ng-time-warning')?.remove();
      updateBusyHint();
      updatePlannerSummary();
      markPlannerDirty();
    });
    modal.querySelector('#ng-days').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      selDayIdx = Number(btn.dataset.day);
      modal.querySelectorAll('#ng-days button').forEach((b) => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-pressed', String(active));
      });
      modal.querySelector('#ng-time-warning')?.remove();
      updateBusyHint();
      updatePlannerSummary();
      markPlannerDirty();
    });
    modal.querySelector('#ng-hours').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      modal.querySelectorAll('#ng-hours button').forEach((b) => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-pressed', String(active));
      });
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
      modal.querySelector('#ng-time-warning')?.remove();
      updateBusyHint();
      updatePlannerSummary();
      markPlannerDirty();
    });
    modal.querySelector('#ng-when').addEventListener('input', () => {
      const value = new Date(modal.querySelector('#ng-when').value);
      if (Number.isFinite(value.getTime()) && value.getTime() > Date.now()) modal.querySelector('#ng-time-warning')?.remove();
      updateBusyHint(); updatePlannerSummary(); markPlannerDirty();
    });
    // Initial hint for a preselected court (after nowMode/customMode exist).
    if (court) {
      if (court.busy_times) { busyTimes = court.busy_times; updateBusyHint(); }
      else loadBusyHint(court.id);
    }

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
      modal.querySelectorAll('#ng-type button').forEach((b) => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-pressed', String(active));
      });
      syncRecurring();
      updateOptionsSummary();
      markPlannerDirty();
    });

    // --- Preferred level ---
    let preferredLevel = 'any';
    modal.querySelector('#ng-level').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      preferredLevel = btn.dataset.level;
      modal.querySelectorAll('#ng-level button').forEach((b) => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-pressed', String(active));
      });
      updateOptionsSummary();
      markPlannerDirty();
    });
    const updateOptionsSummary = () => {
      const players = Number(modal.querySelector('#ng-max').value);
      const size = players === 2 ? 'Singles' : players === 4 ? 'Doubles' : `${players} players`;
      const level = preferredLevel === 'any' ? 'Any level' : skillLabel(preferredLevel);
      modal.querySelector('#ng-options-summary').textContent = `${gameType === 'ranked' ? 'Ranked' : 'Casual'} · ${size} · ${level}`;
    };
    modal.querySelector('#ng-max').addEventListener('change', () => { updateOptionsSummary(); markPlannerDirty(); });
    updateOptionsSummary();

    // --- Club banner ---
    let clubId = null;
    const clubHintEl = modal.querySelector('#ng-club-hint');
    modal.querySelector('#ng-club')?.addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      clubId = Number(btn.dataset.clubId) || null;
      modal.querySelectorAll('#ng-club button').forEach((b) => b.classList.toggle('active', b === btn));
      const picked = myClubs.find((cl) => cl.id === clubId);
      clubHintEl.textContent = picked
        ? (picked.member_count > 1
            ? `📣 The other ${picked.member_count - 1} member${picked.member_count === 2 ? '' : 's'} of ${picked.name} will be pinged.`
            : `📣 Hosted under the ${picked.name} banner.`)
        : '';
      markPlannerDirty();
    });

    // --- Visibility / invites ---
    let visibility = pickedFriend ? 'private' : 'open';
    const inviteIds = new Set(pickedFriend ? [pickedFriend.id] : []);
    const friendsWrap = modal.querySelector('#ng-friends-wrap');
    modal.querySelector('#ng-vis').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      visibility = btn.dataset.vis;
      modal.querySelectorAll('#ng-vis button').forEach((b) => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-pressed', String(active));
      });
      friendsWrap.classList.toggle('hidden', visibility !== 'private');
      markPlannerDirty();
    });
    const invitesEl = modal.querySelector('#ng-invites');
    if (invitesEl) {
      invitesEl.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-fid]');
        if (!btn) return;
        const fid = Number(btn.dataset.fid);
        if (inviteIds.has(fid)) inviteIds.delete(fid); else inviteIds.add(fid);
        btn.classList.toggle('active', inviteIds.has(fid));
        btn.setAttribute('aria-pressed', String(inviteIds.has(fid)));
        modal.querySelector('#ng-invite-hint').textContent = inviteIds.size
          ? `${inviteIds.size} invited — only they will see this game.`
          : 'Pick who to invite — only they will see this game.';
        markPlannerDirty();
      });
    }

    modal.querySelector('#ng-recurring').addEventListener('change', markPlannerDirty);
    modal.querySelector('#ng-notes').addEventListener('input', markPlannerDirty);

    const setPlannerWarning = (id, message, anchor) => {
      modal.querySelector(`#${id}`)?.remove();
      const warning = document.createElement('div');
      warning.id = id;
      warning.className = 'planner-inline-warning';
      warning.textContent = message;
      (anchor || modal.querySelector('#ng-later-fields')).appendChild(warning);
    };

    // Restore only after every closure variable and event handler exists, so
    // the UI and the eventual POST payload are guaranteed to agree.
    if (restoredDraft) {
      if (restoredCourt) setCourt(restoredCourt.id, restoredCourt.name, { dirty: false });
      nowMode = restoredDraft.mode === 'now';
      modal.querySelectorAll('#ng-mode button').forEach((btn) => {
        const active = btn.dataset.mode === (nowMode ? 'now' : 'later');
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', String(active));
      });
      modal.querySelector('#ng-later-fields').classList.toggle('hidden', nowMode);
      modal.querySelector('#ng-submit').textContent = nowMode ? 'Start game now' : 'Schedule game';

      if (!nowMode) {
        const restoredTime = restoredDraft.scheduledAt ? new Date(restoredDraft.scheduledAt) : null;
        const validTime = restoredTime && Number.isFinite(restoredTime.getTime()) && restoredTime.getTime() > Date.now();
        if (validTime) {
          const matchingDay = days.findIndex((day) => day.toDateString() === restoredTime.toDateString());
          const isPreset = restoredDraft.timeKind === 'preset' && matchingDay >= 0
            && restoredTime.getMinutes() === 0 && timePresets.includes(restoredTime.getHours());
          if (isPreset) {
            customMode = false;
            selDayIdx = matchingDay;
            selHour = restoredTime.getHours();
          } else {
            customMode = true;
            const pad2 = (n) => String(n).padStart(2, '0');
            modal.querySelector('#ng-when').value = `${restoredTime.getFullYear()}-${pad2(restoredTime.getMonth() + 1)}-${pad2(restoredTime.getDate())}T${pad2(restoredTime.getHours())}:${pad2(restoredTime.getMinutes())}`;
          }
        } else {
          customMode = true;
          selHour = null;
          setPlannerWarning('ng-time-warning', 'Your saved time has passed. Choose a new time.');
        }
        modal.querySelector('#ng-when').classList.toggle('hidden', !customMode);
        modal.querySelectorAll('#ng-days button').forEach((btn) => {
          const active = !customMode && Number(btn.dataset.day) === selDayIdx;
          btn.classList.toggle('active', active);
          btn.setAttribute('aria-pressed', String(active));
        });
        modal.querySelectorAll('#ng-hours button').forEach((btn) => {
          const active = customMode ? btn.dataset.hour === 'custom' : Number(btn.dataset.hour) === selHour;
          btn.classList.toggle('active', active);
          btn.setAttribute('aria-pressed', String(active));
        });
      }

      gameType = restoredDraft.gameType;
      modal.querySelectorAll('#ng-type button').forEach((btn) => {
        const active = btn.dataset.val === gameType;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', String(active));
      });
      preferredLevel = restoredDraft.preferredLevel;
      modal.querySelectorAll('#ng-level button').forEach((btn) => {
        const active = btn.dataset.level === preferredLevel;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', String(active));
      });
      const restoredMax = gameType === 'ranked' && ![2, 4].includes(restoredDraft.maxPlayers)
        ? 4 : restoredDraft.maxPlayers;
      modal.querySelector('#ng-max').value = String(restoredMax);

      visibility = restoredDraft.visibility;
      modal.querySelectorAll('#ng-vis button').forEach((btn) => {
        const active = btn.dataset.vis === visibility;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', String(active));
      });
      friendsWrap.classList.toggle('hidden', visibility !== 'private');
      const currentFriendIds = new Set(friends.map((friend) => friend.id));
      inviteIds.clear();
      restoredDraft.inviteUserIds.filter((id) => currentFriendIds.has(id)).forEach((id) => inviteIds.add(id));
      invitesEl?.querySelectorAll('[data-fid]').forEach((btn) => {
        const active = inviteIds.has(Number(btn.dataset.fid));
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', String(active));
      });
      if (visibility === 'private' && restoredDraft.inviteUserIds.length !== inviteIds.size) {
        setPlannerWarning('ng-invite-warning', 'Some saved invitees are no longer available. Review who can see this game.', friendsWrap);
      }
      if (modal.querySelector('#ng-invite-hint')) {
        modal.querySelector('#ng-invite-hint').textContent = inviteIds.size
          ? `${inviteIds.size} invited — only they will see this game.`
          : 'Pick who to invite — only they will see this game.';
      }

      const restoredClub = myClubs.find((item) => item.id === restoredDraft.clubId);
      clubId = restoredClub && visibility !== 'private' ? restoredClub.id : null;
      modal.querySelectorAll('#ng-club button').forEach((btn) => {
        btn.classList.toggle('active', (Number(btn.dataset.clubId) || null) === clubId);
      });
      if (restoredDraft.clubId && !clubId) {
        setPlannerWarning('ng-club-warning', visibility === 'private'
          ? 'This private game will be hosted by you, not a club.'
          : 'That club is no longer available, so this game will be hosted by you.', modal.querySelector('#ng-club')?.parentElement);
      }
      recurringBox.checked = gameType !== 'ranked' && restoredDraft.recurrence === 'weekly';
      modal.querySelector('#ng-notes').value = restoredDraft.notes;
      modal.querySelector('#ng-advanced').open = restoredDraft.advancedOpen;
      syncRecurring();
      updateOptionsSummary();
      updateBusyHint();
      updatePlannerSummary();
    }

    let ignoreRestoredAdvancedToggle = !!(restoredDraft && restoredDraft.advancedOpen);
    modal.querySelector('#ng-advanced').addEventListener('toggle', () => {
      if (ignoreRestoredAdvancedToggle) { ignoreRestoredAdvancedToggle = false; return; }
      markPlannerDirty();
    });

    modal.querySelector('#ng-start-over')?.addEventListener('click', () => {
      clearGameDraft();
      plannerSubmitting = false;
      plannerSubmitted = true;
      transitionModal(modal, () => openNewGameModal());
      toast('Saved plan cleared');
    });
    modal.querySelector('#ng-resume-draft')?.addEventListener('click', () => {
      transitionModal(modal, () => openNewGameModal());
    });
    modal.querySelector('#ng-check-games')?.addEventListener('click', () => {
      closeModal(modal);
      state.playSeg = 'games';
      switchTab('play');
    });
    modal.querySelector('#ng-review-retry')?.addEventListener('click', () => {
      ambiguousDraftAccepted = true;
      plannerSubmitting = false;
      plannerSubmitStartedAt = null;
      modal.querySelector('#ng-submit').disabled = false;
      modal.querySelector('#ng-review-retry')?.closest('.planner-recovery')?.remove();
      writeGameDraft(plannerSnapshot('editing'));
      toast('Review the details, then schedule when ready');
    });
    if (!ambiguousDraftAccepted) modal.querySelector('#ng-submit').disabled = true;

    const showPlannerSubmitError = (message, target) => {
      const error = modal.querySelector('#ng-submit-error');
      error.textContent = message;
      error.classList.remove('hidden');
      modal.querySelectorAll('[aria-invalid="true"]').forEach((node) => node.removeAttribute('aria-invalid'));
      if (target) {
        target.setAttribute('aria-invalid', 'true');
        target.scrollIntoView({ block: 'center', behavior: 'auto' });
        target.focus({ preventScroll: true });
      } else {
        error.focus();
      }
    };
    const clearPlannerSubmitError = () => {
      modal.querySelector('#ng-submit-error').classList.add('hidden');
      modal.querySelectorAll('[aria-invalid="true"]').forEach((node) => node.removeAttribute('aria-invalid'));
    };

    // --- Submit ---
    modal.querySelector('#ng-submit').addEventListener('click', async (e) => {
      clearPlannerSubmitError();
      if (!ambiguousDraftAccepted) {
        showPlannerSubmitError('Check My games before retrying this plan.');
        return;
      }
      const courtId = modal.querySelector('#ng-court-id').value;
      if (!courtId) { showPlannerSubmitError('Pick a court first.', modal.querySelector('#ng-court-search')); return; }
      let scheduledAt;
      if (nowMode) {
        scheduledAt = new Date();
      } else if (customMode) {
        const v = modal.querySelector('#ng-when').value;
        if (!v) { showPlannerSubmitError('Pick a date and time.', modal.querySelector('#ng-when')); return; }
        scheduledAt = new Date(v);
        if (!Number.isFinite(scheduledAt.getTime())) { showPlannerSubmitError('Choose a valid date and time.', modal.querySelector('#ng-when')); return; }
      } else {
        if (selHour == null) { showPlannerSubmitError('Pick a time.', modal.querySelector('#ng-hours button')); return; }
        scheduledAt = new Date(days[selDayIdx]);
        scheduledAt.setHours(selHour, 0, 0, 0);
      }
      if (!nowMode && scheduledAt.getTime() <= Date.now()) {
        setPlannerWarning('ng-time-warning', 'That time has passed. Choose a future time.');
        showPlannerSubmitError('Choose a future time.', customMode ? modal.querySelector('#ng-when') : modal.querySelector('#ng-hours button.active'));
        return;
      }
      if (visibility === 'private' && inviteIds.size === 0) {
        showPlannerSubmitError('Pick at least one person to invite.', modal.querySelector('#ng-invites button'));
        return;
      }
      if (clubId && visibility === 'private') {
        showPlannerSubmitError("Club games can't be invite-only — the club needs to see it.", modal.querySelector('#ng-vis button[data-vis="friends"]'));
        return;
      }
      const btn = e.currentTarget;
      if (btn.disabled) return;
      btn.disabled = true;
      btn.textContent = nowMode ? 'Starting game…' : 'Scheduling…';
      plannerSubmitting = true;
      plannerSubmitStartedAt = Date.now();
      modal.classList.add('planner-submitting');
      modal.querySelector('.modal')?.setAttribute('aria-busy', 'true');
      modal.querySelectorAll('.planner-step, .planner-advanced, .planner-submit-bar')
        .forEach((section) => section.setAttribute('inert', ''));
      flushPlannerDraft('submitting');
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
            preferred_level: preferredLevel,
            notes: modal.querySelector('#ng-notes').value.trim(),
            invite_user_ids: visibility === 'private' ? [...inviteIds] : [],
            club_id: clubId,
            client_attempt_id: plannerAttemptId,
          }),
        });
        plannerSubmitting = false;
        plannerSubmitted = true;
        clearGameDraft();
        closeModal(modal);
        toast(nowMode ? "Game on! It's live in My games 🏓" : 'Game scheduled! 🏓');
        if (state.tab === 'play') {
          state.playSeg = 'games';
          syncPlayFab();
          renderPlay();
        }
        document.querySelectorAll('#play-segments button').forEach((b) => {
          const active = b.dataset.seg === state.playSeg;
          b.classList.toggle('active', active);
          b.setAttribute('aria-selected', String(active));
        });
        refreshMe();
      } catch (err) {
        plannerDirty = true;
        // A timeout or interrupted response may still have created the game.
        // Preserve the guard instead of enabling a duplicate one-tap retry.
        flushPlannerDraft('submitting');
        closeModal(modal);
        toast('Couldn’t confirm the game — check My games before retrying');
      }
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
          <h3>${game.game_type === 'ranked' ? '🏆 Record ranked score' : '<svg class="pb-ic"><use href="#pb"/></svg> Record score'}</h3>
          <div class="row-sub">${esc(court.name || '')}</div>
        </div>
        <button class="modal-close" aria-label="Close">✕</button>
      </div>
      ${singles ? '' : `<p class="row-sub" style="margin-bottom:8px">Tap a player to switch their team.${players.length >= 4 ? ' <button type="button" id="sc-balance" class="tag" style="cursor:pointer;border:1px dashed var(--line);background:transparent">⚖️ Balance by rating</button>' : ''}</p>`}
      <div id="sc-chips" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:6px"></div>
      <div id="sc-uneven" class="row-sub" style="color:var(--amber-700);font-weight:700;margin-bottom:10px;display:none"></div>
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

    // Fairest split: the half-size subset whose total rating is closest to
    // the other half's (n ≤ 12 → at most C(12,6)=924 combos, instant).
    modal.querySelector('#sc-balance')?.addEventListener('click', () => {
      const ids = players.map((p) => p.user_id);
      const rating = Object.fromEntries(players.map((p) => [p.user_id, p.rating || 1200]));
      const size = Math.floor(ids.length / 2);
      const total = ids.reduce((sum, id) => sum + rating[id], 0);
      let best = null;
      let bestDiff = Infinity;
      const choose = (start, picked, sum) => {
        if (picked.length === size) {
          const diff = Math.abs(total - 2 * sum);
          if (diff < bestDiff) { bestDiff = diff; best = [...picked]; }
          return;
        }
        for (let i = start; i <= ids.length - (size - picked.length); i++) {
          choose(i + 1, [...picked, ids[i]], sum + rating[ids[i]]);
        }
      };
      choose(0, [], 0);
      const teamOne = new Set(best);
      ids.forEach((id) => { teams[id] = teamOne.has(id) ? 1 : 2; });
      renderChips();
      renderLabels();
      toast(`⚖️ Balanced — teams ${bestDiff} rating point${bestDiff === 1 ? '' : 's'} apart`);
    });

    // If someone else reports a score (or the game changes) while this is open,
    // swap to the game screen instead of letting a stale submission overwrite it.
    const originalStatus = game.status;
    const scorePoll = setInterval(async () => {
      if (!document.body.contains(modal)) { clearInterval(scorePoll); return; }
      if (document.hidden || state.connectionState === 'offline') return;
      try {
        const fresh = await api(`/games/${game.id}`);
        const someoneElseReported = fresh.score_submitted_by && fresh.score_submitted_by !== state.me.id
          && fresh.status === 'awaiting_confirmation' && originalStatus !== 'awaiting_confirmation';
        if (fresh.status !== originalStatus || someoneElseReported) {
          clearInterval(scorePoll);
          toast(`⚡ ${fresh.score_submitted_by_name || 'Your opponent'} already reported a score`);
          refreshMe();
          transitionModal(modal, () => openGameScreen(game.id));
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
        if (updated.status === 'awaiting_confirmation') {
          closeModal(modal);
          toast('Score sent — waiting for your opponent to confirm ✅');
        } else {
          transitionModal(modal, () => showCelebration(updated));
        }
        refreshMe();
        refresh();
      } catch (err) { toast(err.message); btn.disabled = false; }
    });
  }

  // ---------- Tournaments ----------
  const T_FORMAT_LABEL = { single_elim: 'Single elimination', round_robin: 'Round robin' };

  function tournamentStatusChip(t) {
    if (t.status === 'registration') {
      const spots = t.max_entries - t.entry_count;
      return `<span class="tag" style="background:var(--green-100);color:var(--green-ink)">Registration open · ${spots} spot${spots === 1 ? '' : 's'} left</span>`;
    }
    if (t.status === 'active') return '<span class="tag" style="background:var(--amber-50);color:var(--amber-800)">⚡ In progress</span>';
    if (t.status === 'completed') return `<span class="tag" style="background:var(--violet-50);color:var(--violet-700)">🏆 ${t.champion ? esc(t.champion.name) : 'Finished'}</span>`;
    return '<span class="tag">Cancelled</span>';
  }

  function tournamentCardHtml(t) {
    const meta = [
      t.court ? `${t.court.name}${t.court.city ? ', ' + t.court.city : ''}` : '',
      // A finished tournament's card shouldn't advertise its (possibly still
      // future) start time as if it were upcoming.
      t.status === 'completed'
        ? `🏁 Ended${t.completed_at ? ` ${new Date(t.completed_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}` : ''}`
        : fmtDateTime(t.starts_at),
    ].filter(Boolean).join(' · ');
    return `
      <div class="card" data-open-tournament="${t.id}" style="cursor:pointer;padding:14px 16px">
        <div class="row" style="padding:0;gap:10px;align-items:flex-start">
          <span style="font-size:26px">🏆</span>
          <div class="row-main">
            <div class="row-title">${esc(t.name)}</div>
            <div class="row-sub">${esc(meta)}</div>
            <div class="row-sub" style="margin-top:2px">${T_FORMAT_LABEL[t.format] || t.format} · ${t.event_type === 'doubles' ? 'Doubles' : 'Singles'}${t.ranked ? ' · ⚡ Ranked' : ''} · ${t.entry_count}/${t.max_entries} ${t.event_type === 'doubles' ? 'teams' : 'players'}</div>
            <div style="margin-top:6px">${tournamentStatusChip(t)}${t.club_name ? ` <span class="tag">🏛 ${esc(t.club_name)}</span>` : ''}${t.my_entry_id ? ' <span class="tag" style="background:var(--green-50);color:var(--green-accent)">✓ You\'re in</span>' : ''}${t.is_organizer ? ' <span class="tag">Organizer</span>' : ''}</div>
          </div>
          <span class="chev">›</span>
        </div>
      </div>`;
  }

  async function renderTournaments(el, retryFn) {
    const loc = areaLatLng();
    try {
      const [mine, nearby, leagues] = await Promise.all([
        api('/tournaments?mine=1'),
        api(`/tournaments?lat=${loc.lat}&lng=${loc.lng}&radius=60`).catch(() => ({ items: [] })),
        api('/leagues').catch(() => ({ items: [] })),
      ]);
      const mineIds = new Set(mine.items.map((t) => t.id));
      const nearbyOnly = (nearby.items || []).filter((t) => !mineIds.has(t.id));
      const live = mine.items.filter((t) => t.status !== 'completed');
      const past = mine.items.filter((t) => t.status === 'completed');

      let html = '<button class="btn btn-primary btn-block" id="tour-create" style="margin:2px 0 14px">🏆 Create a tournament</button>';
      if (live.length) {
        html += '<div class="section-label">Your tournaments</div>';
        html += live.map(tournamentCardHtml).join('');
      }
      if (nearbyOnly.length) {
        html += '<div class="section-label">Nearby tournaments</div>';
        html += nearbyOnly.map(tournamentCardHtml).join('');
      }
      if (past.length) {
        html += '<div class="section-label">Past tournaments</div>';
        html += past.map(tournamentCardHtml).join('');
      }
      if (!live.length && !nearbyOnly.length && !past.length) {
        html += '<div class="empty-state"><span class="big">🏆</span>No tournaments around yet.<br>Set one up and crown a champion!</div>';
      }

      // Box leagues: season-long ladders alongside one-day brackets.
      html += '<div class="section-label">📦 Box leagues</div>';
      html += (leagues.items || []).map((lg) => `
        <div class="card row" data-open-league="${lg.id}" style="cursor:pointer;padding:12px 14px">
          <span style="font-size:22px">📦</span>
          <div class="row-main">
            <div class="row-title" style="font-size:14.5px">${esc(lg.name)}</div>
            <div class="row-sub">${lg.court ? esc(lg.court.name) + ' · ' : ''}${lg.status === 'registration' ? `Signups open · ${lg.member_count}/${lg.max_players}` : lg.status === 'completed' ? `🏁 Season complete${lg.champion_name ? ` · 👑 ${esc(lg.champion_name)}` : ''}` : `Round ${lg.current_round} · ${lg.member_count} players`}${lg.joined && lg.status === 'active' ? (lg.my_box ? ` · 📦 your box: ${lg.my_box}` : ' · ✓ in') : ''}</div>
          </div>
          <span class="chev">›</span>
        </div>`).join('');
      html += '<button class="btn btn-secondary btn-block btn-sm" id="league-create" style="margin-top:4px">📦 Start a box league</button>';

      if (state.playSeg !== 'brackets') return; // user already switched away
      el.innerHTML = html;
      el.querySelector('#tour-create').addEventListener('click', openCreateTournamentSheet);
      el.querySelector('#league-create').addEventListener('click', openCreateLeagueSheet);
      el.querySelectorAll('[data-open-league]').forEach((card) => {
        card.addEventListener('click', () => openLeagueScreen(Number(card.dataset.openLeague)));
      });
      el.querySelectorAll('[data-open-tournament]').forEach((card) => {
        card.addEventListener('click', () => openTournamentScreen(Number(card.dataset.openTournament)));
      });
    } catch (e) {
      renderError(el, e.message, retryFn || (() => renderTournaments(el)));
    }
  }

  // ---------- Shared competition results ----------
  const COMPETITION_RESULT_STATES = {
    unreported: { label: 'Score needed', tone: 'neutral' },
    awaiting_confirmation: { label: 'Awaiting confirmation', tone: 'pending' },
    disputed: { label: 'Disputed', tone: 'danger' },
    confirmed: { label: 'Final', tone: 'success' },
    bye: { label: 'Bye', tone: 'neutral' },
    void: { label: 'Void', tone: 'neutral' },
  };

  function normalizeCompetitionResult(match = {}) {
    const aliases = {
      pending: 'awaiting_confirmation',
      final: 'confirmed',
      done: 'confirmed',
    };
    let stateName = String(match.result_state || match.status || 'unreported').toLowerCase();
    stateName = aliases[stateName] || stateName;
    if (!COMPETITION_RESULT_STATES[stateName]) stateName = 'unreported';
    const meta = COMPETITION_RESULT_STATES[stateName];
    const waitingForSides = Object.prototype.hasOwnProperty.call(match, 'entry1_id')
      && (match.entry1_id == null || match.entry2_id == null);
    return {
      state: stateName,
      label: waitingForSides ? 'Not ready' : meta.label,
      tone: waitingForSides ? 'neutral' : meta.tone,
      confirmed: stateName === 'confirmed',
      terminal: ['confirmed', 'bye', 'void'].includes(stateName),
      blocksProgression: ['awaiting_confirmation', 'disputed'].includes(stateName),
    };
  }

  function competitionMatchContext(kind, parent, match) {
    if (kind === 'league') {
      return {
        side1: match.player1 || { display_name: 'Player 1' },
        side2: match.player2 || { display_name: 'Player 2' },
        context: `Round ${match.round} · Box ${match.box}`,
      };
    }
    const entries = Object.fromEntries((parent.entries || []).map((entry) => [entry.id, entry]));
    const thirdPlace = match.round === parent.total_rounds && match.position === 1;
    return {
      side1: entries[match.entry1_id] || { name: 'TBD' },
      side2: entries[match.entry2_id] || { name: 'TBD' },
      context: parent.format === 'round_robin'
        ? `Round ${match.round}`
        : thirdPlace ? '3rd-place match' : tournamentRoundLabel(match.round, parent.total_rounds || 1),
    };
  }

  const competitionSideName = (side) => side.display_name || side.name || 'TBD';

  function competitionResultStatusHtml(match, { compact = false } = {}) {
    const result = normalizeCompetitionResult(match);
    const note = result.state === 'awaiting_confirmation' && match.reported_by_name
      ? ` · by ${esc(match.reported_by_name)}`
      : result.state === 'disputed' && match.disputed_by_name
        ? ` · by ${esc(match.disputed_by_name)}` : '';
    return `<span class="competition-result-status is-${result.tone}" aria-label="Result status: ${esc(result.label)}${note}">${esc(result.label)}${compact ? '' : note}</span>`;
  }

  function competitionResultProvenanceHtml(match) {
    const rows = [];
    if (match.reported_by_name) rows.push(`Reported by <b>${esc(match.reported_by_name)}</b>${match.reported_at ? ` · ${esc(fmtDateTime(match.reported_at))}` : ''}`);
    if (match.confirmed_by_name) rows.push(`Confirmed by <b>${esc(match.confirmed_by_name)}</b>${match.confirmed_at ? ` · ${esc(fmtDateTime(match.confirmed_at))}` : ''}`);
    if (match.disputed_by_name) rows.push(`Disputed by <b>${esc(match.disputed_by_name)}</b>${match.disputed_at ? ` · ${esc(fmtDateTime(match.disputed_at))}` : ''}`);
    if (match.dispute_reason) rows.push(`<b>Reason:</b> ${esc(match.dispute_reason)}`);
    if (match.resolution_kind) rows.push(`Resolution: ${esc(String(match.resolution_kind).replace(/_/g, ' '))}`);
    return rows.length ? `<div class="competition-provenance">${rows.map((row) => `<div>${row}</div>`).join('')}</div>` : '';
  }

  function competitionResultHistoryHtml(match) {
    const history = Array.isArray(match.result_history) ? match.result_history : [];
    if (!history.length) return '';
    return `
      <details class="competition-audit">
        <summary>Result activity (${history.length})</summary>
        <ol>
          ${history.slice().reverse().map((event) => {
            const score = event.score1 != null && event.score2 != null ? ` · ${event.score1}–${event.score2}` : '';
            const version = event.version != null ? ` · v${event.version}` : '';
            const reason = event.reason ? `<div>${esc(event.reason)}</div>` : '';
            return `<li><b>${esc(String(event.action || 'updated').replace(/_/g, ' '))}</b>${version}${score}${event.actor_name ? ` · ${esc(event.actor_name)}` : ''}${event.created_at ? ` · ${esc(fmtDateTime(event.created_at))}` : ''}${reason}</li>`;
          }).join('')}
        </ol>
      </details>`;
  }

  function competitionActionNeeded(parent) {
    return (parent.matches || []).map((match) => {
      const result = normalizeCompetitionResult(match);
      if (match.can_confirm_result || match.awaiting_your_confirmation) {
        return { match, priority: 0, title: 'Review reported score', detail: 'Confirm it or flag a problem.' };
      }
      if (result.state === 'disputed' && match.can_resolve_result) {
        return { match, priority: 1, title: 'Resolve disputed result', detail: match.dispute_reason || 'Review the score and record a decision.' };
      }
      if (match.can_report_result) {
        return {
          match,
          priority: result.state === 'disputed' ? 2 : 3,
          title: result.state === 'disputed' ? 'Submit corrected score' : 'Enter match score',
          detail: result.state === 'disputed' ? (match.dispute_reason || 'The previous report was disputed.') : 'The result is still unreported.',
        };
      }
      return null;
    }).filter(Boolean).sort((a, b) => a.priority - b.priority || a.match.id - b.match.id);
  }

  function competitionActionNeededHtml(kind, parent) {
    const items = competitionActionNeeded(parent);
    if (!items.length) return '';
    return `
      <section class="competition-actions" aria-labelledby="${kind}-actions-title">
        <div class="section-label" id="${kind}-actions-title">Action needed · ${items.length}</div>
        ${items.map(({ match, title, detail }) => {
          const context = competitionMatchContext(kind, parent, match);
          return `
            <div class="card competition-action-card" data-result-match="${match.id}" data-match-key="${match.id}">
              <div class="row-main">
                <div class="row-title">${esc(title)}</div>
                <div class="row-sub">${esc(competitionSideName(context.side1))} vs ${esc(competitionSideName(context.side2))} · ${esc(detail)}</div>
              </div>
              <span class="chev" aria-hidden="true">›</span>
            </div>`;
        }).join('')}
      </section>`;
  }

  function captureCompetitionViewState(box) {
    const modal = box.querySelector('.modal');
    const bracket = modal?.querySelector('.bracket');
    const active = document.activeElement;
    const focusedMatch = active && box.contains(active) ? active.closest('[data-match-key]') : null;
    const focusKey = focusedMatch?.dataset.matchKey || (active && box.contains(active) ? active.id || null : null);
    const focusMatchIndex = focusedMatch
      ? [...modal.querySelectorAll(`[data-match-key="${CSS.escape(String(focusKey))}"]`)].indexOf(focusedMatch)
      : -1;
    return {
      scrollTop: modal?.scrollTop || 0,
      bracketScrollLeft: bracket?.scrollLeft || 0,
      focusKey,
      focusMatchIndex,
    };
  }

  function restoreCompetitionViewState(box, snapshot) {
    if (!snapshot) return;
    const modal = box.querySelector('.modal');
    if (modal) modal.scrollTop = snapshot.scrollTop;
    const bracket = modal?.querySelector('.bracket');
    if (bracket) bracket.scrollLeft = snapshot.bracketScrollLeft;
    if (snapshot.focusKey) {
      const matchTargets = [...(modal?.querySelectorAll(`[data-match-key="${CSS.escape(String(snapshot.focusKey))}"]`) || [])];
      const target = matchTargets[snapshot.focusMatchIndex] || matchTargets[0]
        || modal?.querySelector(`#${CSS.escape(String(snapshot.focusKey))}`);
      target?.focus({ preventScroll: true });
    }
  }

  function competitionOverlayCanRefresh(box) {
    if (currentOverlayEntry()?.el !== box || box.dataset.competitionMutation === 'true') return false;
    const active = document.activeElement;
    return !(active && box.contains(active) && active.matches('input, textarea, select, [contenteditable="true"]'));
  }

  function openCompetitionResultSheet(kind, parent, sourceMatch, hooks = {}) {
    let liveParent = parent;
    let match = sourceMatch;
    const plural = kind === 'league' ? 'leagues' : 'tournaments';
    const context = competitionMatchContext(kind, liveParent, match);
    const side1Name = competitionSideName(context.side1);
    const side2Name = competitionSideName(context.side2);
    const result = normalizeCompetitionResult(match);
    const canEditScores = !!(match.can_report_result || match.can_resolve_result || match.can_correct_result);
    const needsReason = !!(match.can_dispute_result || match.can_resolve_result || match.can_correct_result);
    const progressionNote = kind === 'league' && result.state === 'unreported'
      ? 'Submit a score only if this match was played. Once submitted, standings wait for confirmation; an unreported match can close as a sat-out.'
      : result.terminal
        ? 'This result is final, so standings or bracket progression can continue.'
        : 'Standings and bracket progression wait until the score is confirmed or resolved.';
    const actionButtons = [
      match.can_report_result ? '<button type="button" class="btn btn-primary btn-block" data-result-action="score">Submit score for confirmation</button>' : '',
      match.can_confirm_result ? '<button type="button" class="btn btn-primary btn-block" data-result-action="confirm">Confirm score</button>' : '',
      match.can_dispute_result ? '<button type="button" class="btn btn-secondary btn-block" data-result-action="dispute">Dispute score</button>' : '',
      match.can_resolve_result ? '<button type="button" class="btn btn-primary btn-block" data-result-action="resolve">Resolve & finalize</button>' : '',
      match.can_correct_result ? '<button type="button" class="btn btn-secondary btn-block" data-result-action="resolve">Correct final result</button>' : '',
      kind === 'league' && (match.can_resolve_result || match.can_correct_result)
        ? '<button type="button" class="btn btn-secondary btn-block competition-void" data-result-action="void">Void this result</button>' : '',
    ].filter(Boolean).join('');
    const route = { kind, id: Number(liveParent.id), matchId: Number(match.id) };
    const modal = openModal(`
      ${modalHead('Match result')}
      <form id="competition-result-form" class="competition-result-form" novalidate>
        <div class="row-sub" style="margin:-4px 0 4px">${esc(liveParent.name)} · ${esc(context.context)}</div>
        <div class="competition-result-summary" id="competition-result-summary" role="status">
          ${competitionResultStatusHtml(match)}
          ${competitionResultProvenanceHtml(match)}
        </div>
        <div class="form-grid competition-score-grid">
          <div class="form-field">
            <label for="competition-score-1">${esc(side1Name)}</label>
            <input type="number" id="competition-score-1" min="0" max="99" inputmode="numeric" value="${match.score1 ?? ''}" ${canEditScores ? '' : 'readonly'} />
          </div>
          <div class="form-field">
            <label for="competition-score-2">${esc(side2Name)}</label>
            <input type="number" id="competition-score-2" min="0" max="99" inputmode="numeric" value="${match.score2 ?? ''}" ${canEditScores ? '' : 'readonly'} />
          </div>
        </div>
        ${needsReason ? `
          <div class="form-field competition-reason-field">
            <label for="competition-result-reason">Reason <span class="row-sub">(required for disputes and organizer decisions)</span></label>
            <textarea id="competition-result-reason" maxlength="500" rows="3" placeholder="What needs to be corrected?"></textarea>
          </div>` : ''}
        <p class="competition-progression-note">${esc(progressionNote)}</p>
        ${competitionResultHistoryHtml(match)}
        <div class="competition-result-actions">${actionButtons || `<p class="row-sub">${result.state === 'awaiting_confirmation' ? 'Waiting for the other side to review this score.' : 'No action is available for this result.'}</p>`}</div>
      </form>
    `, { route, label: 'Match result' });
    const form = modal.querySelector('#competition-result-form');
    const score1 = modal.querySelector('#competition-score-1');
    const score2 = modal.querySelector('#competition-score-2');
    const reason = modal.querySelector('#competition-result-reason');
    const primaryAction = modal.querySelector('[data-result-action]');
    const formUX = primaryAction ? bindModalFormUX(modal, primaryAction) : null;
    let staleParentNeedsRender = false;
    modal._cleanupFns?.push(() => {
      if (staleParentNeedsRender) hooks.adoptFresh?.(liveParent, { render: true });
    });

    const setMutationBusy = (busy, activeButton = null) => {
      modal.dataset.competitionMutation = String(busy);
      hooks.setMutating?.(busy);
      modal.querySelectorAll('[data-result-action]').forEach((button) => {
        if (busy) {
          if (button !== activeButton) button.disabled = true;
        } else {
          button.disabled = button.dataset.resultUnavailable === 'true';
        }
      });
    };
    const readScores = () => {
      const raw1 = score1.value.trim();
      const raw2 = score2.value.trim();
      if (!raw1) { formUX.showError(`Enter ${side1Name}'s score.`, score1); return null; }
      if (!raw2) { formUX.showError(`Enter ${side2Name}'s score.`, score2); return null; }
      const value1 = Number(raw1);
      const value2 = Number(raw2);
      if (!Number.isInteger(value1) || value1 < 0 || value1 > 99) { formUX.showError('Use a whole-number score from 0 to 99.', score1); return null; }
      if (!Number.isInteger(value2) || value2 < 0 || value2 > 99) { formUX.showError('Use a whole-number score from 0 to 99.', score2); return null; }
      if (value1 === value2) { formUX.showError('Pickleball matches cannot end in a tie.', score2); return null; }
      return { score1: value1, score2: value2 };
    };
    const refreshStaleResult = async (attemptedAction, stalePayload = null) => {
      let canRetry = true;
      let refreshed = false;
      try {
        const fresh = stalePayload?.matches
          ? Object.fromEntries(Object.entries(stalePayload).filter(([key]) => key !== 'error'))
          : hooks.fetchFresh ? await hooks.fetchFresh() : await api(`/${plural}/${liveParent.id}`);
        liveParent = fresh;
        const freshMatch = (fresh.matches || []).find((item) => item.id === match.id);
        if (freshMatch) match = freshMatch;
        staleParentNeedsRender = true;
        hooks.adoptFresh?.(fresh, { render: false });
        const summary = modal.querySelector('#competition-result-summary');
        if (summary) summary.innerHTML = `${competitionResultStatusHtml(match)}${competitionResultProvenanceHtml(match)}`;
        const allowed = {
          score: !!match.can_report_result,
          confirm: !!match.can_confirm_result,
          dispute: !!match.can_dispute_result,
          resolve: !!(match.can_resolve_result || match.can_correct_result),
          void: kind === 'league' && !!(match.can_resolve_result || match.can_correct_result),
        };
        modal.querySelectorAll('[data-result-action]').forEach((button) => {
          button.dataset.resultUnavailable = String(!allowed[button.dataset.resultAction]);
          button.disabled = !allowed[button.dataset.resultAction];
        });
        canRetry = !!allowed[attemptedAction];
        refreshed = true;
      } catch { /* keep the user's inputs and the original stale message */ }
      formUX.showError(!refreshed
        ? 'This result changed on another device, but we could not refresh it. Your entries are preserved—reconnect and try again.'
        : canRetry
        ? 'This result changed on another device. We refreshed it—review your entries and try again.'
        : 'This result changed on another device, so that action is no longer available. Your entries are preserved; close to see the latest match.');
    };

    modal.querySelectorAll('[data-result-action]').forEach((button) => {
      button.addEventListener('click', async () => {
        const action = button.dataset.resultAction;
        let payload = { result_version: Number(match.result_version || 0) };
        if (action === 'score' || action === 'resolve') {
          const scores = readScores();
          if (!scores) return;
          payload = { ...payload, ...scores };
        }
        if (action === 'dispute' || action === 'resolve' || action === 'void') {
          const why = reason?.value.trim() || '';
          if (!why) { formUX.showError('Add a short reason so everyone understands the change.', reason); return; }
          payload.reason = why;
        }
        if (action === 'void') payload.void = true;
        const finishSubmitting = formUX.startSubmitting({
          score: 'Submitting…', confirm: 'Confirming…', dispute: 'Sending…', resolve: 'Finalizing…', void: 'Voiding…',
        }[action] || 'Saving…', button);
        if (!finishSubmitting) return;
        setMutationBusy(true, button);
        try {
          const mutationResult = await api(`/${plural}/${liveParent.id}/matches/${match.id}/${action === 'void' ? 'resolve' : action}`, {
            method: 'POST',
            body: JSON.stringify(payload),
          });
          staleParentNeedsRender = false;
          closeModal(modal);
          toast(action === 'confirm' ? 'Score confirmed ✅' : action === 'dispute' ? 'Dispute sent' : action === 'void' ? 'Result voided' : action === 'resolve' ? 'Result finalized ✅' : 'Score sent for confirmation');
          await hooks.refresh?.({ force: true, data: mutationResult?.matches ? mutationResult : null });
        } catch (err) {
          finishSubmitting();
          if (err.code === 'stale_result' || err.status === 409 && err.data?.error === 'stale_result') {
            await refreshStaleResult(action, err.data);
          } else {
            formUX.showError(err.message);
          }
        } finally {
          setMutationBusy(false);
        }
      });
    });
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      modal.querySelector('[data-result-action]:not([disabled])')?.click();
    });
    return modal;
  }

  // ---------- Box leagues ----------

  function leagueMatchCardHtml(match, { mine = false } = {}) {
    const result = normalizeCompetitionResult(match);
    const score = match.score1 != null && match.score2 != null ? `${match.score1}–${match.score2}` : '—';
    const player1Won = result.confirmed && match.winner_id === match.player1?.id;
    const player2Won = result.confirmed && match.winner_id === match.player2?.id;
    return `
      <div class="card competition-match-card${mine ? ' is-mine' : ''}" data-result-match="${match.id}" data-match-key="${match.id}">
        <div class="competition-match-main">
          <div class="competition-match-names">
            <span class="${player1Won ? 'competition-winner' : ''}">${esc(match.player1?.display_name || 'Player 1')}</span>
            <span class="competition-versus">vs</span>
            <span class="${player2Won ? 'competition-winner' : ''}">${esc(match.player2?.display_name || 'Player 2')}</span>
          </div>
          <div class="row-sub">Round ${match.round} · Box ${match.box}</div>
        </div>
        <div class="competition-match-result">
          <b>${score}</b>
          ${competitionResultStatusHtml(match, { compact: true })}
        </div>
      </div>`;
  }

  async function openLeagueScreen(leagueId, requestedMatchId = null) {
    // The league owns its parent route; only the result sheet owns /match/:id.
    // Back from an exact shared result therefore lands on a stable league URL.
    const route = { kind: 'league', id: leagueId };
    const routeLoad = beginRoutedOverlayLoad(route);
    const detailPath = `/leagues/${leagueId}${requestedMatchId ? `?match_id=${Number(requestedMatchId)}` : ''}`;
    let lg;
    try { lg = await api(detailPath); }
    catch (e) {
      if (!routedOverlayLoadIsCurrent(routeLoad)) return;
      toast(e.message); clearDeadDeepLink(overlayRouteHash(route)); return;
    }
    if (!routedOverlayLoadIsCurrent(routeLoad)) return;
    const box = openModal(modalHead('League'), { route, label: 'League' });
    const content = box.querySelector('.modal');
    let deepLinkOpened = false;

    const refresh = async ({ force = false, data = null } = {}) => {
      if (!force && !competitionOverlayCanRefresh(box)) return null;
      try {
        const previous = JSON.stringify(lg);
        const fresh = data || await api(detailPath);
        lg = fresh;
        if (currentOverlayEntry()?.el === box && (force || JSON.stringify(fresh) !== previous)) {
          render(fresh, { preserve: true });
        }
        return fresh;
      } catch { return null; }
    };
    const openMatch = (match) => openCompetitionResultSheet('league', lg, match, {
      setMutating: (busy) => { box.dataset.competitionMutation = String(busy); },
      fetchFresh: () => api(detailPath),
      adoptFresh: (fresh, { render: shouldRender = true } = {}) => {
        lg = fresh;
        if (shouldRender && currentOverlayEntry()?.el === box) render(fresh, { preserve: true });
      },
      refresh,
    });
    const render = (data, { preserve = false } = {}) => {
      lg = data;
      const snapshot = preserve ? captureCompetitionViewState(box) : null;
      const statusChip = {
        registration: '<span class="tag live" style="margin:0">Signups open</span>',
        active: `<span class="tag ranked" style="margin:0">Round ${lg.current_round}</span>`,
        completed: '<span class="tag" style="margin:0">Season complete</span>',
        cancelled: '<span class="tag warn" style="margin:0">Cancelled</span>',
      }[lg.status] || '';
      const rankMember = (a, b) => (b.points - a.points) || (b.wins - a.wins) || ((b.user?.rating || 0) - (a.user?.rating || 0));
      let body = `
        ${modalHead(`📦 ${lg.name}`)}
        <div class="row-sub" style="margin:-6px 0 6px">${lg.court ? `${esc(lg.court.name)} · ` : ''}${lg.member_count} player${lg.member_count === 1 ? '' : 's'} · boxes of ${lg.box_size} · new round every ${lg.round_days} days</div>
        <div style="margin-bottom:12px">${statusChip}${lg.club_name ? ` <span class="tag" style="margin:0 0 0 4px">🏛 ${esc(lg.club_name)}</span>` : ''}</div>
        ${lg.description ? `<div class="row-sub" style="margin-bottom:12px">${esc(lg.description)}</div>` : ''}
        ${lg.joined ? `<button class="btn btn-secondary btn-block" id="lg-chat" style="margin-bottom:10px;position:relative">💬 League chat${lg.chat_unread ? ` <span class="badge" style="position:static;margin-left:6px">${lg.chat_unread > 9 ? '9+' : lg.chat_unread}</span>` : ''}</button>` : ''}`;

      if (lg.status === 'completed' && lg.champion_name) {
        const champ = lg.members.find((member) => member.user && member.user.id === lg.champion_user_id);
        body += `
          <div class="card" style="text-align:center;padding:18px;background:var(--violet-50);border:1px solid var(--violet-200)">
            <div style="font-size:34px">👑</div>
            <div style="font-weight:800;font-size:17px;color:var(--violet-700)">${esc(lg.champion_name)}</div>
            <div class="row-sub">Season champion${champ ? ` · ${champ.points} pts` : ''}</div>
          </div>`;
      }

      if (lg.status === 'registration') {
        body += `<div class="row-sub" style="margin-bottom:10px">Starts ${fmtDateTime(lg.starts_at)}. Players are seeded into boxes by rating; play everyone in your box each round — winners move up, last place drops down.</div>`;
        body += '<div class="section-label">Signed up</div>';
        body += lg.members.map((member) => `
          <div class="card row" data-view-user="${member.user.id}" style="cursor:pointer;padding:10px 14px">
            ${avatarHtml(member.user, 'sm')}
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(member.user.display_name)}${member.user.id === lg.organizer_id ? ' <span class="tag" style="margin:0 0 0 4px">Organizer</span>' : ''}</div>
              <div class="row-sub">${skillLabel(member.user.skill_level)} · ${member.user.rating}</div>
            </div>
          </div>`).join('');
        if (!lg.joined) body += '<button class="btn btn-primary btn-block" id="lg-join" style="margin-top:10px;padding:15px">🙌 Sign me up</button>';
        else if (!lg.is_organizer) body += '<button class="btn btn-secondary btn-block" id="lg-leave" style="margin-top:10px">Withdraw</button>';
        if (lg.is_organizer) {
          body += `<button class="btn btn-primary btn-block" id="lg-start" style="margin-top:10px;padding:15px" ${lg.member_count < 3 ? 'disabled' : ''}>🏁 Seed boxes & start${lg.member_count < 3 ? ' (need 3+)' : ''}</button>`;
          body += '<button class="btn btn-secondary btn-block" id="lg-cancel" style="margin-top:8px;color:#c92a2a">🗑 Cancel league</button>';
        }
      }

      if (lg.status === 'active' || lg.status === 'completed') {
        const myId = state.me.id;
        const roundMatches = (lg.matches || []).filter((match) => match.round === lg.current_round);
        body += competitionActionNeededHtml('league', { ...lg, matches: roundMatches });
        const mine = roundMatches.filter((match) => match.player1?.id === myId || match.player2?.id === myId);
        if (mine.length) {
          body += '<div class="section-label">🎯 Your matches this round</div>';
          body += mine.map((match) => leagueMatchCardHtml(match, { mine: true })).join('');
        }

        const boxes = {};
        lg.members.forEach((member) => { if (member.box) (boxes[member.box] = boxes[member.box] || []).push(member); });
        Object.keys(boxes).sort((a, b) => a - b).forEach((boxNumber) => {
          body += `<div class="section-label">📦 Box ${boxNumber}${Number(boxNumber) === 1 ? ' · top box' : ''}</div>`;
          body += boxes[boxNumber].sort(rankMember).map((member, index) => `
            <div class="card row" data-view-user="${member.user.id}" style="cursor:pointer;padding:9px 14px">
              <span style="font-size:14px;width:20px;text-align:center;font-weight:700">${index + 1}</span>
              ${avatarHtml(member.user, 'sm')}
              <div class="row-main">
                <div class="row-title" style="font-size:14px">${esc(member.user.display_name)}${member.user.id === myId ? ' <span class="tag" style="margin:0 0 0 4px">You</span>' : ''}</div>
                <div class="row-sub">${member.wins}–${member.losses} this season</div>
              </div>
              <b style="font-size:14px">${member.points} pt${member.points === 1 ? '' : 's'}</b>
            </div>`).join('');
          const matches = roundMatches.filter((match) => match.box === Number(boxNumber));
          if (matches.length) {
            body += '<div class="competition-box-matches" aria-label="Box match results">';
            body += matches.map((match) => leagueMatchCardHtml(match)).join('');
            body += '</div>';
          }
        });

        if (lg.status === 'active' && lg.is_organizer) {
          const unresolved = roundMatches.filter((match) => normalizeCompetitionResult(match).blocksProgression);
          const disabled = unresolved.length ? 'disabled aria-describedby="lg-unresolved-note"' : '';
          body += `
            ${unresolved.length ? `<p class="competition-progression-note" id="lg-unresolved-note">${unresolved.length} result${unresolved.length === 1 ? '' : 's'} must be confirmed, resolved, or voided before the round can close.</p>` : ''}
            <div class="competition-organizer-actions">
              <button class="btn btn-primary" id="lg-advance" ${disabled}>⏭ Close round ${lg.current_round}</button>
              <button class="btn btn-secondary" id="lg-complete" ${disabled}>👑 Finish season</button>
            </div>
            <button class="btn btn-secondary btn-block" id="lg-cancel" style="margin-top:8px;color:#c92a2a">🗑 Cancel league</button>`;
        }
      }

      content.innerHTML = body;
      setDialogLabel(content, 'League');
      bindUserButtons(box);
      content.querySelector('#lg-chat')?.addEventListener('click', () => transitionModal(box, () => openLeagueChat(lg)));
      const act = (path, confirmMsg) => async (event) => {
        if (confirmMsg && !window.confirm(confirmMsg)) return;
        if (box.dataset.competitionMutation === 'true') return;
        const button = event?.currentTarget;
        if (button) button.disabled = true;
        box.dataset.competitionMutation = 'true';
        try { await api(`/leagues/${lg.id}/${path}`, { method: 'POST' }); await refresh({ force: true }); }
        catch (error) { toast(error.message); }
        finally {
          box.dataset.competitionMutation = 'false';
          if (button?.isConnected) button.disabled = false;
        }
      };
      content.querySelector('#lg-join')?.addEventListener('click', act('join'));
      content.querySelector('#lg-leave')?.addEventListener('click', act('leave'));
      content.querySelector('#lg-start')?.addEventListener('click', act('start'));
      content.querySelector('#lg-advance')?.addEventListener('click', act('advance', `Close round ${lg.current_round}? Box winners move up, last place drops.`));
      content.querySelector('#lg-complete')?.addEventListener('click', act('complete', 'Finish the season and crown the champion?'));
      content.querySelector('#lg-cancel')?.addEventListener('click', act('cancel', 'Cancel this league for everyone?'));
      content.querySelectorAll('[data-result-match]').forEach((card) => {
        makePressable(card, () => {
          const match = (lg.matches || []).find((item) => item.id === Number(card.dataset.resultMatch));
          if (match) openMatch(match);
        });
      });
      if (snapshot) restoreCompetitionViewState(box, snapshot);

      if (requestedMatchId && !deepLinkOpened) {
        deepLinkOpened = true;
        const match = (lg.matches || []).find((item) => item.id === Number(requestedMatchId));
        const card = content.querySelector(`[data-result-match="${Number(requestedMatchId)}"]`);
        if (match) {
          if (card) {
            card.classList.add('competition-match-highlight');
            const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
            card.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' });
          }
          queueMicrotask(() => openMatch(match));
        } else {
          toast('That league match is no longer available.');
        }
      }
    };

    render(lg);
    const poll = setInterval(async () => {
      if (!document.body.contains(box)) { clearInterval(poll); return; }
      if (document.hidden || state.connectionState === 'offline' || !competitionOverlayCanRefresh(box)) return;
      await refresh();
    }, 8000);
    box._cleanupFns?.push(() => clearInterval(poll));
  }

  async function openLeagueChat(lg) {
    const modalLoad = beginRoutedOverlayLoad(null);
    let data;
    try { data = await api(`/leagues/${lg.id}/chat`); } catch (e) {
      if (!routedOverlayLoadIsCurrent(modalLoad)) return;
      toast(e.message); return;
    }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;
    refreshMe(); // the room GET is the authoritative read marker

    const modal = openModal(`
      <div class="thread">
        <div class="thread-head">
          <button class="modal-close" style="font-size:18px">‹</button>
          <span style="font-size:22px">📦</span>
          <div class="row-main">
            <div class="row-title">${esc(lg.name)}</div>
            <div class="row-sub">League chat — only players in this league can read it</div>
          </div>
        </div>
        <div class="thread-msgs" id="lgc-msgs" role="log" aria-live="polite" aria-relevant="additions" aria-label="League conversation"></div>
        <form class="thread-input" id="lgc-form">
          <input type="text" id="lgc-text" placeholder="Message the league…" autocomplete="off" maxlength="2000" />
          <button type="submit" aria-label="Send">➤</button>
        </form>
      </div>
    `, { chat: true });

    const msgsEl = modal.querySelector('#lgc-msgs');
    const input = modal.querySelector('#lgc-text');
    const chatUX = bindChatContinuity(modal, msgsEl, input, `league:${lg.id}`);
    let lastId = 0;
    const renderMsgs = (items, append, { forceBottom = false, newMessages = false } = {}) => {
      const batch = prepareChatRenderBatch(msgsEl, items, append);
      if (batch.newestId) lastId = Math.max(lastId, batch.newestId);
      items = batch.items;
      if (append && !items.length) return;
      const snapshot = chatUX.captureScroll();
      const html = items.map((m) => {
        const mine = m.sender_id === state.me.id;
        return `
        <div data-message-id="${m.id}" style="display:flex;gap:8px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%">
          ${mine ? '' : `<div class="avatar sm" style="background:${esc(m.sender_color)}">${esc(initials(m.sender_name))}</div>`}
          <div class="bubble ${mine ? 'me' : 'them'}" style="max-width:100%" ${mine ? `data-del-msg="${m.id}" title="Tap to delete"` : `data-room-heart="${m.id}" title="Tap to ❤️"`}>${m.heart_count ? `<span class="bubble-heart" data-heart-badge>❤️${m.heart_count > 1 ? ' ' + m.heart_count : ''}</span>` : ''}
            ${mine ? '' : `<div style="font-size:11px;font-weight:700;opacity:.75;margin-bottom:2px">${esc(m.sender_name)}</div>`}
            ${m.has_image ? `<div data-img-id="${m.id}" style="min-height:60px;min-width:120px;margin-bottom:${m.body ? '6px' : '0'}">⏳</div>` : ''}
            ${esc(m.body)}
            <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
          </div>
        </div>`;
      }).join('');
      if (append && !msgsEl.querySelector('.empty-state')) msgsEl.insertAdjacentHTML('beforeend', html);
      else if (append) msgsEl.innerHTML = html;
      else msgsEl.innerHTML = html || '<div class="empty-state" style="padding:20px">No messages yet — talk some friendly trash 👋</div>';
      chatUX.restoreScroll(snapshot, { forceBottom, newMessageCount: newMessages ? items.length : 0 });
      hydrateChatImages(msgsEl, chatUX);
    };
    renderMsgs(data.items, false, { forceBottom: true });
    chatUX.activateOutbox((message) => renderMsgs([message], true, { forceBottom: true }));
    addPhotoToComposer(modal, '#lgc-form', '#lgc-text', chatUX);

    const pollTimer = setInterval(async () => {
      if (!document.body.contains(msgsEl)) { clearInterval(pollTimer); return; }
      if (document.hidden || state.connectionState === 'offline') return;
      try {
        const fresh = await api(`/leagues/${lg.id}/chat?since_id=${lastId}`);
        if (fresh.items.length) renderMsgs(fresh.items, true, { newMessages: true });
        applyRoomHearts(msgsEl, fresh.heart_counts);
      } catch { /* offline */ }
    }, 5000);

    modal.querySelector('#lgc-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = input.value.trim();
      if (!body) return;
      try {
        await chatUX.send(body);
      } catch (err) { toast(err.message); }
    });
  }

  async function openCreateLeagueSheet() {
    const modalLoad = beginRoutedOverlayLoad(null);
    const start = new Date();
    start.setDate(start.getDate() + 3);
    start.setHours(18, 0, 0, 0);
    const pad = (n) => String(n).padStart(2, '0');
    const defaultWhen = `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}T${pad(start.getHours())}:${pad(start.getMinutes())}`;
    let myClubs = [];
    try { myClubs = (await api('/clubs/mine')).items || []; } catch { /* clubs optional */ }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;

    const modal = openModal(`
      ${modalHead('Start a box league')}
      <form id="lc-form" novalidate>
      <p class="row-sub" style="margin:-6px 0 12px">A season-long ladder: players are seeded into boxes by rating and play everyone in their box each round. Winners move up a box, last place drops.</p>
      <div class="form-field">
        <label for="lc-name">Name</label>
        <input type="text" id="lc-name" maxlength="120" placeholder="e.g. Riverside Winter Ladder" />
      </div>
      <div class="form-field">
        <label for="lc-court-search">Home court</label>
        <input type="search" id="lc-court-search" placeholder="Search courts…" autocomplete="off" />
        <input type="hidden" id="lc-court-id" value="" />
        <div id="lc-court-results" style="margin-top:8px"></div>
      </div>
      <div class="form-grid">
        <div class="form-field">
          <label for="lc-when">First round starts</label>
          <input type="datetime-local" id="lc-when" value="${defaultWhen}" />
        </div>
        <div class="form-field">
          <label for="lc-box">Box size</label>
          <select id="lc-box"><option>3</option><option selected>4</option><option>5</option><option>6</option></select>
        </div>
      </div>
      ${myClubs.length ? `
      <div class="form-field">
        <label id="lc-club-label">Host under a club banner?</label>
        <div class="quick-times" id="lc-club" role="group" aria-labelledby="lc-club-label" aria-describedby="lc-club-hint">
          <button type="button" data-club-id="" class="active" aria-pressed="true">Just me</button>
          ${myClubs.map((cl) => `<button type="button" data-club-id="${cl.id}" aria-pressed="false">🏛 ${esc(cl.name)}</button>`).join('')}
        </div>
        <div class="row-sub" id="lc-club-hint" style="margin-top:6px"></div>
      </div>` : ''}
      <div class="form-field">
        <label class="sr-only" for="lc-desc">League details</label>
        <input type="text" id="lc-desc" maxlength="200" placeholder="Details (optional) — e.g. Play your box by Sunday each week" />
      </div>
      <button type="submit" class="btn btn-primary btn-block" id="lc-submit" style="padding:15px">Create league</button>
      </form>
    `);
    clubCourtPicker(modal, 'lc');
    const formUX = bindModalFormUX(modal, '#lc-submit', { draftKey: 'create-league' });
    let lcClubId = null;
    modal.querySelector('#lc-club')?.addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      lcClubId = Number(btn.dataset.clubId) || null;
      modal.querySelectorAll('#lc-club button').forEach((b) => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-pressed', String(active));
      });
      const picked = myClubs.find((cl) => cl.id === lcClubId);
      modal.querySelector('#lc-club-hint').textContent = picked && picked.member_count > 1
        ? `📣 The other ${picked.member_count - 1} member${picked.member_count === 2 ? '' : 's'} of ${picked.name} will be invited to sign up.`
        : '';
    });
    modal.querySelector('#lc-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      formUX.clearError();
      const name = modal.querySelector('#lc-name').value.trim();
      const courtId = Number(modal.querySelector('#lc-court-id').value);
      const whenRaw = modal.querySelector('#lc-when').value;
      if (name.length < 3) {
        formUX.showError('Give your league a name (3+ characters).', modal.querySelector('#lc-name'));
        return;
      }
      if (!courtId) {
        formUX.showError('Pick a home court.', modal.querySelector('#lc-court-search'));
        return;
      }
      if (!whenRaw) {
        formUX.showError('Pick a start time.', modal.querySelector('#lc-when'));
        return;
      }
      const startsAt = new Date(whenRaw);
      if (!Number.isFinite(startsAt.getTime())) {
        formUX.showError('Choose a valid start date and time.', modal.querySelector('#lc-when'));
        return;
      }
      const finishSubmitting = formUX.startSubmitting('Creating league…');
      if (!finishSubmitting) return;
      try {
        const lg = await api('/leagues', { method: 'POST', body: JSON.stringify({
          name,
          court_id: courtId,
          starts_at: startsAt.toISOString(),
          box_size: Number(modal.querySelector('#lc-box').value),
          description: modal.querySelector('#lc-desc').value.trim(),
          club_id: lcClubId,
        }) });
        formUX.clearDraft({ disable: true });
        toast('League created 📦 Share it so players can sign up!');
        transitionModal(modal, () => openLeagueScreen(lg.id));
      } catch (err) {
        finishSubmitting();
        formUX.showError(err.message);
      }
    });
  }

  async function openCreateTournamentSheet(presetCourt = null) {
    const modalLoad = beginRoutedOverlayLoad(null);
    // Court suggestions: where you are, saved courts, then nearby.
    const suggestions = [];
    let myClubs = [];
    try {
      const c = areaLatLng();
      const [favs, near, clubsRes] = await Promise.all([
        api('/courts/favorites').catch(() => ({ items: [] })),
        api(`/courts?lat=${c.lat}&lng=${c.lng}&radius=30&limit=6`).catch(() => ({ items: [] })),
        api('/clubs/mine').catch(() => ({ items: [] })),
      ]);
      myClubs = clubsRes.items || [];
      const seen = new Set();
      if (state.presence && state.presence.checked_in) {
        suggestions.push({ id: state.presence.court_id, name: state.presence.court_name, city: '', tag: "📍 You're here" });
        seen.add(state.presence.court_id);
      }
      (favs.items || []).forEach((ct) => {
        if (!seen.has(ct.id) && suggestions.length < 5) { suggestions.push({ ...ct, tag: '⭐ Saved' }); seen.add(ct.id); }
      });
      (near.items || []).forEach((ct) => {
        if (!seen.has(ct.id) && suggestions.length < 5) {
          suggestions.push({ ...ct, tag: ct.distance_miles != null ? `${ct.distance_miles} mi` : 'Nearby' });
          seen.add(ct.id);
        }
      });
    } catch { /* suggestions are optional */ }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;

    const suggestionRows = suggestions.map((c) => `
      <button type="button" class="court-suggestion" data-pick-court="${c.id}" data-pick-name="${esc(c.name)}">
        <div class="row-main">
          <div class="row-title" style="font-size:14px">${esc(c.name)}</div>
          <div class="row-sub">${esc(c.city || '')}</div>
        </div>
        <span class="tag" style="margin:0">${esc(c.tag)}</span>
      </button>`).join('');

    // Default start: tomorrow 9:00 local.
    const start = new Date();
    start.setDate(start.getDate() + 1);
    start.setHours(9, 0, 0, 0);
    const pad = (n) => String(n).padStart(2, '0');
    const defaultWhen = `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}T${pad(start.getHours())}:${pad(start.getMinutes())}`;

    const modal = openModal(`
      ${modalHead('Create a tournament')}
      <form id="tc-form" novalidate>
      <div class="form-field">
        <label for="tc-name">Name</label>
        <input type="text" id="tc-name" maxlength="120" placeholder="e.g. Saturday Slam" />
      </div>
      <div class="form-field" role="group" aria-labelledby="tc-court-label">
        <label id="tc-court-label">Court</label>
        <div id="tc-court-selected" class="${presetCourt ? '' : 'hidden'} court-selected">
          <div class="row-main"><div class="row-title" style="font-size:14.5px" id="tc-court-name">${presetCourt ? esc(presetCourt.name) : ''}</div></div>
          <button type="button" class="btn btn-secondary btn-sm" id="tc-court-change">Change</button>
        </div>
        <div id="tc-court-picker" class="${presetCourt ? 'hidden' : ''}">
          <input type="search" id="tc-court-search" aria-label="Search courts" placeholder="Search courts…" autocomplete="off" />
          <div id="tc-court-results" style="margin-top:8px">${suggestionRows}</div>
        </div>
        <input type="hidden" id="tc-court-id" value="${presetCourt ? presetCourt.id : ''}" />
      </div>
      <div class="form-field">
        <label for="tc-when">Starts</label>
        <input type="datetime-local" id="tc-when" value="${defaultWhen}" />
      </div>
      <div class="form-field">
        <label id="tc-format-label">Format</label>
        <div class="segmented" id="tc-format" role="group" aria-labelledby="tc-format-label">
          <button type="button" data-val="single_elim" class="active" aria-pressed="true">🗂 Bracket</button>
          <button type="button" data-val="round_robin" aria-pressed="false">🔁 Round robin</button>
        </div>
        <div class="row-sub" id="tc-format-hint" style="margin-top:6px">Single elimination — lose and you're out, seeded by rating.</div>
      </div>
      <div class="form-grid">
        <div class="form-field">
          <label id="tc-event-label">Event</label>
          <div class="segmented" id="tc-event" role="group" aria-labelledby="tc-event-label">
            <button type="button" data-val="singles" class="active" aria-pressed="true">Singles</button>
            <button type="button" data-val="doubles" aria-pressed="false">Doubles</button>
          </div>
        </div>
        <div class="form-field">
          <label for="tc-max">Max entries</label>
          <select id="tc-max">
            <option>4</option><option selected>8</option><option>16</option><option>32</option>
          </select>
        </div>
      </div>
      <div class="form-field">
        <label id="tc-ranked-label">Play</label>
        <div class="segmented" id="tc-ranked" role="group" aria-labelledby="tc-ranked-label">
          <button type="button" data-val="" class="active" aria-pressed="true">Casual</button>
          <button type="button" data-val="1" aria-pressed="false">⚡ Ranked</button>
        </div>
        <div class="row-sub" style="margin-top:6px">Ranked: every match counts toward player ratings when the tournament finishes.</div>
      </div>
      ${myClubs.length ? `
      <div class="form-field">
        <label id="tc-club-label">Host under a club banner?</label>
        <div class="quick-times" id="tc-club" role="group" aria-labelledby="tc-club-label" aria-describedby="tc-club-hint">
          <button type="button" data-club-id="" class="active" aria-pressed="true">Just me</button>
          ${myClubs.map((cl) => `<button type="button" data-club-id="${cl.id}" aria-pressed="false">🏛 ${esc(cl.name)}</button>`).join('')}
        </div>
        <div class="row-sub" id="tc-club-hint" style="margin-top:6px"></div>
      </div>` : ''}
      <div class="form-field">
        <label class="sr-only" for="tc-desc">Tournament details</label>
        <input type="text" id="tc-desc" maxlength="200" placeholder="Details (optional) — e.g. Games to 11, win by 2" />
      </div>
      <button type="submit" class="btn btn-primary btn-block" id="tc-submit" style="padding:15px">Create tournament</button>
      </form>
    `);

    const formUX = bindModalFormUX(modal, '#tc-submit', { draftKey: 'create-tournament' });

    const setCourt = (id, name) => {
      modal.querySelector('#tc-court-id').value = id || '';
      modal.querySelector('#tc-court-name').textContent = name || '';
      modal.querySelector('#tc-court-selected').classList.toggle('hidden', !id);
      modal.querySelector('#tc-court-picker').classList.toggle('hidden', !!id);
    };
    modal.querySelector('#tc-court-change').addEventListener('click', () => setCourt(null, null));
    const bindCourtPicks = () => {
      modal.querySelectorAll('[data-pick-court]').forEach((row) => row.addEventListener('click', () => {
        setCourt(row.dataset.pickCourt, row.dataset.pickName);
      }));
    };
    bindCourtPicks();
    let searchTimer;
    modal.querySelector('#tc-court-search').addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      const q = e.target.value.trim();
      searchTimer = setTimeout(async () => {
        const resultsEl = modal.querySelector('#tc-court-results');
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
            </button>`).join('') || '<div class="row-sub" style="padding:8px 2px">No courts found</div>';
          bindCourtPicks();
        } catch { /* keep old results */ }
      }, 300);
    });

    const segPick = (selId, hintFn) => {
      modal.querySelector(selId).addEventListener('click', (e) => {
        const btn = e.target.closest('button');
        if (!btn) return;
        modal.querySelectorAll(`${selId} button`).forEach((b) => {
          const active = b === btn;
          b.classList.toggle('active', active);
          b.setAttribute('aria-pressed', String(active));
        });
        if (hintFn) hintFn(btn.dataset.val);
      });
    };
    segPick('#tc-format', (val) => {
      modal.querySelector('#tc-format-hint').textContent = val === 'round_robin'
        ? 'Round robin — everyone plays everyone; best record wins.'
        : "Single elimination — lose and you're out, seeded by rating.";
    });
    segPick('#tc-event');
    segPick('#tc-ranked');

    let tcClubId = null;
    modal.querySelector('#tc-club')?.addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      tcClubId = Number(btn.dataset.clubId) || null;
      modal.querySelectorAll('#tc-club button').forEach((b) => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-pressed', String(active));
      });
      const picked = myClubs.find((cl) => cl.id === tcClubId);
      modal.querySelector('#tc-club-hint').textContent = picked && picked.member_count > 1
        ? `📣 The other ${picked.member_count - 1} member${picked.member_count === 2 ? '' : 's'} of ${picked.name} will be pinged.`
        : '';
    });

    modal.querySelector('#tc-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      formUX.clearError();
      const name = modal.querySelector('#tc-name').value.trim();
      const courtId = Number(modal.querySelector('#tc-court-id').value);
      const whenRaw = modal.querySelector('#tc-when').value;
      if (name.length < 3) {
        formUX.showError('Give your tournament a name (3+ characters).', modal.querySelector('#tc-name'));
        return;
      }
      if (!courtId) {
        formUX.showError('Pick a court.', modal.querySelector('#tc-court-search'));
        return;
      }
      if (!whenRaw) {
        formUX.showError('Pick a start time.', modal.querySelector('#tc-when'));
        return;
      }
      const startsAt = new Date(whenRaw);
      if (!Number.isFinite(startsAt.getTime())) {
        formUX.showError('Choose a valid start date and time.', modal.querySelector('#tc-when'));
        return;
      }
      const finishSubmitting = formUX.startSubmitting('Creating tournament…');
      if (!finishSubmitting) return;
      try {
        const t = await api('/tournaments', {
          method: 'POST',
          body: JSON.stringify({
            name,
            court_id: courtId,
            starts_at: startsAt.toISOString(),
            format: modal.querySelector('#tc-format button.active').dataset.val,
            event_type: modal.querySelector('#tc-event button.active').dataset.val,
            max_entries: Number(modal.querySelector('#tc-max').value),
            ranked: !!modal.querySelector('#tc-ranked button.active').dataset.val,
            description: modal.querySelector('#tc-desc').value.trim(),
            club_id: tcClubId,
          }),
        });
        formUX.clearDraft({ disable: true });
        toast('Tournament created 🏆 Share it so players can register!');
        if (state.tab === 'play' && state.playSeg === 'brackets') renderPlay();
        transitionModal(modal, () => openTournamentScreen(t.id));
      } catch (err) {
        finishSubmitting();
        formUX.showError(err.message);
      }
    });
  }

  // "🏆 2 tournament titles" strip with the latest wins, tappable through to
  // each tournament. Shared by public profiles and own stats.
  function tournamentTitlesHtml(titles, leagueTitles) {
    const tCount = (titles && titles.count) || 0;
    const lCount = (leagueTitles && leagueTitles.count) || 0;
    if (!tCount && !lCount) return '';
    const headline = [
      tCount ? `${tCount} tournament title${tCount === 1 ? '' : 's'}` : '',
      lCount ? `${lCount} league title${lCount === 1 ? '' : 's'}` : '',
    ].filter(Boolean).join(' · ');
    return `
      <div class="card" style="margin-top:12px;padding:12px 14px">
        <div style="font-weight:800;font-size:14px;text-align:center">👑 ${headline}</div>
        ${((titles && titles.recent) || []).map((t) => `
          <div class="row" data-open-tournament="${t.id}" style="cursor:pointer;padding:7px 0 0;gap:8px">
            <span>🏆</span>
            <div class="row-main"><div class="row-title" style="font-size:13.5px">${esc(t.name)}</div></div>
            <div class="row-sub">${t.completed_at ? new Date(t.completed_at).toLocaleDateString([], { month: 'short', day: 'numeric' }) : ''}</div>
          </div>`).join('')}
        ${((leagueTitles && leagueTitles.recent) || []).map((t) => `
          <div class="row" data-open-league="${t.id}" style="cursor:pointer;padding:7px 0 0;gap:8px">
            <span>📦</span>
            <div class="row-main"><div class="row-title" style="font-size:13.5px">${esc(t.name)}</div></div>
            <div class="row-sub">${t.completed_at ? new Date(t.completed_at).toLocaleDateString([], { month: 'short', day: 'numeric' }) : ''}</div>
          </div>`).join('')}
      </div>`;
  }

  function tournamentRoundLabel(round, total) {
    const remaining = total - round;
    if (remaining === 0) return 'Final';
    if (remaining === 1) return 'Semifinals';
    if (remaining === 2) return 'Quarterfinals';
    return `Round ${round}`;
  }

  function bracketHtml(t) {
    const entries = {};
    t.entries.forEach((en) => { entries[en.id] = en; });
    const sideHtml = (m, entryId, score) => {
      const en = entryId ? entries[entryId] : null;
      const result = normalizeCompetitionResult(m);
      const isWinner = (result.confirmed || result.state === 'bye')
        && m.winner_entry_id && m.winner_entry_id === entryId;
      const mineCls = t.my_entry_id && entryId === t.my_entry_id ? 'bm-mine' : '';
      return `
        <div class="bm-side ${isWinner ? 'bm-win' : ''} ${mineCls}">
          <span class="bm-seed">${en && en.seed ? en.seed : ''}</span>
          <span class="bm-name">${en ? esc(en.name) : '<span style="opacity:.45">—</span>'}</span>
          <span class="bm-score">${score != null ? score : (m.status === 'bye' && isWinner ? 'bye' : '')}</span>
        </div>`;
    };
    const matchHtml = (m) => {
      const result = normalizeCompetitionResult(m);
      const ready = m.entry1_id != null && m.entry2_id != null && t.status === 'active' && result.state === 'unreported';
      return `
        <div class="bm competition-bracket-match ${ready ? 'bm-ready' : ''}" data-tmatch="${m.id}" data-result-match="${m.id}" data-match-key="${m.id}">
          ${sideHtml(m, m.entry1_id, m.score1)}
          ${sideHtml(m, m.entry2_id, m.score2)}
          ${competitionResultStatusHtml(m, { compact: true })}
        </div>`;
    };
    // The bronze match shares the last round but gets its own caption.
    const isThirdPlace = (m) => m.round === t.total_rounds && m.position === 1;
    const rounds = [];
    for (let r = 1; r <= t.total_rounds; r++) {
      const ms = t.matches.filter((m) => m.round === r && !isThirdPlace(m));
      const third = r === t.total_rounds ? t.matches.find(isThirdPlace) : null;
      rounds.push(`
        <div class="bracket-round">
          <div class="bracket-round-title">${tournamentRoundLabel(r, t.total_rounds)}</div>
          <div class="bracket-round-matches">
          ${ms.map(matchHtml).join('')}
          ${third ? `<div><div class="bracket-round-title" style="margin:8px 0 6px">🥉 3rd place</div>${matchHtml(third)}</div>` : ''}
          </div>
        </div>`);
    }
    return `<div class="bracket">${rounds.join('')}</div>`;
  }

  function roundRobinHtml(t) {
    const entries = {};
    t.entries.forEach((en) => { entries[en.id] = en; });
    let html = '';
    if (t.standings && t.standings.length) {
      html += '<div class="section-label">Standings</div><div class="card" style="padding:6px 14px">';
      html += t.standings.map((row, i) => `
        <div class="row" style="padding:8px 0;border-bottom:${i === t.standings.length - 1 ? 'none' : '1px solid var(--line)'}">
          <div class="rank-num">${i + 1}</div>
          <div class="row-main">
            <div class="row-title" style="font-size:14px">${esc(row.entry.name)}${t.my_entry_id === row.entry.id ? ' <span class="tag" style="background:var(--green-50);color:var(--green-accent)">you</span>' : ''}</div>
            <div class="row-sub">${row.wins}W – ${row.losses}L · ${row.point_diff >= 0 ? '+' : ''}${row.point_diff} pts</div>
          </div>
          ${i === 0 && t.status === 'completed' ? '<span style="font-size:20px">👑</span>' : ''}
        </div>`).join('');
      html += '</div>';
    }
    const roundsSeen = [...new Set(t.matches.map((m) => m.round))].sort((a, b) => a - b);
    html += '<div class="section-label">Matches</div>';
    roundsSeen.forEach((r) => {
      const ms = t.matches.filter((m) => m.round === r);
      if (roundsSeen.length > 1) html += `<div class="row-sub" style="margin:4px 2px">Round ${r}</div>`;
      ms.forEach((m) => {
        const e1 = entries[m.entry1_id], e2 = entries[m.entry2_id];
        const result = normalizeCompetitionResult(m);
        const done = result.confirmed || result.state === 'bye';
        const ready = m.entry1_id != null && m.entry2_id != null && t.status === 'active' && result.state === 'unreported';
        html += `
          <div class="card row competition-match-card ${ready ? 'bm-ready' : ''}" data-tmatch="${m.id}" data-result-match="${m.id}" data-match-key="${m.id}" style="padding:10px 14px">
            <div class="row-main">
              <div class="row-title" style="font-size:14px">
                <span class="${done && m.winner_entry_id === m.entry1_id ? 'rr-win' : ''}">${e1 ? esc(e1.name) : '—'}</span>
                <span style="opacity:.55;font-weight:400"> vs </span>
                <span class="${done && m.winner_entry_id === m.entry2_id ? 'rr-win' : ''}">${e2 ? esc(e2.name) : '—'}</span>
              </div>
              ${competitionResultStatusHtml(m)}
            </div>
            <div class="stat-value" style="font-size:15px">${m.score1 != null && m.score2 != null ? `${m.score1}–${m.score2}` : ''}</div>
          </div>`;
      });
    });
    return html;
  }

  async function openTournamentScreen(tournamentId, requestedMatchId = null) {
    const route = { kind: 'tournament', id: tournamentId };
    const routeLoad = beginRoutedOverlayLoad(route);
    let t;
    try { t = await api(`/tournaments/${tournamentId}`); } catch (e) {
      if (!routedOverlayLoadIsCurrent(routeLoad)) return;
      toast(e.message);
      clearDeadDeepLink(overlayRouteHash(route));
      return;
    }
    if (!routedOverlayLoadIsCurrent(routeLoad)) return;

    const box = openModal(modalHead('Tournament'), { route, label: 'Tournament' });
    const content = box.querySelector('.modal');
    let deepLinkOpened = false;
    const runMutation = async (request) => {
      if (box.dataset.competitionMutation === 'true') throw new Error('Another update is already in progress.');
      box.dataset.competitionMutation = 'true';
      try { return await request(); }
      finally { box.dataset.competitionMutation = 'false'; }
    };

    const refresh = async ({ force = false, data = null } = {}) => {
      if (!force && !competitionOverlayCanRefresh(box)) return null;
      try {
        const previous = JSON.stringify(t);
        const fresh = data || await api(`/tournaments/${tournamentId}`);
        t = fresh;
        if (currentOverlayEntry()?.el === box && (force || JSON.stringify(fresh) !== previous)) {
          render(fresh, { preserve: true });
        }
        return fresh;
      } catch { return null; }
    };
    const openMatch = (match) => openCompetitionResultSheet('tournament', t, match, {
      setMutating: (busy) => { box.dataset.competitionMutation = String(busy); },
      adoptFresh: (fresh, { render: shouldRender = true } = {}) => {
        t = fresh;
        if (shouldRender && currentOverlayEntry()?.el === box) render(fresh, { preserve: true });
      },
      refresh,
    });

    const render = (data, { preserve = false } = {}) => {
      t = data;
      const snapshot = preserve ? captureCompetitionViewState(box) : null;
      const isDoubles = t.event_type === 'doubles';
      const unitLabel = isDoubles ? 'team' : 'player';
      const meta = [
        t.court ? `${t.court.name}${t.court.city ? ', ' + t.court.city : ''}` : '',
        // Same honesty rule as the cards: a finished tournament shouldn't
        // advertise its (possibly still future) start time.
        t.status === 'completed'
          ? `🏁 Ended${t.completed_at ? ` ${new Date(t.completed_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}` : ''}`
          : fmtDateTime(t.starts_at),
        `${T_FORMAT_LABEL[t.format] || t.format} · ${isDoubles ? 'Doubles' : 'Singles'}${t.ranked ? ' · ⚡ Ranked' : ''}`,
      ].filter(Boolean);

      let body = `
        ${modalHead(t.name)}
        <div class="row-sub" style="margin:-6px 0 6px">${meta.map(esc).join(' · ')}</div>
        <div style="margin-bottom:12px">${tournamentStatusChip(t)}${t.club_name ? ` <span class="tag" style="margin:0 0 0 4px">🏛 ${esc(t.club_name)}</span>` : ''}</div>
        ${t.description ? `<div class="row-sub" style="margin-bottom:12px">${esc(t.description)}</div>` : ''}`;

      if (t.status === 'completed' && t.champion) {
        body += `
          <div class="card" style="text-align:center;padding:18px;background:var(--violet-50);border:1px solid var(--violet-200)">
            <div style="font-size:34px">👑</div>
            <div style="font-weight:800;font-size:17px;color:var(--violet-700)">${esc(t.champion.name)}</div>
            <div class="row-sub">Tournament champion${isDoubles ? 's' : ''}</div>
          </div>`;
      }

      const checkinOpen = Date.now() >= new Date(t.starts_at).getTime() - 24 * 3600e3
        && (t.status === 'registration' || t.status === 'active');
      const myEntry = t.entries.find((en) => en.id === t.my_entry_id);
      const hereTag = (en) => (en.checked_in
        ? ' <span class="tag" style="background:var(--green-100);color:var(--green-ink)">🙋 here</span>' : '');
      const checkinButton = (myEntry && checkinOpen && !myEntry.checked_in)
        ? '<button class="btn btn-primary btn-block" id="td-checkin" style="margin-top:12px">🙋 Check in — we\'re here</button>' : '';

      if (t.status === 'registration') {
        body += `<div class="section-label">Entries (${t.entry_count}/${t.max_entries})</div>`;
        body += t.entries.length ? t.entries.map((en) => `
          <div class="card row" style="padding:10px 14px">
            ${avatarHtml(en.players[0] || {}, 'sm')}
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(en.name)}${hereTag(en)}</div>
              <div class="row-sub">${en.rating} rating</div>
            </div>
            ${t.is_organizer ? `<button class="btn btn-secondary btn-sm" data-remove-entry="${en.id}" aria-label="Remove entry">Remove</button>` : ''}
          </div>`).join('')
          : `<div class="empty-state" style="padding:16px">No ${unitLabel}s yet — be the first to register!</div>`;
        body += checkinButton;

        if (!t.my_entry_id && t.entry_count < t.max_entries) {
          body += isDoubles
            ? `<div class="form-field" style="margin-top:12px"><label>Your partner (must be a friend)</label><select id="td-partner"><option value="">Choose a partner…</option></select></div>
               <button class="btn btn-primary btn-block" id="td-register">🏆 Register our team</button>`
            : '<button class="btn btn-primary btn-block" id="td-register" style="margin-top:12px">🏆 Register</button>';
        } else if (t.my_entry_id) {
          // Entry owner of a doubles team can swap partners while registration is open.
          if (isDoubles && myEntry && myEntry.players[0] && myEntry.players[0].id === state.me.id) {
            body += `
              <div class="form-field" style="margin-top:12px"><label>Swap partner (must be a friend)</label>
                <select id="td-newpartner"><option value="">Keep ${esc(myEntry.players[1] ? myEntry.players[1].display_name : 'current partner')}</option></select>
              </div>
              <button class="btn btn-secondary btn-block" id="td-swap">🔁 Change partner</button>`;
          }
          body += '<button class="btn btn-secondary btn-block" id="td-withdraw" style="margin-top:12px">Withdraw my entry</button>';
        }
        body += '<button class="btn btn-secondary btn-block" id="td-share" style="margin-top:8px">📤 Share — invite players</button>';
        if (t.my_entry_id || t.is_organizer) {
          body += '<button class="btn btn-secondary btn-block" id="td-ics" style="margin-top:8px">📅 Add to calendar</button>';
          body += `<button class="btn btn-secondary btn-block" id="td-chat" style="margin-top:8px">💬 Tournament chat${t.chat_unread ? ` <span class="tag live" style="margin:0 0 0 6px">${t.chat_unread > 9 ? '9+' : t.chat_unread} new</span>` : ''}</button>`;
        }
        if (t.is_organizer) {
          body += `
            <div class="section-label" style="margin-top:16px">Organizer</div>
            <button class="btn btn-primary btn-block" id="td-start" ${t.entry_count < 2 ? 'disabled' : ''}>▶️ Start tournament${t.entry_count < 2 ? ' (need 2+ entries)' : ''}</button>
            <button class="btn btn-secondary btn-block" id="td-edit" style="margin-top:8px">✏️ Edit details</button>
            <button class="btn btn-secondary btn-block" id="td-cancel" style="margin-top:8px">Cancel tournament</button>`;
        }
      } else if (t.status !== 'cancelled') {
        body += competitionActionNeededHtml('tournament', t);
        body += t.format === 'round_robin' ? roundRobinHtml(t) : bracketHtml(t);
        if (t.status === 'active') {
          body += '<div class="competition-progression-note">Open any match for its result status and activity. Bracket progression waits for confirmation.</div>';
        }
        if (t.my_entry_id || t.is_organizer) {
          body += `<button class="btn btn-secondary btn-block" id="td-chat" style="margin-top:12px">💬 Tournament chat${t.chat_unread ? ` <span class="tag live" style="margin:0 0 0 6px">${t.chat_unread > 9 ? '9+' : t.chat_unread} new</span>` : ''}</button>`;
        }
        if (t.status === 'active' && t.is_organizer) {
          body += '<button class="btn btn-secondary btn-block" id="td-edit" style="margin-top:8px">✏️ Edit details</button>';
          body += '<button class="btn btn-secondary btn-block" id="td-cancel" style="margin-top:8px">Cancel tournament</button>';
        }
        body += checkinButton;
        body += `<div class="section-label" style="margin-top:14px">${isDoubles ? 'Teams' : 'Players'}</div>`;
        body += t.entries.map((en) => `
          <div class="card row" style="padding:8px 14px">
            ${avatarHtml(en.players[0] || {}, 'sm')}
            <div class="row-main"><div class="row-title" style="font-size:14px">${en.seed ? `<span class="bm-seed" style="margin-right:4px">${en.seed}</span>` : ''}${esc(en.name)}${hereTag(en)}</div></div>
            <div class="row-sub">${en.rating}</div>
          </div>`).join('');
      } else {
        body += '<div class="empty-state" style="padding:16px">This tournament was cancelled.</div>';
      }

      content.innerHTML = body;
      setDialogLabel(content, 'Tournament');

      // --- actions ---
      content.querySelector('#td-chat')?.addEventListener('click', () => openTournamentChat(t));
      content.querySelector('#td-ics')?.addEventListener('click', () => downloadTournamentIcs(t));
      content.querySelector('#td-edit')?.addEventListener('click', () => openEditTournamentSheet(t, render));
      content.querySelector('#td-checkin')?.addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        try {
          render(await runMutation(() => api(`/tournaments/${t.id}/checkin`, { method: 'POST' })));
          toast("Checked in — see you on court! 🙋");
        } catch (err) { toast(err.message); btn.disabled = false; }
      });
      content.querySelector('#td-share')?.addEventListener('click', async () => {
        const spots = t.max_entries - t.entry_count;
        const text = `🏆 ${t.name} — ${T_FORMAT_LABEL[t.format] || t.format} ${t.event_type} tournament at ${t.court ? t.court.name : 'the court'} on ${fmtDateTime(t.starts_at)}. ${spots} spot${spots === 1 ? '' : 's'} left — register in Third Shot!`;
        const url = `${location.origin}/t/${t.id}`; // short link → OG preview in chat apps
        try {
          if (navigator.share) await navigator.share({ title: 'Third Shot', text, url });
          else { await navigator.clipboard.writeText(`${text} ${url}`); toast('Copied to share 📋'); }
        } catch { /* user cancelled */ }
      });
      content.querySelector('#td-register')?.addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        const payload = {};
        if (isDoubles) {
          const pid = Number(content.querySelector('#td-partner')?.value || 0);
          if (!pid) { toast('Choose your doubles partner'); return; }
          payload.partner_id = pid;
        }
        btn.disabled = true;
        try {
          render(await runMutation(() => api(`/tournaments/${t.id}/register`, { method: 'POST', body: JSON.stringify(payload) })));
          toast("You're in! 🏆");
        } catch (err) { toast(err.message); btn.disabled = false; }
      });
      content.querySelector('#td-swap')?.addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        const pid = Number(content.querySelector('#td-newpartner')?.value || 0);
        if (!pid) { toast('Pick the friend to swap in'); return; }
        btn.disabled = true;
        try {
          render(await runMutation(() => api(`/tournaments/${t.id}/register`, { method: 'PATCH', body: JSON.stringify({ partner_id: pid }) })));
          toast('Partner updated 🔁');
        } catch (err) { toast(err.message); btn.disabled = false; }
      });
      content.querySelector('#td-withdraw')?.addEventListener('click', async () => {
        if (!confirm('Withdraw from this tournament?')) return;
        try { render(await runMutation(() => api(`/tournaments/${t.id}/register`, { method: 'DELETE' }))); toast('Withdrawn'); }
        catch (err) { toast(err.message); }
      });
      content.querySelectorAll('[data-remove-entry]').forEach((btn) => btn.addEventListener('click', async () => {
        if (!confirm('Remove this entry?')) return;
        try { render(await runMutation(() => api(`/tournaments/${t.id}/entries/${btn.dataset.removeEntry}`, { method: 'DELETE' }))); }
        catch (err) { toast(err.message); }
      }));
      content.querySelector('#td-start')?.addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        if (!confirm(`Start the tournament with ${t.entry_count} entries? Registration closes and the bracket is generated.`)) return;
        btn.disabled = true;
        try { render(await runMutation(() => api(`/tournaments/${t.id}/start`, { method: 'POST' }))); toast('Bracket is live! 🏁'); }
        catch (err) { toast(err.message); btn.disabled = false; }
      });
      content.querySelector('#td-cancel')?.addEventListener('click', async () => {
        if (!confirm('Cancel this tournament? Everyone registered will be notified.')) return;
        try {
          await runMutation(() => api(`/tournaments/${t.id}/cancel`, { method: 'POST' }));
          toast('Tournament cancelled');
          closeModal(box);
          if (state.tab === 'play' && state.playSeg === 'brackets') renderPlay();
        } catch (err) { toast(err.message); }
      });
      content.querySelectorAll('[data-result-match]').forEach((card) => {
        makePressable(card, () => {
          const match = t.matches.find((item) => item.id === Number(card.dataset.resultMatch));
          if (match) openMatch(match);
        });
      });

      // Populate the doubles partner pickers (register + swap) with friends
      // not already entered.
      const partnerSels = [
        content.querySelector('#td-partner'),
        content.querySelector('#td-newpartner'),
      ].filter(Boolean);
      if (partnerSels.length) {
        api('/friends').then((data) => {
          const taken = new Set();
          t.entries.forEach((en) => en.players.forEach((p) => taken.add(p.id)));
          partnerSels.forEach((sel) => {
            (data.friends || []).forEach((f) => {
              if (taken.has(f.id)) return;
              const opt = document.createElement('option');
              opt.value = f.id;
              opt.textContent = f.display_name;
              sel.appendChild(opt);
            });
          });
        }).catch(() => {});
      }
      if (snapshot) restoreCompetitionViewState(box, snapshot);
      if (requestedMatchId && !deepLinkOpened) {
        deepLinkOpened = true;
        const match = (t.matches || []).find((item) => item.id === Number(requestedMatchId));
        const card = content.querySelector(`[data-result-match="${Number(requestedMatchId)}"]`);
        if (match && card) {
          card.classList.add('competition-match-highlight');
          const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
          card.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' });
          queueMicrotask(() => openMatch(match));
        } else {
          toast('That tournament match is no longer available.');
        }
      }
    };

    render(t);

    // Live sync while open — registrations and scores land from other phones.
    const poll = setInterval(async () => {
      if (!document.body.contains(box)) { clearInterval(poll); return; }
      if (document.hidden || state.connectionState === 'offline') return;
      if (t.status !== 'active' && t.status !== 'registration') return;
      if (!competitionOverlayCanRefresh(box)) return;
      await refresh();
    }, 8000);
    box._cleanupFns?.push(() => clearInterval(poll));
  }

  function openEditTournamentSheet(t, onSaved) {
    const start = new Date(t.starts_at);
    const pad = (n) => String(n).padStart(2, '0');
    const whenVal = `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}T${pad(start.getHours())}:${pad(start.getMinutes())}`;
    const inRegistration = t.status === 'registration';
    const modal = openModal(`
      ${modalHead('Edit tournament')}
      <div class="form-field">
        <label>Name</label>
        <input type="text" id="te-name" maxlength="120" value="${esc(t.name)}" />
      </div>
      <div class="form-field">
        <label>Starts</label>
        <input type="datetime-local" id="te-when" value="${whenVal}" />
        <div class="row-sub" style="margin-top:4px">Rescheduling notifies everyone registered.</div>
      </div>
      ${inRegistration ? `
      <div class="form-field">
        <label>Max entries</label>
        <select id="te-max">
          ${[4, 8, 16, 32].map((n) => `<option ${n === t.max_entries ? 'selected' : ''} ${n < t.entry_count ? 'disabled' : ''}>${n}</option>`).join('')}
        </select>
      </div>` : ''}
      <div class="form-field">
        <input type="text" id="te-desc" maxlength="200" placeholder="Details (optional)" value="${esc(t.description || '')}" />
      </div>
      <button class="btn btn-primary btn-block" id="te-save" style="padding:15px">Save changes</button>
    `);
    modal.querySelector('#te-save').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      const name = modal.querySelector('#te-name').value.trim();
      const whenRaw = modal.querySelector('#te-when').value;
      if (name.length < 3) { toast('Name needs 3+ characters'); return; }
      if (!whenRaw) { toast('Pick a start time'); return; }
      const payload = {
        name,
        starts_at: new Date(whenRaw).toISOString(),
        description: modal.querySelector('#te-desc').value.trim(),
      };
      if (inRegistration) payload.max_entries = Number(modal.querySelector('#te-max').value);
      btn.disabled = true;
      try {
        const fresh = await api(`/tournaments/${t.id}`, { method: 'PATCH', body: JSON.stringify(payload) });
        closeModal(modal);
        toast('Tournament updated ✏️');
        onSaved(fresh);
      } catch (err) { toast(err.message); btn.disabled = false; }
    });
  }

  async function openTournamentChat(t) {
    const modalLoad = beginRoutedOverlayLoad(null);
    let data;
    try { data = await api(`/tournaments/${t.id}/chat`); } catch (e) {
      if (!routedOverlayLoadIsCurrent(modalLoad)) return;
      toast(e.message); return;
    }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;
    refreshMe(); // keep the global Community badge exact outside Inbox

    const modal = openModal(`
      <div class="thread">
        <div class="thread-head">
          <button class="modal-close" style="font-size:18px">‹</button>
          <span style="font-size:22px">🏆</span>
          <div class="row-main">
            <div class="row-title">Tournament chat</div>
            <div class="row-sub">${esc(data.tournament.name)} — players & organizer only</div>
          </div>
        </div>
        <div class="thread-msgs" id="tch-msgs" role="log" aria-live="polite" aria-relevant="additions" aria-label="Tournament conversation"></div>
        <form class="thread-input" id="tch-form">
          <input type="text" id="tch-text" placeholder="Message the tournament…" autocomplete="off" maxlength="2000" />
          <button type="submit" aria-label="Send">➤</button>
        </form>
      </div>
    `, { chat: true });

    const msgsEl = modal.querySelector('#tch-msgs');
    const input = modal.querySelector('#tch-text');
    const chatUX = bindChatContinuity(modal, msgsEl, input, `tournament:${t.id}`);
    let lastId = 0;
    const renderMsgs = (items, append, { forceBottom = false, newMessages = false } = {}) => {
      const batch = prepareChatRenderBatch(msgsEl, items, append);
      if (batch.newestId) lastId = Math.max(lastId, batch.newestId);
      items = batch.items;
      if (append && !items.length) return;
      const snapshot = chatUX.captureScroll();
      const html = items.map((m) => {
        const mine = m.sender_id === state.me.id;
        return `
        <div data-message-id="${m.id}" style="display:flex;gap:8px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%">
          ${mine ? '' : `<div class="avatar sm" style="background:${esc(m.sender_color)}">${esc(initials(m.sender_name))}</div>`}
          <div class="bubble ${mine ? 'me' : 'them'}" style="max-width:100%" ${mine ? `data-del-msg="${m.id}" title="Tap to delete"` : `data-room-heart="${m.id}" title="Tap to ❤️"`}>${m.heart_count ? `<span class="bubble-heart" data-heart-badge>❤️${m.heart_count > 1 ? ' ' + m.heart_count : ''}</span>` : ''}
            ${mine ? '' : `<div style="font-size:11px;font-weight:700;opacity:.75;margin-bottom:2px">${esc(m.sender_name)}</div>`}
            ${m.has_image ? `<div data-img-id="${m.id}" style="min-height:60px;min-width:120px;margin-bottom:${m.body ? '6px' : '0'}">⏳</div>` : ''}
            ${esc(m.body)}
            <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
          </div>
        </div>`;
      }).join('');
      if (append && !msgsEl.querySelector('.empty-state')) msgsEl.insertAdjacentHTML('beforeend', html);
      else if (append) msgsEl.innerHTML = html;
      else msgsEl.innerHTML = html || '<div class="empty-state" style="padding:20px">Coordinate the day — “what time are check-ins?”, “courts 3 & 4” 🏆</div>';
      chatUX.restoreScroll(snapshot, { forceBottom, newMessageCount: newMessages ? items.length : 0 });
      hydrateChatImages(msgsEl, chatUX);
    };
    renderMsgs(data.items, false, { forceBottom: true });
    chatUX.activateOutbox((message) => renderMsgs([message], true, { forceBottom: true }));
    addPhotoToComposer(modal, '#tch-form', '#tch-text', chatUX);

    const pollTimer = setInterval(async () => {
      if (!document.body.contains(msgsEl)) { clearInterval(pollTimer); return; }
      if (document.hidden || state.connectionState === 'offline') return;
      try {
        const fresh = await api(`/tournaments/${t.id}/chat?since_id=${lastId}`);
        if (fresh.items.length) renderMsgs(fresh.items, true, { newMessages: true });
        applyRoomHearts(msgsEl, fresh.heart_counts);
      } catch { /* offline */ }
    }, 5000);

    modal.querySelector('#tch-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = input.value.trim();
      if (!body) return;
      try {
        await chatUX.send(body);
      } catch (err) { toast(err.message); }
    });
  }

  // ---------- Chat & Friends ----------
  function setupChat() {
    $('#chat-segments').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      const changed = state.chatSeg !== btn.dataset.seg;
      state.chatSeg = btn.dataset.seg;
      document.querySelectorAll('#chat-segments button').forEach((b) => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-selected', String(active));
      });
      renderChat({ reuseFresh: !changed });
    });
  }

  function inboxMessagePreview(item) {
    const message = item.lastMessage;
    if (!message) return item.emptyText || 'Start the conversation';
    const body = message.body ? esc(message.body.slice(0, 72))
      : message.has_image ? '📷 Photo' : 'New message';
    if (message.sender_id === state.me.id) return `You: ${body}`;
    if (item.kind === 'dm') return body;
    const sender = esc((message.sender_name || 'Player').split(' ')[0]);
    return `${sender}: ${body}`;
  }

  function competitionInboxContext(room) {
    const label = {
      registration: 'Signups open', active: 'In progress', upcoming: 'Upcoming',
      awaiting_confirmation: 'Score awaiting confirmation', completed: 'Completed',
    }[room.status] || 'Competition chat';
    const details = [label];
    if (room.event_at) details.push(fmtDateTime(room.event_at));
    if (room.court_name && !room.title.includes(room.court_name)) details.push(esc(room.court_name));
    return details.join(' · ');
  }

  function universalInboxHtml(data, rooms, clubs, competitions) {
    const items = [];
    (data.items || []).forEach((chat) => items.push({
      kind: 'dm', id: chat.user.id, title: chat.user.display_name,
      iconHtml: avatarHtml(chat.user, '', 'span'), lastMessage: chat.last_message,
      unread: chat.unread || 0, emptyText: 'Send a message',
    }));
    (clubs.items || []).forEach((club) => items.push({
      kind: 'club', id: club.id, title: club.name,
      iconHtml: '<span class="inbox-room-icon">🏛</span>', lastMessage: club.last_message,
      unread: club.unread || 0,
      emptyText: club.announcement ? `📣 ${esc(club.announcement.slice(0, 72))}`
        : `${club.member_count} member${club.member_count === 1 ? '' : 's'}${club.home_court_name ? ` · ${esc(club.home_court_name)}` : ''}`,
    }));
    (rooms.items || []).forEach((room) => items.push({
      kind: 'court', id: room.court.id, title: room.court.name,
      iconHtml: '<span class="inbox-room-icon">🏓</span>', lastMessage: room.last_message,
      unread: room.unread || 0, emptyText: 'Court room',
    }));
    (competitions.items || []).forEach((room) => items.push({
      kind: room.kind, id: room.id, title: room.title,
      iconHtml: `<span class="inbox-room-icon">${room.kind === 'game' ? '🏓' : room.kind === 'tournament' ? '🏆' : '📦'}</span>`,
      lastMessage: room.last_message, unread: room.unread || 0,
      emptyText: competitionInboxContext(room), eventAt: room.event_at,
    }));

    const recent = items.filter((item) => item.lastMessage)
      .sort((a, b) => b.lastMessage.id - a.lastMessage.id);
    const ready = items.filter((item) => !item.lastMessage)
      .sort((a, b) => {
        if (a.eventAt && b.eventAt) return new Date(a.eventAt) - new Date(b.eventAt);
        return a.eventAt ? -1 : b.eventAt ? 1 : a.title.localeCompare(b.title);
      });
    const kindLabel = { dm: 'Direct', club: 'Club', court: 'Court', game: 'Game', tournament: 'Tournament', league: 'League' };
    const rowHtml = (item, extraClass = '') => {
      const attention = item.unread
        ? `, ${item.unread} unread message${item.unread === 1 ? '' : 's'}` : '';
      return `
      <button type="button" class="card row inbox-row ${extraClass}" data-inbox-kind="${item.kind}" data-inbox-id="${item.id}" data-inbox-title="${esc(item.title)}" data-unread="${item.unread}" aria-label="${esc(item.title)}, ${kindLabel[item.kind]} chat${attention}">
        ${item.iconHtml}
        <span class="row-main">
          <span class="row-title" style="display:block">${esc(item.title)}<span class="inbox-kind">${kindLabel[item.kind]}</span></span>
          <span class="row-sub" style="display:block">${inboxMessagePreview(item)}</span>
        </span>
        ${item.unread ? `<span class="badge" style="position:static">${item.unread > 99 ? '99+' : item.unread}</span>`
          : item.lastMessage ? `<span class="row-sub inbox-time">${fmtTimeShort(item.lastMessage.created_at)}</span>` : '<span class="chev">›</span>'}
      </button>`;
    };

    let html = '';
    if (recent.length) {
      html += '<div class="section-label" style="margin-top:4px">Recent conversations</div>';
      html += recent.map((item) => rowHtml(item)).join('');
    }
    if (ready.length) {
      html += `<div class="section-label" style="margin-top:${recent.length ? '18px' : '4px'}">Ready to coordinate</div>`;
      html += ready.map((item, index) => rowHtml(item, index >= 8 ? 'inbox-ready-extra hidden' : '')).join('');
      if (ready.length > 8) {
        html += `<button type="button" class="btn btn-secondary btn-block" id="inbox-show-ready">Show ${ready.length - 8} more active chats</button>`;
      }
    }
    if (!items.length) {
      html = `<div class="empty-state"><span class="big">💬</span><b>Your conversations will live here.</b><br>Find your crew or start a game to open a shared chat.
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px">
          <button class="btn btn-secondary" data-goto="chat-friends">🤝 Find friends</button>
          <button class="btn btn-primary" data-goto="new-game">🏓 Start a game</button>
        </div></div>`;
    }
    html += `<div class="section-label">Build your community</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <button class="btn btn-secondary" id="club-find">🔎 Find clubs</button>
        <button class="btn btn-secondary" id="club-new">＋ Start a club</button>
      </div>`;
    return html;
  }

  async function renderChat({ reuseFresh = false, useCachedData = false } = {}) {
    const seg = state.chatSeg;
    const liveEl = $('#chat-content');
    const viewKey = `${state.me?.id || 'signed-out'}:chat:${seg}`;
    if (reuseFresh && viewIsFresh(liveEl, viewKey)) return;
    const renderSeq = ++state.chatRenderSeq;
    const hadUsableContent = beginViewRender(liveEl, viewKey, 5);
    const el = document.createElement('div');
    const commit = () => {
      if (renderSeq !== state.chatRenderSeq || state.chatSeg !== seg) return false;
      commitViewRender(liveEl, el, viewKey);
      return true;
    };
    try {
      if (seg === 'chats') {
        const [data, rooms, clubs, competitions] = await Promise.all([
          api('/chat'),
          api('/chat/courts'),
          api('/clubs/mine'),
          api('/chat/competitions'),
        ]);
        if (renderSeq !== state.chatRenderSeq || state.chatSeg !== seg) return;
        state.communityRoomUnread = [rooms, clubs, competitions]
          .flatMap((group) => group.items || [])
          .reduce((total, item) => total + Number(item.unread || 0), 0);
        renderBadges();
        el.innerHTML = universalInboxHtml(data, rooms, clubs, competitions);
        el.querySelectorAll('[data-inbox-kind]').forEach((row) => row.addEventListener('click', async () => {
          if (row.disabled) return;
          row.disabled = true;
          const kind = row.dataset.inboxKind;
          const id = Number(row.dataset.inboxId);
          try {
            if (kind === 'dm') await openThread(id);
            else if (kind === 'court') await openCourtChat({ id, name: row.dataset.inboxTitle });
            else if (kind === 'club') await openClubScreen(id);
            else if (kind === 'game') await openGameChat({ id });
            else if (kind === 'tournament') await openTournamentChat({ id });
            else if (kind === 'league') await openLeagueChat({ id, name: row.dataset.inboxTitle });
          } finally {
            row.disabled = false;
            // The room GET is the authoritative read action. Re-fetching the
            // inbox keeps counts exact on success and unchanged on failure.
            if (state.tab === 'chat' && state.chatSeg === 'chats') renderChat();
          }
        }));
        el.querySelector('#inbox-show-ready')?.addEventListener('click', (event) => {
          const firstRevealed = el.querySelector('.inbox-ready-extra');
          el.querySelectorAll('.inbox-ready-extra').forEach((row) => row.classList.remove('hidden'));
          event.currentTarget.remove();
          firstRevealed?.focus({ preventScroll: true });
        });
        el.querySelector('#club-find')?.addEventListener('click', openFindClubsSheet);
        el.querySelector('#club-new')?.addEventListener('click', openCreateClubSheet);
      } else if (seg === 'nearby') {
        await renderNearbyPlayers(el);
      } else {
        await renderFriends(el, { useCachedData });
      }
      commit();
    } catch (e) {
      if (renderSeq !== state.chatRenderSeq || state.chatSeg !== seg) return;
      if (hadUsableContent) {
        retainViewAfterError(liveEl, `${e.message} Showing your last update.`, () => renderChat());
      } else {
        renderError(el, e.message, () => renderChat());
        commit();
      }
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
            ? `📍 At ${esc(p.checked_in_court.name)}${p.checked_in_court.looking_for_game ? ' · <b style="color:var(--green-accent)">wants to play!</b>' : ''}`
            : `${skillLabel(p.skill_level)} · ${p.rating} · ${p.distance_miles} mi away`;
          if (p.active_now) sub = '<b style="color:var(--green-accent)">🟢 active now</b> · ' + sub;
          if (availabilityOverlap(state.me.availability, p.availability)) {
            sub += ' · <b style="color:var(--green-accent)">⏰ plays your times</b>';
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

  async function renderFriends(el, { useCachedData = false } = {}) {
    const loc = areaLatLng();
    let friendBundle = useCachedData && state.chatFriendsCache;
    if (!friendBundle) {
      friendBundle = await Promise.all([
        api('/friends'),
        api(`/games/results?lat=${loc.lat}&lng=${loc.lng}`).catch(() => ({ items: [] })),
        api('/friends/digest').catch(() => null),
        api('/friends/suggestions').catch(() => ({ items: [] })),
      ]);
      state.chatFriendsCache = friendBundle;
    }
    const [data, results, digest, suggestions] = friendBundle;
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

    // Weekly digest — what your friends got up to in the last 7 days.
    if (digest && (digest.games || digest.checkins)) {
      const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
      const bits = [];
      if (digest.games) bits.push(`${plural(digest.games, 'game')} by ${plural(digest.friends_played, 'friend')}`);
      if (digest.checkins) bits.push(plural(digest.checkins, 'court check-in'));
      html += '<div class="section-label">📬 This week among friends</div>';
      html += `<div class="card">
        <div class="row-sub">${bits.join(' · ')}</div>
        ${(digest.top || []).map((t) => `
          <div class="row" data-view-user="${t.id}" style="cursor:pointer;padding:8px 0 0">
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(t.display_name)}</div>
              <div class="row-sub">${plural(t.games, 'game')}${t.wins + t.losses ? ` · ${t.wins}–${t.losses}` : ''}</div>
            </div>
          </div>`).join('')}
      </div>`;
    }

    // "Who plays when" — filter friends by their usual-play slots.
    const SLOT_FILTERS = [
      ['', 'All'],
      ['sat-am,sat-pm', 'Sat'],
      ['sun-am,sun-pm', 'Sun'],
      ['mon-eve,tue-eve,wed-eve,thu-eve,fri-eve', 'Weekday eve'],
      ['sat-am,sun-am,mon-am,tue-am,wed-am,thu-am,fri-am', 'Mornings'],
    ];
    const slotFilter = state.friendSlotFilter || '';
    const wantedSlots = slotFilter ? slotFilter.split(',') : null;
    const shownFriends = wantedSlots
      ? data.friends.filter((f) => (f.availability || []).some((s) => wantedSlots.includes(s)))
      : data.friends;

    html += `<div class="section-label">Friends (${data.friends.length})</div>`;
    if (data.friends.length > 1) {
      html += `<div class="quick-times" id="friend-slots" style="margin:0 0 10px">${SLOT_FILTERS
        .map(([v, label]) => `<button type="button" data-slots="${v}" class="${v === slotFilter ? 'active' : ''}">${label}</button>`).join('')}</div>`;
    }
    if (wantedSlots && !shownFriends.length) {
      html += '<div class="empty-state" style="padding:14px">No friends usually play then — try another time.</div>';
    }
    html += shownFriends.length
      ? shownFriends.map((f) => `
          <div class="card row">
            ${avatarHtml(f)}
            <div class="row-main" data-view-user="${f.id}" style="cursor:pointer">
              <div class="row-title">${esc(f.display_name)}</div>
              <div class="row-sub">${f.checked_in_court
                ? `📍 At ${esc(f.checked_in_court.name)}${f.checked_in_court.looking_for_game ? ' · <b style="color:var(--green-accent)">wants to play!</b>' : ''}`
                : `${skillLabel(f.skill_level)} · ${f.rating}`}</div>
            </div>
            ${f.checked_in_court && f.checked_in_court.looking_for_game
              ? `<button class="btn btn-primary btn-sm" data-coming="${f.id}" title="Tell them you're on your way"><svg class="pb-ic"><use href="#pb"/></svg> On my way</button>`
              : `<button class="btn btn-secondary btn-sm" data-invite="${f.id}" data-invite-court="${f.checked_in_court ? f.checked_in_court.id : ''}" data-invite-court-name="${f.checked_in_court ? esc(f.checked_in_court.name) : ''}" title="Schedule a game"><svg class="pb-ic"><use href="#pb"/></svg></button>`}
            <button class="btn btn-secondary btn-sm" data-msg="${f.id}">💬</button>
          </div>`).join('')
      : (wantedSlots ? '' : '<div class="empty-state" style="padding:18px">No friends yet — search above to find players.</div>');

    // People you've actually played with but haven't friended.
    if (suggestions && suggestions.items && suggestions.items.length) {
      html += '<div class="section-label"><svg class="pb-ic"><use href="#pb"/></svg> Players you\'ve played with</div>';
      html += suggestions.items.map((s) => `
        <div class="card row">
          ${avatarHtml(s)}
          <div class="row-main" data-view-user="${s.id}" style="cursor:pointer">
            <div class="row-title">${esc(s.display_name)}</div>
            <div class="row-sub">${s.games_together} game${s.games_together === 1 ? '' : 's'} together · ${skillLabel(s.skill_level)}</div>
          </div>
          <button class="btn btn-primary btn-sm" data-add-friend="${s.id}">＋ Add</button>
        </div>`).join('');
    }

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
    el.querySelector('#friend-slots')?.addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      state.friendSlotFilter = btn.dataset.slots;
      renderChat({ useCachedData: true });
    });
    el.querySelectorAll('[data-msg]').forEach((b) => b.addEventListener('click', () => openThread(Number(b.dataset.msg))));
    el.querySelectorAll('[data-invite]').forEach((b) => b.addEventListener('click', () => {
      const court = b.dataset.inviteCourt
        ? { id: Number(b.dataset.inviteCourt), name: b.dataset.inviteCourtName }
        : null;
      openNewGameModal(court, 'casual', false, null, Number(b.dataset.invite));
    }));
    el.querySelectorAll('[data-coming]').forEach((b) => b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        await api(`/players/${b.dataset.coming}/coming`, { method: 'POST' });
        toast("They know you're on your way 🏓");
        b.textContent = '✓ Sent';
      } catch (e) { toast(e.message); b.disabled = false; }
    }));
    // Suggestion "＋ Add" buttons (search results wire their own separately).
    el.querySelectorAll('.card > [data-add-friend], .card [data-add-friend]:not(#friend-search-results [data-add-friend])').forEach((b) => {
      if (b.closest('#friend-search-results')) return;
      b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          await api('/friends/request', { method: 'POST', body: JSON.stringify({ user_id: Number(b.dataset.addFriend) }) });
          toast('Friend request sent! 🤝');
          renderChat();
        } catch (e) { toast(e.message); b.disabled = false; }
      });
    });
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
              <div data-view-user="${u.id}" style="cursor:pointer">${avatarHtml(u)}</div>
              <div class="row-main" data-view-user="${u.id}" style="cursor:pointer">
                <div class="row-title">${esc(u.display_name)}</div>
                <div class="row-sub">${skillLabel(u.skill_level)} · ${u.rating}</div>
              </div>
              ${action}
            </div>`;
        }).join('') || '<div class="empty-state" style="padding:12px">No players found.</div>';
        bindUserButtons(resultsEl);

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
    const modalLoad = beginRoutedOverlayLoad(null);
    let data;
    try { data = await api(`/chat/${userId}`); } catch (e) {
      if (!routedOverlayLoadIsCurrent(modalLoad)) return;
      toast(e.message); return;
    }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;
    state.activeThreadUserId = userId;

    const modal = openModal(`
      <div class="thread">
        <div class="thread-head">
          <button class="modal-close" style="font-size:18px">‹</button>
          ${avatarHtml(data.user, 'sm')}
          <div class="row-main">
            <div class="row-title">${esc(data.user.display_name)}</div>
            <div class="row-sub">${data.user.active_now ? '<b style="color:var(--green-accent)">🟢 active now</b> · ' : ''}${skillLabel(data.user.skill_level)} · ${data.user.rating}</div>
          </div>
        </div>
        <div class="thread-msgs" id="thread-msgs" role="log" aria-live="polite" aria-relevant="additions" aria-label="Conversation with ${esc(data.user.display_name)}"></div>
        <form class="thread-input" id="thread-form">
          <button type="button" id="thread-photo" aria-label="Send a photo" style="background:transparent;font-size:19px;padding:0 2px">📷</button>
          <input type="file" id="thread-file" accept="image/*" class="hidden" />
          <input type="text" id="thread-text" placeholder="Message…" autocomplete="off" maxlength="2000" />
          <button type="submit" aria-label="Send">➤</button>
        </form>
      </div>
    `, { chat: true });

    const msgsEl = modal.querySelector('#thread-msgs');
    const input = modal.querySelector('#thread-text');
    const chatUX = bindChatContinuity(modal, msgsEl, input, `dm:${userId}`);
    let lastId = 0;
    const renderMsgs = (items, append, { forceBottom = false, newMessages = false } = {}) => {
      const batch = prepareChatRenderBatch(msgsEl, items, append);
      if (batch.newestId) lastId = Math.max(lastId, batch.newestId);
      items = batch.items;
      if (append && !items.length) return;
      const snapshot = chatUX.captureScroll();
      const html = items.map((m) => `
        <div class="bubble ${m.sender_id === state.me.id ? 'me' : 'them'}" data-message-id="${m.id}" ${m.sender_id === state.me.id ? `data-del-msg="${m.id}" title="Tap to delete"` : `data-heart-msg="${m.id}" title="Tap to ❤️"`}>
          ${m.has_image ? `<div data-img-id="${m.id}" style="min-height:60px;min-width:120px;margin-bottom:${m.body ? '6px' : '0'}">⏳</div>` : ''}
          ${esc(m.body)}
          <div class="bubble-time">${fmtTimeShort(m.created_at)}${m.sender_id === state.me.id && m.read_at ? ' · <span title="Seen">✓✓</span>' : ''}</div>
          ${m.hearted ? '<span class="bubble-heart">❤️</span>' : ''}
        </div>`).join('');
      if (append && !msgsEl.querySelector('.empty-state')) msgsEl.insertAdjacentHTML('beforeend', html);
      else if (append) msgsEl.innerHTML = html;
      else msgsEl.innerHTML = html || '<div class="empty-state" style="padding:20px">Say hi! 👋</div>';
      chatUX.restoreScroll(snapshot, { forceBottom, newMessageCount: newMessages ? items.length : 0 });
      hydrateChatImages(msgsEl, chatUX);
    };
    // Live ✓✓: flip 'Seen' onto already-rendered bubbles as the partner reads.
    const markSeen = (upTo) => {
      if (!upTo) return;
      msgsEl.querySelectorAll('.bubble.me[data-del-msg]').forEach((b) => {
        const t = b.querySelector('.bubble-time');
        if (Number(b.dataset.delMsg) <= upTo && t && !t.textContent.includes('✓✓')) {
          t.insertAdjacentHTML('beforeend', ' · <span title="Seen">✓✓</span>');
        }
      });
    };
    // Live ❤️: the partner's hearts land on my rendered bubbles each poll.
    const applyHearts = (ids) => {
      if (!ids) return;
      const set = new Set(ids);
      msgsEl.querySelectorAll('.bubble.me[data-del-msg]').forEach((b) => {
        const has = b.querySelector('.bubble-heart');
        const want = set.has(Number(b.dataset.delMsg));
        if (want && !has) b.insertAdjacentHTML('beforeend', '<span class="bubble-heart">❤️</span>');
        if (!want && has) has.remove();
      });
    };
    renderMsgs(data.items, false, { forceBottom: true });
    chatUX.activateOutbox((message) => renderMsgs([message], true, { forceBottom: true }));
    markSeen(data.partner_read_up_to);
    applyHearts(data.hearted_ids);
    refreshMe();

    clearInterval(state.threadPollTimer);
    state.threadPollTimer = setInterval(async () => {
      if (!document.body.contains(msgsEl)) { clearInterval(state.threadPollTimer); return; }
      if (document.hidden || state.connectionState === 'offline') return;
      try {
        const fresh = await api(`/chat/${userId}?since_id=${lastId}`);
        if (fresh.items.length) renderMsgs(fresh.items, true, { newMessages: true });
        applyRoomHearts(msgsEl, fresh.heart_counts);
        markSeen(fresh.partner_read_up_to);
        applyHearts(fresh.hearted_ids);
      } catch { /* offline */ }
    }, 4000);

    modal.querySelector('#thread-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = input.value.trim();
      if (!body) return;
      try {
        await chatUX.send(body);
      } catch (err) { toast(err.message); }
    });
    modal.querySelector('#thread-photo').addEventListener('click', () => {
      const picker = modal.querySelector('#thread-file');
      if (picker.value) picker.value = '';
      picker.click();
    });
    modal.querySelector('#thread-file').addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      try {
        const image = await imageFileToDataUrl(file, 1024);
        const textEl = modal.querySelector('#thread-text');
        const body = textEl.value.trim();
        await chatUX.send(body, { image });
        e.target.value = '';
      } catch (err) {
        if (err.message === 'image_too_large' || err.message === 'bad_image') e.target.value = '';
        toast(err.message === 'image_too_large' ? 'That photo is too large — try a smaller one' : err.message);
      }
    });
  }

  async function openCourtChat(court) {
    const modalLoad = beginRoutedOverlayLoad(null);
    let data;
    try { data = await api(`/courts/${court.id}/chat`); } catch (e) {
      if (!routedOverlayLoadIsCurrent(modalLoad)) return;
      toast(e.message); return;
    }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;
    refreshMe(); // keep the global Community badge exact outside Inbox

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
        <div class="thread-msgs" id="cc-msgs" role="log" aria-live="polite" aria-relevant="additions" aria-label="${esc(court.name)} court conversation"></div>
        <form class="thread-input" id="cc-form">
          <input type="text" id="cc-text" placeholder="Message the court…" autocomplete="off" maxlength="2000" />
          <button type="submit" aria-label="Send">➤</button>
        </form>
      </div>
    `, { chat: true });

    const msgsEl = modal.querySelector('#cc-msgs');
    const input = modal.querySelector('#cc-text');
    const chatUX = bindChatContinuity(modal, msgsEl, input, `court:${court.id}`);
    let lastId = 0;
    const renderMsgs = (items, append, { forceBottom = false, newMessages = false } = {}) => {
      const batch = prepareChatRenderBatch(msgsEl, items, append);
      if (batch.newestId) lastId = Math.max(lastId, batch.newestId);
      items = batch.items;
      if (append && !items.length) return;
      const snapshot = chatUX.captureScroll();
      const html = items.map((m) => {
        const mine = m.sender_id === state.me.id;
        return `
        <div data-message-id="${m.id}" style="display:flex;gap:8px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%">
          ${mine ? '' : `<div class="avatar sm" style="background:${esc(m.sender_color)}">${esc(initials(m.sender_name))}</div>`}
          <div class="bubble ${mine ? 'me' : 'them'}" style="max-width:100%" ${mine ? `data-del-msg="${m.id}" title="Tap to delete"` : `data-room-heart="${m.id}" title="Tap to ❤️"`}>${m.heart_count ? `<span class="bubble-heart" data-heart-badge>❤️${m.heart_count > 1 ? ' ' + m.heart_count : ''}</span>` : ''}
            ${mine ? '' : `<div style="font-size:11px;font-weight:700;opacity:.75;margin-bottom:2px">${esc(m.sender_name)}</div>`}
            ${m.has_image ? `<div data-img-id="${m.id}" style="min-height:60px;min-width:120px;margin-bottom:${m.body ? '6px' : '0'}">⏳</div>` : ''}
            ${esc(m.body)}
            <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
          </div>
        </div>`;
      }).join('');
      if (append && !msgsEl.querySelector('.empty-state')) msgsEl.insertAdjacentHTML('beforeend', html);
      else if (append) msgsEl.innerHTML = html;
      else msgsEl.innerHTML = html || '<div class="empty-state" style="padding:20px">No messages yet — say hi to the court! 👋</div>';
      chatUX.restoreScroll(snapshot, { forceBottom, newMessageCount: newMessages ? items.length : 0 });
      hydrateChatImages(msgsEl, chatUX);
    };
    renderMsgs(data.items, false, { forceBottom: true });
    chatUX.activateOutbox((message) => renderMsgs([message], true, { forceBottom: true }));
    addPhotoToComposer(modal, '#cc-form', '#cc-text', chatUX);

    const pollTimer = setInterval(async () => {
      if (!document.body.contains(msgsEl)) { clearInterval(pollTimer); return; }
      if (document.hidden || state.connectionState === 'offline') return;
      try {
        const fresh = await api(`/courts/${court.id}/chat?since_id=${lastId}`);
        if (fresh.items.length) renderMsgs(fresh.items, true, { newMessages: true });
        applyRoomHearts(msgsEl, fresh.heart_counts);
      } catch { /* offline */ }
    }, 5000);

    modal.querySelector('#cc-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = input.value.trim();
      if (!body) return;
      try {
        await chatUX.send(body);
      } catch (err) { toast(err.message); }
    });
  }

  // ---------- Clubs ----------

  async function openClubScreen(clubId) {
    const routeLoad = beginRoutedOverlayLoad({ kind: 'club', id: clubId });
    let club;
    try { club = await api(`/clubs/${clubId}`); }
    catch (e) {
      if (!routedOverlayLoadIsCurrent(routeLoad)) return;
      toast(e.message); clearDeadDeepLink(`#club/${clubId}`); return;
    }
    if (!routedOverlayLoadIsCurrent(routeLoad)) return;
    if (club.joined) return openClubChat(club, routeLoad);
    return openClubInfo(club, routeLoad);
  }

  async function openClubChat(club, routeLoad = null) {
    routeLoad ||= beginRoutedOverlayLoad({ kind: 'club', id: club.id });
    let data;
    try { data = await api(`/clubs/${club.id}/chat`); } catch (e) {
      if (!routedOverlayLoadIsCurrent(routeLoad)) return;
      toast(e.message); return;
    }
    if (!routedOverlayLoadIsCurrent(routeLoad)) return;
    refreshMe(); // keep the global Community badge exact outside Inbox

    const modal = openModal(`
      <div class="thread">
        <div class="thread-head">
          <button class="modal-close" style="font-size:18px">‹</button>
          <span style="font-size:22px">🏛</span>
          <div class="row-main" id="club-head" style="cursor:pointer">
            <div class="row-title">${esc(club.name)}</div>
            <div class="row-sub">${club.member_count} member${club.member_count === 1 ? '' : 's'} · tap for club info ›</div>
          </div>
        </div>
        ${club.announcement ? `
        <div class="card row" style="margin:8px 12px 0;padding:10px 14px;background:var(--green-50)">
          <span style="font-size:17px">📣</span>
          <div class="row-sub" style="flex:1;color:var(--ink)">${esc(club.announcement)}</div>
        </div>` : ''}
        <div class="thread-msgs" id="clb-msgs" role="log" aria-live="polite" aria-relevant="additions" aria-label="${esc(club.name)} club conversation"></div>
        <form class="thread-input" id="clb-form">
          <input type="text" id="clb-text" placeholder="Message the club…" autocomplete="off" maxlength="2000" />
          <button type="submit" aria-label="Send">➤</button>
        </form>
      </div>
    `, { chat: true, route: { kind: 'club', id: club.id } });

    const msgsEl = modal.querySelector('#clb-msgs');
    const input = modal.querySelector('#clb-text');
    const chatUX = bindChatContinuity(modal, msgsEl, input, `club:${club.id}`);
    let lastId = 0;
    const renderMsgs = (items, append, { forceBottom = false, newMessages = false } = {}) => {
      const batch = prepareChatRenderBatch(msgsEl, items, append);
      if (batch.newestId) lastId = Math.max(lastId, batch.newestId);
      items = batch.items;
      if (append && !items.length) return;
      const snapshot = chatUX.captureScroll();
      const html = items.map((m) => {
        const mine = m.sender_id === state.me.id;
        return `
        <div data-message-id="${m.id}" style="display:flex;gap:8px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%">
          ${mine ? '' : `<div class="avatar sm" style="background:${esc(m.sender_color)}">${esc(initials(m.sender_name))}</div>`}
          <div class="bubble ${mine ? 'me' : 'them'}" style="max-width:100%" ${mine ? `data-del-msg="${m.id}" title="Tap to delete"` : `data-room-heart="${m.id}" title="Tap to ❤️"`}>${m.heart_count ? `<span class="bubble-heart" data-heart-badge>❤️${m.heart_count > 1 ? ' ' + m.heart_count : ''}</span>` : ''}
            ${mine ? '' : `<div style="font-size:11px;font-weight:700;opacity:.75;margin-bottom:2px">${esc(m.sender_name)}</div>`}
            ${m.has_image ? `<div data-img-id="${m.id}" style="min-height:60px;min-width:120px;margin-bottom:${m.body ? '6px' : '0'}">⏳</div>` : ''}
            ${esc(m.body)}
            <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
          </div>
        </div>`;
      }).join('');
      if (append && !msgsEl.querySelector('.empty-state')) msgsEl.insertAdjacentHTML('beforeend', html);
      else if (append) msgsEl.innerHTML = html;
      else msgsEl.innerHTML = html || '<div class="empty-state" style="padding:20px">No messages yet — rally the club! 👋</div>';
      chatUX.restoreScroll(snapshot, { forceBottom, newMessageCount: newMessages ? items.length : 0 });
      hydrateChatImages(msgsEl, chatUX);
    };
    renderMsgs(data.items, false, { forceBottom: true });
    chatUX.activateOutbox((message) => renderMsgs([message], true, { forceBottom: true }));
    addPhotoToComposer(modal, '#clb-form', '#clb-text', chatUX);

    const pollTimer = setInterval(async () => {
      if (!document.body.contains(msgsEl)) { clearInterval(pollTimer); return; }
      if (document.hidden || state.connectionState === 'offline') return;
      try {
        const fresh = await api(`/clubs/${club.id}/chat?since_id=${lastId}`);
        if (fresh.items.length) renderMsgs(fresh.items, true, { newMessages: true });
        applyRoomHearts(msgsEl, fresh.heart_counts);
      } catch { /* offline */ }
    }, 5000);

    modal.querySelector('#club-head').addEventListener('click', async () => {
      try {
        const fresh = await api(`/clubs/${club.id}`);
        transitionModal(modal, () => openClubInfo(fresh));
      } catch (e) { toast(e.message); }
    });
    modal.querySelector('#clb-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = input.value.trim();
      if (!body) return;
      try {
        await chatUX.send(body);
      } catch (err) { toast(err.message); }
    });
  }

  function openClubInfo(club, routeLoad = null) {
    if (!routedOverlayLoadIsCurrent(routeLoad)) return;
    const isOwner = club.my_role === 'owner';
    // Members double as the club leaderboard — ranked by rating, with club-game
    // records once the club has played under its banner.
    const ranked = [...(club.members || [])].sort((a, b) => b.rating - a.rating);
    const membersHtml = ranked.map((m, i) => {
      const medal = ['🥇', '🥈', '🥉'][i] || `${i + 1}`;
      const overall = (m.ranked_wins + m.ranked_losses) > 0 ? ` · ${m.ranked_wins}W–${m.ranked_losses}L` : '';
      const clubRec = (m.club_wins || m.club_losses) ? ` · 🏛 ${m.club_wins}–${m.club_losses}` : '';
      return `
      <div class="card row" style="padding:11px">
        <span style="font-size:16px;width:24px;text-align:center;font-weight:700">${medal}</span>
        <div data-view-user="${m.id}" style="cursor:pointer">${avatarHtml(m, 'sm')}</div>
        <div class="row-main" data-view-user="${m.id}" style="cursor:pointer">
          <div class="row-title" style="font-size:14px">${esc(m.display_name)}${m.role === 'owner' ? ' 👑' : ''}${m.current_streak >= 2 ? ' 🔥' : ''}</div>
          <div class="row-sub">${skillLabel(m.skill_level)} · ${m.rating}${overall}${clubRec}</div>
        </div>
        ${isOwner && m.id !== state.me.id ? `<button class="btn btn-secondary btn-sm" data-boot="${m.id}" title="Remove from club">✕</button>` : ''}
      </div>`;
    }).join('');

    if (!routedOverlayLoadIsCurrent(routeLoad)) return;
    const modal = openModal(`
      ${modalHead(`🏛 ${club.name}`)}
      ${club.description ? `<div class="row-sub" style="margin:-6px 0 12px">${esc(club.description)}</div>` : ''}
      ${club.announcement ? `
        <div class="card row" style="padding:10px 14px;background:var(--green-50)">
          <span style="font-size:17px">📣</span>
          <div class="row-sub" style="flex:1;color:var(--ink)">${esc(club.announcement)}</div>
        </div>` : ''}
      ${club.home_court_id ? `
        <div class="card row" id="club-court" style="cursor:pointer">
          <span style="font-size:20px">📍</span>
          <div class="row-main">
            <div class="row-title" style="font-size:14px">${esc(club.home_court_name)}</div>
            <div class="row-sub">Home court${club.home_court_city ? ` · ${esc(club.home_court_city)}` : ''}</div>
          </div>
          <span class="chev">›</span>
        </div>` : ''}
      ${(club.leagues || []).length ? `
        <div class="section-label">📦 Club leagues</div>
        ${club.leagues.map((lg) => `
          <div class="card row" data-open-club-league="${lg.id}" style="cursor:pointer;padding:11px">
            <span style="font-size:20px">📦</span>
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(lg.name)}</div>
              <div class="row-sub">${lg.status === 'registration' ? `Signups open · ${lg.member_count}/${lg.max_players}` : `Round ${lg.current_round} · ${lg.member_count} players`}</div>
            </div>
            <span class="chev">›</span>
          </div>`).join('')}` : ''}
      ${(club.tournaments || []).length ? `
        <div class="section-label">🏆 Club tournaments</div>
        ${club.tournaments.map((t) => `
          <div class="card row" data-open-club-tournament="${t.id}" style="cursor:pointer;padding:11px">
            <span style="font-size:20px">🏆</span>
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(t.name)}</div>
              <div class="row-sub">${fmtDateTime(t.starts_at)} · ${t.entry_count}/${t.max_entries} ${t.event_type === 'doubles' ? 'teams' : 'players'}${t.ranked ? ' · ⚡' : ''}</div>
            </div>
            <span class="chev">›</span>
          </div>`).join('')}` : ''}
      ${(club.upcoming_games || []).length ? `
        <div class="section-label"><svg class="pb-ic"><use href="#pb"/></svg> Upcoming club games</div>
        ${club.upcoming_games.map((gm) => `
          <div class="card row" data-open-game="${gm.id}" style="cursor:pointer;padding:11px">
            <span style="font-size:20px">${gm.game_type === 'ranked' ? '🏆' : '<svg class="pb-ic"><use href="#pb"/></svg>'}</span>
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc((gm.court && gm.court.name) || 'Court')}</div>
              <div class="row-sub">${fmtDateTime(gm.scheduled_at)} · ${gm.players.length}/${gm.max_players} in${gm.spots_left ? ` · ${gm.spots_left} spot${gm.spots_left === 1 ? '' : 's'} left` : ' · full'}</div>
            </div>
            <span class="chev">›</span>
          </div>`).join('')}` : ''}
      ${club.joined
        ? '<button class="btn btn-primary btn-block" id="club-chat-btn" style="margin-bottom:8px">💬 Open club chat</button>'
        : '<button class="btn btn-primary btn-block" id="club-join-btn" style="margin-bottom:8px">🙌 Join this club</button>'}
      ${club.joined ? '<button class="btn btn-secondary btn-block" id="club-invite" style="margin-bottom:8px">🎟 Invite friends</button>' : ''}
      <button class="btn btn-secondary btn-block" id="club-share" style="margin-bottom:8px">📤 Share club</button>
      <div class="section-label">🏆 Leaderboard · ${club.member_count} member${club.member_count === 1 ? '' : 's'}</div>
      ${(club.members || []).some((m) => m.club_wins || m.club_losses)
        ? '<div class="row-sub" style="margin:-2px 0 8px 2px">🏛 = record in club games</div>' : ''}
      ${membersHtml}
      ${isOwner ? `
        <div style="display:flex;gap:8px;margin-top:12px">
          <button class="btn btn-secondary" id="club-edit" style="flex:1">✏️ Edit</button>
          <button class="btn btn-secondary" id="club-delete" style="flex:1;color:#c92a2a">🗑 Disband</button>
        </div>`
        : (club.joined ? '<button class="btn btn-secondary btn-block" id="club-leave" style="margin-top:12px">🚪 Leave club</button>' : '')}
    `, { route: { kind: 'club', id: club.id } });
    bindUserButtons(modal);

    const reopenInfo = async () => {
      try {
        const fresh = await api(`/clubs/${club.id}`);
        transitionModal(modal, () => openClubInfo(fresh));
      } catch (e) { toast(e.message); }
    };

    modal.querySelector('#club-court')?.addEventListener('click', () => {
      transitionModal(modal, () => openCourtDetail(club.home_court_id));
    });
    modal.querySelectorAll('[data-open-game]').forEach((row) => row.addEventListener('click', () => {
      transitionModal(modal, () => openGameScreen(Number(row.dataset.openGame)));
    }));
    modal.querySelectorAll('[data-open-club-tournament]').forEach((row) => row.addEventListener('click', () => {
      transitionModal(modal, () => openTournamentScreen(Number(row.dataset.openClubTournament)));
    }));
    modal.querySelectorAll('[data-open-club-league]').forEach((row) => row.addEventListener('click', () => {
      transitionModal(modal, () => openLeagueScreen(Number(row.dataset.openClubLeague)));
    }));
    modal.querySelector('#club-chat-btn')?.addEventListener('click', () => {
      transitionModal(modal, () => openClubChat(club));
    });
    modal.querySelector('#club-join-btn')?.addEventListener('click', async () => {
      try {
        const joined = await api(`/clubs/${club.id}/join`, { method: 'POST' });
        toast('Welcome to the club! 🎉');
        transitionModal(modal, () => openClubChat(joined));
        renderChat();
      } catch (e) { toast(e.message); }
    });
    modal.querySelector('#club-invite')?.addEventListener('click', () => {
      transitionModal(modal, () => openClubInviteSheet(club));
    });
    modal.querySelector('#club-share').addEventListener('click', async () => {
      const url = `${location.origin}/cl/${club.id}`; // short link → OG preview in chat apps
      try {
        if (navigator.share) {
          await navigator.share({ title: 'Third Shot', text: `Join my pickleball club: ${club.name}`, url });
        } else {
          await navigator.clipboard.writeText(url);
          toast('Link copied 📋');
        }
      } catch { /* user cancelled share */ }
    });
    modal.querySelector('#club-leave')?.addEventListener('click', async () => {
      if (!window.confirm(`Leave ${club.name}?`)) return;
      try {
        await api(`/clubs/${club.id}/leave`, { method: 'POST' });
        toast('You left the club');
        closeModal(modal);
        renderChat();
      } catch (e) { toast(e.message); }
    });
    modal.querySelector('#club-delete')?.addEventListener('click', async () => {
      if (!window.confirm(`Disband ${club.name}? This deletes its chat for everyone.`)) return;
      try {
        await api(`/clubs/${club.id}`, { method: 'DELETE' });
        toast('Club disbanded');
        closeModal(modal);
        renderChat();
      } catch (e) { toast(e.message); }
    });
    modal.querySelector('#club-edit')?.addEventListener('click', () => {
      transitionModal(modal, () => openEditClubSheet(club));
    });
    modal.querySelectorAll('[data-boot]').forEach((btn) => btn.addEventListener('click', async () => {
      if (!window.confirm('Remove this player from the club?')) return;
      try {
        await api(`/clubs/${club.id}/remove`, { method: 'POST', body: JSON.stringify({ user_id: Number(btn.dataset.boot) }) });
        toast('Player removed');
        reopenInfo();
      } catch (e) { toast(e.message); }
    }));
  }

  async function openClubInviteSheet(club) {
    const modalLoad = beginRoutedOverlayLoad(null);
    let data;
    try { data = await api('/friends'); } catch (e) {
      if (!routedOverlayLoadIsCurrent(modalLoad)) return;
      toast(e.message); return;
    }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;
    const memberIds = new Set((club.members || []).map((m) => m.id));
    const candidates = (data.friends || []).filter((f) => !memberIds.has(f.id));

    const modal = openModal(`
      ${modalHead(`🎟 Invite friends to ${club.name}`)}
      ${candidates.length ? candidates.map((f) => `
        <div class="card row">
          ${avatarHtml(f)}
          <div class="row-main">
            <div class="row-title">${esc(f.display_name)}</div>
            <div class="row-sub">${skillLabel(f.skill_level)} · ${f.rating}</div>
          </div>
          <button class="btn btn-primary btn-sm" data-invite="${f.id}">Invite</button>
        </div>`).join('')
        : '<div class="empty-state"><span class="big">🤝</span>All your friends are already in — or you haven\'t added any yet.<br>Share the club link instead!</div>'}
    `);
    modal.querySelectorAll('[data-invite]').forEach((btn) => btn.addEventListener('click', async () => {
      try {
        await api(`/clubs/${club.id}/invite`, { method: 'POST', body: JSON.stringify({ user_id: Number(btn.dataset.invite) }) });
        btn.textContent = 'Invited ✓';
        btn.disabled = true;
        btn.classList.remove('btn-primary');
        btn.classList.add('btn-secondary');
      } catch (e) { toast(e.message); }
    }));
  }

  function clubCourtPicker(modal, prefix) {
    // Lightweight court search for the club forms; fills the hidden id field.
    let searchTimer;
    modal.querySelector(`#${prefix}-court-search`).addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      const q = e.target.value.trim();
      searchTimer = setTimeout(async () => {
        const resultsEl = modal.querySelector(`#${prefix}-court-results`);
        if (q.length < 2) { resultsEl.innerHTML = ''; return; }
        let url = `/courts?q=${encodeURIComponent(q)}&limit=5`;
        if (state.userLoc) url += `&lat=${state.userLoc[0]}&lng=${state.userLoc[1]}`;
        try {
          const data = await api(url);
          resultsEl.innerHTML = data.items.map((c) => `
            <button type="button" class="court-suggestion" data-pick-court="${c.id}" data-pick-name="${esc(c.name)}">
              <div class="row-main">
                <div class="row-title" style="font-size:14px">${esc(c.name)}</div>
                <div class="row-sub">${esc(c.city || '')}</div>
              </div>
            </button>`).join('') || '<div class="row-sub" style="padding:8px 2px">No courts found</div>';
          resultsEl.querySelectorAll('[data-pick-court]').forEach((row) => row.addEventListener('click', () => {
            modal.querySelector(`#${prefix}-court-id`).value = row.dataset.pickCourt;
            modal.querySelector(`#${prefix}-court-search`).value = row.dataset.pickName;
            resultsEl.innerHTML = '';
          }));
        } catch { /* keep old results */ }
      }, 300);
    });
  }

  function openCreateClubSheet() {
    const modal = openModal(`
      ${modalHead('Start a club')}
      <form id="cb-form" novalidate>
      <p class="row-sub" style="margin:-6px 0 12px">A club is your crew — a private chat room, a roster, and a home base other players can find and join.</p>
      <div class="form-field">
        <label for="cb-name">Club name</label>
        <input type="text" id="cb-name" maxlength="80" placeholder="e.g. Sunrise Dinkers" />
      </div>
      <div class="form-field">
        <label for="cb-desc">What's the club about? (optional)</label>
        <input type="text" id="cb-desc" maxlength="200" placeholder="e.g. Early birds, all levels welcome" />
      </div>
      <div class="form-field">
        <label for="cb-court-search">Home court (optional)</label>
        <input type="search" id="cb-court-search" placeholder="Search courts…" autocomplete="off" />
        <input type="hidden" id="cb-court-id" value="" />
        <div id="cb-court-results" style="margin-top:8px"></div>
      </div>
      <button type="submit" class="btn btn-primary btn-block" id="cb-submit" style="padding:15px">Create club</button>
      </form>
    `);
    clubCourtPicker(modal, 'cb');
    const formUX = bindModalFormUX(modal, '#cb-submit', { draftKey: 'create-club' });
    modal.querySelector('#cb-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      formUX.clearError();
      const name = modal.querySelector('#cb-name').value.trim();
      if (name.length < 3) {
        formUX.showError('Give your club a name (3+ characters).', modal.querySelector('#cb-name'));
        return;
      }
      const courtId = Number(modal.querySelector('#cb-court-id').value) || null;
      const finishSubmitting = formUX.startSubmitting('Creating club…');
      if (!finishSubmitting) return;
      try {
        const club = await api('/clubs', { method: 'POST', body: JSON.stringify({
          name,
          description: modal.querySelector('#cb-desc').value.trim(),
          home_court_id: courtId,
        }) });
        formUX.clearDraft({ disable: true });
        toast('Club created! 🏛');
        transitionModal(modal, () => openClubChat(club));
        renderChat();
      } catch (err) {
        finishSubmitting();
        formUX.showError(err.message);
      }
    });
  }

  function openEditClubSheet(club) {
    const modal = openModal(`
      ${modalHead('Edit club')}
      <form id="ce-form" novalidate>
      <div class="form-field">
        <label for="ce-name">Club name</label>
        <input type="text" id="ce-name" maxlength="80" value="${esc(club.name)}" />
      </div>
      <div class="form-field">
        <label for="ce-desc">Description</label>
        <input type="text" id="ce-desc" maxlength="200" value="${esc(club.description || '')}" />
      </div>
      <div class="form-field">
        <label for="ce-announce">📣 Announcement (pinned in the club chat — members get pinged)</label>
        <input type="text" id="ce-announce" maxlength="500" placeholder="e.g. Saturday session moved to 9 AM!" value="${esc(club.announcement || '')}" />
      </div>
      <div class="form-field">
        <label for="ce-court-search">Home court</label>
        <input type="search" id="ce-court-search" placeholder="Search courts…" value="${esc(club.home_court_name || '')}" autocomplete="off" />
        <input type="hidden" id="ce-court-id" value="${club.home_court_id || ''}" />
        <div id="ce-court-results" style="margin-top:8px"></div>
      </div>
      <button type="submit" class="btn btn-primary btn-block" id="ce-save">Save changes</button>
      </form>
    `);
    clubCourtPicker(modal, 'ce');
    const formUX = bindModalFormUX(modal, '#ce-save', { draftKey: `edit-club-${club.id}` });
    modal.querySelector('#ce-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      formUX.clearError();
      const name = modal.querySelector('#ce-name').value.trim();
      if (name.length < 3) {
        formUX.showError('Club name needs 3+ characters.', modal.querySelector('#ce-name'));
        return;
      }
      const searchVal = modal.querySelector('#ce-court-search').value.trim();
      const body = {
        name,
        description: modal.querySelector('#ce-desc').value.trim(),
        announcement: modal.querySelector('#ce-announce').value.trim(),
        home_court_id: searchVal ? (Number(modal.querySelector('#ce-court-id').value) || null) : null,
      };
      const finishSubmitting = formUX.startSubmitting('Saving changes…');
      if (!finishSubmitting) return;
      try {
        await api(`/clubs/${club.id}`, { method: 'PATCH', body: JSON.stringify(body) });
        formUX.clearDraft({ disable: true });
        toast('Club updated');
        transitionModal(modal, () => openClubScreen(club.id));
        renderChat();
      } catch (err) {
        finishSubmitting();
        formUX.showError(err.message);
      }
    });
  }

  async function openFindClubsSheet() {
    const modal = openModal(`
      ${modalHead('Find a club')}
      <div class="form-field" style="margin-top:4px">
        <input type="search" id="fc-search" placeholder="Search clubs by name…" autocomplete="off" />
      </div>
      <div id="fc-results">${skeletonHtml(3)}</div>
    `);
    const resultsEl = modal.querySelector('#fc-results');
    const renderResults = (items) => {
      resultsEl.innerHTML = items.length ? items.map((cl) => `
        <div class="card row" data-open-club="${cl.id}" style="cursor:pointer">
          <span style="font-size:22px">🏛</span>
          <div class="row-main">
            <div class="row-title" style="font-size:14.5px">${esc(cl.name)}</div>
            <div class="row-sub">${cl.member_count} member${cl.member_count === 1 ? '' : 's'}${cl.home_court_name ? ` · 📍 ${esc(cl.home_court_name)}${cl.home_court_city ? `, ${esc(cl.home_court_city)}` : ''}` : ''}</div>
          </div>
          ${cl.joined ? '<span class="tag" style="margin:0">Member ✓</span>' : '<span class="chev">›</span>'}
        </div>`).join('')
        : '<div class="empty-state"><span class="big">🏛</span>No clubs found.<br>Start one and invite your crew!</div>';
      resultsEl.querySelectorAll('[data-open-club]').forEach((row) => row.addEventListener('click', () => {
        transitionModal(modal, () => openClubScreen(Number(row.dataset.openClub)));
      }));
    };
    const load = async (q) => {
      try {
        const data = await api(`/clubs${q ? `?q=${encodeURIComponent(q)}` : ''}`);
        renderResults(data.items);
      } catch (e) { resultsEl.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; }
    };
    load('');
    let searchTimer;
    modal.querySelector('#fc-search').addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => load(e.target.value.trim()), 300);
    });
  }

  async function openCourtGallery(court, uploadFn) {
    const modalLoad = beginRoutedOverlayLoad(null);
    let data;
    try { data = await api(`/courts/${court.id}/photos`); } catch (e) {
      if (!routedOverlayLoadIsCurrent(modalLoad)) return;
      toast(e.message); return;
    }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;
    const modal = openModal(`
      ${modalHead(`📷 ${court.name}`)}
      <div class="gallery-scroll">
        ${data.items.map((p) => `
          <figure class="gallery-item">
            <img src="${esc(p.url)}" alt="Photo of ${esc(court.name)}" loading="lazy" />
            <figcaption class="row-sub" style="display:flex;align-items:center;gap:8px">
              <span style="flex:1">${p.caption ? `<div style="color:var(--ink);font-weight:600">${esc(p.caption)}</div>` : ''}by ${esc(p.user_name)} · ${resultDayLabel(p.created_at)}</span>
              <button type="button" class="btn-link" data-like-photo="${p.id}" style="font-size:14px;white-space:nowrap">${p.liked_by_me ? '❤️' : '🤍'} <span data-like-count>${p.likes || ''}</span></button>
            </figcaption>
          </figure>`).join('')}
      </div>
      <button class="btn btn-secondary btn-block" id="gal-add" style="margin-top:12px">📷 Add your photo</button>
    `);
    modal.querySelector('#gal-add').addEventListener('click', () => {
      if (uploadFn) uploadFn(() => transitionModal(modal, () => openCourtGallery(court, uploadFn)));
    });
    modal.querySelectorAll('[data-like-photo]').forEach((btn) => btn.addEventListener('click', async () => {
      try {
        const res = await api(`/courts/${court.id}/photos/${btn.dataset.likePhoto}/like`, { method: 'POST' });
        btn.innerHTML = `${res.liked ? '❤️' : '🤍'} <span data-like-count>${res.likes || ''}</span>`;
      } catch (e) { toast(e.message); }
    }));
  }

  async function openGameChat(game) {
    const modalLoad = beginRoutedOverlayLoad(null);
    let data;
    try { data = await api(`/games/${game.id}/chat`); } catch (e) {
      if (!routedOverlayLoadIsCurrent(modalLoad)) return;
      toast(e.message); return;
    }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;
    refreshMe(); // keep the global Community badge exact outside Inbox

    const modal = openModal(`
      <div class="thread">
        <div class="thread-head">
          <button class="modal-close" style="font-size:18px">‹</button>
          <span style="font-size:22px"><svg class="pb-ic"><use href="#pb"/></svg></span>
          <div class="row-main">
            <div class="row-title">Game chat</div>
            <div class="row-sub">${esc(data.game.court_name)} — only players in this game can read it</div>
          </div>
        </div>
        <div class="thread-msgs" id="gc-msgs" role="log" aria-live="polite" aria-relevant="additions" aria-label="Game conversation"></div>
        <form class="thread-input" id="gc-form">
          <input type="text" id="gc-text" placeholder="Message your game…" autocomplete="off" maxlength="2000" />
          <button type="submit" aria-label="Send">➤</button>
        </form>
      </div>
    `, { chat: true });

    const msgsEl = modal.querySelector('#gc-msgs');
    const input = modal.querySelector('#gc-text');
    const chatUX = bindChatContinuity(modal, msgsEl, input, `game:${game.id}`);
    let lastId = 0;
    const renderMsgs = (items, append, { forceBottom = false, newMessages = false } = {}) => {
      const batch = prepareChatRenderBatch(msgsEl, items, append);
      if (batch.newestId) lastId = Math.max(lastId, batch.newestId);
      items = batch.items;
      if (append && !items.length) return;
      const snapshot = chatUX.captureScroll();
      const html = items.map((m) => {
        const mine = m.sender_id === state.me.id;
        return `
        <div data-message-id="${m.id}" style="display:flex;gap:8px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%">
          ${mine ? '' : `<div class="avatar sm" style="background:${esc(m.sender_color)}">${esc(initials(m.sender_name))}</div>`}
          <div class="bubble ${mine ? 'me' : 'them'}" style="max-width:100%" ${mine ? `data-del-msg="${m.id}" title="Tap to delete"` : `data-room-heart="${m.id}" title="Tap to ❤️"`}>${m.heart_count ? `<span class="bubble-heart" data-heart-badge>❤️${m.heart_count > 1 ? ' ' + m.heart_count : ''}</span>` : ''}
            ${mine ? '' : `<div style="font-size:11px;font-weight:700;opacity:.75;margin-bottom:2px">${esc(m.sender_name)}</div>`}
            ${m.has_image ? `<div data-img-id="${m.id}" style="min-height:60px;min-width:120px;margin-bottom:${m.body ? '6px' : '0'}">⏳</div>` : ''}
            ${esc(m.body)}
            <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
          </div>
        </div>`;
      }).join('');
      if (append && !msgsEl.querySelector('.empty-state')) msgsEl.insertAdjacentHTML('beforeend', html);
      else if (append) msgsEl.innerHTML = html;
      else msgsEl.innerHTML = html || '<div class="empty-state" style="padding:20px">Coordinate with your game — “running late”, “bringing balls” <svg class="pb-ic"><use href="#pb"/></svg></div>';
      chatUX.restoreScroll(snapshot, { forceBottom, newMessageCount: newMessages ? items.length : 0 });
      hydrateChatImages(msgsEl, chatUX);
    };
    renderMsgs(data.items, false, { forceBottom: true });
    chatUX.activateOutbox((message) => renderMsgs([message], true, { forceBottom: true }));
    addPhotoToComposer(modal, '#gc-form', '#gc-text', chatUX);

    const pollTimer = setInterval(async () => {
      if (!document.body.contains(msgsEl)) { clearInterval(pollTimer); return; }
      if (document.hidden || state.connectionState === 'offline') return;
      try {
        const fresh = await api(`/games/${game.id}/chat?since_id=${lastId}`);
        if (fresh.items.length) renderMsgs(fresh.items, true, { newMessages: true });
        applyRoomHearts(msgsEl, fresh.heart_counts);
      } catch { /* offline */ }
    }, 5000);

    modal.querySelector('#gc-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = input.value.trim();
      if (!body) return;
      try {
        await chatUX.send(body);
      } catch (err) { toast(err.message); }
    });
  }

  // ---------- User profile ----------
  // "Marcus", "Marcus & Priya", "Marcus, Priya & 2 others" — mutual friends.
  function mutualFriendsText(mutuals) {
    const names = mutuals.map((m) => esc(m.display_name.split(' ')[0]));
    let who;
    if (names.length === 1) who = names[0];
    else if (names.length === 2) who = `${names[0]} & ${names[1]}`;
    else who = `${names.slice(0, 2).join(', ')} & ${names.length - 2} other${names.length - 2 === 1 ? '' : 's'}`;
    return `You both know ${who}`;
  }

  async function openUserProfile(userId) {
    const modalLoad = beginRoutedOverlayLoad(null);
    let user;
    try { user = await api(`/users/${userId}`); } catch (e) {
      if (!routedOverlayLoadIsCurrent(modalLoad)) return;
      toast(e.message); return;
    }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;

    let friendAction = '';
    if (userId !== state.me.id) {
      if (user.is_blocked) {
        friendAction = '<span class="tag warn" style="margin:0">🚫 Blocked</span>';
      } else if (user.friendship_status === 'accepted') {
        friendAction = `<button class="btn btn-primary" id="up-challenge">⚔️ Challenge</button>
          <button class="btn btn-secondary" id="up-msg">💬 Message</button>
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
    if (userId !== state.me.id && (user.head_to_head || user.as_teammates)) {
      const firstName = (user.display_name || 'They').split(' ')[0];
      let lines = '';
      if (user.head_to_head) {
        const { wins, losses } = user.head_to_head;
        const line = wins > losses ? `You lead ${wins}–${losses}`
          : losses > wins ? `${esc(firstName)} leads ${losses}–${wins}`
          : `Tied ${wins}–${wins}`;
        lines += `<div style="font-weight:800">🎯 ${line}</div><div class="row-sub">your head-to-head record</div>`;
      }
      if (user.as_teammates) {
        const t = user.as_teammates;
        lines += `<div style="font-weight:800;margin-top:${user.head_to_head ? '8px' : '0'}">🤝 ${t.wins}–${t.losses} together</div><div class="row-sub">as teammates</div>`;
      }
      h2hHtml = `<div class="card" style="text-align:center;padding:10px 14px;margin:12px 0 0">${lines}</div>`;
    }
    const availLines = availabilitySummary(user.availability);
    const availHtml = availLines.length ? `
      <div class="card" style="padding:10px 14px;margin:12px 0 0">
        <div class="row-sub" style="font-weight:800;color:var(--ink)">⏰ Usually plays${
          userId !== state.me.id && availabilityOverlap(state.me.availability, user.availability)
            ? ' <span class="tag" style="margin:0 0 0 6px">🤝 your times too</span>' : ''}</div>
        ${availLines.map((l) => `<div class="row-sub">${l}</div>`).join('')}
        ${userId !== state.me.id && sharedAvailabilityText(state.me.availability, user.availability)
          ? `<div class="row-sub" style="margin-top:6px;color:var(--green-accent);font-weight:700">🤝 You both play: ${esc(sharedAvailabilityText(state.me.availability, user.availability))}</div>
             <button class="btn btn-secondary btn-sm btn-block" id="up-schedule-shared" style="margin-top:8px"><svg class="pb-ic"><use href="#pb"/></svg> Schedule at a shared time</button>` : ''}
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
        <div class="profile-sub">${user.active_now ? '<b style="color:var(--green-accent)">🟢 active now</b> · ' : ''}${skillLabel(user.skill_level)}${user.home_court_name ? ` · 🏠 ${esc(user.home_court_name)}` : ''}</div>
        ${user.bio ? `<p class="profile-sub" style="margin-top:8px">${esc(user.bio)}</p>` : ''}
        ${(user.mutual_friends || []).length ? `<p class="profile-sub" style="margin-top:8px">🤝 ${mutualFriendsText(user.mutual_friends)}</p>` : ''}
      </div>
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">${user.rating}</div><div class="stat-label">Rating${user.best_rating > user.rating ? ` · peak ${user.best_rating}` : ''}</div></div>
        <div class="stat-card"><div class="stat-value">${user.ranked_wins}</div><div class="stat-label">Ranked wins</div></div>
        <div class="stat-card"><div class="stat-value">${user.ranked_losses}</div><div class="stat-label">Ranked losses</div></div>
      </div>
      ${formStripHtml(user.form)}
      ${ratingSparklineHtml(user.rating_history)}
      ${(user.badges || []).length ? `
        <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:center;margin-top:10px">
          ${user.badges.map((b) => `<span class="tag" style="margin:0" title="${esc(b.label)}">${b.emoji} ${esc(b.label)}${b.id === 'mvp' && user.mvp_awards > 1 ? ` ×${user.mvp_awards}` : ''}</span>`).join('')}
        </div>` : ''}
      ${tournamentTitlesHtml(user.tournament_titles, user.league_titles)}
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
          <button id="up-report" style="background:transparent;color:var(--ink-soft);font-size:13px;font-weight:600;margin-left:14px">
            ⚑ Report
          </button>
        </div>` : ''}
    `, { label: `${user.display_name} profile` });

    bindGameButtons(modal, () => transitionModal(modal, () => openUserProfile(userId)));
    modal.querySelectorAll('[data-open-tournament]').forEach((row) => row.addEventListener('click', () => {
      openTournamentScreen(Number(row.dataset.openTournament));
    }));
    modal.querySelectorAll('[data-open-league]').forEach((row) => row.addEventListener('click', () => {
      openLeagueScreen(Number(row.dataset.openLeague));
    }));
    modal.querySelectorAll('[data-pcourt]').forEach((row) => row.addEventListener('click', () => {
      transitionModal(modal, () => openCourtDetail(Number(row.dataset.pcourt)));
    }));

    modal.querySelector('#up-report')?.addEventListener('click', () => {
      const sheet = openModal(`
        ${modalHead('⚑ Report ' + esc(user.display_name.split(' ')[0]))}
        <p class="row-sub" style="margin-bottom:12px">What's going on? Reports go to the Third Shot team.</p>
        ${['Harassment or abusive messages', 'Fake or manipulated scores', 'Spam or fake account', 'Something else']
          .map((r) => `<button class="btn btn-secondary btn-block" data-report-reason="${esc(r)}" style="margin-bottom:8px;text-align:left">${esc(r)}</button>`).join('')}
      `);
      sheet.querySelectorAll('[data-report-reason]').forEach((b) => b.addEventListener('click', async () => {
        try {
          await api(`/users/${userId}/report`, { method: 'POST', body: JSON.stringify({ reason: b.dataset.reportReason }) });
          closeModal(sheet);
          toast('Reported — thanks for keeping the courts friendly 🌿');
        } catch (e) { toast(e.message); }
      }));
    });
    modal.querySelector('#up-block')?.addEventListener('click', async () => {
      if (user.is_blocked) {
        try {
          await api(`/users/${userId}/unblock`, { method: 'POST' });
          toast('User unblocked');
          transitionModal(modal, () => openUserProfile(userId));
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
      transitionModal(modal, () => openThread(userId));
    });
    modal.querySelector('#up-challenge')?.addEventListener('click', () => {
      // Ranked singles, right now. Default to a court that makes sense:
      // where you're checked in, else your home court, else theirs.
      let court = null;
      if (state.presence && state.presence.checked_in) court = { id: state.presence.court_id, name: state.presence.court_name };
      else if (state.me.home_court_id) court = { id: state.me.home_court_id, name: state.me.home_court_name };
      else if (user.home_court_id) court = { id: user.home_court_id, name: user.home_court_name };
      if (!court) { toast('Set a home court first (Profile → Edit) to challenge'); return; }
      transitionModal(modal, () => openChallengeSheet(user, court));
    });
    modal.querySelector('#up-schedule-shared')?.addEventListener('click', () => {
      // Open the scheduler pre-set to the shared slot whose next occurrence
      // is soonest on the calendar (so it lands within the day picker's range).
      const shared = (user.availability || []).filter((s) => (state.me.availability || []).includes(s));
      const dow = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
      const today = new Date().getDay();
      const daysUntil = (s) => ((dow[s.split('-')[0]] - today) + 7) % 7;
      const slot = shared.sort((x, y) => daysUntil(x) - daysUntil(y))[0];
      transitionModal(modal, () => openNewGameModal(null, 'casual', false, slot, userId));
    });
  }

  // ---------- My profile tab ----------
  let profileRenderGeneration = 0;
  let profileDashboardCache = { userId: null, promise: null, data: null, readyAt: 0 };

  function profileDashboardRequest(userId, { reuse = false } = {}) {
    const sameUser = profileDashboardCache.userId === userId;
    if (reuse && sameUser && profileDashboardCache.promise) {
      return profileDashboardCache.promise;
    }
    if (reuse && sameUser && profileDashboardCache.data
        && Date.now() - profileDashboardCache.readyAt < VIEW_FRESH_MS) {
      return Promise.resolve(profileDashboardCache.data);
    }
    const promise = api('/me/dashboard');
    profileDashboardCache = { userId, promise, data: null, readyAt: 0 };
    promise.then((data) => {
      if (profileDashboardCache.userId !== userId
          || profileDashboardCache.promise !== promise) return;
      profileDashboardCache = {
        userId, promise: null, data, readyAt: Date.now(),
      };
    }, () => {
      if (profileDashboardCache.userId === userId
          && profileDashboardCache.promise === promise) {
        profileDashboardCache = { userId, promise: null, data: null, readyAt: 0 };
      }
    });
    return promise;
  }

  async function renderProfile({ reuseDashboard = false } = {}) {
    const generation = ++profileRenderGeneration;
    const el = $('#profile-content');
    const me = state.me;
    if (!me) return;
    el.dataset.profileRender = String(generation);
    const renderIsCurrent = () => generation === profileRenderGeneration
      && el.dataset.profileRender === String(generation)
      && state.tab === 'profile'
      && state.me && state.me.id === me.id;
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
        <div class="stat-card"><div class="stat-value">${me.rating}</div><div class="stat-label">Rating${me.best_rating > me.rating ? ` · peak ${me.best_rating}` : ''}</div></div>
        <div class="stat-card"><div class="stat-value">${me.ranked_wins}–${me.ranked_losses}</div><div class="stat-label">${(me.ranked_wins + me.ranked_losses) ? `Ranked record · ${winPct}%` : 'Ranked record'}</div></div>
        <div class="stat-card"><div class="stat-value">${me.current_streak >= 2 ? '🔥' : ''}${me.current_streak}</div><div class="stat-label">Streak · best ${me.best_streak}</div></div>
      </div>
      <div id="pf-play-stats" aria-busy="true" style="min-height:146px">
        <div class="section-label">Your play stats</div>${skeletonHtml(1)}
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
      <div id="pf-upcoming" aria-busy="true" style="min-height:108px">
        <div class="section-label">My upcoming games</div>${skeletonHtml(1)}
      </div>
      <div id="pf-courts" aria-busy="true" style="min-height:108px">
        <div class="section-label">Saved courts</div>${skeletonHtml(1)}
      </div>
      <div id="pf-history" aria-busy="true" style="min-height:166px">
        <div class="section-label">Match history</div>${skeletonHtml(2)}
      </div>
      <div class="section-label">Settings</div>
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
        <button class="btn btn-sm ${autoCheckInEnabled() ? 'btn-primary' : 'btn-secondary'}" id="pf-auto" aria-pressed="${autoCheckInEnabled()}">
          ${autoCheckInEnabled() ? 'On' : 'Off'}
        </button>
      </div>
      <div class="card row" style="margin-bottom:10px">
        <span style="font-size:20px">📅</span>
        <div class="row-main">
          <div class="row-title" style="font-size:14px">Games calendar</div>
          <div class="row-sub">Subscribe so your games sync to any calendar app</div>
        </div>
        <button class="btn btn-secondary btn-sm" id="pf-calendar">Subscribe</button>
      </div>
      <div class="card row" style="margin-bottom:10px">
        <span style="font-size:20px">🌗</span>
        <div class="row-main">
          <div class="row-title" style="font-size:14px">Appearance</div>
          <div class="row-sub">Dark theme, or follow your device</div>
        </div>
        <div style="display:flex;gap:6px">
          ${['auto', 'light', 'dark'].map((t) => `
            <button class="btn btn-sm ${themePref() === t ? 'btn-primary' : 'btn-secondary'}" data-theme-pick="${t}">${t === 'auto' ? 'Auto' : t === 'light' ? '☀️' : '🌙'}</button>`).join('')}
        </div>
      </div>
      <div class="card row" style="margin-bottom:10px">
        <span style="font-size:20px">💌</span>
        <div class="row-main">
          <div class="row-title" style="font-size:14px">Invite friends</div>
          <div class="row-sub">Share your link — they'll land right on your profile</div>
        </div>
        <button class="btn btn-primary btn-sm" id="pf-invite">Share</button>
      </div>
      ${!window.matchMedia('(display-mode: standalone)').matches ? (state.installPrompt ? `
        <button class="card row btn-reset" id="pf-install" style="margin-bottom:10px;width:100%;text-align:left;cursor:pointer">
          <span style="font-size:20px">📲</span>
          <div class="row-main">
            <div class="row-title" style="font-size:14px">Install Third Shot</div>
            <div class="row-sub">One tap — works offline-ish, opens full screen.</div>
          </div>
          <span class="chev">›</span>
        </button>` : `
        <div class="card row" style="margin-bottom:10px">
          <span style="font-size:20px">📱</span>
          <div class="row-main">
            <div class="row-title" style="font-size:14px">Get the app feel</div>
            <div class="row-sub">In your browser menu tap <b>Add to Home Screen</b> — Third Shot installs like an app.</div>
          </div>
        </div>`) : ''}
      <button class="btn btn-secondary btn-block" id="pf-edit" style="margin-bottom:10px">✏️ Edit profile</button>
      <button class="btn btn-secondary btn-block" id="pf-activity" style="margin-bottom:10px">🔔 Activity</button>
      <button class="btn btn-secondary btn-block" id="pf-feedback" style="margin-bottom:10px">💡 Send feedback</button>
      <button class="btn btn-danger btn-block" id="pf-logout">Log out</button>
    `;

    // One dashboard response keeps Profile to a single mobile round trip while
    // preserving the endpoint-parity section shapes consumed below.
    const profileDataPromise = profileDashboardRequest(me.id, {
      reuse: reuseDashboard,
    }).then((dashboard) => [
      { status: 'fulfilled', value: dashboard.games },
      { status: 'fulfilled', value: dashboard.stats },
      { status: 'fulfilled', value: dashboard.favorites },
      { status: 'fulfilled', value: dashboard.history },
    ], (reason) => [
      { status: 'rejected', reason },
      { status: 'rejected', reason },
      { status: 'rejected', reason },
      { status: 'rejected', reason },
    ]);

    el.querySelector('#pf-feedback')?.addEventListener('click', () => {
      const sheet = openModal(`
        ${modalHead('💡 Send feedback')}
        <p class="row-sub" style="margin-bottom:10px">Found a bug? Missing a feature? It goes straight to the person building Third Shot.</p>
        <textarea id="fb-text" maxlength="2000" rows="5" placeholder="What's on your mind?" style="width:100%"></textarea>
        <button class="btn btn-primary btn-block" id="fb-send" style="margin-top:12px">Send</button>
      `);
      sheet.querySelector('#fb-send').addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        const message = sheet.querySelector('#fb-text').value.trim();
        if (message.length < 3) { toast('Say a little more 🙂'); return; }
        btn.disabled = true;
        try {
          await api('/feedback', { method: 'POST', body: JSON.stringify({ message }) });
          closeModal(sheet);
          toast('Thanks — feedback sent! 💚');
        } catch (err) { toast(err.message); btn.disabled = false; }
      });
    });
    el.querySelector('#pf-install')?.addEventListener('click', async () => {
      const prompt = state.installPrompt;
      if (!prompt) return;
      state.installPrompt = null;
      try {
        prompt.prompt();
        const choice = await prompt.userChoice;
        toast(choice.outcome === 'accepted' ? 'Installing — see you on the home screen! 📲' : 'Maybe later 👍');
      } catch { /* browser said no */ }
      renderProfile();
    });
    el.querySelector('#pf-invite').addEventListener('click', shareInviteLink);
    el.querySelectorAll('[data-theme-pick]').forEach((b) => b.addEventListener('click', () => {
      localStorage.setItem('pp_theme', b.dataset.themePick);
      applyTheme();
      renderProfile();
    }));
    el.querySelector('#pf-auto').addEventListener('click', () => {
      if (!autoCheckInEnabled()) {
        openAutoCheckInConsent(renderProfile);
        return;
      }
      setAutoCheckInEnabled(false);
      stopLocationWatch();
      toast('Auto check-in off');
      renderProfile();
    });

    el.querySelector('#pf-calendar').addEventListener('click', async () => {
      const modalLoad = beginRoutedOverlayLoad(null);
      let webcal;
      let feed;
      try {
        const { token } = await api('/calendar/token');
        // webcal:// prompts a subscribe (auto-updating), unlike a one-off .ics.
        feed = `${location.host}/api/calendar/${token}.ics`;
        webcal = `webcal://${feed}`;
      } catch (e) {
        if (!routedOverlayLoadIsCurrent(modalLoad)) return;
        toast(e.message); return;
      }
      if (!routedOverlayLoadIsCurrent(modalLoad)) return;
      try {
        if (navigator.share) {
          await navigator.share({ title: 'My Third Shot games', url: `${location.protocol}//${feed}` });
        } else {
          await navigator.clipboard.writeText(webcal);
          toast('Calendar link copied — add it in your calendar app 📅');
        }
      } catch {
        if (!routedOverlayLoadIsCurrent(modalLoad)) return;
        // Share cancelled or clipboard blocked — show the link so the user
        // can copy it themselves instead of a raw browser error.
        openModal(`
          ${modalHead('📅 Games calendar')}
          <p class="row-sub" style="margin-bottom:10px">In your calendar app, choose “Subscribe” or “Add calendar by URL” and paste this link — your games will stay in sync.</p>
          <input type="text" readonly value="${esc(webcal)}" onclick="this.select()" style="font-size:12.5px" />
        `);
      }
    });
    el.querySelector('#pf-home').addEventListener('click', () => {
      openHomeAreaSheet({ onSet: renderProfile });
    });
    el.querySelector('#pf-logout').addEventListener('click', logout);
    el.querySelector('#pf-edit').addEventListener('click', openEditProfile);
    el.querySelector('#pf-activity').addEventListener('click', openActivity);
    el.querySelector('#pf-checkout')?.addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      if (btn.disabled) return;
      btn.disabled = true;
      btn.textContent = 'Checking out…';
      const prev = state.presence;
      const followupLoad = beginRoutedOverlayLoad(null);
      try {
        await api('/checkout', { method: 'POST' });
        await refreshMe();
        renderProfile();
        if (routedOverlayLoadIsCurrent(followupLoad)) maybeAskConditions(prev);
      } catch (err) {
        toast(err.message);
        btn.disabled = false;
        btn.textContent = 'Check out';
      }
    });

    const [mineResult, statsResult, favoritesResult, historyResult] = await profileDataPromise;
    if (!renderIsCurrent()) return;

    // Remove all reserved loading space in one paint, then hydrate the same
    // fixed section nodes. A newer Profile render (or another active tab) owns
    // the DOM as soon as the generation/current-view guard above stops matching.
    const statsEl = el.querySelector('#pf-play-stats');
    const upcomingEl = el.querySelector('#pf-upcoming');
    const courtsEl = el.querySelector('#pf-courts');
    const historyEl = el.querySelector('#pf-history');
    [statsEl, upcomingEl, courtsEl, historyEl].forEach((section) => {
      section.innerHTML = '';
      section.removeAttribute('aria-busy');
      section.style.removeProperty('min-height');
    });

    // My upcoming games (parity with public profiles), tappable into the game screen.
    try {
      const mine = mineResult.status === 'fulfilled' ? mineResult.value : null;
      if (!mine) throw mineResult.reason;
      const nowMs = Date.now();
      const up = (mine.items || []).filter((game) =>
        game.status === 'upcoming' && new Date(game.scheduled_at).getTime() > nowMs);
      if (up.length) {
        upcomingEl.innerHTML = '<div class="section-label">My upcoming games</div>'
          + up.map((game) => gameCardHtml(game)).join('');
        bindGameButtons(upcomingEl, renderProfile);
      }
    } catch { /* ignore */ }

    // Personal play stats — quietly skipped for brand-new players.
    try {
      const stats = statsResult.status === 'fulfilled' ? statsResult.value : null;
      if (!stats) throw statsResult.reason;
      if (stats.games_total > 0) {
        statsEl.innerHTML = `
          <div class="stat-grid" style="margin-top:10px">
            <div class="stat-card"><div class="stat-value">${stats.games_this_month}</div><div class="stat-label">Games this month</div></div>
            <div class="stat-card"><div class="stat-value">${stats.week_streak >= 2 ? '🔥' : ''}${stats.week_streak}</div><div class="stat-label">Week streak</div></div>
            <div class="stat-card"${stats.top_court ? ` data-pfcourt-top="${stats.top_court.id}" style="cursor:pointer"` : ''}>
              <div class="stat-value" style="font-size:13px;line-height:1.25;padding-top:4px">${stats.top_court ? esc(stats.top_court.name) : '—'}</div>
              <div class="stat-label">Top court${stats.top_court ? ` · ${stats.top_court.games}` : ''}</div>
            </div>
          </div>`;
        const extras = [];
        if (stats.best_partner) {
          extras.push(`🤝 Best partner: <b data-view-user="${stats.best_partner.user_id}" style="cursor:pointer">${esc(stats.best_partner.display_name)}</b> · ${stats.best_partner.wins} win${stats.best_partner.wins === 1 ? '' : 's'} together`);
        }
        if (stats.top_rival) {
          extras.push(`🥊 Most faced: <b data-view-user="${stats.top_rival.user_id}" style="cursor:pointer">${esc(stats.top_rival.display_name)}</b> · you're ${stats.top_rival.your_wins}–${stats.top_rival.games - stats.top_rival.your_wins}`);
        }
        if (extras.length) {
          statsEl.insertAdjacentHTML('beforeend',
            `<div class="row-sub" style="text-align:center;margin-top:8px">${extras.join('<br>')}</div>`);
        }
        statsEl.insertAdjacentHTML('beforeend', formStripHtml(stats.form));
        statsEl.insertAdjacentHTML('beforeend', ratingSparklineHtml(stats.rating_history));
        if ((stats.badges || []).length || (stats.badge_progress || []).length) {
          const earned = (stats.badges || []).map((b) =>
            `<span class="tag" style="margin:0" title="${esc(b.label)}">${b.emoji} ${esc(b.label)}${b.id === 'mvp' && stats.mvp_awards > 1 ? ` ×${stats.mvp_awards}` : ''}</span>`);
          // Locked badges show dimmed with progress toward the next milestone.
          const locked = (stats.badge_progress || []).map((b) =>
            `<span class="tag" style="margin:0;opacity:.5;filter:grayscale(1)" title="${esc(b.label)} (${b.current}/${b.target})">${b.emoji} ${esc(b.label)} ${b.current}/${b.target}</span>`);
          statsEl.insertAdjacentHTML('beforeend', `
            <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:center;margin-top:10px">
              ${[...earned, ...locked].join('')}
            </div>`);
        }
        statsEl.insertAdjacentHTML('beforeend',
          tournamentTitlesHtml(stats.tournament_titles, stats.league_titles));
        statsEl.querySelectorAll('[data-open-tournament]').forEach((row) => {
          row.addEventListener('click', () => openTournamentScreen(Number(row.dataset.openTournament)));
        });
        statsEl.querySelectorAll('[data-open-league]').forEach((row) => {
          row.addEventListener('click', () => openLeagueScreen(Number(row.dataset.openLeague)));
        });
        if (stats.insights) {
          const ins = stats.insights;
          const lines = [];
          if (ins.best_part) {
            const pct = Math.round((ins.best_part.wins / ins.best_part.games) * 100);
            lines.push(`🌤 You win ${pct}% of your ${esc(ins.best_part.label.replace(/s$/, ''))} games (${ins.best_part.wins}/${ins.best_part.games})`);
          }
          if (ins.busiest_day) lines.push(`📆 You play most on ${esc(ins.busiest_day)}s`);
          if (ins.avg_margin != null) {
            lines.push(`${ins.avg_margin >= 0 ? '📈' : '📉'} Average margin: ${ins.avg_margin > 0 ? '+' : ''}${ins.avg_margin} points`);
          }
          if (lines.length) {
            statsEl.insertAdjacentHTML('beforeend', `
              <div class="card" style="margin-top:12px;padding:12px 14px">
                <div style="font-weight:800;font-size:14px;text-align:center;margin-bottom:6px">📊 Your patterns</div>
                ${lines.map((l) => `<div class="row-sub" style="padding:2px 0">${l}</div>`).join('')}
              </div>`);
          }
        }
        // If the earned rating outgrew the declared level, offer a one-tap
        // upgrade (upward only — nobody wants a demotion prompt). One ask
        // per suggested level per device.
        const LEVEL_ORDER = ['beginner', 'intermediate', 'advanced', 'pro'];
        const levelForRating = (r) => (r >= 1450 ? 'pro' : r >= 1300 ? 'advanced' : r >= 1150 ? 'intermediate' : 'beginner');
        const suggested = levelForRating(me.rating);
        if (me.ranked_wins + me.ranked_losses >= 5
            && LEVEL_ORDER.indexOf(suggested) > LEVEL_ORDER.indexOf(me.skill_level)
            && localStorage.getItem(`pp_skill_nudge_${suggested}`) !== '1') {
          statsEl.insertAdjacentHTML('beforeend', `
            <div class="card" id="pf-skill-nudge" style="margin-top:12px;padding:12px 14px;text-align:center">
              <div style="font-weight:700;font-size:14px">📈 Your rating plays like ${skillLabel(suggested)}</div>
              <div class="row-sub" style="margin:4px 0 10px">You're listed as ${skillLabel(me.skill_level)} — level up your label?</div>
              <div style="display:flex;gap:8px;justify-content:center">
                <button class="btn btn-primary btn-sm" id="pf-skill-up">Update to ${skillLabel(suggested)}</button>
                <button class="btn btn-secondary btn-sm" id="pf-skill-keep">Keep as is</button>
              </div>
            </div>`);
          el.querySelector('#pf-skill-up').addEventListener('click', async () => {
            try {
              applyMe(await api('/me', { method: 'PATCH', body: JSON.stringify({ skill_level: suggested }) }));
              toast(`You're ${skillLabel(suggested)} now — go earn the next one 🏓`);
              renderProfile();
            } catch (e) { toast(e.message); }
          });
          el.querySelector('#pf-skill-keep').addEventListener('click', () => {
            localStorage.setItem(`pp_skill_nudge_${suggested}`, '1');
            el.querySelector('#pf-skill-nudge').remove();
          });
        }

        // Brag line built from real numbers, carrying the invite deep link.
        statsEl.insertAdjacentHTML('beforeend',
          '<button class="btn btn-secondary btn-sm btn-block" id="pf-share-season" style="margin-top:12px">📤 Share my season</button>');
        el.querySelector('#pf-share-season').addEventListener('click', async () => {
          const bits = [];
          if (me.ranked_wins + me.ranked_losses > 0) bits.push(`${me.ranked_wins}–${me.ranked_losses} ranked · rating ${me.rating}`);
          if (stats.games_this_month) bits.push(`${stats.games_this_month} game${stats.games_this_month === 1 ? '' : 's'} this month`);
          if (stats.week_streak >= 2) bits.push(`${stats.week_streak}-week play streak 🔥`);
          if (stats.top_court) bits.push(`home turf: ${stats.top_court.name}`);
          const text = `My season on Third Shot 🏓 ${bits.join(' · ')}. Come play with me!`;
          const url = `${location.origin}/u/${me.id}`; // short link → OG preview in chat apps
          try {
            if (navigator.share) await navigator.share({ title: 'Third Shot', text, url });
            else { await navigator.clipboard.writeText(`${text} ${url}`); toast('Season copied to share 📋'); }
          } catch { /* user cancelled */ }
        });
        el.querySelector('[data-pfcourt-top]')?.addEventListener('click', (e) => openCourtDetail(Number(e.currentTarget.dataset.pfcourtTop)));
        bindUserButtons(statsEl);
      }
    } catch { /* ignore */ }

    // Saved courts (home court first), tappable into court detail.
    try {
      const favs = favoritesResult.status === 'fulfilled' ? favoritesResult.value : null;
      if (!favs) throw favoritesResult.reason;
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
      const history = historyResult.status === 'fulfilled' ? historyResult.value : null;
      if (!history) throw historyResult.reason;
      if (history.items.length) {
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
    const colors = [
      ['#2f9e44', 'Green'], ['#1971c2', 'Blue'], ['#e8590c', 'Orange'], ['#9c36b5', 'Purple'],
      ['#0c8599', 'Teal'], ['#e03131', 'Red'], ['#f08c00', 'Amber'], ['#5f3dc4', 'Indigo'],
    ];
    const modal = openModal(`
      ${modalHead('Edit profile')}
      <form id="ep-form" novalidate>
      <div class="form-field"><label for="ep-name">Display name</label><input type="text" id="ep-name" value="${esc(me.display_name)}" maxlength="60" autocomplete="name" /></div>
      <div class="form-field"><label for="ep-bio">Bio</label><textarea id="ep-bio" rows="2" maxlength="300">${esc(me.bio || '')}</textarea></div>
      <div class="form-field">
        <label for="ep-skill">Skill level</label>
        <select id="ep-skill">
          ${['beginner', 'intermediate', 'advanced', 'pro'].map((s) => `<option value="${s}" ${me.skill_level === s ? 'selected' : ''}>${skillLabel(s)}</option>`).join('')}
        </select>
      </div>
      <div class="form-field">
        <label for="ep-avatar-url">Profile photo (optional)</label>
        <div class="row" style="gap:10px">
          <div id="ep-avatar-preview">${avatarHtml(me)}</div>
          <input type="url" id="ep-avatar-url" placeholder="Paste an image URL…" value="${esc(me.avatar_url || '')}" autocomplete="url" style="flex:1" />
        </div>
        <p class="row-sub" style="margin-top:4px">Leave blank to use your colored initials.</p>
      </div>
      <div class="form-field">
        <label id="ep-avatar-color-label">Avatar color</label>
        <div role="group" aria-labelledby="ep-avatar-color-label" style="display:flex;gap:8px;flex-wrap:wrap">
          ${colors.map(([value, label]) => `<button type="button" class="avatar" data-color="${value}" aria-label="${label}" aria-pressed="${me.avatar_color === value}" style="background:${value};outline:${me.avatar_color === value ? '3px solid var(--ink)' : 'none'}">${esc(initials(me.display_name))}</button>`).join('')}
        </div>
      </div>
      <div class="form-field">
        <label id="ep-availability-label">Usually plays</label>
        <p class="row-sub" id="ep-availability-hint" style="margin-bottom:6px">Tap when you typically play — helps players find partners on their schedule.</p>
        <div role="group" aria-labelledby="ep-availability-label" aria-describedby="ep-availability-hint">
        ${AVAIL_PARTS.map(([part, emoji, partLabel]) => `
          <div class="av-row">
            <span class="av-emoji" title="${partLabel}" style="display:inline-flex;flex-direction:column;align-items:center;min-width:56px">${emoji}<span style="font-size:8px;font-weight:700;color:var(--ink-soft);letter-spacing:.02em">${partLabel.slice(0, -1).toUpperCase()}</span></span>
            ${AVAIL_DAYS.map((d) => `
              <button type="button" class="av-chip ${(me.availability || []).includes(`${d}-${part}`) ? 'active' : ''}" data-av="${d}-${part}" aria-label="${partLabel} ${d}" aria-pressed="${(me.availability || []).includes(`${d}-${part}`)}">${d[0].toUpperCase()}</button>`).join('')}
          </div>`).join('')}
        </div>
      </div>
      <div class="form-field">
        <label for="ep-court-search">Home court</label>
        <input type="search" id="ep-court-search" placeholder="${me.home_court_name ? esc(me.home_court_name) : 'Search courts…'}" autocomplete="off" />
        <input type="hidden" id="ep-court-id" value="${me.home_court_id || ''}" />
        <div id="ep-court-results"></div>
      </div>
      <button type="submit" class="btn btn-primary btn-block" id="ep-save">Save</button>
      </form>
      <details style="margin-top:22px">
        <summary style="font-size:13px;font-weight:600;cursor:pointer">🔔 Notifications</summary>
        <div style="margin-top:10px">
          <p class="row-sub" style="margin-bottom:8px">Silence the optional ones — score confirmations, invites, and challenges always come through.</p>
          ${Object.entries((state.me && state.me.muteable_notifications) || {}).map(([kind, label]) => `
            <label class="row" style="gap:10px;padding:7px 0;cursor:pointer">
              <input type="checkbox" class="ep-notif-toggle" data-kind="${kind}" ${(state.me.muted_notifications || []).includes(kind) ? '' : 'checked'} style="width:18px;height:18px;flex:0 0 auto" />
              <span style="font-size:14px">${esc(label)}</span>
            </label>`).join('')}
        </div>
      </details>
      <details style="margin-top:22px" id="ep-blocked-wrap">
        <summary style="font-size:13px;font-weight:600;cursor:pointer">🚫 Blocked players</summary>
        <div id="ep-blocked" style="margin-top:10px"><div class="row-sub">Loading…</div></div>
      </details>
      <details style="margin-top:22px">
        <summary style="font-size:13px;font-weight:600;cursor:pointer">Change password</summary>
        <div class="form-field" style="margin-top:10px">
          <label class="sr-only" for="ep-pw-current">Current password</label>
          <input type="password" id="ep-pw-current" placeholder="Current password" autocomplete="current-password" />
          <label class="sr-only" for="ep-pw-new">New password</label>
          <input type="password" id="ep-pw-new" placeholder="New password (6+ characters)" autocomplete="new-password" style="margin-top:8px" />
          <button type="button" class="btn btn-secondary btn-block" id="ep-pw-save" style="margin-top:8px">Update password</button>
        </div>
      </details>
      <details style="margin-top:22px">
        <summary style="color:#e03131;font-size:13px;font-weight:600;cursor:pointer">Danger zone</summary>
        <div class="form-field" style="margin-top:10px">
          <label for="ep-delete-password">Delete account</label>
          <p class="row-sub" style="margin-bottom:8px">Permanently removes your profile, friends, messages, and check-ins. Completed match results stay for your opponents, shown as “Deleted player”. This cannot be undone.</p>
          <input type="password" id="ep-delete-password" placeholder="Confirm your password" autocomplete="current-password" />
          <button type="button" class="btn btn-danger btn-block" id="ep-delete" style="margin-top:8px">Delete my account</button>
        </div>
      </details>
    `);

    const formUX = bindModalFormUX(modal, '#ep-save', { draftKey: 'edit-profile' });
    modal.querySelectorAll('[data-av]').forEach((chip) =>
      chip.addEventListener('click', () => {
        const active = chip.classList.toggle('active');
        chip.setAttribute('aria-pressed', String(active));
      }));

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
      modal.querySelectorAll('[data-color]').forEach((x) => {
        const active = x === b;
        x.style.outline = active ? '3px solid var(--ink)' : 'none';
        x.setAttribute('aria-pressed', String(active));
      });
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
          <button type="button" class="court-suggestion" data-pick="${c.id}" data-name="${esc(c.name)}" style="margin:6px 0">
            <span class="row-main" style="display:block">
              <span class="row-title" style="display:block;font-size:14px">${esc(c.name)}</span>
              <span class="row-sub" style="display:block">${esc(c.city)}</span>
            </span>
          </button>`).join('');
        modal.querySelectorAll('[data-pick]').forEach((row) => row.addEventListener('click', () => {
          modal.querySelector('#ep-court-id').value = row.dataset.pick;
          courtSearch.value = row.dataset.name;
          modal.querySelector('#ep-court-results').innerHTML = '';
        }));
      }, 300);
    });

    // Blocked players — the one place they still show, so unblocking is possible.
    const loadBlocked = async () => {
      const box = modal.querySelector('#ep-blocked');
      try {
        const data = await api('/users/blocked');
        box.innerHTML = data.items.length
          ? data.items.map((u) => `
              <div class="row" style="margin-bottom:8px">
                ${avatarHtml(u, 'sm')}
                <div class="row-main"><div class="row-title" style="font-size:14px">${esc(u.display_name)}</div></div>
                <button class="btn btn-secondary btn-sm" data-unblock="${u.id}">Unblock</button>
              </div>`).join('')
          : '<div class="row-sub">No one — your courts are drama-free 🌿</div>';
        box.querySelectorAll('[data-unblock]').forEach((b) => b.addEventListener('click', async () => {
          try {
            await api(`/users/${b.dataset.unblock}/unblock`, { method: 'POST' });
            toast('Unblocked');
            loadBlocked();
          } catch (e) { toast(e.message); }
        }));
      } catch { box.innerHTML = '<div class="row-sub">Could not load right now.</div>'; }
    };
    modal.querySelector('#ep-blocked-wrap').addEventListener('toggle', (e) => {
      if (e.target.open) loadBlocked();
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
      } catch (e) {
        // The generic auth message talks about email — confusing here.
        toast(/email or password/i.test(e.message) ? 'Current password is incorrect' : e.message);
      }
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

    modal.querySelector('#ep-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      formUX.clearError();
      const nameInput = modal.querySelector('#ep-name');
      if (!nameInput.value.trim()) {
        formUX.showError('Please enter a display name.', nameInput);
        return;
      }
      const finishSubmitting = formUX.startSubmitting('Saving profile…');
      if (!finishSubmitting) return;
      try {
        const body = {
          display_name: nameInput.value.trim(),
          bio: modal.querySelector('#ep-bio').value.trim(),
          skill_level: modal.querySelector('#ep-skill').value,
          avatar_color: color,
          avatar_url: avatarUrlInput.value.trim(),
          availability: [...modal.querySelectorAll('[data-av].active')].map((c) => c.dataset.av),
          // Unchecked = muted.
          muted_notifications: [...modal.querySelectorAll('.ep-notif-toggle')]
            .filter((c) => !c.checked).map((c) => c.dataset.kind),
        };
        const courtId = modal.querySelector('#ep-court-id').value;
        if (courtId) body.home_court_id = Number(courtId);
        const data = await api('/me', { method: 'PATCH', body: JSON.stringify(body) });
        formUX.clearDraft({ disable: true });
        applyMe(data);
        closeModal(modal);
        toast('Profile updated');
        renderProfile();
      } catch (err) {
        finishSubmitting();
        const target = /display name/i.test(err.message) ? nameInput : null;
        formUX.showError(err.message, target);
      }
    });

    // Notification switches live below the Save button — apply them the
    // instant they're flipped so closing the sheet can't lose the change.
    modal.querySelectorAll('.ep-notif-toggle').forEach((t) => t.addEventListener('change', async () => {
      const muted = [...modal.querySelectorAll('.ep-notif-toggle')]
        .filter((c) => !c.checked).map((c) => c.dataset.kind);
      try {
        applyMe(await api('/me', { method: 'PATCH', body: JSON.stringify({ muted_notifications: muted }) }));
      } catch (e) {
        t.checked = !t.checked; // roll the switch back so the UI stays honest
        toast(e.message);
      }
    }));
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

    let emoji = '🏓';
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
    // Host can remove other players from an upcoming game (no-show swap).
    const canRemove = (p) => game.is_creator && game.status === 'upcoming' && p.user_id !== game.creator_id;
    const playerRow = (p) => `
      <div class="row" style="margin-bottom:8px">
        <div style="display:flex;align-items:center;gap:10px;flex:1;cursor:pointer" data-view-user="${p.user_id}">
          ${avatarHtml(p, 'sm')}
          <div class="row-main">
            <div class="row-title" style="font-size:14px">${esc(p.display_name)}${p.user_id === game.creator_id ? ' <span class="tag" style="margin:0 0 0 6px;font-size:10.5px;padding:2px 8px">Host</span>' : ''}${p.attending && game.status === 'upcoming' ? ' <span class="tag live" style="margin:0 0 0 6px;font-size:10.5px;padding:2px 8px">👋 Coming</span>' : ''}</div>
            <div class="row-sub">${skillLabel(p.skill_level)} · ${p.rating}${p.rating_delta != null ? ` · <span class="${p.rating_delta >= 0 ? 'delta-up' : 'delta-down'}">${p.rating_delta >= 0 ? '+' : ''}${p.rating_delta}</span>` : ''}</div>
          </div>
        </div>
        ${canRemove(p) ? `<button class="btn btn-secondary btn-sm" data-remove-player="${p.user_id}" title="Remove from game" aria-label="Remove ${esc(p.display_name)}">✕</button>` : ''}
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
        actions = `<button class="btn btn-primary btn-block" id="gs-join" style="padding:16px">${isChallenge ? '⚔️ Accept challenge' : '<svg class="pb-ic"><use href="#pb"/></svg> Join this game'}</button>`;
        if (isChallenge && game.players.length === 1) {
          actions += '<button class="btn btn-danger btn-block" id="gs-decline" style="margin-top:10px">Decline</button>';
        }
      } else if (!game.is_joined) {
        actions = game.waitlist_position
          ? `<div class="empty-state" style="padding:12px">⏳ You're #${game.waitlist_position} on the waitlist — we'll notify you when a spot opens.</div>
             <button class="btn btn-secondary btn-block" id="gs-waitlist-leave">Leave waitlist</button>`
          : `<button class="btn btn-primary btn-block" id="gs-waitlist" style="padding:16px">⏳ Join waitlist${game.waitlist_count ? ` · ${game.waitlist_count} waiting` : ''}</button>`;
      } else if (game.is_joined) {
        const startsAhead = new Date(game.scheduled_at).getTime() > Date.now();
        if (!startsAhead && game.players.length >= 2) {
          actions = `<button class="btn btn-primary btn-block" id="gs-score" style="padding:16px">📝 Enter the score</button>`;
        }
        if (startsAhead) {
          const mine = game.players.find((p) => p.user_id === (state.me && state.me.id));
          if (mine && !mine.attending) {
            // Vouching you'll show up is the main ask before a game starts.
            actions += `<button class="btn ${actions ? 'btn-secondary' : 'btn-primary'} btn-block" id="gs-attend" style="margin-top:${actions ? '10px' : '0'};padding:15px">👋 I'm coming — count me in</button>`;
          }
          if (game.spots_left > 0) {
            actions += `<button class="btn btn-secondary btn-block" id="gs-invite" style="margin-top:10px">＋ Invite a friend${game.spots_left ? ` · ${game.spots_left} spot${game.spots_left === 1 ? '' : 's'} left` : ''}</button>`;
            // Hosts can flag the game to the court room's regulars.
            if (game.is_creator && game.visibility === 'open' && game.court) {
              actions += '<button class="btn btn-secondary btn-block" id="gs-post-court" style="margin-top:10px">📣 Post to court chat</button>';
            }
          }
          actions += '<button class="btn btn-secondary btn-block" id="gs-calendar" style="margin-top:10px">📅 Add to calendar</button>';
          if (game.is_creator && game.recurrence !== 'weekly') {
            actions += '<button class="btn btn-secondary btn-block" id="gs-reschedule" style="margin-top:10px">🕑 Reschedule</button>';
          }
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
      const mvpBanner = game.mvp ? `
        <div class="card" style="text-align:center;padding:10px 14px;margin-bottom:10px">
          <b>🌟 MVP: ${esc(game.mvp.display_name)}</b>
          <div class="row-sub">${game.mvp.votes} vote${game.mvp.votes === 1 ? '' : 's'} from the game</div>
        </div>` : '';
      const votables = game.players.filter((p) => p.user_id !== (state.me && state.me.id));
      const voteChips = votables.length ? `
        <div class="row-sub" style="margin:0 0 6px 2px">${game.my_mvp_vote ? 'Your MVP vote:' : 'Who carried the game? Vote MVP:'}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">
          ${votables.map((p) => `<button class="btn btn-sm ${game.my_mvp_vote === p.user_id ? 'btn-primary' : 'btn-secondary'}" data-mvp="${p.user_id}">🌟 ${esc(p.display_name.split(' ')[0])}</button>`).join('')}
        </div>` : '';
      actions = `${mvpBanner}${voteChips}<button class="btn btn-secondary btn-block" id="gs-rematch">↺ Rematch at ${esc(court.name || 'this court')}</button>`;
    }

    return `
      <div class="modal-head">
        <div style="flex:1">
          <h3>${emoji} ${headline} ${game.game_type === 'ranked' ? '<span class="tag ranked" style="margin:0 0 0 6px">Ranked</span>' : '<span class="tag" style="margin:0 0 0 6px">Casual</span>'}${game.recurrence === 'weekly' ? '<span class="tag" style="margin:0 0 0 6px">🔁 Weekly</span>' : ''}${game.preferred_level && game.preferred_level !== 'any' ? `<span class="tag" style="margin:0 0 0 6px">🎚 ${skillLabel(game.preferred_level)}</span>` : ''}${game.club_name ? `<span class="tag" style="margin:0 0 0 6px">🏛 ${esc(game.club_name)}</span>` : ''}</h3>
          <div class="row-sub">${subline}</div>
        </div>
        ${game.is_joined ? `<button class="icon-btn" id="gs-chat" title="Game chat" aria-label="Game chat" style="box-shadow:none;font-size:17px;position:relative">💬${game.chat_unread ? `<span class="badge" style="top:-2px;right:-4px">${game.chat_unread > 9 ? '9+' : game.chat_unread}</span>` : ''}</button>` : ''}
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
      <div id="gs-weather"></div>
      <div id="gs-stakes"></div>
      ${game.notes ? `<div class="row-sub" style="margin:0 0 12px 4px">“${esc(game.notes)}”</div>` : ''}
      <div class="section-label">Players (${game.players.length}/${game.max_players})</div>
      ${playersHtml}
      <div style="margin-top:16px">${actions}</div>`;
  }

  async function openGameScreen(gameId) {
    const routeLoad = beginRoutedOverlayLoad({ kind: 'game', id: gameId });
    let game;
    try { game = await api(`/games/${gameId}`); } catch (e) {
      if (!routedOverlayLoadIsCurrent(routeLoad)) return;
      toast(e.message);
      clearDeadDeepLink(`#game/${gameId}`);
      return;
    }
    if (!routedOverlayLoadIsCurrent(routeLoad)) return;

    const modal = openModal('', { route: { kind: 'game', id: gameId }, label: 'Game details' });
    const box = modal.querySelector('.modal');
    let fingerprint = '';
    let rematchAttemptId = null;
    let rematchScheduledAt = null;

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
      box.querySelector('#gs-court')?.addEventListener('click', () => transitionModal(modal, () => openCourtDetail(court.id)));
      box.querySelector('#gs-chat')?.addEventListener('click', () => openGameChat(game));
      box.querySelector('#gs-calendar')?.addEventListener('click', () => downloadIcs(game));
      box.querySelector('#gs-post-court')?.addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        try {
          const spots = game.spots_left;
          await api(`/courts/${game.court.id}/chat`, {
            method: 'POST',
            body: JSON.stringify({
              body: `Open game ${fmtDateTime(game.scheduled_at)} — ${spots} spot${spots === 1 ? '' : 's'} left! Join: ${location.origin}/g/${game.id}`,
            }),
          });
          btn.textContent = '📣 Posted to court chat ✓';
          toast(`Posted to the ${game.court.name} room 📣`);
        } catch (err) { toast(err.message); btn.disabled = false; }
      });
      box.querySelector('#gs-invite')?.addEventListener('click', async () => {
        const modalLoad = beginRoutedOverlayLoad(null);
        let friends = [];
        try { friends = (await api('/friends')).friends || []; } catch { /* offline */ }
        if (!routedOverlayLoadIsCurrent(modalLoad)) return;
        const inGame = new Set(game.players.map((p) => p.user_id));
        const invitable = friends.filter((f) => !inGame.has(f.id));
        const sheet = openModal(`
          ${modalHead('＋ Invite a friend')}
          <p class="row-sub" style="margin-bottom:12px">They'll get an invite with a link to join.</p>
          <div id="gi-list">${invitable.length
            ? invitable.map((f) => `
                <button class="btn btn-secondary btn-block" data-invite-friend="${f.id}" style="margin-bottom:8px;text-align:left;display:flex;align-items:center;gap:8px">
                  ${avatarHtml(f, 'sm')} ${esc(f.display_name)}
                </button>`).join('')
            : `<div class="empty-state" style="padding:18px">${friends.length ? 'All your friends are already in this game <svg class="pb-ic"><use href="#pb"/></svg>' : 'Add friends first to invite them.'}</div>`}</div>
        `);
        sheet.querySelectorAll('[data-invite-friend]').forEach((b) => b.addEventListener('click', async () => {
          b.disabled = true;
          try {
            await api(`/games/${game.id}/invite`, { method: 'POST', body: JSON.stringify({ user_id: Number(b.dataset.inviteFriend) }) });
            closeModal(sheet);
            toast('Invite sent 📨');
          } catch (e) { toast(e.message); b.disabled = false; }
        }));
      });
      box.querySelector('#gs-attend')?.addEventListener('click', async () => {
        try {
          render(await api(`/games/${game.id}/attend`, { method: 'POST' }));
          toast("You're counted in 👋");
        } catch (e) { toast(e.message); }
      });
      // Ranked 1v1: show what's on the line and the rivalry so far.
      if (game.game_type === 'ranked' && game.status === 'upcoming'
          && game.players.length === 2 && state.me
          && game.players.some((p) => p.user_id === state.me.id)) {
        const opp = game.players.find((p) => p.user_id !== state.me.id);
        const mine = game.players.find((p) => p.user_id === state.me.id);
        if (opp && opp.rating != null && mine.rating != null) {
          const expected = 1 / (1 + 10 ** ((opp.rating - mine.rating) / 400));
          const winPts = Math.round(32 * (1 - expected));
          const losePts = Math.round(32 * expected);
          const el = box.querySelector('#gs-stakes');
          if (el) {
            el.innerHTML = `<div class="row-sub" style="text-align:center;margin:2px 0 10px">
              ⚡ Stakes: win <b style="color:var(--green-accent)">+${winPts}</b> · lose <b>−${losePts}</b></div>`;
            // Rivalry record loads after — it's a nice-to-have.
            api(`/users/${opp.user_id}`).then((prof) => {
              const h2h = prof.head_to_head;
              if (!h2h || !document.body.contains(el)) return;
              const lead = h2h.wins > h2h.losses ? 'You lead' : h2h.wins < h2h.losses ? 'They lead' : 'All square';
              const a = Math.max(h2h.wins, h2h.losses);
              const b = Math.min(h2h.wins, h2h.losses);
              el.insertAdjacentHTML('beforeend',
                `<div class="row-sub" style="text-align:center;margin:-6px 0 10px">🤺 ${lead} ${h2h.wins === h2h.losses ? `${h2h.wins}–${h2h.losses}` : `${a}–${b}`} vs ${esc(opp.display_name.split(' ')[0])}</div>`);
            }).catch(() => {});
          }
        }
      }
      // Playability heads-up for games starting soon (NWS summary covers ~6h).
      const startMs = new Date(game.scheduled_at).getTime();
      if (game.status === 'upcoming' && court.id
          && startMs - Date.now() < 6 * 3600e3 && startMs - Date.now() > -3600e3) {
        api(`/courts/${court.id}/weather`).then((w) => {
          const el = box.querySelector('#gs-weather');
          if (!el) return;
          const bits = [];
          if (!w.error && w.temp_f != null) {
            bits.push(`${weatherEmoji(w.short)} ${w.temp_f}°F${w.short ? ` · ${esc(w.short)}` : ''}${w.rain_soon ? ' · 🌧 rain likely around game time' : ''}`);
          }
          const cond = w.latest_condition;
          if (cond && COURT_CONDITION_LABELS[cond.condition]) {
            const [emoji, label] = COURT_CONDITION_LABELS[cond.condition];
            const mins = Math.max(1, Math.round((Date.now() - new Date(cond.reported_at)) / 60000));
            bits.push(`${emoji} ${esc(label)} — reported ${mins >= 60 ? `${Math.round(mins / 60)}h` : `${mins}m`} ago`);
          }
          if (!bits.length) return;
          el.innerHTML = `<div class="row-sub" style="text-align:center;margin:2px 0 10px">
            ${bits.join('<br>')}
          </div>`;
        }).catch(() => { /* forecast is a nicety */ });
      }
      box.querySelectorAll('[data-mvp]').forEach((b) => b.addEventListener('click', async () => {
        try {
          render(await api(`/games/${gameId}/mvp`, { method: 'POST', body: JSON.stringify({ user_id: Number(b.dataset.mvp) }) }));
          toast('MVP vote in 🌟');
        } catch (e) { toast(e.message); }
      }));
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
          toast(isChallenge ? 'Challenge accepted! ⚔️' : "You're in! 🏓");
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
        transitionModal(modal, () => openScoreModal(fresh, () => refreshMe()));
      });
      box.querySelector('#gs-confirm')?.addEventListener('click', async () => {
        try {
          const updated = await api(`/games/${gameId}/confirm`, { method: 'POST' });
          transitionModal(modal, () => showCelebration(updated));
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
        rematchAttemptId ||= newGameAttemptId();
        rematchScheduledAt ||= new Date().toISOString();
        const others = game.players.map((p) => p.user_id).filter((id) => id !== state.me.id);
        try {
          const rematch = await api('/games', {
            method: 'POST',
            body: JSON.stringify({
              court_id: court.id,
              scheduled_at: rematchScheduledAt,
              game_type: game.game_type,
              max_players: game.max_players,
              visibility: others.length ? 'private' : 'open',
              invite_user_ids: others,
              notes: '↺ Rematch!',
              client_attempt_id: rematchAttemptId,
            }),
          });
          toast(others.length ? 'Rematch is on — invites sent ⚔️' : 'Rematch is on ⚔️');
          refreshMe();
          transitionModal(modal, () => openGameScreen(rematch.id));
        } catch (err) { toast(err.message); btn.disabled = false; }
      });
      box.querySelector('#gs-leave')?.addEventListener('click', async (e) => {
        if (!confirm("Leave this game? The host and other players will see that your spot opened.")) return;
        const btn = e.currentTarget;
        btn.disabled = true;
        btn.textContent = 'Leaving…';
        try {
          await api(`/games/${gameId}/leave`, { method: 'POST' });
          toast('Left the game');
          closeModal(modal); refreshMe();
          if (state.tab === 'play') renderPlay();
        } catch (err) { toast(err.message); reopenFresh(); }
      });
      box.querySelector('#gs-cancel')?.addEventListener('click', async (e) => {
        if (!confirm('Cancel this game for everyone?')) return;
        const btn = e.currentTarget;
        btn.disabled = true;
        btn.textContent = 'Cancelling…';
        try {
          await api(`/games/${gameId}/cancel`, { method: 'POST' });
          toast('Game cancelled');
          closeModal(modal); refreshMe();
          if (state.tab === 'play') renderPlay();
        } catch (e) { toast(e.message); reopenFresh(); }
      });
      box.querySelector('#gs-reschedule')?.addEventListener('click', () => {
        const cur = new Date(game.scheduled_at);
        const pad2 = (n) => String(n).padStart(2, '0');
        const val = `${cur.getFullYear()}-${pad2(cur.getMonth() + 1)}-${pad2(cur.getDate())}T${pad2(cur.getHours())}:${pad2(cur.getMinutes())}`;
        const sheet = openModal(`
          ${modalHead('🕑 Reschedule game')}
          <p class="row-sub" style="margin-bottom:10px">Everyone in the game keeps their spot and gets re-notified.</p>
          <div class="form-field"><input type="datetime-local" id="rs-when" value="${val}" /></div>
          <button class="btn btn-primary btn-block" id="rs-save" style="padding:15px">Save new time</button>
        `);
        sheet.querySelector('#rs-save').addEventListener('click', async (e) => {
          const raw = sheet.querySelector('#rs-when').value;
          if (!raw) { toast('Pick a time'); return; }
          const when = new Date(raw);
          if (when.getTime() < Date.now() - 15 * 60000) { toast("That time's already passed"); return; }
          e.target.disabled = true;
          try {
            render(await api(`/games/${gameId}/reschedule`, { method: 'POST', body: JSON.stringify({ scheduled_at: when.toISOString() }) }));
            closeModal(sheet);
            toast('Game rescheduled — players notified 🕑');
          } catch (err) { toast(err.message); e.target.disabled = false; }
        });
      });
      box.querySelectorAll('[data-remove-player]').forEach((b) => b.addEventListener('click', async (e) => {
        e.stopPropagation();
        const uid = Number(b.dataset.removePlayer);
        if (!confirm('Remove this player from the game?')) return;
        try {
          render(await api(`/games/${gameId}/remove/${uid}`, { method: 'POST' }));
          toast('Player removed');
        } catch (err) { toast(err.message); }
      }));
      bindUserButtons(box);
    }

    render(game);

    // Live sync: while this screen is open, pick up joins, scores, confirmations…
    const pollTimer = setInterval(async () => {
      if (!document.body.contains(box)) { clearInterval(pollTimer); return; }
      if (document.hidden || state.connectionState === 'offline') return;
      try {
        const fresh = await api(`/games/${gameId}`);
        if (gameFingerprint(fresh) !== fingerprint) {
          render(fresh);
          refreshMe();
        }
      } catch { /* offline */ }
    }, 5000);
  }

  function safeNotificationOverlayRoute(actionUrl) {
    if (!actionUrl || typeof actionUrl !== 'string') return null;
    try {
      const url = new URL(actionUrl, location.origin);
      if (url.origin !== location.origin) return null;
      return normalizeOverlayRoute(url.hash);
    } catch { return null; }
  }

  async function openActivity() {
    const modalLoad = beginRoutedOverlayLoad(null);
    let data;
    try { data = await api('/notifications'); } catch (e) {
      if (!routedOverlayLoadIsCurrent(modalLoad)) return;
      toast(e.message); return;
    }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;
    const enableBtn = (typeof Notification !== 'undefined' && Notification.permission === 'default')
      ? '<button class="btn btn-secondary btn-block" id="act-enable" style="margin-bottom:12px">🔔 Enable phone notifications</button>'
      : '';
    const icons = { friend_request: '🤝', friend_accept: '🎉', game_join: '🏓', game_cancelled: '🚫', ranked_result: '🏆', game_invite: '📅', game_invite_direct: '📨', score_submitted: '📝', score_confirmed: '✅', score_disputed: '⚠️', challenge: '⚔️', challenge_declined: '🙅', game_reminder: '⏰', game_message: '💬', session_rsvp: '🔁', friend_checkin: '📍', court_game: '⭐', weekly_recap: '📊', game_logged: '✍️', badge_earned: '🏅', player_coming: '🏓', player_left: '🚪', tournament_join: '📥', tournament_invite: '🎽', tournament_withdraw: '↩️', tournament_start: '🏁', tournament_match: '🎯', tournament_score: '🆚', tournament_result: '👑', tournament_cancelled: '🚫', tournament_message: '💬', tournament_update: '🕑', tournament_reminder: '⏰', invite_declined: '🙅', club_join: '🙌', club_message: '💬', club_update: '🏛', club_invite: '🎟', club_game: '📣', league_update: '📦', league_match: '🎯', league_message: '💬', nearby_games: '🗓', streak_nag: '🔥' };
    // Where each notification taps to: game if it references one, else the other user for friend events.
    const targetFor = (n) => {
      const actionRoute = safeNotificationOverlayRoute(n.action_url);
      if (actionRoute) return { type: 'route', ...actionRoute };
      const relatedMatchId = Number(n.related_match_id || n.match_id || 0) || null;
      if (n.related_league_id) return { type: 'league', id: n.related_league_id, matchId: relatedMatchId };
      if (n.related_club_id) return { type: 'club', id: n.related_club_id };
      if (n.related_tournament_id) return { type: 'tournament', id: n.related_tournament_id, matchId: relatedMatchId };
      if (n.related_game_id) return { type: 'game', id: n.related_game_id };
      if (n.related_user_id && (n.kind === 'friend_request' || n.kind === 'friend_accept' || n.kind === 'friend_checkin' || n.kind === 'player_coming')) {
        return { type: 'user', id: n.related_user_id };
      }
      // No related row? Some kinds still have an obvious destination.
      if (n.kind === 'streak_nag' || n.kind === 'session_rsvp') return { type: 'tab', id: 'play' };
      if (n.kind === 'weekly_recap' || n.kind === 'badge_earned') return { type: 'tab', id: 'profile' };
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
        <div class="card row" ${t ? `data-notif-type="${t.type}" data-notif-kind="${t.kind || ''}" data-notif-id="${t.id}" data-notif-match="${t.matchId || ''}" style="cursor:pointer"` : ''}>
          ${n.read ? '' : '<span class="notif-dot"></span>'}
          <span style="font-size:20px">${icons[n.kind] || '🔔'}</span>
          <div class="row-main">
            <div class="row-title notif-title" style="font-size:14px;${n.read ? '' : 'font-weight:800'}">${esc(n.title)}</div>
            ${n.body ? `<div class="row-sub notif-body">${esc(n.body)}</div>` : ''}
            <div class="row-sub">${time}</div>
          </div>
          ${t ? '<span class="chev">›</span>' : ''}
        </div>`;
    });

    const modal = openModal(`
      ${modalHead('Activity')}
      ${enableBtn}
      ${data.items.length ? `<div style="text-align:right;margin-bottom:6px"><button class="btn-link" id="act-clear" style="font-size:13px">Clear all</button></div>${listHtml}`
        : '<div class="empty-state"><span class="big">🔔</span>Nothing yet — go play some pickleball!<br><button class="btn btn-primary" data-goto="play" style="margin-top:10px"><svg class="pb-ic"><use href="#pb"/></svg> Find a game</button></div>'}
    `);
    modal.querySelector('#act-clear')?.addEventListener('click', async () => {
      try {
        await api('/notifications', { method: 'DELETE' });
        closeModal(modal);
        toast('Activity cleared');
        refreshMe();
      } catch (e) { toast(e.message); }
    });
    modal.querySelector('#act-enable')?.addEventListener('click', async (e) => {
      const result = await Notification.requestPermission();
      e.target.remove();
      toast(result === 'granted' ? 'Notifications on 🔔' : 'Notifications stay off');
      if (result === 'granted') syncPushSubscription();
    });
    modal.querySelectorAll('[data-notif-type]').forEach((row) => {
      makePressable(row, () => {
        closeModal(modal);
        const kind = row.dataset.notifType === 'route' ? row.dataset.notifKind : row.dataset.notifType;
        const matchId = Number(row.dataset.notifMatch || 0) || null;
        if (kind === 'tournament') openTournamentScreen(Number(row.dataset.notifId), matchId);
        else if (kind === 'game') openGameScreen(Number(row.dataset.notifId));
        else if (kind === 'club') openClubScreen(Number(row.dataset.notifId));
        else if (kind === 'league') openLeagueScreen(Number(row.dataset.notifId), matchId);
        else if (kind === 'court') openCourtDetail(Number(row.dataset.notifId));
        else if (row.dataset.notifType === 'tab') switchTab(row.dataset.notifId);
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
        const btn = e.currentTarget;
        if (btn.disabled) return;
        btn.disabled = true;
        btn.textContent = 'Checking out…';
        const prev = state.presence;
        const followupLoad = beginRoutedOverlayLoad(null);
        try {
          await api('/checkout', { method: 'POST' });
          toast('Checked out 👋');
          await refreshMe();
          if (state.map) fetchCourtsInView();
          if (routedOverlayLoadIsCurrent(followupLoad)) maybeAskConditions(prev);
        } catch (err) {
          toast(err.message);
          btn.disabled = false;
          btn.textContent = 'Check out';
        }
      });
    } else {
      el.classList.add('hidden');
      el.onclick = null;
    }
  }

  // ---------- Boot ----------
  // Persist a home area and move the app there.
  async function saveHomeArea(lat, lng, label, { silent = false } = {}) {
    try {
      const data = await api('/me', {
        method: 'PATCH',
        body: JSON.stringify({ home_lat: lat, home_lng: lng, home_area: label || '' }),
      });
      applyMe(data);
      state.areaLoc = [lat, lng];
      state.areaLabel = label || 'Home area';
      state.snapshotAreaProvisional = false;
      state.playGamesCache = null;
      state.chatFriendsCache = null;
      if (state.map) {
        beginCourtContextRefresh(`Finding courts near ${label || 'your home area'}…`);
        moveCourtMapWithoutRefresh(
          () => state.map.setView([lat, lng], 12, { animate: false }),
        );
        fetchCourtsInView({ surfaceError: true });
      }
      toast(label ? `Home area set to ${label} 📍` : 'Home area set 📍');
      return true;
    } catch (e) { if (!silent) toast(e.message); return false; }
  }

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
        state.userLoc = [lat, lng];
        updateUserDot();
        resolve(await saveHomeArea(lat, lng, label, { silent }));
      }, () => {
        if (!silent) toast('Could not get your location');
        resolve(false);
      }, { timeout: 10000 });
    });
  }

  // City typeahead against /geocode: tappable place rows under the input.
  function bindCitySearch(input, resultsEl, onPick) {
    let timer;
    input.addEventListener('input', () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 3) { resultsEl.innerHTML = ''; return; }
      timer = setTimeout(async () => {
        let places = [];
        try {
          places = ((await api(`/geocode?q=${encodeURIComponent(q)}`)).items || []).slice(0, 4);
        } catch { /* search is best-effort */ }
        resultsEl.innerHTML = places.map((p, i) => `
          <div class="card row" data-city="${i}" style="cursor:pointer;padding:10px 12px;margin-top:6px">
            <span>📍</span>
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(p.label)}</div>
              <div class="row-sub">${esc((p.detail || '').split(',').slice(1, 3).join(',').trim())}</div>
            </div>
          </div>`).join('');
        resultsEl.querySelectorAll('[data-city]').forEach((row) => {
          row.addEventListener('click', () => onPick(places[Number(row.dataset.city)]));
        });
      }, 350);
    });
  }

  // Home-area picker: device location or a city search. Used by onboarding
  // and the profile's Set/Change button.
  function openHomeAreaSheet({ intro, dismissLabel = 'Cancel', onSet, onDismiss } = {}) {
    const modal = openModal(`
      <div class="checkin-sheet">
        <div class="celebrate-emoji" style="font-size:46px">📍</div>
        <h3 style="margin:6px 0 2px">Set your home area</h3>
        <p class="row-sub" style="margin-bottom:18px">${esc(intro || 'Courts, games, and players near here greet you when the app opens.')}</p>
        <button class="btn btn-primary btn-block" id="ha-loc" style="padding:15px;margin-bottom:8px">Use my current location</button>
        <div class="form-field" style="margin:2px 0 0">
          <input type="search" id="ha-city" placeholder="Or search your city…" autocomplete="off" />
          <div id="ha-results"></div>
        </div>
        <button class="btn-link modal-close btn-block">${esc(dismissLabel)}</button>
      </div>
    `);
    const done = (ok) => {
      if (!ok) return;
      fetchCourtsInView();
      const top = currentOverlayEntry();
      if (!top || top.el !== modal || top.closing || !closeModal(modal)) return;
      if (onSet) onSet();
    };
    modal.querySelector('#ha-loc').addEventListener('click', async () => {
      done(await setHomeAreaFromLocation());
    });
    bindCitySearch(modal.querySelector('#ha-city'), modal.querySelector('#ha-results'), async (p) => {
      done(await saveHomeArea(p.lat, p.lng, p.label));
    });
    if (onDismiss) modal.querySelector('.modal-close').addEventListener('click', (e) => {
      e.stopPropagation();
      dismissModal(modal, onDismiss);
    });
  }

  // Onboarding step 2: seed the saved-courts list — saved courts power the
  // Saved filter, court rooms, and new-game pings, so an empty list is a
  // quieter app. Skips itself when there's nothing decent nearby.
  async function maybeSuggestStarterCourts(next) {
    const modalLoad = beginRoutedOverlayLoad(null);
    let courts = [];
    try {
      if (state.me && state.me.home_lat != null) {
        const data = await api(`/courts?lat=${state.me.home_lat}&lng=${state.me.home_lng}&radius=15&limit=30&sort=rating`);
        courts = (data.items || [])
          .sort((a, b) => (b.rating_avg ?? 0) - (a.rating_avg ?? 0) || (b.num_courts || 0) - (a.num_courts || 0))
          .slice(0, 3);
      }
    } catch { /* offline — skip the nicety */ }
    if (!routedOverlayLoadIsCurrent(modalLoad)) return;
    if (!courts.length) { next(); return; }

    const modal = openModal(`
      <div class="checkin-sheet">
        <div class="celebrate-emoji" style="font-size:46px">⭐</div>
        <h3 style="margin:6px 0 2px">Save your courts</h3>
        <p class="row-sub" style="margin-bottom:14px">The best-known courts near you — saved courts get their own chat room and game alerts.</p>
        ${courts.map((c) => `
          <div class="card row" style="padding:11px;text-align:left">
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(c.name)}</div>
              <div class="row-sub">${[esc(c.city || ''), `${c.num_courts} court${c.num_courts === 1 ? '' : 's'}`, c.rating_avg ? `⭐ ${c.rating_avg}` : ''].filter(Boolean).join(' · ')}</div>
            </div>
            <button class="btn btn-secondary btn-sm" data-star-court="${c.id}" style="font-size:16px;min-width:44px">☆</button>
          </div>`).join('')}
        <button class="btn btn-primary btn-block modal-close" style="margin-top:6px">Done</button>
      </div>
    `);
    modal.querySelectorAll('[data-star-court]').forEach((btn) => btn.addEventListener('click', async () => {
      try {
        const res = await api(`/courts/${btn.dataset.starCourt}/favorite`, { method: 'POST' });
        btn.textContent = res.favorited ? '★' : '☆';
        btn.classList.toggle('btn-primary', res.favorited);
        btn.classList.toggle('btn-secondary', !res.favorited);
        state.favIds = null; // map markers re-learn favorites on next fetch
      } catch (e) { toast(e.message); }
    }));
    modal.querySelector('.modal-close').addEventListener('click', (e) => {
      e.stopPropagation();
      dismissModal(modal, () => { next(); fetchCourtsInView(); });
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
    // Home area → starter courts → quick tour; dismissing skips ahead.
    openHomeAreaSheet({
      intro: 'So Third Shot opens to courts, games, and players near you — anywhere in the US.',
      dismissLabel: 'Maybe later',
      onSet: () => maybeSuggestStarterCourts(maybeShowTour),
      onDismiss: maybeShowTour,
    });
  }

  // One-time 3-step welcome tour for brand-new users.
  function maybeShowTour() {
    if (!state.me || localStorage.getItem('pp_tour_seen') === '1') return;
    localStorage.setItem('pp_tour_seen', '1');
    const steps = [
      { emoji: '🗺️', title: 'Find courts near you', body: "Browse the map, tap a court to see who's playing, and check in when you arrive." },
      { emoji: '🤝', title: 'Meet players', body: 'See players nearby and your friends — then add, message, or challenge them.' },
      { emoji: '🏓', title: 'Play a game', body: 'Start a game now or schedule one — casual, ranked, or a weekly open-play session.' },
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
          <button class="btn btn-primary btn-block" id="tour-next" style="padding:14px;margin-top:14px">${last ? 'Let\'s play <svg class="pb-ic"><use href="#pb"/></svg>' : 'Next'}</button>
          ${last ? '' : '<button class="btn-link btn-block" id="tour-skip">Skip</button>'}
        </div>`;
      box.querySelector('#tour-next').onclick = () => {
        if (last) {
          closeModal(modal);
          // "Let's play" should land where the games are, not back on the map.
          document.querySelector('[data-tab="play"]')?.click();
        } else { i += 1; render(); }
      };
      box.querySelector('#tour-skip')?.addEventListener('click', () => closeModal(modal));
    };
    render();
  }

  async function showMain() {
    $('#boot-screen')?.classList.add('hidden');
    // A shared browser can switch accounts without a page reload. Recenter the
    // existing Leaflet instance before revealing it so no prior map/search
    // context flashes for the new account.
    if (state.map) restoreAccountMapView();
    $('#auth-screen').classList.add('hidden');
    $('#main-screen').classList.remove('hidden');
    updatePlayHeader();
    switchTab(state.tab || 'play', { preserveOverlayIntent: true });
    if (state.connectionState !== 'offline') flushChatOutboxForAccount(state.me && state.me.id);
    if (autoCheckInEnabled()) startLocationWatch();
    setTimeout(maybeShowUsualTimeNudge, 1200); // after the map/feeds settle
    clearInterval(state.mePollTimer);
    let tick = 0;
    state.mePollTimer = setInterval(() => {
      if (document.hidden || state.connectionState === 'offline') return;
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
      <span style="font-size:20px"><svg class="pb-ic"><use href="#pb"/></svg></span>
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
    window.addEventListener('offline', () => setConnectionState('offline'));
    window.addEventListener('online', () => {
      setConnectionState('online');
      toast('Back online 🏓');
      if (state.token) {
        flushChatOutboxForAccount(state.me && state.me.id);
        refreshMe();
        refreshActiveView();
      }
    });
    $('#connection-retry')?.addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.textContent = 'Checking…';
      try {
        if (state.token) await refreshMe();
        refreshActiveView();
      } finally {
        btn.disabled = false;
        btn.textContent = 'Retry';
      }
    });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stopLocationWatch();
      else if (autoCheckInEnabled()) startLocationWatch();
    });
    setConnectionState(navigator.onLine ? 'online' : 'offline');
  }

  function setupServiceWorkerRouteMessages() {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.addEventListener('message', (event) => {
      if (event.data?.type !== 'open-overlay-route') return;
      const route = safeNotificationOverlayRoute(event.data.url);
      if (!route) return;
      if (state.token) {
        navigateOverlayRoute(route);
        return;
      }
      // Preserve the exact destination through login without triggering a
      // same-document fragment traversal on the signed-out screen.
      try {
        history.replaceState(
          overlayHistoryState(null, 0, null), '',
          `${baseAppUrl()}${overlayRouteHash(route)}`,
        );
      } catch { /* boot/login still remains usable */ }
    });
  }

  function setConnectionState(next) {
    const restored = state.connectionState === 'offline' && next === 'online';
    state.connectionState = next;
    const offline = next === 'offline';
    $('#offline-banner')?.classList.toggle('hidden', !offline);
    $('#main-screen')?.classList.toggle('is-offline', offline);
    if (restored && state.token && state.me) {
      queueMicrotask(() => flushChatOutboxForAccount(state.me && state.me.id));
    }
  }

  function refreshActiveView() {
    if (!state.token) return;
    if (state.tab === 'play') renderPlay();
    else if (state.tab === 'chat') renderChat();
    else if (state.tab === 'profile') renderProfile();
    else if (state.tab === 'courts' && state.map) refreshCourtResults();
  }

  function navigateOverlayRoute(candidate) {
    const route = normalizeOverlayRoute(candidate);
    if (!route || !state.token) return false;
    const ownerIndex = overlayStack.findLastIndex
      ? overlayStack.findLastIndex(
        (entry) => entry.ownsRoute && sameOverlayRoute(entry.route, route),
      )
      : (() => {
        for (let index = overlayStack.length - 1; index >= 0; index -= 1) {
          if (overlayStack[index].ownsRoute
              && sameOverlayRoute(overlayStack[index].route, route)) return index;
        }
        return -1;
      })();
    if (ownerIndex >= 0) {
      if (ownerIndex === overlayStack.length - 1) {
        overlayStack[ownerIndex].el.querySelector('.modal')?.focus({ preventScroll: true });
        return true;
      }
      const pops = overlayStack.slice(ownerIndex + 1)
        .reduce((count, entry) => count + Math.max(1, entry.historyPops || 1), 0);
      try { history.go(-pops); }
      catch { normalizeHistoryToLiveOverlay(); }
      return true;
    }
    if (route.kind === 'court') openCourtDetail(route.id);
    else if (route.kind === 'game') openGameScreen(route.id);
    else if (route.kind === 'tournament') openTournamentScreen(route.id, route.matchId || null);
    else if (route.kind === 'club') openClubScreen(route.id);
    else if (route.kind === 'league') openLeagueScreen(route.id, route.matchId || null);
    else return false;
    return true;
  }

  function rebuildReloadedMatchRouteIfNeeded() {
    const route = normalizeOverlayRoute(adoptOverlayEntry && adoptOverlayEntry.route);
    if (!route?.matchId || location.hash !== overlayRouteHash(route)) return false;
    const distance = Math.max(1, Number(adoptOverlayEntry.depth) || 1);
    pendingDeepMatchRebuild = route;
    adoptOverlayEntry = null;
    const finishWithoutTraversal = () => {
      if (!pendingDeepMatchRebuild) return;
      const fallbackRoute = pendingDeepMatchRebuild;
      pendingDeepMatchRebuild = null;
      try {
        history.replaceState(overlayHistoryState(null, 0, null), '', baseAppUrl());
      } catch { /* openModal still has a safe push fallback */ }
      navigateOverlayRoute(fallbackRoute);
    };
    if (history.length <= 1) {
      finishWithoutTraversal();
      return true;
    }
    try {
      history.go(-distance);
      setTimeout(finishWithoutTraversal, 800);
    } catch {
      finishWithoutTraversal();
    }
    return true;
  }

  function initialTabFromLocation() {
    const shortcut = new URLSearchParams(location.search).get('tab');
    if (['courts', 'play', 'chat', 'profile'].includes(shortcut)) return shortcut;
    if (/^#court\//.test(location.hash)) return 'courts';
    if (/^#(?:club|invite)\//.test(location.hash)) return 'chat';
    return state.tab || 'play';
  }

  function openDeepLink() {
    // PWA app-icon shortcuts land on /?tab=<name> — jump straight there.
    const tabParam = new URLSearchParams(location.search).get('tab');
    if (tabParam && ['courts', 'play', 'chat', 'profile'].includes(tabParam)) {
      const params = new URLSearchParams(location.search);
      params.delete('tab');
      const clean = `${location.pathname}${params.toString() ? `?${params}` : ''}${location.hash}`;
      try { history.replaceState(overlayHistoryState(null, 0, null), '', clean); } catch { /* ignore */ }
      if (state.tab !== tabParam || $(`#tab-${tabParam}`).classList.contains('hidden')) {
        switchTab(tabParam, { preserveOverlayIntent: true });
      }
    }
    const prepareRoute = (kind, id, matchId = null) => {
      const adopting = adoptOverlayEntry && adoptOverlayEntry.route.kind === kind
        && adoptOverlayEntry.route.id === id
        && (adoptOverlayEntry.route.matchId || null) === (matchId || null);
      if (!adopting) {
        try { history.replaceState(overlayHistoryState(null, 0, null), '', baseAppUrl()); } catch { /* ignore */ }
      }
    };
    const courtMatch = location.hash.match(/^#court\/(\d+)$/);
    if (courtMatch) { const id = Number(courtMatch[1]); prepareRoute('court', id); openCourtDetail(id); return true; }
    const gameMatch = location.hash.match(/^#game\/(\d+)$/);
    if (gameMatch) { const id = Number(gameMatch[1]); prepareRoute('game', id); openGameScreen(id); return true; }
    const tournamentMatchRoute = location.hash.match(/^#tournament\/(\d+)\/match\/(\d+)$/);
    if (tournamentMatchRoute) {
      const id = Number(tournamentMatchRoute[1]);
      const matchId = Number(tournamentMatchRoute[2]);
      prepareRoute('tournament', id, matchId);
      openTournamentScreen(id, matchId);
      return true;
    }
    const tournamentMatch = location.hash.match(/^#tournament\/(\d+)$/);
    if (tournamentMatch) { const id = Number(tournamentMatch[1]); prepareRoute('tournament', id); openTournamentScreen(id); return true; }
    const clubMatch = location.hash.match(/^#club\/(\d+)$/);
    if (clubMatch) { const id = Number(clubMatch[1]); prepareRoute('club', id); openClubScreen(id); return true; }
    const leagueMatchRoute = location.hash.match(/^#league\/(\d+)\/match\/(\d+)$/);
    if (leagueMatchRoute) {
      const id = Number(leagueMatchRoute[1]);
      const matchId = Number(leagueMatchRoute[2]);
      prepareRoute('league', id, matchId);
      openLeagueScreen(id, matchId);
      return true;
    }
    const leagueMatch = location.hash.match(/^#league\/(\d+)$/);
    if (leagueMatch) { const id = Number(leagueMatch[1]); prepareRoute('league', id); openLeagueScreen(id); return true; }
    const inviteMatch = location.hash.match(/^#invite\/(\d+)$/);
    if (inviteMatch) {
      try { history.replaceState(overlayHistoryState(null, 0, null), '', baseAppUrl()); } catch { /* ignore */ }
      openUserProfile(Number(inviteMatch[1]));
      return true;
    }
    return false;
  }

  // A focused installed app stays on the same document when a push notification
  // (or another in-app surface) navigates to a hash route. Boot-time routing is
  // not enough in that case, so honor the new destination without requiring a
  // reload. pushState/replaceState do not emit hashchange, which keeps ordinary
  // modal navigation from re-opening its own route.
  window.addEventListener('hashchange', () => {
    if (suppressNativeHashRoute) {
      suppressNativeHashRoute = null;
      return;
    }
    if (!state.token || !location.hash) return;
    const requested = normalizeOverlayRoute(location.hash);
    const current = currentOverlayEntry()?.route;
    if (requested && current
        && requested.kind === current.kind
        && requested.id === current.id
        && (requested.matchId || null) === (current.matchId || null)) return;
    if (requested) navigateOverlayRoute(requested);
    else openDeepLink();
  });

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

  // Ship unexpected browser errors to the server log — capped per session so
  // a render loop can't flood anything.
  let errorsReported = 0;
  function reportClientError(message, stack) {
    if (errorsReported >= 3) return;
    errorsReported += 1;
    try {
      fetch('/api/client-errors', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: String(message || '').slice(0, 300),
          stack: String(stack || '').slice(0, 600),
          url: location.href.slice(0, 200),
        }),
      }).catch(() => {});
    } catch { /* never let the reporter throw */ }
  }
  window.addEventListener('error', (e) => {
    reportClientError(e.message, e.error && e.error.stack);
  });
  // Stash Chrome's install offer so the profile can show a one-tap button.
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    state.installPrompt = e;
  });

  // Tap a partner's DM bubble to ❤️ it (tap again to take it back).
  document.addEventListener('click', async (e) => {
    const bubble = e.target.closest('.bubble.them[data-heart-msg]');
    if (!bubble) return;
    try {
      const res = await api(`/messages/${bubble.dataset.heartMsg}/heart`, { method: 'POST' });
      const badge = bubble.querySelector('.bubble-heart');
      if (res.hearted && !badge) bubble.insertAdjacentHTML('beforeend', '<span class="bubble-heart">❤️</span>');
      if (!res.hearted && badge) badge.remove();
    } catch (err) { toast(err.message); }
  });

  // Repaint ❤️ badges from a {message_id: count} map — polls carry it so
  // counts on already-rendered bubbles stay live without reopening the room.
  // No-ops for DM threads (their payloads don't include heart_counts).
  function applyRoomHearts(root, counts) {
    if (!counts || !root) return;
    root.querySelectorAll('.bubble[data-room-heart], .bubble.me[data-del-msg]').forEach((b) => {
      const id = b.dataset.roomHeart || b.dataset.delMsg;
      const n = counts[id] || 0;
      let badge = b.querySelector('[data-heart-badge]');
      if (n) {
        const label = `❤️${n > 1 ? ' ' + n : ''}`;
        if (badge) badge.textContent = label;
        else b.insertAdjacentHTML('afterbegin', `<span class="bubble-heart" data-heart-badge>${label}</span>`);
      } else if (badge) {
        badge.remove();
      }
    });
  }

  // Room chats: tap anyone else's bubble to toggle your ❤️; badge shows count.
  document.addEventListener('click', async (e) => {
    const bubble = e.target.closest('.bubble.them[data-room-heart]');
    if (!bubble) return;
    try {
      const res = await api(`/messages/${bubble.dataset.roomHeart}/heart`, { method: 'POST' });
      let badge = bubble.querySelector('[data-heart-badge]');
      if (res.heart_count) {
        const label = `❤️${res.heart_count > 1 ? ' ' + res.heart_count : ''}`;
        if (badge) badge.textContent = label;
        else bubble.insertAdjacentHTML('afterbegin', `<span class="bubble-heart" data-heart-badge>${label}</span>`);
      } else if (badge) {
        badge.remove();
      }
    } catch (err) { toast(err.message); }
  });

  // Tap your own chat bubble (any thread type) to delete it.
  document.addEventListener('click', async (e) => {
    const bubble = e.target.closest('.bubble.me[data-del-msg]');
    if (!bubble) return;
    if (!confirm('Delete this message?')) return;
    try {
      await api(`/messages/${bubble.dataset.delMsg}`, { method: 'DELETE' });
      (bubble.closest('[style*="align-self"]') || bubble).remove();
      toast('Message deleted');
    } catch (err) { toast(err.message); }
  });
  window.addEventListener('unhandledrejection', (e) => {
    const reason = e.reason || {};
    reportClientError(reason.message || String(e.reason), reason.stack);
  });

  // --- Web push: mirror in-app notifications to this device. Quietly does
  // nothing unless the server has VAPID keys and the user granted permission.
  function urlBase64ToUint8Array(base64) {
    const padding = '='.repeat((4 - (base64.length % 4)) % 4);
    const raw = atob((base64 + padding).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
  }

  let pushResetPromise = Promise.resolve();
  function revokePushSubscription(authToken) {
    pageNotifications.forEach((notification) => {
      try { notification.close(); } catch { /* already closed */ }
    });
    pageNotifications.clear();
    navigator.serviceWorker?.controller?.postMessage({
      type: 'push-auth-state', enabled: false,
    });
    pushResetPromise = (async () => {
      try {
        if (!('serviceWorker' in navigator)) return;
        const reg = await navigator.serviceWorker.ready;
        reg.active?.postMessage({ type: 'push-auth-state', enabled: false });
        if (typeof reg.getNotifications === 'function') {
          try {
            const visibleNotifications = await reg.getNotifications();
            visibleNotifications.forEach((notification) => notification.close());
          } catch { /* tray cleanup support varies; continue revoking push */ }
        }
        if (!('PushManager' in window)) return;
        const sub = await reg.pushManager.getSubscription();
        if (!sub) return;
        const requests = [sub.unsubscribe()];
        if (authToken) {
          requests.push(fetch('/api/push/unsubscribe', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${authToken}`,
            },
            body: JSON.stringify({ endpoint: sub.endpoint }),
            keepalive: true,
          }));
        }
        await Promise.allSettled(requests);
      } catch { /* local unsubscribe is best effort and must not block logout */ }
    })();
    return pushResetPromise;
  }

  async function syncPushSubscription() {
    try {
      await pushResetPromise;
      if (!state.token) return;
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
      if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
      const conf = await api('/push/public-key');
      if (!conf.enabled) return;
      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(conf.key),
        });
      }
      await api('/push/subscribe', { method: 'POST', body: JSON.stringify(sub.toJSON()) });
      navigator.serviceWorker.controller?.postMessage({
        type: 'push-auth-state', enabled: true,
      });
    } catch { /* push is a bonus, never block the app */ }
  }

  async function boot() {
    applyTheme();
    if ('serviceWorker' in navigator
        && (location.protocol === 'https:' || ['localhost', '127.0.0.1'].includes(location.hostname))) {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    }
    if (!state.token) revokePushSubscription(null);
    // Remember a friend's invite link across the signup flow, and greet the
    // newcomer with who invited them.
    const inviteRef = location.hash.match(/^#invite\/(\d+)$/);
    if (inviteRef && !state.token) {
      localStorage.setItem('pp_invite_ref', inviteRef[1]);
      try { history.replaceState(overlayHistoryState(null, 0, null), '', baseAppUrl()); } catch { /* ignore */ }
      api(`/invite/${inviteRef[1]}`).then((card) => {
        const tagline = document.querySelector('.auth-tagline');
        if (!tagline || document.querySelector('.invite-hello')) return;
        const el = document.createElement('div');
        el.className = 'invite-hello';
        el.innerHTML = `${avatarHtml(card, 'sm')} <span><b>${esc(card.display_name)}</b> invited you to play <svg class="pb-ic"><use href="#pb"/></svg></span>`;
        tagline.after(el);
      }).catch(() => { /* inviter gone — sign up normally */ });
    }
    setupAuth();
    setupTabs();
    setupPlay();
    setupChat();
    setupEmptyStateCtas();
    setupPullToRefresh();
    setupConnectivity();
    setupServiceWorkerRouteMessages();
    state.tab = initialTabFromLocation();
    if (state.token) {
      const snapshot = readMeSnapshot();
      let initialRouteHandled = false;
      if (snapshot) {
        // Returning players get useful, token-scoped UI immediately. The live
        // response below quietly refreshes it instead of holding the screen blank.
        applyMe(snapshot.data, { persist: false, provisional: true });
        await showMain();
        initialRouteHandled = rebuildReloadedMatchRouteIfNeeded() || openDeepLink();
      }
      try {
        const freshMe = await api('/me', { timeoutMs: snapshot ? 5000 : 8000 });
        applyMe(freshMe, { reconcileSnapshot: !!snapshot });
        if (!snapshot) {
          await showMain();
          initialRouteHandled = rebuildReloadedMatchRouteIfNeeded() || openDeepLink();
        }
        if (!initialRouteHandled) maybeOnboardHomeArea();
        syncPushSubscription();
        return;
      } catch (err) {
        if (err.isStaleSession) return;
        // 401 already called logout() inside api() and cleared the token. Any
        // other refresh failure keeps the authenticated snapshot visible
        // instead of stacking the login screen over a stale main screen.
        if (snapshot && state.token) {
          if (err.isNetworkError) setConnectionState('offline');
          else toast("Couldn't refresh yet — showing your last update");
          return;
        }
        // Authentication failures still fall through to the login screen.
      }
    }
    $('#boot-screen')?.classList.add('hidden');
    $('#auth-screen').classList.remove('hidden');
  }

  boot();
})();
