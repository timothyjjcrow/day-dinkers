/* Third Shot — simple social pickleball app */
(() => {
  'use strict';

  // ---------- State ----------
  const DEFAULT_CENTER = [33.6695, -117.8231]; // Orange County, CA
  const ME_POLL_INTERVAL_MS = 60_000;
  const LIVE_DETAIL_POLL_INTERVAL_MS = 15_000;
  const COMPETITION_POLL_INTERVAL_MS = 20_000;
  const state = {
    token: localStorage.getItem('pp_token') || null,
    me: null,
    presence: null,
    unreadMessages: 0,
    pendingRequests: 0,
    communityRoomUnread: 0,
    communityMessageUnread: 0,
    communityGroupUnread: 0,
    gamesToConfirm: 0,
    lastNotifId: null,
    tab: 'play',
    playSeg: 'games',
    chatSeg: 'chats',
    peopleMode: 'friends',
    nearbySkill: '',
    boardPeriod: 'all',
    map: null,
    markers: null,
    courtFilters: {
      active: false,
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
    activeArrival: null,
    activePlayPulse: null,
  };
  const pageNotifications = new Set();
  let meRequestGeneration = 0;
  let rallyArrivalInFlight = null;
  let playPulseCreateInFlight = null;
  const playPulseAcceptInFlight = new Map();

  function stopThreadPolling() {
    if (state.threadPollTimer && typeof state.threadPollTimer.stop === 'function') {
      state.threadPollTimer.stop();
    } else {
      clearInterval(state.threadPollTimer);
    }
    state.threadPollTimer = null;
  }

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

  // Game plans are account-scoped. Editable drafts expire quickly, while an
  // unresolved POST is retained until the server gives a definitive answer.
  // The login token already persists on this device, so keeping recovery state
  // through a PWA/browser restart does not extend access beyond the login.
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
  const INSTANT_RALLY_ATTEMPT_TTL = 2 * 60 * 60 * 1000;
  const INSTANT_RALLY_ATTEMPT_PREFIX = 'pp_instant_rally_v2:';
  const RALLY_ARRIVAL_ATTEMPT_PREFIX = 'pp_rally_arrival_v1:';
  const PLAY_PULSE_CREATE_ATTEMPT_PREFIX = 'pp_play_pulse_create_v1:';
  const PLAY_PULSE_ACCEPT_ATTEMPT_PREFIX = 'pp_play_pulse_accept_v1:';
  const GAME_OPEN_CALL_ATTEMPT_PREFIX = 'pp_game_open_call_v1:';
  const legacyInstantRallyAttemptKey = (userId = state.me && state.me.id) =>
    userId ? `pp_instant_rally_v1:${userId}` : null;
  const instantRallyAttemptKey = (userId = state.me && state.me.id, courtId = null) => {
    const accountId = Number(userId);
    const expectedCourtId = Number(courtId);
    return Number.isSafeInteger(accountId) && accountId > 0
      && Number.isSafeInteger(expectedCourtId) && expectedCourtId > 0
      ? `${INSTANT_RALLY_ATTEMPT_PREFIX}${accountId}:${expectedCourtId}` : null;
  };
  function pendingInstantRallyAttempt(userId = state.me && state.me.id, courtId = null) {
    const expectedCourtId = Number(courtId);
    if (!Number.isSafeInteger(expectedCourtId) || expectedCourtId <= 0) return null;
    const key = instantRallyAttemptKey(userId, expectedCourtId);
    if (!key) return null;
    const isRecoverable = (saved) => saved && typeof saved.id === 'string'
      && typeof saved.scheduledAt === 'string'
      && Number(saved.courtId) === expectedCourtId
      && Number.isFinite(saved.createdAt)
      && Date.now() - saved.createdAt <= INSTANT_RALLY_ATTEMPT_TTL;
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
      if (isRecoverable(saved)) return saved;
      // Recover an unresolved attempt written by the prior single-court-key
      // release, but only when it was already bound to this exact court.
      const legacyKey = legacyInstantRallyAttemptKey(userId);
      const legacy = JSON.parse(sessionStorage.getItem(legacyKey) || 'null');
      if (isRecoverable(legacy)) {
        sessionStorage.setItem(key, JSON.stringify(legacy));
        sessionStorage.removeItem(legacyKey);
        return legacy;
      }
      const fresh = {
        id: `rally-${newGameAttemptId()}`,
        scheduledAt: new Date().toISOString(),
        createdAt: Date.now(),
        courtId: expectedCourtId,
      };
      sessionStorage.setItem(key, JSON.stringify(fresh));
      return fresh;
    } catch {
      return {
        id: `rally-${newGameAttemptId()}`,
        scheduledAt: new Date().toISOString(),
        createdAt: Date.now(),
        courtId: expectedCourtId,
      };
    }
  }
  function clearInstantRallyAttempt(
    userId = state.me && state.me.id, courtId = null, attemptId = null,
  ) {
    const accountId = Number(userId);
    if (!Number.isSafeInteger(accountId) || accountId <= 0) return;
    try {
      const removeOwned = (key) => {
        if (!key) return;
        if (attemptId) {
          const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
          if (!saved || saved.id !== attemptId) return;
        }
        sessionStorage.removeItem(key);
      };
      const expectedCourtId = Number(courtId);
      if (Number.isSafeInteger(expectedCourtId) && expectedCourtId > 0) {
        removeOwned(instantRallyAttemptKey(accountId, expectedCourtId));
        const legacyKey = legacyInstantRallyAttemptKey(accountId);
        const legacy = JSON.parse(sessionStorage.getItem(legacyKey) || 'null');
        if (legacy && Number(legacy.courtId) === expectedCourtId
            && (!attemptId || legacy.id === attemptId)) sessionStorage.removeItem(legacyKey);
        return;
      }
      const prefix = `${INSTANT_RALLY_ATTEMPT_PREFIX}${accountId}:`;
      const keys = [];
      for (let index = 0; index < sessionStorage.length; index += 1) {
        const key = sessionStorage.key(index);
        if (key?.startsWith(prefix)) keys.push(key);
      }
      keys.forEach((key) => sessionStorage.removeItem(key));
      sessionStorage.removeItem(legacyInstantRallyAttemptKey(accountId));
    } catch { /* storage unavailable */ }
  }
  const rallyArrivalAttemptKey = (userId = state.me && state.me.id, gameId = null) => {
    const accountId = Number(userId);
    const expectedGameId = Number(gameId);
    return Number.isSafeInteger(accountId) && accountId > 0
      && Number.isSafeInteger(expectedGameId) && expectedGameId > 0
      ? `${RALLY_ARRIVAL_ATTEMPT_PREFIX}${accountId}:${expectedGameId}` : null;
  };
  function readRallyArrivalAttempt(userId = state.me && state.me.id, gameId = null) {
    const key = rallyArrivalAttemptKey(userId, gameId);
    if (!key) return null;
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
      const etaMinutes = Number(saved && saved.etaMinutes);
      const valid = saved && typeof saved.id === 'string'
        && /^[a-zA-Z0-9_-]{16,80}$/.test(saved.id)
        && [5, 10, 15].includes(etaMinutes)
        && Number.isFinite(Number(saved.createdAt));
      if (valid) return { ...saved, etaMinutes };
      sessionStorage.removeItem(key);
    } catch { /* malformed or unavailable storage is handled by the caller */ }
    return null;
  }
  function pendingRallyArrivalAttempt(
    userId = state.me && state.me.id, gameId = null, etaMinutes = 10,
  ) {
    const key = rallyArrivalAttemptKey(userId, gameId);
    const eta = Number(etaMinutes);
    if (!key || ![5, 10, 15].includes(eta)) return null;
    const existing = readRallyArrivalAttempt(userId, gameId);
    // A retry always reuses the original ETA and id. It must never silently
    // mint a second hold or extend the first one after an ambiguous response.
    if (existing) return existing;
    const fresh = {
      id: `arrival-${newGameAttemptId()}`,
      etaMinutes: eta,
      createdAt: Date.now(),
    };
    try {
      sessionStorage.setItem(key, JSON.stringify(fresh));
      return fresh;
    } catch { return null; }
  }
  function clearRallyArrivalAttempt(
    userId = state.me && state.me.id, gameId = null, attemptId = null,
  ) {
    const accountId = Number(userId);
    if (!Number.isSafeInteger(accountId) || accountId <= 0) return;
    try {
      const removeOwned = (key) => {
        if (!key) return;
        if (attemptId) {
          const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
          if (!saved || saved.id !== attemptId) return;
        }
        sessionStorage.removeItem(key);
      };
      const expectedGameId = Number(gameId);
      if (Number.isSafeInteger(expectedGameId) && expectedGameId > 0) {
        removeOwned(rallyArrivalAttemptKey(accountId, expectedGameId));
        return;
      }
      const prefix = `${RALLY_ARRIVAL_ATTEMPT_PREFIX}${accountId}:`;
      const keys = [];
      for (let index = 0; index < sessionStorage.length; index += 1) {
        const key = sessionStorage.key(index);
        if (key?.startsWith(prefix)) keys.push(key);
      }
      keys.forEach((key) => sessionStorage.removeItem(key));
    } catch { /* storage unavailable */ }
  }
  const playPulseCreateAttemptKey = (
    userId = state.me && state.me.id, courtId = null,
  ) => {
    const accountId = Number(userId);
    const expectedCourtId = Number(courtId);
    return Number.isSafeInteger(accountId) && accountId > 0
      && Number.isSafeInteger(expectedCourtId) && expectedCourtId > 0
      ? `${PLAY_PULSE_CREATE_ATTEMPT_PREFIX}${accountId}:${expectedCourtId}` : null;
  };
  function readPlayPulseCreateAttempt(userId = state.me && state.me.id, courtId = null) {
    const key = playPulseCreateAttemptKey(userId, courtId);
    if (!key) return null;
    const expectedCourtId = Number(courtId);
    const storages = [availableStorage('localStorage'), availableStorage('sessionStorage')];
    const candidates = storages.map((storage) => readStoredJson(storage, key)).filter((saved) => (
      saved && typeof saved.id === 'string' && /^[a-zA-Z0-9_-]{16,80}$/.test(saved.id)
      && Number(saved.courtId) === expectedCourtId && Number.isFinite(Number(saved.createdAt))
    )).sort((a, b) => Number(b.createdAt) - Number(a.createdAt));
    if (candidates.length) return { ...candidates[0], courtId: expectedCourtId };
    storages.forEach((storage) => removeStoredValue(storage, key));
    return null;
  }
  function pendingPlayPulseCreateAttempt(
    userId = state.me && state.me.id, courtId = null,
  ) {
    const key = playPulseCreateAttemptKey(userId, courtId);
    if (!key) return null;
    const existing = readPlayPulseCreateAttempt(userId, courtId);
    // An ambiguous retry keeps both the destination and id immutable. The
    // server's exact replay returns the original one-hour window without
    // extending it.
    if (existing) return existing;
    const fresh = {
      id: `pulse-create-${newGameAttemptId()}`,
      courtId: Number(courtId),
      createdAt: Date.now(),
    };
    return persistRecoveryValue(key, JSON.stringify(fresh)) ? fresh : null;
  }
  function clearPlayPulseCreateAttempt(
    userId = state.me && state.me.id, courtId = null, attemptId = null,
  ) {
    const key = playPulseCreateAttemptKey(userId, courtId);
    if (!key) return;
    for (const storage of [availableStorage('localStorage'), availableStorage('sessionStorage')]) {
      const saved = readStoredJson(storage, key);
      if (!attemptId || !saved || saved.id === attemptId) removeStoredValue(storage, key);
    }
  }
  function clearPlayPulseCreateAttempts(userId = state.me && state.me.id) {
    const accountId = Number(userId);
    if (!Number.isSafeInteger(accountId) || accountId <= 0) return;
    const prefix = `${PLAY_PULSE_CREATE_ATTEMPT_PREFIX}${accountId}:`;
    for (const storage of [availableStorage('localStorage'), availableStorage('sessionStorage')]) {
      if (!storage) continue;
      try {
        const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index))
          .filter((key) => key && key.startsWith(prefix));
        keys.forEach((key) => storage.removeItem(key));
      } catch { /* storage unavailable */ }
    }
  }
  const playPulseAcceptAttemptKey = (
    userId = state.me && state.me.id, pulseId = null,
  ) => {
    const accountId = Number(userId);
    const expectedPulseId = Number(pulseId);
    return Number.isSafeInteger(accountId) && accountId > 0
      && Number.isSafeInteger(expectedPulseId) && expectedPulseId > 0
      ? `${PLAY_PULSE_ACCEPT_ATTEMPT_PREFIX}${accountId}:${expectedPulseId}` : null;
  };
  function readPlayPulseAcceptAttempt(userId = state.me && state.me.id, pulseId = null) {
    const key = playPulseAcceptAttemptKey(userId, pulseId);
    if (!key) return null;
    const expectedPulseId = Number(pulseId);
    const storages = [availableStorage('localStorage'), availableStorage('sessionStorage')];
    const candidates = storages.map((storage) => readStoredJson(storage, key)).filter((saved) => (
      saved && typeof saved.id === 'string' && /^[a-zA-Z0-9_-]{16,80}$/.test(saved.id)
      && Number(saved.pulseId) === expectedPulseId
      && typeof saved.acceptCapability === 'string' && saved.acceptCapability.length >= 16
      && Number.isFinite(Number(saved.createdAt))
    )).sort((a, b) => Number(b.createdAt) - Number(a.createdAt));
    if (candidates.length) return { ...candidates[0], pulseId: expectedPulseId };
    storages.forEach((storage) => removeStoredValue(storage, key));
    return null;
  }
  function pendingPlayPulseAcceptAttempt(
    userId = state.me && state.me.id, pulseId = null, acceptCapability = '',
  ) {
    const key = playPulseAcceptAttemptKey(userId, pulseId);
    if (!key) return null;
    const existing = readPlayPulseAcceptAttempt(userId, pulseId);
    // A refreshed feed may rotate its short-lived viewer capability while the
    // unresolved mutation keeps the same id. Exact server replay is keyed by
    // that id, so replacing only the capability cannot create a second game.
    if (existing) {
      if (typeof acceptCapability === 'string' && acceptCapability.length >= 16
          && acceptCapability !== existing.acceptCapability) {
        const refreshed = {
          ...existing,
          acceptCapability,
          capabilityRefreshedAt: Date.now(),
        };
        return persistRecoveryValue(key, JSON.stringify(refreshed)) ? refreshed : existing;
      }
      return existing;
    }
    if (typeof acceptCapability !== 'string' || acceptCapability.length < 16) return null;
    const fresh = {
      id: `pulse-accept-${newGameAttemptId()}`,
      pulseId: Number(pulseId),
      acceptCapability,
      createdAt: Date.now(),
    };
    return persistRecoveryValue(key, JSON.stringify(fresh)) ? fresh : null;
  }
  function clearPlayPulseAcceptAttempt(
    userId = state.me && state.me.id, pulseId = null, attemptId = null,
  ) {
    const key = playPulseAcceptAttemptKey(userId, pulseId);
    if (!key) return;
    for (const storage of [availableStorage('localStorage'), availableStorage('sessionStorage')]) {
      const saved = readStoredJson(storage, key);
      if (!attemptId || !saved || saved.id === attemptId) removeStoredValue(storage, key);
    }
  }
  function clearPlayPulseAcceptAttempts(userId = state.me && state.me.id) {
    const accountId = Number(userId);
    if (!Number.isSafeInteger(accountId) || accountId <= 0) return;
    const prefix = `${PLAY_PULSE_ACCEPT_ATTEMPT_PREFIX}${accountId}:`;
    for (const storage of [availableStorage('localStorage'), availableStorage('sessionStorage')]) {
      if (!storage) continue;
      try {
        const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index))
          .filter((key) => key && key.startsWith(prefix));
        keys.forEach((key) => storage.removeItem(key));
      } catch { /* storage unavailable */ }
    }
  }
  const gameOpenCallAttemptKey = (
    userId = state.me && state.me.id, gameId = null,
  ) => {
    const accountId = Number(userId);
    const expectedGameId = Number(gameId);
    return Number.isSafeInteger(accountId) && accountId > 0
      && Number.isSafeInteger(expectedGameId) && expectedGameId > 0
      ? `${GAME_OPEN_CALL_ATTEMPT_PREFIX}${accountId}:${expectedGameId}` : null;
  };
  function readGameOpenCallAttempt(
    userId = state.me && state.me.id, gameId = null,
  ) {
    const key = gameOpenCallAttemptKey(userId, gameId);
    if (!key) return null;
    const expectedGameId = Number(gameId);
    const storages = [availableStorage('localStorage'), availableStorage('sessionStorage')];
    const candidates = storages.map((storage) => readStoredJson(storage, key)).filter((saved) => (
      saved && typeof saved.id === 'string'
      && /^[a-zA-Z0-9._:-]{1,64}$/.test(saved.id)
      && Number(saved.gameId) === expectedGameId
      && Number.isFinite(Number(saved.createdAt))
    )).sort((a, b) => Number(b.createdAt) - Number(a.createdAt));
    if (candidates.length) return { ...candidates[0], gameId: expectedGameId };
    storages.forEach((storage) => removeStoredValue(storage, key));
    return null;
  }
  function pendingGameOpenCallAttempt(
    userId = state.me && state.me.id, gameId = null,
  ) {
    const key = gameOpenCallAttemptKey(userId, gameId);
    if (!key) return null;
    const existing = readGameOpenCallAttempt(userId, gameId);
    // Keep this exact key even after acknowledgement. The server retains the
    // matching receipt, so an interrupted retry can never create new speech.
    if (existing) return existing;
    const fresh = {
      id: `open-call-${newGameAttemptId()}`,
      gameId: Number(gameId),
      createdAt: Date.now(),
    };
    return persistRecoveryValue(key, JSON.stringify(fresh)) ? fresh : null;
  }
  function clearGameOpenCallAttempts(userId = state.me && state.me.id) {
    const accountId = Number(userId);
    if (!Number.isSafeInteger(accountId) || accountId <= 0) return;
    const prefix = `${GAME_OPEN_CALL_ATTEMPT_PREFIX}${accountId}:`;
    for (const storage of [availableStorage('localStorage'), availableStorage('sessionStorage')]) {
      if (!storage) continue;
      try {
        const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index))
          .filter((key) => key && key.startsWith(prefix));
        keys.forEach((key) => storage.removeItem(key));
      } catch { /* storage unavailable */ }
    }
  }
  function sanitizePlannerInvitee(value) {
    if (!value || typeof value !== 'object') return null;
    const id = Number(value.id ?? value.user_id);
    if (!Number.isSafeInteger(id) || id <= 0) return null;
    return {
      id,
      display_name: String(value.display_name || 'Player').slice(0, 80),
      avatar_color: String(value.avatar_color || '').slice(0, 24),
      avatar_url: String(value.avatar_url || '').slice(0, 500),
      availability: [...new Set(Array.isArray(value.availability)
        ? value.availability.filter((slot) => /^(sun|mon|tue|wed|thu|fri|sat)-(am|pm|eve)$/.test(slot))
        : [])],
      skill_level: ['beginner', 'intermediate', 'advanced', 'pro'].includes(value.skill_level)
        ? value.skill_level : 'intermediate',
      rating: Number.isFinite(Number(value.rating)) ? Number(value.rating) : 1200,
    };
  }
  function sanitizeGameCreatePayload(value, expectedAttemptId = null) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const positiveId = (raw) => Number.isSafeInteger(Number(raw)) && Number(raw) > 0
      ? Number(raw) : null;
    const courtId = positiveId(value.court_id);
    const scheduled = typeof value.scheduled_at === 'string' ? new Date(value.scheduled_at) : null;
    const attemptId = typeof value.client_attempt_id === 'string'
      && /^[a-zA-Z0-9_-]{16,80}$/.test(value.client_attempt_id)
      ? value.client_attempt_id : null;
    if (!courtId || !scheduled || !Number.isFinite(scheduled.getTime()) || !attemptId
        || (expectedAttemptId && attemptId !== expectedAttemptId)) return null;
    const inviteIds = [...new Set((Array.isArray(value.invite_user_ids)
      ? value.invite_user_ids : []).map(positiveId).filter(Boolean))].slice(0, 20);
    const crewVersion = value.expected_crew_version == null || value.expected_crew_version === ''
      ? null : Number(value.expected_crew_version);
    const crewId = positiveId(value.crew_id);
    const visibility = ['open', 'friends', 'private'].includes(value.visibility)
      ? value.visibility : (inviteIds.length ? 'private' : 'open');
    return {
      court_id: courtId,
      scheduled_at: scheduled.toISOString(),
      game_type: value.game_type === 'ranked' ? 'ranked' : 'casual',
      visibility,
      recurrence: crewId ? 'none' : (value.recurrence === 'weekly' ? 'weekly' : 'none'),
      max_players: [2, 4, 6, 8, 10, 12].includes(Number(value.max_players))
        ? Number(value.max_players) : 4,
      preferred_level: ['any', 'beginner', 'intermediate', 'advanced', 'pro'].includes(value.preferred_level)
        ? value.preferred_level : 'any',
      notes: String(value.notes || '').trim().slice(0, 500),
      invite_user_ids: inviteIds,
      require_all_invitees: value.require_all_invitees === true,
      source_game_id: positiveId(value.source_game_id),
      club_id: positiveId(value.club_id),
      crew_id: positiveId(value.crew_id),
      expected_crew_version: Number.isSafeInteger(crewVersion) && crewVersion >= 0
        ? crewVersion : null,
      client_attempt_id: attemptId,
    };
  }
  function availableStorage(name) {
    try { return globalThis[name] || null; } catch { return null; }
  }
  function readStoredJson(storage, key) {
    if (!storage) return null;
    try {
      const value = storage.getItem(key);
      if (!value) return null;
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
    } catch { return null; }
  }
  function removeStoredValue(storage, key) {
    if (!storage) return;
    try { storage.removeItem(key); } catch { /* storage unavailable */ }
  }
  function persistRecoveryValue(key, value) {
    const persistent = availableStorage('localStorage');
    const fallback = availableStorage('sessionStorage');
    try {
      if (persistent) {
        persistent.setItem(key, value);
        removeStoredValue(fallback, key);
        return true;
      }
    } catch { /* use the per-tab fallback */ }
    try {
      if (fallback) {
        fallback.setItem(key, value);
        return true;
      }
    } catch { /* caller must not POST without recovery */ }
    return false;
  }
  function safeGameDraftRecord(raw) {
    if (!raw || raw.v !== GAME_DRAFT_VERSION || !Number.isFinite(raw.updatedAt)
        || raw.updatedAt > Date.now() + 60000) return null;
    const status = raw.status === 'submitting' ? 'submitting' : 'editing';
    // A response may have been lost after commit. Never age out the only exact
    // idempotency key; editable, never-submitted drafts can still expire.
    if (status !== 'submitting' && Date.now() - raw.updatedAt > GAME_DRAFT_TTL) return null;
    const allowed = (value, values, fallback) => values.includes(value) ? value : fallback;
    const id = (value) => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : null;
    const clientAttemptId = typeof raw.clientAttemptId === 'string'
      && /^[a-zA-Z0-9_-]{16,80}$/.test(raw.clientAttemptId) ? raw.clientAttemptId : null;
    const crewId = id(raw.crewId);
    const submittedPayload = sanitizeGameCreatePayload(raw.submittedPayload, clientAttemptId);
    // Pre-immutable builds stored the attempt key and planner fields but not a
    // submittedPayload. Keep those unresolved reservations: scheduled plans
    // are reconstructed below, while legacy "right now" plans stay locked and
    // point the player to My games instead of risking a fresh-key duplicate.
    if (status === 'submitting' && !clientAttemptId) return null;
    return {
      v: GAME_DRAFT_VERSION,
      updatedAt: raw.updatedAt,
      status,
      submitStartedAt: Number.isFinite(raw.submitStartedAt) ? raw.submitStartedAt : null,
      clientAttemptId,
      mode: allowed(raw.mode, ['now', 'later'], 'later'),
      courtId: id(raw.courtId),
      scheduledAt: typeof raw.scheduledAt === 'string' ? raw.scheduledAt : null,
      timeKind: allowed(raw.timeKind, ['preset', 'custom'], 'preset'),
      visibility: allowed(raw.visibility, ['open', 'friends', 'private'], 'open'),
      inviteUserIds: [...new Set(Array.isArray(raw.inviteUserIds) ? raw.inviteUserIds.map(id).filter(Boolean) : [])].slice(0, 20),
      invitees: (Array.isArray(raw.invitees) ? raw.invitees : [])
        .map(sanitizePlannerInvitee).filter(Boolean).slice(0, 20),
      requireAllInvitees: raw.requireAllInvitees === true,
      sourceLabel: String(raw.sourceLabel || '').slice(0, 80),
      availabilityLabel: String(raw.availabilityLabel || '').slice(0, 120),
      sourceGameId: id(raw.sourceGameId),
      crewId,
      crewVersion: raw.crewVersion != null && Number.isSafeInteger(Number(raw.crewVersion)) && Number(raw.crewVersion) >= 0
        ? Number(raw.crewVersion) : null,
      gameType: allowed(raw.gameType, ['casual', 'ranked'], 'casual'),
      maxPlayers: [2, 4, 6, 8, 10, 12].includes(Number(raw.maxPlayers)) ? Number(raw.maxPlayers) : 4,
      preferredLevel: allowed(raw.preferredLevel, ['any', 'beginner', 'intermediate', 'advanced', 'pro'], 'any'),
      clubId: id(raw.clubId),
      recurrence: crewId ? 'none' : allowed(raw.recurrence, ['none', 'weekly'], 'none'),
      notes: String(raw.notes || '').slice(0, 200),
      advancedOpen: !!raw.advancedOpen,
      submittedPayload,
    };
  }
  function readGameDraft() {
    const key = gameDraftKey();
    if (!key) return null;
    const storages = [availableStorage('localStorage'), availableStorage('sessionStorage')];
    const candidates = storages
      .map((storage) => safeGameDraftRecord(readStoredJson(storage, key)))
      .filter(Boolean)
      .sort((a, b) => b.updatedAt - a.updatedAt);
    if (candidates.length) return candidates[0];
    storages.forEach((storage) => removeStoredValue(storage, key));
    return null;
  }
  function writeGameDraft(draft) {
    const key = gameDraftKey();
    if (!key) return false;
    const value = JSON.stringify({ ...draft, v: GAME_DRAFT_VERSION, updatedAt: Date.now() });
    return persistRecoveryValue(key, value);
  }
  function clearGameDraft(userId = state.me && state.me.id) {
    const key = gameDraftKey(userId);
    if (!key) return;
    removeStoredValue(availableStorage('localStorage'), key);
    removeStoredValue(availableStorage('sessionStorage'), key);
  }
  const REMATCH_ATTEMPT_VERSION = 1;
  const rematchAttemptKey = (sourceGameId, userId = state.me && state.me.id) =>
    userId && sourceGameId ? `pp_rematch_attempt_v1:${userId}:${sourceGameId}` : null;
  const rematchClientAttemptId = (sourceGameId) => `rematch-source-v1-${Number(sourceGameId)}`;
  function safeRematchAttemptRecord(raw, sourceGameId) {
    if (!raw || raw.v !== REMATCH_ATTEMPT_VERSION || !Number.isFinite(raw.updatedAt)
        || raw.updatedAt > Date.now() + 60000
        || Number(raw.sourceGameId) !== Number(sourceGameId)) return null;
    const payload = sanitizeGameCreatePayload(raw.payload);
    if (!payload || Number(payload.source_game_id) !== Number(sourceGameId)) return null;
    const gameId = Number.isSafeInteger(Number(raw.gameId)) && Number(raw.gameId) > 0
      ? Number(raw.gameId) : null;
    return { sourceGameId: Number(sourceGameId), payload, gameId, updatedAt: raw.updatedAt };
  }
  function readRematchAttempt(sourceGameId) {
    const key = rematchAttemptKey(sourceGameId);
    if (!key) return null;
    const storages = [availableStorage('localStorage'), availableStorage('sessionStorage')];
    const candidates = storages
      .map((storage) => safeRematchAttemptRecord(readStoredJson(storage, key), sourceGameId))
      .filter(Boolean)
      .sort((a, b) => b.updatedAt - a.updatedAt);
    if (candidates.length) return candidates[0];
    storages.forEach((storage) => removeStoredValue(storage, key));
    return null;
  }
  function writeRematchAttempt(sourceGameId, payload, gameId = null) {
    const key = rematchAttemptKey(sourceGameId);
    const safePayload = sanitizeGameCreatePayload(payload);
    if (!key || !safePayload || Number(safePayload.source_game_id) !== Number(sourceGameId)) return null;
    const value = JSON.stringify({
      v: REMATCH_ATTEMPT_VERSION,
      sourceGameId: Number(sourceGameId),
      payload: safePayload,
      gameId: Number.isSafeInteger(Number(gameId)) && Number(gameId) > 0 ? Number(gameId) : null,
      updatedAt: Date.now(),
    });
    return persistRecoveryValue(key, value) ? safePayload : null;
  }
  function clearRematchAttempt(sourceGameId, userId = state.me && state.me.id) {
    const key = rematchAttemptKey(sourceGameId, userId);
    if (!key) return;
    removeStoredValue(availableStorage('localStorage'), key);
    removeStoredValue(availableStorage('sessionStorage'), key);
  }
  function clearRematchAttempts(userId = state.me && state.me.id) {
    if (!userId) return;
    const prefix = `pp_rematch_attempt_v1:${userId}:`;
    for (const storage of [availableStorage('localStorage'), availableStorage('sessionStorage')].filter(Boolean)) {
      try {
        const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index))
          .filter((key) => key && key.startsWith(prefix));
        keys.forEach((key) => storage.removeItem(key));
      } catch { /* storage unavailable */ }
    }
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
    rally_no_longer_active: 'That rally ended. Refresh nearby rallies to find the current one.',
    rally_full: 'That rally is fully committed.',
    arrival_slot_taken: 'Another player is already arriving, so that travel spot is held.',
    active_arrival_elsewhere: 'You already have a held spot at another rally.',
    arrival_already_active: 'Your spot is already held for this rally.',
    already_at_court: 'You’re already checked in at this court. Joining the rally instead.',
    active_checkin_elsewhere: 'You’re checked in at another court. Confirm this court before joining.',
    invalid_payload: 'That request could not be read. Refresh and try again.',
    invalid_court_id: 'Choose a valid court.',
    court_not_found: 'That court is no longer available.',
    court_closed: 'That court is marked closed right now. Choose another destination.',
    court_location_unavailable: 'That court needs a map location before it can be used as a destination.',
    active_checkin_present: 'You’re already checked in. Start or join a live rally at your current court instead.',
    active_arrival: 'You already have a spot held while heading to another rally.',
    active_rally: 'You already have a live rally in progress.',
    active_game: 'You already have a game starting during this hour.',
    pulse_already_active: 'You’re already marked free this hour.',
    pulse_conflict: 'Couldn’t confirm Free this hour. Try again.',
    pulse_not_found: 'That Free this hour post is no longer active.',
    pulse_start_window_closed: 'There is not enough time left to start this quick game.',
    invalid_accept_capability: 'That Free this hour post is no longer active.',
    invalid_eta_minutes: 'Choose a 5, 10, or 15 minute arrival time.',
    invalid_client_attempt_id: 'This saved action expired. Close this sheet and try again.',
    client_attempt_id_conflict: 'That saved action conflicts with an earlier request. Close this sheet and try again.',
    open_call_not_available: 'This game can no longer be posted to court chat.',
    open_call_not_found: 'There is no active court post for this game.',
    open_call_conflict: 'Couldn’t confirm the court post. Try again.',
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
    active_checkin_required: 'Check in at a court before starting a live rally.',
    active_checkin_court_mismatch: 'Confirm the court where you are playing now.',
    active_rally_elsewhere: 'You already have another live rally in progress.',
    rally_time_out_of_range: 'That rally attempt expired. Tap again to start a fresh one.',
    game_already_started: 'Too late — the game already has players.',
    already_joined: "You're already in this game.",
    user_blocked: "You can't interact with this player.",
    crew_changed: 'Someone in this crew is no longer available. Review the roster and try again.',
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
    clearPlayPulseCreateAttempts(accountId);
    clearPlayPulseAcceptAttempts(accountId);
    clearGameOpenCallAttempts(accountId);
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
    state.communityMessageUnread = 0;
    state.communityGroupUnread = 0;
    state.gamesToConfirm = 0;
    state.activeGame = null;
    state.activeArrival = null;
    state.activePlayPulse = null;
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
    clearLookingBanner();
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
    invalidateMeRequests();
    // Detach this account's request dedupe record. Its fetch may still settle,
    // but captured session ownership prevents it from touching the next login.
    instantRallyInFlight = null;
    rallyArrivalInFlight = null;
    playPulseCreateInFlight = null;
    playPulseAcceptInFlight.clear();
    revokePushSubscription(state.token);
    clearGameDraft(accountId);
    clearInstantRallyAttempt(accountId);
    clearRallyArrivalAttempt(accountId);
    clearRematchAttempts(accountId);
    resetPrivateUiForLogout(accountId);
    stopLocationWatch();
    state.token = null;
    state.me = null;
    localStorage.removeItem('pp_token');
    localStorage.removeItem('pp_me_snapshot_v1');
    clearInterval(state.mePollTimer);
    stopThreadPolling();
    state.mePollTimer = null;
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

  function invalidateMeRequests() {
    meRequestGeneration += 1;
    return meRequestGeneration;
  }

  function applyMe(data, {
    persist = true,
    provisional = false,
    reconcileSnapshot = false,
    fromRefresh = false,
  } = {}) {
    // Mutation/login/snapshot responses are authoritative and retire any
    // slower /me request that began against the state they replaced.
    if (!fromRefresh) invalidateMeRequests();
    const hadProvisionalArea = state.snapshotAreaProvisional;
    const previousArea = state.areaLoc ? [...state.areaLoc] : null;
    const previousPresenceView = JSON.stringify([
      !!state.presence?.checked_in,
      state.presence?.court_id || null,
      state.presence?.court_name || '',
    ]);
    const previousPlayPulseView = JSON.stringify([
      state.activePlayPulse?.id || null,
      state.activePlayPulse?.courtId || null,
      state.activePlayPulse?.expiresAt || '',
    ]);
    const previousAccountId = safePositiveId(state.me && state.me.id);
    const nextAccountId = safePositiveId(data.user && data.user.id);
    if (previousAccountId && previousAccountId !== nextAccountId) {
      rallyArrivalInFlight = null;
      playPulseCreateInFlight = null;
      playPulseAcceptInFlight.clear();
      clearRallyArrivalAttempt(previousAccountId);
    }
    state.me = data.user;
    // Catalog of muteable kinds rides alongside the user for the settings UI.
    if (data.muteable_notifications) state.me.muteable_notifications = data.muteable_notifications;
    state.presence = data.presence;
    state.unreadMessages = data.unread_messages || 0;
    if (data.community_room_unread != null) {
      const roomUnread = Number(data.community_room_unread) || 0;
      if (data.community_message_unread != null && data.community_group_unread != null) {
        const messageUnread = Number(data.community_message_unread) || 0;
        const groupUnread = Number(data.community_group_unread) || 0;
        if (messageUnread + groupUnread === roomUnread) {
          state.communityMessageUnread = messageUnread;
          state.communityGroupUnread = groupUnread;
        } else {
          state.communityMessageUnread = 0;
          state.communityGroupUnread = roomUnread;
        }
      } else {
        // Older servers only expose the aggregate. Keep the global badge exact
        // and route the unknown room total to Groups until a lane refresh splits it.
        state.communityMessageUnread = 0;
        state.communityGroupUnread = roomUnread;
      }
      state.communityRoomUnread = roomUnread;
    }
    state.pendingRequests = data.pending_friend_requests || 0;
    state.gamesToConfirm = data.games_to_confirm || 0;

    // Live updates: pop a toast when something new lands while the app is open.
    state.unreadNotifications = data.unread_notifications || 0;
    state.activeGame = data.active_game || null;
    state.activeArrival = normalizeActiveArrival(data.active_arrival);
    if (state.activeArrival && nextAccountId) {
      clearRallyArrivalAttempt(nextAccountId, state.activeArrival.gameId);
    }
    state.activePlayPulse = normalizeActivePlayPulse(data.active_play_pulse);
    if (state.activePlayPulse && nextAccountId) {
      clearPlayPulseCreateAttempt(nextAccountId, state.activePlayPulse.courtId);
    }
    state.activeTournament = data.active_tournament || null;
    const latest = data.latest_notification;
    if (latest) {
      if (state.lastNotifId !== null && latest.id > state.lastNotifId && !latest.read) {
        const coveredByBanner = latest.related_game_id
          && ((state.activeGame && state.activeGame.id === latest.related_game_id)
            || (state.activeArrival && state.activeArrival.gameId === latest.related_game_id));
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
    const nextPresenceView = JSON.stringify([
      !!state.presence?.checked_in,
      state.presence?.court_id || null,
      state.presence?.court_name || '',
    ]);
    const nextPlayPulseView = JSON.stringify([
      state.activePlayPulse?.id || null,
      state.activePlayPulse?.courtId || null,
      state.activePlayPulse?.expiresAt || '',
    ]);
    if (!areaChanged && (previousPresenceView !== nextPresenceView
        || previousPlayPulseView !== nextPlayPulseView)
        && state.tab === 'play' && state.playSeg === 'games'
        && !$('#main-screen').classList.contains('hidden')) {
      // Check-in/out and Free-this-hour state can change in another tab.
      // Reuse cached discovery data while immediately swapping the hero.
      renderPlay({ useCachedData: true });
    }
    if (reconcileSnapshot) {
      if (areaChanged) {
        state.playGamesCache = null;
        state.chatFriendsCache = null;
        clearLookingBanner();
        refreshLookingBanner();
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

  function instantRallyAssembly(game) {
    if (!game || !game.is_instant || game.status !== 'upcoming') return null;
    // The server owns whether an instant rally is still recruiting. A rally
    // can remain `upcoming` after play so its participants can enter a score;
    // never turn that durable score-pending row back into a live signal.
    const serverAssemblyState = String(game.assembly_state || '');
    if (game.assembly_active === false || (serverAssemblyState
        && !['finding', 'ready', 'full'].includes(serverAssemblyState))) return null;
    const visibleRosterCount = Array.isArray(game.players) ? game.players.length : 0;
    const aggregateReadyCount = Number(game.ready_count);
    const readyCount = Number.isFinite(aggregateReadyCount)
      ? Math.max(0, aggregateReadyCount) : visibleRosterCount;
    const aggregateRosterCount = Number(game.roster_count);
    const rosterCount = Number.isFinite(aggregateRosterCount)
      ? Math.max(0, aggregateRosterCount) : visibleRosterCount;
    const onWayCount = Math.max(0, Number(game.on_the_way_count) || 0);
    const aggregateCommittedCount = Number(game.committed_count);
    const committedCount = Number.isFinite(aggregateCommittedCount)
      ? Math.max(0, aggregateCommittedCount)
      : Math.max(readyCount, rosterCount) + onWayCount;
    const maxPlayers = Math.max(1, Number(game.max_players) || 4);
    const physicalSpotsLeft = Math.max(0, Number.isFinite(Number(game.physical_spots_left))
      ? Number(game.physical_spots_left) : maxPlayers - readyCount);
    const spotsLeft = Math.max(0, Number.isFinite(Number(game.spots_left))
      ? Number(game.spots_left) : maxPlayers - committedCount);
    const counts = rallyCountsText({
      readyCount, rosterCount, onWayCount, spotsLeft, maxPlayers,
    });
    if (readyCount <= 1) {
      return {
        icon: '⚡',
        title: 'Finding players',
        sub: counts,
        banner: `⚡ Finding players · ${counts}`,
        readyCount, rosterCount, onWayCount, committedCount, maxPlayers,
        physicalSpotsLeft, spotsLeft,
      };
    }
    if (spotsLeft > 0) {
      return {
        icon: '🏓',
        title: 'Ready to play',
        sub: counts,
        banner: `🏓 Ready to play · ${counts}`,
        readyCount, rosterCount, onWayCount, committedCount, maxPlayers,
        physicalSpotsLeft, spotsLeft,
      };
    }
    if (physicalSpotsLeft > 0 && onWayCount > 0) {
      return {
        icon: '🚗',
        title: 'Travel spot held',
        sub: counts,
        banner: `🚗 Travel spot held · ${counts}`,
        readyCount, rosterCount, onWayCount, committedCount, maxPlayers,
        physicalSpotsLeft, spotsLeft,
      };
    }
    return {
      icon: '🏓',
      title: 'Rally full — ready to play',
      sub: counts,
      banner: `🏓 Rally full · ${counts}`,
      readyCount, rosterCount, onWayCount, committedCount, maxPlayers,
      physicalSpotsLeft, spotsLeft,
    };
  }

  function instantRallyScorePending(game) {
    return !!(game && game.is_instant && game.status === 'upcoming'
      && game.can_enter_score && !instantRallyAssembly(game));
  }

  function instantRallyClosed(game) {
    if (!game || !game.is_instant || game.status !== 'upcoming' || game.can_enter_score) return false;
    const assemblyState = String(game.assembly_state || '');
    return game.assembly_active === false
      || (assemblyState && !['finding', 'ready', 'full'].includes(assemblyState));
  }

  function renderActiveGameBanner() {
    const el = $('#active-game-banner');
    // Courts is a location-first workspace. Keeping the global game banner
    // docked above its results sheet hides the very court decision a player is
    // trying to make, so active-game context stays on the other primary tabs.
    if (state.tab === 'courts') {
      el.className = 'active-game-banner hidden';
      $('#app').classList.remove('has-banner');
      return;
    }
    const trip = normalizeActiveArrival(state.activeArrival);
    if (state.activeArrival && !trip) state.activeArrival = null;
    if (trip) {
      el.className = 'active-game-banner state-arrival';
      el.innerHTML = `
        <button type="button" class="agb-open" aria-label="${esc(arrivalEtaLabel(trip))} to ${esc(trip.courtName)}. ${esc(rallyCountsText(arrivalRallySummary(trip), { includeOpen: false }))}. Spot held until ${esc(fmtTimeShort(trip.expiresAt))}. View details.">
          <span class="agb-arrival-icon" aria-hidden="true">🚗</span>
          <span class="agb-main">
            <span class="agb-title">Heading to ${esc(trip.courtName)}</span>
            <span class="agb-sub">${esc(arrivalEtaLabel(trip))} · ${esc(rallyCountsText(arrivalRallySummary(trip), { includeOpen: false }))}</span>
          </span>
        </button>
        <button type="button" class="agb-arrived" id="agb-arrived" aria-label="I’m at ${esc(trip.courtName)}">I’m at the court</button>`;
      el.querySelector('.agb-open').onclick = () => openArrivalDetails(trip);
      el.querySelector('#agb-arrived').onclick = (event) => {
        event.stopPropagation();
        openArrivalCheckInConfirmation(trip);
      };
      el.classList.remove('hidden');
      $('#app').classList.add('has-banner');
      return;
    }
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
    const assembly = instantRallyAssembly(game);
    const stateCfg = assembly && game.banner_state !== 'invited' ? {
      icon: assembly.icon,
      title: `${assembly.title} at ${esc(court.name || 'the court')}`,
      sub: assembly.sub,
    } : {
      challenge: {
        icon: '⚔️',
        title: `${esc((game.players[0] || {}).display_name || 'Someone')} challenged you!`,
        sub: `Ranked at ${esc(court.name || 'the court')} · tap to accept or decline`,
      },
      invited: {
        icon: '📨',
        title: `${esc((game.players.find((p) => p.user_id === game.creator_id) || {}).display_name || 'A friend')} invited you to play`,
        sub: game.is_instant
          ? `${esc(court.name || '')} · ${esc(rallyCountsText(rallySummaryFromValue(game)))}`
        : `${fmtDateTime(game.scheduled_at)} · ${esc(court.name || '')} · ${game.spots_left} spot${game.spots_left === 1 ? '' : 's'} left`,
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
    if (!stateCfg) {
      el.classList.add('hidden');
      $('#app').classList.remove('has-banner');
      return;
    }

    el.className = `active-game-banner state-${assembly && game.banner_state !== 'invited' ? 'rally' : game.banner_state}`;
    const inviteRally = game.banner_state === 'invited' && game.is_instant
      ? rallySummaryFromValue(game) : null;
    const inviteRallyAction = inviteRally ? rallyActionState(inviteRally) : null;
    const inviteCanAct = !inviteRally || game.is_joined || inviteRallyAction.enabled;
    const inviteActionLabel = inviteRallyAction ? inviteRallyAction.label : 'Join';
    el.innerHTML = `
      <button type="button" class="agb-open">
        ${stateCfg.icon.startsWith('<') ? stateCfg.icon : `<span style="font-size:17px">${stateCfg.icon}</span>`}
        <span class="agb-main">
          <span class="agb-title">${stateCfg.title}</span>
          <span class="agb-sub">${stateCfg.sub}</span>
        </span>
        ${game.banner_state === 'invited' ? '' : '<span class="agb-chev">›</span>'}
      </button>
      ${game.banner_state === 'invited' ? `${inviteCanAct ? `<button type="button" class="agb-join" id="agb-join" aria-label="${esc(inviteActionLabel)} at ${esc(court.name || 'this court')}">${esc(inviteActionLabel)}</button>` : '<span class="agb-unavailable">Travel spot held</span>'}<button type="button" class="agb-dismiss" id="agb-dismiss" aria-label="Decline game invite">✕</button>` : ''}`;
    const joinBtn = el.querySelector('#agb-join');
    if (joinBtn) {
      joinBtn.onclick = async (e) => {
        e.stopPropagation();
        if (game.is_instant) {
          await openReadyRally(inviteRally, joinBtn);
          return;
        }
        if (joinBtn.disabled) return;
        joinBtn.disabled = true;
        joinBtn.textContent = '…';
        try {
          await api(`/games/${game.id}/join`, { method: 'POST' });
          toast("You're in! 🏓");
          state.playGamesCache = null;
          await refreshMe();
          openGameScreen(game.id);
        } catch (err) {
          toast(err.message);
          joinBtn.disabled = false;
          joinBtn.textContent = inviteActionLabel;
          refreshMe().catch(() => {});
        }
      };
    }
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
      if (!assembly && game.banner_state === 'live' && game.players.length >= 2) {
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

  function syncCommunityUnreadLanes(rooms, clubs, competitions, crews) {
    const unreadTotal = (items) => (items || [])
      .reduce((total, item) => total + (Number(item.unread) || 0), 0);
    const competitionItems = competitions.items || [];
    state.communityMessageUnread = unreadTotal(
      competitionItems.filter((item) => item.kind === 'game'),
    );
    state.communityGroupUnread = unreadTotal(rooms.items)
      + unreadTotal(clubs.items)
      + unreadTotal(crews.items)
      + unreadTotal(competitionItems.filter((item) => (
        item.kind === 'tournament' || item.kind === 'league'
      )));
    state.communityRoomUnread = state.communityMessageUnread + state.communityGroupUnread;
  }

  function renderBadges() {
    const messagesTotal = state.unreadMessages + state.communityMessageUnread;
    const groupsTotal = state.communityGroupUnread;
    const total = state.unreadMessages + state.communityRoomUnread + state.pendingRequests;
    const badge = $('#chat-badge');
    badge.textContent = total > 99 ? '99+' : String(total);
    badge.classList.toggle('hidden', total === 0);

    const inboxBadge = $('#chat-inbox-badge');
    if (inboxBadge) {
      inboxBadge.textContent = messagesTotal > 99 ? '99+' : String(messagesTotal);
      inboxBadge.classList.toggle('hidden', messagesTotal === 0);
      $('#chat-tab-chats')?.setAttribute('aria-label', messagesTotal
        ? `Messages, ${messagesTotal} unread` : 'Messages');
    }
    const groupsBadge = $('#chat-groups-badge');
    if (groupsBadge) {
      groupsBadge.textContent = groupsTotal > 99 ? '99+' : String(groupsTotal);
      groupsBadge.classList.toggle('hidden', groupsTotal === 0);
      $('#chat-tab-nearby')?.setAttribute('aria-label', groupsTotal
        ? `Groups, ${groupsTotal} unread` : 'Groups');
    }
    const friendsBadge = $('#chat-friends-badge');
    if (friendsBadge) {
      friendsBadge.textContent = state.pendingRequests > 99 ? '99+' : String(state.pendingRequests);
      friendsBadge.classList.toggle('hidden', state.pendingRequests === 0);
      $('#chat-tab-friends')?.setAttribute('aria-label', state.pendingRequests
        ? `People, ${state.pendingRequests} pending request${state.pendingRequests === 1 ? '' : 's'}` : 'People');
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
    const generation = invalidateMeRequests();
    const requestToken = state.token;
    try {
      const data = await api('/me');
      if (generation !== meRequestGeneration || state.token !== requestToken) return false;
      applyMe(data, {
        reconcileSnapshot: state.snapshotAreaProvisional,
        fromRefresh: true,
      });
      return true;
    } catch { /* logged out */ }
    return false;
  }

  // ---------- Tabs ----------
  function setupTabs() {
    document.querySelectorAll('.nav-btn').forEach((btn) => {
      btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
    setupTablistKeyboard($('#play-segments'));
    setupTablistKeyboard($('#chat-segments'));
    $('#profile-settings')?.addEventListener('click', openSettingsHub);
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
      const pulseDetails = e.target.closest('[data-play-pulse-details]');
      if (pulseDetails) openPlayPulseDetails();
      const pulseCancel = e.target.closest('[data-play-pulse-cancel]');
      if (pulseCancel) cancelPlayPulse(state.activePlayPulse, pulseCancel);
    });
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-goto]');
      if (!btn) return;
      const target = btn.dataset.goto;
      dismissAllModals(() => {
        if (target === 'play-soon') {
          if (state.tab !== 'play') switchTab('play');
          openPlaySoonFlow();
        } else if (target === 'instant-rally') {
          if (state.tab !== 'play') switchTab('play');
          startInstantRally(btn);
        } else if (target === 'play-now') {
          if (state.tab !== 'play') switchTab('play');
          openPlayNowCourtPicker();
        } else if (target === 'play-pulse') {
          if (state.tab !== 'play') switchTab('play');
          openPlayPulseCourtPicker();
        } else if (target === 'new-ranked-game') {
          if (state.tab !== 'play') switchTab('play');
          openNewGameModal({ gameType: 'ranked' });
        } else if (target === 'new-game') {
          if (state.tab !== 'play') switchTab('play');
          openNewGameModal();
        } else if (target === 'courts-list') {
          switchTab('courts');
          setCourtSheetSnap('half');
        } else if (target === 'chat-friends') {
          state.chatSeg = 'friends';
          state.peopleMode = 'friends';
          document.querySelectorAll('#chat-segments button').forEach((b) => {
            const active = b.dataset.seg === 'friends';
            b.classList.toggle('active', active);
            b.setAttribute('aria-selected', String(active));
          });
          switchTab('chat');
        } else if (target === 'chat-nearby') {
          state.chatSeg = 'friends';
          state.peopleMode = 'nearby';
          document.querySelectorAll('#chat-segments button').forEach((b) => {
            const active = b.dataset.seg === 'friends';
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
    const existing = document.querySelector(`link[href="${src}"]`);
    if (existing && existing.dataset.loaded === '1') return Promise.resolve();
    existing?.remove();
    return new Promise((resolve, reject) => {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = src;
      link.crossOrigin = '';
      if (integrity) link.integrity = integrity;
      link.onload = () => { link.dataset.loaded = '1'; resolve(); };
      link.onerror = () => {
        link.remove();
        reject(new Error('Could not load the court map'));
      };
      document.head.appendChild(link);
    });
  }

  function loadScript(src, integrity) {
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing && existing.dataset.loaded === '1') return Promise.resolve();
    existing?.remove();
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = src;
      script.crossOrigin = '';
      if (integrity) script.integrity = integrity;
      script.onload = () => { script.dataset.loaded = '1'; resolve(); };
      script.onerror = () => {
        script.remove();
        reject(new Error('Could not load the court map'));
      };
      document.head.appendChild(script);
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
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(state.map);
    syncMapTileTheme();
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

    // Keep the rendered map decision-light. New shells provide one combined
    // `active` quick filter; older cached shells are upgraded in place while
    // their more specific controls remain available in the Filters sheet.
    const quickFilters = $('#map-filters');
    const legacyPlayers = quickFilters?.querySelector('[data-court-filter="players"]');
    if (legacyPlayers && !quickFilters.querySelector('[data-court-filter="active"]')) {
      legacyPlayers.dataset.courtFilter = 'active';
      legacyPlayers.textContent = '🟢 Active now';
    }
    quickFilters?.querySelector('[data-court-filter="saved"]')?.remove();
    quickFilters?.querySelector('[data-court-filter="games"]')?.remove();

    quickFilters?.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-court-filter]');
      if (!btn) return;
      const key = btn.dataset.courtFilter;
      if (!(key in state.courtFilters)) return;
      state.courtFilters[key] = !state.courtFilters[key];
      if (key === 'active' && state.courtFilters.active) {
        // The combined quick choice replaces, rather than stacks with, the
        // two advanced activity modes.
        state.courtFilters.players = false;
        state.courtFilters.games = false;
      }
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
    $('#court-more-filters')?.addEventListener('click', openCourtFilterSheet);

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
      clearLookingBanner();
      btn.classList.add('hidden');
      updatePlayHeader();
      refreshLookingBanner();
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
    $('#bell-btn')?.addEventListener('click', openActivity);
    $('#looking-banner').addEventListener('click', (event) => {
      const banner = event.currentTarget;
      const rally = rallySummaryFromDataset(banner);
      if (rally) {
        openReadyRally(rally, banner);
        return;
      }
      state.chatSeg = 'friends';
      state.peopleMode = 'nearby';
      document.querySelectorAll('#chat-segments button').forEach((b) => {
        const active = b.dataset.seg === 'friends';
        b.classList.toggle('active', active);
        b.setAttribute('aria-selected', String(active));
      });
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
    $('#court-sheet-expand')?.addEventListener('click', () => {
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
    // CARTO's formerly anonymous raster endpoints began returning tiles
    // watermarked "API KEY REQUIRED". OSM's standard endpoint needs no key
    // for normal interactive web use and keeps the provider easy to replace.
    return 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
  }
  function syncMapTileTheme() {
    state.map?.getContainer()?.classList.toggle('map-tiles-dark', themeIsDark());
  }
  function applyTheme() {
    const pref = themePref();
    if (pref === 'auto') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.dataset.theme = pref;
    const dark = themeIsDark();
    document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', dark ? 'dark' : 'light');
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#111614' : '#14532d');
    syncMapTileTheme();
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
        clearLookingBanner();
        refreshLookingBanner();
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

  function areaViewKey() {
    const area = areaLatLng();
    return `${Number(area.lat).toFixed(4)},${Number(area.lng).toFixed(4)}`;
  }

  function jumpToPlace(lat, lng, label) {
    state.areaLoc = [lat, lng];
    state.areaLabel = label || 'Selected area';
    state.snapshotAreaProvisional = false;
    state.playGamesCache = null;
    state.chatFriendsCache = null;
    clearLookingBanner();
    refreshLookingBanner();
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
  const COURT_DETAIL_FILTERS = ['saved', 'players', 'games', ...COURT_AMENITY_FILTERS];

  function activeCourtFilterCount() {
    return Object.values(state.courtFilters).filter(Boolean).length;
  }

  function syncCourtFilterControls() {
    document.querySelectorAll('[data-court-filter]').forEach((btn) => {
      const active = !!state.courtFilters[btn.dataset.courtFilter];
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', String(active));
    });
    const detailCount = COURT_DETAIL_FILTERS.filter((key) => state.courtFilters[key]).length;
    const more = $('#court-more-filters');
    const badge = $('#court-filter-count');
    if (more) more.classList.toggle('active', detailCount > 0);
    if (badge) {
      badge.textContent = String(detailCount);
      badge.classList.toggle('hidden', detailCount === 0);
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
      if (state.courtFilters.active
          && !(court.players_here > 0 || court.upcoming_games > 0)) return false;
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
    const activityOptions = [
      ['saved', '⭐', 'Saved courts'],
      ['players', '🟢', 'Players here'],
      ['games', '🏓', 'Open games'],
    ];
    const amenityOptions = [
      ['indoor', '🏠', 'Indoor'],
      ['lighted', '💡', 'Lighted'],
      ['nets', '🥅', 'Nets provided'],
      ['restrooms', '🚻', 'Restrooms'],
      ['water', '🚰', 'Drinking water'],
    ];
    const modal = openModal(`
      ${modalHead('Filter courts')}
      <p class="row-sub" style="margin:-4px 0 14px">Choose only what matters for this search.</p>
      <div class="section-label" style="margin-top:0">Activity</div>
      <div class="court-filter-grid">
        ${activityOptions.map(([key, icon, label]) => `
          <button type="button" class="court-filter-option ${draft[key] ? 'active' : ''}" data-filter-option="${key}" aria-pressed="${draft[key]}">
            <span style="font-size:18px;margin-right:5px">${icon}</span>${label}
          </button>`).join('')}
      </div>
      <div class="section-label" style="margin-top:0">Amenities</div>
      <div class="court-filter-grid">
        ${amenityOptions.map(([key, icon, label]) => `
          <button type="button" class="court-filter-option ${draft[key] ? 'active' : ''}" data-filter-option="${key}" aria-pressed="${draft[key]}">
            <span style="font-size:18px;margin-right:5px">${icon}</span>${label}
          </button>`).join('')}
      </div>
      <div class="court-filter-actions">
        <button type="button" class="btn btn-secondary" id="court-filter-clear">Clear all</button>
        <button type="button" class="btn btn-primary" id="court-filter-apply">Show matches</button>
      </div>
    `, { label: 'Court filters' });
    $('#court-more-filters')?.setAttribute('aria-expanded', 'true');
    modal._cleanupFns.push(() => $('#court-more-filters')?.setAttribute('aria-expanded', 'false'));
    const syncDraft = () => {
      modal.querySelectorAll('[data-filter-option]').forEach((btn) => {
        const active = !!draft[btn.dataset.filterOption];
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', String(active));
      });
    };
    modal.querySelectorAll('.court-filter-grid').forEach((grid) => {
      grid.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-filter-option]');
        if (!btn) return;
        draft[btn.dataset.filterOption] = !draft[btn.dataset.filterOption];
        if (['players', 'games'].includes(btn.dataset.filterOption)
            && draft[btn.dataset.filterOption]) draft.active = false;
        syncDraft();
      });
    });
    modal.querySelector('#court-filter-clear').addEventListener('click', () => {
      Object.keys(draft).forEach((key) => { draft[key] = false; });
      syncDraft();
    });
    modal.querySelector('#court-filter-apply').addEventListener('click', async () => {
      state.courtFilters = draft;
      syncCourtFilterControls();
      closeModal(modal);
      await refreshCourtResults();
      if (state.courtFilters.saved && state.courtsInView.length) fitSearchResults();
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

  function safePositiveId(value) {
    const id = Number(value);
    return Number.isSafeInteger(id) && id > 0 ? id : null;
  }

  function playPulseFromValue(value) {
    if (!value || typeof value !== 'object') return null;
    const id = safePositiveId(value.id ?? value.pulse_id ?? value.pulseId);
    const court = value.court && typeof value.court === 'object' ? value.court : {};
    const courtId = safePositiveId(value.court_id ?? value.courtId ?? court.id);
    const expiresAt = String(value.expires_at ?? value.expiresAt ?? '');
    const expires = new Date(expiresAt).getTime();
    if (!id || !courtId || !Number.isFinite(expires)) return null;
    const numberOrNull = (raw) => raw != null && raw !== '' && Number.isFinite(Number(raw))
      ? Number(raw) : null;
    const user = value.user && typeof value.user === 'object' ? value.user : null;
    return {
      id,
      courtId,
      courtName: String(value.court_name ?? value.courtName ?? court.name ?? 'this court'),
      courtCity: String(value.court_city ?? value.courtCity ?? court.city ?? ''),
      courtAddress: String(value.court_address ?? value.courtAddress ?? court.address ?? ''),
      courtLatitude: numberOrNull(value.court_latitude ?? value.courtLatitude
        ?? court.latitude ?? court.lat),
      courtLongitude: numberOrNull(value.court_longitude ?? value.courtLongitude
        ?? court.longitude ?? court.lng ?? court.lon),
      distanceMiles: numberOrNull(value.distance_miles ?? value.distanceMiles),
      declaredAt: String(value.declared_at ?? value.declaredAt ?? ''),
      expiresAt,
      active: value.active !== false,
      acceptCapability: String(value.accept_capability ?? value.acceptCapability ?? ''),
      user,
      court: {
        ...court,
        id: courtId,
        name: String(value.court_name ?? value.courtName ?? court.name ?? 'this court'),
        city: String(value.court_city ?? value.courtCity ?? court.city ?? ''),
        address: String(value.court_address ?? value.courtAddress ?? court.address ?? ''),
        latitude: numberOrNull(value.court_latitude ?? value.courtLatitude
          ?? court.latitude ?? court.lat),
        longitude: numberOrNull(value.court_longitude ?? value.courtLongitude
          ?? court.longitude ?? court.lng ?? court.lon),
      },
    };
  }

  function normalizeActivePlayPulse(value) {
    const pulse = playPulseFromValue(value);
    if (!pulse || !pulse.active || new Date(pulse.expiresAt).getTime() <= Date.now()) return null;
    return pulse;
  }

  function normalizeLookingPulses(data) {
    const values = Array.isArray(data?.pulses) ? data.pulses : [];
    const seen = new Set();
    return values.map(playPulseFromValue).filter((pulse) => {
      if (!pulse || !pulse.active || !pulse.acceptCapability
          || new Date(pulse.expiresAt).getTime() <= Date.now() || seen.has(pulse.id)) return false;
      seen.add(pulse.id);
      return !state.me || safePositiveId(pulse.user && pulse.user.id) !== safePositiveId(state.me.id);
    }).sort((a, b) => (a.distanceMiles ?? Infinity) - (b.distanceMiles ?? Infinity)
      || new Date(a.expiresAt) - new Date(b.expiresAt));
  }

  function rallySummaryFromValue(value, player = null) {
    if (!value || typeof value !== 'object') return null;
    const game = value.game && typeof value.game === 'object' ? value.game : {};
    const court = value.court && typeof value.court === 'object' ? value.court
      : (game.court && typeof game.court === 'object' ? game.court
        : (player && player.checked_in_court) || {});
    const gameId = safePositiveId(
      value.game_id ?? value.gameId ?? value.rally_game_id ?? value.rally_id ?? game.id
        ?? (value.kind === 'rally' || value.is_instant ? value.id : null),
    );
    const courtId = safePositiveId(value.court_id ?? value.courtId ?? court.id
      ?? game.court_id ?? (game.court || {}).id);
    if (!courtId) return null;
    const gamePlayers = Array.isArray(game.players) ? game.players : [];
    const valuePlayers = Array.isArray(value.players) ? value.players : [];
    const readyCount = Math.max(0, Number(
      value.ready_count ?? value.readyCount ?? value.player_count ?? value.players_count
        ?? (valuePlayers.length || null) ?? (gamePlayers.length || null) ?? 0,
    ) || 0);
    const rosterCount = Math.max(0, Number(
      value.roster_count ?? value.rosterCount ?? game.roster_count
        ?? (valuePlayers.length || null) ?? (gamePlayers.length || null) ?? readyCount,
    ) || 0);
    const maxPlayers = Math.max(1, Number(
      value.max_players ?? value.maxPlayers ?? game.max_players,
    ) || 4);
    const onWayCount = Math.max(0, Number(
      value.on_the_way_count ?? value.onWayCount ?? game.on_the_way_count ?? 0,
    ) || 0);
    const committedCount = Math.max(0, Number(
      value.committed_count ?? value.committedCount ?? game.committed_count
        ?? Math.max(readyCount, rosterCount) + onWayCount,
    ) || 0);
    const physicalSpotsLeft = Math.max(0, Number(
      value.physical_spots_left ?? value.physicalSpotsLeft ?? game.physical_spots_left
        ?? Math.max(0, maxPlayers - readyCount),
    ) || 0);
    const spotsLeft = Math.max(0, Number(
      value.spots_left ?? value.spotsLeft ?? game.spots_left
        ?? Math.max(0, maxPlayers - committedCount),
    ) || 0);
    const arrivalCapability = String(
      value.arrival_capability ?? value.arrivalCapability ?? value.discovery_token
        ?? game.arrival_capability ?? game.discovery_token ?? '',
    );
    const rawArrivalAvailable = value.arrival_available ?? value.arrivalAvailable
      ?? game.arrival_available ?? game.arrivalAvailable;
    const arrivalAvailable = !!arrivalCapability && (rawArrivalAvailable == null
      ? true
      : rawArrivalAvailable === true || rawArrivalAvailable === 1
        || rawArrivalAvailable === 'true' || rawArrivalAvailable === '1');
    const numberOrNull = (raw) => raw != null && raw !== '' && Number.isFinite(Number(raw))
      ? Number(raw) : null;
    return {
      gameId,
      courtId,
      courtName: String(value.court_name ?? value.courtName ?? court.name ?? (game.court || {}).name ?? 'this court'),
      courtCity: String(value.court_city ?? value.courtCity ?? court.city ?? (game.court || {}).city ?? ''),
      courtAddress: String(value.court_address ?? value.courtAddress ?? court.address ?? (game.court || {}).address ?? ''),
      courtLatitude: numberOrNull(value.court_latitude ?? value.courtLatitude
        ?? value.latitude ?? value.lat ?? court.latitude ?? court.lat
        ?? (game.court || {}).latitude ?? (game.court || {}).lat),
      courtLongitude: numberOrNull(value.court_longitude ?? value.courtLongitude
        ?? value.longitude ?? value.lng ?? value.lon ?? court.longitude ?? court.lng ?? court.lon
        ?? (game.court || {}).longitude ?? (game.court || {}).lng ?? (game.court || {}).lon),
      readyCount,
      rosterCount,
      onWayCount,
      committedCount,
      physicalSpotsLeft,
      spotsLeft,
      maxPlayers,
      distanceMiles: Number.isFinite(Number(value.distance_miles ?? value.distanceMiles))
        ? Number(value.distance_miles ?? value.distanceMiles) : null,
      arrivalCapability,
      arrivalAvailable,
      myArrival: value.my_arrival ?? value.myArrival ?? game.my_arrival ?? null,
    };
  }

  function arrivalSummaryFromValue(value, rally = null) {
    if (!value || typeof value !== 'object') return null;
    const game = value.game && typeof value.game === 'object' ? value.game : {};
    const base = rally && rally.courtId ? rally : rallySummaryFromValue({
      ...game,
      game_id: value.game_id ?? value.gameId ?? game.id,
      court: value.court ?? game.court,
      court_id: value.court_id ?? value.courtId ?? game.court_id,
      court_name: value.court_name ?? value.courtName,
      court_city: value.court_city ?? value.courtCity,
      court_address: value.court_address ?? value.courtAddress,
      court_latitude: value.court_latitude ?? value.courtLatitude,
      court_longitude: value.court_longitude ?? value.courtLongitude,
      ready_count: value.ready_count ?? value.readyCount,
      roster_count: value.roster_count ?? value.rosterCount,
      on_the_way_count: value.on_the_way_count ?? value.onWayCount,
      committed_count: value.committed_count ?? value.committedCount,
      physical_spots_left: value.physical_spots_left ?? value.physicalSpotsLeft,
      spots_left: value.spots_left ?? value.spotsLeft,
      max_players: value.max_players ?? value.maxPlayers,
      arrival_capability: value.arrival_capability ?? value.arrivalCapability
        ?? value.discovery_token,
    });
    const gameId = safePositiveId(value.game_id ?? value.gameId
      ?? value.rally_game_id ?? game.id ?? base?.gameId);
    const courtId = safePositiveId(value.court_id ?? value.courtId ?? (value.court || {}).id
      ?? game.court_id ?? (game.court || {}).id ?? base?.courtId);
    if (!gameId || !courtId) return null;
    const etaMinutes = Number(value.eta_minutes ?? value.etaMinutes);
    const expiresAt = String(value.expires_at ?? value.expiresAt ?? '');
    const latitudeValue = value.court_latitude ?? value.courtLatitude
      ?? (value.court || {}).latitude ?? (value.court || {}).lat
      ?? (game.court || {}).latitude ?? (game.court || {}).lat ?? base?.courtLatitude;
    const longitudeValue = value.court_longitude ?? value.courtLongitude
      ?? (value.court || {}).longitude ?? (value.court || {}).lng ?? (value.court || {}).lon
      ?? (game.court || {}).longitude ?? (game.court || {}).lng ?? (game.court || {}).lon
      ?? base?.courtLongitude;
    const arrivalCapability = String(value.arrival_capability ?? value.arrivalCapability
      ?? value.discovery_token ?? base?.arrivalCapability ?? '');
    const rawArrivalAvailable = value.arrival_available ?? value.arrivalAvailable
      ?? game.arrival_available ?? game.arrivalAvailable ?? base?.arrivalAvailable;
    const arrivalAvailable = !!arrivalCapability && (rawArrivalAvailable == null
      ? true
      : rawArrivalAvailable === true || rawArrivalAvailable === 1
        || rawArrivalAvailable === 'true' || rawArrivalAvailable === '1');
    return {
      id: safePositiveId(value.id),
      gameId,
      courtId,
      courtName: String(value.court_name ?? value.courtName ?? (value.court || {}).name
        ?? (game.court || {}).name ?? base?.courtName ?? 'this court'),
      courtCity: String(value.court_city ?? value.courtCity ?? (value.court || {}).city
        ?? (game.court || {}).city ?? base?.courtCity ?? ''),
      courtAddress: String(value.court_address ?? value.courtAddress ?? (value.court || {}).address
        ?? (game.court || {}).address ?? base?.courtAddress ?? ''),
      courtLatitude: latitudeValue != null && latitudeValue !== ''
        && Number.isFinite(Number(latitudeValue)) ? Number(latitudeValue) : null,
      courtLongitude: longitudeValue != null && longitudeValue !== ''
        && Number.isFinite(Number(longitudeValue)) ? Number(longitudeValue) : null,
      etaMinutes: [5, 10, 15].includes(etaMinutes) ? etaMinutes : null,
      declaredAt: String(value.declared_at ?? value.declaredAt ?? ''),
      arrivesAt: String(value.arrives_at ?? value.arrivesAt ?? ''),
      expiresAt,
      active: value.active !== false,
      endReason: value.end_reason ?? value.endReason ?? null,
      readyCount: Math.max(0, Number(value.ready_count ?? value.readyCount
        ?? base?.readyCount ?? 0) || 0),
      rosterCount: Math.max(0, Number(value.roster_count ?? value.rosterCount
        ?? base?.rosterCount ?? value.ready_count ?? value.readyCount ?? 0) || 0),
      onWayCount: Math.max(0, Number(value.on_the_way_count ?? value.onWayCount
        ?? base?.onWayCount ?? 1) || 0),
      committedCount: Math.max(0, Number(value.committed_count ?? value.committedCount
        ?? base?.committedCount ?? 0) || 0),
      physicalSpotsLeft: Math.max(0, Number(
        value.physical_spots_left ?? value.physicalSpotsLeft ?? base?.physicalSpotsLeft ?? 0,
      ) || 0),
      spotsLeft: Math.max(0, Number(value.spots_left ?? value.spotsLeft
        ?? base?.spotsLeft ?? 0) || 0),
      maxPlayers: Math.max(1, Number(value.max_players ?? value.maxPlayers
        ?? base?.maxPlayers ?? 4) || 4),
      arrivalCapability,
      arrivalAvailable,
      user: value.user && typeof value.user === 'object' ? value.user : value,
    };
  }

  function normalizeActiveArrival(value, rally = null) {
    const arrival = arrivalSummaryFromValue(value, rally);
    if (!arrival || !arrival.active) return null;
    const expires = new Date(arrival.expiresAt).getTime();
    if (!Number.isFinite(expires) || expires <= Date.now()) return null;
    return arrival;
  }

  function activeArrivalForGame(gameId, ownValue = null, rally = null) {
    const own = normalizeActiveArrival(ownValue, rally);
    if (own && own.gameId === Number(gameId)) return own;
    return state.activeArrival && state.activeArrival.gameId === Number(gameId)
      ? state.activeArrival : null;
  }

  function rallyCountsText(rally, { includeOpen = true } = {}) {
    const ready = Math.max(0, Number(rally?.readyCount) || 0);
    // Keep the server's configured ceiling visible even if a bad/stale payload
    // reports more commitments than capacity (for example, 5/4). Open counts
    // are clamped separately; rewriting the denominator would hide the fault.
    const max = Math.max(1, Number(rally?.maxPlayers) || 4);
    const onWay = Math.max(0, Number(rally?.onWayCount) || 0);
    const parts = [`${ready}/${max} at the court`, `${onWay} arriving`];
    if (includeOpen) {
      const spots = Math.max(0, Number(rally?.spotsLeft) || 0);
      parts.push(`${spots} spot${spots === 1 ? '' : 's'} left`);
    }
    return parts.join(' · ');
  }

  function arrivalEtaLabel(arrival) {
    const arrives = new Date(arrival && arrival.arrivesAt).getTime();
    if (!Number.isFinite(arrives)) return `${arrival?.etaMinutes || 10} min ETA`;
    const minutes = Math.max(0, Math.ceil((arrives - Date.now()) / 60000));
    return minutes ? `${minutes} min away` : 'Arriving now';
  }

  function arrivalReservationCopy(arrival) {
    return `We’ll hold one spot until ${fmtTimeShort(arrival.expiresAt)}. Check in when you arrive.`;
  }

  function rallyCourtForDirections(rally) {
    return {
      id: rally.courtId,
      name: rally.courtName,
      city: rally.courtCity,
      address: rally.courtAddress,
      latitude: rally.courtLatitude,
      longitude: rally.courtLongitude,
    };
  }

  function rallyDatasetAttributes(rally) {
    if (!rally) return '';
    const attrs = [
      ['data-rally-game-id', rally.gameId || ''],
      ['data-rally-court-id', rally.courtId || ''],
      ['data-rally-court-name', rally.courtName || 'this court'],
      ['data-rally-court-city', rally.courtCity || ''],
      ['data-rally-court-address', rally.courtAddress || ''],
      ['data-rally-court-latitude', rally.courtLatitude ?? ''],
      ['data-rally-court-longitude', rally.courtLongitude ?? ''],
      ['data-rally-ready-count', rally.readyCount || 0],
      ['data-rally-roster-count', rally.rosterCount || 0],
      ['data-rally-on-way-count', rally.onWayCount || 0],
      ['data-rally-committed-count', rally.committedCount || 0],
      ['data-rally-physical-spots-left', rally.physicalSpotsLeft || 0],
      ['data-rally-spots-left', rally.spotsLeft || 0],
      ['data-rally-max-players', rally.maxPlayers || 4],
      ['data-rally-arrival-capability', rally.arrivalCapability || ''],
      ['data-rally-arrival-available', String(!!rally.arrivalAvailable)],
    ];
    return attrs.map(([name, value]) => `${name}="${esc(value)}"`).join(' ');
  }

  function rallySummaryFromDataset(element) {
    const dataset = element && element.dataset;
    if (!dataset) return null;
    return rallySummaryFromValue({
      game_id: dataset.rallyGameId,
      court_id: dataset.rallyCourtId,
      court_name: dataset.rallyCourtName,
      court_city: dataset.rallyCourtCity,
      court_address: dataset.rallyCourtAddress,
      court_latitude: dataset.rallyCourtLatitude,
      court_longitude: dataset.rallyCourtLongitude,
      ready_count: dataset.rallyReadyCount,
      roster_count: dataset.rallyRosterCount,
      on_the_way_count: dataset.rallyOnWayCount,
      committed_count: dataset.rallyCommittedCount,
      physical_spots_left: dataset.rallyPhysicalSpotsLeft,
      spots_left: dataset.rallySpotsLeft,
      max_players: dataset.rallyMaxPlayers,
      arrival_capability: dataset.rallyArrivalCapability,
      arrival_available: dataset.rallyArrivalAvailable,
    });
  }

  function rallyActionState(rally) {
    if (!rally) return { enabled: false, label: 'Game unavailable', kind: 'committed' };
    const ownArrival = activeArrivalForGame(rally.gameId, rally.myArrival, rally);
    if (isCheckedInAtCourt(rally.courtId)) {
      return (rally.spotsLeft > 0 || ownArrival)
        ? { enabled: true, label: 'Join this game', kind: 'join' }
        : { enabled: true, label: 'Find another game', kind: 'next' };
    }
    if (ownArrival) return { enabled: true, label: 'View held spot', kind: 'held' };
    if (rally.onWayCount > 0) {
      return { enabled: false, label: 'Travel spot held', kind: 'committed' };
    }
    if (rally.spotsLeft <= 0) {
      return { enabled: false, label: 'Game full', kind: 'committed' };
    }
    if (!rally.arrivalAvailable) {
      return { enabled: false, label: 'Game wrapping up', kind: 'wrapping' };
    }
    return { enabled: true, label: 'Arrive in 5–15 min', kind: 'arrival' };
  }

  function playerRallySummary(player) {
    if (!player || typeof player !== 'object') return null;
    const nested = player.rally || player.open_rally || player.instant_rally;
    const fromNested = rallySummaryFromValue(nested, player);
    if (fromNested) return fromNested;
    const court = player.checked_in_court || {};
    return rallySummaryFromValue({
      rally_game_id: player.rally_game_id ?? player.game_id ?? court.rally_game_id ?? court.game_id,
      court_id: player.court_id ?? court.id,
      court_name: player.court_name ?? court.name,
      ready_count: player.ready_count ?? court.ready_count,
      roster_count: player.roster_count ?? court.roster_count,
      on_the_way_count: player.on_the_way_count ?? court.on_the_way_count,
      committed_count: player.committed_count ?? court.committed_count,
      physical_spots_left: player.physical_spots_left ?? court.physical_spots_left,
      spots_left: player.spots_left ?? court.spots_left,
      max_players: player.max_players ?? court.max_players,
      distance_miles: player.distance_miles,
      arrival_capability: player.arrival_capability ?? player.discovery_token
        ?? court.arrival_capability ?? court.discovery_token,
      arrival_available: player.arrival_available ?? player.arrivalAvailable
        ?? court.arrival_available ?? court.arrivalAvailable,
      my_arrival: player.my_arrival ?? court.my_arrival,
    }, player);
  }

  function normalizeLookingRallies(data) {
    if (!data || typeof data !== 'object') return [];
    const raw = [
      ...(Array.isArray(data.rallies) ? data.rallies : []),
      ...(Array.isArray(data.open_rallies) ? data.open_rallies : []),
      ...(data.rally && typeof data.rally === 'object' ? [data.rally] : []),
    ];
    (Array.isArray(data.players) ? data.players : []).forEach((player) => {
      const summary = playerRallySummary(player);
      if (summary) raw.push(summary);
    });
    const seen = new Set();
    return raw.map((value) => value && value.courtId ? value : rallySummaryFromValue(value))
      .filter(Boolean)
      .filter((rally) => {
        const key = rally.gameId ? `game:${rally.gameId}` : `court:${rally.courtId}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort((a, b) => (a.distanceMiles ?? Infinity) - (b.distanceMiles ?? Infinity)
        || b.readyCount - a.readyCount);
  }

  // "N players near you want to play" — prefer an exact rally/court action,
  // while accepting the legacy people-only response during rollout.
  let lookingBannerGeneration = 0;
  let lookingBannerCommittedContext = '';

  function lookingBannerContext(token, area) {
    const coordinate = (value) => Number(value).toFixed(5);
    return `${token || ''}:${coordinate(area.lat)}:${coordinate(area.lng)}`;
  }

  function clearLookingBanner({ invalidate = true } = {}) {
    if (invalidate) lookingBannerGeneration += 1;
    lookingBannerCommittedContext = '';
    const el = $('#looking-banner');
    if (!el) return;
    delete el.dataset.rallyGameId;
    delete el.dataset.rallyCourtId;
    delete el.dataset.rallyCourtName;
    delete el.dataset.rallyCourtCity;
    delete el.dataset.rallyCourtAddress;
    delete el.dataset.rallyCourtLatitude;
    delete el.dataset.rallyCourtLongitude;
    delete el.dataset.rallyReadyCount;
    delete el.dataset.rallyRosterCount;
    delete el.dataset.rallyOnWayCount;
    delete el.dataset.rallyCommittedCount;
    delete el.dataset.rallyPhysicalSpotsLeft;
    delete el.dataset.rallySpotsLeft;
    delete el.dataset.rallyMaxPlayers;
    delete el.dataset.rallyArrivalCapability;
    delete el.dataset.rallyArrivalAvailable;
    el.replaceChildren();
    el.removeAttribute('aria-label');
    el.classList.add('hidden');
    el.classList.remove('below');
  }

  async function refreshLookingBanner() {
    const generation = ++lookingBannerGeneration;
    const el = $('#looking-banner');
    if (!el || !state.token) return;
    const c = areaLatLng();
    const token = state.token;
    const context = lookingBannerContext(token, c);
    if (lookingBannerCommittedContext && lookingBannerCommittedContext !== context) {
      clearLookingBanner({ invalidate: false });
    }
    const isCurrent = () => generation === lookingBannerGeneration
      && state.token === token
      && lookingBannerContext(state.token, areaLatLng()) === context;
    try {
      const data = await api(`/players/looking?lat=${c.lat}&lng=${c.lng}&radius=25`);
      if (!isCurrent()) return;
      const rallies = normalizeLookingRallies(data);
      const rally = rallies[0] || null;
      const pulses = normalizeLookingPulses(data);
      const pulse = pulses[0] || null;
      const players = Array.isArray(data.players) ? data.players : [];
      const count = Math.max(0, Number(data.count) || 0);
      lookingBannerCommittedContext = context;
      if (!rally && !count && !pulse) { clearLookingBanner({ invalidate: false }); return; }
      delete el.dataset.rallyGameId;
      delete el.dataset.rallyCourtId;
      delete el.dataset.rallyCourtName;
      delete el.dataset.rallyCourtCity;
      delete el.dataset.rallyCourtAddress;
      delete el.dataset.rallyCourtLatitude;
      delete el.dataset.rallyCourtLongitude;
      delete el.dataset.rallyReadyCount;
      delete el.dataset.rallyRosterCount;
      delete el.dataset.rallyOnWayCount;
      delete el.dataset.rallyCommittedCount;
      delete el.dataset.rallyPhysicalSpotsLeft;
      delete el.dataset.rallySpotsLeft;
      delete el.dataset.rallyMaxPlayers;
      delete el.dataset.rallyArrivalCapability;
      delete el.dataset.rallyArrivalAvailable;
      if (rally) {
        if (rally.gameId) el.dataset.rallyGameId = String(rally.gameId);
        el.dataset.rallyCourtId = String(rally.courtId);
        el.dataset.rallyCourtName = rally.courtName;
        el.dataset.rallyCourtCity = rally.courtCity;
        el.dataset.rallyCourtAddress = rally.courtAddress;
        if (rally.courtLatitude != null) el.dataset.rallyCourtLatitude = String(rally.courtLatitude);
        if (rally.courtLongitude != null) el.dataset.rallyCourtLongitude = String(rally.courtLongitude);
        el.dataset.rallyReadyCount = String(rally.readyCount);
        el.dataset.rallyRosterCount = String(rally.rosterCount);
        el.dataset.rallyOnWayCount = String(rally.onWayCount);
        el.dataset.rallyCommittedCount = String(rally.committedCount);
        el.dataset.rallyPhysicalSpotsLeft = String(rally.physicalSpotsLeft);
        el.dataset.rallySpotsLeft = String(rally.spotsLeft);
        el.dataset.rallyMaxPlayers = String(rally.maxPlayers);
        el.dataset.rallyArrivalCapability = rally.arrivalCapability;
        el.dataset.rallyArrivalAvailable = String(rally.arrivalAvailable);
        const fill = rallyCountsText(rally);
        const checkedIn = isCheckedInAtCourt(rally.courtId);
        const ownArrival = activeArrivalForGame(rally.gameId, rally.myArrival, rally);
        const action = checkedIn
          ? (rally.spotsLeft > 0 || ownArrival ? 'Join this game.' : 'Find another game at this court.')
          : ownArrival ? 'View your held spot.'
            : !rally.arrivalAvailable ? 'Travel spots are closed because this rally is wrapping up.'
              : rally.spotsLeft > 0 && rally.onWayCount === 0 ? 'Choose a 5–15 minute arrival.'
                : 'The travel spot is already held.';
        el.innerHTML = `<svg class="pb-ic"><use href="#pb"/></svg> <span><b>${esc(rally.courtName)}</b> · ${fill}</span><span class="chev">›</span>`;
        el.setAttribute('aria-label', `${fill} at ${rally.courtName}. ${action}`);
      } else if (count) {
        const firstName = players[0] && String(players[0].display_name || '').split(' ')[0];
        const who = count === 1 && firstName
          ? `${esc(firstName)} wants` : `${count} player${count === 1 ? '' : 's'} near you want`;
        el.innerHTML = `<svg class="pb-ic"><use href="#pb"/></svg> ${who} to play soon <span class="chev">›</span>`;
        el.setAttribute('aria-label', `${count} nearby player${count === 1 ? '' : 's'} want to play soon. View nearby players.`);
      } else {
        const firstName = String(pulse.user?.display_name || 'A nearby player').split(/\s+/)[0];
        el.innerHTML = `<svg class="pb-ic"><use href="#pb"/></svg> <span><b>${esc(firstName)}</b> can play at ${esc(pulse.courtName)} this hour</span><span class="chev">›</span>`;
        el.setAttribute('aria-label', `${firstName} can play at ${pulse.courtName} this hour. View and confirm the intended destination.`);
      }
      el.classList.remove('hidden');
      el.classList.toggle('below', !$('#presence-banner').classList.contains('hidden'));
    } catch {
      if (!isCurrent()) return;
      clearLookingBanner({ invalidate: false });
    }
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
    const sort = $('#court-sort');
    const sortLabel = document.querySelector('label[for="court-sort"]');
    const n = courts.length;
    if (count) count.textContent = n ? String(n) : '0';
    sort?.classList.toggle('hidden', n === 0);
    sortLabel?.classList.toggle('hidden', n === 0);
    if (title) title.textContent = savedOnly ? 'Saved courts'
      : searching ? 'Search results' : n ? `${n} court${n === 1 ? '' : 's'} nearby` : 'No matching courts';
    if (context) {
      const active = [];
      if (state.courtFilters.active) active.push('active now');
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

  function splitExactCourtNameMatches(courts, q) {
    const queryName = String(q || '').trim().toLocaleLowerCase();
    const exact = [];
    const other = [];
    (courts || []).forEach((court) => {
      const courtName = String(court.name || '').trim().toLocaleLowerCase();
      // A typed court-name prefix is a stronger signal than a geocoder place
      // with a similar name (for example, “Los Cab” versus Los Angeles).
      const bucket = queryName && (courtName === queryName || courtName.startsWith(queryName))
        ? exact : other;
      bucket.push(court);
    });
    return { exact, other };
  }

  function renderSearchSuggest(courts, places, q) {
    const el = $('#search-suggest');
    if (!el) return;
    // Only surface suggestions while this query is still what's typed.
    if (!q || state.searchQ !== q) { hideSearchSuggest(); return; }
    let html = '';
    const { exact, other } = splitExactCourtNameMatches(courts, q);
    const courtRowsHtml = (rows) => rows.map((c) => `
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
    if (exact.length) {
      html += '<div class="sug-label">🏓 Exact court</div>';
      html += courtRowsHtml(exact.slice(0, 2));
    }
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
    const remainingSlots = Math.max(0, 5 - Math.min(2, exact.length));
    const visibleOther = other.slice(0, remainingSlots);
    if (visibleOther.length) {
      html += '<div class="sug-label">🏓 Courts</div>';
      html += courtRowsHtml(visibleOther);
    }
    const shownCourtCount = Math.min(2, exact.length) + visibleOther.length;
    if (courts.length > shownCourtCount) {
      html += `<button class="sug-row sug-all" role="option" aria-selected="false" data-sug-all>See all ${courts.length} courts</button>`;
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
      // Clusters keep their numeric venue count; a single location uses a
      // paddle, so the same green number never means two different things.
      html: `<div class="court-marker ${busy ? 'busy' : ''} ${fav ? 'fav' : ''} ${selected ? 'selected' : ''}" role="img" aria-label="${esc(markerLabel)}" style="width:${size}px;height:${size}px">${busy ? court.players_here + '👤' : '🏓'}${gameBadge}${favBadge}${condBadge}</div>`,
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
    const address = court.address ? [court.address, court.city].filter(Boolean).join(' ') : '';
    const latitude = court.latitude ?? court.lat;
    const longitude = court.longitude ?? court.lng ?? court.lon;
    const hasCoordinates = latitude != null && longitude != null
      && latitude !== '' && longitude !== ''
      && Number.isFinite(Number(latitude)) && Number.isFinite(Number(longitude));
    const destination = address || (hasCoordinates ? `${latitude},${longitude}` : '');
    if (!destination) return '';
    return /iPhone|iPad|Macintosh/.test(navigator.userAgent)
      ? `https://maps.apple.com/?daddr=${encodeURIComponent(destination)}`
      : `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(destination)}`;
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
      const checkedInHere = isCheckedInAtCourt(court.id);
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
          <button type="button" class="btn btn-primary" data-preview-play>${checkedInHere ? 'Find a game now' : 'I’m at this court'}</button>
          <a class="btn btn-secondary" data-preview-directions href="${courtDirectionsUrl(court)}" target="_blank" rel="noopener" style="display:flex;align-items:center;justify-content:center">Directions</a>
        </div>`;
      preview.classList.remove('hidden');
      preview.querySelector('[data-preview-detail]').addEventListener('click', () => openCourtDetail(court.id));
      preview.querySelector('[data-preview-play]').addEventListener('click', (event) => {
        if (checkedInHere) startInstantRally(event.currentTarget);
        else openCheckInSheet(court);
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
    const callerSession = instantRallySession();
    if (!callerSession) return;
    const callerLocation = [Number(state.userLoc[0]), Number(state.userLoc[1])];
    const requestIsCurrent = () => instantRallySessionMatches(callerSession)
      && Array.isArray(state.userLoc)
      && Number(state.userLoc[0]) === callerLocation[0]
      && Number(state.userLoc[1]) === callerLocation[1];
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
            if (!requestIsCurrent()) return;
            toast(`👋 Auto checked out of ${presence.court_name}`);
            await refreshMe();
            if (!requestIsCurrent()) return;
            fetchCourtsInView();
          } catch { /* ignore */ }
        }
      }
      return;
    }

    try {
      const data = await api(`/courts?lat=${callerLocation[0]}&lng=${callerLocation[1]}&radius=1&limit=3`);
      if (!requestIsCurrent()) return;
      const nearest = data.items[0];
      if (nearest && nearest.distance_miles != null && nearest.distance_miles <= AUTO_CHECKIN_MILES) {
        await api(`/courts/${nearest.id}/checkin`, {
          method: 'POST',
          body: JSON.stringify({ looking_for_game: false }),
        });
        if (!requestIsCurrent()) return;
        toast(`📍 Auto checked in at ${nearest.name}`);
        await refreshMe();
        if (!requestIsCurrent()) return;
        fetchCourtsInView();
      }
    } catch { /* offline */ }
  }

  function courtRowHtml(c) {
    const cond = c.condition && COURT_CONDITION_LABELS[c.condition];
    const quietNow = !(c.players_here > 0) && !(c.upcoming_games > 0);
    const reason = state.listSort === 'active'
      ? (c.players_here ? `${c.players_here} playing now`
        : c.upcoming_games ? `${c.upcoming_games} open game${c.upcoming_games === 1 ? '' : 's'}` : 'Quiet now')
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
    const liveMetrics = [];
    if (c.players_here > 0) liveMetrics.push(
      `<span class="court-card-metric live"><b>${c.players_here}</b><span>here now</span></span>`,
    );
    if (c.upcoming_games > 0) liveMetrics.push(
      `<span class="court-card-metric live"><b>${c.upcoming_games}</b><span>open game${c.upcoming_games === 1 ? '' : 's'}</span></span>`,
    );
    if (c.rating_count > 0 && c.rating_avg) liveMetrics.push(
      `<span class="court-card-metric"><b>⭐ ${c.rating_avg}</b><span>${c.rating_count} rating${c.rating_count === 1 ? '' : 's'}</span></span>`,
    );
    const activityHtml = quietNow
      ? `<span class="court-card-quiet">Quiet now${c.rating_count > 0 && c.rating_avg ? ` · ⭐ ${c.rating_avg}` : ''}</span>`
      : `<span class="court-card-metrics">${liveMetrics.join('')}</span>`;
    const reasonHtml = quietNow && state.listSort === 'active'
      ? '' : `<span class="court-card-reason">${reason}</span>`;
    const accessibleActivity = quietNow ? 'Quiet now' : [
      c.players_here > 0 ? `${c.players_here} here now` : '',
      c.upcoming_games > 0
        ? `${c.upcoming_games} open game${c.upcoming_games === 1 ? '' : 's'}` : '',
    ].filter(Boolean).join(', ');
    const accessibleAmenities = [
      c.indoor ? 'Indoor' : 'Outdoor',
      c.lighted ? 'Lights' : '',
      c.nets_provided ? 'Nets provided' : '',
      c.has_restrooms ? 'Restrooms' : '',
      c.has_water ? 'Water' : '',
    ].filter(Boolean).join(', ');
    const accessibleSummary = [
      `View ${c.name}`,
      c.distance_miles != null ? `${c.distance_miles} miles away` : c.city,
      accessibleActivity,
      cond ? cond[1] : '',
      `${c.num_courts} court${c.num_courts === 1 ? '' : 's'}`,
      c.rating_count > 0 && c.rating_avg
        ? `${c.rating_avg} stars from ${c.rating_count} rating${c.rating_count === 1 ? '' : 's'}` : '',
      accessibleAmenities,
    ].filter(Boolean).join('. ');
    return `
      <button type="button" class="court-decision-card ${quietNow ? 'quiet' : ''} ${state.selectedCourtId === c.id ? 'selected' : ''}" data-court="${c.id}" aria-label="${esc(accessibleSummary)}">
        <span class="court-card-head">
          <span class="court-card-name">${esc(c.name)}${cond ? ` <span class="tag ${c.condition === 'good' ? 'live' : 'warn'}" style="margin:0 0 0 5px;font-size:10px;padding:2px 7px">${cond[0]} ${esc(cond[1].split(' — ')[0].split(' /')[0])}</span>` : ''}</span>
          <span class="court-card-distance">${c.distance_miles != null ? `${c.distance_miles} mi` : esc(c.city || '')}</span>
        </span>
        ${reasonHtml}
        ${activityHtml}
        <span class="court-card-tags">
          <span class="tag">${c.num_courts} court${c.num_courts === 1 ? '' : 's'}</span>
          ${tags.slice(0, quietNow ? 2 : 4).map((tag) => `<span class="tag">${tag}</span>`).join('')}
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

    const searchMatches = searching
      ? splitExactCourtNameMatches(courts, state.searchQ)
      : { exact: [], other: courts };
    const displayCourts = [...searchMatches.exact, ...searchMatches.other];
    const visibleLimit = state.courtSheetSnap === 'peek' ? 2 : state.courtListLimit;
    const visibleCourts = displayCourts.slice(0, visibleLimit);
    const exactIds = new Set(searchMatches.exact.map((court) => court.id));
    const visibleExact = visibleCourts.filter((court) => exactIds.has(court.id));
    const visibleOther = visibleCourts.filter((court) => !exactIds.has(court.id));
    const placesHtml = places.map((p, i) => `
      <button type="button" class="card row" data-place="${i}" style="cursor:pointer;width:100%;text-align:left;color:var(--ink)">
        <span style="font-size:18px">📍</span>
        <div class="row-main">
          <div class="row-title">${esc(p.label)}</div>
          <div class="row-sub">${esc((p.detail || '').split(',').slice(1, 4).join(',').trim())}</div>
        </div>
        <span class="chev">›</span>
      </button>`).join('');

    // Filter recovery is the primary answer; related place jumps remain just
    // below it instead of pushing the action behind the active-game banner.
    if (recoveryBeforePlaces) html += emptyResultHtml();
    if (visibleExact.length) {
      html += '<div class="section-label" style="margin-top:4px">🏓 Exact court</div>';
      html += visibleExact.map(courtRowHtml).join('');
    }
    if (places.length) {
      html += '<div class="section-label" style="margin-top:4px">📍 Jump to area</div>';
      html += placesHtml;
    }

    if (courts.length) {
      if (visibleOther.length) {
        if (places.length || visibleExact.length) {
          html += `<div class="section-label">${visibleExact.length ? 'Other courts' : 'Courts'}</div>`;
        }
        html += visibleOther.map(courtRowHtml).join('');
      }
      if (visibleCourts.length < displayCourts.length) {
        const remaining = displayCourts.length - visibleCourts.length;
        const label = state.courtSheetSnap === 'peek'
          ? `Browse all ${displayCourts.length} courts`
          : `Show ${Math.min(20, remaining)} more · ${remaining} remaining`;
        html += `<button type="button" class="btn btn-primary btn-block" id="court-show-more" style="margin:2px 0 10px">${label}</button>`;
      }
    } else if (!recoveryBeforePlaces) html += emptyResultHtml();
    if (state.courtSheetSnap !== 'peek') {
      html += '<button class="btn btn-secondary btn-block" id="list-add-court" style="margin-top:10px">➕ Missing a court? Add it</button>';
    }

    el.innerHTML = html;
    el.querySelector('#list-add-court')?.addEventListener('click', openAddCourtSheet);
    el.querySelector('#court-clear-results')?.addEventListener('click', clearCourtFilters);
    el.querySelector('#court-show-more')?.addEventListener('click', () => {
      if (state.courtSheetSnap === 'peek') {
        const firstNewIndex = 2;
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
      row.addEventListener('click', () => openCourtDetail(Number(row.dataset.court)));
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
  const OVERLAY_ROUTE_KINDS = new Set(['court', 'game', 'tournament', 'club', 'crew', 'league']);
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
      const match = route.match(/^#(court|game|tournament|club|crew|league)\/(\d+)(?:\/match\/(\d+))?$/);
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
      const visibleText = btn.textContent.trim();
      if (visibleText.includes('‹')) btn.setAttribute('aria-label', 'Back');
      else if (!visibleText || /^[✕×]$/.test(visibleText)) btn.setAttribute('aria-label', 'Close');
    });
  }

  function openModal(html, opts = {}) {
    const root = $('#overlay-root');
    const previousFocus = document.activeElement;
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop'
      + (opts.chat ? ' chat-modal' : '')
      + (opts.court ? ' court-modal' : '')
      + (opts.page ? ' page-modal' : '');
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
    const match = String(channelKey || '').match(/^(dm|court|game|tournament|club|crew|league):([1-9]\d*)$/);
    if (!match) return null;
    const [, kind, id] = match;
    if (kind === 'dm') return `/chat/${id}`;
    const collections = {
      court: 'courts', game: 'games', tournament: 'tournaments', club: 'clubs', crew: 'crews', league: 'leagues',
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
        if (sessionRevision !== chatOutboxSessionRevision
            || chatOutboxCancelledAttempts.has(item.id)) {
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
            || !state.token || state.me?.id !== item.accountId
            || chatOutboxCancelledAttempts.has(item.id)) {
          await chatOutboxStore.remove(item.id).catch(() => {});
          return null;
        }
        if (Number(error.status) === 404 && item.channelKey.startsWith('crew:')) {
          chatOutboxCancelledAttempts.add(item.id);
          clearChatOutboxRetry(item.id);
          await chatOutboxStore.remove(item.id).catch(() => {});
          notifyChatOutboxBindings(item.accountId, item.channelKey, {
            announcement: 'This Crew conversation is no longer available. The unsent message was removed.',
          });
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

  async function purgeChatOutboxChannel(rawAccountId, channelKey) {
    const accountId = chatOutboxAccountId(rawAccountId);
    if (!accountId || !channelKey) return;
    let items = [];
    try { items = await listChatOutbox(accountId, channelKey); } catch { /* best-effort cleanup */ }
    items.forEach((item) => {
      chatOutboxCancelledAttempts.add(item.id);
      clearChatOutboxRetry(item.id);
    });
    await Promise.all(items.map((item) => chatOutboxStore.remove(item.id).catch(() => {})));
    chatOutboxBindings.delete(chatOutboxBindingKey(accountId, channelKey));
    try {
      sessionStorage.removeItem(
        `pp_chat_draft_v${CHAT_DRAFT_VERSION}:${accountId}:${encodeURIComponent(channelKey)}`,
      );
    } catch { /* storage unavailable */ }
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
  const CHAT_POLL_DELAYS_MS = [15_000, 30_000, 60_000];
  const CHAT_POLL_WAKE_THROTTLE_MS = 1500;

  // Open conversations stay responsive while somebody is actively using them,
  // then progressively back off after empty polls. Foregrounding the app or
  // reconnecting wakes the visible room immediately; hidden/stacked modals do
  // not spend serverless invocations.
  function startAdaptiveChatPoll(modal, msgsEl, poll) {
    let timer = null;
    let dueAt = 0;
    let idleIndex = 0;
    let running = false;
    let rerun = false;
    let stopped = false;
    let lastRunAt = 0;
    let activityRevision = 0;
    const startedAt = Date.now();

    const canPoll = () => document.body.contains(msgsEl)
      && currentOverlayEntry()?.el === modal
      && !document.hidden
      && state.connectionState !== 'offline';

    const schedule = (delay, { onlySooner = false } = {}) => {
      if (stopped) return;
      const nextDueAt = Date.now() + delay;
      if (onlySooner && timer != null && dueAt <= nextDueAt) return;
      clearTimeout(timer);
      dueAt = nextDueAt;
      timer = setTimeout(() => {
        timer = null;
        dueAt = 0;
        run();
      }, delay);
    };

    const run = async () => {
      if (stopped) return;
      if (!document.body.contains(msgsEl)) {
        stop();
        return;
      }
      if (!canPoll()) {
        schedule(CHAT_POLL_DELAYS_MS[0]);
        return;
      }
      if (running) {
        rerun = true;
        return;
      }
      running = true;
      lastRunAt = Date.now();
      const revisionAtStart = activityRevision;
      let changed = false;
      try { changed = !!(await poll()); } catch { /* retry on the backed-off cadence */ }
      finally {
        running = false;
        if (stopped) return;
        idleIndex = changed || activityRevision !== revisionAtStart
          ? 0 : Math.min(idleIndex + 1, CHAT_POLL_DELAYS_MS.length - 1);
        if (rerun) {
          rerun = false;
          schedule(0);
        } else {
          schedule(CHAT_POLL_DELAYS_MS[idleIndex]);
        }
      }
    };

    const wake = ({ immediate = false } = {}) => {
      if (stopped) return;
      activityRevision += 1;
      idleIndex = 0;
      if (!immediate) {
        schedule(CHAT_POLL_DELAYS_MS[0], { onlySooner: true });
        return;
      }
      if (!canPoll()) return;
      if (running) {
        if (Date.now() - lastRunAt >= CHAT_POLL_WAKE_THROTTLE_MS) rerun = true;
        return;
      }
      const throttle = Math.max(0, CHAT_POLL_WAKE_THROTTLE_MS - (Date.now() - lastRunAt));
      schedule(throttle, { onlySooner: true });
    };

    const onActivity = () => wake();
    const onModalFocus = () => {
      if (Date.now() - startedAt >= CHAT_POLL_WAKE_THROTTLE_MS) wake({ immediate: true });
    };
    const onForeground = () => { if (!document.hidden) wake({ immediate: true }); };
    const onOnline = () => wake({ immediate: true });
    const stop = () => {
      if (stopped) return;
      stopped = true;
      clearTimeout(timer);
      timer = null;
      dueAt = 0;
      modal.removeEventListener('input', onActivity);
      modal.removeEventListener('pointerdown', onActivity);
      modal.removeEventListener('focusin', onModalFocus);
      document.removeEventListener('visibilitychange', onForeground);
      window.removeEventListener('focus', onForeground);
      window.removeEventListener('online', onOnline);
    };

    modal.addEventListener('input', onActivity);
    modal.addEventListener('pointerdown', onActivity);
    modal.addEventListener('focusin', onModalFocus);
    document.addEventListener('visibilitychange', onForeground);
    window.addEventListener('focus', onForeground);
    window.addEventListener('online', onOnline);
    modal._cleanupFns?.push(stop);
    schedule(CHAT_POLL_DELAYS_MS[0]);
    return { stop, wake };
  }

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
        ? `<button type="button" class="star-btn ${filled ? 'on' : ''}" data-star="${i}" aria-label="${i} star${i === 1 ? '' : 's'}" aria-pressed="${filled}">★</button>`
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
    const courtAddressDisplay = [court.address, court.city].filter(Boolean).join(', ')
      || (court.latitude != null ? `${court.latitude.toFixed(5)}, ${court.longitude.toFixed(5)}` : '');
    const courtAddressText = [court.address, court.city, court.state, court.zip_code]
      .filter(Boolean).join(', ') || courtAddressDisplay;

    const visiblePlayersHere = Array.isArray(court.players_here) ? court.players_here : [];
    const nHere = Math.max(visiblePlayersHere.length, Number(court.players_here_count) || 0);
    const privatePlayersHere = Math.max(0, nHere - visiblePlayersHere.length);
    const playersHtml = visiblePlayersHere.length
      ? visiblePlayersHere.map((p) => {
          const badges = [];
          if (p.is_me) badges.push('<span class="tag" style="margin:0 0 0 6px">You</span>');
          else if (p.is_friend) badges.push('<span class="tag" style="margin:0 0 0 6px">🤝 Friend</span>');
          if (p.looking_for_game) badges.push('<span class="tag live" style="margin:0 0 0 6px">Wants to play</span>');
          const record = (p.ranked_wins + p.ranked_losses) > 0 ? ` · ${p.ranked_wins}W–${p.ranked_losses}L` : '';
          const actions = p.is_me ? ''
            : `<button class="btn btn-secondary btn-sm" data-msg-user="${p.id}" aria-label="Message ${esc(p.display_name)}">Message</button>`;
          return `
          <div class="card row" style="padding:11px">
            <div data-view-user="${p.id}" style="cursor:pointer">${avatarHtml(p)}</div>
            <div class="row-main" data-view-user="${p.id}" style="cursor:pointer">
              <div class="row-title" style="display:flex;align-items:center;flex-wrap:wrap">${esc(p.display_name)}${badges.join('')}</div>
              <div class="row-sub">${skillLabel(p.skill_level)} · ${p.rating}${record} · here ${fmtDuration(p.minutes_here)}</div>
            </div>
            ${actions}
          </div>`;
        }).join('') + (privatePlayersHere
          ? `<div class="row-sub" style="padding:2px 8px 10px">＋ ${privatePlayersHere} other checked-in player${privatePlayersHere === 1 ? '' : 's'} not shown here.</div>` : '')
      : nHere
        ? `<div class="empty-state" style="padding:14px">${nHere} player${nHere === 1 ? ' is' : 's are'} checked in. Their profiles aren’t shared here.</div>`
        : '<div class="empty-state" style="padding:14px">No one checked in right now — be the first!</div>';

    const allCourtGames = Array.isArray(court.games) ? court.games : [];
    let gamesHtml = '';
    // Group by day (backend sends them sorted by scheduled_at).
    const gamesByDay = [];
    if (allCourtGames.length) {
      for (const g of allCourtGames) {
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
      gamesHtml = '<div class="empty-state" style="padding:14px">No games scheduled yet.</div>';
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

    // "Now" is the decision users opened the court to make. Static venue
    // details and account-management actions stay one disclosure away.
    const fallbackNowLimit = Date.now() + 2 * 60 * 60 * 1000;
    const nowGames = Array.isArray(court.now_games) ? court.now_games : allCourtGames.filter((game) => (
      new Date(game.scheduled_at).getTime() <= fallbackNowLimit
    ));
    const actionableGames = nowGames.filter((game) => game.is_joined || Number(game.spots_left) > 0);
    const nGames = actionableGames.length;
    const myOpenGame = nowGames.find((game) => game.is_joined && game.status === 'upcoming') || null;
    const quietNow = nHere === 0 && nGames === 0;
    const nowSummary = quietNow
      ? '<p class="cd-now-quiet">Quiet now — be the first to get a game going.</p>'
      : `<div class="cd-now-signals">
          <button type="button" class="cd-now-signal${nHere ? ' hot' : ''}" data-scroll-to="cd-sec-players">
            <b>${nHere}</b><span>at the court</span><span class="chev" aria-hidden="true">›</span>
          </button>
          <button type="button" class="cd-now-signal${nGames ? ' hot' : ''}" data-scroll-to="cd-sec-games">
            <b>${nGames}</b><span>open game${nGames === 1 ? '' : 's'}</span><span class="chev" aria-hidden="true">›</span>
          </button>
        </div>`;
    const primaryAction = myOpenGame
      ? `<button class="btn btn-primary btn-block cd-primary-action" id="cd-open-game" data-game-id="${myOpenGame.id}">Open your game</button>`
      : checkedIn
        ? '<button class="btn btn-primary btn-block cd-primary-action" id="cd-play-now">Find or start a game</button>'
        : `<a class="btn btn-primary btn-block cd-primary-action" href="${mapsUrl}" target="_blank" rel="noopener">Get directions</a>`;
    const secondaryActions = `
      <div class="cd-now-actions">
        ${checkedIn ? '' : '<button type="button" class="btn btn-secondary" id="cd-checkin">I’m at this court</button>'}
        <button type="button" class="btn btn-secondary" id="cd-schedule">Plan a game</button>
        <button type="button" class="btn btn-secondary" id="cd-chat">${checkedIn ? 'Message players' : 'Message the court'}</button>
      </div>`;

    if (!routedOverlayLoadIsCurrent(routeLoad)) return;
    const modal = openModal(`
      <div class="cd-hero">
        ${heroImg}
        <div class="cd-hero-shade"></div>
        <div class="cd-hero-actions">
          <details class="cd-hero-more">
            <summary class="glass-btn" aria-label="More court actions">More</summary>
            <div class="cd-hero-more-menu">
              <button type="button" id="cd-share">Share court</button>
              <button type="button" id="cd-favorite">${isFavorite ? '★ Saved' : '☆ Save court'}</button>
              ${court.photo_count > 0
                ? `<button type="button" id="cd-gallery">📷 View ${court.photo_count} photo${court.photo_count === 1 ? '' : 's'}</button>`
                : '<button type="button" id="cd-add-photo">📷 Add a photo</button>'}
            </div>
          </details>
          <button class="glass-btn modal-close" aria-label="Close">✕</button>
        </div>
        <div class="cd-hero-title">
          <h2>${esc(court.name)}</h2>
          <button type="button" id="cd-address" class="cd-address-copy" title="Copy address" aria-label="Copy court address: ${esc(courtAddressText)}">
            ${esc(courtAddressDisplay)}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:13px;height:13px;vertical-align:-2px;opacity:.85"><rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>
          </button>
        </div>
      </div>
      <div class="cd-scroll">
      ${court.closed ? '<div class="card" style="background:var(--red-50);color:var(--red-700);text-align:center;padding:10px 14px;margin-bottom:10px;font-weight:700">🚫 This court is reported permanently closed</div>' : ''}
      <section class="card cd-now-card" aria-labelledby="cd-now-heading">
        <div class="cd-now-heading">
          <div>
            <div class="row-sub">At this court</div>
            <h3 id="cd-now-heading">Now at this court</h3>
          </div>
          ${quietNow ? '<span class="tag">Quiet now</span>' : '<span class="tag live">Active now</span>'}
        </div>
        ${nowSummary}
        ${primaryAction}
        ${secondaryActions}
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
      </section>
      <div class="section-label" id="cd-sec-players">Playing now (${nHere})${court.friends_here ? ` · ${court.friends_here} friend${court.friends_here === 1 ? '' : 's'} here` : ''}</div>
      ${playersHtml}
      <div class="section-label" id="cd-sec-games">Upcoming games</div>
      ${gamesHtml}
      <details class="card cd-progressive cd-court-details">
        <summary>Court details</summary>
        <div class="cd-progressive-body">
          <div>${chipsHtml}</div>
          ${court.busy_times && court.busy_times.length
            ? `<div class="row-sub" style="margin-top:10px">📊 Popular here: ${court.busy_times.map((b) => esc(b.label)).join(' · ')}</div>`
            : ''}
          ${court.open_play_schedule ? `
            <div class="cd-hours">
              <div class="row-title">🕑 Open play hours</div>
              <p>${esc(court.open_play_schedule)}</p>
            </div>` : ''}
          ${linkParts.length ? `<div class="cd-links">${linkParts.join('')}</div>` : ''}
          <div class="cd-management-actions">
            ${state.token && state.me && state.me.home_court_id !== court.id
              ? '<button id="cd-sethome" class="tag" style="border:1px dashed var(--line);background:transparent;cursor:pointer">🏠 Make home court</button>'
              : (state.me && state.me.home_court_id === court.id ? '<span class="tag" style="margin:0">🏠 Your home court</span>' : '')}
            <button id="cd-suggest" class="tag" style="border:1px dashed var(--line);background:transparent;cursor:pointer">✏️ Suggest an edit</button>
            <button id="cd-condition" class="tag" style="border:1px dashed var(--line);background:transparent;cursor:pointer">📣 Report conditions</button>
          </div>
          ${checkedIn ? '<button type="button" class="btn btn-danger btn-block" id="cd-checkout">Check out</button>' : ''}
        </div>
      </details>
      <details class="card cd-progressive cd-community-details">
        <summary>More at this court</summary>
        <div class="cd-progressive-body">
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
      ${(court.recent_results || []).length ? `
        <div class="section-label">Recent results here</div>
        ${court.recent_results.map(resultRowHtml).join('')}` : ''}
        </div>
      </details>
      <details class="card cd-progressive cd-reviews-details">
        <summary id="cd-sec-reviews">Reviews${court.rating_avg ? ` · ⭐ ${court.rating_avg} (${court.rating_count})` : ''}</summary>
        <div class="cd-progressive-body" id="cd-reviews"></div>
      </details>
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

    modal.querySelector('#cd-checkin')?.addEventListener('click', () => {
      transitionModal(modal, () => openCheckInSheet(court));
    });
    modal.querySelector('#cd-checkout')?.addEventListener('click', async () => {
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
      // writeText can reject even on secure contexts (unfocused document,
      // denied permission) — always fall back to the hidden-textarea trick.
      let copied = false;
      if (navigator.clipboard && window.isSecureContext) {
        copied = await navigator.clipboard.writeText(courtAddressText).then(() => true, () => false);
      }
      if (!copied) {
        try {
          const ta = document.createElement('textarea');
          ta.value = courtAddressText;
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

    modal.querySelector('#cd-play-now')?.addEventListener('click', (event) => {
      startInstantRally(event.currentTarget, { fromModal: modal });
    });
    modal.querySelector('#cd-open-game')?.addEventListener('click', (event) => {
      transitionModal(modal, () => openGameScreen(Number(event.currentTarget.dataset.gameId)));
    });
    modal.querySelector('#cd-schedule').addEventListener('click', () => {
      transitionModal(modal, () => openNewGameModal({ court }));
    });
    modal.querySelector('#cd-schedule-empty')?.addEventListener('click', () => {
      transitionModal(modal, () => openNewGameModal({ court }));
    });

    modal.querySelector('#cd-favorite').addEventListener('click', async (e) => {
      const favBtn = e.currentTarget;
      try {
        const data = await api(`/courts/${court.id}/favorite`, { method: 'POST' });
        isFavorite = data.favorited;
        favBtn.textContent = isFavorite ? '★ Saved' : '☆ Save court';
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

  function isCheckedInAtCourt(courtId) {
    return !!(state.presence && state.presence.checked_in
      && Number(state.presence.court_id) === Number(courtId));
  }

  function playNowCourt(value, tag = '') {
    if (!value || typeof value !== 'object') return null;
    const id = safePositiveId(value.id ?? value.court_id);
    if (!id) return null;
    return {
      id,
      name: String(value.name ?? value.court_name ?? 'Pickleball court').slice(0, 120),
      city: String(value.city || '').slice(0, 120),
      distanceMiles: Number.isFinite(Number(value.distance_miles)) ? Number(value.distance_miles) : null,
      tag: String(tag || value.tag || '').slice(0, 40),
    };
  }

  function playPulseRequestIsAmbiguous(error) {
    const status = Number(error && error.status) || 0;
    return !!(error && (error.isNetworkError || error.data?.retryable === true))
      || status === 408 || status === 425
      || status === 429 || status >= 500;
  }

  function playPulseDirectionsUrl(pulse) {
    if (!pulse) return '';
    return courtDirectionsUrl(pulse.court || {
      id: pulse.courtId,
      name: pulse.courtName,
      city: pulse.courtCity,
      address: pulse.courtAddress,
      latitude: pulse.courtLatitude,
      longitude: pulse.courtLongitude,
    });
  }

  function refreshPlayPulseSurfaces() {
    state.playGamesCache = null;
    refreshLookingBanner();
    if (state.tab === 'play' && state.playSeg === 'games') renderPlay();
    if (state.tab === 'chat' && state.chatSeg === 'friends' && state.peopleMode === 'nearby') renderChat();
  }

  async function declarePlayPulse(court, modal, button, errorEl = null) {
    const selected = playNowCourt(court);
    if (!selected || !button || button.dataset.playPulseCreating === 'true') return null;
    const callerSession = instantRallySession();
    const showError = (message) => {
      if (!errorEl || !document.body.contains(errorEl)) return;
      errorEl.textContent = message;
      errorEl.classList.remove('hidden');
      errorEl.focus({ preventScroll: true });
    };
    if (!callerSession) {
      showError('Sign in again before sharing that you’re free this hour.');
      return null;
    }
    const attempt = pendingPlayPulseCreateAttempt(callerSession.userId, selected.id);
    if (!attempt) {
      showError('This device could not save your request. Free some browser storage and try again.');
      return null;
    }
    const original = button.textContent;
    const modalBox = modal && modal.querySelector('.modal');
    button.dataset.playPulseCreating = 'true';
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.textContent = 'Sharing that you’re free…';
    modalBox?.setAttribute('aria-busy', 'true');
    errorEl?.classList.add('hidden');
    let record = playPulseCreateInFlight;
    if (!record || record.token !== callerSession.token || record.userId !== callerSession.userId
        || record.courtId !== selected.id || record.attemptId !== attempt.id) {
      record = {
        token: callerSession.token,
        userId: callerSession.userId,
        courtId: selected.id,
        attemptId: attempt.id,
        promise: api('/play/pulse', {
          method: 'PUT',
          body: JSON.stringify({ court_id: selected.id, client_attempt_id: attempt.id }),
        }),
      };
      playPulseCreateInFlight = record;
    }
    try {
      const response = await record.promise;
      if (!instantRallySessionMatches(callerSession)) return null;
      const responsePulse = playPulseFromValue(response && response.pulse);
      if (!responsePulse) {
        const malformed = new Error('We couldn’t confirm Free this hour. Try again.');
        malformed.isNetworkError = true;
        throw malformed;
      }
      const pulse = normalizeActivePlayPulse(responsePulse);
      clearPlayPulseCreateAttempt(callerSession.userId, selected.id, attempt.id);
      if (!pulse) {
        invalidateMeRequests();
        state.activePlayPulse = null;
        if (modal && document.body.contains(modal)) closeModal(modal);
        refreshPlayPulseSurfaces();
        refreshMe().catch(() => {});
        toast('That Free this hour window already ended. Start a new one when you’re still free.');
        return null;
      }
      invalidateMeRequests();
      state.activePlayPulse = pulse;
      refreshPlayPulseSurfaces();
      refreshMe().catch(() => {});
      toast(`You’re free this hour at ${pulse.courtName} · until ${fmtTimeShort(pulse.expiresAt)}`);
      if (modal && document.body.contains(modal) && currentOverlayEntry()?.el === modal) {
        transitionModal(modal, () => openPlayPulseDetails(pulse));
      }
      return pulse;
    } catch (error) {
      if (!instantRallySessionMatches(callerSession)) return null;
      const active = normalizeActivePlayPulse(error?.data?.pulse);
      if (error.code === 'pulse_already_active' && active) {
        invalidateMeRequests();
        state.activePlayPulse = active;
        clearPlayPulseCreateAttempt(callerSession.userId, selected.id, attempt.id);
        refreshPlayPulseSurfaces();
        if (modal && document.body.contains(modal) && currentOverlayEntry()?.el === modal) {
          transitionModal(modal, () => openPlayPulseDetails(active));
        }
        return active;
      }
      const ambiguous = playPulseRequestIsAmbiguous(error);
      if (!ambiguous) {
        clearPlayPulseCreateAttempt(callerSession.userId, selected.id, attempt.id);
      }
      showError(ambiguous
        ? 'Couldn’t confirm Free this hour. Try again.'
        : error.message);
      return null;
    } finally {
      if (playPulseCreateInFlight === record) playPulseCreateInFlight = null;
      if (button && document.body.contains(button)) {
        delete button.dataset.playPulseCreating;
        button.disabled = false;
        button.removeAttribute('aria-busy');
        button.textContent = errorEl && !errorEl.classList.contains('hidden')
          ? 'Try again' : original;
        modalBox?.removeAttribute('aria-busy');
      }
    }
  }

  async function cancelPlayPulse(pulseValue, button = null, modal = null) {
    const pulse = normalizeActivePlayPulse(pulseValue) || playPulseFromValue(pulseValue);
    if (!pulse || (button && button.dataset.playPulseCancelling === 'true')) return false;
    const callerSession = instantRallySession();
    if (!callerSession) return false;
    const original = button && button.textContent;
    if (button) {
      button.dataset.playPulseCancelling = 'true';
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.textContent = 'Cancelling…';
    }
    try {
      const response = await api(`/play/pulses/${pulse.id}`, { method: 'DELETE' });
      if (!instantRallySessionMatches(callerSession)) return false;
      invalidateMeRequests();
      if (state.activePlayPulse?.id === pulse.id) state.activePlayPulse = null;
      clearPlayPulseCreateAttempt(callerSession.userId, pulse.courtId);
      if (modal && document.body.contains(modal)) closeModal(modal);
      if (response?.cancelled === false && response?.pulse?.end_reason === 'matched') {
        toast('A player already accepted — your quick game was not cancelled.');
      } else if (response?.cancelled === false) {
        toast('That Free this hour post had already ended.');
      } else {
        toast('Free this hour ended');
      }
      refreshPlayPulseSurfaces();
      refreshMe().catch(() => {});
      return true;
    } catch (error) {
      if (!instantRallySessionMatches(callerSession)) return false;
      if (error.code === 'pulse_not_found') {
        invalidateMeRequests();
        if (state.activePlayPulse?.id === pulse.id) state.activePlayPulse = null;
        if (modal && document.body.contains(modal)) closeModal(modal);
        refreshPlayPulseSurfaces();
        refreshMe().catch(() => {});
        toast('That Free this hour post already ended');
        return true;
      }
      toast(error.message);
      return false;
    } finally {
      if (button && document.body.contains(button)) {
        delete button.dataset.playPulseCancelling;
        button.disabled = false;
        button.removeAttribute('aria-busy');
        button.textContent = original;
      }
    }
  }

  function playPulseCommitmentCopy(pulse) {
    const first = String(pulse?.user?.display_name || 'This player').split(/\s+/)[0];
    return `${first} chose ${pulse?.courtName || 'this court'} as an intended destination, not a current location. Confirming creates an open quick game starting in about 15 minutes for both of you and notifies ${first}; it does not check either player in.`;
  }

  async function refreshPlayPulseAcceptAttempt(pulse, attempt, callerSession) {
    const loc = areaLatLng();
    const looking = await api(`/players/looking?lat=${encodeURIComponent(loc.lat)}&lng=${encodeURIComponent(loc.lng)}&radius=50`);
    if (!instantRallySessionMatches(callerSession)) return null;
    const refreshedPulse = normalizeLookingPulses(looking).find((item) => item.id === pulse.id);
    if (!refreshedPulse || !refreshedPulse.acceptCapability
        || refreshedPulse.courtId !== pulse.courtId) return null;
    const refreshedAttempt = {
      ...attempt,
      acceptCapability: refreshedPulse.acceptCapability,
      capabilityRefreshedAt: Date.now(),
    };
    const storageKey = playPulseAcceptAttemptKey(callerSession.userId, pulse.id);
    if (!storageKey || !persistRecoveryValue(storageKey, JSON.stringify(refreshedAttempt))) {
      const storageError = new Error('This device could not save your request. Free some browser storage and try again.');
      storageError.code = 'retry_storage_unavailable';
      throw storageError;
    }
    return refreshedAttempt;
  }

  async function postPlayPulseAcceptance(pulse, attempt, callerSession) {
    const send = (record) => api(`/play/pulses/${pulse.id}/accept`, {
      method: 'POST',
      body: JSON.stringify({
        accept_capability: record.acceptCapability,
        client_attempt_id: record.id,
      }),
    });
    try {
      return await send(attempt);
    } catch (error) {
      // Exact replay is attempted first. A 404 therefore proves this request
      // did not already create a game; refresh a stale discovery capability
      // and retry once with the same id if the pulse is still discoverable.
      if (error.code !== 'pulse_not_found') throw error;
      const refreshedAttempt = await refreshPlayPulseAcceptAttempt(
        pulse, attempt, callerSession,
      );
      if (!refreshedAttempt) throw error;
      return send(refreshedAttempt);
    }
  }

  async function acceptPlayPulse(pulseValue, button, modal = null, errorEl = null) {
    const pulseRecord = playPulseFromValue(pulseValue);
    const savedAttempt = pulseRecord && readPlayPulseAcceptAttempt(
      state.me && state.me.id, pulseRecord.id,
    );
    // Once an acceptance POST may have crossed the wire, local expiry must not
    // block exact replay: the server can still return the already-created game.
    const activePulse = normalizeActivePlayPulse(pulseRecord);
    const pulse = activePulse || (savedAttempt && pulseRecord
      ? { ...pulseRecord, acceptCapability: savedAttempt.acceptCapability }
      : null);
    if (!pulse || !pulse.acceptCapability || !button
        || button.dataset.playPulseAccepting === 'true') return null;
    const callerSession = instantRallySession();
    const showError = (message) => {
      if (errorEl && document.body.contains(errorEl)) {
        errorEl.textContent = message;
        errorEl.classList.remove('hidden');
        errorEl.focus({ preventScroll: true });
      } else toast(message);
    };
    if (!callerSession) {
      showError('Sign in again before creating the quick game.');
      return null;
    }
    const attempt = pendingPlayPulseAcceptAttempt(
      callerSession.userId, pulse.id, pulse.acceptCapability,
    );
    if (!attempt) {
      showError('This device could not save your request. Free some browser storage and try again.');
      return null;
    }
    const original = button.textContent;
    button.dataset.playPulseAccepting = 'true';
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.textContent = 'Creating game…';
    modal?.querySelector('.modal')?.setAttribute('aria-busy', 'true');
    errorEl?.classList.add('hidden');
    const key = `${callerSession.userId}:${pulse.id}`;
    let record = playPulseAcceptInFlight.get(key);
    if (!record || record.token !== callerSession.token || record.attemptId !== attempt.id) {
      record = {
        token: callerSession.token,
        attemptId: attempt.id,
        promise: postPlayPulseAcceptance(pulse, attempt, callerSession),
      };
      playPulseAcceptInFlight.set(key, record);
    }
    try {
      const response = await record.promise;
      if (!instantRallySessionMatches(callerSession)) return null;
      const gameId = safePositiveId(response && response.game && response.game.id);
      if (!gameId) {
        const malformed = new Error('We couldn’t confirm the quick game. Try again.');
        malformed.isNetworkError = true;
        throw malformed;
      }
      clearPlayPulseAcceptAttempt(callerSession.userId, pulse.id, attempt.id);
      invalidateMeRequests();
      state.playGamesCache = null;
      refreshLookingBanner();
      refreshMe().catch(() => {});
      toast(`Quick game created at ${pulse.courtName}`);
      if (modal && document.body.contains(modal) && currentOverlayEntry()?.el === modal) {
        transitionModal(modal, () => openGameScreen(gameId));
      } else openGameScreen(gameId);
      if (state.tab === 'chat' && state.chatSeg === 'friends' && state.peopleMode === 'nearby') renderChat();
      return response;
    } catch (error) {
      if (!instantRallySessionMatches(callerSession)) return null;
      const ambiguous = playPulseRequestIsAmbiguous(error);
      if (!ambiguous) {
        clearPlayPulseAcceptAttempt(callerSession.userId, pulse.id, attempt.id);
      }
      if (['pulse_not_found', 'pulse_start_window_closed', 'invalid_accept_capability'].includes(error.code)) {
        refreshLookingBanner();
        if (state.tab === 'chat' && state.chatSeg === 'friends' && state.peopleMode === 'nearby') renderChat();
      }
      showError(ambiguous
        ? 'Couldn’t confirm the game. Try again.'
        : error.message);
      return null;
    } finally {
      if (playPulseAcceptInFlight.get(key) === record) playPulseAcceptInFlight.delete(key);
      if (button && document.body.contains(button)) {
        delete button.dataset.playPulseAccepting;
        button.disabled = false;
        button.removeAttribute('aria-busy');
        button.textContent = errorEl && !errorEl.classList.contains('hidden')
          ? 'Try again' : original;
        modal?.querySelector('.modal')?.removeAttribute('aria-busy');
      }
    }
  }

  function openPlayPulseAcceptConfirmation(pulseValue) {
    const pulseRecord = playPulseFromValue(pulseValue);
    const retry = pulseRecord && readPlayPulseAcceptAttempt(
      state.me && state.me.id, pulseRecord.id,
    );
    const pulse = normalizeActivePlayPulse(pulseRecord) || (retry ? pulseRecord : null);
    if (!pulse) {
      toast('That Free this hour post is no longer active.');
      if (state.tab === 'chat' && state.chatSeg === 'friends' && state.peopleMode === 'nearby') renderChat();
      return null;
    }
    const first = String(pulse.user?.display_name || 'This player').split(/\s+/)[0];
    const modal = openModal(`
      ${modalHead(`🏓 Play at ${pulse.courtName}`)}
      <div class="play-pulse-confirm-person">
        ${pulse.user ? avatarHtml(pulse.user, 'sm') : '<span class="play-pulse-avatar" aria-hidden="true">🏓</span>'}
        <div><b>${esc(first)} can play at ${esc(pulse.courtName)} this hour</b><span>Free until ${esc(fmtTimeShort(pulse.expiresAt))}</span></div>
      </div>
      <p class="play-pulse-commitment">${esc(playPulseCommitmentCopy(pulse))}</p>
      <p class="form-error hidden" id="play-pulse-accept-error" role="alert" tabindex="-1"></p>
      <button type="button" class="btn btn-primary btn-block" id="play-pulse-accept-confirm">${retry ? 'Try again' : 'Create quick game'}</button>
      <button type="button" class="btn-link modal-close btn-block">Not now</button>
    `, { label: `Create a quick game with ${first} at ${pulse.courtName}` });
    const button = modal.querySelector('#play-pulse-accept-confirm');
    button.addEventListener('click', () => acceptPlayPulse(
      pulse, button, modal, modal.querySelector('#play-pulse-accept-error'),
    ));
    return modal;
  }

  function openPlayPulseDetails(pulseValue = state.activePlayPulse) {
    const pulse = normalizeActivePlayPulse(pulseValue);
    if (!pulse) {
      state.activePlayPulse = null;
      toast('Your Free this hour post has ended.');
      refreshPlayPulseSurfaces();
      return null;
    }
    const directions = playPulseDirectionsUrl(pulse);
    const modal = openModal(`
      ${modalHead('Free this hour')}
      <div class="play-pulse-detail-card">
        <span aria-hidden="true">📍</span>
        <div><b>${esc(pulse.courtName)}</b><span>Free until ${esc(fmtTimeShort(pulse.expiresAt))}</span></div>
      </div>
      <p class="play-pulse-commitment">This court is your intended destination, not your current location or a check-in. The first nearby player who accepts creates an open quick game starting in about 15 minutes for both of you and you’ll be notified.</p>
      ${directions ? `<a class="btn btn-secondary btn-block play-pulse-directions" href="${directions}" target="_blank" rel="noopener" aria-label="Directions to ${esc(pulse.courtName)} (opens Maps)">Directions</a>` : ''}
      <button type="button" class="btn btn-danger btn-block" id="play-pulse-cancel-detail">End Free this hour</button>
      <button type="button" class="btn-link modal-close btn-block">Done</button>
    `, { label: `Free this hour at ${pulse.courtName}` });
    modal.querySelector('#play-pulse-cancel-detail').addEventListener('click', (event) => {
      cancelPlayPulse(pulse, event.currentTarget, modal);
    });
    return modal;
  }

  function activePlayPulseBannerHtml(pulseValue = state.activePlayPulse) {
    const pulse = normalizeActivePlayPulse(pulseValue);
    if (!pulse) return '';
    const directions = playPulseDirectionsUrl(pulse);
    return `
      <section class="play-pulse-active" aria-label="Free this hour at ${esc(pulse.courtName)} until ${esc(fmtTimeShort(pulse.expiresAt))}. This is an intended destination, not a check-in.">
        <button type="button" class="play-pulse-active-main" data-play-pulse-details>
          <span class="play-pulse-active-icon" aria-hidden="true">📍</span>
          <span><b>Free this hour at ${esc(pulse.courtName)}</b><small>Until ${esc(fmtTimeShort(pulse.expiresAt))} · intended destination</small></span>
          <span class="chev" aria-hidden="true">›</span>
        </button>
        <div class="play-pulse-active-actions">
          ${directions ? `<a href="${directions}" target="_blank" rel="noopener" aria-label="Directions to ${esc(pulse.courtName)} (opens Maps)">Directions</a>` : '<button type="button" data-play-pulse-details>Details</button>'}
          <button type="button" data-play-pulse-cancel>Cancel</button>
        </div>
      </section>`;
  }

  async function checkInAndStartRally(court, modal, button, errorEl = null) {
    const selected = playNowCourt(court);
    if (!selected || !button || button.dataset.playNowStarting === 'true') return null;
    const original = button.textContent;
    const modalBox = modal && modal.querySelector('.modal');
    const showError = (message) => {
      if (!errorEl || !document.body.contains(errorEl)) return;
      errorEl.textContent = message;
      errorEl.classList.remove('hidden');
      errorEl.focus({ preventScroll: true });
    };
    const callerSession = instantRallySession();
    if (!callerSession) {
      showError('Sign in again before checking in.');
      return null;
    }
    button.dataset.playNowStarting = 'true';
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.textContent = 'Checking you in…';
    modalBox?.setAttribute('aria-busy', 'true');
    errorEl?.classList.add('hidden');
    try {
      const checkedIn = await api(`/courts/${selected.id}/checkin`, {
        method: 'POST',
        body: JSON.stringify({ looking_for_game: true }),
      });
      if (!instantRallySessionMatches(callerSession)) return null;
      // The check-in POST is authoritative even before the next /me poll. This
      // lets the rally request follow immediately without a redundant round trip.
      const fallbackPresence = {
        ...(state.presence || {}),
        checked_in: true,
        court_id: selected.id,
        court_name: selected.name,
        looking_for_game: true,
      };
      // The POST response is newer than any background /me already in flight.
      invalidateMeRequests();
      state.presence = checkedIn && checkedIn.presence
        ? checkedIn.presence : fallbackPresence;
      renderPresenceBanner();
      button.textContent = 'Finding your rally…';
      const result = await startInstantRally(null, {
        presenceConfirmed: true,
        expectedCourtId: selected.id,
        fromModal: modal,
        confirmationButton: button,
        confirmationOriginalHtml: original,
        onError: (error, retrySafely) => {
          showError(retrySafely
            ? 'We couldn’t confirm the rally. Your check-in is saved; try again.'
            : error.message);
        },
      });
      if (!instantRallySessionMatches(callerSession)) return null;
      if (!result) {
        refreshMe().catch(() => { /* keep the confirmed local presence while offline */ });
        return null;
      }
      fetchCourtsInView();
      return result;
    } catch (error) {
      if (!instantRallySessionMatches(callerSession)) return null;
      showError(error.message);
      return null;
    } finally {
      if (button && document.body.contains(button)) {
        delete button.dataset.playNowStarting;
        if (!button.dataset.instantRallyAction) {
          button.disabled = false;
          button.removeAttribute('aria-busy');
          button.textContent = errorEl && !errorEl.classList.contains('hidden')
            ? 'Try again' : original;
        }
        modalBox?.removeAttribute('aria-busy');
      }
    }
  }

  function openPlaySoonFlow() {
    const modal = openModal(`
      ${modalHead('Play soon')}
      <p class="play-now-intro">What fits right now?</p>
      <div class="play-soon-choices" role="group" aria-label="When you can play">
        <button type="button" class="btn btn-primary btn-block" data-play-soon-choice="at-court">I’m at a court now</button>
        <button type="button" class="btn btn-secondary btn-block" data-play-soon-choice="arriving">I can be there in 5–15 minutes</button>
        <button type="button" class="btn btn-secondary btn-block" data-play-soon-choice="available">I’m free sometime this hour</button>
      </div>
      <button type="button" class="btn-link modal-close btn-block">Not now</button>
    `, { label: 'Choose when you can play' });
    modal.querySelector('[data-play-soon-choice="at-court"]').addEventListener('click', (event) => {
      if (state.presence && state.presence.checked_in) {
        startInstantRally(event.currentTarget, { fromModal: modal });
        return;
      }
      transitionModal(modal, () => openPlayNowCourtPicker());
    });
    modal.querySelector('[data-play-soon-choice="arriving"]').addEventListener('click', () => {
      transitionModal(modal, openPlaySoonArrivalChoices);
    });
    modal.querySelector('[data-play-soon-choice="available"]').addEventListener('click', () => {
      transitionModal(modal, openPlayPulseCourtPicker);
    });
    return modal;
  }

  async function openPlaySoonArrivalChoices() {
    const accountId = Number(state.me && state.me.id);
    const modal = openModal(`
      ${modalHead('Arrive in 5–15 minutes')}
      <p class="play-now-intro">Choose a live rally. You’ll pick a 5, 10, or 15 minute arrival time next.</p>
      <div id="play-soon-rallies" aria-live="polite" aria-busy="true">
        <div class="play-now-loading" role="status"><span class="spinner"></span><span>Finding nearby rallies…</span></div>
      </div>
      <button type="button" class="btn btn-secondary btn-block hidden" id="play-soon-hour-fallback">Share that I’m free this hour</button>
      <button type="button" class="btn-link modal-close btn-block">Not now</button>
    `, { label: 'Nearby rallies you can reach' });
    const results = modal.querySelector('#play-soon-rallies');
    const fallback = modal.querySelector('#play-soon-hour-fallback');
    fallback.addEventListener('click', () => transitionModal(modal, openPlayPulseCourtPicker));
    try {
      const loc = areaLatLng();
      const response = await api(`/players/looking?lat=${loc.lat}&lng=${loc.lng}&radius=25`);
      if (!document.body.contains(modal) || Number(state.me && state.me.id) !== accountId) return modal;
      const rallies = normalizeLookingRallies(response).filter((rally) => {
        const ownArrival = activeArrivalForGame(rally.gameId, rally.myArrival, rally);
        return !!ownArrival || (rally.arrivalAvailable && rally.spotsLeft > 0 && rally.onWayCount === 0);
      });
      results.setAttribute('aria-busy', 'false');
      fallback.classList.toggle('hidden', rallies.length > 0);
      results.innerHTML = rallies.length ? rallies.map((rally) => {
        const action = rallyActionState(rally);
        return `<article class="card nearby-rally-card">
          <div class="row">
            <div class="row-main"><div class="row-title">${esc(rally.courtName)}</div><div class="row-sub">${esc(rallyCountsText(rally))}</div></div>
            ${rally.distanceMiles != null ? `<span class="tag">${esc(rally.distanceMiles)} mi</span>` : ''}
          </div>
          <button type="button" class="btn btn-primary btn-block" data-play-soon-rally ${rallyDatasetAttributes(rally)}>${esc(action.label)}</button>
        </article>`;
      }).join('') : '<div class="empty-state" style="padding:14px">No nearby rally has a travel spot right now. You can still let players know you’re free this hour.</div>';
      results.querySelectorAll('[data-play-soon-rally]').forEach((button) => {
        button.addEventListener('click', () => {
          const rally = rallies.find((item) => item.gameId === Number(button.dataset.rallyGameId));
          openReadyRally(rally || rallySummaryFromDataset(button), button);
        });
      });
    } catch {
      if (!document.body.contains(modal)) return modal;
      results.setAttribute('aria-busy', 'false');
      results.innerHTML = '<div class="empty-state" style="padding:14px">Nearby rallies aren’t available right now. You can still share that you’re free this hour.</div>';
      fallback.classList.remove('hidden');
    }
    return modal;
  }

  async function openPlayNowCourtPicker({ court = null, rally = null, intent = 'play-now' } = {}) {
    const pulseIntent = intent === 'play-pulse';
    const preset = playNowCourt(court || (rally && {
      id: rally.courtId,
      name: rally.courtName,
      city: rally.courtCity,
    }), rally ? `${rally.readyCount || 1} at the court` : 'Selected court');
    let selected = preset;
    const modal = openModal(`
      ${modalHead(pulseIntent ? 'Free this hour' : 'At the court')}
      <form id="play-now-form" novalidate>
        <p class="play-now-intro">${pulseIntent
          ? 'Choose a court you could reach this hour. Nearby players can respond; this does not check you in.'
          : 'Choose the court you’re at. We’ll check you in, then join or start its live rally.'}</p>
        <div class="form-field">
          <label for="play-now-search">Court</label>
          <input type="search" id="play-now-search" placeholder="Search courts…" autocomplete="off" />
        </div>
        <div class="play-now-courts" id="play-now-courts" role="listbox" aria-label="Court choices" aria-busy="true">
          <div class="play-now-loading" role="status"><span class="spinner"></span><span>Finding current, saved, home, and nearby courts…</span></div>
        </div>
        <button type="button" class="btn btn-secondary btn-sm hidden" id="play-now-retry-courts">Try court suggestions again</button>
        <div class="play-now-selection ${preset ? '' : 'hidden'}" id="play-now-selection" role="status">
          <span aria-hidden="true">📍</span><span><b id="play-now-selected-name">${preset ? esc(preset.name) : ''}</b><small>${pulseIntent ? 'Intended destination · not a check-in' : 'I am at this court now'}</small></span>
        </div>
        <p class="play-now-privacy"><span aria-hidden="true">👀</span> ${pulseIntent
          ? 'Nearby signed-in players may see this court for the next hour. Your current location stays private.'
          : 'Nearby signed-in players may see that you’re at this court. This status expires automatically.'}</p>
        <p class="form-error hidden" id="play-now-error" role="alert" tabindex="-1"></p>
        <button type="submit" class="btn btn-primary btn-block" id="play-now-confirm" ${preset ? '' : 'disabled'}>${pulseIntent ? 'Share for this hour' : 'Find a game now'}</button>
        <button type="button" class="btn-link modal-close btn-block">Cancel</button>
      </form>
    `, { label: pulseIntent ? 'Share that you are free this hour at a court' : 'Choose the court you are at now' });
    const resultsEl = modal.querySelector('#play-now-courts');
    const searchEl = modal.querySelector('#play-now-search');
    const confirmButton = modal.querySelector('#play-now-confirm');
    const retryButton = modal.querySelector('#play-now-retry-courts');
    const errorEl = modal.querySelector('#play-now-error');
    const suggestionsById = new Map();
    let suggestionRows = [];
    let searchTimer = null;
    let searchSeq = 0;
    let suggestionLoadSeq = 0;

    const addSuggestion = (value, tag = '') => {
      const item = playNowCourt(value, tag);
      if (!item || suggestionsById.has(item.id)) return;
      suggestionsById.set(item.id, item);
      suggestionRows.push(item);
    };
    if (preset) addSuggestion(preset, preset.tag);
    if (state.presence && state.presence.checked_in) addSuggestion({
      id: state.presence.court_id,
      name: state.presence.court_name,
    }, '📍 Current check-in');
    if (state.me && state.me.home_court_id) addSuggestion({
      id: state.me.home_court_id,
      name: state.me.home_court_name || 'Home court',
    }, '🏠 Home');

    const syncSelection = () => {
      modal.querySelector('#play-now-selection').classList.toggle('hidden', !selected);
      modal.querySelector('#play-now-selected-name').textContent = selected ? selected.name : '';
      confirmButton.disabled = !selected;
      errorEl.classList.add('hidden');
      resultsEl.querySelectorAll('[data-play-now-court]').forEach((row) => {
        const active = !!selected && Number(row.dataset.playNowCourt) === selected.id;
        row.classList.toggle('selected', active);
        row.setAttribute('aria-selected', String(active));
        const pin = row.querySelector('.play-now-court-pin');
        if (pin) pin.textContent = active ? '✓' : '📍';
      });
    };
    const renderCourtRows = (items = suggestionRows) => {
      resultsEl.setAttribute('aria-busy', 'false');
      resultsEl.innerHTML = items.length ? items.map((item) => `
        <button type="button" class="play-now-court${selected && selected.id === item.id ? ' selected' : ''}"
          role="option" aria-selected="${!!selected && selected.id === item.id}" data-play-now-court="${item.id}">
          <span class="play-now-court-pin" aria-hidden="true">${selected && selected.id === item.id ? '✓' : '📍'}</span>
          <span class="row-main"><span class="row-title">${esc(item.name)}</span><span class="row-sub">${esc(item.city || 'Pickleball court')}</span></span>
          ${item.tag || item.distanceMiles != null ? `<span class="tag">${esc(item.tag || `${item.distanceMiles} mi`)}</span>` : ''}
        </button>`).join('') : '<div class="empty-state" style="padding:14px">No suggested courts yet. Search by court or city.</div>';
      resultsEl.querySelectorAll('[data-play-now-court]').forEach((row) => row.addEventListener('click', () => {
        selected = items.find((item) => item.id === Number(row.dataset.playNowCourt))
          || suggestionsById.get(Number(row.dataset.playNowCourt));
        syncSelection();
        confirmButton.focus({ preventScroll: true });
      }));
      syncSelection();
    };

    const loadSuggestions = async () => {
      const loadSeq = ++suggestionLoadSeq;
      retryButton.classList.add('hidden');
      resultsEl.setAttribute('aria-busy', 'true');
      resultsEl.innerHTML = '<div class="play-now-loading" role="status"><span class="spinner"></span><span>Finding current, saved, home, and nearby courts…</span></div>';
      const loc = areaLatLng();
      const [saved, nearby] = await Promise.all([
        api('/courts/favorites').catch(() => null),
        api(`/courts?lat=${loc.lat}&lng=${loc.lng}&radius=30&limit=8`).catch(() => null),
      ]);
      if (loadSeq !== suggestionLoadSeq || !document.body.contains(modal)) return;
      (saved && saved.items || []).forEach((item) => addSuggestion(item, '⭐ Saved'));
      (nearby && nearby.items || []).forEach((item) => addSuggestion(
        item, item.distance_miles != null ? `${item.distance_miles} mi` : 'Nearby',
      ));
      if (searchEl.value.trim().length < 2) renderCourtRows();
      if (!saved && !nearby && searchEl.value.trim().length < 2) {
        retryButton.classList.remove('hidden');
        errorEl.textContent = 'Court suggestions could not load. Search for a court or try again.';
        errorEl.classList.remove('hidden');
      }
    };

    retryButton.addEventListener('click', loadSuggestions);
    searchEl.addEventListener('input', () => {
      clearTimeout(searchTimer);
      const query = searchEl.value.trim();
      if (query.length < 2) {
        searchSeq += 1;
        renderCourtRows();
        return;
      }
      searchTimer = setTimeout(async () => {
        const seq = ++searchSeq;
        resultsEl.setAttribute('aria-busy', 'true');
        resultsEl.innerHTML = '<div class="play-now-loading" role="status"><span class="spinner"></span><span>Searching courts…</span></div>';
        let url = `/courts?q=${encodeURIComponent(query)}&limit=8`;
        if (state.userLoc) url += `&lat=${state.userLoc[0]}&lng=${state.userLoc[1]}`;
        try {
          const response = await api(url);
          if (seq !== searchSeq || !document.body.contains(modal)) return;
          const items = (response.items || []).map((item) => playNowCourt(
            item, item.distance_miles != null ? `${item.distance_miles} mi` : '',
          )).filter(Boolean);
          items.forEach((item) => {
            if (!suggestionsById.has(item.id)) suggestionsById.set(item.id, item);
          });
          renderCourtRows(items);
        } catch (error) {
          if (seq !== searchSeq || !document.body.contains(modal)) return;
          resultsEl.setAttribute('aria-busy', 'false');
          resultsEl.innerHTML = `<div class="empty-state" style="padding:14px">${esc(error.message)}<br>Change the search or try again.</div>`;
        }
      }, 250);
    });
    modal._cleanupFns.push(() => clearTimeout(searchTimer));
    modal.querySelector('#play-now-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!selected) {
        errorEl.textContent = pulseIntent
          ? 'Choose the court where you could play this hour.'
          : 'Choose the court where you are playing.';
        errorEl.classList.remove('hidden');
        errorEl.focus({ preventScroll: true });
        return;
      }
      if (pulseIntent) await declarePlayPulse(selected, modal, confirmButton, errorEl);
      else await checkInAndStartRally(selected, modal, confirmButton, errorEl);
    });
    loadSuggestions();
    return modal;
  }

  function openPlayPulseCourtPicker(options = {}) {
    return openPlayNowCourtPicker({ ...options, intent: 'play-pulse' });
  }

  function arrivalRequestIsAmbiguous(error) {
    const status = Number(error && error.status) || 0;
    return !!(error && error.isNetworkError) || status === 408 || status === 425
      || status === 429 || status >= 500;
  }

  function arrivalRallySummary(arrival) {
    if (!arrival) return null;
    return rallySummaryFromValue({
      game_id: arrival.gameId,
      court_id: arrival.courtId,
      court_name: arrival.courtName,
      court_city: arrival.courtCity,
      court_address: arrival.courtAddress,
      court_latitude: arrival.courtLatitude,
      court_longitude: arrival.courtLongitude,
      ready_count: arrival.readyCount,
      roster_count: arrival.rosterCount,
      on_the_way_count: arrival.onWayCount,
      committed_count: arrival.committedCount,
      physical_spots_left: arrival.physicalSpotsLeft,
      spots_left: arrival.spotsLeft,
      max_players: arrival.maxPlayers,
      arrival_capability: arrival.arrivalCapability,
      arrival_available: arrival.arrivalAvailable,
      my_arrival: arrival,
    });
  }

  function hydrateArrivalDirections(modal, rally) {
    const link = modal && modal.querySelector('[data-arrival-directions]');
    if (!link) return;
    const applyCourt = (court) => {
      const href = courtDirectionsUrl(court);
      if (!href || !document.body.contains(link)) return false;
      link.href = href;
      link.removeAttribute('aria-disabled');
      link.classList.remove('is-disabled');
      return true;
    };
    if (applyCourt(rallyCourtForDirections(rally))) return;
    link.setAttribute('aria-disabled', 'true');
    link.classList.add('is-disabled');
    const callerSession = instantRallySession();
    if (!callerSession) return;
    api(`/courts/${rally.courtId}`).then((court) => {
      if (!instantRallySessionMatches(callerSession) || !document.body.contains(modal)) return;
      applyCourt(court);
    }).catch(() => { /* the court detail remains available if Maps cannot be resolved */ });
  }

  async function reserveRallyArrival(rally, etaMinutes) {
    const callerSession = instantRallySession();
    const gameId = safePositiveId(rally && rally.gameId);
    if (!callerSession || !gameId) {
      const error = new Error('Sign in again before holding a rally spot.');
      error.code = 'invalid_session';
      throw error;
    }
    const knownArrival = activeArrivalForGame(gameId, rally.myArrival, rally);
    if (knownArrival) {
      clearRallyArrivalAttempt(callerSession.userId, gameId);
      return { outcome: 'existing', arrival: knownArrival };
    }
    const attempt = pendingRallyArrivalAttempt(callerSession.userId, gameId, etaMinutes);
    if (!attempt) {
      const error = new Error('Nothing was sent because this browser could not save your request.');
      error.code = 'arrival_attempt_unavailable';
      throw error;
    }
    const shared = rallyArrivalInFlight;
    if (shared && shared.token === callerSession.token && shared.userId === callerSession.userId
        && shared.gameId === gameId && shared.attemptId === attempt.id) return shared.promise;
    const body = {
      eta_minutes: attempt.etaMinutes,
      client_attempt_id: attempt.id,
    };
    if (rally.arrivalCapability) body.arrival_capability = rally.arrivalCapability;
    const promise = (async () => {
      try {
        const result = await api(`/games/${gameId}/arrival`, {
          method: 'PUT',
          body: JSON.stringify(body),
        });
        if (!instantRallySessionMatches(callerSession)) return { abandoned: true };
        clearRallyArrivalAttempt(callerSession.userId, gameId, attempt.id);
        const freshRally = rallySummaryFromValue(result && result.game) || rally;
        const arrival = normalizeActiveArrival(result && result.arrival, freshRally);
        if (!arrival) {
          const ended = new Error('That saved arrival has already ended. Refresh nearby rallies and try again.');
          ended.code = 'arrival_no_longer_active';
          ended.status = 409;
          throw ended;
        }
        invalidateMeRequests();
        state.activeArrival = arrival;
        refreshPlayGamesAfterRallyMutation();
        renderActiveGameBanner();
        refreshLookingBanner();
        refreshMe().catch(() => {});
        return { ...result, arrival };
      } catch (error) {
        if (instantRallySessionMatches(callerSession) && !arrivalRequestIsAmbiguous(error)) {
          clearRallyArrivalAttempt(callerSession.userId, gameId, attempt.id);
        }
        throw error;
      }
    })();
    const record = {
      token: callerSession.token,
      userId: callerSession.userId,
      gameId,
      attemptId: attempt.id,
      promise,
    };
    rallyArrivalInFlight = record;
    try { return await promise; }
    finally { if (rallyArrivalInFlight === record) rallyArrivalInFlight = null; }
  }

  function openRallyArrivalSheet(value, { fromModal = null } = {}) {
    const rally = value && value.courtId ? value : rallySummaryFromValue(value);
    if (!rally || !rally.gameId) {
      toast('This rally needs a fresh nearby update before it can hold a spot.');
      refreshLookingBanner();
      return null;
    }
    const existingArrival = activeArrivalForGame(rally.gameId, rally.myArrival, rally);
    if (existingArrival) return openArrivalDetails(existingArrival);
    if (!rally.arrivalAvailable || !rally.arrivalCapability) {
      toast('This rally is wrapping up, so travel spots are closed. Refresh nearby rallies for the next one.');
      refreshLookingBanner();
      return null;
    }
    if (rally.spotsLeft <= 0 || rally.onWayCount > 0) {
      toast(rally.onWayCount > 0
        ? 'Another player is already arriving, so that travel spot is held.'
        : 'That rally is fully committed.');
      return null;
    }
    const callerSession = instantRallySession();
    if (!callerSession) return null;
    const pending = readRallyArrivalAttempt(callerSession.userId, rally.gameId);
    const initialEta = pending ? pending.etaMinutes : 10;
    const modal = openModal(`
      ${modalHead(`🚗 Head to ${rally.courtName}`)}
      <div class="arrival-summary" aria-label="${esc(rallyCountsText(rally))}">
        <span><b>${rally.readyCount}/${rally.maxPlayers}</b><small>at the court</small></span>
        <span><b>${rally.onWayCount}</b><small>arriving</small></span>
        <span><b>${rally.spotsLeft}</b><small>spot${rally.spotsLeft === 1 ? '' : 's'} left</small></span>
      </div>
      <form id="arrival-eta-form" novalidate>
        <fieldset class="arrival-eta-fieldset">
          <legend>When can you arrive?</legend>
          <div class="arrival-eta-options">
            ${[5, 10, 15].map((eta) => `
              <label class="arrival-eta-option">
                <input type="radio" name="arrival-eta" value="${eta}" ${eta === initialEta ? 'checked' : ''} ${pending && eta !== initialEta ? 'disabled' : ''}>
                <span>${eta}<small>min</small></span>
              </label>`).join('')}
          </div>
        </fieldset>
        <p class="arrival-hold-explainer">We’ll hold one travel spot while you head over. It ends if the rally closes first.</p>
        ${pending ? `<p class="arrival-retry-note" role="status">We still need to confirm your ${pending.etaMinutes}-minute arrival.</p>` : ''}
        <p class="form-error hidden" id="arrival-error" role="alert" tabindex="-1"></p>
        <button type="submit" class="btn btn-primary btn-block" id="arrival-confirm">Hold my spot · ${initialEta} min</button>
        <a class="btn btn-secondary btn-block arrival-directions" data-arrival-directions target="_blank" rel="noopener" aria-label="Directions to ${esc(rally.courtName)} (opens Maps)">Directions</a>
        <button type="button" class="btn-link modal-close btn-block">Not now</button>
      </form>
    `, { label: `Arrival time for ${rally.courtName}` });
    hydrateArrivalDirections(modal, rally);
    const form = modal.querySelector('#arrival-eta-form');
    const button = modal.querySelector('#arrival-confirm');
    const errorEl = modal.querySelector('#arrival-error');
    const syncEta = () => {
      const eta = Number(form.elements['arrival-eta'].value) || initialEta;
      button.textContent = pending ? `Try again · ${eta} min` : `Hold my spot · ${eta} min`;
    };
    form.addEventListener('change', syncEta);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (button.disabled) return;
      const eta = Number(form.elements['arrival-eta'].value);
      if (![5, 10, 15].includes(eta)) {
        errorEl.textContent = ERROR_TEXT.invalid_eta_minutes;
        errorEl.classList.remove('hidden');
        errorEl.focus({ preventScroll: true });
        return;
      }
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.textContent = pending ? 'Checking your spot…' : 'Holding your spot…';
      errorEl.classList.add('hidden');
      try {
        const result = await reserveRallyArrival(rally, eta);
        if (!result || result.abandoned || !document.body.contains(modal)) return;
        toast(result.outcome === 'existing' ? 'Your held spot is confirmed' : 'Spot held — head to the court 🚗');
        refreshMe().catch(() => {});
        transitionModal(modal, () => openArrivalDetails(result.arrival));
      } catch (error) {
        if (!instantRallySessionMatches(callerSession) || !document.body.contains(errorEl)) return;
        if (error.code === 'already_joined') {
          clearArrivalAfterConfirmedMembership(callerSession, rally.gameId);
          transitionModal(modal, () => openGameScreen(rally.gameId));
          refreshMe().catch(() => {});
          return;
        }
        if (['already_at_court', 'active_checkin_elsewhere'].includes(error.code)) {
          // Presence errors are authoritative even if the follow-up /me read
          // is offline. Keep routing the player to an explicit, private
          // court-confirmation path instead of escaping this submit handler.
          await refreshMe().catch(() => false);
          if (!instantRallySessionMatches(callerSession) || !document.body.contains(modal)) return;
          let atTargetCourt = isCheckedInAtCourt(rally.courtId);
          if (error.code === 'already_at_court' && !atTargetCourt) {
            // The failed reservation is itself an authoritative same-court
            // presence check. Keep it private while handing off to direct join.
            invalidateMeRequests();
            state.presence = {
              ...(state.presence || {}),
              checked_in: true,
              court_id: rally.courtId,
              court_name: rally.courtName,
              looking_for_game: false,
            };
            renderPresenceBanner();
            atTargetCourt = true;
          }
          if (atTargetCourt) {
            transitionModal(modal, () => openAtCourtRallyJoinSheet(rally));
          } else {
            transitionModal(modal, () => openArrivalCheckInConfirmation(rally));
          }
          return;
        }
        if (['active_arrival_elsewhere', 'arrival_already_active'].includes(error.code)) {
          await refreshMe().catch(() => {});
          if (state.activeArrival && document.body.contains(modal)) {
            transitionModal(modal, () => openArrivalDetails(state.activeArrival));
            return;
          }
        }
        if (['rally_no_longer_active', 'rally_full', 'arrival_slot_taken', 'game_not_found'].includes(error.code)) {
          state.playGamesCache = null;
          refreshLookingBanner();
        }
        const ambiguous = arrivalRequestIsAmbiguous(error);
        errorEl.textContent = ambiguous
          ? 'We couldn’t confirm your spot. Try again.'
          : error.message;
        errorEl.classList.remove('hidden');
        errorEl.focus({ preventScroll: true });
        const saved = readRallyArrivalAttempt(callerSession.userId, rally.gameId);
        if (saved) {
          form.querySelectorAll('input[name="arrival-eta"]').forEach((input) => {
            input.checked = Number(input.value) === saved.etaMinutes;
            input.disabled = Number(input.value) !== saved.etaMinutes;
          });
        }
      } finally {
        if (document.body.contains(button)) {
          button.disabled = false;
          button.removeAttribute('aria-busy');
          syncEta();
        }
      }
    });
    return modal;
  }

  async function cancelRallyArrival(arrival, modal, button, errorEl) {
    const callerSession = instantRallySession();
    if (!callerSession || !arrival || button.disabled) return;
    const original = button.textContent;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.textContent = 'Cancelling…';
    errorEl?.classList.add('hidden');
    try {
      await api(`/games/${arrival.gameId}/arrival`, { method: 'DELETE' });
      if (!instantRallySessionMatches(callerSession)) return;
      clearRallyArrivalAttempt(callerSession.userId, arrival.gameId);
      invalidateMeRequests();
      state.activeArrival = null;
      refreshPlayGamesAfterRallyMutation();
      renderActiveGameBanner();
      refreshLookingBanner();
      closeModal(modal);
      toast('Your held spot was released');
      refreshMe().catch(() => {});
    } catch (error) {
      if (!instantRallySessionMatches(callerSession) || !document.body.contains(button)) return;
      if (errorEl) {
        errorEl.textContent = error.message;
        errorEl.classList.remove('hidden');
        errorEl.focus({ preventScroll: true });
      } else toast(error.message);
      button.disabled = false;
      button.removeAttribute('aria-busy');
      button.textContent = original;
    }
  }

  function openArrivalDetails(value) {
    const arrival = normalizeActiveArrival(value);
    if (!arrival) {
      state.activeArrival = null;
      renderActiveGameBanner();
      toast('That held spot has ended. Refresh nearby rallies to try again.');
      refreshMe().catch(() => {});
      return null;
    }
    const rally = arrivalRallySummary(arrival);
    const modal = openModal(`
      ${modalHead('🚗 Your trip to the rally')}
      <div class="arrival-held-card" role="status">
        <span class="arrival-held-icon" aria-hidden="true">✓</span>
        <div><b>Spot held at ${esc(arrival.courtName)}</b><span>${esc(arrivalEtaLabel(arrival))}</span></div>
      </div>
      <p class="arrival-reservation-copy">${esc(arrivalReservationCopy(arrival))}</p>
      <div class="arrival-summary" aria-label="${esc(rallyCountsText(rally))}">
        <span><b>${arrival.readyCount}/${arrival.maxPlayers}</b><small>at the court</small></span>
        <span><b>${arrival.onWayCount}</b><small>arriving</small></span>
        <span><b>${arrival.physicalSpotsLeft}</b><small>spot${arrival.physicalSpotsLeft === 1 ? '' : 's'} left</small></span>
      </div>
      <a class="btn btn-secondary btn-block arrival-directions" data-arrival-directions target="_blank" rel="noopener" aria-label="Directions to ${esc(arrival.courtName)} (opens Maps)">Directions</a>
      <button type="button" class="btn btn-primary btn-block" id="arrival-im-here">I’m at the court</button>
      <button type="button" class="btn btn-danger btn-block" id="arrival-cancel">Cancel trip</button>
      <p class="form-error hidden" id="arrival-detail-error" role="alert" tabindex="-1"></p>
      <button type="button" class="btn-link modal-close btn-block">Close</button>
    `, { label: `Held spot at ${arrival.courtName}` });
    hydrateArrivalDirections(modal, rally);
    modal.querySelector('#arrival-im-here').addEventListener('click', () => {
      transitionModal(modal, () => openArrivalCheckInConfirmation(arrival));
    });
    modal.querySelector('#arrival-cancel').addEventListener('click', (event) => {
      cancelRallyArrival(arrival, modal, event.currentTarget, modal.querySelector('#arrival-detail-error'));
    });
    return modal;
  }

  function openAtCourtRallyJoinSheet(rally) {
    if (!rally || !rally.courtId) return null;
    const modal = openModal(`
      ${modalHead(`📍 You’re at ${rally.courtName}`)}
      <p class="arrival-checkin-copy">Your court check-in is private. Join the rally to show its players that you’re at the court.</p>
      <p class="form-error hidden" id="at-court-join-error" role="alert" tabindex="-1"></p>
      <button type="button" class="btn btn-primary btn-block" id="at-court-join">Join this game</button>
      <button type="button" class="btn btn-secondary btn-block modal-close">Not now</button>
    `, { label: `Join the rally at ${rally.courtName}` });
    const button = modal.querySelector('#at-court-join');
    const errorEl = modal.querySelector('#at-court-join-error');
    button.addEventListener('click', async () => {
      if (button.disabled) return;
      const callerSession = instantRallySession();
      if (!callerSession) return;
      errorEl.classList.add('hidden');
      const result = await openReadyRally(rally, button);
      if (!instantRallySessionMatches(callerSession) || !document.body.contains(errorEl)) return;
      if (!result) {
        errorEl.textContent = 'We could not confirm the join. Your private court check-in is saved; tap Join this game again.';
        errorEl.classList.remove('hidden');
        errorEl.focus({ preventScroll: true });
      }
    });
    return modal;
  }

  function openArrivalCheckInConfirmation(value) {
    const arrival = normalizeActiveArrival(value);
    const resemblesArrival = !!(value && (value.expires_at || value.expiresAt));
    if (!arrival && resemblesArrival) return openArrivalDetails(value);
    const rally = arrival ? arrivalRallySummary(arrival)
      : (value && value.courtId ? value : rallySummaryFromValue(value));
    if (!rally) {
      toast('Refresh nearby rallies before confirming this court.');
      return null;
    }
    const modal = openModal(`
      ${modalHead(`📍 Are you at ${rally.courtName}?`)}
      <p class="arrival-checkin-copy">Only continue once you’re at this court. This checks you in privately, then adds you to the current rally.</p>
      <p class="form-error hidden" id="arrival-checkin-error" role="alert" tabindex="-1"></p>
      <button type="button" class="btn btn-primary btn-block" id="arrival-checkin-confirm">Check in &amp; join</button>
      <button type="button" class="btn btn-secondary btn-block modal-close">Not there yet</button>
    `, { label: `Confirm arrival at ${rally.courtName}` });
    const button = modal.querySelector('#arrival-checkin-confirm');
    const errorEl = modal.querySelector('#arrival-checkin-error');
    button.addEventListener('click', async () => {
      if (button.disabled) return;
      const callerSession = instantRallySession();
      if (!callerSession) return;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.textContent = 'Checking you in…';
      errorEl.classList.add('hidden');
      try {
        const checkedIn = await api(`/courts/${rally.courtId}/checkin`, {
          method: 'POST',
          body: JSON.stringify({ looking_for_game: false }),
        });
        if (!instantRallySessionMatches(callerSession)) return;
        invalidateMeRequests();
        state.presence = checkedIn && checkedIn.presence ? checkedIn.presence : {
          ...(state.presence || {}),
          checked_in: true,
          court_id: rally.courtId,
          court_name: rally.courtName,
          looking_for_game: false,
        };
        renderPresenceBanner();
        button.disabled = false;
        button.removeAttribute('aria-busy');
        button.textContent = 'Joining your rally…';
        const joined = await openReadyRally(rally, button);
        if (!instantRallySessionMatches(callerSession)) return;
        refreshMe().catch(() => {});
        fetchCourtsInView();
        return joined;
      } catch (error) {
        if (!instantRallySessionMatches(callerSession) || !document.body.contains(errorEl)) return;
        errorEl.textContent = error.message;
        errorEl.classList.remove('hidden');
        errorEl.focus({ preventScroll: true });
      } finally {
        if (document.body.contains(button) && !button.dataset.joiningRally) {
          button.disabled = false;
          button.removeAttribute('aria-busy');
          button.textContent = 'Check in & join';
        }
      }
    });
    return modal;
  }

  function refreshPlayGamesAfterRallyMutation() {
    state.playGamesCache = null;
    if (state.tab === 'play' && state.playSeg === 'games') renderPlay();
  }

  function clearArrivalAfterConfirmedMembership(callerSession, arrivalGameId) {
    if (!instantRallySessionMatches(callerSession)) return;
    const gameId = safePositiveId(arrivalGameId);
    invalidateMeRequests();
    if (gameId) clearRallyArrivalAttempt(callerSession.userId, gameId);
    if (!state.activeArrival || !gameId || state.activeArrival.gameId === gameId) {
      state.activeArrival = null;
      renderActiveGameBanner();
    }
    refreshPlayGamesAfterRallyMutation();
  }

  async function recoverRallyAfterConfirmedArrival(
    button, options, callerSession, arrivalGameId,
  ) {
    const result = await startInstantRally(button, options);
    if (!instantRallySessionMatches(callerSession)) return null;
    if (safePositiveId(result && result.game && result.game.id)) {
      clearArrivalAfterConfirmedMembership(callerSession, arrivalGameId);
    }
    return result;
  }

  async function openReadyRally(rally, button = null) {
    const summary = rally && rally.courtId ? rally : rallySummaryFromValue(rally);
    const courtId = safePositiveId(summary && summary.courtId);
    const sourceModal = button && button.closest('.modal-backdrop');
    if (!courtId) {
      state.chatSeg = 'friends';
      state.peopleMode = 'nearby';
      switchTab('chat');
      return null;
    }
    const ownArrival = activeArrivalForGame(summary.gameId, summary.myArrival, summary);
    if (!isCheckedInAtCourt(courtId)) {
      const openConfirmation = () => ownArrival
        ? openArrivalDetails(ownArrival)
        : openRallyArrivalSheet(summary);
      if (sourceModal && currentOverlayEntry()?.el === sourceModal) {
        transitionModal(sourceModal, openConfirmation);
        return null;
      }
      return openConfirmation();
    }
    if (summary.spotsLeft <= 0 && !ownArrival) {
      toast(summary.onWayCount > 0
        ? 'That rally is committed — finding the next rally at this court.'
        : 'That rally is full — finding the next rally at this court.');
      return startInstantRally(button, {
        fromModal: sourceModal || null,
        expectedCourtId: courtId,
      });
    }
    const gameId = safePositiveId(summary.gameId);
    const callerSession = instantRallySession();
    if (!callerSession) return null;
    if (!gameId) return startInstantRally(button, {
      fromModal: sourceModal || null,
      expectedCourtId: courtId,
    });
    if (button?.dataset.joiningRally === 'true') return null;
    const original = button?.innerHTML;
    const originalAriaLabel = button?.getAttribute('aria-label');
    if (button) {
      button.dataset.joiningRally = 'true';
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.textContent = 'Joining…';
    }
    let keepConfirmation = false;
    try {
      await api(`/games/${gameId}/join`, { method: 'POST' });
      if (!instantRallySessionMatches(callerSession)) return null;
      clearArrivalAfterConfirmedMembership(callerSession, gameId);
      toast("You're in the rally! 🏓");
      refreshMe().catch(() => {});
      if (button && document.body.contains(button)) {
        keepConfirmation = true;
        button._rallyJoinConfirmationCleanup?.();
        let confirmationTimer = null;
        const cleanup = () => {
          clearTimeout(confirmationTimer);
          confirmationTimer = null;
          button.removeEventListener('click', undoJoin, true);
          delete button.dataset.rallyJoinUndo;
          delete button._rallyJoinConfirmationCleanup;
        };
        const openJoinedGame = () => {
          cleanup();
          delete button.dataset.joiningRally;
          openResolvedRallyGame(gameId, sourceModal || null);
        };
        const restoreJoinControl = () => {
          cleanup();
          delete button.dataset.joiningRally;
          if (!document.body.contains(button)) return;
          button.disabled = false;
          button.removeAttribute('aria-busy');
          if (originalAriaLabel == null) button.removeAttribute('aria-label');
          else button.setAttribute('aria-label', originalAriaLabel);
          button.innerHTML = original;
        };
        async function undoJoin(event) {
          if (button.dataset.rallyJoinUndo !== 'true') return;
          event.preventDefault();
          event.stopImmediatePropagation();
          clearTimeout(confirmationTimer);
          confirmationTimer = null;
          button.dataset.rallyJoinUndo = 'pending';
          button.disabled = true;
          button.textContent = 'Undoing…';
          try {
            await api(`/games/${gameId}/leave`, { method: 'POST' });
            if (!instantRallySessionMatches(callerSession)) { cleanup(); return; }
            cleanup();
            button.textContent = 'Left game ✓';
            toast('Join undone');
            refreshMe().catch(() => {});
            setTimeout(restoreJoinControl, 800);
          } catch (error) {
            if (!instantRallySessionMatches(callerSession)) { cleanup(); return; }
            button.dataset.rallyJoinUndo = 'true';
            button.disabled = false;
            button.textContent = 'Joined ✓ · Undo';
            confirmationTimer = setTimeout(openJoinedGame, 4000);
            toast(error.message);
          }
        }
        button.disabled = false;
        button.removeAttribute('aria-busy');
        button.dataset.rallyJoinUndo = 'true';
        button.textContent = 'Joined ✓ · Undo';
        button.setAttribute('aria-label', 'Joined. Undo joining this rally');
        button.addEventListener('click', undoJoin, true);
        button._rallyJoinConfirmationCleanup = cleanup;
        confirmationTimer = setTimeout(openJoinedGame, 4000);
      } else {
        openResolvedRallyGame(gameId, sourceModal || null);
      }
      return gameId;
    } catch (error) {
      if (!instantRallySessionMatches(callerSession)) return null;
      const staleCodes = ['game_full', 'game_not_open', 'game_not_found', 'rally_no_longer_active'];
      if (staleCodes.includes(error.code)) {
        toast('That rally just changed — finding the current one');
        return recoverRallyAfterConfirmedArrival(button, {
          fromModal: sourceModal || null,
          expectedCourtId: courtId,
        }, callerSession, gameId);
      }
      if (error.code === 'already_joined') {
        clearArrivalAfterConfirmedMembership(callerSession, gameId);
        openResolvedRallyGame(gameId, sourceModal || null);
        return gameId;
      }
      // A direct join only owns another game when the server explicitly says
      // this player already has an active rally. Do not open arbitrary IDs
      // embedded in stale/privacy errors.
      const recoveredGame = error.code === 'active_rally_elsewhere'
        ? authoritativeRallyGame(error) : null;
      if (recoveredGame) {
        clearInstantRallyAttempt();
        clearArrivalAfterConfirmedMembership(callerSession, gameId);
        toast('You already have a rally in progress — opening it now');
        openResolvedRallyGame(recoveredGame.id, sourceModal || null);
        return recoveredGame.id;
      }
      if (['active_checkin_required', 'active_checkin_court_mismatch'].includes(error.code)) {
        const openConfirmation = () => {
          if (!instantRallySessionMatches(callerSession)) return;
          if (sourceModal) {
            if (!document.body.contains(sourceModal)
                || currentOverlayEntry()?.el !== sourceModal) return;
            transitionModal(sourceModal, () => openPlayNowCourtPicker());
          } else {
            openPlayNowCourtPicker();
          }
        };
        refreshMe().finally(openConfirmation);
        return null;
      }
      toast(error.message);
      return null;
    } finally {
      if (!keepConfirmation && button && document.body.contains(button)) {
        delete button.dataset.joiningRally;
        button.disabled = false;
        button.removeAttribute('aria-busy');
        button.innerHTML = original;
      }
    }
  }

  function openCheckInSheet(court) {
    const modal = openModal(`
      <div class="checkin-sheet">
        <div class="celebrate-emoji" style="font-size:46px">📍</div>
        <h3 style="margin:6px 0 2px">I’m at ${esc(court.name)}</h3>
        <p class="row-sub" style="margin-bottom:10px">What do you want to do?</p>
        <button class="btn btn-primary btn-block" id="ci-lfg" style="margin-bottom:10px;padding:16px">
          <svg class="pb-ic"><use href="#pb"/></svg> Find a game now
        </button>
        <button class="btn btn-secondary btn-block" id="ci-play" style="padding:16px">
          👍 Just check in
        </button>
        <p class="play-now-privacy"><span aria-hidden="true">👀</span> If you’re ready, signed-in players nearby may see that fresh status until it expires automatically.</p>
        <p class="form-error hidden" id="ci-error" role="alert" tabindex="-1"></p>
        <button class="btn-link modal-close btn-block" style="margin-top:8px">Cancel</button>
      </div>
    `);
    const errorEl = modal.querySelector('#ci-error');
    const doGroupCheckIn = async (button) => {
      if (button.dataset.submitting === 'true') return;
      const original = button.textContent;
      button.dataset.submitting = 'true';
      button.disabled = true;
      button.textContent = 'Checking in…';
      errorEl.classList.add('hidden');
      try {
        await api(`/courts/${court.id}/checkin`, {
          method: 'POST',
          body: JSON.stringify({ looking_for_game: false }),
        });
        const followupLoad = beginFollowupAfterClosingModal(modal);
        toast(`Checked in at ${court.name}`);
        await refreshMe();
        fetchCourtsInView();
        if (followupLoad && routedOverlayLoadIsCurrent(followupLoad)) {
          maybeAskHours(court);
        }
      } catch (error) {
        errorEl.textContent = error.message;
        errorEl.classList.remove('hidden');
        errorEl.focus({ preventScroll: true });
        delete button.dataset.submitting;
        button.disabled = false;
        button.textContent = original;
      }
    };
    modal.querySelector('#ci-lfg').addEventListener('click', (event) => {
      checkInAndStartRally(court, modal, event.currentTarget, errorEl);
    });
    modal.querySelector('#ci-play').addEventListener('click', (event) => doGroupCheckIn(event.currentTarget));
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
    const assembly = instantRallyAssembly(game);
    const typeTag = game.is_instant
      ? `<span class="tag${assembly ? ' live' : ''}" style="margin:0 0 0 8px">⚡ Rally</span>`
      : game.game_type === 'ranked'
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

    if (game.status === 'upcoming' && assembly) {
      cardStyle = 'border:2px solid var(--green-600)';
      banner = `<div class="status-banner rally-banner">${assembly.banner}</div>`;
      if (!game.is_joined) {
        const rally = rallySummaryFromValue(game);
        const rallyAction = rallyActionState(rally);
        action = rallyAction.enabled
          ? `<button class="btn btn-primary btn-sm" data-game-join="${game.id}" data-instant-rally="true" ${rallyDatasetAttributes(rally)}>${esc(rallyAction.label)}</button>`
          : `<span class="tag warn" style="margin:0">${esc(rallyAction.label)}</span>`;
      }
    } else if (instantRallyScorePending(game)) {
      banner = '<div class="status-banner">📝 Played? Tap to enter the score.</div>';
      if (game.is_creator) {
        action = `<button class="btn btn-secondary btn-sm" data-game-dismiss="${game.id}">Didn't happen</button>`;
      }
    } else if (instantRallyClosed(game)) {
      banner = '<div class="status-banner">😴 This rally ended without enough players.</div>';
    } else if (game.status === 'upcoming') {
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
        action = `<button class="btn btn-secondary btn-sm" data-game-waitlist-manage="${game.id}">Waitlisted #${game.waitlist_position}</button>`;
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
                  <button class="btn btn-danger btn-sm" data-game-dispute="${game.id}" aria-label="Dispute this score">✕</button>`;
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
            <div class="row-title">${game.is_instant && game.status === 'upcoming' ? (assembly ? 'Right now' : 'Played recently') : esc(game.recurrence === 'weekly' && game.status === 'upcoming'
              ? `${new Date(game.scheduled_at).toLocaleDateString([], { weekday: 'long' })}s · ${new Date(game.scheduled_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
              : fmtDateTime(game.scheduled_at))}${typeTag}${visTag}${recurTag}${clubTag}${levelTag}${chatTag}</div>
            <div class="row-sub">${esc(court.name || '')}${!compact && court.city ? ` · ${esc(court.city)}` : ''}${game.distance_miles != null ? ` · ${game.distance_miles} mi` : ''}${hostLabel}</div>
          </div>
          <span class="chev">›</span>
        </div>
        ${banner}
        ${game.notes && !(game.is_instant && game.notes === '⚡ Instant rally') ? `<div class="row-sub" style="margin-bottom:8px">“${esc(game.notes)}”</div>` : ''}
        <div class="row">
          <div class="avatar-stack">${avatars}</div>
          <span class="row-sub">${assembly ? esc(rallyCountsText(assembly)) : `${game.players.length}/${game.max_players} players${game.spots_left && game.status === 'upcoming' ? ` · ${game.spots_left} spot${game.spots_left === 1 ? '' : 's'} left` : ''}`}${(() => { const n = game.status === 'upcoming' ? game.players.filter((p) => p.attending).length : 0; return n ? ` · 👋 ${n} coming` : ''; })()}</span>
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
      if (b.dataset.instantRally === 'true') {
        await openReadyRally(rallySummaryFromDataset(b), b);
        return;
      }
      if (b.disabled) return;
      if (b.dataset.undoJoin === 'true') {
        clearTimeout(b._confirmationTimer);
        b.disabled = true;
        b.textContent = 'Undoing…';
        try {
          await api(`/games/${b.dataset.gameJoin}/leave`, { method: 'POST' });
          b.textContent = 'Left game ✓';
          toast('Join undone');
          refreshMe();
          setTimeout(refresh, 450);
        } catch (err) {
          b.disabled = false;
          b.textContent = 'Joined ✓ · Undo';
          toast(err.message);
        }
        return;
      }
      const original = b.textContent;
      b.disabled = true;
      b.setAttribute('aria-busy', 'true');
      b.textContent = 'Joining…';
      try {
        await api(`/games/${b.dataset.gameJoin}/join`, { method: 'POST' });
        b.removeAttribute('aria-busy');
        b.dataset.undoJoin = 'true';
        b.disabled = false;
        b.textContent = 'Joined ✓ · Undo';
        b.setAttribute('aria-label', 'Joined. Undo joining this game');
        toast('Joined 🏓');
        refreshMe();
        b._confirmationTimer = setTimeout(refresh, 4000);
      } catch (err) {
        b.disabled = false;
        b.removeAttribute('aria-busy');
        b.textContent = original;
        toast(err.message);
      }
    }));
    rootEl.querySelectorAll('[data-game-waitlist]').forEach((b) => b.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (b.disabled) return;
      if (b.dataset.undoWaitlist === 'true') {
        clearTimeout(b._confirmationTimer);
        b.disabled = true;
        b.textContent = 'Leaving…';
        try {
          await api(`/games/${b.dataset.gameWaitlist}/waitlist/leave`, { method: 'POST' });
          b.textContent = 'Left waitlist ✓';
          toast('Left the waitlist');
          setTimeout(refresh, 450);
        } catch (err) {
          b.disabled = false;
          b.textContent = b.dataset.confirmationLabel || 'Waitlisted · Leave';
          toast(err.message);
        }
        return;
      }
      const original = b.textContent;
      b.disabled = true;
      b.setAttribute('aria-busy', 'true');
      b.textContent = 'Joining waitlist…';
      try {
        const updated = await api(`/games/${b.dataset.gameWaitlist}/waitlist`, { method: 'POST' });
        b.removeAttribute('aria-busy');
        const position = Number(updated.waitlist_position) || null;
        b.dataset.undoWaitlist = 'true';
        b.dataset.confirmationLabel = position ? `Waitlisted #${position} · Leave` : 'Waitlisted · Leave';
        b.disabled = false;
        b.textContent = b.dataset.confirmationLabel;
        b.setAttribute('aria-label', `${b.dataset.confirmationLabel}. Leave the waitlist`);
        toast("Waitlisted — we'll let you know if a spot opens ⏳");
        b._confirmationTimer = setTimeout(refresh, 4000);
      } catch (err) {
        b.disabled = false;
        b.removeAttribute('aria-busy');
        b.textContent = original;
        toast(err.message);
      }
    }));
    rootEl.querySelectorAll('[data-game-waitlist-manage]').forEach((b) => b.addEventListener('click', (e) => {
      e.stopPropagation();
      openGameScreen(Number(b.dataset.gameWaitlistManage));
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
    if (instantRallyScorePending(game)) {
      return `We played a pickleball rally${courtName ? ` at ${courtName}` : ''} — score coming soon on Third Shot`;
    }
    if (instantRallyClosed(game)) {
      return `That pickleball rally${courtName ? ` at ${courtName}` : ''} has ended — start a fresh one on Third Shot`;
    }
    if (game.is_instant) {
      return `Join our live pickleball rally${courtName ? ` at ${courtName}` : ''} — we're finding players now`;
    }
    return `Join my pickleball game${courtName ? ` at ${courtName}` : ''} — ${fmtDateTime(game.scheduled_at)}`;
  }

  async function shareGame(game, { notify = true } = {}) {
    const url = `${location.origin}/g/${game.id}`; // short link → OG preview in chat apps
    const text = gameShareText(game);
    try {
      if (navigator.share) {
        await navigator.share({ title: 'Third Shot', text, url });
        return 'shared';
      }
      await navigator.clipboard.writeText(`${text} ${url}`);
      if (notify) toast('Copied to share 📋');
      return 'copied';
    } catch (error) {
      if (error && error.name === 'AbortError') return 'cancelled';
      if (notify) toast('Couldn’t open sharing — try again');
      return 'failed';
    }
  }

  function rosterBoostSummaryHtml(game) {
    const players = Array.isArray(game.players) ? game.players : [];
    const spotsLeft = Math.max(0, Number(game.spots_left) || 0);
    const full = spotsLeft === 0;
    const roster = players.slice(0, 6).map((player) => `
      <span class="roster-boost-person" title="${esc(player.display_name)}">
        ${avatarHtml(player, 'sm')}<span>${esc((player.display_name || 'Player').split(' ')[0])}</span>
      </span>`).join('');
    return `
      <div class="roster-boost-summary${full ? ' is-full' : ''}">
        <div class="roster-boost-count"><b>${full ? 'Roster full' : spotsLeft === 1
          ? '1 spot left. The first person to join gets it.'
          : `${spotsLeft} spots left. The next ${spotsLeft} players to join get them.`}</b>
          <span>${players.length}/${game.max_players} players · ${esc(fmtDateTime(game.scheduled_at))}</span>
        </div>
        <div class="roster-boost-people">${roster}</div>
      </div>`;
  }

  function gameOpenCallCanBeCreated(game) {
    if (!game || !game.is_creator || game.status !== 'upcoming'
        || game.visibility !== 'open' || game.is_instant
        || game.recurrence !== 'none' || !game.court
        || Number(game.spots_left) <= 0) return false;
    const startsAt = new Date(game.scheduled_at).getTime();
    return Number.isFinite(startsAt) && startsAt >= Date.now() - 15 * 60 * 1000;
  }

  function openRosterBoostSheet(initialGame, { onGameUpdated } = {}) {
    const accountId = Number(state.me && state.me.id);
    let game = initialGame;
    let openCall = game.open_call || null;
    let friends = [];
    let friendsReady = false;
    let friendsError = false;
    let busy = false;
    let inviteReceipt = null;
    let postReceipt = null;
    let shareReceipt = null;
    const selected = new Set();
    const sentInviteIds = new Set();

    const sheet = openModal(`
      ${modalHead('Find players')}
      <div class="roster-boost">
        <div id="rb-summary"></div>
        ${game.is_instant ? `<p class="arrival-retry-note">${esc(rallyCountsText(rallySummaryFromValue(game)))}. A player who is arriving keeps the travel spot shown above.</p>` : ''}
        <div id="rb-primary-channel"></div>
        <details class="roster-boost-more hidden" id="rb-more-channels">
          <summary>More ways to share</summary>
          <div class="roster-boost-more-body" id="rb-more-channels-body"></div>
        </details>
        <section class="roster-boost-channel" id="rb-friends-channel" aria-labelledby="rb-friends-title">
          <div class="roster-boost-channel-head">
            <span class="roster-boost-channel-icon">👥</span>
            <div><b id="rb-friends-title">Invite friends</b><span>Pick several and send once.</span></div>
          </div>
          <div class="invite-chips" id="rb-friends" aria-live="polite"></div>
          <button type="button" class="btn btn-primary btn-block" id="rb-invite-send" disabled>Loading friends…</button>
        </section>
        <section class="roster-boost-channel hidden" id="rb-court-channel" aria-labelledby="rb-court-title">
          <div class="roster-boost-channel-head">
            <span class="roster-boost-channel-icon">📣</span>
            <div><b id="rb-court-title">Post to court chat</b><span>Share one live opening in ${esc(game.court?.name || 'court')} chat.</span></div>
          </div>
          <button type="button" class="btn btn-secondary btn-block" id="rb-post-court">Post to court chat</button>
          <button type="button" class="roster-boost-withdraw hidden" id="rb-withdraw-court">Withdraw court post</button>
        </section>
        <section class="roster-boost-channel" id="rb-share-channel" aria-labelledby="rb-share-title">
          <div class="roster-boost-channel-head">
            <span class="roster-boost-channel-icon">📤</span>
            <div><b id="rb-share-title">Share anywhere</b><span>Text the live game link to any group.</span></div>
          </div>
          <button type="button" class="btn btn-secondary btn-block" id="rb-share">Share game link</button>
        </section>
        <div class="roster-boost-receipts" id="rb-receipts"></div>
        <div class="roster-boost-status" id="rb-status" role="status" aria-live="polite" aria-atomic="true"></div>
      </div>
    `, { label: 'Find players' });

    const summaryEl = sheet.querySelector('#rb-summary');
    const friendsSection = sheet.querySelector('#rb-friends-channel');
    const friendsEl = sheet.querySelector('#rb-friends');
    const sendButton = sheet.querySelector('#rb-invite-send');
    const courtSection = sheet.querySelector('#rb-court-channel');
    const postButton = sheet.querySelector('#rb-post-court');
    const withdrawButton = sheet.querySelector('#rb-withdraw-court');
    const shareButton = sheet.querySelector('#rb-share');
    const shareSection = sheet.querySelector('#rb-share-channel');
    const primaryChannel = sheet.querySelector('#rb-primary-channel');
    const moreChannels = sheet.querySelector('#rb-more-channels');
    const moreChannelsBody = sheet.querySelector('#rb-more-channels-body');
    const receiptsEl = sheet.querySelector('#rb-receipts');
    const statusEl = sheet.querySelector('#rb-status');
    let primaryChannelId = '';

    const announce = (message, tone = '') => {
      statusEl.textContent = message || '';
      statusEl.className = `roster-boost-status${tone ? ` is-${tone}` : ''}`;
    };
    const currentPlayerIds = () => new Set(
      (game.players || []).map((player) => Number(player.user_id)),
    );
    const invitableFriends = () => {
      const inGame = currentPlayerIds();
      return friends.filter((friend) => !inGame.has(Number(friend.id)));
    };
    const syncSendButton = () => {
      const full = Number(game.spots_left) <= 0 || game.status !== 'upcoming';
      sendButton.disabled = busy || full || selected.size === 0;
      if (busy) return;
      if (full) sendButton.textContent = 'Roster is full';
      else if (selected.size) sendButton.textContent = `Send ${selected.size} invite${selected.size === 1 ? '' : 's'}`;
      else sendButton.textContent = 'Select players to invite';
    };
    const renderFriends = () => {
      if (!friendsReady) {
        friendsSection.classList.remove('hidden');
        friendsEl.innerHTML = friendsError
          ? '<button type="button" class="btn btn-secondary btn-block" id="rb-friends-retry">Retry loading friends</button>'
          : '<div class="roster-boost-loading">Loading your crew…</div>';
        sendButton.textContent = friendsError ? 'Friends unavailable' : 'Loading friends…';
        sendButton.disabled = true;
        return;
      }
      const candidates = invitableFriends();
      friendsSection.classList.toggle('hidden', candidates.length === 0);
      const candidateIds = new Set(candidates.map((friend) => Number(friend.id)));
      [...selected].forEach((userId) => { if (!candidateIds.has(userId)) selected.delete(userId); });
      friendsEl.innerHTML = candidates.length ? candidates.map((friend) => {
        const userId = Number(friend.id);
        const active = selected.has(userId);
        const sent = sentInviteIds.has(userId);
        return `<button type="button" class="invite-chip${active ? ' active' : ''}${sent ? ' is-sent' : ''}"
          data-rb-friend="${userId}" aria-pressed="${active}" ${sent ? 'disabled' : ''}>
          ${avatarHtml(friend, 'sm')} ${esc((friend.display_name || 'Player').split(' ')[0])}${sent ? ' ✓' : ''}
        </button>`;
      }).join('') : '';
      syncSendButton();
    };
    const renderCourtAction = () => {
      const relevant = !!(game && game.is_creator && game.visibility === 'open'
        && !game.is_instant && game.recurrence === 'none' && game.court);
      courtSection.classList.toggle('hidden', !relevant);
      if (!relevant) return;
      const stateName = String(openCall && openCall.state || '');
      const liveCall = ['open', 'full'].includes(stateName);
      withdrawButton.classList.toggle('hidden', !liveCall || !openCall.can_withdraw);
      if (liveCall) {
        postButton.disabled = true;
        postButton.textContent = stateName === 'full'
          ? '✓ Court post live · roster full'
          : '✓ Live in court chat';
      } else if (openCall && ['closed', 'withdrawn'].includes(stateName)) {
        postButton.disabled = true;
        postButton.textContent = stateName === 'withdrawn' ? 'Court post withdrawn' : 'Court post closed';
      } else if (Number(game.spots_left) <= 0) {
        postButton.disabled = true;
        postButton.textContent = 'Roster is full';
      } else {
        postButton.disabled = busy || !gameOpenCallCanBeCreated(game);
        postButton.textContent = gameOpenCallCanBeCreated(game)
          ? `Post ${game.spots_left} spot${game.spots_left === 1 ? '' : 's'} left to court chat`
          : 'Court posting is no longer available';
      }
    };
    const renderReceipts = () => {
      const receipts = [];
      if (inviteReceipt) receipts.push(`<span>✓ ${esc(inviteReceipt)}</span>`);
      if (postReceipt) receipts.push(`<span>✓ ${esc(postReceipt)}</span>`);
      if (shareReceipt) receipts.push(`<span>✓ ${esc(shareReceipt)}</span>`);
      receiptsEl.innerHTML = receipts.join('');
    };
    const renderAll = () => {
      summaryEl.innerHTML = rosterBoostSummaryHtml(game);
      renderFriends();
      renderCourtAction();
      renderReceipts();
      const full = Number(game.spots_left) <= 0 || game.status !== 'upcoming';
      const hasFriends = friendsReady && invitableFriends().length > 0;
      const hasCourtPost = !courtSection.classList.contains('hidden');
      const callState = String(openCall && openCall.state || '');
      const canManageCourtPost = ['open', 'full'].includes(callState) && !!openCall.can_withdraw;
      const canCreateCourtPost = !openCall && gameOpenCallCanBeCreated(game);
      const hasCourtAction = hasCourtPost && (canManageCourtPost || canCreateCourtPost);
      friendsSection.classList.toggle('hidden', full || (friendsReady && !hasFriends));
      courtSection.classList.toggle('hidden', full || !hasCourtPost);
      shareSection.classList.toggle('hidden', full);
      postButton.classList.toggle('btn-primary', !full && !hasFriends && hasCourtAction);
      postButton.classList.toggle('btn-secondary', full || hasFriends || !hasCourtAction);
      shareButton.classList.toggle('btn-primary', !full && !hasFriends && !hasCourtAction);
      shareButton.classList.toggle('btn-secondary', full || hasFriends || hasCourtAction);
      shareButton.disabled = busy;
      const availableChannels = [friendsSection, courtSection, shareSection]
        .filter((section) => !section.classList.contains('hidden'));
      const usableChannels = [
        hasFriends ? friendsSection : null,
        hasCourtAction ? courtSection : null,
        !full ? shareSection : null,
      ].filter((section) => section && availableChannels.includes(section));
      const orderedChannels = [
        ...usableChannels,
        ...availableChannels.filter((section) => !usableChannels.includes(section)),
      ];
      const lead = orderedChannels[0] || null;
      if ((lead?.id || '') !== primaryChannelId) {
        primaryChannelId = lead?.id || '';
        moreChannels.open = false;
      }
      if (lead) primaryChannel.appendChild(lead);
      orderedChannels.slice(1).forEach((section) => moreChannelsBody.appendChild(section));
      moreChannels.classList.toggle('hidden', orderedChannels.length < 2);
      if (full && !statusEl.textContent) {
        announce('Roster full — game on! 🏓', 'success');
      }
    };
    const acceptFreshGame = (fresh, nextOpenCall = undefined) => {
      if (!fresh || Number(fresh.id) !== Number(game.id)) return false;
      const changed = gameFingerprint(fresh) !== gameFingerprint(game);
      game = fresh;
      if (nextOpenCall !== undefined) openCall = nextOpenCall;
      else if (fresh.open_call) openCall = fresh.open_call;
      else if (openCall && ['open', 'full'].includes(openCall.state)) {
        openCall = { ...openCall, state: 'closed', active: false, can_withdraw: false };
      }
      if (changed && typeof onGameUpdated === 'function') onGameUpdated(fresh);
      renderAll();
      return changed;
    };
    const loadFriends = async () => {
      friendsReady = false;
      friendsError = false;
      renderAll();
      try {
        const response = await api('/friends');
        if (!document.body.contains(sheet) || Number(state.me && state.me.id) !== accountId) return;
        friends = response.friends || [];
        friendsReady = true;
      } catch {
        if (!document.body.contains(sheet)) return;
        friendsError = true;
      }
      renderAll();
    };
    const refreshGame = async () => {
      const fresh = await api(`/games/${game.id}`);
      if (!document.body.contains(sheet) || Number(state.me && state.me.id) !== accountId) return false;
      return acceptFreshGame(fresh);
    };

    friendsEl.addEventListener('click', (event) => {
      const retry = event.target.closest('#rb-friends-retry');
      if (retry) { loadFriends(); return; }
      const chip = event.target.closest('[data-rb-friend]');
      if (!chip || chip.disabled || busy) return;
      const userId = Number(chip.dataset.rbFriend);
      if (selected.has(userId)) selected.delete(userId); else selected.add(userId);
      renderFriends();
    });
    sendButton.addEventListener('click', async () => {
      if (!selected.size || busy || Number(game.spots_left) <= 0) return;
      busy = true;
      syncSendButton();
      const requested = [...selected];
      announce(`Sending ${requested.length} invite${requested.length === 1 ? '' : 's'}…`);
      try {
        const response = await api(`/games/${game.id}/invite`, {
          method: 'POST', body: JSON.stringify({ user_ids: requested }),
        });
        (response.invited_user_ids || []).forEach((userId) => sentInviteIds.add(Number(userId)));
        const delivered = Number(response.invited) || 0;
        requested.forEach((userId) => selected.delete(userId));
        inviteReceipt = delivered
          ? `${delivered} invite${delivered === 1 ? '' : 's'} sent`
          : 'Invites already sent';
        announce(inviteReceipt, 'success');
      } catch (error) {
        announce(error.message, 'error');
      } finally {
        busy = false;
        renderAll();
      }
    });
    postButton.addEventListener('click', async () => {
      if (postButton.disabled || busy) return;
      const attempt = pendingGameOpenCallAttempt(accountId, game.id);
      if (!attempt) {
        announce('Nothing was posted because this browser could not save your request.', 'error');
        return;
      }
      busy = true;
      renderAll();
      announce('Posting one live card to court chat…');
      try {
        const response = await api(`/games/${game.id}/open-call`, {
          method: 'POST',
          body: JSON.stringify({ client_attempt_id: attempt.id }),
        });
        openCall = response.open_call;
        postReceipt = response.open_call.state === 'withdrawn'
          ? 'Court post already withdrawn'
          : 'Court chat post is live';
        if (response.game) acceptFreshGame(response.game, response.open_call);
        announce(postReceipt, 'success');
      } catch (error) {
        announce(error.isNetworkError
          ? 'We couldn’t confirm the post. Tap Post again.'
          : error.message, 'error');
      } finally {
        busy = false;
        renderAll();
      }
    });
    withdrawButton.addEventListener('click', async () => {
      if (busy) return;
      busy = true;
      renderAll();
      announce('Withdrawing the court post…');
      try {
        const response = await api(`/games/${game.id}/open-call`, { method: 'DELETE' });
        openCall = response.open_call;
        postReceipt = 'Court post withdrawn';
        if (response.game) acceptFreshGame(response.game, response.open_call);
        announce(postReceipt, 'success');
      } catch (error) {
        announce(error.message, 'error');
      } finally {
        busy = false;
        renderAll();
      }
    });
    shareButton.addEventListener('click', async () => {
      if (busy) return;
      busy = true;
      renderAll();
      const outcome = await shareGame(game, { notify: false });
      busy = false;
      if (outcome === 'shared' || outcome === 'copied') {
        shareReceipt = outcome === 'copied' ? 'Game link copied' : 'Game link shared';
        announce(shareReceipt, 'success');
      } else if (outcome === 'failed') announce('Couldn’t open sharing — try again.', 'error');
      else announce('');
      renderAll();
    });

    renderAll();
    loadFriends();
    refreshGame().catch(() => { /* the initial snapshot keeps every action usable */ });
    const pollTimer = setInterval(async () => {
      if (!document.body.contains(sheet)) { clearInterval(pollTimer); return; }
      if (document.hidden || state.connectionState === 'offline'
          || currentOverlayEntry()?.el !== sheet || busy) return;
      try { await refreshGame(); } catch { /* retry on the next live interval */ }
    }, LIVE_DETAIL_POLL_INTERVAL_MS);
    sheet._cleanupFns?.push(() => clearInterval(pollTimer));
    return sheet;
  }

  function crewSummaryFrom(value) {
    if (!value || typeof value !== 'object') return null;
    const candidate = value.crew && typeof value.crew === 'object' ? value.crew
      : value.saved_crew && typeof value.saved_crew === 'object' ? value.saved_crew : value;
    const id = Number(candidate.id ?? candidate.crew_id);
    if (!Number.isSafeInteger(id) || id <= 0) return null;
    const rawVersion = candidate.roster_version ?? candidate.crew_version;
    const version = rawVersion == null || rawVersion === '' ? null : Number(rawVersion);
    return {
      ...candidate,
      id,
      name: String(candidate.name || candidate.crew_name || 'Your crew').slice(0, 80),
      roster_version: Number.isSafeInteger(version) && version >= 0 ? version : null,
      member_count: Math.max(1, Number(candidate.member_count) || 1),
      pending_count: Math.max(0, Number(candidate.pending_count) || 0),
      default_court_id: Number(candidate.default_court_id) || null,
      default_court_name: String(candidate.default_court_name || '').slice(0, 120),
    };
  }

  function completedGameCrewSummary(game, response = null) {
    return crewSummaryFrom(response)
      || crewSummaryFrom(game && game.crew)
      || crewSummaryFrom(game && game.saved_crew)
      || (game && game.crew_id ? crewSummaryFrom({
        id: game.crew_id,
        name: game.crew_name,
        roster_version: game.crew_roster_version,
      }) : null);
  }

  function completedCrewPlannerOptions(game, crew, crewContext = null) {
    const invitees = (crew || []).map(sanitizePlannerInvitee).filter(Boolean);
    const savedCrew = crewSummaryFrom(crewContext);
    const attachCrew = !!savedCrew && (!crewContext || crewContext.attachCrew !== false);
    const suggestion = window.CrewPlanner && window.CrewPlanner.bestSlot
      ? window.CrewPlanner.bestSlot([state.me, ...invitees], {
          hostId: state.me && state.me.id,
          fallbackScheduledAt: game.scheduled_at,
          minLeadMinutes: 50,
        })
      : null;
    let availabilityLabel = `${invitees.length} teammate${invitees.length === 1 ? '' : 's'} selected`;
    if (suggestion && window.CrewPlanner) {
      const slot = window.CrewPlanner.slotLabel(suggestion.slot);
      availabilityLabel = suggestion.usedFallback
        ? `Same rhythm: ${slot}`
        : `Best overlap: ${slot} · ${suggestion.coverage} of ${suggestion.total} available`;
    }
    const sourceLabel = savedCrew
      ? `${savedCrew.name} · ${invitees.length} teammate${invitees.length === 1 ? '' : 's'}`
      : `Same crew · ${invitees.length} teammate${invitees.length === 1 ? '' : 's'}`;
    return {
      court: game.court ? { ...game.court } : null,
      gameType: game.game_type,
      maxPlayers: game.max_players,
      preferredLevel: game.preferred_level || 'any',
      visibility: 'private',
      invitees,
      inviteUserIds: invitees.map((person) => person.id),
      scheduledAt: suggestion && suggestion.scheduledAt,
      sourceGameId: Number(game.id) || null,
      crewId: attachCrew ? savedCrew.id : null,
      crewVersion: attachCrew ? savedCrew.roster_version : null,
      crewName: savedCrew && savedCrew.name,
      sourceLabel,
      availabilityLabel,
      requireAllInvitees: true,
    };
  }

  async function openCompletedCrewPlanner(game, fromModal, button, crewRequest = null) {
    const intentButtons = [...fromModal.querySelectorAll('#cel-plan-crew, #gs-plan-crew, #gs-rematch')];
    const originalLabels = new Map(intentButtons.map((item) => [item, item.textContent]));
    const restoreIntents = () => intentButtons.forEach((item) => {
      item.disabled = false;
      item.textContent = originalLabels.get(item);
    });
    intentButtons.forEach((item) => { item.disabled = true; });
    if (button) {
      button.textContent = 'Getting the crew ready…';
    }
    try {
      const [response, saved] = await Promise.all([
        crewRequest || api(`/games/${game.id}/crew`),
        api(`/games/${game.id}/crew`, { method: 'POST' }),
      ]);
      const crew = response.items || [];
      if (!crew.length) {
        toast('No eligible teammates are available to invite from this game');
        restoreIntents();
        return;
      }
      let savedCrew = completedGameCrewSummary(game, saved);
      if (!savedCrew) throw new Error('Crew could not be opened');
      if (savedCrew.joined === false || savedCrew.invitation_pending) {
        const joined = await api(`/crews/${savedCrew.id}/respond`, {
          method: 'POST', body: JSON.stringify({ accept: true }),
        });
        savedCrew = completedGameCrewSummary(game, joined) || savedCrew;
        toast(`Joined ${savedCrew.name} 🏓`);
      }
      game.crew = savedCrew;
      const invitedCount = Math.max(0, Number(saved.invited_count) || 0);
      if (!(saved.crew && (saved.crew.joined === false || saved.crew.invitation_pending))) toast(saved.created
        ? `Crew created${invitedCount ? ` — ${invitedCount} invitation${invitedCount === 1 ? '' : 's'} sent` : ''}`
        : `${savedCrew.name} is ready`);
      let options = null;
      if (!saved.created) {
        // Once a Crew already exists, its accepted member list is the privacy
        // boundary. A detail failure must never downgrade this into an
        // editable source-game invite plan with different people or cadence.
        const detail = await api(`/crews/${savedCrew.id}`);
        options = crewPlannerOptions({ ...detail, ...crewSummaryFrom(detail) });
        if (!options.inviteUserIds.length) {
          toast('At least one teammate needs to join before this Crew can plan a game');
          restoreIntents();
          return;
        }
      }
      if (!options) {
        // On the first create, everyone else is still pending. The immediate
        // plan remains a normal private rematch and does not impersonate an
        // accepted Crew roster.
        options = completedCrewPlannerOptions(game, crew, { ...savedCrew, attachCrew: false });
      }
      transitionModal(fromModal, () => openNewGameModal(options));
    } catch (err) {
      toast(err.message);
      restoreIntents();
    }
  }

  function completedCrewConnectionsHtml(crew) {
    const people = crew || [];
    const actions = people.filter((person) => person.friendship_status !== 'accepted');
    if (!people.length) {
      return '<div class="postgame-connection-loading">No eligible connection actions are available from this game.</div>';
    }
    if (!actions.length) {
      return `<div class="postgame-connected"><span>✓</span><div><b>Your crew is connected</b><div class="row-sub">Friends can coordinate the next game anytime.</div></div></div>`;
    }
    return `<div class="section-label" style="margin-top:16px">Stay connected</div>
      <div class="postgame-connections">
        ${actions.map((person) => {
          const pending = person.friendship_status === 'pending';
          const outgoing = pending && person.friendship_outgoing;
          const shared = sharedAvailabilityText(state.me && state.me.availability, person.availability);
          return `<div class="postgame-person">
            <button type="button" class="postgame-person-profile" data-view-user="${person.id}">
              ${avatarHtml(person, 'sm')}
              <span><b>${esc(person.display_name)}</b>${shared ? `<small>Also plays ${esc(shared)}</small>` : '<small>Played this game with you</small>'}</span>
            </button>
            ${outgoing
              ? '<button type="button" class="btn btn-secondary btn-sm" disabled>Requested</button>'
              : `<button type="button" class="btn btn-secondary btn-sm" data-connect-crew="${person.id}" data-friendship-id="${person.friendship_id || ''}" data-connect-kind="${pending ? 'accept' : 'request'}">${pending ? 'Accept' : '＋ Add'}</button>`}
          </div>`;
        }).join('')}
      </div>`;
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

    const savedCrew = completedGameCrewSummary(game);
    const crewInvitePending = !!savedCrew && (savedCrew.joined === false || savedCrew.invitation_pending);
    const planLabel = savedCrew
      ? crewInvitePending ? `👥 Join ${esc(savedCrew.name)} &amp; plan next game` : `📅 Plan next game with ${esc(savedCrew.name)}`
      : '👥 Create crew &amp; plan next game';
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
        <button class="btn btn-primary btn-block" id="cel-plan-crew" style="margin-top:18px">${planLabel}</button>
        ${savedCrew && !crewInvitePending ? '<button class="btn btn-secondary btn-block" id="cel-open-crew" style="margin-top:10px">👥 Open crew</button>' : ''}
        ${won === true ? '<button class="btn btn-secondary btn-block" id="cel-share" style="margin-top:10px">📤 Share the win</button>' : ''}
        <button class="btn btn-secondary btn-block" id="cel-view-game" style="margin-top:10px">See game &amp; connect</button>
        <button class="btn btn-ghost btn-block modal-close" style="margin-top:6px">Done</button>
      </div>
    `);
    modal.querySelector('#cel-share')?.addEventListener('click', () => shareGame(game));
    modal.querySelector('#cel-plan-crew')?.addEventListener('click', (event) => {
      openCompletedCrewPlanner(game, modal, event.currentTarget);
    });
    modal.querySelector('#cel-open-crew')?.addEventListener('click', () => {
      transitionModal(modal, () => openCrewScreen(savedCrew.id));
    });
    modal.querySelector('#cel-view-game')?.addEventListener('click', () => {
      transitionModal(modal, () => openGameScreen(game.id));
    });
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

  let instantRallyInFlight = null;

  function instantRallySession() {
    const userId = safePositiveId(state.me && state.me.id);
    return state.token && userId ? { token: state.token, userId } : null;
  }

  function instantRallySessionMatches(session) {
    return !!session && state.token === session.token
      && safePositiveId(state.me && state.me.id) === session.userId;
  }

  function authoritativeRallyGame(error) {
    const data = error && error.data;
    if (!data || typeof data !== 'object') return null;
    const embedded = data.game && typeof data.game === 'object' ? data.game : null;
    const id = safePositiveId((embedded && embedded.id) ?? data.game_id
      ?? data.rally_game_id ?? data.existing_game_id);
    return id ? { id, ...(embedded || {}) } : null;
  }

  function openResolvedRallyGame(gameId, fromModal = null) {
    if (fromModal) {
      // Do not resurrect a sheet the player already dismissed, and do not
      // replace a newer child sheet that now owns their attention.
      if (!document.body.contains(fromModal) || currentOverlayEntry()?.el !== fromModal) return false;
      return transitionModal(fromModal, () => openGameScreen(gameId));
    }
    openGameScreen(gameId);
    return true;
  }

  function setInstantRallyButtonBusy(button, busy) {
    if (!button) return;
    if (busy) {
      if (button.dataset.rallyStarting === 'true') return;
      button.dataset.rallyStarting = 'true';
      button.dataset.rallyOriginalHtml = button.innerHTML;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.innerHTML = button.classList.contains('rally-action')
        ? '<span class="rally-action-icon">⚡</span><span><b>Finding your rally…</b><small>Checking this court first</small></span>'
        : 'Finding your rally…';
      return;
    }
    if (button.dataset.rallyStarting !== 'true') return;
    const original = button.dataset.rallyOriginalHtml;
    delete button.dataset.rallyStarting;
    delete button.dataset.rallyOriginalHtml;
    button.disabled = false;
    button.removeAttribute('aria-busy');
    if (original != null) button.innerHTML = original;
  }

  function showInstantRallyManagement(button, result, options = {}, callerSession = null) {
    const game = result && result.game;
    const gameId = safePositiveId(game && game.id);
    const ownsRally = !!game && (game.is_creator
      || safePositiveId(game.creator_id) === safePositiveId(callerSession && callerSession.userId));
    if (!button || !gameId || !ownsRally || result.outcome === 'joined' || result.recovered
        || !document.body.contains(button)) return false;

    button._instantRallyManagementCleanup?.();
    const sourceModal = options.fromModal || button.closest('.modal-backdrop') || null;
    const originalHtml = options.confirmationOriginalHtml ?? button.innerHTML;
    const originalAriaLabel = button.getAttribute('aria-label');
    let confirmationTimer = null;
    let more = null;

    const clearManagement = () => {
      clearTimeout(confirmationTimer);
      confirmationTimer = null;
      more?.remove();
      more = null;
      button.removeEventListener('click', handlePrimaryClick, true);
      delete button.dataset.instantRallyAction;
      delete button.dataset.instantRallyGameId;
      delete button._instantRallyManagementCleanup;
    };

    const restoreLauncher = () => {
      if (!document.body.contains(button)) return;
      clearManagement();
      button.disabled = false;
      button.removeAttribute('aria-busy');
      if (originalAriaLabel == null) button.removeAttribute('aria-label');
      else button.setAttribute('aria-label', originalAriaLabel);
      button.innerHTML = originalHtml;
    };

    const cancelRally = async (trigger) => {
      if (!instantRallySessionMatches(callerSession)) {
        clearManagement();
        return;
      }
      clearTimeout(confirmationTimer);
      confirmationTimer = null;
      trigger.disabled = true;
      trigger.setAttribute('aria-busy', 'true');
      trigger.textContent = 'Cancelling…';
      try {
        await api(`/games/${gameId}/cancel`, { method: 'POST' });
        if (!instantRallySessionMatches(callerSession)) return;
        more?.remove();
        more = null;
        button.dataset.instantRallyAction = 'cancelled';
        button.disabled = true;
        button.removeAttribute('aria-busy');
        button.textContent = 'Game cancelled ✓';
        state.playGamesCache = null;
        toast('Game cancelled');
        refreshMe().catch(() => {});
        fetchCourtsInView();
        setTimeout(restoreLauncher, 1200);
      } catch (error) {
        if (!instantRallySessionMatches(callerSession)) return;
        trigger.disabled = false;
        trigger.removeAttribute('aria-busy');
        trigger.textContent = trigger === button ? 'Game started · Cancel' : 'Cancel game';
        button.dataset.instantRallyAction = trigger === button ? 'cancel' : 'open';
        toast(error.message);
      }
    };

    const revealManagement = () => {
      if (!instantRallySessionMatches(callerSession) || !document.body.contains(button)) {
        clearManagement();
        return;
      }
      button.dataset.instantRallyAction = 'open';
      button.textContent = 'Open game';
      button.setAttribute('aria-label', 'Game started. Open game details');
      more = document.createElement('details');
      more.className = 'game-more-actions instant-rally-management';
      more.innerHTML = '<summary>More</summary><div class="game-more-actions-body"><button type="button" class="btn btn-danger btn-block">Cancel game</button></div>';
      more.querySelector('button').addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        cancelRally(event.currentTarget);
      });
      button.insertAdjacentElement('afterend', more);
    };

    function handlePrimaryClick(event) {
      const action = button.dataset.instantRallyAction;
      if (!action) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (action === 'cancel') {
        cancelRally(button);
      } else if (action === 'open') {
        openResolvedRallyGame(gameId, sourceModal);
      }
    }

    button._instantRallyManagementCleanup = clearManagement;
    button.dataset.instantRallyAction = 'cancel';
    button.dataset.instantRallyGameId = String(gameId);
    button.disabled = false;
    button.removeAttribute('aria-busy');
    button.textContent = 'Game started · Cancel';
    button.setAttribute('aria-label', 'Game started. Cancel this game');
    button.addEventListener('click', handlePrimaryClick, true);
    confirmationTimer = setTimeout(revealManagement, 4000);
    return true;
  }

  async function startInstantRally(button, options = {}) {
    if ((!state.presence || !state.presence.checked_in) && !options.presenceConfirmed) {
      openPlayNowCourtPicker();
      return null;
    }
    const callerSession = instantRallySession();
    if (!callerSession) {
      const error = new Error('Sign in again before starting a rally.');
      if (options.onError) options.onError(error, false); else toast(error.message);
      return null;
    }
    const expectedCourtId = safePositiveId(
      options.expectedCourtId ?? state.presence?.court_id,
    );
    if (!expectedCourtId) {
      openPlayNowCourtPicker();
      return null;
    }
    const sharedRecord = instantRallyInFlight;
    if (sharedRecord && sharedRecord.token === callerSession.token
        && sharedRecord.userId === callerSession.userId
        && sharedRecord.courtId === expectedCourtId) {
      setInstantRallyButtonBusy(button, true);
      let resolution;
      try {
        resolution = await sharedRecord.promise;
      }
      finally { if (button && document.body.contains(button)) setInstantRallyButtonBusy(button, false); }
      return continueInstantRallyCall(resolution, button, options, callerSession);
    }
    const attempt = pendingInstantRallyAttempt(callerSession.userId, expectedCourtId);
    if (!attempt) {
      const error = new Error('Sign in again before starting a rally.');
      if (options.onError) options.onError(error, false); else toast(error.message);
      return null;
    }
    setInstantRallyButtonBusy(button, true);
    const operation = (async () => {
      try {
        const result = await api('/games/rally', {
          method: 'POST',
          body: JSON.stringify({
            scheduled_at: attempt.scheduledAt,
            client_attempt_id: attempt.id,
            court_id: attempt.courtId,
          }),
        });
        if (!instantRallySessionMatches(callerSession)) return { abandoned: true };
        const game = result && result.game;
        if (!game || !safePositiveId(game.id)) {
          const malformed = new Error('The rally may have started, but we couldn’t confirm its game. Try again.');
          malformed.isNetworkError = true;
          throw malformed;
        }
        if (game.is_instant
            && (game.status !== 'upcoming' || game.assembly_active === false)) {
          const stale = new Error('That rally is no longer assembling players.');
          stale.code = 'rally_no_longer_active';
          clearInstantRallyAttempt(callerSession.userId, attempt.courtId, attempt.id);
          return { staleRally: true, error: stale };
        }
        clearInstantRallyAttempt(callerSession.userId, attempt.courtId, attempt.id);
        state.playGamesCache = null;
        if (result.outcome === 'joined') {
          toast(`You're in the live rally at ${(game.court || {}).name || 'this court'} 🏓`);
        } else if (result.invited_count > 0) {
          toast(`Rally started — ${result.invited_count} ready player${result.invited_count === 1 ? '' : 's'} invited ⚡`);
        } else {
          toast(result.outcome === 'existing'
            ? 'Your live rally is ready ⚡'
            : 'Rally started — invite or share to fill it ⚡');
        }
        refreshMe().catch(() => { /* the game screen is already authoritative */ });
        return { result };
      } catch (error) {
        if (!instantRallySessionMatches(callerSession)) return { abandoned: true };
        if (['rally_no_longer_active', 'rally_time_out_of_range'].includes(error.code)) {
          // The server has definitively retired this attempt's old assembly.
          // Forget that key before resolving once more, so a lost response can
          // never revive an expired solo shell or erase the new ready signal.
          clearInstantRallyAttempt(callerSession.userId, attempt.courtId, attempt.id);
          return { staleRally: true, error };
        }
        const recoveredGame = authoritativeRallyGame(error);
        if (recoveredGame) {
          clearInstantRallyAttempt(callerSession.userId, attempt.courtId, attempt.id);
          state.playGamesCache = null;
          toast(error.code === 'active_rally_elsewhere'
            ? 'You already have a rally in progress — opening it now'
            : 'That rally already exists — opening it');
          refreshMe().catch(() => {});
          return {
            result: { outcome: 'existing', recovered: true, game: recoveredGame },
          };
        }
        // A network/429/5xx response can be lost after commit. Keep the exact
        // attempt so another tap retrieves the same game instead of duplicating it.
        const retrySafely = !!(error.isNetworkError || Number(error.status) === 429
          || Number(error.status) >= 500);
        if (!retrySafely) {
          clearInstantRallyAttempt(callerSession.userId, attempt.courtId, attempt.id);
        }
        if (error.code === 'active_checkin_required') refreshMe().catch(() => {});
        return { error, retrySafely };
      }
    })();
    const record = {
      token: callerSession.token,
      userId: callerSession.userId,
      courtId: expectedCourtId,
      promise: operation,
    };
    instantRallyInFlight = record;
    let resolution;
    try {
      resolution = await operation;
    }
    finally {
      if (instantRallyInFlight === record) instantRallyInFlight = null;
      if (button && document.body.contains(button)) setInstantRallyButtonBusy(button, false);
    }
    return continueInstantRallyCall(resolution, button, options, callerSession);
  }

  function continueInstantRallyCall(resolution, button, options = {}, callerSession = null) {
    if (!instantRallySessionMatches(callerSession) || resolution?.abandoned) return null;
    if (resolution && resolution.staleRally && !options.staleRallyRestarted) {
      return startInstantRally(button, { ...options, staleRallyRestarted: true });
    }
    if (resolution && resolution.staleRally) {
      return finishInstantRallyCall(
        { error: resolution.error, retrySafely: false }, options, callerSession,
      );
    }
    const finishOptions = button && !options.confirmationButton
      ? { ...options, confirmationButton: button }
      : options;
    return finishInstantRallyCall(resolution, finishOptions, callerSession);
  }

  function finishInstantRallyCall(resolution, options = {}, callerSession = null) {
    if (!resolution) return null;
    if (resolution.error) {
      if (['active_checkin_required', 'active_checkin_court_mismatch'].includes(
        resolution.error.code,
      )) {
        const sourceModal = options.fromModal || null;
        const reopenConfirmation = () => {
          if (!instantRallySessionMatches(callerSession)) return;
          if (sourceModal) {
            if (!document.body.contains(sourceModal)
                || currentOverlayEntry()?.el !== sourceModal) return;
            transitionModal(sourceModal, () => openPlayNowCourtPicker());
          } else {
            openPlayNowCourtPicker();
          }
        };
        // Learn the court that won any other-tab race before asking the player
        // to explicitly confirm where they are now.
        refreshMe().finally(reopenConfirmation);
        return null;
      }
      const message = resolution.retrySafely
        ? 'Couldn’t confirm the rally — tap again'
        : resolution.error.message;
      if (options.onError) options.onError(resolution.error, resolution.retrySafely);
      else toast(message);
      return null;
    }
    const result = resolution.result;
    const gameId = safePositiveId(result && result.game && result.game.id);
    const confirmationButton = options.confirmationButton || null;
    const keptAnchor = showInstantRallyManagement(
      confirmationButton, result, options, callerSession,
    );
    if (gameId && options.openGame !== false && !keptAnchor) {
      // Each caller owns its own current sheet/navigation intent. The shared
      // promise only deduplicates the network mutation, so a dismissed first
      // sheet cannot strand a later, still-visible confirmation sheet.
      openResolvedRallyGame(gameId, options.fromModal || null);
    }
    return result || null;
  }

  function rallyLauncherHtml() {
    const here = state.presence && state.presence.checked_in;
    const pulse = here ? null : normalizeActivePlayPulse(state.activePlayPulse);
    if (!here && state.activePlayPulse && !pulse) state.activePlayPulse = null;
    const title = here ? `At ${esc(state.presence.court_name)}`
      : pulse ? 'Your hour is live' : 'Ready to play?';
    const sub = here
      ? 'Start or join the live rally here, or make a plan for later.'
      : pulse ? 'Nearby players can respond to the court you picked.'
        : 'Play soon, arrive in a few minutes, or play anytime in the next hour.';
    const immediateAction = here
      ? `<button type="button" class="rally-action primary" data-goto="instant-rally">
          <span class="rally-action-icon">⚡</span>
          <span><b>Find or start a game</b><small>At ${esc(state.presence.court_name)}</small></span>
        </button>`
      : pulse ? ''
        : `<button type="button" class="rally-action primary" data-goto="play-soon">
            <span class="rally-action-icon">⚡</span>
            <span><b>Play soon</b><small>Now, arriving, or free this hour</small></span>
          </button>`;
    const actions = `${immediateAction}
      <button type="button" class="rally-action" data-goto="new-game">
        <span class="rally-action-icon">📅</span>
        <span><b>Plan a game</b><small>Choose where, when, and who</small></span>
      </button>`;
    return `
      <section class="rally-launch" aria-labelledby="rally-title">
        <div class="rally-kicker">Get on court</div>
        <h3 id="rally-title">${title}</h3>
        <p>${sub}</p>
        ${pulse ? activePlayPulseBannerHtml(pulse) : ''}
        <div class="rally-actions">${actions}</div>
      </section>`;
  }

  function playMoreRoutesHtml() {
    return `<section class="play-more-routes" aria-labelledby="play-more-title">
      <div class="section-label" id="play-more-title">More ways to play</div>
      <div class="row">
        ${state.playSeg === 'games' ? '' : '<button type="button" class="btn btn-secondary" data-play-route="games">🏓 Games</button>'}
        <button type="button" class="btn btn-secondary" data-play-route="scores" ${state.playSeg === 'scores' ? 'aria-current="page"' : ''}>🏆 Rankings</button>
        <button type="button" class="btn btn-secondary" data-play-route="brackets" ${state.playSeg === 'brackets' ? 'aria-current="page"' : ''}>🎯 Competitions</button>
      </div>
    </section>`;
  }

  function bindPlayMoreRoutes(root) {
    root.querySelectorAll('[data-play-route]').forEach((button) => button.addEventListener('click', () => {
      state.playSeg = button.dataset.playRoute;
      syncPlayFab();
      renderPlay();
    }));
  }

  async function renderPlay({ reuseFresh = false, useCachedData = false } = {}) {
    const seg = state.playSeg;
    const liveEl = $('#play-content');
    if (document.getElementById(`play-tab-${seg}`)) liveEl.setAttribute('aria-labelledby', `play-tab-${seg}`);
    else {
      liveEl.removeAttribute('aria-labelledby');
      liveEl.setAttribute('aria-label', 'Play');
    }
    const viewKey = `${state.me?.id || 'signed-out'}:play:${seg}:${areaViewKey()}`;
    if (reuseFresh && viewIsFresh(liveEl, viewKey)) return;
    const renderSeq = ++state.playRenderSeq;
    const hadUsableContent = beginViewRender(liveEl, viewKey, 5);
    if (seg === 'games' && !hadUsableContent) {
      // The checked-in action is useful before discovery feeds finish.
      liveEl.innerHTML = rallyLauncherHtml() + skeletonHtml(4);
    }
    const el = document.createElement('div');
    const commit = () => {
      if (renderSeq !== state.playRenderSeq || state.playSeg !== seg) return false;
      commitViewRender(liveEl, el, viewKey);
      return true;
    };
    const loc = areaLatLng();
    if (seg === 'brackets') {
      await renderTournaments(el, () => renderPlay());
      el.insertAdjacentHTML('afterbegin', playMoreRoutesHtml());
      bindPlayMoreRoutes(el);
      commit();
      return;
    }
    try {
      if (seg === 'scores') {
        let scope = state.boardScope || 'near';
        if (scope === 'month') {
          // Migrate the former one-dimensional "This month" scope in memory.
          scope = 'all';
          state.boardScope = 'all';
          state.boardPeriod = 'month';
        }
        const period = state.boardPeriod === 'month' ? 'month' : 'all';
        const params = [];
        if (scope === 'near') params.push(`lat=${loc.lat}`, `lng=${loc.lng}`, 'radius=50');
        if (period === 'month') params.push('period=month');
        const boardUrl = `/leaderboard${params.length ? `?${params.join('&')}` : ''}`;
        const isMonth = period === 'month';
        const boardVal = (u) => (isMonth
          ? `<span class="${u.month_delta >= 0 ? 'delta-up' : 'delta-down'}">${u.month_delta >= 0 ? '+' : ''}${u.month_delta}</span>`
          : u.rating);
        const [board, results] = await Promise.all([
          api(boardUrl),
          api(`/games/results?lat=${loc.lat}&lng=${loc.lng}`),
        ]);
        let html = playMoreRoutesHtml() + `
          <div class="rankings-filters">
            <div class="segmented" id="board-geography" role="group" aria-label="Ranking area">
              <button type="button" data-scope="near" class="${scope === 'near' ? 'active' : ''}" aria-pressed="${scope === 'near'}">📍 Near me</button>
              <button type="button" data-scope="all" class="${scope === 'all' ? 'active' : ''}" aria-pressed="${scope === 'all'}">🌎 Everyone</button>
            </div>
            <div class="segmented" id="board-period" role="group" aria-label="Ranking period">
              <button type="button" data-period="all" class="${!isMonth ? 'active' : ''}" aria-pressed="${!isMonth}">All time</button>
              <button type="button" data-period="month" class="${isMonth ? 'active' : ''}" aria-pressed="${isMonth}">This month</button>
            </div>
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
          const meIndex = me ? board.items.findIndex((u) => u.id === me.id) : -1;
          if (me && meIndex >= 10) {
            const boardMe = board.items[meIndex];
            html += `<div class="card row you-row" data-view-user="${boardMe.id}" style="cursor:pointer;padding:10px 14px">
              <div class="rank-num">${meIndex + 1}</div>
              ${avatarHtml(boardMe, 'sm')}
              <div class="row-main">
                <div class="row-title" style="font-size:14px">You</div>
                <div class="row-sub">${isMonth ? `${boardMe.month_games} ranked game${boardMe.month_games === 1 ? '' : 's'} this month` : `${boardMe.ranked_wins}W – ${boardMe.ranked_losses}L`}</div>
              </div>
              <div class="stat-value" style="font-size:16px">${boardVal(boardMe)}</div>
            </div>`;
          } else if (me && meIndex === -1) {
            html += `<div class="card row you-row" style="padding:10px 14px">
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
        el.querySelector('#board-geography').addEventListener('click', (e) => {
          const btn = e.target.closest('button');
          if (!btn) return;
          state.boardScope = btn.dataset.scope;
          renderPlay();
        });
        el.querySelector('#board-period').addEventListener('click', (e) => {
          const btn = e.target.closest('button');
          if (!btn) return;
          state.boardPeriod = btn.dataset.period;
          renderPlay();
        });
        bindGameButtons(el, renderPlay);
        bindUserButtons(el);
        bindPlayMoreRoutes(el);
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
        ]);
        state.playGamesCache = gameBundle;
      }
      const [mine, friends, nearby] = gameBundle;
      const nowMs = Date.now();
      const toScore = mine.items.filter((g) =>
        g.status === 'upcoming' && g.can_enter_score
          && (g.is_instant
            ? instantRallyScorePending(g)
            : new Date(g.scheduled_at).getTime() <= nowMs));
      const toConfirm = mine.items.filter((g) => g.awaiting_your_confirmation);
      const waiting = mine.items.filter((g) =>
        g.status === 'awaiting_confirmation' && !g.awaiting_your_confirmation);
      const upcoming = mine.items.filter((g) =>
        !toScore.includes(g) && !toConfirm.includes(g) && !waiting.includes(g)
          && !instantRallyClosed(g));
      const mineIds = new Set(mine.items.map((g) => g.id));
      const friendsGames = (friends.items || []).filter((g) =>
        !mineIds.has(g.id) && !instantRallyClosed(g));
      const friendsIds = new Set(friendsGames.map((g) => g.id));
      const nearbyOpen = nearby.items.filter((g) =>
        !mineIds.has(g.id) && !friendsIds.has(g.id) && !instantRallyClosed(g));

      let html = rallyLauncherHtml();

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
        html += '<div class="section-label">Next game</div>';
        html += gameCardHtml(upcoming[0]);
      }
      // Weekly open-play sessions get their own discovery section, whether a
      // friend hosts them or they're just nearby. Your own stay under "upcoming".
      const isWeekly = (g) => g.recurrence === 'weekly';
      const weeklySessions = [...friendsGames.filter(isWeekly), ...nearbyOpen.filter(isWeekly)];
      const friendsOneOff = friendsGames.filter((g) => !isWeekly(g));
      const nearbyOneOff = nearbyOpen.filter((g) => !isWeekly(g));
      // Best skill/time fits get pulled out of the nearby list into their own rail.
      const picked = nearbyOneOff
        .filter((g) => gameMatchReasons(g).length)
        .sort((a, b) => gameMatchReasons(b).length - gameMatchReasons(a).length
          || (a.distance_miles ?? 1e9) - (b.distance_miles ?? 1e9))
        .slice(0, 3);
      const pickedIds = new Set(picked.map((g) => g.id));
      const restNearby = nearbyOneOff.filter((g) => !pickedIds.has(g.id));
      const discovery = [];
      const discoveryIds = new Set();
      [...friendsOneOff, ...picked, ...restNearby, ...weeklySessions].forEach((game) => {
        if (!discoveryIds.has(game.id)) { discoveryIds.add(game.id); discovery.push(game); }
      });
      const featuredDiscovery = discovery.slice(0, 2);
      html += '<div class="section-label">Games near you</div>';
      html += featuredDiscovery.length
        ? featuredDiscovery.map((game) => gameCardHtml(game, { compact: true })).join('')
        : '<div class="empty-state" style="padding:18px">No open games around you right now.<br><button class="btn btn-primary" data-goto="new-game" style="margin-top:10px"><svg class="pb-ic"><use href="#pb"/></svg> Start a game</button><br><button class="btn btn-secondary btn-sm" data-invite-share style="margin-top:8px">💌 Invite friends to play</button></div>';
      const moreUpcoming = upcoming.slice(1);
      const moreDiscovery = discovery.slice(2);
      if (moreUpcoming.length || moreDiscovery.length) {
        html += `<details class="play-game-depth">
          <summary>See all games · ${moreUpcoming.length + moreDiscovery.length} more</summary>
          <div class="play-game-depth-body">
            ${moreUpcoming.length ? `<div class="section-label">Your upcoming games</div>${moreUpcoming.map((game) => gameCardHtml(game, { compact: true })).join('')}` : ''}
            ${moreDiscovery.length ? `<div class="section-label">More nearby games</div>${moreDiscovery.map((game) => gameCardHtml(game, { compact: true })).join('')}` : ''}
          </div>
        </details>`;
      }
      // Capture spontaneous pickup games that never got scheduled here.
      html += '<button class="btn btn-secondary btn-block" id="pl-log-game" style="margin-top:14px">✍️ Log a game you already played</button>';
      html += playMoreRoutesHtml();

      if (state.playSeg !== seg) return; // a newer segment render owns the panel
      el.innerHTML = html;
      bindPlayMoreRoutes(el);
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
    $('#play-segments')?.addEventListener('click', (e) => {
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
    $('#new-game-fab')?.addEventListener('click', () => {
      if (state.playSeg === 'scores') openNewGameModal({ gameType: 'ranked' });
      else if (state.playSeg === 'brackets') openCompetitionCreateSheet();
      else openNewGameModal();
    });
    syncPlayFab();
    $('#play-activity')?.addEventListener('click', openActivity);
    $('#play-avatar-button')?.addEventListener('click', () => switchTab('profile'));
  }

  function syncPlayFab() {
    const fab = $('#new-game-fab');
    if (!fab) return;
    // Compete owns one labeled create path in its page content.
    fab.classList.toggle('hidden', state.playSeg === 'brackets');
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
          <div class="score-stepper"><button type="button" data-lg-step="-1" data-lg-target="lg-s1" aria-label="Decrease your score">−</button><input type="number" id="lg-s1" min="0" max="99" value="11" inputmode="numeric" aria-label="Your score" /><button type="button" data-lg-step="1" data-lg-target="lg-s1" aria-label="Increase your score">＋</button></div>
        </div>
        <div class="score-vs">vs</div>
        <div class="score-panel"><div class="score-team-label" id="lg-opp-label">Them</div>
          <div class="score-stepper"><button type="button" data-lg-step="-1" data-lg-target="lg-s2" aria-label="Decrease opponent score">−</button><input type="number" id="lg-s2" min="0" max="99" value="9" inputmode="numeric" aria-label="Opponent score" /><button type="button" data-lg-step="1" data-lg-target="lg-s2" aria-label="Increase opponent score">＋</button></div>
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

  async function openNewGameModal(options = {}) {
    const plannerOptions = options && typeof options === 'object' ? options : {};
    const plannerId = (value) => Number.isSafeInteger(Number(value)) && Number(value) > 0
      ? Number(value) : null;
    let court = plannerOptions.court || null;
    const defaultType = plannerOptions.gameType === 'ranked' ? 'ranked' : 'casual';
    const preferredSlot = typeof plannerOptions.preferredSlot === 'string' ? plannerOptions.preferredSlot : null;
    const preferredScheduledAt = typeof plannerOptions.scheduledAt === 'string' ? plannerOptions.scheduledAt : null;
    const requestedMaxPlayers = [2, 4, 6, 8, 10, 12].includes(Number(plannerOptions.maxPlayers))
      ? Number(plannerOptions.maxPlayers) : 4;
    let presetMaxPlayers = defaultType === 'ranked' ? Math.min(requestedMaxPlayers, 4) : requestedMaxPlayers;
    const presetPreferredLevel = ['any', 'beginner', 'intermediate', 'advanced', 'pro'].includes(plannerOptions.preferredLevel)
      ? plannerOptions.preferredLevel : 'any';
    const presetVisibility = ['open', 'friends', 'private'].includes(plannerOptions.visibility)
      ? plannerOptions.visibility : null;
    const presetCrewId = plannerId(plannerOptions.crewId);
    const presetCrewVersion = plannerOptions.crewVersion != null
      && Number.isSafeInteger(Number(plannerOptions.crewVersion))
      && Number(plannerOptions.crewVersion) >= 0 ? Number(plannerOptions.crewVersion) : null;
    const presetCrewName = String(plannerOptions.crewName || '').slice(0, 80);
    const presetInvitees = (Array.isArray(plannerOptions.invitees) ? plannerOptions.invitees : [])
      .map(sanitizePlannerInvitee).filter(Boolean).slice(0, 20);
    const presetInviteUserIds = [...new Set([
      ...(Array.isArray(plannerOptions.inviteUserIds) ? plannerOptions.inviteUserIds : []),
      ...presetInvitees.map((person) => person.id),
    ].map(Number).filter((id) => Number.isSafeInteger(id) && id > 0))].slice(0, 20);
    const plannerTitle = presetCrewId ? `Plan with ${presetCrewName || 'your crew'}`
        : plannerOptions.sourceGameId ? 'Plan the next game' : 'Plan a game';
    const plannerShell = openModal(`
      ${modalHead(plannerTitle)}
      <div class="planner-loading">
        <p class="row-sub" style="margin-bottom:12px">Getting your courts and crew ready…</p>
        ${skeletonHtml(3)}
      </div>
    `, { label: plannerTitle });
    const modalLoad = beginRoutedOverlayLoad(null);
    const explicitPlannerIntent = Object.keys(plannerOptions).length > 0;
    const savedDraft = readGameDraft();
    const restoredDraft = !explicitPlannerIntent ? savedDraft : null;
    const protectedSubmittingDraft = !!(
      savedDraft && explicitPlannerIntent && savedDraft.status === 'submitting'
    );
    let requireAllInvitees = restoredDraft
      ? restoredDraft.requireAllInvitees : plannerOptions.requireAllInvitees === true;
    let sourceLabel = restoredDraft ? restoredDraft.sourceLabel : String(plannerOptions.sourceLabel || '').slice(0, 80);
    let availabilityLabel = restoredDraft
      ? restoredDraft.availabilityLabel : String(plannerOptions.availabilityLabel || '').slice(0, 120);
    const sourceGameId = restoredDraft ? restoredDraft.sourceGameId : Number(plannerOptions.sourceGameId) || null;
    const crewId = restoredDraft ? restoredDraft.crewId : presetCrewId;
    let crewVersion = restoredDraft ? restoredDraft.crewVersion : presetCrewVersion;
    let crewName = restoredDraft && restoredDraft.crewId
      ? String(restoredDraft.sourceLabel || '').split(' · ')[0].slice(0, 80)
      : presetCrewName;
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

    // Invitees from a completed game are legitimate private-game invitees even
    // when they are not friends yet. Keep their public snapshots ahead of the
    // general friend list so the exact crew remains visible and recoverable.
    const invitePeopleById = new Map();
    const plannerPeople = [
      ...presetInvitees,
      ...((restoredDraft && restoredDraft.invitees) || []),
      ...(crewId ? [] : friends),
    ];
    plannerPeople
      .map(sanitizePlannerInvitee).filter(Boolean)
      .forEach((person) => {
        if (!state.me || person.id !== state.me.id) invitePeopleById.set(person.id, person);
      });
    const invitePeople = [...invitePeopleById.values()];
    const requestedInviteIds = restoredDraft ? restoredDraft.inviteUserIds : presetInviteUserIds;
    const invitePeopleIds = new Set(invitePeople.map((person) => person.id));
    const initialInviteIds = new Set(requestedInviteIds.filter((id) => invitePeopleIds.has(id)));
    if (crewId) {
      const acceptedCrewSize = initialInviteIds.size + 1;
      presetMaxPlayers = [2, 4, 6, 8, 10, 12].find((count) => count >= acceptedCrewSize) || 12;
    }
    const initialVisibility = crewId ? 'private' : (restoredDraft
      ? restoredDraft.visibility
      : (presetVisibility || (initialInviteIds.size ? 'private' : 'open')));

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
    // Include the same weekday next week. This lets a passed Monday-evening
    // habit resolve to next Monday rather than an already-past time today.
    for (let i = 0; i < 8; i++) {
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
    const initialScheduledAt = (restoredDraft && restoredDraft.scheduledAt) || preferredScheduledAt;
    if (initialScheduledAt) {
      const preferredDate = new Date(initialScheduledAt);
      const dayIdx = days.findIndex((day) => day.toDateString() === preferredDate.toDateString());
      if (Number.isFinite(preferredDate.getTime())
          && preferredDate.getTime() > Date.now() + 50 * 60000
          && dayIdx >= 0 && preferredDate.getMinutes() === 0
          && timePresets.includes(preferredDate.getHours())) {
        selDayIdx = dayIdx;
        selHour = preferredDate.getHours();
      }
    }

    // Keep the first decision light: offer three useful complete date/time
    // choices instead of asking players to scan a day-by-time matrix.
    const smartTimeSuggestions = [];
    const smartTimeKeys = new Set();
    const addSmartTime = (dayIdx, hour) => {
      if (smartTimeSuggestions.length >= 3 || !days[dayIdx] || !timePresets.includes(hour)) return;
      const date = new Date(days[dayIdx]);
      date.setHours(hour, 0, 0, 0);
      const key = `${dayIdx}:${hour}`;
      if (date.getTime() <= Date.now() + 50 * 60000 || smartTimeKeys.has(key)) return;
      smartTimeKeys.add(key);
      smartTimeSuggestions.push({ dayIdx, hour, date });
    };
    addSmartTime(selDayIdx, selHour);
    for (let dayIdx = selDayIdx; dayIdx < days.length && smartTimeSuggestions.length < 3; dayIdx++) {
      [10, 14, 18, 20].forEach((hour) => addSmartTime(dayIdx, hour));
    }
    for (let dayIdx = 0; dayIdx < days.length && smartTimeSuggestions.length < 3; dayIdx++) {
      timePresets.forEach((hour) => addSmartTime(dayIdx, hour));
    }
    const smartTimeLabel = ({ date, dayIdx, hour }) => `${dayLabel(date, dayIdx)} at ${timeLabel(hour)}`;
    const smartTimeChips = smartTimeSuggestions.map((slot) =>
      `<button type="button" data-smart-time="${slot.date.toISOString()}" data-smart-day="${slot.dayIdx}" data-smart-hour="${slot.hour}" aria-pressed="${slot.dayIdx === selDayIdx && slot.hour === selHour}" class="${slot.dayIdx === selDayIdx && slot.hour === selHour ? 'active' : ''}">${smartTimeLabel(slot)}</button>`).join('');

    const inviteChipHtml = (f, selected = false) => `
      <button type="button" class="invite-chip ${selected ? 'active' : ''}" data-fid="${f.id}" aria-pressed="${selected}">
        ${avatarHtml(f, 'sm')} ${esc(f.display_name.split(' ')[0])}
      </button>`;
    const friendChips = invitePeople.map((f) => inviteChipHtml(f, initialInviteIds.has(f.id))).join('');

    const suggestionRows = suggestions.map((c) => `
      <button type="button" class="court-suggestion" data-pick-court="${c.id}" data-pick-name="${esc(c.name)}">
        <div class="row-main">
          <div class="row-title" style="font-size:14px">${esc(c.name)}</div>
          <div class="row-sub">${esc(c.city || '')}</div>
        </div>
        <span class="tag" style="margin:0">${esc(c.tag)}</span>
      </button>`).join('');

    const hasPresetInvites = initialInviteIds.size > 0;
    const plannerCrewHtml = sourceLabel || crewId ? `<div class="planner-crew-preset" id="ng-crew-preset" role="status">
      <div class="planner-crew-avatars">${invitePeople.filter((person) => initialInviteIds.has(person.id)).slice(0, 4).map((person) => avatarHtml(person, 'sm')).join('')}</div>
      <div class="row-main"><b id="ng-crew-title">${esc(sourceLabel || crewName || 'Your Crew')}</b><div class="row-sub" id="ng-crew-copy">${crewId
        ? `🔒 Private to all ${initialInviteIds.size + 1} accepted crew members.`
        : esc(availabilityLabel || `${initialInviteIds.size} teammate${initialInviteIds.size === 1 ? '' : 's'} selected`)}</div></div>
    </div>` : '';
    const plannerRecoveryHtml = restoredDraft
      ? `<div class="planner-recovery ${restoredDraft.status === 'submitting' ? 'warn' : ''}" role="status">
          <div class="row-main">
            <b>${restoredDraft.status === 'submitting' ? 'Confirm this game' : 'Continuing your saved plan'}</b>
            <div class="row-sub">${restoredDraft.status === 'submitting'
              ? 'We lost the confirmation. Check My games or try this same plan again.'
              : 'Review the time and people, then schedule when ready.'}</div>
          </div>
          ${restoredDraft.status === 'submitting'
            ? '<button type="button" class="btn btn-secondary btn-sm" id="ng-check-games">My games</button><button type="button" class="btn btn-primary btn-sm" id="ng-retry-exact">Try again</button>'
            : '<button type="button" class="btn btn-secondary btn-sm" id="ng-start-over">Start over</button>'}
        </div>`
      : (savedDraft && explicitPlannerIntent
        ? `<div class="planner-recovery ${protectedSubmittingDraft ? 'warn' : ''}" role="status">
            <div class="row-main"><b>${protectedSubmittingDraft ? 'Resolve the game awaiting confirmation' : 'You have a saved plan'}</b><div class="row-sub">${protectedSubmittingDraft ? 'Check whether it was created before starting another plan.' : 'This new plan will stay separate until you edit it.'}</div></div>
            ${protectedSubmittingDraft ? '<button type="button" class="btn btn-secondary btn-sm" id="ng-check-games">Check</button>' : ''}
            <button type="button" class="btn btn-secondary btn-sm" id="ng-resume-draft">Resume</button>
          </div>` : '');
    const modal = plannerShell;
    const plannerBox = modal.querySelector('.modal');
    plannerBox.innerHTML = `
      ${modalHead(plannerTitle)}
      ${plannerRecoveryHtml}
      ${plannerCrewHtml}

      <div class="court-selected hidden" id="ng-answer-where" role="group" aria-label="Where">
        <div class="row-main"><div class="row-title"><span class="row-sub">Where</span> · <span id="ng-answer-where-value">${court ? esc(court.name) : 'Choose a court'}</span></div></div>
        <button type="button" class="btn btn-secondary btn-sm" id="ng-back-where" aria-label="Change where">Change</button>
      </div>
      <section class="planner-step" id="ng-step-where" aria-labelledby="planner-where-title">
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
        <button type="button" class="btn btn-primary btn-block planner-next" id="ng-next-when" ${court ? '' : 'disabled'}>Choose when</button>
      </section>

      <div class="court-selected hidden" id="ng-answer-when" role="group" aria-label="When">
        <div class="row-main"><div class="row-title"><span class="row-sub">When</span> · <span id="ng-answer-when-value">${dayLabel(days[selDayIdx], selDayIdx)} at ${timeLabel(selHour)}</span></div></div>
        <button type="button" class="btn btn-secondary btn-sm" id="ng-back-when" aria-label="Change when">Change</button>
      </div>
      <section class="planner-step hidden" id="ng-step-when" aria-labelledby="planner-when-title">
        <div class="planner-step-head">
          <span class="planner-step-num">2</span>
          <div><div class="planner-step-title" id="planner-when-title">When?</div><div class="planner-step-sub">Pick a suggestion or choose any other time.</div></div>
        </div>
        <div id="ng-later-fields">
          <div class="quick-times" id="ng-smart-times" role="group" aria-label="Suggested game times" style="margin-bottom:8px">${smartTimeChips}</div>
          <details id="ng-other-time" style="margin-bottom:12px">
            <summary>Choose another time</summary>
            <input type="datetime-local" id="ng-when" aria-label="Game date and time" style="margin-top:10px" />
          </details>
          <div id="ng-busy-hint" class="row-sub" style="margin-bottom:4px"></div>
        </div>
        ${crewId ? `<div class="planner-crew-private" id="ng-crew-private" role="status">
          <b>🔒 Private to ${esc(crewName || 'your crew')}</b>
          <span>All ${initialInviteIds.size + 1} accepted player${initialInviteIds.size ? 's are' : ' is'} included.</span>
        </div>` : ''}
        ${crewId ? '' : '<button type="button" class="btn btn-primary btn-block planner-next" id="ng-next-who">Choose who</button>'}
      </section>

      ${crewId ? `<section class="planner-step hidden" id="ng-step-who" aria-hidden="true">
        <div id="ng-friends-wrap" class="hidden" aria-hidden="true"></div>
      </section>` : `<section class="planner-step hidden" id="ng-step-who" aria-labelledby="planner-who-title">
        <div class="planner-step-head">
          <span class="planner-step-num">3</span>
          <div><div class="planner-step-title" id="planner-who-title">Who should see it?</div><div class="planner-step-sub">Keep it open, share with friends, or invite specific players.</div></div>
        </div>
        <div class="type-cards vis-cards" id="ng-vis" role="group" aria-label="Who can join">
          <button type="button" data-vis="open" aria-pressed="${initialVisibility === 'open'}" class="${initialVisibility === 'open' ? 'active' : ''}"><span style="font-size:19px">🌍</span><b>Anyone</b><small>Nearby players</small></button>
          <button type="button" data-vis="friends" aria-pressed="${initialVisibility === 'friends'}" class="${initialVisibility === 'friends' ? 'active' : ''}"><span style="font-size:19px">🤝</span><b>Friends</b><small>All your friends</small></button>
          <button type="button" data-vis="private" aria-pressed="${initialVisibility === 'private'}" class="${initialVisibility === 'private' ? 'active' : ''}"><span style="font-size:19px">🔒</span><b>Specific</b><small>Only who you pick</small></button>
        </div>
        <div class="planner-inline-warning ${initialVisibility === 'friends' && friends.length === 0 ? '' : 'hidden'}" id="ng-friends-empty" role="status">
          You don’t have any friends here yet. Add friends from Community, or choose Anyone so nearby players can join.
        </div>
        <div id="ng-friends-wrap" class="${initialVisibility === 'private' ? '' : 'hidden'}" style="margin-top:10px">
          ${invitePeople.length
            ? `<div class="invite-chips" id="ng-invites">${friendChips}</div>
               <p class="row-sub" id="ng-invite-hint" style="margin-top:6px">${hasPresetInvites ? `${initialInviteIds.size} invited — only selected players will see this game.` : 'Pick who to invite — only they will see this game.'}</p>`
            : '<p class="row-sub">Add friends first to invite specific people.</p>'}
        </div>
      </section>`}

      <details class="planner-advanced hidden" id="ng-advanced">
        <summary><span>More options</span><span class="planner-advanced-copy" id="ng-options-summary">${defaultType === 'ranked' ? 'Ranked' : 'Casual'} · ${crewId ? `${initialInviteIds.size + 1} accepted Crew players` : (presetMaxPlayers === 2 ? 'Singles' : presetMaxPlayers === 4 ? 'Doubles' : `${presetMaxPlayers} players`)} · ${presetPreferredLevel === 'any' ? 'Any level' : skillLabel(presetPreferredLevel)}</span></summary>
        <div class="planner-advanced-body">
          <div class="form-grid">
            <div class="form-field">
              <label>Type</label>
              <div class="type-cards" id="ng-type">
                <button type="button" data-val="casual" aria-pressed="${defaultType === 'casual'}" class="${defaultType === 'casual' ? 'active' : ''}">
                  <span style="font-size:20px"><svg class="pb-ic"><use href="#pb"/></svg></span><b>Casual</b><small>Just for fun</small>
                </button>
                <button type="button" data-val="ranked" aria-pressed="${defaultType === 'ranked'}" class="${defaultType === 'ranked' ? 'active' : ''}" ${crewId && ![2, 4].includes(initialInviteIds.size + 1) ? 'disabled title="Ranked Crew games need exactly 2 or 4 accepted players"' : ''}>
                  <span style="font-size:20px">🏆</span><b>Ranked</b><small>Counts for rating</small>
                </button>
              </div>
            </div>
            <div class="form-field ${crewId ? 'hidden' : ''}">
              <label for="ng-max">Players needed</label>
              <select id="ng-max">
                <option value="2" ${presetMaxPlayers === 2 ? 'selected' : ''}>2 (singles)</option>
                <option value="4" ${presetMaxPlayers === 4 ? 'selected' : ''}>4 (doubles)</option>
                <option value="6" ${presetMaxPlayers === 6 ? 'selected' : ''}>6</option>
                <option value="8" ${presetMaxPlayers === 8 ? 'selected' : ''}>8</option>
                <option value="10" ${presetMaxPlayers === 10 ? 'selected' : ''}>10</option>
                <option value="12" ${presetMaxPlayers === 12 ? 'selected' : ''}>12</option>
              </select>
            </div>
          </div>

          <div class="form-field">
            <label>Level <span class="row-sub">(a hint, not a gate)</span></label>
            <div class="quick-times" id="ng-level" style="margin-top:2px">
              ${['any', 'beginner', 'intermediate', 'advanced', 'pro'].map((level) => `<button type="button" data-level="${level}" class="${presetPreferredLevel === level ? 'active' : ''}" aria-pressed="${presetPreferredLevel === level}">${level === 'any' ? 'Anyone' : skillLabel(level)}</button>`).join('')}
            </div>
          </div>

          ${myClubs.length && !crewId ? `
          <div class="form-field">
            <label>Host under a club banner?</label>
            <div class="quick-times" id="ng-club">
              <button type="button" data-club-id="" class="active">Just me</button>
              ${myClubs.map((cl) => `<button type="button" data-club-id="${cl.id}">🏛 ${esc(cl.name)}</button>`).join('')}
            </div>
            <div class="row-sub" id="ng-club-hint" style="margin-top:6px"></div>
          </div>` : ''}

          <label class="row ${crewId ? 'hidden' : ''}" id="ng-recurring-row" style="margin-bottom:14px;cursor:pointer;gap:10px">
            <input type="checkbox" id="ng-recurring" ${crewId ? 'disabled' : ''} style="width:22px;height:22px;flex:0 0 auto" />
            <span><span style="font-weight:700">🔁 Repeats weekly</span><br><span class="row-sub">Open-play session — players re-RSVP each week</span></span>
          </label>

          <div class="form-field">
            <label for="ng-notes">Note <span class="row-sub">(optional)</span></label>
            <input type="text" id="ng-notes" maxlength="200" placeholder="e.g. All levels welcome!" />
          </div>
        </div>
      </details>

      <div class="planner-submit-bar hidden">
        <div class="form-error hidden" id="ng-submit-error" role="alert" tabindex="-1"></div>
        <div class="planner-summary" id="ng-summary">${court ? esc(court.name) : 'Choose a court'} · ${dayLabel(days[selDayIdx], selDayIdx)} at ${timeLabel(selHour)}</div>
        <button class="btn btn-primary btn-block" id="ng-submit" style="padding:15px">
          Schedule game
        </button>
      </div>
    `;
    setDialogLabel(plannerBox, plannerTitle);

    // A completed-game preset is already valuable user work. Save it on an
    // accidental dismiss unless another draft must remain untouched.
    let plannerDirty = !!sourceGameId && !savedDraft;
    if (crewId && !savedDraft) plannerDirty = true;
    let plannerSubmitted = false;
    let plannerSubmitting = !!(restoredDraft && restoredDraft.status === 'submitting');
    let plannerSubmitStartedAt = plannerSubmitting ? restoredDraft.submitStartedAt : null;
    let plannerAttemptId = (restoredDraft && restoredDraft.clientAttemptId) || newGameAttemptId();
    let frozenSubmitPayload = plannerSubmitting ? restoredDraft.submittedPayload : null;
    let plannerSaveTimer = null;
    let exactRetryRequested = false;
    const plannerScheduledIso = () => {
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
      mode: 'later',
      courtId: Number(modal.querySelector('#ng-court-id').value) || null,
      scheduledAt: plannerScheduledIso(),
      timeKind: customMode ? 'custom' : 'preset',
      visibility,
      inviteUserIds: [...inviteIds],
      invitees: invitePeople.filter((person) => inviteIds.has(person.id)).map(sanitizePlannerInvitee),
      requireAllInvitees,
      sourceLabel,
      availabilityLabel,
      sourceGameId,
      crewId,
      crewVersion,
      gameType,
      maxPlayers: Number(modal.querySelector('#ng-max').value),
      preferredLevel,
      clubId,
      recurrence: crewId ? 'none' : (recurringBox.checked ? 'weekly' : 'none'),
      notes: modal.querySelector('#ng-notes').value.trim(),
      advancedOpen: modal.querySelector('#ng-advanced').open,
      submittedPayload: status === 'submitting' ? frozenSubmitPayload : null,
    });
    const flushPlannerDraft = (status = 'editing') => {
      clearTimeout(plannerSaveTimer);
      plannerSaveTimer = null;
      return writeGameDraft(plannerSnapshot(status));
    };
    const markPlannerDirty = () => {
      if (protectedSubmittingDraft || plannerSubmitting) return;
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
          ? 'Couldn’t confirm the game — check My games'
          : 'Plan saved — finish it anytime');
      }
    });

    // --- Busy-time hint: nudge scheduling toward when players actually show up ---
    let busyTimes = null; // for the currently selected court
    const partOfHour = (h) => (h >= 5 && h < 12 ? 'mornings' : h < 17 ? 'afternoons' : h < 23 ? 'evenings' : null);
    const updateBusyHint = () => {
      const el = modal.querySelector('#ng-busy-hint');
      if (!busyTimes || !busyTimes.length) { el.innerHTML = ''; return; }
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
      const courtName = modal.querySelector('#ng-court-name').textContent || 'Choose a court';
      let whenText;
      if (customMode) {
        const raw = modal.querySelector('#ng-when').value;
        const parsed = raw ? new Date(raw) : null;
        whenText = parsed && Number.isFinite(parsed.getTime()) ? fmtDateTime(parsed.toISOString()) : 'Choose a time';
      } else {
        whenText = `${dayLabel(days[selDayIdx], selDayIdx)} at ${timeLabel(selHour)}`;
      }
      if (summary) summary.textContent = `${courtName} · ${whenText}`;
      const whereAnswer = modal.querySelector('#ng-answer-where');
      modal.querySelector('#ng-answer-where-value').textContent = courtName;
      whereAnswer.setAttribute('aria-label', `Where: ${courtName}`);
      const whenAnswer = modal.querySelector('#ng-answer-when');
      modal.querySelector('#ng-answer-when-value').textContent = whenText;
      whenAnswer.setAttribute('aria-label', `When: ${whenText}`);
    };

    // --- Court picking ---
    const setCourt = (id, name, { dirty = true } = {}) => {
      modal.querySelector('#ng-court-id').value = id || '';
      modal.querySelector('#ng-court-name').textContent = name || '';
      modal.querySelector('#ng-court-selected').classList.toggle('hidden', !id);
      modal.querySelector('#ng-court-picker').classList.toggle('hidden', !!id);
      modal.querySelector('#ng-next-when').disabled = !id;
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
    let customMode = false;
    updatePlannerSummary();
    const otherTimeDetails = modal.querySelector('#ng-other-time');
    modal.querySelector('#ng-smart-times').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-smart-time]');
      if (!btn) return;
      customMode = false;
      selDayIdx = Number(btn.dataset.smartDay);
      selHour = Number(btn.dataset.smartHour);
      modal.querySelectorAll('#ng-smart-times button').forEach((b) => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-pressed', String(active));
      });
      otherTimeDetails.open = false;
      modal.querySelector('#ng-time-warning')?.remove();
      updateBusyHint();
      updatePlannerSummary();
      markPlannerDirty();
    });
    otherTimeDetails.addEventListener('toggle', () => {
      if (!otherTimeDetails.open) return;
      const whenEl = modal.querySelector('#ng-when');
      if (whenEl.value) return;
      const d = new Date(days[selDayIdx]); d.setHours(selHour || 18);
      const pad2 = (n) => String(n).padStart(2, '0');
      whenEl.value = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:00`;
    });
    modal.querySelector('#ng-when').addEventListener('input', () => {
      const value = new Date(modal.querySelector('#ng-when').value);
      customMode = true;
      modal.querySelectorAll('#ng-smart-times button').forEach((button) => {
        button.classList.remove('active');
        button.setAttribute('aria-pressed', 'false');
      });
      if (Number.isFinite(value.getTime()) && value.getTime() > Date.now()) modal.querySelector('#ng-time-warning')?.remove();
      updateBusyHint(); updatePlannerSummary(); markPlannerDirty();
    });
    // Initial hint for a preselected court (after customMode exists).
    if (court) {
      if (court.busy_times) { busyTimes = court.busy_times; updateBusyHint(); }
      else loadBusyHint(court.id);
    }

    // Crew roster state is shared by the options summary and the visibility
    // section. Attached Crew plans keep this snapshot locked.
    let visibility = initialVisibility;
    const inviteIds = new Set(initialInviteIds);

    // --- Type ---
    let gameType = defaultType;
    const recurringRow = modal.querySelector('#ng-recurring-row');
    const recurringBox = modal.querySelector('#ng-recurring');
    const syncRecurring = () => {
      // Recurring weekly sessions are open-play only (ranked games don't repeat).
      const isRanked = gameType === 'ranked';
      const recurringAllowed = !crewId && !isRanked;
      recurringRow.classList.toggle('hidden', !recurringAllowed);
      recurringBox.disabled = !recurringAllowed;
      if (!recurringAllowed) recurringBox.checked = false;
      modal.querySelectorAll('#ng-max option').forEach((option) => {
        option.disabled = isRanked && Number(option.value) > 4;
      });
    };
    syncRecurring();
    modal.querySelector('#ng-type').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn || btn.disabled) return;
      gameType = btn.dataset.val;
      if (gameType === 'ranked' && Number(modal.querySelector('#ng-max').value) > 4) {
        modal.querySelector('#ng-max').value = '4';
      }
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
    let preferredLevel = presetPreferredLevel;
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
      const size = crewId
        ? `${inviteIds.size + 1} accepted Crew players`
        : (players === 2 ? 'Singles' : players === 4 ? 'Doubles' : `${players} players`);
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
    const friendsWrap = modal.querySelector('#ng-friends-wrap');
    const friendsEmpty = modal.querySelector('#ng-friends-empty');
    const updateCrewPresetBanner = () => {
      const title = modal.querySelector('#ng-crew-title');
      const copy = modal.querySelector('#ng-crew-copy');
      if (!title || !copy) return;
      if (crewId) {
        title.textContent = `${crewName || 'Your Crew'} · ${inviteIds.size + 1} accepted player${inviteIds.size === 0 ? '' : 's'}`;
        copy.textContent = `🔒 Private to all ${inviteIds.size + 1} accepted crew members.`;
        return;
      }
      if (visibility !== 'private') {
        if (crewName) title.textContent = crewName;
        else title.textContent = 'Crew selection saved';
        copy.textContent = 'Switch back to Specific to invite these teammates directly.';
        return;
      }
      title.textContent = inviteIds.size
        ? `${crewName || 'Same crew'} · ${inviteIds.size} teammate${inviteIds.size === 1 ? '' : 's'}`
        : 'No teammates selected';
      const matchesOriginal = inviteIds.size === initialInviteIds.size
        && [...inviteIds].every((id) => initialInviteIds.has(id));
      copy.textContent = matchesOriginal && availabilityLabel
        ? availabilityLabel
        : (inviteIds.size
            ? `${inviteIds.size} teammate${inviteIds.size === 1 ? '' : 's'} will get a direct invite.`
            : 'Select at least one available teammate to keep this private.');
    };
    modal.querySelector('#ng-vis')?.addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      if (crewId && btn.dataset.vis !== 'private') return;
      visibility = btn.dataset.vis;
      modal.querySelectorAll('#ng-vis button').forEach((b) => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-pressed', String(active));
      });
      friendsWrap.classList.toggle('hidden', visibility !== 'private');
      friendsEmpty?.classList.toggle('hidden', visibility !== 'friends' || friends.length > 0);
      updateCrewPresetBanner();
      markPlannerDirty();
    });
    const invitesEl = modal.querySelector('#ng-invites');
    if (invitesEl) {
      invitesEl.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-fid]');
        if (!btn || btn.disabled || crewId) return;
        const fid = Number(btn.dataset.fid);
        if (inviteIds.has(fid)) inviteIds.delete(fid); else inviteIds.add(fid);
        btn.classList.toggle('active', inviteIds.has(fid));
        btn.setAttribute('aria-pressed', String(inviteIds.has(fid)));
        modal.querySelector('#ng-invite-hint').textContent = inviteIds.size
          ? `${inviteIds.size} invited — only they will see this game.`
          : 'Pick who to invite — only they will see this game.';
        updateCrewPresetBanner();
        markPlannerDirty();
      });
    }

    const applyFreshCrewRoster = (detail) => {
      if (!crewId || !detail || !Array.isArray(detail.members)) return false;
      const summary = crewSummaryFrom(detail);
      const freshInvitees = detail.members
        .map(sanitizePlannerInvitee).filter(Boolean)
        .filter((person) => !state.me || person.id !== state.me.id);
      invitePeople.splice(0, invitePeople.length, ...freshInvitees);
      initialInviteIds.clear();
      inviteIds.clear();
      freshInvitees.forEach((person) => {
        initialInviteIds.add(person.id);
        inviteIds.add(person.id);
      });
      if (summary) {
        crewVersion = summary.roster_version;
        crewName = summary.name;
      }
      sourceLabel = `${crewName || 'Your Crew'} · ${freshInvitees.length} accepted teammate${freshInvitees.length === 1 ? '' : 's'}`;
      availabilityLabel = `Accepted Crew roster snapshot${crewVersion == null ? '' : ` v${crewVersion}`}`;

      if (invitesEl) {
        invitesEl.innerHTML = freshInvitees.map((person) => inviteChipHtml(person, true)).join('');
      }
      const avatars = modal.querySelector('.planner-crew-avatars');
      if (avatars) avatars.innerHTML = freshInvitees.slice(0, 4).map((person) => avatarHtml(person, 'sm')).join('');
      const inviteHint = modal.querySelector('#ng-invite-hint');
      if (inviteHint) inviteHint.textContent = `${freshInvitees.length} accepted teammate${freshInvitees.length === 1 ? '' : 's'} — everyone shown is included.`;

      const playerCount = freshInvitees.length + 1;
      const nextCapacity = [2, 4, 6, 8, 10, 12].find((count) => count >= playerCount) || 12;
      modal.querySelector('#ng-max').value = String(nextCapacity);
      const capacity = modal.querySelector('#ng-crew-capacity');
      if (capacity) capacity.textContent = `All ${playerCount} accepted players are included. Capacity is fixed to this roster.`;
      const privateSummary = modal.querySelector('#ng-crew-private');
      if (privateSummary) privateSummary.innerHTML = `<b>🔒 Private to ${esc(crewName || 'your crew')}</b><span>All ${playerCount} accepted players are included.</span>`;
      const rankedButton = modal.querySelector('#ng-type button[data-val="ranked"]');
      const rankedEligible = [2, 4].includes(playerCount);
      if (rankedButton) {
        rankedButton.disabled = !rankedEligible;
        rankedButton.title = rankedEligible ? '' : 'Ranked Crew games need exactly 2 or 4 accepted players';
      }
      if (!rankedEligible && gameType === 'ranked') {
        gameType = 'casual';
        modal.querySelectorAll('#ng-type button').forEach((button) => {
          const active = button.dataset.val === 'casual';
          button.classList.toggle('active', active);
          button.setAttribute('aria-pressed', String(active));
        });
      }
      syncRecurring();
      updateOptionsSummary();
      updateCrewPresetBanner();
      modal.querySelector('#ng-invite-warning')?.remove();
      return playerCount >= 2 && playerCount <= 12;
    };

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
      otherTimeDetails.open = customMode;
      modal.querySelectorAll('#ng-smart-times button').forEach((btn) => {
        const active = !customMode
          && Number(btn.dataset.smartDay) === selDayIdx
          && Number(btn.dataset.smartHour) === selHour;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', String(active));
      });

      const restoredCrewSize = crewId ? initialInviteIds.size + 1 : null;
      gameType = crewId && ![2, 4].includes(restoredCrewSize) && restoredDraft.gameType === 'ranked'
        ? 'casual' : restoredDraft.gameType;
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
      const restoredMax = crewId
        ? ([2, 4, 6, 8, 10, 12].find((count) => count >= restoredCrewSize) || 12)
        : (gameType === 'ranked' && ![2, 4].includes(restoredDraft.maxPlayers)
            ? 4 : restoredDraft.maxPlayers);
      modal.querySelector('#ng-max').value = String(restoredMax);

      visibility = crewId ? 'private' : restoredDraft.visibility;
      modal.querySelectorAll('#ng-vis button').forEach((btn) => {
        const active = btn.dataset.vis === visibility;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', String(active));
      });
      friendsWrap.classList.toggle('hidden', visibility !== 'private');
      friendsEmpty?.classList.toggle('hidden', visibility !== 'friends' || friends.length > 0);
      const currentInviteeIds = new Set(invitePeople.map((person) => person.id));
      inviteIds.clear();
      const restoredInviteIds = crewId
        ? [...currentInviteeIds]
        : restoredDraft.inviteUserIds.filter((id) => currentInviteeIds.has(id));
      restoredInviteIds.forEach((id) => inviteIds.add(id));
      invitesEl?.querySelectorAll('[data-fid]').forEach((btn) => {
        const active = inviteIds.has(Number(btn.dataset.fid));
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', String(active));
      });
      if (visibility === 'private' && restoredDraft.inviteUserIds.length !== inviteIds.size) {
        setPlannerWarning('ng-invite-warning', crewId
          ? 'This saved Crew snapshot is incomplete. The roster will refresh before it can be scheduled.'
          : 'Some saved invitees are no longer available. Review who can see this game.', friendsWrap);
      }
      if (modal.querySelector('#ng-invite-hint')) {
        modal.querySelector('#ng-invite-hint').textContent = crewId
          ? `${inviteIds.size} accepted teammate${inviteIds.size === 1 ? '' : 's'} — everyone shown is included.`
          : (inviteIds.size
              ? `${inviteIds.size} invited — only they will see this game.`
              : 'Pick who to invite — only they will see this game.');
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
      recurringBox.checked = !crewId && gameType !== 'ranked' && restoredDraft.recurrence === 'weekly';
      modal.querySelector('#ng-notes').value = restoredDraft.notes;
      modal.querySelector('#ng-advanced').open = restoredDraft.advancedOpen;
      syncRecurring();
      updateOptionsSummary();
      updateCrewPresetBanner();
      updateBusyHint();
      updatePlannerSummary();
    }

    let plannerStep = restoredDraft ? (crewId ? 'when' : 'who') : 'where';
    const syncPlannerStep = ({ focus = false } = {}) => {
      const whereStep = modal.querySelector('#ng-step-where');
      const whenStep = modal.querySelector('#ng-step-when');
      const whoStep = modal.querySelector('#ng-step-who');
      const whereAnswer = modal.querySelector('#ng-answer-where');
      const whenAnswer = modal.querySelector('#ng-answer-when');
      const finalStep = crewId ? plannerStep === 'when' : plannerStep === 'who';
      updatePlannerSummary();
      whereStep.classList.toggle('hidden', plannerStep !== 'where');
      whenStep.classList.toggle('hidden', plannerStep !== 'when');
      whoStep.classList.toggle('hidden', plannerStep !== 'who' || !!crewId);
      whereAnswer.classList.toggle('hidden', plannerStep === 'where');
      whenAnswer.classList.toggle('hidden', plannerStep !== 'who');
      modal.querySelector('#ng-advanced').classList.toggle('hidden', !finalStep);
      modal.querySelector('.planner-submit-bar').classList.toggle('hidden', !finalStep);
      [whereStep, whenStep, whoStep].forEach((step) => {
        step.setAttribute('aria-hidden', String(step.classList.contains('hidden')));
      });
      [whereAnswer, whenAnswer].forEach((answer) => {
        answer.setAttribute('aria-hidden', String(answer.classList.contains('hidden')));
      });
      if (focus) {
        const visible = plannerStep === 'where' ? whereStep : plannerStep === 'when' ? whenStep : whoStep;
        const title = visible.querySelector('.planner-step-title');
        title?.setAttribute('tabindex', '-1');
        title?.focus({ preventScroll: true });
      }
    };
    const chosenPlannerTime = () => {
      if (customMode) {
        const raw = modal.querySelector('#ng-when').value;
        return raw ? new Date(raw) : null;
      }
      if (selHour == null) return null;
      const value = new Date(days[selDayIdx]);
      value.setHours(selHour, 0, 0, 0);
      return value;
    };
    modal.querySelector('#ng-next-when').addEventListener('click', () => {
      if (!modal.querySelector('#ng-court-id').value) {
        modal.querySelector('#ng-court-search').focus();
        return;
      }
      plannerStep = 'when';
      syncPlannerStep({ focus: true });
    });
    modal.querySelector('#ng-back-where').addEventListener('click', () => {
      plannerStep = 'where';
      syncPlannerStep({ focus: true });
    });
    modal.querySelector('#ng-next-who')?.addEventListener('click', () => {
      const selectedTime = chosenPlannerTime();
      if (!selectedTime || !Number.isFinite(selectedTime.getTime()) || selectedTime.getTime() <= Date.now()) {
        setPlannerWarning('ng-time-warning', 'Choose a future time.');
        (customMode ? modal.querySelector('#ng-when') : modal.querySelector('#ng-smart-times button.active'))?.focus();
        return;
      }
      plannerStep = 'who';
      syncPlannerStep({ focus: true });
    });
    modal.querySelector('#ng-back-when')?.addEventListener('click', () => {
      plannerStep = 'when';
      syncPlannerStep({ focus: true });
    });
    syncPlannerStep();

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
    if (plannerSubmitting && !frozenSubmitPayload && restoredDraft && restoredDraft.scheduledAt) {
      // Pre-immutable scheduled drafts can be reconstructed canonically. Old
      // "right now" drafts did not retain their generated timestamp and must
      // never manufacture a different request under the original key.
      frozenSubmitPayload = sanitizeGameCreatePayload({
        court_id: restoredDraft.courtId,
        scheduled_at: restoredDraft.scheduledAt,
        game_type: restoredDraft.gameType,
        visibility: restoredDraft.visibility,
        recurrence: restoredDraft.recurrence,
        max_players: restoredDraft.maxPlayers,
        preferred_level: restoredDraft.preferredLevel,
        notes: restoredDraft.notes,
        invite_user_ids: restoredDraft.visibility === 'private' ? restoredDraft.inviteUserIds : [],
        require_all_invitees: restoredDraft.visibility === 'private' && restoredDraft.requireAllInvitees,
        source_game_id: restoredDraft.sourceGameId,
        club_id: restoredDraft.clubId,
        crew_id: restoredDraft.crewId,
        expected_crew_version: restoredDraft.crewVersion,
        client_attempt_id: plannerAttemptId,
      }, plannerAttemptId);
    }
    if (plannerSubmitting) {
      modal.querySelectorAll('.planner-step, .planner-advanced, .planner-submit-bar')
        .forEach((section) => section.setAttribute('inert', ''));
      const retry = modal.querySelector('#ng-retry-exact');
      if (retry && !frozenSubmitPayload) {
        retry.disabled = true;
        retry.textContent = 'Check My games';
      }
    }
    if (plannerSubmitting || protectedSubmittingDraft) modal.querySelector('#ng-submit').disabled = true;
    modal.querySelector('#ng-retry-exact')?.addEventListener('click', () => {
      if (!frozenSubmitPayload) return;
      exactRetryRequested = true;
      modal.querySelectorAll('.planner-step, .planner-advanced, .planner-submit-bar')
        .forEach((section) => section.removeAttribute('inert'));
      const submit = modal.querySelector('#ng-submit');
      submit.disabled = false;
      submit.click();
    });

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
      const exactRetry = exactRetryRequested;
      exactRetryRequested = false;
      if (protectedSubmittingDraft) {
        showPlannerSubmitError('Check the unconfirmed game before starting another plan.');
        return;
      }
      const exactPayload = exactRetry
        ? sanitizeGameCreatePayload(frozenSubmitPayload, plannerAttemptId) : null;
      if (exactRetry && !exactPayload) {
        showPlannerSubmitError('This plan can’t be confirmed here. Check My games before starting a new plan.');
        return;
      }
      const courtId = exactRetry ? exactPayload.court_id : modal.querySelector('#ng-court-id').value;
      if (!exactRetry && !courtId) { showPlannerSubmitError('Pick a court first.', modal.querySelector('#ng-court-search')); return; }
      let scheduledAt;
      if (exactRetry) {
        scheduledAt = new Date(exactPayload.scheduled_at);
      } else if (customMode) {
        const v = modal.querySelector('#ng-when').value;
        if (!v) { showPlannerSubmitError('Pick a date and time.', modal.querySelector('#ng-when')); return; }
        scheduledAt = new Date(v);
        if (!Number.isFinite(scheduledAt.getTime())) { showPlannerSubmitError('Choose a valid date and time.', modal.querySelector('#ng-when')); return; }
      } else {
        if (selHour == null) { showPlannerSubmitError('Pick a time.', modal.querySelector('#ng-smart-times button')); return; }
        scheduledAt = new Date(days[selDayIdx]);
        scheduledAt.setHours(selHour, 0, 0, 0);
      }
      if (!exactRetry && scheduledAt.getTime() <= Date.now()) {
        setPlannerWarning('ng-time-warning', 'That time has passed. Choose a future time.');
        showPlannerSubmitError('Choose a future time.', customMode ? modal.querySelector('#ng-when') : modal.querySelector('#ng-smart-times button.active'));
        return;
      }
      if (!exactRetry && visibility === 'private' && inviteIds.size === 0) {
        showPlannerSubmitError('Pick at least one person to invite.', modal.querySelector('#ng-invites button'));
        return;
      }
      const chosenCapacity = Number(modal.querySelector('#ng-max').value);
      const effectiveCapacity = gameType === 'ranked' ? (chosenCapacity <= 2 ? 2 : 4) : chosenCapacity;
      const plannedPlayerCount = crewId ? invitePeople.length + 1 : inviteIds.size + 1;
      const selectedInviteesExceedCapacity = inviteIds.size + 1 > effectiveCapacity;
      const plannedRosterExceedsCapacity = crewId
        ? plannedPlayerCount > effectiveCapacity : selectedInviteesExceedCapacity;
      if (!exactRetry && visibility === 'private' && plannedRosterExceedsCapacity) {
        showPlannerSubmitError(
          crewId
            ? `This Crew has ${plannedPlayerCount} accepted players. Crew games support up to 12 players.`
            : `This crew needs room for ${plannedPlayerCount} players. Increase Players needed or remove someone.`,
          modal.querySelector('#ng-max'),
        );
        return;
      }
      if (!exactRetry && clubId && visibility === 'private') {
        showPlannerSubmitError("Club games can't be invite-only — the club needs to see it.", modal.querySelector('#ng-vis button[data-vis="friends"]'));
        return;
      }
      const requestPayload = exactPayload || sanitizeGameCreatePayload({
        court_id: Number(courtId),
        scheduled_at: scheduledAt.toISOString(),
        game_type: gameType,
        visibility,
        recurrence: crewId ? 'none' : (recurringBox.checked ? 'weekly' : 'none'),
        max_players: Number(modal.querySelector('#ng-max').value),
        preferred_level: preferredLevel,
        notes: modal.querySelector('#ng-notes').value.trim(),
        invite_user_ids: visibility === 'private' ? [...inviteIds] : [],
        require_all_invitees: visibility === 'private' && requireAllInvitees,
        source_game_id: sourceGameId,
        club_id: clubId,
        crew_id: crewId,
        expected_crew_version: crewVersion,
        client_attempt_id: plannerAttemptId,
      }, plannerAttemptId);
      if (!requestPayload) {
        showPlannerSubmitError('Review the court and time, then try again.');
        return;
      }
      const btn = e.currentTarget;
      if (btn.disabled) return;
      btn.disabled = true;
      btn.textContent = 'Scheduling…';
      plannerSubmitting = true;
      plannerSubmitStartedAt ||= Date.now();
      frozenSubmitPayload = requestPayload;
      if (!flushPlannerDraft('submitting')) {
        plannerSubmitting = false;
        plannerSubmitStartedAt = null;
        frozenSubmitPayload = null;
        btn.disabled = false;
        btn.textContent = 'Schedule game';
        showPlannerSubmitError('Nothing was sent because this browser could not save your plan. Free up browser storage, then try again.');
        return;
      }
      modal.classList.add('planner-submitting');
      modal.querySelector('.modal')?.setAttribute('aria-busy', 'true');
      modal.querySelectorAll('.planner-step, .planner-advanced, .planner-submit-bar')
        .forEach((section) => section.setAttribute('inert', ''));
      try {
        const createdGame = await api('/games', {
          method: 'POST',
          body: JSON.stringify(requestPayload),
        });
        plannerSubmitting = false;
        plannerSubmitted = true;
        clearGameDraft();
        closeModal(modal);
        toast("Game scheduled — let's fill the roster 🏓");
        state.playGamesCache = null;
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
        // Creation is the start of assembling the game, not the end. Keep the
        // host on the roster where invite/share/court-post actions are visible.
        openGameScreen(createdGame.id);
      } catch (err) {
        if (err.code === 'crew_not_found' && crewId) {
          plannerSubmitting = false;
          plannerSubmitStartedAt = null;
          frozenSubmitPayload = null;
          plannerDirty = false;
          plannerSubmitted = true;
          clearGameDraft();
          modal.classList.remove('planner-submitting');
          modal.querySelector('.modal')?.removeAttribute('aria-busy');
          modal.querySelectorAll('.planner-step, .planner-advanced, .planner-submit-bar')
            .forEach((section) => section.removeAttribute('inert'));
          const submitButton = modal.querySelector('#ng-submit');
          submitButton.disabled = true;
          submitButton.textContent = 'Crew unavailable';
          const message = 'You no longer have access to this Crew. This saved plan was cleared.';
          setPlannerWarning('ng-invite-warning', message, friendsWrap);
          showPlannerSubmitError(message, null);
          return;
        }
        if (err.code === 'crew_changed' && crewId) {
          plannerSubmitting = false;
          plannerSubmitStartedAt = null;
          frozenSubmitPayload = null;
          exactRetryRequested = false;
          const refreshedAttemptId = newGameAttemptId();
          plannerAttemptId = refreshedAttemptId;
          plannerDirty = true;
          modal.querySelector('.planner-recovery.warn')?.remove();
          modal.classList.remove('planner-submitting');
          modal.querySelector('.modal')?.removeAttribute('aria-busy');
          modal.querySelectorAll('.planner-step, .planner-advanced, .planner-submit-bar')
            .forEach((section) => section.removeAttribute('inert'));
          const submitButton = modal.querySelector('#ng-submit');
          submitButton.disabled = true;
          submitButton.textContent = 'Refreshing Crew roster…';
          try {
            // Never trust only the version returned by the 409: the accepted
            // names and IDs are the privacy boundary. Refresh the full detail
            // before this planner is allowed to submit a different attempt.
            const detail = await api(`/crews/${crewId}`);
            if (!document.body.contains(modal)) return;
            const schedulable = applyFreshCrewRoster(detail);
            const playerCount = invitePeople.length + 1;
            const message = schedulable
              ? `Crew roster refreshed to ${playerCount} accepted player${playerCount === 1 ? '' : 's'}. Review the full snapshot, then schedule again.`
              : (playerCount < 2
                  ? 'This Crew needs another accepted player before it can plan a game.'
                  : 'This Crew is too large to schedule as one game.');
            setPlannerWarning('ng-invite-warning', message, friendsWrap);
            submitButton.disabled = !schedulable;
            submitButton.textContent = 'Schedule game';
            showPlannerSubmitError(message, null);
            flushPlannerDraft('editing');
          } catch (refreshError) {
            if (!document.body.contains(modal)) return;
            const message = 'The Crew changed, but the accepted roster could not be refreshed. Close this planner and try again when connected.';
            setPlannerWarning('ng-invite-warning', message, friendsWrap);
            submitButton.disabled = true;
            submitButton.textContent = 'Crew refresh needed';
            showPlannerSubmitError(message, null);
            flushPlannerDraft('editing');
          }
          return;
        }
        if (err.code === 'crew_changed') {
          const unavailableIds = new Set((err.data && err.data.unavailable_user_ids || []).map(Number));
          const unavailableNames = invitePeople
            .filter((person) => unavailableIds.has(person.id))
            .map((person) => person.display_name.split(' ')[0]);
          unavailableIds.forEach((id) => inviteIds.delete(id));
          invitesEl?.querySelectorAll('[data-fid]').forEach((chip) => {
            if (!unavailableIds.has(Number(chip.dataset.fid))) return;
            chip.classList.remove('active');
            chip.setAttribute('aria-pressed', 'false');
            chip.disabled = true;
            chip.title = 'No longer available for this invite';
          });
          const message = unavailableNames.length
            ? `${unavailableNames.join(', ')} ${unavailableNames.length === 1 ? 'is' : 'are'} no longer available for this invite. Review the crew, then schedule again.`
            : 'The crew changed while this plan was open. Review the selected players, then schedule again.';
          setPlannerWarning('ng-invite-warning', message, friendsWrap);
          if (modal.querySelector('#ng-invite-hint')) {
            modal.querySelector('#ng-invite-hint').textContent = inviteIds.size
              ? `${inviteIds.size} invited — only they will see this game.`
              : 'Pick at least one available player to continue.';
          }
          updateCrewPresetBanner();
          plannerSubmitting = false;
          plannerSubmitStartedAt = null;
          frozenSubmitPayload = null;
          plannerDirty = true;
          modal.querySelector('.planner-recovery.warn')?.remove();
          modal.classList.remove('planner-submitting');
          modal.querySelector('.modal')?.removeAttribute('aria-busy');
          modal.querySelectorAll('.planner-step, .planner-advanced, .planner-submit-bar')
            .forEach((section) => section.removeAttribute('inert'));
          const btn = modal.querySelector('#ng-submit');
          btn.disabled = false;
          btn.textContent = 'Schedule game';
          showPlannerSubmitError(message, inviteIds.size ? null : modal.querySelector('#ng-invites button:not(:disabled)'));
          flushPlannerDraft('editing');
          return;
        }
        if (err.isStaleSession) return;
        if (err.code === 'client_attempt_id_conflict') {
          const existingGameId = Number(err.data && err.data.existing_game_id);
          if (Number.isSafeInteger(existingGameId) && existingGameId > 0) {
            plannerSubmitting = false;
            plannerSubmitted = true;
            clearGameDraft();
            closeModal(modal);
            toast('That game already exists — opening it');
            state.playGamesCache = null;
            refreshMe();
            openGameScreen(existingGameId);
            return;
          }
          // Older servers may not include the creator-owned game id. Keep the
          // exact key frozen and never manufacture a second create attempt.
          plannerDirty = true;
          flushPlannerDraft('submitting');
          closeModal(modal);
          toast('This game may already exist — check My games');
          return;
        }
        const ambiguous = err.isNetworkError || Number(err.status) === 429 || Number(err.status) >= 500;
        if (ambiguous) {
          plannerDirty = true;
          // A timeout or interrupted response may still have created the game.
          // Keep the exact request frozen; reopening can only replay this body.
          flushPlannerDraft('submitting');
          closeModal(modal);
          toast('We couldn’t confirm the game — reopen Plan a game to check it');
          return;
        }

        // A known non-conflict 4xx means this request did not create a new game. Return to
        // editing in place instead of trapping the player in an ambiguity loop.
        plannerSubmitting = false;
        plannerSubmitStartedAt = null;
        frozenSubmitPayload = null;
        plannerDirty = true;
        modal.querySelector('.planner-recovery.warn')?.remove();
        modal.classList.remove('planner-submitting');
        modal.querySelector('.modal')?.removeAttribute('aria-busy');
        modal.querySelectorAll('.planner-step, .planner-advanced, .planner-submit-bar')
          .forEach((section) => section.removeAttribute('inert'));
        const submit = modal.querySelector('#ng-submit');
        submit.disabled = false;
        submit.textContent = 'Schedule game';
        showPlannerSubmitError(err.message);
        flushPlannerDraft('editing');
      }
    });

    // Editable restored Crew drafts refresh opportunistically before the next
    // submit. A submitting draft stays frozen so an interrupted, already-
    // committed request can still replay byte-for-byte first.
    if (crewId && restoredDraft && !plannerSubmitting) {
      api(`/crews/${crewId}`).then((detail) => {
        if (!document.body.contains(modal) || plannerSubmitting || plannerSubmitted) return;
        const schedulable = applyFreshCrewRoster(detail);
        if (!schedulable) {
          const count = invitePeople.length + 1;
          const message = count < 2
            ? 'This Crew needs another accepted player before it can plan a game.'
            : 'This Crew is too large to schedule as one game.';
          setPlannerWarning('ng-invite-warning', message, friendsWrap);
          modal.querySelector('#ng-submit').disabled = true;
        }
        plannerDirty = true;
        flushPlannerDraft('editing');
      }).catch((error) => {
        if (!document.body.contains(modal) || plannerSubmitting || plannerSubmitted) return;
        if (error.code !== 'crew_not_found') return;
        plannerDirty = false;
        plannerSubmitted = true;
        clearGameDraft();
        const message = 'You no longer have access to this Crew. This saved plan was cleared.';
        setPlannerWarning('ng-invite-warning', message, friendsWrap);
        const submit = modal.querySelector('#ng-submit');
        submit.disabled = true;
        submit.textContent = 'Crew unavailable';
        showPlannerSubmitError(message, null);
      });
    }
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
      if (document.hidden || state.connectionState === 'offline' || currentOverlayEntry()?.el !== modal) return;
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
    }, LIVE_DETAIL_POLL_INTERVAL_MS);

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

  function competitionDetailTabsHtml(items) {
    return `<nav class="competition-detail-nav competition-detail-tabs" role="tablist" aria-label="Competition sections">
      ${items.map(([target, label], index) => `<button type="button" class="btn btn-secondary btn-sm" id="${target}-tab" role="tab" data-competition-tab="${target}" aria-controls="${target}-panel" aria-selected="${index === 0}" tabindex="${index === 0 ? '0' : '-1'}">${label}</button>`).join('')}
    </nav>`;
  }

  function bindCompetitionDetailTabs(root, initialTarget = null) {
    const tabs = [...root.querySelectorAll('[data-competition-tab]')];
    const markers = tabs.map((tab) => root.querySelector(`#${CSS.escape(tab.dataset.competitionTab)}`));
    if (!tabs.length || markers.some((marker) => !marker)) return;

    markers.forEach((marker, index) => {
      const target = tabs[index].dataset.competitionTab;
      const nextMarker = markers[index + 1] || null;
      const panel = document.createElement('section');
      panel.id = `${target}-panel`;
      panel.className = 'competition-detail-panel';
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', tabs[index].id);
      marker.before(panel);
      let node = marker;
      while (node && node !== nextMarker) {
        const next = node.nextSibling;
        panel.appendChild(node);
        node = next;
      }
    });

    const activate = (target, { focus = false } = {}) => {
      const selected = tabs.find((tab) => tab.dataset.competitionTab === target) || tabs[0];
      tabs.forEach((tab) => {
        const active = tab === selected;
        tab.setAttribute('aria-selected', String(active));
        tab.tabIndex = active ? 0 : -1;
        root.querySelector(`#${CSS.escape(tab.getAttribute('aria-controls'))}`).hidden = !active;
      });
      if (focus) selected.focus({ preventScroll: true });
    };
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => activate(tab.dataset.competitionTab));
      tab.addEventListener('keydown', (event) => {
        let nextIndex = null;
        if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
        else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === 'Home') nextIndex = 0;
        else if (event.key === 'End') nextIndex = tabs.length - 1;
        if (nextIndex == null) return;
        event.preventDefault();
        activate(tabs[nextIndex].dataset.competitionTab, { focus: true });
      });
    });
    activate(initialTarget);
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

      let html = '<button class="btn btn-primary btn-block" id="competition-create" style="margin:2px 0 14px">＋ Create competition</button>';
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

      if (state.playSeg !== 'brackets') return; // user already switched away
      el.innerHTML = html;
      el.querySelector('#competition-create').addEventListener('click', openCompetitionCreateSheet);
      el.querySelectorAll('[data-open-league]').forEach((card) => {
        makePressable(card, () => openLeagueScreen(Number(card.dataset.openLeague)));
      });
      el.querySelectorAll('[data-open-tournament]').forEach((card) => {
        makePressable(card, () => openTournamentScreen(Number(card.dataset.openTournament)));
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

  function tournamentCheckinState(tournament) {
    const myEntry = (tournament.entries || []).find((entry) => entry.id === tournament.my_entry_id) || null;
    const startsAt = new Date(tournament.starts_at).getTime();
    const open = Number.isFinite(startsAt)
      && Date.now() >= startsAt - 24 * 3600e3
      && (tournament.status === 'registration' || tournament.status === 'active');
    return { myEntry, canCheckIn: !!(myEntry && open && !myEntry.checked_in) };
  }

  function competitionActionNeeded(kind, parent) {
    const actions = (parent.matches || []).map((match) => {
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
    }).filter(Boolean);

    if (kind === 'league' && parent.status === 'registration') {
      if (parent.is_organizer && Number(parent.member_count) >= 3) {
        actions.push({
          action: 'start', priority: -1, title: 'Start the league',
          detail: `${parent.member_count} players are ready. Seed the boxes and begin round 1.`,
          label: 'Start league', controlId: 'lg-start', targetTab: 'lg-standings', direct: true,
        });
      } else if (!parent.joined && Number(parent.member_count) < Number(parent.max_players)) {
        actions.push({
          action: 'join', priority: 4, title: 'Join this league',
          detail: 'Claim a place before registration fills.',
          label: 'Join league', controlId: 'lg-join', targetTab: 'lg-standings', direct: true,
        });
      }
    }

    if (kind === 'tournament') {
      const { canCheckIn } = tournamentCheckinState(parent);
      if (canCheckIn) {
        actions.push({
          action: 'checkin', priority: -2, title: 'Check in at the court',
          detail: 'Let the organizer know your entry is here.',
          label: 'Check in', controlId: 'td-checkin', targetTab: 'td-players', direct: true,
        });
      }
      if (parent.status === 'registration' && parent.is_organizer && Number(parent.entry_count) >= 2) {
        actions.push({
          action: 'start', priority: -1, title: 'Start the tournament',
          detail: `${parent.entry_count} ${parent.event_type === 'doubles' ? 'teams are' : 'players are'} ready. Generate the matches now.`,
          label: 'Start tournament', controlId: 'td-start', targetTab: 'td-players', direct: true,
        });
      } else if (parent.status === 'registration' && !parent.my_entry_id
          && Number(parent.entry_count) < Number(parent.max_entries)) {
        const needsPartner = parent.event_type === 'doubles';
        actions.push({
          action: 'register', priority: 4,
          title: needsPartner ? 'Choose a partner and register' : 'Register for this tournament',
          detail: `${Number(parent.max_entries) - Number(parent.entry_count)} spot${Number(parent.max_entries) - Number(parent.entry_count) === 1 ? '' : 's'} left.`,
          label: needsPartner ? 'Choose partner' : 'Register', controlId: 'td-register',
          focusId: needsPartner ? 'td-partner' : null, targetTab: 'td-players', direct: !needsPartner,
        });
      }
    }

    return actions.sort((a, b) => a.priority - b.priority
      || Number(a.match?.id || 0) - Number(b.match?.id || 0));
  }

  function competitionActionNeededHtml(kind, parent) {
    const next = competitionActionNeeded(kind, parent)[0];
    if (!next) return '';
    if (!next.match) {
      return `
        <section class="competition-actions" aria-labelledby="${kind}-actions-title">
          <div class="section-label" id="${kind}-actions-title">Your next action</div>
          <button type="button" class="card competition-action-card competition-global-action" data-competition-global-action="${next.action}" data-competition-control="${next.controlId}" data-competition-target="${next.targetTab}" data-competition-direct="${next.direct}" ${next.focusId ? `data-competition-focus="${next.focusId}"` : ''} style="width:100%;text-align:left">
            <span class="row-main">
              <span class="row-title" style="display:block">${esc(next.title)}</span>
              <span class="row-sub" style="display:block">${esc(next.detail)}</span>
            </span>
            <span class="tag live" style="margin:0">${esc(next.label)}</span>
          </button>
        </section>`;
    }
    const { match, title, detail } = next;
    const context = competitionMatchContext(kind, parent, match);
    return `
      <section class="competition-actions" aria-labelledby="${kind}-actions-title">
        <div class="section-label" id="${kind}-actions-title">Your next action</div>
        <div class="card competition-action-card" data-result-match="${match.id}" data-match-key="${match.id}">
          <div class="row-main">
            <div class="row-title">${esc(title)}</div>
            <div class="row-sub">${esc(competitionSideName(context.side1))} vs ${esc(competitionSideName(context.side2))} · ${esc(detail)}</div>
          </div>
          <span class="chev" aria-hidden="true">›</span>
        </div>
      </section>`;
  }

  function bindCompetitionGlobalAction(root) {
    root.querySelectorAll('[data-competition-global-action]').forEach((button) => {
      button.addEventListener('click', () => {
        const control = root.querySelector(`#${CSS.escape(button.dataset.competitionControl)}`);
        if (!control) return;
        const direct = button.dataset.competitionDirect === 'true' && !control.disabled;
        if (direct) {
          control.click();
          return;
        }
        root.querySelector(`[data-competition-tab="${button.dataset.competitionTarget}"]`)?.click();
        const focusId = button.dataset.competitionFocus;
        (focusId ? root.querySelector(`#${CSS.escape(focusId)}`) : control)?.focus({ preventScroll: true });
      });
    });
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
      activeCompetitionTab: modal?.querySelector('[data-competition-tab][aria-selected="true"]')?.dataset.competitionTab || null,
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
    const box = openModal(modalHead('League'), { route, label: 'League', page: true });
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
      const leagueNav = [['lg-overview', 'Overview']];
      if (lg.status === 'active' || lg.status === 'completed') leagueNav.push(['lg-matches', 'Matches'], ['lg-standings', 'Standings']);
      else if (lg.status === 'registration') leagueNav.push(['lg-standings', 'Players']);
      if (lg.joined) leagueNav.push(['lg-chat', 'Chat']);
      const currentRoundMatches = (lg.matches || []).filter((match) => match.round === lg.current_round);
      const nextActionHtml = competitionActionNeededHtml('league', { ...lg, matches: currentRoundMatches });
      let body = `
        ${modalHead(`📦 ${lg.name}`)}
        <div class="row-sub" style="margin:-6px 0 6px">${lg.court ? `${esc(lg.court.name)} · ` : ''}${lg.member_count} player${lg.member_count === 1 ? '' : 's'} · boxes of ${lg.box_size} · new round every ${lg.round_days} days</div>
        ${nextActionHtml}
        ${competitionDetailTabsHtml(leagueNav)}
        <div id="lg-overview" tabindex="-1" style="margin-bottom:12px">${statusChip}${lg.club_name ? ` <span class="tag" style="margin:0 0 0 4px">🏛 ${esc(lg.club_name)}</span>` : ''}</div>
        ${lg.description ? `<div class="row-sub" style="margin-bottom:12px">${esc(lg.description)}</div>` : ''}`;

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
        body += '<div class="section-label" id="lg-standings" tabindex="-1">Signed up</div>';
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
        const roundMatches = currentRoundMatches;
        body += '<div id="lg-matches" tabindex="-1"></div>';
        const matchesByBox = {};
        roundMatches.forEach((match) => {
          (matchesByBox[match.box] = matchesByBox[match.box] || []).push(match);
        });
        const matchBoxes = Object.keys(matchesByBox).sort((a, b) => a - b);
        if (matchBoxes.length) {
          matchBoxes.forEach((boxNumber) => {
            body += `<div class="section-label">📦 Box ${boxNumber} matches</div>`;
            body += matchesByBox[boxNumber].map((match) => leagueMatchCardHtml(match, {
              mine: match.player1?.id === myId || match.player2?.id === myId,
            })).join('');
          });
        } else {
          body += '<div class="empty-state" style="padding:16px">No matches are scheduled for this round.</div>';
        }

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

        const boxes = {};
        lg.members.forEach((member) => { if (member.box) (boxes[member.box] = boxes[member.box] || []).push(member); });
        body += '<div id="lg-standings" tabindex="-1"></div>';
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
        });
      }

      if (lg.joined) {
        body += `<button class="btn btn-secondary btn-block" id="lg-chat" style="margin-bottom:10px;position:relative">💬 Open league chat${lg.chat_unread ? ` <span class="badge" style="position:static;margin-left:6px">${lg.chat_unread > 9 ? '9+' : lg.chat_unread}</span>` : ''}</button>`;
      }

      content.innerHTML = body;
      setDialogLabel(content, 'League');
      bindCompetitionDetailTabs(
        content, snapshot?.activeCompetitionTab || (requestedMatchId ? 'lg-matches' : null),
      );
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
      bindCompetitionGlobalAction(content);
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
    }, COMPETITION_POLL_INTERVAL_MS);
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
          <button type="button" class="modal-close" style="font-size:18px" aria-label="Back">‹</button>
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
        <div class="chat-message-row ${mine ? 'is-mine' : 'is-theirs'}" data-message-row="${m.id}" style="display:flex;gap:8px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%">
          ${mine ? '' : `<div class="avatar sm" style="background:${esc(m.sender_color)}">${esc(initials(m.sender_name))}</div>`}
          <div class="bubble ${mine ? 'me' : 'them'}" data-message-id="${m.id}" style="max-width:100%">${m.heart_count ? `<span class="bubble-heart" data-heart-badge>❤️${m.heart_count > 1 ? ' ' + m.heart_count : ''}</span>` : ''}
            ${mine ? '' : `<div style="font-size:11px;font-weight:700;opacity:.75;margin-bottom:2px">${esc(m.sender_name)}</div>`}
            ${m.has_image ? `<div data-img-id="${m.id}" style="min-height:60px;min-width:120px;margin-bottom:${m.body ? '6px' : '0'}">⏳</div>` : ''}
            ${esc(m.body)}
            <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
          </div>
          ${chatMessageActionHtml(m, mine)}
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

    startAdaptiveChatPoll(modal, msgsEl, async () => {
      const fresh = await api(`/leagues/${lg.id}/chat?since_id=${lastId}`);
      if (fresh.items.length) renderMsgs(fresh.items, true, { newMessages: true });
      applyRoomHearts(msgsEl, fresh.heart_counts);
      return fresh.items.length > 0;
    });

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
        <div class="row-sub" id="tc-ranked-hint" style="margin-top:6px">Casual: results are recorded, with no rating changes.</div>
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
    segPick('#tc-ranked', (value) => {
      modal.querySelector('#tc-ranked-hint').textContent = value
        ? 'Ranked: every match counts toward player ratings when the tournament finishes.'
        : 'Casual: results are recorded, with no rating changes.';
    });

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

    const box = openModal(modalHead('Tournament'), { route, label: 'Tournament', page: true });
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
      const hasTournamentChat = !!(t.my_entry_id || t.is_organizer);
      const tournamentNav = [['td-overview', 'Overview']];
      if (t.status === 'active' || t.status === 'completed') {
        tournamentNav.push(['td-matches', t.format === 'single_elim' ? 'Bracket' : 'Matches']);
      }
      if (t.status !== 'cancelled') tournamentNav.push(['td-players', isDoubles ? 'Teams' : 'Players']);
      if (hasTournamentChat) tournamentNav.push(['td-chat', 'Chat']);
      const nextActionHtml = competitionActionNeededHtml('tournament', t);

      let body = `
        ${modalHead(t.name)}
        <div class="row-sub" style="margin:-6px 0 6px">${meta.map(esc).join(' · ')}</div>
        ${nextActionHtml}
        ${competitionDetailTabsHtml(tournamentNav)}
        <div id="td-overview" tabindex="-1" style="margin-bottom:12px">${tournamentStatusChip(t)}${t.club_name ? ` <span class="tag" style="margin:0 0 0 4px">🏛 ${esc(t.club_name)}</span>` : ''}</div>
        ${t.description ? `<div class="row-sub" style="margin-bottom:12px">${esc(t.description)}</div>` : ''}`;

      if (t.status === 'completed' && t.champion) {
        body += `
          <div class="card" style="text-align:center;padding:18px;background:var(--violet-50);border:1px solid var(--violet-200)">
            <div style="font-size:34px">👑</div>
            <div style="font-weight:800;font-size:17px;color:var(--violet-700)">${esc(t.champion.name)}</div>
            <div class="row-sub">Tournament champion${isDoubles ? 's' : ''}</div>
          </div>`;
      }

      const { myEntry, canCheckIn } = tournamentCheckinState(t);
      const hereTag = (en) => (en.checked_in
        ? ' <span class="tag" style="background:var(--green-100);color:var(--green-ink)">🙋 here</span>' : '');
      const checkinButton = canCheckIn
        ? '<button class="btn btn-primary btn-block" id="td-checkin" style="margin-top:12px">🙋 Check in — we\'re here</button>' : '';

      if (t.status === 'registration') {
        body += `<div class="section-label" id="td-players" tabindex="-1">Entries (${t.entry_count}/${t.max_entries})</div>`;
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
        }
        if (t.is_organizer) {
          body += `
            <div class="section-label" style="margin-top:16px">Organizer</div>
            <button class="btn btn-primary btn-block" id="td-start" ${t.entry_count < 2 ? 'disabled' : ''}>▶️ Start tournament${t.entry_count < 2 ? ' (need 2+ entries)' : ''}</button>
            <button class="btn btn-secondary btn-block" id="td-edit" style="margin-top:8px">✏️ Edit details</button>
            <button class="btn btn-secondary btn-block" id="td-cancel" style="margin-top:8px">Cancel tournament</button>`;
        }
      } else if (t.status !== 'cancelled') {
        body += '<div id="td-matches" tabindex="-1"></div>';
        body += t.format === 'round_robin' ? roundRobinHtml(t) : bracketHtml(t);
        if (t.status === 'active') {
          body += '<div class="competition-progression-note">Open any match for its result status and activity. Bracket progression waits for confirmation.</div>';
        }
        if (t.status === 'active' && t.is_organizer) {
          body += '<button class="btn btn-secondary btn-block" id="td-edit" style="margin-top:8px">✏️ Edit details</button>';
          body += '<button class="btn btn-secondary btn-block" id="td-cancel" style="margin-top:8px">Cancel tournament</button>';
        }
        body += checkinButton;
        body += `<div class="section-label" id="td-players" tabindex="-1" style="margin-top:14px">${isDoubles ? 'Teams' : 'Players'}</div>`;
        body += t.entries.map((en) => `
          <div class="card row" style="padding:8px 14px">
            ${avatarHtml(en.players[0] || {}, 'sm')}
            <div class="row-main"><div class="row-title" style="font-size:14px">${en.seed ? `<span class="bm-seed" style="margin-right:4px">${en.seed}</span>` : ''}${esc(en.name)}${hereTag(en)}</div></div>
            <div class="row-sub">${en.rating}</div>
          </div>`).join('');
      } else {
        body += '<div class="empty-state" style="padding:16px">This tournament was cancelled.</div>';
      }

      if (hasTournamentChat) {
        body += `<button class="btn btn-secondary btn-block" id="td-chat" style="margin-top:12px">💬 Open tournament chat${t.chat_unread ? ` <span class="tag live" style="margin:0 0 0 6px">${t.chat_unread > 9 ? '9+' : t.chat_unread} new</span>` : ''}</button>`;
      }

      content.innerHTML = body;
      setDialogLabel(content, 'Tournament');
      bindCompetitionDetailTabs(
        content, snapshot?.activeCompetitionTab || (requestedMatchId ? 'td-matches' : null),
      );

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
      bindCompetitionGlobalAction(content);
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
    }, COMPETITION_POLL_INTERVAL_MS);
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
          <button type="button" class="modal-close" style="font-size:18px" aria-label="Back">‹</button>
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
        <div class="chat-message-row ${mine ? 'is-mine' : 'is-theirs'}" data-message-row="${m.id}" style="display:flex;gap:8px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%">
          ${mine ? '' : `<div class="avatar sm" style="background:${esc(m.sender_color)}">${esc(initials(m.sender_name))}</div>`}
          <div class="bubble ${mine ? 'me' : 'them'}" data-message-id="${m.id}" style="max-width:100%">${m.heart_count ? `<span class="bubble-heart" data-heart-badge>❤️${m.heart_count > 1 ? ' ' + m.heart_count : ''}</span>` : ''}
            ${mine ? '' : `<div style="font-size:11px;font-weight:700;opacity:.75;margin-bottom:2px">${esc(m.sender_name)}</div>`}
            ${m.has_image ? `<div data-img-id="${m.id}" style="min-height:60px;min-width:120px;margin-bottom:${m.body ? '6px' : '0'}">⏳</div>` : ''}
            ${esc(m.body)}
            <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
          </div>
          ${chatMessageActionHtml(m, mine)}
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

    startAdaptiveChatPoll(modal, msgsEl, async () => {
      const fresh = await api(`/tournaments/${t.id}/chat?since_id=${lastId}`);
      if (fresh.items.length) renderMsgs(fresh.items, true, { newMessages: true });
      applyRoomHearts(msgsEl, fresh.heart_counts);
      return fresh.items.length > 0;
    });

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
  function configureCommunityLaneTabs() {
    const labels = [
      ['#chat-tab-chats', 'Messages'],
      ['#chat-tab-friends', 'People'],
      ['#chat-tab-nearby', 'Groups'],
    ];
    labels.forEach(([selector, label]) => {
      const button = $(selector);
      if (!button) return;
      const badge = button.querySelector('.segment-badge');
      button.replaceChildren(document.createTextNode(`${label} `));
      if (badge) button.appendChild(badge);
    });
  }

  function chatMessageActionHtml(message, mine) {
    const id = Number(message && message.id);
    if (!Number.isSafeInteger(id) || id <= 0) return '';
    const sender = esc(message.sender_name || 'this player');
    return mine
      ? `<button type="button" class="chat-message-action" data-message-action="delete" data-message-id="${id}" aria-label="Delete your message">🗑</button>`
      : `<button type="button" class="chat-message-action" data-message-action="heart" data-message-id="${id}" aria-label="React to ${sender} with a heart">♡</button>`;
  }

  function setupChat() {
    configureCommunityLaneTabs();
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
    if (message.open_call) {
      const call = message.open_call;
      if (call.state === 'open') return `🏓 ${call.spots_left} spot${call.spots_left === 1 ? '' : 's'} left · ${esc(fmtDateTime(call.scheduled_at))}`;
      if (call.state === 'full') return `✓ Roster full · ${esc(fmtDateTime(call.scheduled_at))}`;
      return call.state === 'withdrawn' ? 'Court game post withdrawn' : 'Court game post closed';
    }
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

  function universalInboxHtml(
    data, rooms, clubs, competitions, crews = { items: [], invitations: [] },
    { lane = 'messages' } = {},
  ) {
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
    (crews.items || []).forEach((value) => {
      const crew = crewSummaryFrom(value);
      if (!crew) return;
      items.push({
        kind: 'crew', id: crew.id, title: crew.name,
        iconHtml: '<span class="inbox-room-icon crew">👥</span>',
        lastMessage: value.last_message || crew.last_message || null,
        unread: Number(value.unread ?? crew.unread) || 0,
        emptyText: `${crew.member_count} player${crew.member_count === 1 ? '' : 's'}${crew.pending_count ? ` · ${crew.pending_count} invited` : ''}${crew.default_court_name ? ` · ${esc(crew.default_court_name)}` : ''}`,
      });
    });
    (rooms.items || []).forEach((room) => items.push({
      kind: 'court', id: room.court.id, title: room.court.name,
      iconHtml: '<span class="inbox-room-icon">🏓</span>', lastMessage: room.last_message,
      unread: room.unread || 0, emptyText: 'Court chat',
    }));
    (competitions.items || []).forEach((room) => items.push({
      kind: room.kind, id: room.id, title: room.title,
      iconHtml: `<span class="inbox-room-icon">${room.kind === 'game' ? '🏓' : room.kind === 'tournament' ? '🏆' : '📦'}</span>`,
      lastMessage: room.last_message, unread: room.unread || 0,
      emptyText: competitionInboxContext(room), eventAt: room.event_at, status: room.status,
    }));

    const groupKinds = new Set(['club', 'crew', 'court', 'tournament', 'league']);
    const activeStatuses = new Set(['registration', 'active', 'upcoming', 'awaiting_confirmation']);
    const visibleItems = items.filter((item) => (
      lane === 'groups'
        ? groupKinds.has(item.kind)
        : !groupKinds.has(item.kind)
          && (item.kind !== 'game' || activeStatuses.has(item.status) || item.unread > 0)
    ));
    const activeGames = lane === 'messages' ? visibleItems.filter((item) => (
      item.kind === 'game' && activeStatuses.has(item.status)
    )).sort((a, b) => {
      if (a.unread !== b.unread) return b.unread - a.unread;
      if (a.eventAt && b.eventAt) return new Date(a.eventAt) - new Date(b.eventAt);
      return a.eventAt ? -1 : b.eventAt ? 1 : a.title.localeCompare(b.title);
    }) : [];
    const activeIds = new Set(activeGames.map((item) => `${item.kind}:${item.id}`));
    const recent = visibleItems.filter((item) => item.lastMessage
        && !activeIds.has(`${item.kind}:${item.id}`))
      .sort((a, b) => b.lastMessage.id - a.lastMessage.id);
    const ready = visibleItems.filter((item) => !item.lastMessage
        && !activeIds.has(`${item.kind}:${item.id}`))
      .sort((a, b) => {
        if (a.eventAt && b.eventAt) return new Date(a.eventAt) - new Date(b.eventAt);
        return a.eventAt ? -1 : b.eventAt ? 1 : a.title.localeCompare(b.title);
      });
    const kindLabel = { dm: 'Direct', club: 'Club', crew: 'Crew', court: 'Court', game: 'Game', tournament: 'Tournament', league: 'League' };
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

    const invitations = (crews.invitations || []).map((invitation) => ({
      invitation,
      crew: crewSummaryFrom(invitation),
    })).filter((entry) => entry.crew);
    let html = '';
    if (lane === 'groups' && invitations.length) {
      html += '<div class="section-label" style="margin-top:4px">Crew invitations</div>';
      html += invitations.map(({ invitation, crew }) => {
        const inviter = invitation.inviter && invitation.inviter.display_name
          ? invitation.inviter.display_name
          : invitation.invited_by_name || invitation.creator_name || 'A player';
        const playedAt = invitation.source_court_name || crew.default_court_name;
        return `<div class="card crew-invite-card" data-crew-invitation="${crew.id}">
          <div class="row crew-invite-main">
            <span class="inbox-room-icon crew">👥</span>
            <span class="row-main"><span class="row-title">${esc(crew.name)}</span><span class="row-sub">${esc(inviter)} invited you${playedAt ? ` · You played together at ${esc(playedAt)}` : ''}</span></span>
          </div>
          <div class="crew-invite-actions">
            <button type="button" class="btn btn-secondary" data-crew-response="decline" data-crew-id="${crew.id}">Decline</button>
            <button type="button" class="btn btn-primary" data-crew-response="accept" data-crew-id="${crew.id}">Join crew</button>
          </div>
        </div>`;
      }).join('');
    }
    if (lane === 'groups') {
      const crewItems = visibleItems.filter((item) => item.kind === 'crew');
      const clubItems = visibleItems.filter((item) => item.kind === 'club');
      const conversationItems = visibleItems.filter((item) => ['court', 'tournament', 'league'].includes(item.kind))
        .sort((a, b) => {
          if (a.unread !== b.unread) return b.unread - a.unread;
          if (a.lastMessage && b.lastMessage) return b.lastMessage.id - a.lastMessage.id;
          return a.lastMessage ? -1 : b.lastMessage ? 1 : a.title.localeCompare(b.title);
        });
      html += '<div class="section-label" style="margin-top:4px">Your crews</div>';
      html += crewItems.length ? crewItems.map((item) => rowHtml(item)).join('')
        : '<div class="empty-state community-lane-empty" style="padding:14px">Crews you create after a game stay here for the next plan.</div>';
      html += '<div class="section-label">Your clubs</div>';
      html += clubItems.length ? clubItems.map((item) => rowHtml(item)).join('')
        : '<div class="empty-state community-lane-empty" style="padding:14px">Join a local club or start one for your court.</div>';
      if (conversationItems.length) {
        html += '<div class="section-label">Group conversations</div>';
        html += conversationItems.map((item) => rowHtml(item)).join('');
      }
      html += `<div class="section-label">Find your group</div>
        <div class="community-group-actions" style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <button class="btn btn-secondary" id="club-find">🔎 Find clubs</button>
          <button class="btn btn-secondary" id="club-new">＋ Start a club</button>
        </div>`;
      return html;
    }

    if (activeGames.length) {
      html += '<div class="section-label" style="margin-top:4px">Active game chats</div>';
      html += activeGames.map((item) => rowHtml(item, 'inbox-row-pinned')).join('');
    }
    if (recent.length) {
      html += `<div class="section-label" style="margin-top:${activeGames.length ? '18px' : '4px'}">Messages</div>`;
      html += recent.map((item) => rowHtml(item)).join('');
    }
    if (ready.length) {
      html += `<div class="section-label" style="margin-top:${activeGames.length || recent.length ? '18px' : '4px'}">Ready to coordinate</div>`;
      html += ready.map((item, index) => rowHtml(item, index >= 8 ? 'inbox-ready-extra hidden' : '')).join('');
      if (ready.length > 8) {
        html += `<button type="button" class="btn btn-secondary btn-block" id="inbox-show-ready">Show ${ready.length - 8} more chats</button>`;
      }
    }
    if (!visibleItems.length) {
      html = `<div class="empty-state"><span class="big">💬</span><b>Your messages will live here.</b><br>Start with a player or coordinate an upcoming game.
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px">
          <button class="btn btn-secondary" data-goto="chat-friends">Find players</button>
          <button class="btn btn-primary" data-goto="new-game">Plan a game</button>
        </div></div>`;
    }
    return html;
  }

  function bindCommunityConversationRows(el) {
    el.querySelectorAll('[data-inbox-kind]').forEach((row) => row.addEventListener('click', async () => {
      if (row.disabled) return;
      row.disabled = true;
      const kind = row.dataset.inboxKind;
      const id = Number(row.dataset.inboxId);
      try {
        if (kind === 'dm') await openThread(id);
        else if (kind === 'court') await openCourtChat({ id, name: row.dataset.inboxTitle });
        else if (kind === 'club') await openClubScreen(id);
        else if (kind === 'crew') await openCrewChatById(id);
        else if (kind === 'game') await openGameChat({ id });
        else if (kind === 'tournament') await openTournamentChat({ id });
        else if (kind === 'league') await openLeagueChat({ id, name: row.dataset.inboxTitle });
      } finally {
        row.disabled = false;
        // The room GET is the authoritative read action. Re-fetch whichever
        // stable Community lane the player returns to so its count stays exact.
        if (state.tab === 'chat') renderChat();
      }
    }));
  }

  function bindCrewInvitationActions(el) {
    el.querySelectorAll('[data-crew-response]').forEach((button) => button.addEventListener('click', async () => {
      const crewId = Number(button.dataset.crewId);
      const accept = button.dataset.crewResponse === 'accept';
      const card = button.closest('[data-crew-invitation]');
      const buttons = [...(card?.querySelectorAll('[data-crew-response]') || [])];
      buttons.forEach((item) => { item.disabled = true; });
      button.textContent = accept ? 'Joining…' : 'Declining…';
      try {
        await api(`/crews/${crewId}/respond`, {
          method: 'POST', body: JSON.stringify({ accept }),
        });
        toast(accept ? 'Joined the crew 🏓' : 'Crew invitation declined');
        renderChat();
        refreshMe();
        if (accept) openCrewScreen(crewId);
      } catch (error) {
        toast(error.message);
        buttons.forEach((item) => { item.disabled = false; });
        button.textContent = accept ? 'Join crew' : 'Decline';
      }
    }));
  }

  async function renderPeopleLane(el, { useCachedData = false } = {}) {
    const mode = state.peopleMode === 'nearby' ? 'nearby' : 'friends';
    el.innerHTML = `
      <div class="segmented community-people-switch" id="people-mode" role="tablist" aria-label="People views" style="margin:2px 0 12px">
        <button type="button" id="people-tab-friends" role="tab" data-people-mode="friends" aria-controls="people-content" aria-selected="${mode === 'friends'}" class="${mode === 'friends' ? 'active' : ''}">Friends</button>
        <button type="button" id="people-tab-nearby" role="tab" data-people-mode="nearby" aria-controls="people-content" aria-selected="${mode === 'nearby'}" class="${mode === 'nearby' ? 'active' : ''}">Nearby</button>
      </div>
      <div id="people-content" role="tabpanel" aria-labelledby="people-tab-${mode}"></div>`;
    const body = el.querySelector('#people-content');
    if (mode === 'nearby') await renderNearbyPlayers(body);
    else await renderFriends(body, { useCachedData });
    setupTablistKeyboard(el.querySelector('#people-mode'));
    el.querySelector('#people-mode').addEventListener('click', (event) => {
      const button = event.target.closest('[data-people-mode]');
      if (!button || button.dataset.peopleMode === state.peopleMode) return;
      state.peopleMode = button.dataset.peopleMode;
      renderChat({ useCachedData: state.peopleMode === 'friends' });
    });
  }

  async function renderChat({ reuseFresh = false, useCachedData = false } = {}) {
    const seg = state.chatSeg;
    const liveEl = $('#chat-content');
    liveEl.setAttribute('aria-labelledby', `chat-tab-${seg}`);
    const peopleKey = seg === 'friends' ? `:${state.peopleMode}` : '';
    const viewKey = `${state.me?.id || 'signed-out'}:chat:${seg}${peopleKey}:${areaViewKey()}`;
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
        const [data, rooms, clubs, competitions, crews] = await Promise.all([
          api('/chat'),
          api('/chat/courts'),
          api('/clubs/mine'),
          api('/chat/competitions'),
          api('/crews/mine').catch(() => ({ items: [], invitations: [] })),
        ]);
        if (renderSeq !== state.chatRenderSeq || state.chatSeg !== seg) return;
        syncCommunityUnreadLanes(rooms, clubs, competitions, crews);
        renderBadges();
        el.innerHTML = universalInboxHtml(data, rooms, clubs, competitions, crews, { lane: 'messages' });
        bindCommunityConversationRows(el);
        el.querySelector('#inbox-show-ready')?.addEventListener('click', (event) => {
          const firstRevealed = el.querySelector('.inbox-ready-extra');
          el.querySelectorAll('.inbox-ready-extra').forEach((row) => row.classList.remove('hidden'));
          event.currentTarget.remove();
          firstRevealed?.focus({ preventScroll: true });
        });
        el.querySelector('#club-find')?.addEventListener('click', openFindClubsSheet);
        el.querySelector('#club-new')?.addEventListener('click', openCreateClubSheet);
      } else if (seg === 'nearby') {
        const [rooms, clubs, competitions, crews] = await Promise.all([
          api('/chat/courts'),
          api('/clubs/mine'),
          api('/chat/competitions'),
          api('/crews/mine').catch(() => ({ items: [], invitations: [] })),
        ]);
        if (renderSeq !== state.chatRenderSeq || state.chatSeg !== seg) return;
        syncCommunityUnreadLanes(rooms, clubs, competitions, crews);
        renderBadges();
        el.innerHTML = universalInboxHtml(
          { items: [] }, rooms, clubs, competitions, crews, { lane: 'groups' },
        );
        bindCommunityConversationRows(el);
        bindCrewInvitationActions(el);
        el.querySelector('#club-find')?.addEventListener('click', openFindClubsSheet);
        el.querySelector('#club-new')?.addEventListener('click', openCreateClubSheet);
      } else {
        await renderPeopleLane(el, { useCachedData });
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
    let looking;
    try {
      [data, looking] = await Promise.all([
        api(`/players/nearby?lat=${loc.lat}&lng=${loc.lng}&radius=50${skill ? `&skill=${skill}` : ''}`),
        api(`/players/looking?lat=${loc.lat}&lng=${loc.lng}&radius=50`).catch(() => null),
      ]);
    } catch (e) { el.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; return; }

    const skills = [['', 'All levels'], ['beginner', 'Beginner'], ['intermediate', 'Intermediate'], ['advanced', 'Advanced'], ['pro', 'Pro']];
    const rallies = normalizeLookingRallies(looking);
    const pulses = normalizeLookingPulses(looking).filter((pulse) => (
      !skill || pulse.user?.skill_level === skill
    ));
    const pulsesById = new Map(pulses.map((pulse) => [pulse.id, pulse]));
    const players = [...(Array.isArray(data.items) ? data.items : [])].sort((a, b) => {
      const rank = (player) => {
        const playerRally = playerRallySummary(player);
        if (playerRally && playerRally.gameId) return 4;
        if (player.checked_in_court && player.checked_in_court.looking_for_game) return 3;
        if (player.checked_in_court) return 2;
        return player.active_now ? 1 : 0;
      };
      return rank(b) - rank(a)
        || Number(a.distance_miles ?? Infinity) - Number(b.distance_miles ?? Infinity)
        || String(a.display_name || '').localeCompare(String(b.display_name || ''));
    });
    let html = `
      <details class="nearby-filter" style="margin:4px 0 12px">
        <summary>Level: ${esc((skills.find(([value]) => value === skill) || skills[0])[1])}</summary>
        <div class="form-field" style="margin:8px 0 0">
          <label class="sr-only" for="nearby-skill">Filter nearby players by level</label>
          <select id="nearby-skill">
            ${skills.map(([value, label]) => `<option value="${value}" ${value === skill ? 'selected' : ''}>${label}</option>`).join('')}
          </select>
        </div>
      </details>`;

    if (pulses.length) {
      html += '<div class="section-label">📣 Can play this hour</div><div class="nearby-play-pulses">';
      html += pulses.map((pulse) => {
        const person = pulse.user || {};
        const first = String(person.display_name || 'A nearby player').split(/\s+/)[0];
        const proximity = pulse.distanceMiles == null ? '' : `${pulse.distanceMiles} mi to court · `;
        return `
          <div class="card play-pulse-nearby-card">
            <div class="play-pulse-nearby-person" ${safePositiveId(person.id) ? `data-view-user="${safePositiveId(person.id)}"` : ''}>
              ${pulse.user ? avatarHtml(person, 'sm') : '<span class="play-pulse-avatar" aria-hidden="true">🏓</span>'}
            </div>
            <div class="play-pulse-nearby-copy">
              <b>${esc(first)} can play at ${esc(pulse.courtName)} this hour</b>
              <span>${esc(proximity)}free until ${esc(fmtTimeShort(pulse.expiresAt))} · intended destination, not current presence</span>
              <small>Confirming creates an open quick game starting in about 15 minutes and notifies ${esc(first)}.</small>
            </div>
            <button type="button" class="btn btn-primary btn-sm" data-play-pulse-accept="${pulse.id}" aria-label="Play with ${esc(first)} at ${esc(pulse.courtName)}">Play there</button>
          </div>`;
      }).join('');
      html += '</div>';
    }

    if (rallies.length) {
      html += '<div class="section-label">Games starting now</div><div class="nearby-rallies">';
      html += rallies.map((rally) => {
        const action = rallyActionState(rally);
        return `
          <div class="card nearby-rally-card">
            <div class="nearby-rally-copy"><b>${esc(rally.courtName)}</b><span>${esc(rallyCountsText(rally))}</span></div>
            ${action.enabled
              ? `<button type="button" class="btn btn-primary btn-sm" data-rally-action="${action.kind}" ${rallyDatasetAttributes(rally)}>${esc(action.label)}</button>`
              : `<span class="tag warn nearby-rally-unavailable">${esc(action.label)}</span>`}
          </div>`;
      }).join('');
      html += '</div>';
    }

    html += players.length
      ? players.map((p) => {
          const playerRally = playerRallySummary(p);
          const readyAtCourt = !!(p.checked_in_court && p.checked_in_court.looking_for_game);
          let action = '';
          if (playerRally && playerRally.gameId) {
            const rallyAction = rallyActionState(playerRally);
            action = rallyAction.enabled
              ? `<button type="button" class="btn btn-primary btn-sm" data-rally-action="${rallyAction.kind}" ${rallyDatasetAttributes(playerRally)}>${esc(rallyAction.label)}</button>`
              : `<span class="tag warn" style="margin:0">${esc(rallyAction.label)}</span>`;
          } else if (readyAtCourt) {
            action = `<button type="button" class="btn btn-secondary btn-sm" data-nearby-court="${p.checked_in_court.id}">View court</button>`;
          } else if (p.is_friend) {
            action = `<button class="btn btn-secondary btn-sm" data-msg="${p.id}">Message</button>`;
          }
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
            <div class="card row nearby-player${readyAtCourt || playerRally ? ' is-ready' : ''}">
              <div data-view-user="${p.id}" style="cursor:pointer">${avatarHtml(p)}</div>
              <div class="row-main" data-view-user="${p.id}" style="cursor:pointer">
                <div class="row-title">${esc(p.display_name)}${p.current_streak >= 2 ? ' 🔥' : ''}</div>
                <div class="row-sub">${sub}</div>
              </div>
              ${action}
            </div>`;
        }).join('')
      : pulses.length || rallies.length ? ''
        : '<div class="empty-state"><span class="big">📍</span>No players near you yet.<br>Check in at a court so others can find you!<br><button class="btn btn-primary" data-goto="courts" style="margin-top:10px">🗺 Browse courts</button></div>';

    html += `<details class="nearby-privacy">
      <summary>How location sharing works</summary>
      <p>Court check-ins show where you are now. Free this hour shares only where you intend to play and expires automatically.</p>
    </details>`;

    el.innerHTML = html;
    el.querySelector('#nearby-skill').addEventListener('change', (e) => {
      state.nearbySkill = e.currentTarget.value;
      renderChat();
    });
    el.querySelectorAll('[data-rally-action]').forEach((button) => button.addEventListener('click', () => {
      openReadyRally(rallySummaryFromDataset(button), button);
    }));
    el.querySelectorAll('[data-play-pulse-accept]').forEach((button) => button.addEventListener('click', () => {
      const pulse = pulsesById.get(Number(button.dataset.playPulseAccept));
      if (pulse) openPlayPulseAcceptConfirmation(pulse);
    }));
    el.querySelectorAll('[data-nearby-court]').forEach((button) => button.addEventListener('click', () => {
      openCourtDetail(Number(button.dataset.nearbyCourt));
    }));
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
          <button class="btn btn-secondary btn-sm" data-respond="${f.friendship_id}" data-accept="0" aria-label="Decline friend request from ${esc(f.display_name)}">✕</button>
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
              ? `<button class="btn btn-primary btn-sm" data-coming="${f.id}" title="Tell them you can be there soon"><svg class="pb-ic"><use href="#pb"/></svg> I can be there soon</button>`
              : `<button class="btn btn-secondary btn-sm" data-invite="${f.id}" data-invite-court="${f.checked_in_court ? f.checked_in_court.id : ''}" data-invite-court-name="${f.checked_in_court ? esc(f.checked_in_court.name) : ''}" aria-label="Invite ${esc(f.display_name)} to a game">Invite</button>`}
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
    el.querySelectorAll('[data-invite]').forEach((b) => b.addEventListener('click', () => {
      const court = b.dataset.inviteCourt
        ? { id: Number(b.dataset.inviteCourt), name: b.dataset.inviteCourtName }
        : null;
      openNewGameModal({ court, inviteUserIds: [Number(b.dataset.invite)], visibility: 'private' });
    }));
    el.querySelectorAll('[data-coming]').forEach((b) => b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        await api(`/players/${b.dataset.coming}/coming`, { method: 'POST' });
        toast('They know you can be there soon 🏓');
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
    if (!search) return;
    search.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        if (!search.isConnected) return;
        const q = search.value.trim();
        let resultsEl = el.querySelector('#friend-search-results');
        if (!resultsEl) return;
        if (q.length < 2) { resultsEl.innerHTML = ''; return; }
        let data;
        try {
          data = await api(`/users/search?q=${encodeURIComponent(q)}`);
        } catch (error) {
          if (search.isConnected && search.value.trim() === q) toast(error.message);
          return;
        }
        if (!search.isConnected || search.value.trim() !== q) return;
        resultsEl = el.querySelector('#friend-search-results');
        if (!resultsEl) return;
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
          <button type="button" class="modal-close" style="font-size:18px" aria-label="Back">‹</button>
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
      const html = items.map((m) => {
        const mine = m.sender_id === state.me.id;
        return `
        <div class="chat-message-row ${mine ? 'is-mine' : 'is-theirs'}" data-message-row="${m.id}" style="display:flex;gap:6px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%;align-items:flex-end">
        <div class="bubble ${mine ? 'me' : 'them'}" data-message-id="${m.id}">
          ${m.has_image ? `<div data-img-id="${m.id}" style="min-height:60px;min-width:120px;margin-bottom:${m.body ? '6px' : '0'}">⏳</div>` : ''}
          ${esc(m.body)}
          <div class="bubble-time">${fmtTimeShort(m.created_at)}${mine && m.read_at ? ' · <span title="Seen">✓✓</span>' : ''}</div>
          ${m.hearted ? '<span class="bubble-heart">❤️</span>' : ''}
        </div>
        ${chatMessageActionHtml(m, mine)}
        </div>`;
      }).join('');
      if (append && !msgsEl.querySelector('.empty-state')) msgsEl.insertAdjacentHTML('beforeend', html);
      else if (append) msgsEl.innerHTML = html;
      else msgsEl.innerHTML = html || '<div class="empty-state" style="padding:20px">Say hi! 👋</div>';
      chatUX.restoreScroll(snapshot, { forceBottom, newMessageCount: newMessages ? items.length : 0 });
      hydrateChatImages(msgsEl, chatUX);
    };
    // Live ✓✓: flip 'Seen' onto already-rendered bubbles as the partner reads.
    const markSeen = (upTo) => {
      if (!upTo) return;
      msgsEl.querySelectorAll('.bubble.me[data-message-id]').forEach((b) => {
        const t = b.querySelector('.bubble-time');
        if (Number(b.dataset.messageId) <= upTo && t && !t.textContent.includes('✓✓')) {
          t.insertAdjacentHTML('beforeend', ' · <span title="Seen">✓✓</span>');
        }
      });
    };
    // Live ❤️: the partner's hearts land on my rendered bubbles each poll.
    const applyHearts = (ids) => {
      if (!ids) return;
      const set = new Set(ids);
      msgsEl.querySelectorAll('.bubble.me[data-message-id]').forEach((b) => {
        const has = b.querySelector('.bubble-heart');
        const want = set.has(Number(b.dataset.messageId));
        if (want && !has) b.insertAdjacentHTML('beforeend', '<span class="bubble-heart">❤️</span>');
        if (!want && has) has.remove();
      });
    };
    renderMsgs(data.items, false, { forceBottom: true });
    chatUX.activateOutbox((message) => renderMsgs([message], true, { forceBottom: true }));
    markSeen(data.partner_read_up_to);
    applyHearts(data.hearted_ids);
    refreshMe();

    stopThreadPolling();
    const threadPoller = startAdaptiveChatPoll(modal, msgsEl, async () => {
      const fresh = await api(`/chat/${userId}?since_id=${lastId}`);
      if (fresh.items.length) renderMsgs(fresh.items, true, { newMessages: true });
      applyRoomHearts(msgsEl, fresh.heart_counts);
      markSeen(fresh.partner_read_up_to);
      applyHearts(fresh.hearted_ids);
      return fresh.items.length > 0;
    });
    state.threadPollTimer = threadPoller;
    modal._cleanupFns?.push(() => {
      if (state.threadPollTimer === threadPoller) state.threadPollTimer = null;
    });

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

  function courtOpenCallFingerprint(call) {
    return JSON.stringify([
      call.id, call.state, call.active, call.scheduled_at, call.player_count,
      call.max_players, call.spots_left, call.waitlist_count, call.is_joined,
      call.can_join, call.can_waitlist, call.can_withdraw,
    ]);
  }

  function courtOpenCallCardHtml(call) {
    const stateName = ['open', 'full', 'closed', 'withdrawn'].includes(call.state)
      ? call.state : 'closed';
    const stateLabel = {
      open: 'Open', full: 'Full', closed: 'Closed', withdrawn: 'Withdrawn',
    }[stateName];
    const title = stateName === 'open'
      ? `${call.spots_left} spot${call.spots_left === 1 ? '' : 's'} left`
      : stateName === 'full' ? 'Roster is full'
        : stateName === 'withdrawn' ? 'Court post withdrawn' : 'Game closed';
    const skill = call.preferred_level && call.preferred_level !== 'any'
      ? ` · ${skillLabel(call.preferred_level)}` : '';
    const primary = call.can_join
      ? `<button type="button" class="btn btn-primary" data-open-call-action="join" data-open-call-game="${call.game_id}">Join game</button>`
      : call.can_waitlist
        ? `<button type="button" class="btn btn-primary" data-open-call-action="waitlist" data-open-call-game="${call.game_id}">Join waitlist</button>`
        : `<button type="button" class="btn btn-secondary" data-open-call-action="open" data-open-call-game="${call.game_id}">${call.is_joined && ['open', 'full'].includes(stateName) ? 'Open your game' : 'View game details'}</button>`;
    return `<article class="court-open-call is-${stateName}" data-open-call-id="${call.id}"
      data-open-call-fingerprint="${esc(courtOpenCallFingerprint(call))}">
      <div class="court-open-call-head">
        <span class="court-open-call-icon">${stateName === 'full' ? '✓' : stateName === 'open' ? '🏓' : '—'}</span>
        <div class="court-open-call-title"><b>${esc(title)}</b>
          <span>${esc(fmtDateTime(call.scheduled_at))} · ${call.game_type === 'ranked' ? 'Ranked' : 'Casual'}${esc(skill)}</span>
        </div>
        <span class="court-open-call-state">${stateLabel}</span>
      </div>
      <div class="court-open-call-capacity">
        <span><b>${call.player_count}/${call.max_players}</b> players</span>
        <span><b>${call.waitlist_count || 0}</b> waiting</span>
      </div>
      <div class="court-open-call-actions">${primary}
        ${call.can_withdraw ? `<button type="button" class="court-open-call-withdraw" data-open-call-action="withdraw" data-open-call-game="${call.game_id}" aria-label="Withdraw court post">Withdraw</button>` : ''}
      </div>
    </article>`;
  }

  function courtOpenCallMessageHtml(message) {
    return `<div data-message-id="${message.id}" class="court-open-call-message">
      <div class="court-open-call-byline">
        <div class="avatar sm" style="background:${esc(message.sender_color)}">${esc(initials(message.sender_name))}</div>
        <span>${esc(message.sender_name)} · ${fmtTimeShort(message.created_at)}</span>
      </div>
      ${courtOpenCallCardHtml(message.open_call)}
    </div>`;
  }

  function applyCourtOpenCallSnapshots(msgsEl, rawCalls, { prune = true } = {}) {
    const calls = Array.isArray(rawCalls) ? rawCalls : [];
    const byId = new Map(calls.map((call) => [Number(call.id), call]));
    let changed = false;
    msgsEl.querySelectorAll('[data-open-call-id]').forEach((card) => {
      const call = byId.get(Number(card.dataset.openCallId));
      if (!call) {
        if (prune) {
          card.closest('[data-message-id]')?.remove();
          changed = true;
        }
        return;
      }
      if (card.dataset.openCallFingerprint !== courtOpenCallFingerprint(call)) {
        card.outerHTML = courtOpenCallCardHtml(call);
        changed = true;
      }
      byId.delete(Number(call.id));
    });
    return changed;
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
          <button type="button" class="modal-close" style="font-size:18px" aria-label="Back">‹</button>
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
        if (m.open_call) return courtOpenCallMessageHtml(m);
        const mine = m.sender_id === state.me.id;
        return `
        <div class="chat-message-row ${mine ? 'is-mine' : 'is-theirs'}" data-message-row="${m.id}" style="display:flex;gap:8px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%">
          ${mine ? '' : `<div class="avatar sm" style="background:${esc(m.sender_color)}">${esc(initials(m.sender_name))}</div>`}
          <div class="bubble ${mine ? 'me' : 'them'}" data-message-id="${m.id}" style="max-width:100%">${m.heart_count ? `<span class="bubble-heart" data-heart-badge>❤️${m.heart_count > 1 ? ' ' + m.heart_count : ''}</span>` : ''}
            ${mine ? '' : `<div style="font-size:11px;font-weight:700;opacity:.75;margin-bottom:2px">${esc(m.sender_name)}</div>`}
            ${m.has_image ? `<div data-img-id="${m.id}" style="min-height:60px;min-width:120px;margin-bottom:${m.body ? '6px' : '0'}">⏳</div>` : ''}
            ${esc(m.body)}
            <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
          </div>
          ${chatMessageActionHtml(m, mine)}
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

    startAdaptiveChatPoll(modal, msgsEl, async () => {
      const fresh = await api(`/courts/${court.id}/chat?since_id=${lastId}`);
      if (fresh.items.length) renderMsgs(fresh.items, true, { newMessages: true });
      applyRoomHearts(msgsEl, fresh.heart_counts);
      const callChanged = applyCourtOpenCallSnapshots(msgsEl, fresh.open_calls);
      return fresh.items.length > 0 || callChanged;
    });

    msgsEl.addEventListener('click', async (event) => {
      const button = event.target.closest('[data-open-call-action]');
      if (!button || button.disabled) return;
      const gameId = Number(button.dataset.openCallGame);
      const action = button.dataset.openCallAction;
      if (!gameId) return;
      if (action === 'open') { openGameScreen(gameId); return; }
      button.disabled = true;
      const originalText = button.textContent;
      button.textContent = action === 'join' ? 'Joining…'
        : action === 'waitlist' ? 'Joining waitlist…' : 'Withdrawing…';
      try {
        const response = action === 'withdraw'
          ? await api(`/games/${gameId}/open-call`, { method: 'DELETE' })
          : await api(`/games/${gameId}/${action === 'join' ? 'join' : 'waitlist'}`, { method: 'POST' });
        const nextCall = response.open_call || (response.game && response.game.open_call);
        if (nextCall) applyCourtOpenCallSnapshots(msgsEl, [nextCall], { prune: false });
        toast(action === 'join' ? "You're in! 🏓"
          : action === 'waitlist' ? "You're on the waitlist ⏳" : 'Court post withdrawn');
        refreshMe();
      } catch (error) {
        toast(error.message);
        button.disabled = false;
        button.textContent = originalText;
        try {
          const fresh = await api(`/courts/${court.id}/chat?since_id=${lastId}`);
          if (fresh.items.length) renderMsgs(fresh.items, true);
          applyCourtOpenCallSnapshots(msgsEl, fresh.open_calls);
        } catch { /* the adaptive poll will recover */ }
      }
    });

    modal.querySelector('#cc-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = input.value.trim();
      if (!body) return;
      try {
        await chatUX.send(body);
      } catch (err) { toast(err.message); }
    });
  }

  // ---------- Crews ----------
  function showCommunityInbox() {
    // Crew consent belongs in the persistent Groups lane, even though the
    // legacy helper name remains for deep-link compatibility.
    state.chatSeg = 'nearby';
    document.querySelectorAll('#chat-segments button').forEach((button) => {
      const active = button.dataset.seg === 'nearby';
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
    switchTab('chat');
    renderChat();
  }


  function crewIsOwner(crew) {
    return !!(crew && (
      crew.is_owner === true || crew.owner === true || crew.my_role === 'owner' || crew.role === 'owner'
      || Number(crew.owner_id || crew.creator_id) === Number(state.me && state.me.id)
    ));
  }

  function crewPlannerOptions(crew) {
    const members = (crew.members || []).map(sanitizePlannerInvitee).filter(Boolean);
    const invitees = members.filter((person) => !state.me || person.id !== state.me.id);
    const total = invitees.length + 1;
    const maxPlayers = [2, 4, 6, 8, 10, 12].find((count) => count >= total) || 12;
    const game = {
      id: null,
      court: crew.default_court_id ? {
        id: crew.default_court_id, name: crew.default_court_name || 'Crew court',
      } : null,
      game_type: 'casual',
      max_players: maxPlayers,
      preferred_level: 'any',
      scheduled_at: null,
    };
    return completedCrewPlannerOptions(game, invitees, crew);
  }

  async function openCrewScreen(crewId) {
    const routeLoad = beginRoutedOverlayLoad({ kind: 'crew', id: crewId });
    let detail;
    try { detail = await api(`/crews/${crewId}`); } catch (error) {
      if (!routedOverlayLoadIsCurrent(routeLoad)) return;
      if (Number(error.status) === 404) {
        try {
          const mine = await api('/crews/mine');
          if (!routedOverlayLoadIsCurrent(routeLoad)) return;
          const pending = (mine.invitations || []).some(
            (invitation) => Number(crewSummaryFrom(invitation)?.id) === Number(crewId),
          );
          if (pending) {
            clearDeadDeepLink(`#crew/${crewId}`);
            showCommunityInbox();
            toast('Crew invitation ready — choose Join crew or Decline');
            return;
          }
        } catch { /* preserve the original member-only error below */ }
      }
      toast(error.message); clearDeadDeepLink(`#crew/${crewId}`); return;
    }
    if (!routedOverlayLoadIsCurrent(routeLoad)) return;
    const summary = crewSummaryFrom(detail);
    if (!summary) { toast('Crew is unavailable'); return; }
    const members = Array.isArray(detail.members) ? detail.members : [];
    const crew = { ...detail, ...summary, members };
    crew.member_count = Math.max(1, Number(detail.member_count) || members.length || summary.member_count);
    const owner = crewIsOwner(crew);
    const heroContent = `
      <div class="crew-avatar-stack">${crew.members.slice(0, 4).map((member) => avatarHtml(member, 'sm')).join('')}</div>
      <div class="row-main">
        <b>${crew.member_count} player${crew.member_count === 1 ? '' : 's'}${crew.pending_count ? ` · ${crew.pending_count} invited` : ''}</b>
        <span class="row-sub">${crew.default_court_name ? `📍 ${esc(crew.default_court_name)}` : 'Private crew · invite only'}</span>
      </div>`;
    const heroHtml = crew.default_court_id
      ? `<button type="button" class="crew-hero" id="crew-court" aria-label="Open ${esc(crew.default_court_name || 'Crew court')} details">${heroContent}</button>`
      : `<div class="crew-hero">${heroContent}</div>`;
    const membersHtml = crew.members.map((member) => `
      <button type="button" class="card row crew-member" data-view-user="${Number(member.id ?? member.user_id)}">
        ${avatarHtml(member, 'sm')}
        <span class="row-main"><span class="row-title">${esc(member.display_name || 'Player')}${member.role === 'owner' || Number(member.id ?? member.user_id) === Number(crew.owner_id || crew.creator_id) ? ' 👑' : ''}</span><span class="row-sub">${skillLabel(member.skill_level)} · ${Number(member.rating) || 1200}</span></span>
        <span class="chev">›</span>
      </button>`).join('');
    const modal = openModal(`
      ${modalHead(`👥 ${crew.name}`)}
      ${heroHtml}
      <div class="crew-primary-actions">
        <button type="button" class="btn btn-primary" id="crew-plan">📅 Plan a game</button>
        <button type="button" class="btn btn-secondary" id="crew-chat">💬 Crew chat</button>
      </div>
      ${crew.pending_count ? `<div class="crew-pending-note">${crew.pending_count} invitation${crew.pending_count === 1 ? '' : 's'} pending. They choose whether to join.</div>` : ''}
      <div class="section-label">Players</div>
      <div class="crew-roster">${membersHtml || '<div class="empty-state" style="padding:18px">No players available.</div>'}</div>
      <div class="crew-manage-actions">
        ${owner ? '<button type="button" class="btn btn-secondary" id="crew-rename">✏️ Rename</button><button type="button" class="btn btn-secondary danger-text" id="crew-delete">🗑 Disband</button>'
          : '<button type="button" class="btn btn-secondary" id="crew-leave">🚪 Leave crew</button>'}
      </div>
    `, { route: { kind: 'crew', id: crew.id }, label: crew.name });
    bindUserButtons(modal);
    modal.querySelector('#crew-court')?.addEventListener('click', () => {
      transitionModal(modal, () => openCourtDetail(crew.default_court_id));
    });
    modal.querySelector('#crew-plan')?.addEventListener('click', () => {
      const options = crewPlannerOptions(crew);
      if (!options.inviteUserIds.length) {
        toast('At least one teammate needs to join before you can plan a crew game');
        return;
      }
      transitionModal(modal, () => openNewGameModal(options));
    });
    modal.querySelector('#crew-chat')?.addEventListener('click', () => {
      transitionModal(modal, () => openCrewChat(crew));
    });
    modal.querySelector('#crew-rename')?.addEventListener('click', () => {
      transitionModal(modal, () => openRenameCrewSheet(crew));
    });
    modal.querySelector('#crew-leave')?.addEventListener('click', async (event) => {
      if (!window.confirm(`Leave ${crew.name}?`)) return;
      event.currentTarget.disabled = true;
      try {
        await api(`/crews/${crew.id}/leave`, { method: 'POST' });
        await purgeChatOutboxChannel(state.me?.id, `crew:${crew.id}`);
        toast('You left the crew');
        closeModal(modal); renderChat(); refreshMe();
      } catch (error) { toast(error.message); event.currentTarget.disabled = false; }
    });
    modal.querySelector('#crew-delete')?.addEventListener('click', async (event) => {
      if (!window.confirm(`Disband ${crew.name}? This deletes its crew chat for everyone.`)) return;
      event.currentTarget.disabled = true;
      try {
        await api(`/crews/${crew.id}`, { method: 'DELETE' });
        await purgeChatOutboxChannel(state.me?.id, `crew:${crew.id}`);
        toast('Crew disbanded');
        closeModal(modal); renderChat(); refreshMe();
      } catch (error) { toast(error.message); event.currentTarget.disabled = false; }
    });
  }

  function openRenameCrewSheet(crew) {
    const modal = openModal(`
      ${modalHead('Rename crew')}
      <form id="crew-rename-form" novalidate>
        <div class="form-field"><label for="crew-name">Crew name</label><input id="crew-name" type="text" maxlength="80" value="${esc(crew.name)}" autocomplete="off" /></div>
        <button type="submit" class="btn btn-primary btn-block" id="crew-name-save">Save name</button>
      </form>
    `);
    const formUX = bindModalFormUX(modal, '#crew-name-save', { draftKey: `rename-crew-${crew.id}` });
    modal.querySelector('#crew-rename-form').addEventListener('submit', async (event) => {
      event.preventDefault(); formUX.clearError();
      const name = modal.querySelector('#crew-name').value.trim();
      if (name.length < 3) { formUX.showError('Crew name needs 3+ characters.', modal.querySelector('#crew-name')); return; }
      const finish = formUX.startSubmitting('Saving name…');
      if (!finish) return;
      try {
        await api(`/crews/${crew.id}`, { method: 'PATCH', body: JSON.stringify({ name }) });
        formUX.clearDraft({ disable: true });
        toast('Crew renamed');
        transitionModal(modal, () => openCrewScreen(crew.id));
        renderChat();
      } catch (error) { finish(); formUX.showError(error.message); }
    });
  }

  async function openCrewChatById(crewId) {
    const routeLoad = beginRoutedOverlayLoad({ kind: 'crew', id: crewId });
    let detail;
    try { detail = await api(`/crews/${crewId}`); } catch (error) {
      if (!routedOverlayLoadIsCurrent(routeLoad)) return;
      toast(error.message);
      return;
    }
    if (!routedOverlayLoadIsCurrent(routeLoad)) return;
    const summary = crewSummaryFrom(detail);
    if (!summary) { toast('Crew is unavailable'); return; }
    return openCrewChat({ ...detail, ...summary });
  }

  async function openCrewChat(crew) {
    const routeLoad = beginRoutedOverlayLoad({ kind: 'crew', id: crew.id });
    let data;
    try { data = await api(`/crews/${crew.id}/chat`); } catch (error) {
      if (!routedOverlayLoadIsCurrent(routeLoad)) return;
      toast(error.message); return;
    }
    if (!routedOverlayLoadIsCurrent(routeLoad)) return;
    refreshMe();
    const modal = openModal(`
      <div class="thread">
        <div class="thread-head">
          <button type="button" class="modal-close" style="font-size:18px" aria-label="Back">‹</button>
          <span class="inbox-room-icon crew" aria-hidden="true">👥</span>
          <button type="button" class="row-main crew-chat-head" id="crew-chat-head">
            <span class="row-title">${esc(crew.name)}</span>
            <span class="row-sub">${crew.member_count} player${crew.member_count === 1 ? '' : 's'} · tap for crew info ›</span>
          </button>
        </div>
        <div class="thread-msgs" id="crew-msgs" role="log" aria-live="polite" aria-relevant="additions" aria-label="${esc(crew.name)} crew conversation"></div>
        <form class="thread-input" id="crew-form">
          <input type="text" id="crew-text" aria-label="Message the crew" placeholder="Message the crew…" autocomplete="off" maxlength="2000" />
          <button type="submit" aria-label="Send">➤</button>
        </form>
      </div>
    `, { chat: true, route: { kind: 'crew', id: crew.id } });
    const msgsEl = modal.querySelector('#crew-msgs');
    const input = modal.querySelector('#crew-text');
    const chatUX = bindChatContinuity(modal, msgsEl, input, `crew:${crew.id}`);
    let lastId = 0;
    const renderMessages = (rawItems, append, { forceBottom = false, newMessages = false } = {}) => {
      const batch = prepareChatRenderBatch(msgsEl, rawItems || [], append);
      if (batch.newestId) lastId = Math.max(lastId, batch.newestId);
      const items = batch.items;
      if (append && !items.length) return;
      const snapshot = chatUX.captureScroll();
      const html = items.map((message) => {
        const mine = message.sender_id === state.me.id;
        return `<div class="chat-message-row ${mine ? 'is-mine' : 'is-theirs'}" data-message-row="${message.id}" style="display:flex;gap:8px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%">
          ${mine ? '' : `<div class="avatar sm" style="background:${esc(message.sender_color)}">${esc(initials(message.sender_name))}</div>`}
          <div class="bubble ${mine ? 'me' : 'them'}" data-message-id="${message.id}" style="max-width:100%">${message.heart_count ? `<span class="bubble-heart" data-heart-badge>❤️${message.heart_count > 1 ? ` ${message.heart_count}` : ''}</span>` : ''}
            ${mine ? '' : `<div style="font-size:11px;font-weight:700;opacity:.75;margin-bottom:2px">${esc(message.sender_name)}</div>`}
            ${message.has_image ? `<div data-img-id="${message.id}" style="min-height:60px;min-width:120px;margin-bottom:${message.body ? '6px' : '0'}">⏳</div>` : ''}
            ${esc(message.body)}<div class="bubble-time">${fmtTimeShort(message.created_at)}</div>
          </div>
          ${chatMessageActionHtml(message, mine)}
        </div>`;
      }).join('');
      if (append && !msgsEl.querySelector('.empty-state')) msgsEl.insertAdjacentHTML('beforeend', html);
      else if (append) msgsEl.innerHTML = html;
      else msgsEl.innerHTML = html || '<div class="empty-state" style="padding:20px">No messages yet — pick the next time! 👋</div>';
      chatUX.restoreScroll(snapshot, { forceBottom, newMessageCount: newMessages ? items.length : 0 });
      hydrateChatImages(msgsEl, chatUX);
    };
    renderMessages(data.items || data.messages || [], false, { forceBottom: true });
    chatUX.activateOutbox?.((message) => renderMessages([message], true, { forceBottom: true }));
    addPhotoToComposer(modal, '#crew-form', '#crew-text', chatUX);
    startAdaptiveChatPoll(modal, msgsEl, async () => {
      const fresh = await api(`/crews/${crew.id}/chat?since_id=${lastId}`);
      const items = fresh.items || fresh.messages || [];
      if (items.length) renderMessages(items, true, { newMessages: true });
      applyRoomHearts(msgsEl, fresh.heart_counts);
      return items.length > 0;
    });
    modal.querySelector('#crew-chat-head').addEventListener('click', () => {
      transitionModal(modal, () => openCrewScreen(crew.id));
    });
    modal.querySelector('#crew-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const body = input.value.trim();
      if (!body) return;
      try { await chatUX.send?.(body); } catch (error) { toast(error.message); }
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
          <button type="button" class="modal-close" style="font-size:18px" aria-label="Back">‹</button>
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
        <div class="chat-message-row ${mine ? 'is-mine' : 'is-theirs'}" data-message-row="${m.id}" style="display:flex;gap:8px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%">
          ${mine ? '' : `<div class="avatar sm" style="background:${esc(m.sender_color)}">${esc(initials(m.sender_name))}</div>`}
          <div class="bubble ${mine ? 'me' : 'them'}" data-message-id="${m.id}" style="max-width:100%">${m.heart_count ? `<span class="bubble-heart" data-heart-badge>❤️${m.heart_count > 1 ? ' ' + m.heart_count : ''}</span>` : ''}
            ${mine ? '' : `<div style="font-size:11px;font-weight:700;opacity:.75;margin-bottom:2px">${esc(m.sender_name)}</div>`}
            ${m.has_image ? `<div data-img-id="${m.id}" style="min-height:60px;min-width:120px;margin-bottom:${m.body ? '6px' : '0'}">⏳</div>` : ''}
            ${esc(m.body)}
            <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
          </div>
          ${chatMessageActionHtml(m, mine)}
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

    startAdaptiveChatPoll(modal, msgsEl, async () => {
      const fresh = await api(`/clubs/${club.id}/chat?since_id=${lastId}`);
      if (fresh.items.length) renderMsgs(fresh.items, true, { newMessages: true });
      applyRoomHearts(msgsEl, fresh.heart_counts);
      return fresh.items.length > 0;
    });

    makePressable(modal.querySelector('#club-head'), async () => {
      try {
        const fresh = await api(`/clubs/${club.id}`);
        transitionModal(modal, () => openClubInfo(fresh));
      } catch (e) { toast(e.message); }
    }, `Open ${club.name} club info`);
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
        ${isOwner && m.id !== state.me.id ? `<button class="btn btn-secondary btn-sm" data-boot="${m.id}" title="Remove from club" aria-label="Remove ${esc(m.display_name)} from club">✕</button>` : ''}
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

    makePressable(modal.querySelector('#club-court'), () => {
      transitionModal(modal, () => openCourtDetail(club.home_court_id));
    }, `Open ${club.home_court_name || 'home court'}`);
    modal.querySelectorAll('[data-open-game]').forEach((row) => makePressable(row, () => {
      transitionModal(modal, () => openGameScreen(Number(row.dataset.openGame)));
    }));
    modal.querySelectorAll('[data-open-club-tournament]').forEach((row) => makePressable(row, () => {
      transitionModal(modal, () => openTournamentScreen(Number(row.dataset.openClubTournament)));
    }));
    modal.querySelectorAll('[data-open-club-league]').forEach((row) => makePressable(row, () => {
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
      resultsEl.querySelectorAll('[data-open-club]').forEach((row) => makePressable(row, () => {
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
          <button type="button" class="modal-close" style="font-size:18px" aria-label="Back">‹</button>
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
        <div class="chat-message-row ${mine ? 'is-mine' : 'is-theirs'}" data-message-row="${m.id}" style="display:flex;gap:8px;align-self:${mine ? 'flex-end' : 'flex-start'};max-width:85%">
          ${mine ? '' : `<div class="avatar sm" style="background:${esc(m.sender_color)}">${esc(initials(m.sender_name))}</div>`}
          <div class="bubble ${mine ? 'me' : 'them'}" data-message-id="${m.id}" style="max-width:100%">${m.heart_count ? `<span class="bubble-heart" data-heart-badge>❤️${m.heart_count > 1 ? ' ' + m.heart_count : ''}</span>` : ''}
            ${mine ? '' : `<div style="font-size:11px;font-weight:700;opacity:.75;margin-bottom:2px">${esc(m.sender_name)}</div>`}
            ${m.has_image ? `<div data-img-id="${m.id}" style="min-height:60px;min-width:120px;margin-bottom:${m.body ? '6px' : '0'}">⏳</div>` : ''}
            ${esc(m.body)}
            <div class="bubble-time">${fmtTimeShort(m.created_at)}</div>
          </div>
          ${chatMessageActionHtml(m, mine)}
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

    startAdaptiveChatPoll(modal, msgsEl, async () => {
      const fresh = await api(`/games/${game.id}/chat?since_id=${lastId}`);
      if (fresh.items.length) renderMsgs(fresh.items, true, { newMessages: true });
      applyRoomHearts(msgsEl, fresh.heart_counts);
      return fresh.items.length > 0;
    });

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
    let profileMoreAction = '';
    if (userId !== state.me.id) {
      if (user.is_blocked) {
        friendAction = '<span class="tag warn" style="margin:0">🚫 Blocked</span>';
      } else if (user.friendship_status === 'accepted') {
        friendAction = '<button class="btn btn-primary" id="up-msg">Message</button>';
      } else if (user.friendship_status === 'pending') {
        friendAction = user.outgoing
          ? '<span class="tag" style="margin:0">Request pending…</span>'
          : `<button class="btn btn-primary" id="up-accept">Accept friend request</button>`;
      } else {
        friendAction = '<button class="btn btn-primary" id="up-add">＋ Add friend</button>';
      }
      profileMoreAction = `<details class="profile-more-actions">
        <summary class="btn btn-secondary" aria-label="More actions for ${esc(user.display_name)}">More</summary>
        <div class="profile-more-menu">
          ${user.friendship_status === 'accepted' ? '<button type="button" class="btn btn-secondary btn-block" id="up-challenge">Challenge to a ranked game</button>' : ''}
          ${user.friendship_status === 'accepted' ? '<button type="button" class="btn btn-secondary btn-block" id="up-remove">Remove friend</button>' : ''}
          <button type="button" class="btn btn-secondary btn-block" id="up-block">${user.is_blocked ? 'Unblock user' : 'Block user'}</button>
          <button type="button" class="btn btn-secondary btn-block" id="up-report">Report</button>
        </div>
      </details>`;
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
      <div class="action-row">${friendAction}${profileMoreAction}</div>
      ${upcoming.length ? `<div class="section-label">Upcoming games</div>${upcoming.map((g) => gameCardHtml(g, { compact: true })).join('')}` : ''}
      ${courts.length ? `<div class="section-label">Courts</div>${courts.map(courtRow).join('')}` : ''}
      ${games.length ? `<div class="section-label">Recent games</div>${games.map((g) => gameCardHtml(g, { compact: true })).join('')}` : ''}
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
      if (!court) { toast('Set a home court first (Me → Edit profile) to challenge'); return; }
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
      transitionModal(modal, () => openNewGameModal({
        preferredSlot: slot,
        invitees: [user],
        inviteUserIds: [userId],
        visibility: 'private',
      }));
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

  async function subscribeGamesCalendar() {
    const modalLoad = beginRoutedOverlayLoad(null);
    let webcal;
    let feed;
    try {
      const { token } = await api('/calendar/token');
      feed = `${location.host}/api/calendar/${token}.ics`;
      webcal = `webcal://${feed}`;
    } catch (error) {
      if (routedOverlayLoadIsCurrent(modalLoad)) toast(error.message);
      return;
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
      openModal(`
        ${modalHead('📅 Games calendar')}
        <p class="row-sub" style="margin-bottom:10px">In your calendar app, choose “Subscribe” or “Add calendar by URL” and paste this link.</p>
        <input type="text" readonly value="${esc(webcal)}" onclick="this.select()" style="font-size:12.5px" />
      `);
    }
  }

  function openNotificationSettings() {
    const notifications = Object.entries((state.me && state.me.muteable_notifications) || {});
    const modal = openModal(`
      ${modalHead('Notifications')}
      <p class="row-sub" style="margin-bottom:10px">Choose the optional updates you want. Score confirmations, invites, and challenges always come through.</p>
      <div class="settings-notification-list">
        ${notifications.length ? notifications.map(([kind, label]) => `
          <label class="card row" style="gap:10px;cursor:pointer">
            <span class="row-main"><span class="row-title" style="font-size:14px">${esc(label)}</span></span>
            <input type="checkbox" class="settings-notification-toggle" data-kind="${esc(kind)}" ${(state.me.muted_notifications || []).includes(kind) ? '' : 'checked'} style="width:20px;height:20px;flex:0 0 auto" />
          </label>`).join('') : '<div class="empty-state" style="padding:16px">No optional notification categories are available.</div>'}
      </div>
    `, { label: 'Notification settings' });
    modal.querySelectorAll('.settings-notification-toggle').forEach((toggle) => {
      toggle.addEventListener('change', async () => {
        const muted = [...modal.querySelectorAll('.settings-notification-toggle')]
          .filter((item) => !item.checked).map((item) => item.dataset.kind);
        toggle.disabled = true;
        try {
          applyMe(await api('/me', {
            method: 'PATCH', body: JSON.stringify({ muted_notifications: muted }),
          }));
        } catch (error) {
          toggle.checked = !toggle.checked;
          toast(error.message);
        } finally {
          toggle.disabled = false;
        }
      });
    });
  }

  async function loadBlockedPlayers(root) {
    const box = root.querySelector('[data-blocked-players]');
    if (!box) return;
    try {
      const data = await api('/users/blocked');
      box.innerHTML = data.items.length ? data.items.map((user) => `
        <div class="card row">
          ${avatarHtml(user, 'sm')}
          <div class="row-main"><div class="row-title" style="font-size:14px">${esc(user.display_name)}</div></div>
          <button type="button" class="btn btn-secondary btn-sm" data-unblock="${user.id}">Unblock</button>
        </div>`).join('') : '<div class="row-sub">No blocked players.</div>';
      box.querySelectorAll('[data-unblock]').forEach((button) => button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          await api(`/users/${button.dataset.unblock}/unblock`, { method: 'POST' });
          toast('Player unblocked');
          loadBlockedPlayers(root);
        } catch (error) {
          button.disabled = false;
          toast(error.message);
        }
      }));
    } catch {
      box.innerHTML = '<div class="row-sub">Could not load blocked players right now.</div>';
    }
  }

  function openPrivacySafetySettings() {
    const modal = openModal(`
      ${modalHead('Privacy & safety')}
      <button type="button" class="card row btn-reset" id="privacy-home-area" style="width:100%;text-align:left">
        <span aria-hidden="true">🏠</span><span class="row-main"><span class="row-title">Home area</span><span class="row-sub">${state.me.home_area ? esc(state.me.home_area) : 'Choose where nearby results begin'}</span></span><span class="chev">›</span>
      </button>
      <div class="card row">
        <span aria-hidden="true">📍</span><span class="row-main"><span class="row-title">Auto check-in</span><span class="row-sub">Only while Third Shot is open</span></span>
        <button type="button" class="btn btn-sm ${autoCheckInEnabled() ? 'btn-primary' : 'btn-secondary'}" id="privacy-auto-checkin" aria-pressed="${autoCheckInEnabled()}">${autoCheckInEnabled() ? 'On' : 'Off'}</button>
      </div>
      <button type="button" class="card row btn-reset" id="privacy-replay-setup" style="width:100%;text-align:left">
        <span aria-hidden="true">👋</span><span class="row-main"><span class="row-title">Quick setup</span><span class="row-sub">Review home-area choices</span></span><span class="chev">›</span>
      </button>
      <div class="section-label">Blocked players</div>
      <div data-blocked-players><div class="row-sub">Loading…</div></div>
    `, { label: 'Privacy and safety settings' });
    modal.querySelector('#privacy-home-area').addEventListener('click', () => {
      transitionModal(modal, () => openHomeAreaSheet({ onSet: renderProfile }));
    });
    modal.querySelector('#privacy-replay-setup').addEventListener('click', () => {
      transitionModal(modal, () => openHomeAreaOnboarding({ replay: true, onComplete: renderProfile }));
    });
    modal.querySelector('#privacy-auto-checkin').addEventListener('click', () => {
      if (!autoCheckInEnabled()) {
        transitionModal(modal, () => openAutoCheckInConsent(renderProfile));
        return;
      }
      setAutoCheckInEnabled(false);
      stopLocationWatch();
      toast('Auto check-in off');
      transitionModal(modal, openPrivacySafetySettings);
    });
    loadBlockedPlayers(modal);
  }

  function openAppearanceCalendarSettings() {
    const modal = openModal(`
      ${modalHead('Appearance & calendar')}
      <div class="section-label" style="margin-top:4px">Appearance</div>
      <div class="segmented" id="settings-theme" role="group" aria-label="Appearance">
        ${['auto', 'light', 'dark'].map((theme) => `<button type="button" data-theme-pick="${theme}" class="${themePref() === theme ? 'active' : ''}" aria-pressed="${themePref() === theme}">${theme === 'auto' ? 'Auto' : theme === 'light' ? 'Light' : 'Dark'}</button>`).join('')}
      </div>
      <div class="section-label">Games calendar</div>
      <button type="button" class="card row btn-reset" id="settings-calendar" style="width:100%;text-align:left">
        <span aria-hidden="true">📅</span><span class="row-main"><span class="row-title">Subscribe to your games</span><span class="row-sub">Keep upcoming games synced in any calendar app</span></span><span class="chev">›</span>
      </button>
    `, { label: 'Appearance and calendar settings' });
    modal.querySelectorAll('[data-theme-pick]').forEach((button) => button.addEventListener('click', () => {
      localStorage.setItem('pp_theme', button.dataset.themePick);
      applyTheme();
      modal.querySelectorAll('[data-theme-pick]').forEach((item) => {
        const active = item === button;
        item.classList.toggle('active', active);
        item.setAttribute('aria-pressed', String(active));
      });
    }));
    modal.querySelector('#settings-calendar').addEventListener('click', subscribeGamesCalendar);
  }

  function openAccountSettings() {
    const installHtml = !window.matchMedia('(display-mode: standalone)').matches
      ? (state.installPrompt
          ? '<button type="button" class="card row btn-reset" id="account-install" style="width:100%;text-align:left"><span aria-hidden="true">📲</span><span class="row-main"><span class="row-title">Install Third Shot</span><span class="row-sub">Open full screen and keep the app close</span></span><span class="chev">›</span></button>'
          : '<div class="card row"><span aria-hidden="true">📱</span><span class="row-main"><span class="row-title">Install Third Shot</span><span class="row-sub">Use your browser’s Add to Home Screen command</span></span></div>') : '';
    const modal = openModal(`
      ${modalHead('Account')}
      ${installHtml}
      <details class="settings-account-section">
        <summary>Change password</summary>
        <div class="form-field" style="margin-top:10px">
          <label class="sr-only" for="account-current-password">Current password</label>
          <input type="password" id="account-current-password" placeholder="Current password" autocomplete="current-password" />
          <label class="sr-only" for="account-new-password">New password</label>
          <input type="password" id="account-new-password" placeholder="New password (6+ characters)" autocomplete="new-password" style="margin-top:8px" />
          <button type="button" class="btn btn-secondary btn-block" id="account-password-save" style="margin-top:8px">Update password</button>
        </div>
      </details>
      <button type="button" class="btn btn-secondary btn-block" id="account-logout" style="margin-top:14px">Log out</button>
      <details class="settings-account-section" style="margin-top:18px">
        <summary style="color:#e03131">Delete account</summary>
        <div class="form-field" style="margin-top:10px">
          <p class="row-sub">Permanently removes your profile, friends, messages, and check-ins. This cannot be undone.</p>
          <label class="sr-only" for="account-delete-password">Confirm password</label>
          <input type="password" id="account-delete-password" placeholder="Confirm your password" autocomplete="current-password" />
          <button type="button" class="btn btn-danger btn-block" id="account-delete" style="margin-top:8px">Delete my account</button>
        </div>
      </details>
    `, { label: 'Account settings' });
    modal.querySelector('#account-install')?.addEventListener('click', async () => {
      const prompt = state.installPrompt;
      if (!prompt) return;
      state.installPrompt = null;
      try {
        prompt.prompt();
        const choice = await prompt.userChoice;
        toast(choice.outcome === 'accepted' ? 'Installing — see you on the home screen! 📲' : 'Maybe later 👍');
      } catch { /* browser cancelled */ }
    });
    modal.querySelector('#account-password-save').addEventListener('click', async (event) => {
      const current = modal.querySelector('#account-current-password').value;
      const next = modal.querySelector('#account-new-password').value;
      if (!current || !next) { toast('Fill in both password fields'); return; }
      event.currentTarget.disabled = true;
      try {
        await api('/auth/change-password', {
          method: 'POST', body: JSON.stringify({ current_password: current, new_password: next }),
        });
        modal.querySelector('#account-current-password').value = '';
        modal.querySelector('#account-new-password').value = '';
        toast('Password updated 🔒');
      } catch (error) {
        toast(/email or password/i.test(error.message) ? 'Current password is incorrect' : error.message);
      } finally {
        event.currentTarget.disabled = false;
      }
    });
    modal.querySelector('#account-logout').addEventListener('click', () => {
      closeModal(modal);
      logout();
    });
    modal.querySelector('#account-delete').addEventListener('click', async (event) => {
      const password = modal.querySelector('#account-delete-password').value;
      if (!password) { toast('Enter your password to confirm'); return; }
      if (!window.confirm('Delete your account forever? This cannot be undone.')) return;
      event.currentTarget.disabled = true;
      try {
        await api('/me', { method: 'DELETE', body: JSON.stringify({ password }) });
        closeModal(modal);
        logout();
      } catch (error) {
        event.currentTarget.disabled = false;
        toast(error.message);
      }
    });
  }

  function openSettingsHub() {
    const destinations = [
      ['edit-profile', '✏️', 'Edit profile', 'Name, photo, skill, availability, and home court'],
      ['notifications', '🔔', 'Notifications', 'Choose optional alerts'],
      ['privacy', '🛡️', 'Privacy & safety', 'Location choices and blocked players'],
      ['appearance', '🌗', 'Appearance & calendar', 'Theme and game calendar'],
      ['account', '🔐', 'Account', 'Password, install, sign out, or delete'],
    ];
    const modal = openModal(`
      ${modalHead('Settings')}
      <div class="settings-destinations">
        ${destinations.map(([key, icon, title, copy]) => `
          <button type="button" class="card row inbox-row settings-destination" data-settings-destination="${key}">
            <span aria-hidden="true" style="font-size:20px">${icon}</span>
            <span class="row-main"><span class="row-title" style="display:block">${title}</span><span class="row-sub" style="display:block">${copy}</span></span><span class="chev">›</span>
          </button>`).join('')}
      </div>
    `, { label: 'Settings' });
    const handlers = {
      'edit-profile': openEditProfile,
      notifications: openNotificationSettings,
      privacy: openPrivacySafetySettings,
      appearance: openAppearanceCalendarSettings,
      account: openAccountSettings,
    };
    modal.querySelectorAll('[data-settings-destination]').forEach((button) => {
      button.addEventListener('click', () => transitionModal(
        modal, handlers[button.dataset.settingsDestination],
      ));
    });
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
      <div class="profile-dashboard-head">
        <div class="profile-hero">
          ${avatarHtml(me)}
          <div class="profile-name">${esc(me.display_name)}</div>
          <div class="profile-sub">${skillLabel(me.skill_level)}${me.home_court_name ? ` · 🏠 ${esc(me.home_court_name)}` : ''}</div>
          ${me.bio ? `<p class="profile-sub" style="margin-top:8px">${esc(me.bio)}</p>` : ''}
        </div>
      </div>
      <div class="stat-grid">
        <button type="button" class="stat-card" id="pf-rankings" aria-label="Open rankings. Your rating is ${me.rating}"><div class="stat-value">${me.rating}</div><div class="stat-label">Rating${me.best_rating > me.rating ? ` · peak ${me.best_rating}` : ''} · Rankings</div></button>
        <div class="stat-card"><div class="stat-value">${me.ranked_wins}–${me.ranked_losses}</div><div class="stat-label">${(me.ranked_wins + me.ranked_losses) ? `Ranked record · ${winPct}%` : 'Ranked record'}</div></div>
        <div class="stat-card"><div class="stat-value">${me.current_streak >= 2 ? '🔥' : ''}${me.current_streak}</div><div class="stat-label">Streak · best ${me.best_streak}</div></div>
      </div>
      <div id="pf-upcoming" aria-busy="true" style="min-height:108px">
        <div class="section-label">Next game</div>${skeletonHtml(1)}
      </div>
      <div id="pf-courts" aria-busy="true" style="min-height:108px">
        <div class="section-label">Saved courts</div>${skeletonHtml(1)}
      </div>
      <div id="pf-history" aria-busy="true" style="min-height:166px">
        <div class="section-label">Recent games</div>${skeletonHtml(2)}
      </div>
      <details class="profile-dashboard-more">
        <summary>More stats and history</summary>
        <div class="profile-dashboard-actions" style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:4px 0 12px">
          <button type="button" class="btn btn-secondary btn-sm" id="pf-invite">Invite</button>
          <button type="button" class="btn btn-secondary btn-sm" id="pf-activity">Activity</button>
          <button type="button" class="btn btn-secondary btn-sm" id="pf-feedback">Feedback</button>
        </div>
        <div id="pf-play-stats" aria-busy="true" style="min-height:146px">
          <div class="section-label">Your play stats</div>${skeletonHtml(1)}
        </div>
        <div id="pf-upcoming-more">
        </div>
        <div id="pf-history-more">
        </div>
      </details>
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
    el.querySelector('#pf-invite').addEventListener('click', shareInviteLink);
    el.querySelector('#pf-activity').addEventListener('click', openActivity);
    el.querySelector('#pf-rankings').addEventListener('click', () => {
      state.playSeg = 'scores';
      switchTab('play');
    });
    const [mineResult, statsResult, favoritesResult, historyResult] = await profileDataPromise;
    if (!renderIsCurrent()) return;

    // Remove all reserved loading space in one paint, then hydrate the same
    // fixed section nodes. A newer Profile render (or another active tab) owns
    // the DOM as soon as the generation/current-view guard above stops matching.
    const statsEl = el.querySelector('#pf-play-stats');
    const upcomingEl = el.querySelector('#pf-upcoming');
    const upcomingMoreEl = el.querySelector('#pf-upcoming-more');
    const courtsEl = el.querySelector('#pf-courts');
    const historyEl = el.querySelector('#pf-history');
    const historyMoreEl = el.querySelector('#pf-history-more');
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
      const scorePending = (mine.items || []).filter((game) => instantRallyScorePending(game));
      const up = (mine.items || []).filter((game) =>
        game.status === 'upcoming' && !scorePending.includes(game) && !instantRallyClosed(game)
          && (instantRallyAssembly(game) || new Date(game.scheduled_at).getTime() > nowMs));
      const ordered = [...scorePending, ...up];
      const nextGame = ordered[0] || null;
      if (nextGame) {
        upcomingEl.innerHTML = `<div class="section-label">${scorePending.includes(nextGame) ? 'Played — enter the score' : 'Next game'}</div>${gameCardHtml(nextGame)}`;
        const remaining = ordered.slice(1);
        upcomingMoreEl.innerHTML = remaining.length
          ? `<div class="section-label">More upcoming games</div>${remaining.map((game) => gameCardHtml(game, { compact: true })).join('')}`
          : '';
        bindGameButtons(upcomingEl, renderProfile);
        bindGameButtons(upcomingMoreEl, renderProfile);
      } else {
        upcomingEl.innerHTML = '<div class="section-label">Next game</div><div class="empty-state" style="padding:14px">Nothing planned yet.<br><button type="button" class="btn btn-primary btn-sm" id="pf-plan-game" style="margin-top:9px">Plan a game</button></div>';
        upcomingEl.querySelector('#pf-plan-game').addEventListener('click', () => openNewGameModal());
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
      const savedCourtRowHtml = (c) => `
        <div class="card row" data-pfcourt="${c.id}" style="cursor:pointer">
          <span style="font-size:18px">${c.is_home ? '🏠' : '⭐'}</span>
          <div class="row-main">
            <div class="row-title" style="font-size:14px">${esc(c.name)}</div>
            <div class="row-sub">${esc(c.city || '')}${c.is_home ? ' · Home court' : ''}${c.rating_avg ? ` · ⭐ ${c.rating_avg}` : ''}</div>
          </div>
          <span class="chev">›</span>
        </div>`;
      const featuredCourts = rows.slice(0, 3);
      const moreCourts = rows.slice(3);
      courtsEl.innerHTML = '<div class="section-label">Saved courts</div>' + (rows.length
        ? `${featuredCourts.map(savedCourtRowHtml).join('')}${moreCourts.length ? `
            <details class="profile-saved-courts-more">
              <summary>See all ${rows.length} saved courts</summary>
              ${moreCourts.map(savedCourtRowHtml).join('')}
            </details>` : ''}`
        : '<div class="empty-state" style="padding:16px">No saved courts yet — tap ☆ on a court to save it.<br><button class="btn btn-secondary btn-sm" data-goto="courts-list" style="margin-top:10px">🗺 Browse courts</button></div>');
      courtsEl.querySelectorAll('[data-pfcourt]').forEach((row) =>
        makePressable(row, () => openCourtDetail(Number(row.dataset.pfcourt))));
    } catch { /* ignore */ }

    try {
      const history = historyResult.status === 'fulfilled' ? historyResult.value : null;
      if (!history) throw historyResult.reason;
      if (history.items.length) {
        historyEl.innerHTML = `<div class="section-label">Recent games</div>${history.items.slice(0, 3).map(resultRowHtml).join('')}`;
        bindGameButtons(historyEl, renderProfile);
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
          historyMoreEl.innerHTML = `
            <div class="section-label">Match history</div>
            <div class="quick-times" style="margin:0 0 10px">
              ${filters.map(([k, label]) => `<button type="button" data-hf="${k}" class="${k === active ? 'active' : ''}">${label}</button>`).join('')}
            </div>
            ${rows.length ? rows.map(resultRowHtml).join('')
              : '<div class="empty-state" style="padding:14px">No games match this filter yet.</div>'}`;
          historyMoreEl.querySelectorAll('[data-hf]').forEach((b) => b.addEventListener('click', () => {
            active = b.dataset.hf;
            render();
          }));
          bindGameButtons(historyMoreEl, renderProfile);
        };
        render();
      } else historyEl.innerHTML = '<div class="section-label">Recent games</div><div class="empty-state" style="padding:14px">Your completed games will show up here.</div>';
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

  }

  function gameFingerprint(game) {
    return JSON.stringify([
      game.status, game.is_instant, game.assembly_active, game.ready_count, game.roster_count,
      game.on_the_way_count, game.committed_count, game.physical_spots_left, game.spots_left,
      game.arrival_available, !!(game.arrival_capability || game.discovery_token),
      game.assembly_state, game.score_team1, game.score_team2, game.score_submitted_by,
      game.my_arrival && [game.my_arrival.id, game.my_arrival.active, game.my_arrival.arrives_at,
        game.my_arrival.expires_at, game.my_arrival.end_reason],
      (game.arrivals || []).map((arrival) => [arrival.id, arrival.user_id,
        arrival.arrives_at, arrival.expires_at, arrival.active]),
      game.waitlist_count, game.waitlist_position,
      game.open_call && [game.open_call.id, game.open_call.state, game.open_call.active,
        game.open_call.player_count, game.open_call.spots_left, game.open_call.waitlist_count,
        game.open_call.scheduled_at, game.open_call.can_withdraw],
      game.players.map((p) => [p.user_id, p.team, p.attending]).sort((x, y) => x[0] - y[0]),
    ]);
  }

  function gameScreenHtml(game) {
    const court = game.court || {};
    const isChallenge = String(game.notes || '').startsWith('⚔️');
    const assembly = instantRallyAssembly(game);
    const rally = assembly ? rallySummaryFromValue(game) : null;
    const myArrival = rally ? activeArrivalForGame(game.id, game.my_arrival, rally) : null;
    const closedRally = instantRallyClosed(game);
    const readyCount = assembly ? assembly.readyCount : game.players.length;
    const rosterCount = assembly ? assembly.rosterCount : game.players.length;
    const live = game.status === 'upcoming' && !closedRally
      && new Date(game.scheduled_at).getTime() <= Date.now();
    const completedParticipant = game.status === 'completed' && game.is_joined && state.me
      && game.players.some((player) => player.user_id === state.me.id && [1, 2].includes(player.team));
    const canFillRoster = game.status === 'upcoming' && game.is_joined
      && !closedRally && game.spots_left > 0 && (!game.is_instant || assembly);

    let emoji = '🏓';
    let headline = fmtDateTime(game.scheduled_at);
    let subline = `${readyCount}/${game.max_players} players`;
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
    } else if (assembly) {
      emoji = assembly.icon;
      headline = assembly.title;
      subline = assembly.sub;
    } else if (instantRallyScorePending(game)) {
      emoji = '📝';
      headline = 'Played? Enter the score';
      subline = 'This rally is closed to new players.';
    } else if (closedRally) {
      emoji = '😴';
      headline = 'Rally ended';
      subline = 'This rally closed without enough players.';
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
    const canRemove = (p) => game.is_creator && game.status === 'upcoming'
      && !closedRally && p.user_id !== game.creator_id;
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
    let playersHtml = (team1.length && team2.length)
      ? `<div class="form-grid">
          <div><div class="section-label" style="margin-top:0">Team 1</div>${team1.map(playerRow).join('')}</div>
          <div><div class="section-label" style="margin-top:0">Team 2</div>${team2.map(playerRow).join('')}</div>
        </div>`
      : game.players.map(playerRow).join('');
    if (!playersHtml && assembly && rosterCount > 0) {
      playersHtml = '<div class="empty-state" style="padding:12px">Player identities stay private until you join this rally at the court.</div>';
    }
    const arrivals = rally ? (Array.isArray(game.arrivals) ? game.arrivals : [])
      .map((value) => normalizeActiveArrival(value, rally)).filter(Boolean) : [];
    const arrivalsHtml = rally && rally.onWayCount > 0 ? `
      <div class="section-label">Arriving (${rally.onWayCount})</div>
      <div class="arrival-roster">
        ${arrivals.length ? arrivals.map((arrival) => {
          const person = arrival.user || {};
          const userId = safePositiveId(person.user_id ?? person.id);
          return `<div class="row arrival-roster-row"${userId ? ` data-view-user="${userId}"` : ''}>
            ${avatarHtml(person, 'sm')}
            <div class="row-main"><div class="row-title">${esc(person.display_name || 'Player')}</div>
              <div class="row-sub">ETA ${esc(fmtTimeShort(arrival.arrivesAt))} · spot held until ${esc(fmtTimeShort(arrival.expiresAt))}</div>
            </div><span class="tag live">Arriving</span>
          </div>`;
        }).join('') : '<div class="empty-state" style="padding:12px">One player is arriving. Their identity is visible only to the current court roster.</div>'}
      </div>` : '';

    let actions = '';
    if (game.status === 'upcoming') {
      if (closedRally) {
        actions = '<div class="empty-state" style="padding:12px">This rally is no longer accepting players. Start a new one when you’re ready.</div>';
      } else if (!game.is_joined && game.is_instant && assembly) {
        if (myArrival && !isCheckedInAtCourt(rally.courtId)) {
          actions = `<div class="arrival-inline-held"><b>✓ Your spot is held</b><span>${esc(arrivalReservationCopy(myArrival))}</span></div>
            <button class="btn btn-primary btn-block" id="gs-arrival-details" style="padding:16px">View trip details</button>`;
        } else if (isCheckedInAtCourt(rally.courtId) && (rally.spotsLeft > 0 || myArrival)) {
          actions = '<button class="btn btn-primary btn-block" id="gs-join" style="padding:16px"><svg class="pb-ic"><use href="#pb"/></svg> Join this game</button>';
        } else if (isCheckedInAtCourt(rally.courtId)) {
          actions = '<div class="empty-state" style="padding:12px">This game is full. Your court check-in stays active while we find another game here.</div><button class="btn btn-primary btn-block" id="gs-join" style="margin-top:10px;padding:16px">Find another game here</button>';
        } else if (!isCheckedInAtCourt(rally.courtId)
            && rally.arrivalAvailable && rally.arrivalCapability
            && rally.spotsLeft > 0 && rally.onWayCount === 0) {
          actions = `<button class="btn btn-primary btn-block" id="gs-arrival" style="padding:16px">Arrive in 5–15 min</button>
            <p class="arrival-action-note">Choose a 5, 10, or 15 minute arrival. You count as at the court after you check in there.</p>`;
        } else {
          actions = `<div class="empty-state" style="padding:12px">${!rally.arrivalAvailable
            ? '⌛ This rally is wrapping up, so travel spots are closed. Refresh Nearby for the next rally.'
            : rally.onWayCount > 0
              ? '🚗 The travel spot is held by another player. No additional spot is promised.'
              : '🏓 This rally is fully committed.'}</div>`;
        }
      } else if (!game.is_joined && game.spots_left > 0) {
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
        const moreActions = [];
        if (game.is_instant && game.players.length >= 2) {
          actions = '<button class="btn btn-primary btn-block" id="gs-score" style="padding:16px">✓ We finished — enter score</button>';
        } else if (!game.is_instant && !startsAhead && game.players.length >= 2) {
          actions = '<button class="btn btn-primary btn-block" id="gs-score" style="padding:16px">📝 Enter the score</button>';
        }
        if (!game.is_instant && startsAhead) {
          const mine = game.players.find((p) => p.user_id === (state.me && state.me.id));
          if (mine && !mine.attending) {
            // Vouching you'll show up is the main ask before a game starts.
            const attend = `<button class="btn ${actions ? 'btn-secondary' : 'btn-primary'} btn-block" id="gs-attend">👋 I'm coming — count me in</button>`;
            if (actions) moreActions.push(attend);
            else actions = attend;
          }
        }
        // A live, underfilled rally still needs recruiting; hiding these once
        // its start time passed was the sharpest post-create dead end.
        if (canFillRoster) {
          const fill = `<button class="btn ${actions ? 'btn-secondary' : 'btn-primary'} btn-block roster-boost-launch" id="gs-fill-roster">＋ Find players · ${game.spots_left} spot${game.spots_left === 1 ? '' : 's'} left</button>`;
          if (actions) moreActions.push(fill);
          else actions = fill;
        }
        if (!game.is_instant && startsAhead) {
          moreActions.push('<button class="btn btn-secondary btn-block" id="gs-calendar">📅 Add to calendar</button>');
          if (game.is_creator && game.recurrence !== 'weekly') {
            moreActions.push('<button class="btn btn-secondary btn-block" id="gs-reschedule">🕑 Reschedule</button>');
          }
        }
        moreActions.push('<button class="btn btn-secondary btn-block" id="gs-share">📤 Share game</button>');
        moreActions.push('<button class="btn btn-secondary btn-block" id="gs-leave">Leave game</button>');
        if (game.is_creator) {
          moreActions.push('<details class="game-danger-actions"><summary>Cancel this game…</summary><button class="btn btn-danger btn-block" id="gs-cancel">Cancel for everyone</button></details>');
        }
        if (moreActions.length) {
          actions += `<details class="game-more-actions">
            <summary>More</summary>
            <div class="game-more-actions-body">${moreActions.join('')}</div>
          </details>`;
        }
      }
    } else if (game.status === 'awaiting_confirmation' && game.awaiting_your_confirmation) {
      actions = `
        <button class="btn btn-primary btn-block" id="gs-confirm" style="padding:16px">✓ Confirm ${game.score_team1}–${game.score_team2}</button>
        <button class="btn btn-danger btn-block" id="gs-dispute" style="margin-top:10px">✕ That score is wrong</button>`;
    } else if (completedParticipant) {
      const savedCrew = completedGameCrewSummary(game);
      const crewInvitePending = !!savedCrew && (savedCrew.joined === false || savedCrew.invitation_pending);
      const mvpBanner = game.mvp ? `
        <div class="card" style="text-align:center;padding:10px 14px;margin-bottom:10px">
          <b>🌟 MVP: ${esc(game.mvp.display_name)}</b>
          <div class="row-sub">${game.mvp.votes} vote${game.mvp.votes === 1 ? '' : 's'} from the game</div>
        </div>` : '';
      const votables = game.players.filter((p) => [1, 2].includes(p.team) && p.user_id !== (state.me && state.me.id));
      const voteChips = votables.length ? `
        <div class="row-sub" style="margin:0 0 6px 2px">${game.my_mvp_vote ? 'Your MVP vote:' : 'Who carried the game? Vote MVP:'}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">
          ${votables.map((p) => `<button class="btn btn-sm ${game.my_mvp_vote === p.user_id ? 'btn-primary' : 'btn-secondary'}" data-mvp="${p.user_id}">🌟 ${esc(p.display_name.split(' ')[0])}</button>`).join('')}
        </div>` : '';
      actions = `${mvpBanner}${voteChips}
        <div class="postgame-next">
          <div class="postgame-next-copy"><b>${savedCrew ? crewInvitePending ? 'You’re invited to this crew' : 'Your crew is ready' : 'Keep this crew going'}</b><span>${savedCrew ? crewInvitePending ? `Join ${esc(savedCrew.name)}, then pick the next time.` : `Plan with ${esc(savedCrew.name)} or open the crew chat.` : 'Create a private crew, then pick the next time. Everyone chooses whether to join.'}</span></div>
          <button class="btn btn-primary btn-block" id="gs-plan-crew">${savedCrew ? crewInvitePending ? `👥 Join ${esc(savedCrew.name)} &amp; plan next game` : `📅 Plan next game with ${esc(savedCrew.name)}` : '👥 Create crew &amp; plan next game'}</button>
          ${savedCrew && !crewInvitePending ? `<button class="btn btn-secondary btn-block" id="gs-open-crew" data-crew-id="${savedCrew.id}">👥 Open crew</button>` : ''}
          <button class="btn btn-secondary btn-block" id="gs-rematch">⚡ Play again now at ${esc(court.name || 'this court')}</button>
        </div>
        <div id="gs-crew-connect" aria-live="polite"><div class="postgame-connection-loading">Checking who you still need to connect with…</div></div>`;
    }

    return `
      <div class="modal-head">
        <div style="flex:1">
          <h3>${emoji} ${headline} ${game.is_instant ? `<span class="tag${assembly ? ' live' : ''}" style="margin:0 0 0 6px">${assembly ? 'Rally now' : 'Rally'}</span>` : game.game_type === 'ranked' ? '<span class="tag ranked" style="margin:0 0 0 6px">Ranked</span>' : '<span class="tag" style="margin:0 0 0 6px">Casual</span>'}${game.recurrence === 'weekly' ? '<span class="tag" style="margin:0 0 0 6px">🔁 Weekly</span>' : ''}${game.preferred_level && game.preferred_level !== 'any' ? `<span class="tag" style="margin:0 0 0 6px">🎚 ${skillLabel(game.preferred_level)}</span>` : ''}${game.club_name ? `<span class="tag" style="margin:0 0 0 6px">🏛 ${esc(game.club_name)}</span>` : ''}</h3>
          <div class="row-sub">${subline}</div>
        </div>
        ${game.is_joined ? `<button class="icon-btn" id="gs-chat" title="Game chat — current players only" aria-label="Game chat — current players only" style="box-shadow:none;font-size:17px;position:relative">💬${game.chat_unread ? `<span class="badge" style="top:-2px;right:-4px">${game.chat_unread > 9 ? '9+' : game.chat_unread}</span>` : ''}</button>` : ''}
        ${!game.is_joined && !canFillRoster ? '<button class="icon-btn" id="gs-share" title="Share game" aria-label="Share game" style="box-shadow:none;font-size:17px">📤</button>' : ''}
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
      ${game.is_instant && courtDirectionsUrl(court)
        ? `<a class="btn btn-secondary btn-block gs-directions" href="${courtDirectionsUrl(court)}" target="_blank" rel="noopener" aria-label="Directions to ${esc(court.name || 'the court')} (opens Maps)">Directions</a>` : ''}
      <div id="gs-weather"></div>
      <div id="gs-stakes"></div>
      ${game.notes && !(game.is_instant && game.notes === '⚡ Instant rally') ? `<div class="row-sub" style="margin:0 0 12px 4px">“${esc(game.notes)}”</div>` : ''}
      <div class="section-label">${assembly ? `At the court (${readyCount}/${game.max_players})` : `Players (${readyCount}/${game.max_players})`}</div>
      ${playersHtml}
      ${arrivalsHtml}
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
    let rematchAttempt = readRematchAttempt(gameId);
    let crewPromise = null;
    const loadCrew = (refresh = false) => {
      if (refresh) crewPromise = null;
      if (!crewPromise) {
        crewPromise = Promise.all([
          api(`/games/${gameId}/crew`),
          api('/crews/mine').catch(() => ({ items: [] })),
        ]).then(([response, mine]) => {
          if (!completedGameCrewSummary(game, response)) {
            const savedCrew = (mine.items || []).find((item) => Number(
              item.source_game_id ?? (item.source_game && item.source_game.id),
            ) === Number(gameId));
            if (savedCrew) response.saved_crew = savedCrew;
          }
          return response;
        }).catch((error) => {
          crewPromise = null;
          throw error;
        });
      }
      return crewPromise;
    };

    const render = (fresh) => {
      game = fresh;
      fingerprint = gameFingerprint(game);
      box.innerHTML = gameScreenHtml(game);
      bind();
    };

    const reopenFresh = async () => {
      try { render(await api(`/games/${gameId}`)); } catch (e) { toast(e.message); }
    };

    const rememberFresh = (fresh) => {
      if (!fresh || Number(fresh.id) !== Number(gameId)) return false;
      game = fresh;
      fingerprint = gameFingerprint(fresh);
      return true;
    };

    function bind() {
      const court = game.court || {};
      const isChallenge = String(game.notes || '').startsWith('⚔️');
      const bindSavedCrewButton = () => {
        const openButton = box.querySelector('#gs-open-crew');
        if (!openButton || openButton.dataset.bound === 'true') return;
        openButton.dataset.bound = 'true';
        openButton.addEventListener('click', () => {
          transitionModal(modal, () => openCrewScreen(Number(openButton.dataset.crewId)));
        });
      };
      const showSavedCrew = (value) => {
        const savedCrew = crewSummaryFrom(value);
        const next = box.querySelector('.postgame-next');
        const plan = box.querySelector('#gs-plan-crew');
        if (!savedCrew || !next || !plan) return;
        game.crew = savedCrew;
        const pending = savedCrew.joined === false || savedCrew.invitation_pending;
        const copy = next.querySelector('.postgame-next-copy');
        if (copy) copy.innerHTML = pending
          ? `<b>You’re invited to this crew</b><span>Join ${esc(savedCrew.name)}, then pick the next time.</span>`
          : `<b>Your crew is ready</b><span>Plan with ${esc(savedCrew.name)} or open the crew chat.</span>`;
        plan.textContent = pending
          ? `👥 Join ${savedCrew.name} & plan next game`
          : `📅 Plan next game with ${savedCrew.name}`;
        let openButton = next.querySelector('#gs-open-crew');
        if (pending) { openButton?.remove(); return; }
        if (!openButton) {
          openButton = document.createElement('button');
          openButton.type = 'button';
          openButton.id = 'gs-open-crew';
          openButton.className = 'btn btn-secondary btn-block';
          next.insertBefore(openButton, next.querySelector('#gs-rematch'));
        }
        openButton.dataset.crewId = String(savedCrew.id);
        openButton.textContent = '👥 Open crew';
        bindSavedCrewButton();
      };
      bindSavedCrewButton();
      const crewTarget = box.querySelector('#gs-crew-connect');
      if (crewTarget) {
        const hydrateCrewConnections = async (refresh = false) => {
          try {
            const response = await loadCrew(refresh);
            if (!document.body.contains(crewTarget)) return;
            showSavedCrew(response.crew || response.saved_crew);
            crewTarget.innerHTML = completedCrewConnectionsHtml(response.items || []);
            bindUserButtons(crewTarget);
            crewTarget.querySelectorAll('[data-connect-crew]').forEach((connectButton) => {
              connectButton.addEventListener('click', async () => {
                const userId = Number(connectButton.dataset.connectCrew);
                const kind = connectButton.dataset.connectKind;
                connectButton.disabled = true;
                connectButton.textContent = kind === 'accept' ? 'Accepting…' : 'Sending…';
                try {
                  if (kind === 'accept') {
                    await api(`/friends/${connectButton.dataset.friendshipId}/respond`, {
                      method: 'POST', body: JSON.stringify({ accept: true }),
                    });
                    toast('You’re connected! 🤝');
                  } else {
                    await api('/friends/request', {
                      method: 'POST', body: JSON.stringify({ user_id: userId }),
                    });
                    toast('Friend request sent');
                  }
                } catch (error) {
                  if (!['request_already_sent', 'already_friends', 'not_pending'].includes(error.code)) {
                    toast(error.message);
                    connectButton.disabled = false;
                    connectButton.textContent = kind === 'accept' ? 'Accept' : '＋ Add';
                    return;
                  }
                }
                hydrateCrewConnections(true);
              });
            });
          } catch (error) {
            if (!document.body.contains(crewTarget)) return;
            crewTarget.innerHTML = '<button type="button" class="btn btn-secondary btn-block" id="gs-crew-retry">Retry connection check</button>';
            crewTarget.querySelector('#gs-crew-retry').addEventListener('click', () => hydrateCrewConnections(true));
          }
        };
        hydrateCrewConnections();
      }
      box.querySelector('#gs-court')?.addEventListener('click', () => transitionModal(modal, () => openCourtDetail(court.id)));
      box.querySelector('#gs-chat')?.addEventListener('click', () => openGameChat(game));
      box.querySelector('#gs-calendar')?.addEventListener('click', () => downloadIcs(game));
      box.querySelector('#gs-fill-roster')?.addEventListener('click', () => {
        transitionModal(modal, () => openRosterBoostSheet(game));
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
      box.querySelector('#gs-waitlist')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        if (button.disabled) return;
        if (button.dataset.undoWaitlist === 'true') {
          clearTimeout(button._confirmationTimer);
          button.disabled = true;
          button.textContent = 'Leaving…';
          try {
            const fresh = await api(`/games/${gameId}/waitlist/leave`, { method: 'POST' });
            rememberFresh(fresh);
            button.textContent = 'Left waitlist ✓';
            toast('Left the waitlist');
            setTimeout(() => render(game), 650);
          } catch (e) {
            button.disabled = false;
            button.textContent = button.dataset.confirmationLabel || 'Waitlisted · Leave';
            toast(e.message);
          }
          return;
        }
        const original = button.textContent;
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        button.textContent = 'Joining waitlist…';
        try {
          const fresh = await api(`/games/${gameId}/waitlist`, { method: 'POST' });
          rememberFresh(fresh);
          const position = Number(fresh.waitlist_position) || null;
          button.dataset.undoWaitlist = 'true';
          button.dataset.confirmationLabel = position ? `Waitlisted #${position} · Leave` : 'Waitlisted · Leave';
          button.disabled = false;
          button.removeAttribute('aria-busy');
          button.textContent = button.dataset.confirmationLabel;
          button.setAttribute('aria-label', `${button.dataset.confirmationLabel}. Leave the waitlist`);
          toast("Waitlisted — we'll let you know if a spot opens ⏳");
          button._confirmationTimer = setTimeout(() => render(game), 4000);
        } catch (e) {
          button.disabled = false;
          button.removeAttribute('aria-busy');
          button.textContent = original;
          toast(e.message);
        }
      });
      box.querySelector('#gs-waitlist-leave')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        if (button.disabled) return;
        button.disabled = true;
        button.textContent = 'Leaving…';
        try {
          const fresh = await api(`/games/${gameId}/waitlist/leave`, { method: 'POST' });
          rememberFresh(fresh);
          button.textContent = 'Left waitlist ✓';
          toast('Left the waitlist');
          setTimeout(() => render(game), 650);
        } catch (e) {
          button.disabled = false;
          button.textContent = 'Leave waitlist';
          toast(e.message);
        }
      });
      box.querySelector('#gs-share')?.addEventListener('click', () => shareGame(game));
      box.querySelector('#gs-arrival')?.addEventListener('click', () => {
        const gameRally = rallySummaryFromValue(game);
        transitionModal(modal, () => openRallyArrivalSheet(gameRally));
      });
      box.querySelector('#gs-arrival-details')?.addEventListener('click', () => {
        const gameRally = rallySummaryFromValue(game);
        const arrival = activeArrivalForGame(game.id, game.my_arrival, gameRally);
        if (arrival) transitionModal(modal, () => openArrivalDetails(arrival));
        else reopenFresh();
      });
      box.querySelector('#gs-join')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        if (game.is_instant) {
          await openReadyRally(rallySummaryFromValue(game), button);
          return;
        }
        if (button.disabled) return;
        if (button.dataset.undoJoin === 'true') {
          clearTimeout(button._confirmationTimer);
          button.disabled = true;
          button.textContent = 'Undoing…';
          try {
            const fresh = await api(`/games/${gameId}/leave`, { method: 'POST' });
            rememberFresh(fresh);
            button.textContent = 'Left game ✓';
            toast('Join undone');
            refreshMe();
            setTimeout(() => render(game), 650);
          } catch (e) {
            button.disabled = false;
            button.textContent = 'Joined ✓ · Undo';
            toast(e.message);
          }
          return;
        }
        const original = button.innerHTML;
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        button.textContent = 'Joining…';
        try {
          const fresh = await api(`/games/${gameId}/join`, { method: 'POST' });
          rememberFresh(fresh);
          button.dataset.undoJoin = 'true';
          button.disabled = false;
          button.removeAttribute('aria-busy');
          button.textContent = 'Joined ✓ · Undo';
          button.setAttribute('aria-label', 'Joined. Undo joining this game');
          toast(isChallenge ? 'Challenge accepted! ⚔️' : "You're in! 🏓");
          refreshMe();
          button._confirmationTimer = setTimeout(() => render(game), 4000);
        } catch (e) {
          button.disabled = false;
          button.removeAttribute('aria-busy');
          button.innerHTML = original;
          toast(e.message);
        }
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
      box.querySelector('#gs-plan-crew')?.addEventListener('click', (event) => {
        openCompletedCrewPlanner(game, modal, event.currentTarget, loadCrew());
      });
      const rematchButton = box.querySelector('#gs-rematch');
      const rematchLabel = () => {
        rematchAttempt = readRematchAttempt(gameId);
        if (rematchAttempt && rematchAttempt.gameId) return '↗ Open the rematch';
        if (rematchAttempt && rematchAttempt.payload) return '↻ Continue starting the rematch';
        return `⚡ Play again now at ${court.name || 'this court'}`;
      };
      if (rematchButton) rematchButton.textContent = rematchLabel();
      rematchButton?.addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        if (btn.disabled) return;
        rematchAttempt = readRematchAttempt(gameId);
        if (rematchAttempt && rematchAttempt.gameId) {
          transitionModal(modal, () => openGameScreen(rematchAttempt.gameId));
          return;
        }
        const planButton = box.querySelector('#gs-plan-crew');
        [btn, planButton].filter(Boolean).forEach((item) => { item.disabled = true; });
        const restoreIntents = () => {
          btn.disabled = false;
          btn.textContent = rematchLabel();
          if (planButton) planButton.disabled = false;
        };
        btn.textContent = rematchAttempt ? 'Checking the rematch…' : 'Starting the rematch…';
        let requestPayload = rematchAttempt && rematchAttempt.payload;
        let postStarted = false;
        try {
          if (!requestPayload) {
            const crew = (await loadCrew()).items || [];
            const others = crew.map((person) => person.id);
            if (!others.length) {
              toast('No eligible teammates are available for this rematch');
              restoreIntents();
              return;
            }
            requestPayload = writeRematchAttempt(gameId, {
              court_id: court.id,
              scheduled_at: new Date().toISOString(),
              game_type: game.game_type,
              max_players: game.max_players,
              preferred_level: game.preferred_level || 'any',
              visibility: 'private',
              recurrence: 'none',
              invite_user_ids: others,
              require_all_invitees: true,
              source_game_id: game.id,
              notes: '↺ Rematch!',
              client_attempt_id: rematchClientAttemptId(gameId),
            });
            if (!requestPayload) {
              toast('Nothing was sent because this browser could not save the rematch');
              restoreIntents();
              return;
            }
            rematchAttempt = readRematchAttempt(gameId);
          }
          postStarted = true;
          const rematch = await api('/games', {
            method: 'POST',
            body: JSON.stringify(requestPayload),
          });
          writeRematchAttempt(gameId, requestPayload, rematch.id);
          rematchAttempt = readRematchAttempt(gameId);
          toast('Rematch is on — the crew is invited ⚔️');
          refreshMe();
          transitionModal(modal, () => openGameScreen(rematch.id));
        } catch (err) {
          if (postStarted && err.code === 'client_attempt_id_conflict') {
            const existingGameId = Number(err.data && err.data.existing_game_id);
            if (Number.isSafeInteger(existingGameId) && existingGameId > 0) {
              writeRematchAttempt(gameId, requestPayload, existingGameId);
              rematchAttempt = readRematchAttempt(gameId);
              toast('That rematch already exists — opening it');
              refreshMe();
              transitionModal(modal, () => openGameScreen(existingGameId));
              return;
            }
          }
          const ambiguous = postStarted && (
            err.isNetworkError || Number(err.status) === 429 || Number(err.status) >= 500
            || err.code === 'client_attempt_id_conflict'
          );
          if (err.code === 'crew_changed' || (!ambiguous && postStarted)) {
            clearRematchAttempt(gameId);
            rematchAttempt = null;
          }
          if (err.code === 'crew_changed') loadCrew(true);
          toast(ambiguous
            ? 'We couldn’t confirm the rematch — tap Continue rematch to check it'
            : err.message);
          restoreIntents();
        }
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
      if (document.hidden || state.connectionState === 'offline' || currentOverlayEntry()?.el !== modal) return;
      try {
        const fresh = await api(`/games/${gameId}`);
        if (gameFingerprint(fresh) !== fingerprint) {
          render(fresh);
          refreshMe();
        }
      } catch { /* offline */ }
    }, LIVE_DETAIL_POLL_INTERVAL_MS);
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
    const icons = { friend_request: '🤝', friend_accept: '🎉', game_join: '🏓', game_cancelled: '🚫', ranked_result: '🏆', game_invite: '📅', game_invite_direct: '📨', score_submitted: '📝', score_confirmed: '✅', score_disputed: '⚠️', challenge: '⚔️', challenge_declined: '🙅', game_reminder: '⏰', game_message: '💬', session_rsvp: '🔁', friend_checkin: '📍', court_game: '⭐', weekly_recap: '📊', game_logged: '✍️', badge_earned: '🏅', player_coming: '🏓', player_left: '🚪', rally_arrival: '🚗', rally_arrival_ended: '⚠️', rally_arrival_cancelled: '↩️', rally_arrival_expired: '⌛', player_arriving: '🚗', arrival_cancelled: '↩️', tournament_join: '📥', tournament_invite: '🎽', tournament_withdraw: '↩️', tournament_start: '🏁', tournament_match: '🎯', tournament_score: '🆚', tournament_result: '👑', tournament_cancelled: '🚫', tournament_message: '💬', tournament_update: '🕑', tournament_reminder: '⏰', invite_declined: '🙅', club_join: '🙌', club_message: '💬', club_update: '🏛', club_invite: '🎟', club_game: '📣', crew_invite: '👥', crew_message: '💬', crew_update: '👥', league_update: '📦', league_match: '🎯', league_message: '💬', nearby_games: '🗓', streak_nag: '🔥' };
    // Where each notification taps to: game if it references one, else the other user for friend events.
    const targetFor = (n) => {
      const actionRoute = safeNotificationOverlayRoute(n.action_url);
      if (actionRoute) return { type: 'route', ...actionRoute };
      const relatedMatchId = Number(n.related_match_id || n.match_id || 0) || null;
      if (n.related_crew_id) return { type: 'crew', id: n.related_crew_id };
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
        <div class="card row" ${t ? `data-notif-type="${t.type}" data-notif-kind="${esc(n.kind || t.kind || '')}" data-notif-id="${t.id}" data-notif-match="${t.matchId || ''}" style="cursor:pointer"` : ''}>
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
        else if (kind === 'crew' && row.dataset.notifKind === 'crew_message') openCrewChatById(Number(row.dataset.notifId));
        else if (kind === 'crew') openCrewScreen(Number(row.dataset.notifId));
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
      clearLookingBanner();
      refreshLookingBanner();
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
    let searchSeq = 0;
    const renderFeedback = (message, isError = false) => {
      resultsEl.innerHTML = `<div class="city-search-feedback${isError ? ' is-error' : ''}" role="${isError ? 'alert' : 'status'}">${esc(message)}</div>`;
    };
    input.addEventListener('input', () => {
      clearTimeout(timer);
      const seq = ++searchSeq;
      const q = input.value.trim();
      resultsEl.removeAttribute('aria-busy');
      if (q.length < 3) { resultsEl.innerHTML = ''; return; }
      renderFeedback('Searching cities…');
      resultsEl.setAttribute('aria-busy', 'true');
      timer = setTimeout(async () => {
        try {
          const response = await api(`/geocode?q=${encodeURIComponent(q)}`);
          if (seq !== searchSeq || input.value.trim() !== q) return;
          const places = (response.items || []).slice(0, 4);
          resultsEl.removeAttribute('aria-busy');
          if (!places.length) {
            renderFeedback(`No cities found for “${q}”.`);
            return;
          }
          resultsEl.innerHTML = places.map((p, i) => `
            <button type="button" class="card row city-search-result" data-city="${i}" aria-label="Use ${esc(p.label)} as your home area">
              <span>📍</span>
              <div class="row-main">
                <div class="row-title" style="font-size:14px">${esc(p.label)}</div>
                <div class="row-sub">${esc((p.detail || '').split(',').slice(1, 3).join(',').trim())}</div>
              </div>
            </button>`).join('');
          resultsEl.querySelectorAll('[data-city]').forEach((row) => {
            row.addEventListener('click', () => onPick(places[Number(row.dataset.city)]));
          });
        } catch {
          if (seq !== searchSeq || input.value.trim() !== q) return;
          resultsEl.removeAttribute('aria-busy');
          renderFeedback('Couldn’t search cities. Check your connection and try again.', true);
        }
      }, 350);
    });
  }

  // Home-area picker: device location or a city search. Used by onboarding
  // and the profile's Set/Change button.
  function openHomeAreaSheet({ intro, dismissLabel = 'Cancel', onSet, onDismiss } = {}) {
    const modal = openModal(`
      <div class="checkin-sheet">
        <div class="celebrate-emoji" style="font-size:46px">📍</div>
        <h3 style="margin:6px 0 2px">Where do you usually play?</h3>
        <p class="row-sub" style="margin-bottom:18px">${esc(intro || 'Courts, games, and players near here greet you when the app opens.')}</p>
        <button class="btn btn-primary btn-block" id="ha-loc" style="padding:15px;margin-bottom:8px">Use my current location</button>
        <div class="form-field" style="margin:2px 0 0">
          <label class="sr-only" for="ha-city">Search for your home city</label>
          <input type="search" id="ha-city" placeholder="Or search your city…" autocomplete="off" />
          <div id="ha-results" aria-live="polite"></div>
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
  // Saved filter, court chat, and new-game pings, so an empty list is a
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
        <p class="row-sub" style="margin-bottom:14px">The best-known courts near you — saved courts get their own court chat and game alerts.</p>
        ${courts.map((c) => `
          <div class="card row" style="padding:11px;text-align:left">
            <div class="row-main">
              <div class="row-title" style="font-size:14px">${esc(c.name)}</div>
              <div class="row-sub">${[esc(c.city || ''), `${c.num_courts} court${c.num_courts === 1 ? '' : 's'}`, c.rating_avg ? `⭐ ${c.rating_avg}` : ''].filter(Boolean).join(' · ')}</div>
            </div>
            <button class="btn btn-secondary btn-sm" data-star-court="${c.id}" aria-label="Save ${esc(c.name)}" style="font-size:16px;min-width:44px">☆</button>
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

  function homeAreaOnboardingKey(accountId = state.me && state.me.id) {
    const id = Number(accountId);
    return Number.isSafeInteger(id) && id > 0 ? `pp_onboarded_home:${id}` : '';
  }

  function completeHomeAreaOnboarding(accountId = state.me && state.me.id) {
    const key = homeAreaOnboardingKey(accountId);
    if (key) localStorage.setItem(key, '1');
  }

  function openHomeAreaOnboarding({ replay = false, onComplete } = {}) {
    if (!state.me) return null;
    const accountId = state.me.id;
    const finish = () => {
      completeHomeAreaOnboarding(accountId);
      if (Number(state.me && state.me.id) === Number(accountId)) onComplete?.();
    };
    return openHomeAreaSheet({
      intro: replay
        ? 'Choose the area Third Shot should use for nearby courts, games, and players.'
        : 'Optional: choose a home area so Third Shot opens near the courts and players you care about.',
      dismissLabel: replay ? 'Keep current area' : 'Maybe later',
      onSet: finish,
      onDismiss: finish,
    });
  }

  function maybeOnboardHomeArea() {
    if (!state.me) return;
    const key = homeAreaOnboardingKey();
    if (!key) return;
    if (state.me.home_lat != null) {
      completeHomeAreaOnboarding();
      return;
    }
    if (localStorage.getItem(key) === '1') return;
    openHomeAreaOnboarding();
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
        const pingToken = state.token;
        const pingPresenceIdentity = JSON.stringify([
          true,
          state.presence.court_id || null,
          state.presence.checked_in_at || null,
        ]);
        api('/presence/ping', { method: 'POST' }).then((data) => {
          if (state.token !== pingToken || !data || !data.presence) return;
          const currentPresenceIdentity = JSON.stringify([
            !!state.presence?.checked_in,
            state.presence?.court_id || null,
            state.presence?.checked_in_at || null,
          ]);
          if (currentPresenceIdentity !== pingPresenceIdentity) return;
          const wasCheckedIn = !!state.presence?.checked_in;
          const nextPresence = data.presence;
          const changed = JSON.stringify([
            wasCheckedIn, state.presence?.court_id || null,
          ]) !== JSON.stringify([
            !!nextPresence.checked_in, nextPresence.court_id || null,
          ]);
          if (!changed) return;
          // A heartbeat can hit the absolute privacy cap. Its mutation result
          // is newer than any in-flight /me snapshot and owns presence now.
          invalidateMeRequests();
          state.presence = nextPresence;
          state.playGamesCache = null;
          renderPresenceBanner();
          if (wasCheckedIn && !nextPresence.checked_in) {
            toast('Your court check-in expired — confirm again if you’re still there.');
          }
          if (state.tab === 'play') renderPlay({ useCachedData: true });
          refreshLookingBanner();
          refreshMe();
        }).catch(() => {});
      }
    }, ME_POLL_INTERVAL_MS);
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
    let lastForegroundRefreshAt = 0;
    const refreshForegroundState = () => {
      if (document.hidden || !state.token || state.connectionState === 'offline') return;
      const now = Date.now();
      if (now - lastForegroundRefreshAt < 5000) return;
      lastForegroundRefreshAt = now;
      refreshMe();
    };
    window.addEventListener('offline', () => setConnectionState('offline'));
    window.addEventListener('online', () => {
      setConnectionState('online');
      toast('Back online 🏓');
      if (state.token) {
        flushChatOutboxForAccount(state.me && state.me.id);
        refreshForegroundState();
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
      else {
        if (autoCheckInEnabled()) startLocationWatch();
        refreshForegroundState();
      }
    });
    window.addEventListener('focus', refreshForegroundState);
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
    else if (route.kind === 'crew') openCrewScreen(route.id);
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
    if (/^#(?:club|crew|invite)\//.test(location.hash)) return 'chat';
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
    const crewMatch = location.hash.match(/^#crew\/(\d+)$/);
    if (crewMatch) { const id = Number(crewMatch[1]); prepareRoute('crew', id); openCrewScreen(id); return true; }
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

  // Repaint ❤️ badges from a bounded {message_id: count} snapshot. Only
  // IDs present in the snapshot are authoritative; older rendered messages
  // stay untouched after they move outside the server's sync window.
  // No-ops for DM threads (their payloads don't include heart_counts).
  function applyRoomHearts(root, counts) {
    if (!counts || !root) return;
    root.querySelectorAll('.bubble[data-message-id]').forEach((b) => {
      const id = b.dataset.messageId;
      if (!Object.prototype.hasOwnProperty.call(counts, id)) return;
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

  // Message bubbles are inert. Every chat exposes the same adjacent native
  // button for a reaction or deletion, so the action is visible and keyboard
  // accessible without turning conversational content into a hidden target.
  document.addEventListener('click', async (e) => {
    const button = e.target.closest('.chat-message-action[data-message-action][data-message-id]');
    if (!button || button.disabled) return;
    const id = Number(button.dataset.messageId);
    const row = button.closest('[data-message-row]') || button.parentElement;
    const bubble = row?.querySelector(`.bubble[data-message-id="${id}"]`);
    if (!id || !bubble) return;
    if (button.dataset.messageAction === 'delete') {
      if (!confirm('Delete this message?')) return;
      button.disabled = true;
      try {
        await api(`/messages/${id}`, { method: 'DELETE' });
        row.remove();
        toast('Message deleted');
      } catch (err) {
        button.disabled = false;
        toast(err.message);
      }
      return;
    }
    button.disabled = true;
    try {
      const res = await api(`/messages/${id}/heart`, { method: 'POST' });
      let badge = bubble.querySelector('.bubble-heart');
      const count = res.heart_count == null ? (res.hearted ? 1 : 0) : Number(res.heart_count);
      if (count) {
        const label = `❤️${count > 1 ? ' ' + count : ''}`;
        if (badge) badge.textContent = label;
        else bubble.insertAdjacentHTML('afterbegin', `<span class="bubble-heart" data-heart-badge>${label}</span>`);
      } else if (badge) badge.remove();
    } catch (err) { toast(err.message); }
    finally { button.disabled = false; }
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
