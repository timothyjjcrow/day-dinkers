/**
 * Sessions module — Open-to-Play system replacing Games.
 * Handles creating, joining, listing play sessions (now or scheduled),
 * session detail with chat, and invite friends.
 */
const Sessions = {
    currentSessionId: null,
    currentSessionCourtId: null,
    sessionsById: {},
    cachedListSessions: [],
    calendarMonthKey: null,
    calendarSelectedDayKey: null,
    calendarWeekOffset: 0,
    calendarExpanded: false,
    scheduleControlsBound: false,

    async load() {
        const list = document.getElementById('sessions-list');
        if (!list) return;
        list.innerHTML = '<div class="loading">Loading sessions...</div>';

        const typeFilter = document.getElementById('sessions-type-filter')?.value || 'all';
        const visibilityFilter = document.getElementById('sessions-visibility-filter')?.value || 'all';
        const skillFilter = document.getElementById('sessions-skill-filter')?.value || 'all';
        const token = localStorage.getItem('token');

        if (visibilityFilter === 'friends' && !token) {
            list.innerHTML = `
                <div class="empty-state">
                    <h3>Sign in to view friends-only sessions</h3>
                    <p>Friends-only sessions are visible after you sign in.</p>
                    <button class="btn-primary" onclick="Auth.showModal()">Sign In / Sign Up</button>
                </div>`;
            return;
        }

        const params = new URLSearchParams();
        if (typeFilter !== 'all') params.set('type', typeFilter);
        if (visibilityFilter !== 'all') params.set('visibility', visibilityFilter);
        if (skillFilter !== 'all') params.set('skill_level', skillFilter);
        const url = params.toString() ? `/api/sessions?${params.toString()}` : '/api/sessions';

        try {
            const res = await API.get(url);
            const sessions = res.sessions || [];
            Sessions.cachedListSessions = sessions;
            Sessions.sessionsById = Object.fromEntries(sessions.map(s => [s.id, s]));
            Sessions._renderList(list, sessions, {
                typeFilter,
                visibilityFilter,
                skillFilter,
                token,
            });
        } catch {
            list.innerHTML = '<p class="error">Failed to load sessions</p>';
        }
    },

    _renderList(list, sessions, { typeFilter, visibilityFilter, skillFilter, token }) {
        const nowSessions = sessions.filter(s => s.session_type === 'now');
        const scheduledFuture = Sessions._getFutureScheduledSessions(sessions);
        const isScheduledView = typeFilter === 'scheduled';

        if (!scheduledFuture.length && Sessions.calendarExpanded) {
            Sessions.calendarExpanded = false;
        }

        if (!sessions.length) {
            list.innerHTML = `
                <div class="empty-state">
                    <h3>No open sessions</h3>
                    <p>${typeFilter !== 'all' || visibilityFilter !== 'all' || skillFilter !== 'all'
                        ? 'Try different filters or create a new session.'
                        : 'Check in at a court and set yourself as open to play, or schedule a session!'}</p>
                    <button class="btn-primary" onclick="Sessions.showCreateModal()">Schedule Open to Play</button>
                </div>`;
            return;
        }

        let html = '';
        html += `
            <section class="sessions-overview-card">
                <div class="sessions-overview-copy">
                    <h3>Find or schedule your next run</h3>
                    <p>${isScheduledView ? 'Browse upcoming games by date and jump in quickly.' : 'Join active games now or lock in your next session.'}</p>
                </div>
                <div class="sessions-overview-stats">
                    <div><strong>${sessions.length}</strong><span>Total</span></div>
                    <div><strong>${nowSessions.length}</strong><span>Live</span></div>
                    <div><strong>${scheduledFuture.length}</strong><span>Upcoming</span></div>
                </div>
                <button class="btn-primary btn-full" onclick="Sessions.showCreateModal()">Schedule Open to Play</button>
            </section>
        `;
        const suggestions = (!isScheduledView && token)
            ? Sessions._getSuggestedSessions(sessions).slice(0, 3)
            : [];
        if (suggestions.length > 0) {
            html += '<h3 class="session-section-title">Suggested For You</h3>';
            html += '<div class="session-card-grid">';
            html += suggestions.map(s => Sessions._renderCard(s)).join('');
            html += '</div>';
        }
        if (!isScheduledView && nowSessions.length > 0) {
            html += '<h3 class="session-section-title">Active Now</h3>';
            html += '<div class="session-card-grid">';
            html += nowSessions.map(s => Sessions._renderCard(s)).join('');
            html += '</div>';
        }
        if (scheduledFuture.length > 0) {
            html += `<h3 class="session-section-title">${isScheduledView ? 'Scheduled Games Calendar' : 'Upcoming Calendar'}</h3>`;
            html += Sessions._renderScheduledCalendar(scheduledFuture);
            if (isScheduledView) {
                html += '<h3 class="session-section-title">All Scheduled Games</h3>';
                html += Sessions._renderScheduledByDay(scheduledFuture);
            } else {
                html += '<h3 class="session-section-title">Upcoming Sessions</h3>';
                html += '<div class="session-card-grid">';
                html += scheduledFuture.map(s => Sessions._renderCard(s)).join('');
                html += '</div>';
            }
        }

        if (!html) {
            html = `
                <div class="empty-state">
                    <h3>No active or future sessions</h3>
                    <p>Try a different filter, or create a new session.</p>
                    <button class="btn-primary" onclick="Sessions.showCreateModal()">Schedule Open to Play</button>
                </div>`;
        }

        list.innerHTML = html;
    },

    _rerenderFromCache() {
        const list = document.getElementById('sessions-list');
        if (!list || Sessions.currentSessionId) return;
        const typeFilter = document.getElementById('sessions-type-filter')?.value || 'all';
        const visibilityFilter = document.getElementById('sessions-visibility-filter')?.value || 'all';
        const skillFilter = document.getElementById('sessions-skill-filter')?.value || 'all';
        const token = localStorage.getItem('token');
        Sessions._renderList(list, Sessions.cachedListSessions || [], {
            typeFilter,
            visibilityFilter,
            skillFilter,
            token,
        });
    },

    changeCalendarWeek(step) {
        const current = Number(Sessions.calendarWeekOffset);
        const safeCurrent = Number.isFinite(current) ? current : 0;
        const delta = Number(step);
        const safeDelta = Number.isFinite(delta) ? delta : 0;
        Sessions.calendarWeekOffset = safeCurrent + safeDelta;
        Sessions.calendarSelectedDayKey = null;
        Sessions._rerenderFromCache();
    },

    jumpCalendarToCurrentWeek() {
        Sessions.calendarWeekOffset = 0;
        Sessions.calendarSelectedDayKey = null;
        Sessions._rerenderFromCache();
    },

    toggleCalendarExpanded() {
        Sessions.calendarExpanded = !Sessions.calendarExpanded;
        Sessions._rerenderFromCache();
    },

    // Legacy aliases kept for existing onclick bindings.
    changeCalendarMonth(step) {
        Sessions.changeCalendarWeek(step);
    },

    jumpCalendarToCurrentMonth() {
        Sessions.jumpCalendarToCurrentWeek();
    },

    selectCalendarDate(dayKey) {
        const day = Sessions._dateFromKey(dayKey);
        if (!day) return;
        Sessions.calendarSelectedDayKey = dayKey;
        const selectedDayStart = Sessions._startOfDay(day);
        const weekStart = Sessions._calendarWeekStartDate();
        const diffDays = Math.floor((selectedDayStart.getTime() - weekStart.getTime()) / 86400000);
        if (diffDays < 0 || diffDays > 6) {
            const today = Sessions._startOfDay(new Date());
            const offsetDays = Math.floor((selectedDayStart.getTime() - today.getTime()) / 86400000);
            Sessions.calendarWeekOffset = Math.floor(offsetDays / 7);
        }
        Sessions._rerenderFromCache();
    },

    _calendarWeekStartDate() {
        const today = Sessions._startOfDay(new Date());
        const offset = Number(Sessions.calendarWeekOffset);
        const safeOffset = Number.isFinite(offset) ? offset : 0;
        const start = new Date(today);
        start.setDate(start.getDate() + safeOffset * 7);
        return start;
    },

    _renderScheduledCalendar(sessions) {
        if (!sessions.length) return '';

        const grouped = Sessions._groupSessionsByDay(sessions);
        const today = Sessions._startOfDay(new Date());
        const weekStart = Sessions._calendarWeekStartDate();
        const weekEnd = new Date(weekStart);
        weekEnd.setDate(weekStart.getDate() + 6);
        const sessionCount = sessions.length;
        const totalDaysWithSessions = Object.keys(grouped).length;
        const todayKey = Sessions._dateKey(today);
        const weekDates = [];
        for (let i = 0; i < 7; i += 1) {
            const date = new Date(weekStart);
            date.setDate(weekStart.getDate() + i);
            weekDates.push(date);
        }
        const weekDayKeys = weekDates.map(date => Sessions._dateKey(date));
        const weekSessionsCount = weekDayKeys
            .reduce((sum, key) => sum + ((grouped[key] || []).length), 0);
        const weekDaysWithSessions = weekDayKeys
            .filter(key => (grouped[key] || []).length > 0)
            .length;

        let selectedDayKey = Sessions.calendarSelectedDayKey;
        if (!selectedDayKey || !weekDayKeys.includes(selectedDayKey)) {
            selectedDayKey = weekDayKeys.find(key => (grouped[key] || []).length > 0) || weekDayKeys[0];
            Sessions.calendarSelectedDayKey = selectedDayKey;
        }
        const weekRangeLabel = `${weekStart.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} - ${weekEnd.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`;

        const dayButtons = weekDates.map(date => {
            const key = Sessions._dateKey(date);
            const count = grouped[key]?.length || 0;
            const weekday = date.toLocaleDateString('en-US', { weekday: 'short' });
            const dateLabel = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            const classes = ['sessions-calendar-day'];
            if (count > 0) classes.push('has-session');
            if (key === todayKey) classes.push('today');
            if (key === selectedDayKey) classes.push('selected');
            return `
                <button
                    type="button"
                    class="${classes.join(' ')}"
                    onclick="Sessions.selectCalendarDate('${key}')"
                    aria-label="${date.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })}${count ? `, ${count} session${count > 1 ? 's' : ''}` : ''}"
                >
                    <span class="sessions-calendar-day-main">
                        <span class="sessions-calendar-day-name">${weekday}</span>
                        <span class="sessions-calendar-day-date">${dateLabel}</span>
                    </span>
                    ${count > 0 ? `<span class="sessions-calendar-day-count">${count}<span class="sessions-calendar-day-count-label"> games</span></span>` : ''}
                </button>
            `;
        }).join('');

        let selectedSection = '';
        if (selectedDayKey) {
            const selectedDate = Sessions._dateFromKey(selectedDayKey);
            const selectedLabel = selectedDate
                ? selectedDate.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
                : selectedDayKey;
            const selectedSessions = grouped[selectedDayKey] || [];
            if (selectedSessions.length > 0) {
                selectedSection = `
                    <div class="sessions-calendar-selected">
                        <h4>
                            <span>Games on ${Sessions._e(selectedLabel)}</span>
                            <span class="sessions-calendar-selected-count">${selectedSessions.length} session${selectedSessions.length > 1 ? 's' : ''}</span>
                        </h4>
                        <div class="session-card-grid">
                            ${selectedSessions.map(s => Sessions._renderCard(s)).join('')}
                        </div>
                    </div>`;
            } else {
                selectedSection = `
                    <div class="sessions-calendar-selected">
                        <h4>
                            <span>${Sessions._e(selectedLabel)}</span>
                            <span class="sessions-calendar-selected-count">No games</span>
                        </h4>
                        <p class="muted">No scheduled games on this day. Pick a highlighted date to see game details.</p>
                    </div>`;
            }
        } else {
            selectedSection = `
                <div class="sessions-calendar-selected">
                    <p class="muted">No scheduled sessions in this week.</p>
                </div>`;
        }

        return `
            <div class="sessions-calendar-shell ${Sessions.calendarExpanded ? 'expanded' : ''}">
                ${Sessions.calendarExpanded
        ? '<button type="button" class="sessions-calendar-backdrop" onclick="Sessions.toggleCalendarExpanded()" aria-label="Close expanded calendar"></button>'
        : ''}
            <div class="sessions-calendar ${Sessions.calendarExpanded ? 'expanded' : ''}">
                <div class="sessions-calendar-header">
                    <div>
                        <div class="sessions-calendar-month">Next 7 Days</div>
                        <div class="sessions-calendar-range">${Sessions._e(weekRangeLabel)}</div>
                    </div>
                    <div class="sessions-calendar-controls">
                        <div class="sessions-calendar-nav">
                            <button type="button" class="btn-secondary btn-sm" onclick="Sessions.changeCalendarWeek(-1)" aria-label="View previous week">←</button>
                            <button type="button" class="btn-secondary btn-sm" onclick="Sessions.jumpCalendarToCurrentWeek()">Today</button>
                            <button type="button" class="btn-secondary btn-sm" onclick="Sessions.changeCalendarWeek(1)" aria-label="View next week">→</button>
                        </div>
                        <button type="button" class="btn-secondary btn-sm sessions-calendar-expand-btn" onclick="Sessions.toggleCalendarExpanded()">${Sessions.calendarExpanded ? 'Close' : 'Expand'}</button>
                    </div>
                </div>
                <div class="sessions-calendar-meta">
                    ${weekSessionsCount} game${weekSessionsCount !== 1 ? 's' : ''} on ${weekDaysWithSessions} day${weekDaysWithSessions !== 1 ? 's' : ''} in this 7-day window
                    • ${sessionCount} future game${sessionCount !== 1 ? 's' : ''} across ${totalDaysWithSessions} day${totalDaysWithSessions !== 1 ? 's' : ''}.
                </div>
                <div class="sessions-calendar-week-grid">${dayButtons}</div>
                <div class="sessions-calendar-legend">Use arrows to move week by week for upcoming games.</div>
                ${selectedSection}
            </div>
            </div>`;
    },

    _renderScheduledByDay(sessions) {
        const grouped = Sessions._groupSessionsByDay(sessions);
        const dayKeys = Object.keys(grouped).sort();
        if (!dayKeys.length) {
            return '<p class="muted">No future scheduled games.</p>';
        }

        return `
            <div class="sessions-schedule-groups">
                ${dayKeys.map(key => {
                    const dayDate = Sessions._dateFromKey(key);
                    const dayLabel = dayDate
                        ? dayDate.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
                        : key;
                    const daySessions = grouped[key];
                    return `
                        <section class="sessions-schedule-group">
                            <div class="sessions-schedule-group-header">
                                <h4>${Sessions._e(dayLabel)}</h4>
                                <span class="sessions-schedule-group-count">${daySessions.length} game${daySessions.length > 1 ? 's' : ''}</span>
                            </div>
                            <div class="session-card-grid">
                                ${daySessions.map(s => Sessions._renderCard(s)).join('')}
                            </div>
                        </section>`;
                }).join('')}
            </div>`;
    },

    _resolveCalendarMonthStart(sessions) {
        const firstSessionDate = Sessions._sessionStartDate(sessions[0]) || new Date();
        const currentMonthStart = Sessions._monthStartFromKey(Sessions.calendarMonthKey);
        if (currentMonthStart) return currentMonthStart;
        const now = new Date();
        const currentMonthKey = Sessions._monthKey(now);
        const hasCurrentMonthSessions = sessions.some(s => {
            const start = Sessions._sessionStartDate(s);
            return start && Sessions._monthKey(start) === currentMonthKey;
        });
        if (hasCurrentMonthSessions) {
            const thisMonth = Sessions._startOfMonth(now);
            Sessions.calendarMonthKey = Sessions._monthKey(thisMonth);
            return thisMonth;
        }
        const fallback = Sessions._startOfMonth(firstSessionDate);
        Sessions.calendarMonthKey = Sessions._monthKey(fallback);
        return fallback;
    },

    _getFutureScheduledSessions(sessions) {
        const nowMs = Date.now();
        return (sessions || [])
            .filter(s => s.session_type === 'scheduled' && s.start_time)
            .map(s => ({ session: s, start: Sessions._sessionStartDate(s) }))
            .filter(entry => entry.start && entry.start.getTime() >= nowMs)
            .sort((a, b) => a.start.getTime() - b.start.getTime())
            .map(entry => entry.session);
    },

    _groupSessionsByDay(sessions) {
        const grouped = {};
        (sessions || []).forEach(session => {
            const start = Sessions._sessionStartDate(session);
            if (!start) return;
            const key = Sessions._dateKey(start);
            if (!grouped[key]) grouped[key] = [];
            grouped[key].push(session);
        });
        Object.keys(grouped).forEach(key => {
            grouped[key].sort((a, b) => {
                const aStart = Sessions._sessionStartDate(a);
                const bStart = Sessions._sessionStartDate(b);
                if (!aStart || !bStart) return 0;
                return aStart.getTime() - bStart.getTime();
            });
        });
        return grouped;
    },

    _sessionStartDate(session) {
        if (!session?.start_time) return null;
        const date = new Date(session.start_time);
        if (Number.isNaN(date.getTime())) return null;
        return date;
    },

    _startOfMonth(date) {
        return new Date(date.getFullYear(), date.getMonth(), 1);
    },

    _startOfDay(date) {
        return new Date(date.getFullYear(), date.getMonth(), date.getDate());
    },

    _monthKey(date) {
        const y = date.getFullYear();
        const m = String(date.getMonth() + 1).padStart(2, '0');
        return `${y}-${m}`;
    },

    _monthStartFromKey(key) {
        if (!key) return null;
        const match = /^(\d{4})-(\d{2})$/.exec(String(key));
        if (!match) return null;
        const year = Number(match[1]);
        const monthIndex = Number(match[2]) - 1;
        if (!Number.isFinite(year) || !Number.isFinite(monthIndex) || monthIndex < 0 || monthIndex > 11) {
            return null;
        }
        return new Date(year, monthIndex, 1);
    },

    _dateKey(date) {
        const y = date.getFullYear();
        const m = String(date.getMonth() + 1).padStart(2, '0');
        const d = String(date.getDate()).padStart(2, '0');
        return `${y}-${m}-${d}`;
    },

    _dateFromKey(key) {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(key));
        if (!match) return null;
        const year = Number(match[1]);
        const monthIndex = Number(match[2]) - 1;
        const day = Number(match[3]);
        if (!Number.isFinite(year) || !Number.isFinite(monthIndex) || !Number.isFinite(day)) return null;
        const date = new Date(year, monthIndex, day);
        if (
            date.getFullYear() !== year
            || date.getMonth() !== monthIndex
            || date.getDate() !== day
        ) {
            return null;
        }
        return date;
    },

    _renderCard(session) {
        const court = session.court || {};
        const creator = session.creator || {};
        const players = session.players || [];
        const series = session.series || null;
        const joined = players.filter(p => p.status === 'joined').length;
        const waitlisted = players.filter(p => p.status === 'waitlisted').length;
        const isNow = session.session_type === 'now';
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const isMine = session.creator_id === currentUser.id;
        const amJoined = players.some(p => p.user_id === currentUser.id && p.status === 'joined');
        const amWaitlisted = players.some(p => p.user_id === currentUser.id && p.status === 'waitlisted');
        const isFull = joined + 1 >= session.max_players;
        const safeCourtName = Sessions._e(court.name || 'Unknown');
        const safeCourtCity = Sessions._e(court.city || '');
        const safeCreatorName = Sessions._e(creator.name || creator.username || 'Unknown');
        const safeGameType = Sessions._e((session.game_type || 'open').replace(/_/g, ' '));
        const safeSkillLevel = Sessions._e(session.skill_level === 'all' ? 'All levels' : (session.skill_level || 'All levels'));
        const safeNotes = Sessions._e(session.notes || '');
        const creatorInitial = Sessions._e((creator.name || creator.username || '?')[0].toUpperCase());
        const openSpots = Math.max(session.max_players - (joined + 1), 0);
        const skillTone = session.skill_level === 'beginner'
            ? 'beginner'
            : session.skill_level === 'intermediate'
                ? 'intermediate'
                : session.skill_level === 'advanced'
                    ? 'advanced'
                    : 'all';

        let timeStr = 'Open now';
        if (!isNow && session.start_time) {
            const dt = new Date(session.start_time);
            const dateStr = dt.toLocaleDateString('en-US', {
                weekday: 'short', month: 'short', day: 'numeric',
            });
            const tStr = dt.toLocaleTimeString('en-US', {
                hour: 'numeric', minute: '2-digit',
            });
            timeStr = `${dateStr} at ${tStr}`;
            if (session.end_time) {
                const end = new Date(session.end_time);
                timeStr += ` – ${end.toLocaleTimeString('en-US', {
                    hour: 'numeric', minute: '2-digit',
                })}`;
            }
        }

        let actionBtn = '';
        if (isMine) {
            actionBtn = `<button class="btn-danger btn-sm session-primary-action" onclick="event.stopPropagation(); Sessions.endSession(${session.id})">End Session</button>`;
        } else if (amJoined || amWaitlisted) {
            actionBtn = `<button class="btn-secondary btn-sm session-primary-action" onclick="event.stopPropagation(); Sessions.leaveSession(${session.id})">Leave</button>`;
        } else {
            actionBtn = `<button class="btn-primary btn-sm session-primary-action" onclick="event.stopPropagation(); Sessions.joinSession(${session.id})">${isFull ? 'Join Waitlist' : 'Join Game'}</button>`;
        }
        const calendarBtn = !isNow
            ? `<button class="btn-secondary btn-sm" onclick="event.stopPropagation(); Sessions.downloadCalendar(${session.id})">Add to Calendar</button>`
            : '';

        return `
        <article class="session-card ${isNow ? 'session-active' : ''}" onclick="Sessions.openDetail(${session.id})">
            <div class="session-card-header">
                <div class="session-status-wrap">
                    <span class="session-status-pill ${isNow ? 'live' : 'scheduled'}">${isNow ? 'Live Now' : 'Scheduled'}</span>
                    <span class="session-time-text">${timeStr}</span>
                </div>
                <div class="session-chip-row">
                    <span class="session-chip">${session.visibility === 'friends' ? 'Friends only' : 'Open to all'}</span>
                    ${series ? `<span class="session-chip">Series ${series.sequence}/${series.occurrences}</span>` : ''}
                </div>
            </div>
            <div class="session-card-court">
                <strong>${safeCourtName}</strong>
                ${court.city ? `<span>${safeCourtCity}</span>` : ''}
            </div>
            <div class="session-card-meta-grid">
                <div class="session-meta-item"><span>Format</span><strong>${safeGameType}</strong></div>
                <div class="session-meta-item"><span>Skill</span><strong class="session-skill ${skillTone}">${safeSkillLevel}</strong></div>
                <div class="session-meta-item"><span>Players</span><strong>${joined + 1}/${session.max_players}</strong></div>
                ${waitlisted > 0
        ? `<div class="session-meta-item"><span>Waitlist</span><strong>${waitlisted}</strong></div>`
        : `<div class="session-meta-item"><span>Open spots</span><strong>${openSpots}</strong></div>`}
            </div>
            <div class="session-card-creator">
                <span class="session-creator-avatar">${creatorInitial}</span>
                <span>Hosted by ${safeCreatorName}</span>
                ${isMine ? '<span class="session-mine-badge">You</span>' : ''}
                ${amWaitlisted ? '<span class="session-mine-badge">Waitlisted</span>' : ''}
            </div>
            ${session.notes ? `<p class="session-notes">${safeNotes}</p>` : ''}
            <div class="session-card-actions" onclick="event.stopPropagation()">
                ${actionBtn}
                <button class="btn-secondary btn-sm" onclick="event.stopPropagation(); App.openCourtDetails(${session.court_id});">View Court</button>
                <button class="btn-secondary btn-sm" onclick="event.stopPropagation(); Sessions.inviteFriends(${session.id})">Invite</button>
                ${calendarBtn}
            </div>
        </article>`;
    },

    // ── Session Detail Page ─────────────────────────────────────
    async openDetail(sessionId) {
        Sessions.currentSessionId = sessionId;
        const container = document.getElementById('sessions-list');
        container.innerHTML = '<div class="loading">Loading session details...</div>';

        try {
            const res = await API.get(`/api/sessions/${sessionId}`);
            const session = res.session;
            Sessions.sessionsById[session.id] = session;
            Sessions.currentSessionCourtId = session.court_id;
            const tabHeader = document.querySelector('#sessions-tab .tab-page-header');
            if (tabHeader) {
                tabHeader.innerHTML = `
                    <button class="btn-secondary" onclick="Sessions._backToList()">&larr; Back</button>
                    <h2>Session Details</h2>
                `;
            }
            container.innerHTML = Sessions._renderDetail(session);

            // Load chat
            Sessions._loadSessionChat(sessionId, session.court_id);

            // Join the court room so chat updates stream into the detail view.
            if (typeof Chat !== 'undefined' && typeof Chat.joinRoom === 'function') {
                Chat.joinRoom(`court_${session.court_id}`);
            }
        } catch {
            container.innerHTML = '<p class="error">Failed to load session details</p>';
        }
    },

    _renderDetail(session) {
        const court = session.court || {};
        const creator = session.creator || {};
        const players = session.players || [];
        const series = session.series || null;
        const isNow = session.session_type === 'now';
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const isMine = session.creator_id === currentUser.id;
        const amJoined = players.some(p => p.user_id === currentUser.id && p.status === 'joined');
        const amWaitlisted = players.some(p => p.user_id === currentUser.id && p.status === 'waitlisted');
        const joinedPlayers = players.filter(p => p.status === 'joined');
        const invitedPlayers = players.filter(p => p.status === 'invited');
        const waitlistedPlayers = players.filter(p => p.status === 'waitlisted');
        const isFull = joinedPlayers.length + 1 >= session.max_players;
        const safeCourtName = Sessions._e(court.name || 'Unknown');
        const safeCourtAddress = Sessions._e(court.address || '');
        const safeCourtCity = Sessions._e(court.city || '');
        const safeCreatorName = Sessions._e(creator.name || creator.username || '');
        const safeSessionNotes = Sessions._e(session.notes || '');
        const safeGameType = Sessions._e(session.game_type || 'Open Play');
        const safeSkillLevel = Sessions._e(session.skill_level || 'All');
        const safeVisibility = Sessions._e(session.visibility === 'friends' ? '👥 Friends Only' : '🌐 Open to All');
        const directionsUrl = Sessions._directionsUrl(court);
        const targetCourtId = Number(session.court_id) || Number(court.id) || 0;

        let timeStr = '';
        if (isNow) {
            timeStr = 'Active Now — Open to Play';
        } else if (session.start_time) {
            const dt = new Date(session.start_time);
            timeStr = dt.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
            timeStr += ' at ' + dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
            if (session.end_time) {
                const end = new Date(session.end_time);
                timeStr += ` – ${end.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}`;
            }
        }

        const playersHTML = joinedPlayers.length > 0
            ? joinedPlayers.map(p => {
                const u = p.user || {};
                const initials = (u.name || u.username || '?')[0].toUpperCase();
                const safeName = Sessions._e(u.name || u.username || '');
                return `<div class="player-chip">
                    <span class="player-avatar">${initials}</span>
                    <span>${safeName}${u.skill_level ? ` (${u.skill_level})` : ''}</span>
                </div>`;
            }).join('')
            : '<p class="muted">No players have joined yet. Be the first!</p>';

        const invitedHTML = invitedPlayers.length > 0
            ? `<div class="invited-players"><strong>Invited:</strong> ${invitedPlayers.map(p => Sessions._e(p.user?.name || p.user?.username)).join(', ')}</div>`
            : '';

        const waitlistedHTML = waitlistedPlayers.length > 0
            ? `<div class="invited-players"><strong>Waitlist:</strong> ${waitlistedPlayers.map(p => Sessions._e(p.user?.name || p.user?.username)).join(', ')}</div>`
            : '';

        let actionBtns = '';
        if (isMine) {
            actionBtns = `
                <button class="btn-danger" onclick="Sessions.endSession(${session.id})">🛑 End Session</button>
                <button class="btn-secondary" onclick="Sessions.inviteFriends(${session.id})">👥 Invite Friends</button>`;
        } else if (amJoined || amWaitlisted) {
            actionBtns = `
                <button class="btn-danger" onclick="Sessions.leaveSession(${session.id})">${amWaitlisted ? 'Leave Waitlist' : 'Leave Session'}</button>
                <button class="btn-secondary" onclick="Sessions.inviteFriends(${session.id})">👥 Invite Friends</button>`;
        } else {
            actionBtns = `
                <button class="btn-primary" onclick="Sessions.joinSession(${session.id})">✅ ${isFull ? 'Join Waitlist' : 'Join Session'}</button>
                <button class="btn-secondary" onclick="Sessions.inviteFriends(${session.id})">👥 Invite Friends</button>`;
        }
        if (!isNow) {
            actionBtns += `
                <button class="btn-secondary" onclick="Sessions.downloadCalendar(${session.id})">🗓 Add to Calendar</button>`;
        }
        if (isMine && series) {
            actionBtns += `
                <button class="btn-danger" onclick="Sessions.cancelSeries(${series.id}, ${session.id})">🔁 Cancel Future Sessions</button>`;
        }

        return `
        <div class="session-detail">
            <div class="session-detail-hero">
                <div class="session-detail-time">
                    <div class="hero-date">${isNow ? '🟢 Active Now' : '📅 Scheduled'}</div>
                    <div class="hero-time">${timeStr}</div>
                </div>
                <div class="session-status-badge ${isNow ? 'active' : 'scheduled'}">${isNow ? 'Live' : 'Scheduled'}</div>
            </div>

            <div class="game-detail-grid">
                <div class="game-detail-info">
                    <div class="detail-section">
                        <h4>Court</h4>
                        <div class="detail-court-card" onclick="${targetCourtId ? `App.openCourtDetails(${targetCourtId});` : ''}">
                            <strong>${safeCourtName}</strong>
                            <span>${safeCourtAddress}, ${safeCourtCity}</span>
                            <span>${court.indoor ? '🏢 Indoor' : '☀️ Outdoor'} · ${court.num_courts || '?'} courts</span>
                            <a href="${directionsUrl}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-sm" onclick="event.stopPropagation()">🗺 Directions</a>
                        </div>
                    </div>

                    <div class="detail-section">
                        <h4>Session Info</h4>
                        <div class="detail-meta-grid">
                            <div><span class="detail-label">Type</span><span>${safeGameType}</span></div>
                            <div><span class="detail-label">Skill</span><span>${safeSkillLevel}</span></div>
                            <div><span class="detail-label">Players</span><span>${joinedPlayers.length + 1}/${session.max_players}</span></div>
                            <div><span class="detail-label">Visibility</span><span>${safeVisibility}</span></div>
                            ${series ? `<div><span class="detail-label">Series</span><span>🔁 ${series.sequence}/${series.occurrences}</span></div>` : ''}
                            ${waitlistedPlayers.length > 0 ? `<div><span class="detail-label">Waitlist</span><span>⏳ ${waitlistedPlayers.length}</span></div>` : ''}
                        </div>
                    </div>

                    ${session.notes ? `<div class="detail-section"><h4>Notes</h4><p>${safeSessionNotes}</p></div>` : ''}

                    <div class="detail-section">
                        <h4>Created by</h4>
                        <div class="player-chip">
                            <span class="player-avatar">${(creator.name || creator.username || '?')[0].toUpperCase()}</span>
                            <span>${safeCreatorName}</span>
                        </div>
                    </div>

                    <div class="detail-section">
                        <h4>Joined Players (${joinedPlayers.length + 1}/${session.max_players})</h4>
                        <div class="players-list">
                            <div class="player-chip">
                                <span class="player-avatar" style="background:var(--primary)">${(creator.name || creator.username || '?')[0].toUpperCase()}</span>
                                <span>${safeCreatorName} <span class="muted">(organizer)</span></span>
                            </div>
                            ${playersHTML}
                        </div>
                        ${invitedHTML}
                        ${waitlistedHTML}
                    </div>

                    <div class="detail-actions">
                        ${actionBtns}
                    </div>
                </div>

                <div class="game-detail-chat">
                    <div class="game-chat-container">
                        <h4>💬 Session Chat</h4>
                        <div id="session-chat-messages" class="game-chat-messages"></div>
                        <form class="game-chat-input" onsubmit="Sessions.sendChat(event, ${session.id}, ${session.court_id})">
                            <input type="text" id="session-chat-text" placeholder="Message the group..." autocomplete="off">
                            <button type="submit" class="btn-primary btn-sm">Send</button>
                        </form>
                    </div>
                </div>
            </div>
        </div>`;
    },

    async _loadSessionChat(sessionId, courtId) {
        const container = document.getElementById('session-chat-messages');
        if (!container) return;
        const token = localStorage.getItem('token');
        if (!token) {
            container.innerHTML = '<p class="muted">Sign in to view and send messages</p>';
            return;
        }
        if (!courtId) {
            container.innerHTML = '<p class="muted">Chat unavailable for this session</p>';
            return;
        }
        try {
            const res = await API.get(`/api/chat/court/${courtId}`);
            const msgs = res.messages || [];
            if (!msgs.length) {
                container.innerHTML = '<p class="muted">No messages yet. Say hello!</p>';
                return;
            }
            container.innerHTML = msgs.map(m => Sessions._renderChatMsg(m)).join('');
            container.scrollTop = container.scrollHeight;
        } catch {
            container.innerHTML = '<p class="muted">Chat not available yet</p>';
        }
    },

    _renderChatMsg(msg) {
        const sender = msg.sender || {};
        const time = new Date(msg.created_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const isMe = sender.id === currentUser.id;
        const safeSender = Sessions._e(isMe ? 'You' : (sender.name || sender.username));
        const safeContent = Sessions._e(msg.content || '');
        return `
        <div class="chat-msg ${isMe ? 'chat-msg-me' : ''}">
            <div class="chat-msg-header">
                <strong>${safeSender}</strong>
                <span class="chat-msg-time">${time}</span>
            </div>
            <div class="chat-msg-body">${safeContent}</div>
        </div>`;
    },

    async sendChat(e, sessionId, courtId) {
        e.preventDefault();
        const input = document.getElementById('session-chat-text');
        const content = input.value.trim();
        if (!content) return;
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        if (!courtId) { App.toast('Chat unavailable for this session', 'error'); return; }

        try {
            await API.post('/api/chat/send', {
                content, court_id: courtId, msg_type: 'court',
            });
            input.value = '';
            Sessions._loadSessionChat(sessionId, courtId);
        } catch { App.toast('Failed to send message', 'error'); }
    },

    _backToList() {
        const tabHeader = document.querySelector('#sessions-tab .tab-page-header');
        if (tabHeader) {
            tabHeader.innerHTML = `
                <h2>Open to Play</h2>
                <button class="btn-primary btn-sm" onclick="Sessions.showCreateModal()">Schedule Open to Play</button>
            `;
        }
        Sessions.currentSessionId = null;
        Sessions.currentSessionCourtId = null;
        Sessions.load();
    },

    // ── Create Session Modal ──────────────────────────────────
    async showCreateModal(preselectedCourtId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }

        const modal = document.getElementById('session-modal');
        modal.style.display = 'flex';
        const modalContent = modal.querySelector('.modal-content');
        if (modalContent) modalContent.scrollTop = 0;
        const advancedOptions = modal.querySelector('.session-form-advanced');
        if (advancedOptions) advancedOptions.open = false;
        const timingOptions = modal.querySelector('#session-form-timing');
        if (timingOptions) timingOptions.open = false;
        const form = document.getElementById('create-session-form');
        if (form && typeof form.reset === 'function') {
            form.reset();
        }

        await Sessions._populateCourtSelect(preselectedCourtId);
        Sessions._populateInviteFriendOptions();

        const recurrenceSelect = document.getElementById('session-recurrence-select');
        const recurrenceCount = document.getElementById('session-recurrence-count');
        const durationSelect = document.getElementById('session-duration-select');
        if (recurrenceSelect) recurrenceSelect.value = 'none';
        if (recurrenceCount) recurrenceCount.value = '4';
        if (durationSelect) durationSelect.value = '90';
        Sessions._toggleRecurrenceFields();
        Sessions._initializeScheduleInputs('tomorrow');
    },

    hideCreateModal() {
        document.getElementById('session-modal').style.display = 'none';
    },

    async _populateCourtSelect(preselectedCourtId) {
        const select = document.getElementById('session-court-select');
        if (!select) return;
        try {
            const courtsUrl = (typeof App !== 'undefined' && typeof App.buildCourtsQuery === 'function')
                ? App.buildCourtsQuery()
                : '/api/courts';
            const res = await API.get(courtsUrl);
            const courts = res.courts || [];
            if (!courts.length) {
                select.innerHTML = '<option value="">No courts available</option>';
                return;
            }
            select.innerHTML = courts.map(c =>
                `<option value="${c.id}" ${String(c.id) === String(preselectedCourtId) ? 'selected' : ''}>${Sessions._e(c.name)} — ${Sessions._e(c.city)}</option>`
            ).join('');
        } catch {
            select.innerHTML = '<option value="">Error loading courts</option>';
        }
    },

    _populateInviteFriendOptions() {
        const inviteSection = document.getElementById('session-invite-section');
        const inviteList = document.getElementById('session-invite-friends');
        if (!inviteSection || !inviteList) return;
        if (App.friendsList.length > 0) {
            inviteSection.style.display = 'block';
            inviteList.innerHTML = App.friendsList.map(f => `
                <label class="friend-pick-item">
                    <input type="checkbox" value="${f.id}" name="invite_friends">
                    <span class="friend-pick-avatar">${Sessions._e((f.name || f.username || '?')[0].toUpperCase())}</span>
                    <span class="friend-pick-name">${Sessions._e(f.name || f.username)}</span>
                </label>
            `).join('');
        } else {
            inviteSection.style.display = 'none';
        }
    },

    _bindScheduleControls() {
        if (Sessions.scheduleControlsBound) return;
        const dayButtons = document.getElementById('session-quick-buttons');
        const timeButtons = document.getElementById('session-time-buttons');
        const durationButtons = document.getElementById('session-duration-buttons');
        const startInput = document.getElementById('session-start-time');
        const endInput = document.getElementById('session-end-time');
        const durationSelect = document.getElementById('session-duration-select');
        if (!startInput || !endInput || !durationSelect) return;

        const onStartInput = () => {
            if (!startInput.value) {
                endInput.min = '';
                Sessions._setQuickPresetActive('custom');
                Sessions._setQuickTimeActive('custom');
                Sessions._renderScheduleSummary();
                return;
            }
            endInput.min = startInput.value;
            if (durationSelect.value !== 'custom') {
                Sessions._syncEndFromDuration();
            }
            Sessions._syncQuickSelectorsFromStart();
            Sessions._renderScheduleSummary();
        };
        startInput.addEventListener('input', onStartInput);
        startInput.addEventListener('change', onStartInput);

        const onEndInput = () => {
            if (!endInput.value || !startInput.value) return;
            const start = Sessions._parseDateTimeLocal(startInput.value);
            const end = Sessions._parseDateTimeLocal(endInput.value);
            if (!start || !end || end <= start) {
                durationSelect.value = 'custom';
                Sessions._syncDurationButtonsFromValue('custom');
                Sessions._renderScheduleSummary();
                return;
            }
            const diffMinutes = Math.round((end.getTime() - start.getTime()) / 60000);
            const presetDurations = ['60', '90', '120', '150', '180'];
            if (presetDurations.includes(String(diffMinutes))) {
                durationSelect.value = String(diffMinutes);
            } else {
                durationSelect.value = 'custom';
            }
            Sessions._syncDurationButtonsFromValue(durationSelect.value);
            Sessions._renderScheduleSummary();
        };
        endInput.addEventListener('input', onEndInput);
        endInput.addEventListener('change', onEndInput);

        if (dayButtons) {
            dayButtons.addEventListener('click', (event) => {
                const btn = event.target.closest('.session-quick-btn');
                if (!btn) return;
                event.preventDefault();
                Sessions._applyQuickPreset(btn.dataset.preset || 'custom');
            });
        }
        if (timeButtons) {
            timeButtons.addEventListener('click', (event) => {
                const btn = event.target.closest('.session-quick-btn');
                if (!btn) return;
                event.preventDefault();
                Sessions._applyQuickTime(btn.dataset.timeSlot || 'custom');
            });
        }
        if (durationButtons) {
            durationButtons.addEventListener('click', (event) => {
                const btn = event.target.closest('.session-quick-btn');
                if (!btn) return;
                event.preventDefault();
                Sessions._applyDurationSelection(btn.dataset.duration || '90');
            });
        }

        Sessions.scheduleControlsBound = true;
    },

    _initializeScheduleInputs(defaultPreset = 'tomorrow', preserveExisting = false) {
        Sessions._bindScheduleControls();
        const startInput = document.getElementById('session-start-time');
        const endInput = document.getElementById('session-end-time');
        const durationSelect = document.getElementById('session-duration-select');
        if (!startInput || !endInput || !durationSelect) return;

        const minStart = Sessions._roundUpToMinutes(new Date(Date.now() + 5 * 60000), 15);
        const minValue = Sessions._formatDateTimeLocal(minStart);
        startInput.min = minValue;
        endInput.min = minValue;

        if (preserveExisting && startInput.value) {
            endInput.min = startInput.value;
            if (durationSelect.value !== 'custom') {
                Sessions._syncEndFromDuration();
            }
            Sessions._syncQuickSelectorsFromStart();
            Sessions._syncDurationButtonsFromValue(durationSelect.value);
            Sessions._renderScheduleSummary();
            return;
        }

        if (!durationSelect.value || durationSelect.value === 'custom') {
            durationSelect.value = '90';
        }
        Sessions._syncDurationButtonsFromValue(durationSelect.value);
        Sessions._applyQuickPreset(defaultPreset);
        Sessions._applyQuickTime('18:30');
        Sessions._renderScheduleSummary();
    },

    _setQuickPresetActive(preset) {
        const buttons = document.querySelectorAll('#session-quick-buttons .session-quick-btn');
        buttons.forEach(button => {
            button.classList.toggle('active', button.dataset.preset === preset);
        });
    },

    _setQuickTimeActive(slot) {
        const buttons = document.querySelectorAll('#session-time-buttons .session-quick-btn');
        buttons.forEach(button => {
            button.classList.toggle('active', button.dataset.timeSlot === slot);
        });
    },

    _syncDurationButtonsFromValue(duration) {
        const buttons = document.querySelectorAll('#session-duration-buttons .session-quick-btn');
        buttons.forEach(button => {
            button.classList.toggle('active', button.dataset.duration === String(duration));
        });
    },

    _openTimingDetails() {
        const timingDetails = document.getElementById('session-form-timing');
        if (timingDetails) timingDetails.open = true;
    },

    _activeQuickDayPreset() {
        const active = document.querySelector('#session-quick-buttons .session-quick-btn.active');
        return active?.dataset?.preset || null;
    },

    _activeQuickTimeSlot() {
        const active = document.querySelector('#session-time-buttons .session-quick-btn.active');
        return active?.dataset?.timeSlot || null;
    },

    _syncQuickSelectorsFromStart() {
        const startInput = document.getElementById('session-start-time');
        const start = Sessions._parseDateTimeLocal(startInput?.value || '');
        if (!start) {
            Sessions._setQuickPresetActive('custom');
            Sessions._setQuickTimeActive('custom');
            return;
        }
        Sessions._setQuickPresetActive(Sessions._quickDayPresetForDate(start));
        Sessions._setQuickTimeActive(Sessions._quickTimeSlotForDate(start));
    },

    _quickDayPresetForDate(date) {
        const toStartOfDay = (rawDate) => new Date(rawDate.getFullYear(), rawDate.getMonth(), rawDate.getDate());
        const target = toStartOfDay(date);
        const today = toStartOfDay(new Date());
        const tomorrow = new Date(today);
        tomorrow.setDate(tomorrow.getDate() + 1);
        const weekend = Sessions._nextWeekendDate(today);
        const nextWeek = new Date(today);
        nextWeek.setDate(nextWeek.getDate() + 7);

        const targetKey = Sessions._dateKey(target);
        if (targetKey === Sessions._dateKey(today)) return 'today';
        if (targetKey === Sessions._dateKey(tomorrow)) return 'tomorrow';
        if (targetKey === Sessions._dateKey(weekend)) return 'weekend';
        if (targetKey === Sessions._dateKey(nextWeek)) return 'next_week';
        return 'custom';
    },

    _quickTimeSlotForDate(date) {
        const pad = (n) => String(n).padStart(2, '0');
        const slot = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
        const allowed = Sessions._quickTimeSlots();
        return allowed.includes(slot) ? slot : 'custom';
    },

    _quickTimeSlots() {
        return ['09:00', '12:00', '17:30', '18:30'];
    },

    _timeSlotToMinutes(slot) {
        const match = /^(\d{2}):(\d{2})$/.exec(String(slot || ''));
        if (!match) return null;
        const hours = Number(match[1]);
        const minutes = Number(match[2]);
        if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return null;
        if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null;
        return hours * 60 + minutes;
    },

    _resolveNextAvailableStart(dayPreset, currentStart) {
        const minStart = Sessions._roundUpToMinutes(new Date(Date.now() + 5 * 60000), 15);
        const slots = Sessions._quickTimeSlots();
        let baseDate;
        if (dayPreset && dayPreset !== 'custom') {
            baseDate = Sessions._resolveQuickPresetStart(dayPreset) || new Date(minStart);
        } else if (currentStart) {
            baseDate = new Date(currentStart);
        } else {
            baseDate = new Date(minStart);
        }
        const startDay = new Date(baseDate.getFullYear(), baseDate.getMonth(), baseDate.getDate());

        for (let dayOffset = 0; dayOffset < 14; dayOffset += 1) {
            const day = new Date(startDay);
            day.setDate(startDay.getDate() + dayOffset);
            for (const slot of slots) {
                const slotMinutes = Sessions._timeSlotToMinutes(slot);
                if (slotMinutes === null) continue;
                const candidate = new Date(day);
                candidate.setHours(Math.floor(slotMinutes / 60), slotMinutes % 60, 0, 0);
                if (candidate >= minStart) {
                    return candidate;
                }
            }
        }
        return minStart;
    },

    _applyQuickPreset(preset) {
        const startInput = document.getElementById('session-start-time');
        if (!startInput) return;

        Sessions._setQuickPresetActive(preset);
        if (preset === 'custom') {
            Sessions._openTimingDetails();
            startInput.focus();
            Sessions._renderScheduleSummary();
            return;
        }

        let quickStart = Sessions._resolveQuickPresetStart(preset);
        if (!quickStart) return;

        const existingStart = Sessions._parseDateTimeLocal(startInput.value);
        const activeSlot = Sessions._activeQuickTimeSlot();
        let minutes = existingStart
            ? (existingStart.getHours() * 60 + existingStart.getMinutes())
            : Sessions._timeSlotToMinutes(activeSlot);
        if (minutes === null) minutes = 18 * 60 + 30;
        quickStart.setHours(Math.floor(minutes / 60), minutes % 60, 0, 0);

        const minStart = Sessions._roundUpToMinutes(new Date(Date.now() + 5 * 60000), 15);
        if (quickStart < minStart) quickStart = minStart;

        startInput.value = Sessions._formatDateTimeLocal(quickStart);
        Sessions._syncEndFromDuration();
        Sessions._syncQuickSelectorsFromStart();
        Sessions._renderScheduleSummary();
    },

    _resolveQuickPresetStart(preset) {
        const today = new Date();
        const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
        switch (preset) {
            case 'today':
                return new Date(startOfToday);
            case 'tomorrow': {
                const tomorrow = new Date(startOfToday);
                tomorrow.setDate(tomorrow.getDate() + 1);
                return tomorrow;
            }
            case 'weekend':
                return Sessions._nextWeekendDate(startOfToday);
            case 'next_week': {
                const nextWeek = new Date(startOfToday);
                nextWeek.setDate(nextWeek.getDate() + 7);
                return nextWeek;
            }
            default:
                return null;
        }
    },

    _nextWeekendDate(baseDate) {
        const startOfDay = new Date(baseDate.getFullYear(), baseDate.getMonth(), baseDate.getDate());
        const day = startOfDay.getDay();
        if (day === 6 || day === 0) return startOfDay;
        const daysUntilSaturday = 6 - day;
        startOfDay.setDate(startOfDay.getDate() + daysUntilSaturday);
        return startOfDay;
    },

    _applyQuickTime(slot) {
        const startInput = document.getElementById('session-start-time');
        if (!startInput) return;

        Sessions._setQuickTimeActive(slot);
        if (slot === 'next') {
            const dayPreset = Sessions._activeQuickDayPreset();
            const currentStart = Sessions._parseDateTimeLocal(startInput.value);
            const nextStart = Sessions._resolveNextAvailableStart(dayPreset, currentStart);
            startInput.value = Sessions._formatDateTimeLocal(nextStart);
            Sessions._syncEndFromDuration();
            Sessions._syncQuickSelectorsFromStart();
            Sessions._renderScheduleSummary();
            return;
        }
        if (slot === 'custom') {
            Sessions._openTimingDetails();
            startInput.focus();
            Sessions._renderScheduleSummary();
            return;
        }

        const minutes = Sessions._timeSlotToMinutes(slot);
        if (minutes === null) return;

        let start = Sessions._parseDateTimeLocal(startInput.value);
        if (!start) {
            const dayPreset = Sessions._activeQuickDayPreset();
            start = Sessions._resolveQuickPresetStart(
                dayPreset && dayPreset !== 'custom' ? dayPreset : 'tomorrow'
            ) || new Date();
        }

        start = new Date(start);
        start.setHours(Math.floor(minutes / 60), minutes % 60, 0, 0);

        const minStart = Sessions._roundUpToMinutes(new Date(Date.now() + 5 * 60000), 15);
        if (start < minStart) {
            if (Sessions._activeQuickDayPreset() === 'today') {
                const tomorrow = Sessions._resolveQuickPresetStart('tomorrow') || new Date(minStart);
                tomorrow.setHours(Math.floor(minutes / 60), minutes % 60, 0, 0);
                start = tomorrow;
            } else {
                start = minStart;
            }
        }

        startInput.value = Sessions._formatDateTimeLocal(start);
        Sessions._syncEndFromDuration();
        Sessions._syncQuickSelectorsFromStart();
        Sessions._renderScheduleSummary();
    },

    _applyDurationSelection(selectedDuration) {
        const durationSelect = document.getElementById('session-duration-select');
        const endInput = document.getElementById('session-end-time');
        if (!durationSelect) return;
        if (selectedDuration !== undefined && selectedDuration !== null) {
            durationSelect.value = String(selectedDuration);
        }
        Sessions._syncDurationButtonsFromValue(durationSelect.value);
        if (durationSelect.value === 'custom') {
            Sessions._openTimingDetails();
            if (endInput) endInput.focus();
            Sessions._renderScheduleSummary();
            return;
        }
        Sessions._syncEndFromDuration();
        Sessions._renderScheduleSummary();
    },

    _syncEndFromDuration() {
        const startInput = document.getElementById('session-start-time');
        const endInput = document.getElementById('session-end-time');
        const durationSelect = document.getElementById('session-duration-select');
        if (!startInput || !endInput || !durationSelect || !startInput.value) return;

        endInput.min = startInput.value;
        if (durationSelect.value === 'custom') return;

        const minutes = parseInt(durationSelect.value, 10);
        if (!Number.isFinite(minutes) || minutes <= 0) return;
        const start = Sessions._parseDateTimeLocal(startInput.value);
        if (!start) return;
        const end = new Date(start.getTime() + minutes * 60000);
        endInput.value = Sessions._formatDateTimeLocal(end);
    },

    _renderScheduleSummary() {
        const summaryEl = document.getElementById('session-schedule-summary');
        const startInput = document.getElementById('session-start-time');
        const endInput = document.getElementById('session-end-time');
        const durationSelect = document.getElementById('session-duration-select');
        if (!summaryEl || !startInput || !endInput || !durationSelect) return;

        const start = Sessions._parseDateTimeLocal(startInput.value);
        if (!start) {
            summaryEl.textContent = 'Choose a day and time above.';
            Sessions._updateScheduleSubmitButton(null);
            return;
        }

        let end = Sessions._parseDateTimeLocal(endInput.value);
        const durationValue = String(durationSelect.value || 'custom');
        let durationLabel = 'Custom length';
        if (durationValue !== 'custom') {
            const minutes = parseInt(durationValue, 10);
            if (Number.isFinite(minutes) && minutes > 0) {
                durationLabel = Sessions._formatDurationLabel(minutes);
                if (!end || end <= start) {
                    end = new Date(start.getTime() + minutes * 60000);
                }
            }
        } else if (end && end > start) {
            const customMinutes = Math.round((end.getTime() - start.getTime()) / 60000);
            durationLabel = Sessions._formatDurationLabel(customMinutes);
        }

        const startDateLabel = start.toLocaleDateString('en-US', {
            weekday: 'short',
            month: 'short',
            day: 'numeric',
        });
        const startTimeLabel = start.toLocaleTimeString('en-US', {
            hour: 'numeric',
            minute: '2-digit',
        });

        let summary = `${startDateLabel} at ${startTimeLabel} • ${durationLabel}`;
        if (end && end > start) {
            const endTimeLabel = end.toLocaleTimeString('en-US', {
                hour: 'numeric',
                minute: '2-digit',
            });
            summary += ` • Ends ${endTimeLabel}`;
        }
        const recurrence = document.getElementById('session-recurrence-select')?.value || 'none';
        if (recurrence !== 'none') {
            const recurrenceCountRaw = parseInt(
                document.getElementById('session-recurrence-count')?.value || '4',
                10
            );
            const recurrenceCount = Number.isFinite(recurrenceCountRaw)
                ? Math.max(2, recurrenceCountRaw)
                : 4;
            const recurrenceLabel = recurrence === 'biweekly' ? 'Every 2 weeks' : 'Weekly';
            summary += ` • ${recurrenceLabel} x${recurrenceCount}`;
        }
        summaryEl.textContent = summary;
        Sessions._updateScheduleSubmitButton(start);
    },

    _updateScheduleSubmitButton(startDate) {
        const submitBtn = document.getElementById('session-submit-btn');
        if (!submitBtn) return;
        if (!startDate) {
            submitBtn.textContent = 'Schedule Open to Play';
            return;
        }
        const dayLabel = startDate.toLocaleDateString('en-US', {
            weekday: 'short',
            month: 'short',
            day: 'numeric',
        });
        const timeLabel = startDate.toLocaleTimeString('en-US', {
            hour: 'numeric',
            minute: '2-digit',
        });
        submitBtn.textContent = `Schedule ${dayLabel} ${timeLabel}`;
    },

    _formatDurationLabel(totalMinutes) {
        const minutes = Number(totalMinutes);
        if (!Number.isFinite(minutes) || minutes <= 0) return 'Custom length';
        const hours = Math.floor(minutes / 60);
        const mins = minutes % 60;
        if (!hours) return `${mins} min`;
        if (!mins) return `${hours}h`;
        return `${hours}h ${mins}m`;
    },

    _roundUpToMinutes(date, intervalMinutes) {
        const rounded = new Date(date);
        rounded.setSeconds(0, 0);
        const remainder = rounded.getMinutes() % intervalMinutes;
        if (remainder !== 0) {
            rounded.setMinutes(rounded.getMinutes() + intervalMinutes - remainder);
        }
        return rounded;
    },

    _formatDateTimeLocal(date) {
        const pad = (n) => String(n).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T`
            + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    },

    _parseDateTimeLocal(value) {
        if (!value) return null;
        const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(String(value));
        if (!match) return null;
        const year = Number(match[1]);
        const month = Number(match[2]) - 1;
        const day = Number(match[3]);
        const hour = Number(match[4]);
        const minute = Number(match[5]);
        const second = match[6] ? Number(match[6]) : 0;
        if (
            !Number.isFinite(year)
            || !Number.isFinite(month)
            || !Number.isFinite(day)
            || !Number.isFinite(hour)
            || !Number.isFinite(minute)
            || !Number.isFinite(second)
        ) {
            return null;
        }
        const parsed = new Date(year, month, day, hour, minute, second, 0);
        if (
            parsed.getFullYear() !== year
            || parsed.getMonth() !== month
            || parsed.getDate() !== day
            || parsed.getHours() !== hour
            || parsed.getMinutes() !== minute
        ) {
            return null;
        }
        return parsed;
    },

    _toggleRecurrenceFields() {
        const recurrence = document.getElementById('session-recurrence-select')?.value || 'none';
        const group = document.getElementById('session-recurrence-count-group');
        if (!group) return;
        group.style.display = recurrence !== 'none' ? 'block' : 'none';
        Sessions._renderScheduleSummary();
    },

    async create(e) {
        e.preventDefault();
        const form = e.target;
        const courtId = parseInt(form.court_id.value, 10);
        if (!Number.isFinite(courtId) || courtId <= 0) {
            App.toast('Please select a court', 'error');
            return;
        }
        const maxPlayers = parseInt(form.max_players.value, 10);
        if (!Number.isFinite(maxPlayers) || maxPlayers < 2 || maxPlayers > 20) {
            App.toast('Max players must be between 2 and 20', 'error');
            return;
        }

        const data = {
            court_id: courtId,
            session_type: 'scheduled',
            game_type: form.game_type.value,
            skill_level: form.skill_level.value,
            max_players: maxPlayers,
            visibility: form.visibility.value,
            notes: form.notes.value,
        };

        if (!form.start_time.value) {
            App.toast('Start time is required for scheduled sessions', 'error');
            return;
        }
        const startAt = Sessions._parseDateTimeLocal(form.start_time.value);
        if (!startAt) {
            App.toast('Start time is invalid', 'error');
            return;
        }
        if (startAt.getTime() < (Date.now() - 60000)) {
            App.toast('Start time must be in the future', 'error');
            return;
        }
        data.start_time = Sessions._formatDateTimeLocal(startAt);

        if (form.end_time.value) {
            const endAt = Sessions._parseDateTimeLocal(form.end_time.value);
            if (!endAt) {
                App.toast('End time is invalid', 'error');
                return;
            }
            if (endAt <= startAt) {
                App.toast('End time must be after start time', 'error');
                return;
            }
            data.end_time = Sessions._formatDateTimeLocal(endAt);
        }

        const recurrence = form.recurrence?.value || 'none';
        const parsedCount = parseInt(form.recurrence_count?.value || '1', 10);
        const recurrenceCount = Number.isFinite(parsedCount) ? Math.max(1, parsedCount) : 1;
        data.recurrence = recurrence;
        data.recurrence_count = recurrenceCount;

        // Collect invited friends
        const selectedFriends = Array.from(
            form.querySelectorAll('input[name="invite_friends"]:checked')
        ).map(cb => parseInt(cb.value));
        if (selectedFriends.length > 0) data.invite_friends = selectedFriends;

        try {
            const res = await API.post('/api/sessions', data);
            if (res?.session?.id) Sessions.sessionsById[res.session.id] = res.session;
            if (Array.isArray(res.sessions)) {
                res.sessions.forEach(s => {
                    if (s?.id) Sessions.sessionsById[s.id] = s;
                });
            }
            if ((res.created_count || 1) > 1) {
                App.toast(`Scheduled ${res.created_count} recurring sessions.`);
            } else {
                App.toast('Session scheduled!');
            }
            Sessions.hideCreateModal();
            Sessions.load();
            // Refresh map if visible
            if (App.currentView === 'map') MapView.loadCourts();
        } catch (err) {
            App.toast(err.message || 'Failed to create session', 'error');
        }
    },

    // ── Join / Leave Sessions ─────────────────────────────────
    async joinSession(sessionId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        try {
            const res = await API.post(`/api/sessions/${sessionId}/join`, {});
            if (res.waitlisted) App.toast('Session is full. You were added to the waitlist.');
            else App.toast("You've joined the session!");
            Sessions.load();
            if (App.currentView === 'map') MapView.loadCourts();
            if (Sessions.currentSessionId === sessionId) {
                Sessions.openDetail(sessionId);
            }
        } catch (err) {
            App.toast(err.message || 'Failed to join session', 'error');
        }
    },

    async leaveSession(sessionId) {
        try {
            const res = await API.post(`/api/sessions/${sessionId}/leave`, {});
            App.toast('Left session');
            if (res.promoted_user_id) {
                App.toast('A waitlisted player was moved into the open spot.');
            }
            Sessions.load();
            if (Sessions.currentSessionId === sessionId) {
                Sessions.openDetail(sessionId);
            }
            if (App.currentView === 'map') MapView.loadCourts();
        } catch { App.toast('Failed to leave session', 'error'); }
    },

    async endSession(sessionId) {
        try {
            await API.post(`/api/sessions/${sessionId}/end`, {});
            App.toast('Session ended');
            Sessions.load();
            if (App.currentView === 'map') MapView.loadCourts();
        } catch { App.toast('Failed to end session', 'error'); }
    },

    // ── Invite Friends to Session ────────────────────────────
    inviteFriends(sessionId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        App.showInviteModal('Invite Friends to Session', async (friendIds) => {
            try {
                const res = await API.post(`/api/sessions/${sessionId}/invite`, {
                    friend_ids: friendIds,
                });
                App.toast(res.message || 'Invites sent!');
                App.hideInviteModal();
            } catch (err) {
                App.toast(err.message || 'Failed to send invites', 'error');
            }
        });
    },

    async cancelSeries(seriesId, sessionId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        if (!confirm('Cancel all upcoming sessions in this recurring series?')) return;
        try {
            const res = await API.post(`/api/sessions/series/${seriesId}/cancel`, {});
            if ((res.cancelled_count || 0) > 0) {
                App.toast(`Cancelled ${res.cancelled_count} future session(s).`);
            } else {
                App.toast('No upcoming sessions were cancelled.');
            }
            Sessions.load();
            if (Sessions.currentSessionId === sessionId) {
                Sessions.openDetail(sessionId);
            }
            if (App.currentView === 'map') MapView.loadCourts();
        } catch (err) {
            App.toast(err.message || 'Failed to cancel recurring sessions', 'error');
        }
    },

    _getSuggestedSessions(sessions) {
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const userId = currentUser.id;
        if (!userId) return [];
        const friendIds = App.friendIds || [];
        const userSkill = Sessions._userSkillCategory(currentUser.skill_level);

        return sessions
            .filter(s => s.creator_id !== userId)
            .filter(s => {
                const me = (s.players || []).find(p => p.user_id === userId);
                return !me || !['joined', 'waitlisted'].includes(me.status);
            })
            .map(s => ({ session: s, score: Sessions._scoreSession(s, userSkill, friendIds) }))
            .filter(entry => entry.score > 0)
            .sort((a, b) => b.score - a.score)
            .map(entry => entry.session);
    },

    _scoreSession(session, userSkill, friendIds) {
        const players = session.players || [];
        const joined = players.filter(p => p.status === 'joined').length;
        const available = (joined + 1) < session.max_players;
        if (!available) return -100;

        let score = 0;
        if (session.session_type === 'now') score += 26;
        else score += 8;

        if (friendIds.includes(session.creator_id)) score += 36;
        const friendParticipants = players.filter(p => friendIds.includes(p.user_id)).length;
        score += Math.min(friendParticipants, 3) * 14;

        if (session.skill_level === 'all') score += 10;
        else if (userSkill && session.skill_level === userSkill) score += 24;
        else if (!userSkill) score += 4;

        if (session.visibility === 'friends' && friendIds.includes(session.creator_id)) score += 8;
        if (session.game_type === 'open') score += 6;
        return score;
    },

    _userSkillCategory(level) {
        const parsed = parseFloat(level);
        if (!Number.isFinite(parsed)) return null;
        if (parsed <= 2.5) return 'beginner';
        if (parsed <= 3.5) return 'intermediate';
        return 'advanced';
    },

    async downloadCalendar(sessionId) {
        let session = Sessions.sessionsById[sessionId];
        if (!session) {
            try {
                const res = await API.get(`/api/sessions/${sessionId}`);
                session = res.session;
                if (session?.id) Sessions.sessionsById[session.id] = session;
            } catch {
                App.toast('Unable to load session details for calendar export', 'error');
                return;
            }
        }
        if (!session || session.session_type !== 'scheduled' || !session.start_time) {
            App.toast('Only scheduled sessions can be added to calendar', 'error');
            return;
        }

        const start = new Date(session.start_time);
        if (Number.isNaN(start.getTime())) {
            App.toast('Session start time is invalid', 'error');
            return;
        }
        const end = session.end_time
            ? new Date(session.end_time)
            : new Date(start.getTime() + 90 * 60000);
        const court = session.court || {};
        const summary = `Third Shot: ${(session.game_type || 'open').replace(/_/g, ' ')} at ${court.name || 'Court'}`;
        const description = [
            session.notes || 'Open to Play session',
            `Skill: ${session.skill_level || 'all'}`,
            `Visibility: ${session.visibility || 'all'}`,
        ].filter(Boolean).join('\n');
        const location = [court.name, court.address, court.city].filter(Boolean).join(', ');
        const ics = Sessions._buildICS({
            uid: `third-shot-session-${session.id}@local`,
            summary,
            description,
            location,
            start,
            end,
        });

        const blob = new Blob([ics], { type: 'text/calendar;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        const safeName = (court.name || 'session').toLowerCase().replace(/[^a-z0-9]+/g, '-');
        link.download = `third-shot-${safeName}-${session.id}.ics`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        App.toast('Calendar file downloaded');
    },

    _buildICS({ uid, summary, description, location, start, end }) {
        const now = new Date();
        return [
            'BEGIN:VCALENDAR',
            'VERSION:2.0',
            'PRODID:-//Third Shot//Sessions//EN',
            'CALSCALE:GREGORIAN',
            'BEGIN:VEVENT',
            `UID:${uid}`,
            `DTSTAMP:${Sessions._icsDate(now)}`,
            `DTSTART:${Sessions._icsDate(start)}`,
            `DTEND:${Sessions._icsDate(end)}`,
            `SUMMARY:${Sessions._icsEscape(summary)}`,
            `DESCRIPTION:${Sessions._icsEscape(description)}`,
            `LOCATION:${Sessions._icsEscape(location)}`,
            'END:VEVENT',
            'END:VCALENDAR',
            '',
        ].join('\r\n');
    },

    _icsDate(date) {
        const pad = (n) => String(n).padStart(2, '0');
        return `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())}`
            + `T${pad(date.getUTCHours())}${pad(date.getUTCMinutes())}${pad(date.getUTCSeconds())}Z`;
    },

    _icsEscape(text) {
        return String(text || '')
            .replace(/\\/g, '\\\\')
            .replace(/\n/g, '\\n')
            .replace(/,/g, '\\,')
            .replace(/;/g, '\\;');
    },

    // ── Render mini cards for court detail / profile ──────────
    renderMiniCards(sessions) {
        if (!sessions || !sessions.length) {
            return '<p class="muted">No open sessions at this court</p>';
        }
        return sessions.map(s => {
            const isNow = s.session_type === 'now';
            const creator = s.creator || {};
            const players = s.players || [];
            const joined = players.filter(p => p.status === 'joined').length;
            const safeCreatorName = Sessions._e(creator.name || creator.username || '?');
            const safeGameType = Sessions._e((s.game_type || 'open').replace(/_/g, ' '));
            const safeSkillLevel = Sessions._e(s.skill_level === 'all' ? 'All levels' : (s.skill_level || 'All levels'));
            const visibilityLabel = s.visibility === 'friends' ? 'Friends only' : 'Open to all';

            let timeStr = 'Now';
            if (!isNow && s.start_time) {
                const dt = new Date(s.start_time);
                timeStr = dt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
                    + ' ' + dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
            }

            return `
            <div class="session-mini-card ${isNow ? 'session-mini-active' : ''}" onclick="App.setMainTab('sessions'); setTimeout(() => Sessions.openDetail(${s.id}), 200)">
                <div class="session-mini-top">
                    <span class="session-mini-status ${isNow ? 'live' : 'scheduled'}">${isNow ? 'Live now' : 'Scheduled'}</span>
                    <span class="session-mini-players">${joined + 1}/${s.max_players}</span>
                </div>
                <div class="session-mini-time">${isNow ? 'Open to play now' : timeStr}</div>
                <div class="session-mini-info">
                    <span class="session-mini-host">Host: ${safeCreatorName}</span>
                    <span class="session-mini-meta">${safeGameType} · ${safeSkillLevel} · ${visibilityLabel}</span>
                </div>
            </div>`;
        }).join('');
    },

    _e(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    },

    _directionsUrl(court) {
        const lat = Number(court?.latitude);
        const lng = Number(court?.longitude);
        if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
            return 'https://www.google.com/maps';
        }
        return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`;
    },
};
