/**
 * Ranked tournaments UI/actions.
 * Extends Ranked with create, invite, detail, and live bracket views.
 */
Object.assign(Ranked, {
    currentTournamentId: null,
    currentTournamentCourtId: null,
    _tournamentInviteSelection: new Set(),

    _formatTournamentDate(value) {
        if (!value) return 'TBD';
        const dt = new Date(value);
        if (Number.isNaN(dt.getTime())) return 'TBD';
        return dt.toLocaleString('en-US', {
            weekday: 'short',
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
        });
    },

    _tournamentStatusBadge(status) {
        const value = String(status || 'upcoming').toLowerCase();
        if (value === 'live') return '<span class="t-badge t-badge-live">Live</span>';
        if (value === 'completed') return '<span class="t-badge t-badge-completed">Completed</span>';
        if (value === 'cancelled') return '<span class="t-badge t-badge-cancelled">Cancelled</span>';
        return '<span class="t-badge t-badge-upcoming">Upcoming</span>';
    },

    _tournamentStatusClass(status) {
        const value = String(status || 'upcoming').toLowerCase();
        if (value === 'live') return 't-status-live';
        if (value === 'completed') return 't-status-completed';
        if (value === 'cancelled') return 't-status-cancelled';
        return 't-status-upcoming';
    },

    _renderTournamentCard(tournament, courtId) {
        const t = tournament || {};
        const tId = Number(t.id) || 0;
        const name = Ranked._e(t.name || 'Tournament');
        const when = Ranked._e(Ranked._formatTournamentDate(t.start_time));
        const accessLabel = t.access_mode === 'invite_only' ? 'Invite only' : 'Open';
        const participants = Number(t.registered_count || t.participants_count || 0);
        const maxPlayers = Number(t.max_players || 0);
        const fillPct = maxPlayers ? Math.min(100, Math.round((participants / maxPlayers) * 100)) : 0;
        const playerLabel = maxPlayers ? `${participants}/${maxPlayers}` : `${participants}`;
        const statusClass = Ranked._tournamentStatusClass(t.status);
        return `
            <article class="active-match-card tournament-card ${statusClass}">
                <div class="tournament-card-header">
                    <strong>${name}</strong>
                    ${Ranked._tournamentStatusBadge(t.status)}
                </div>
                <div class="tournament-card-info">
                    <span class="t-info-item">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
                        ${when}
                    </span>
                    <span class="t-info-item">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s-8-4.5-8-11.8A8 8 0 0 1 12 2a8 8 0 0 1 8 8.2c0 7.3-8 11.8-8 11.8z"/></svg>
                        ${Ranked._e(accessLabel)}
                    </span>
                </div>
                <div class="tournament-card-footer">
                    <div class="tournament-player-bar">
                        <span class="tournament-player-bar-label">${Ranked._e(playerLabel)}</span>
                        ${maxPlayers ? `<div class="tournament-player-bar-track"><div class="tournament-player-bar-fill" style="width:${fillPct}%"></div></div>` : ''}
                    </div>
                    <button class="btn-secondary btn-sm" onclick="Ranked.openTournament(${tId}, ${Number(courtId) || Number(t.court_id) || 0})">View</button>
                </div>
            </article>
        `;
    },

    _renderTournamentsPanel(data, courtId) {
        const live = data.tournaments_live || [];
        const upcoming = data.tournaments_upcoming || [];
        const completed = data.tournaments_completed || [];
        const hasAny = live.length || upcoming.length || completed.length;
        const totalCount = live.length + upcoming.length + completed.length;
        return `
            <div class="ranked-sub-section">
                <div class="section-header">
                    <h5>Tournaments${totalCount ? ` (${totalCount})` : ''}</h5>
                    <button class="btn-primary btn-sm" onclick="Ranked.showCreateTournamentModal(${courtId})">+ New</button>
                </div>
                ${!hasAny ? '<p class="muted">No tournaments yet. Create one to get started.</p>' : ''}
                ${live.length ? `
                    <div class="ranked-action-group">
                        <h6>Live Now</h6>
                        <div class="ranked-action-items">
                            ${live.map(t => Ranked._renderTournamentCard(t, courtId)).join('')}
                        </div>
                    </div>
                ` : ''}
                ${upcoming.length ? `
                    <div class="ranked-action-group">
                        <h6>Upcoming</h6>
                        <div class="ranked-action-items">
                            ${upcoming.map(t => Ranked._renderTournamentCard(t, courtId)).join('')}
                        </div>
                    </div>
                ` : ''}
                ${completed.length ? `
                    <div class="ranked-action-group">
                        <h6>Recently Completed</h6>
                        <div class="ranked-action-items">
                            ${completed.slice(0, 3).map(t => Ranked._renderTournamentCard(t, courtId)).join('')}
                        </div>
                    </div>
                ` : ''}
                <div id="ranked-tournament-view"></div>
            </div>
        `;
    },

    _resetTournamentInviteSelection() {
        Ranked._tournamentInviteSelection = new Set();
    },

    _toggleTournamentInviteUser(userId, checked) {
        const numericId = Number(userId) || 0;
        if (!numericId) return;
        if (checked) Ranked._tournamentInviteSelection.add(numericId);
        else Ranked._tournamentInviteSelection.delete(numericId);
    },

    _selectedTournamentInviteIds() {
        return Array.from(Ranked._tournamentInviteSelection.values());
    },

    _friendInviteOptionsHtml() {
        const friends = App.friendsList || [];
        if (!friends.length) return '<p class="muted">No friends loaded yet. Use player search below.</p>';
        return friends.map(friend => {
            const selected = Ranked._tournamentInviteSelection.has(friend.id);
            const safeName = Ranked._e(friend.name || friend.username || 'Player');
            return `
                <label class="friend-pick-item">
                    <input type="checkbox" ${selected ? 'checked' : ''} onchange="Ranked._toggleTournamentInviteUser(${friend.id}, this.checked)">
                    <span class="friend-pick-avatar">${Ranked._e((safeName[0] || '?').toUpperCase())}</span>
                    <span class="friend-pick-name">${safeName}</span>
                </label>
            `;
        }).join('');
    },

    async searchTournamentUsers(query, resultsContainerId = 'tournament-search-results') {
        const container = document.getElementById(resultsContainerId);
        if (!container) return;
        const q = String(query || '').trim();
        if (q.length < 2) {
            container.innerHTML = '';
            return;
        }
        try {
            const res = await API.get(`/api/auth/users/search?q=${encodeURIComponent(q)}`);
            const currentUserId = Number(Ranked._currentUser().id) || 0;
            const users = (res.users || []).filter(user => Number(user.id) !== currentUserId);
            if (!users.length) {
                container.innerHTML = '<p class="muted">No players found.</p>';
                return;
            }
            container.innerHTML = users.map(user => {
                const selected = Ranked._tournamentInviteSelection.has(user.id);
                const safeName = Ranked._e(user.name || user.username || 'Player');
                const safeUsername = Ranked._e(user.username || '');
                return `
                    <label class="friend-pick-item">
                        <input type="checkbox" ${selected ? 'checked' : ''} onchange="Ranked._toggleTournamentInviteUser(${user.id}, this.checked)">
                        <span class="friend-pick-avatar">${Ranked._e((safeName[0] || '?').toUpperCase())}</span>
                        <span class="friend-pick-name">${safeName} <span class="muted">@${safeUsername}</span></span>
                    </label>
                `;
            }).join('');
        } catch {
            container.innerHTML = '<p class="muted">Unable to search players.</p>';
        }
    },

    async showCreateTournamentModal(courtId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        await App.loadFriendsCache();
        Ranked._resetTournamentInviteSelection();
        const modal = document.getElementById('match-modal');
        modal.style.display = 'flex';
        modal.innerHTML = `
            <div class="modal-content tournament-create-modal">
                <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
                <h2>Create Tournament</h2>
                <p class="muted" style="margin-bottom:12px">Single-elimination bracket. Set up your tournament details below.</p>
                <form onsubmit="Ranked.createTournament(event, ${Number(courtId) || 0})">
                    <div class="tournament-create-section">
                        <span class="tournament-create-section-label">Basic Info</span>
                        <div class="form-group">
                            <label>Tournament Name</label>
                            <input type="text" id="tournament-name" required maxlength="200" placeholder="Saturday Showdown">
                        </div>
                        <div class="form-group">
                            <label>Start Time</label>
                            <input type="datetime-local" id="tournament-start-time" required value="${Ranked._defaultScheduleTime()}">
                        </div>
                    </div>
                    <div class="tournament-create-section">
                        <span class="tournament-create-section-label">Tournament Settings</span>
                        <div class="form-row">
                            <div class="form-group">
                                <label>Access</label>
                                <select id="tournament-access-mode">
                                    <option value="open">Open tournament</option>
                                    <option value="invite_only">Invite only</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label>No-show policy</label>
                                <select id="tournament-no-show-policy">
                                    <option value="auto_forfeit">Auto forfeit</option>
                                    <option value="host_mark">Host marks no-show</option>
                                </select>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>Min participants</label>
                                <input type="number" id="tournament-min-participants" min="2" max="64" value="4" required>
                            </div>
                            <div class="form-group">
                                <label>Max players</label>
                                <input type="number" id="tournament-max-players" min="2" max="128" value="16" required>
                            </div>
                        </div>
                        <div class="form-group">
                            <label>No-show grace (minutes)</label>
                            <input type="number" id="tournament-no-show-grace" min="0" max="180" value="10" required>
                        </div>
                        <div class="tournament-checkbox-row">
                            <label>
                                <input type="checkbox" id="tournament-checkin-required" checked>
                                Require check-in
                            </label>
                            <label>
                                <input type="checkbox" id="tournament-affects-elo" checked>
                                Affects ELO
                            </label>
                        </div>
                    </div>
                    <div class="tournament-create-section">
                        <span class="tournament-create-section-label">Invite Players</span>
                        <div class="form-group" style="margin-bottom:8px">
                            <label>Friends</label>
                            <div class="friend-picker">${Ranked._friendInviteOptionsHtml()}</div>
                        </div>
                        <div class="form-group" style="margin-bottom:0">
                            <label>Search players</label>
                            <input type="text" placeholder="Search by name..." oninput="Ranked.searchTournamentUsers(this.value)">
                            <div id="tournament-search-results" class="friend-picker"></div>
                        </div>
                    </div>
                    <button type="submit" class="btn-primary btn-full" style="min-height:44px;font-size:14px">Create Tournament</button>
                </form>
            </div>
        `;
        if (typeof DateTimePicker !== 'undefined') {
            DateTimePicker.enhanceWithin(modal);
        }
    },

    async createTournament(event, courtId) {
        event.preventDefault();
        const btn = Ranked._disableBtn(event);
        const name = String(document.getElementById('tournament-name')?.value || '').trim();
        const startTime = document.getElementById('tournament-start-time')?.value || '';
        const accessMode = document.getElementById('tournament-access-mode')?.value || 'open';
        const noShowPolicy = document.getElementById('tournament-no-show-policy')?.value || 'auto_forfeit';
        const minParticipants = Number(document.getElementById('tournament-min-participants')?.value || 4);
        const maxPlayers = Number(document.getElementById('tournament-max-players')?.value || 16);
        const noShowGraceMinutes = Number(document.getElementById('tournament-no-show-grace')?.value || 10);
        const checkInRequired = !!document.getElementById('tournament-checkin-required')?.checked;
        const affectsElo = !!document.getElementById('tournament-affects-elo')?.checked;
        if (!name) {
            if (btn) btn.disabled = false;
            App.toast('Tournament name is required', 'error');
            return;
        }
        if (!Number.isFinite(minParticipants) || !Number.isFinite(maxPlayers) || minParticipants < 2 || maxPlayers < 2) {
            if (btn) btn.disabled = false;
            App.toast('Participant counts must be valid numbers', 'error');
            return;
        }
        if (minParticipants > maxPlayers) {
            if (btn) btn.disabled = false;
            App.toast('Min participants cannot exceed max players', 'error');
            return;
        }
        try {
            const res = await API.post('/api/ranked/tournaments', {
                court_id: Number(courtId),
                name,
                start_time: startTime,
                access_mode: accessMode,
                no_show_policy: noShowPolicy,
                min_participants: minParticipants,
                max_players: maxPlayers,
                check_in_required: checkInRequired,
                affects_elo: affectsElo,
                no_show_grace_minutes: noShowGraceMinutes,
                invite_user_ids: Ranked._selectedTournamentInviteIds(),
            });
            const tournament = res.tournament || {};
            document.getElementById('match-modal').style.display = 'none';
            App.toast('Tournament created.');
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
            if (tournament.id) Ranked.openTournament(tournament.id, courtId);
        } catch (err) {
            if (btn) btn.disabled = false;
            App.toast(err.message || 'Failed to create tournament', 'error');
        }
    },

    closeTournamentInlineView() {
        const view = document.getElementById('ranked-tournament-view');
        if (view) view.innerHTML = '';
        Ranked.currentTournamentId = null;
        Ranked.currentTournamentCourtId = null;
    },

    async openTournament(tournamentId, courtId = null, options = {}) {
        const id = Number(tournamentId) || 0;
        if (!id) return;
        const silent = !!options.silent;
        try {
            const res = await API.get(`/api/ranked/tournaments/${id}`);
            const tournament = res.tournament || {};
            if (typeof Ranked._tournamentDigest === 'function') {
                const digest = Ranked._tournamentDigest(tournament);
                if (!Ranked.tournamentSummaryDigestById) Ranked.tournamentSummaryDigestById = {};
                Ranked.tournamentSummaryDigestById[id] = digest;
            }
            Ranked.currentTournamentId = id;
            Ranked.currentTournamentCourtId = Number(courtId) || Number(tournament.court_id) || null;
            const inlineView = document.getElementById('ranked-tournament-view');
            if (inlineView) {
                inlineView.innerHTML = Ranked._renderTournamentDetail(tournament, { inline: true });
                if (!silent) {
                    inlineView.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
                return;
            }

            const modal = document.getElementById('match-modal');
            if (modal) {
                modal.style.display = 'flex';
                modal.innerHTML = Ranked._renderTournamentDetail(tournament, { inline: false });
            }
        } catch (err) {
            if (!silent) App.toast(err.message || 'Unable to load tournament', 'error');
        }
    },

    async refreshOpenTournamentModal() {
        if (!Ranked.currentTournamentId) return;
        await Ranked.openTournament(Ranked.currentTournamentId, Ranked.currentTournamentCourtId, { silent: true });
    },

    _renderTournamentParticipantRow(tournament, participant, currentUserId, isHost) {
        const row = participant || {};
        const user = row.user || {};
        const canMarkNoShow = isHost
            && tournament.status === 'upcoming'
            && !row.checked_in_at
            && row.participant_status !== 'no_show'
            && Number(row.user_id) !== Number(tournament.host_user_id);
        const isMe = Number(user.id) === Number(currentUserId);
        const safeName = Ranked._e(user.name || user.username || 'Player');
        const initial = Ranked._e((safeName[0] || '?').toUpperCase());

        const statusChip = (() => {
            if (row.participant_status === 'no_show') return '<span class="t-participant-chip t-chip-no-show">No-show</span>';
            if (row.checked_in_at) return '<span class="t-participant-chip t-chip-checked-in">Checked in</span>';
            if (row.invite_status === 'invited') return '<span class="t-participant-chip t-chip-invited">Invited</span>';
            if (row.participant_status === 'registered') return '<span class="t-participant-chip t-chip-registered">Registered</span>';
            return `<span class="t-participant-chip t-chip-registered">${Ranked._e(String(row.participant_status || 'registered').replace(/_/g, ' '))}</span>`;
        })();
        const checkInChip = tournament.check_in_required && !row.checked_in_at && row.participant_status !== 'no_show'
            ? '<span class="t-participant-chip t-chip-not-checked-in">Not checked in</span>'
            : '';

        return `
            <div class="tournament-participant ${isMe ? 't-participant-self' : ''}">
                <div class="tournament-participant-avatar">${initial}</div>
                <div class="tournament-participant-info">
                    <div class="tournament-participant-name">${safeName}${isMe ? ' (You)' : ''}</div>
                    <div class="tournament-participant-status">
                        ${statusChip}
                        ${checkInChip}
                    </div>
                </div>
                ${canMarkNoShow ? `<button class="btn-danger btn-sm" onclick="Ranked.markTournamentNoShow(${tournament.id}, ${row.user_id})">No-show</button>` : ''}
            </div>
        `;
    },

    _renderTournamentMatchCard(match, currentUserId) {
        const m = match || {};
        const t1 = Ranked._teamNames(m.team1 || []);
        const t2 = Ranked._teamNames(m.team2 || []);
        const isPlayer = (m.players || []).some(player => Number(player.user_id) === Number(currentUserId));
        const myEntry = (m.players || []).find(player => Number(player.user_id) === Number(currentUserId));
        const hasScore = m.team1_score !== null && m.team2_score !== null;
        const t1Won = hasScore && Number(m.team1_score) > Number(m.team2_score);
        const t2Won = hasScore && Number(m.team2_score) > Number(m.team1_score);

        const statusClass = m.status === 'in_progress' ? 't-match-in-progress'
            : m.status === 'completed' ? 't-match-completed' : '';

        const statusBadge = (() => {
            if (m.status === 'in_progress') return '<span class="t-badge t-badge-live">In Progress</span>';
            if (m.status === 'pending_confirmation') return '<span class="t-badge t-badge-upcoming">Confirming</span>';
            if (m.status === 'completed') return '<span class="t-badge t-badge-completed">Done</span>';
            if (m.status === 'cancelled') return '<span class="t-badge t-badge-cancelled">Cancelled</span>';
            return '<span class="t-badge t-badge-upcoming">Pending</span>';
        })();

        const actionHtml = (() => {
            if (m.status === 'in_progress' && isPlayer) {
                return `<button class="btn-primary btn-sm" onclick="Ranked.showScoreModal(${m.id})">Enter Score</button>`;
            }
            if (m.status === 'pending_confirmation' && myEntry && !myEntry.confirmed) {
                return `
                    <button class="btn-primary btn-sm" onclick="Ranked.confirmMatch(${m.id}, event)">Confirm</button>
                    <button class="btn-danger btn-sm" onclick="Ranked.rejectMatch(${m.id}, event)">Reject</button>
                `;
            }
            return '';
        })();

        return `
            <article class="tournament-match-card ${statusClass}">
                <div class="tournament-match-header">
                    <span class="t-match-label">M${Number(m.bracket_slot) || 1}</span>
                    ${statusBadge}
                </div>
                <div class="tournament-match-teams">
                    <div class="tournament-match-team ${t1Won ? 't-team-winner' : ''} ${!t1 ? 't-team-tbd' : ''}">
                        <span>${t1 || 'TBD'}</span>
                        ${hasScore ? `<span class="t-team-score">${m.team1_score}</span>` : ''}
                    </div>
                    <div class="tournament-match-team ${t2Won ? 't-team-winner' : ''} ${!t2 ? 't-team-tbd' : ''}">
                        <span>${t2 || 'TBD'}</span>
                        ${hasScore ? `<span class="t-team-score">${m.team2_score}</span>` : ''}
                    </div>
                </div>
                ${actionHtml ? `<div class="tournament-match-actions">${actionHtml}</div>` : ''}
            </article>
        `;
    },

    _renderTournamentDetail(tournament, options = {}) {
        const currentUserId = Number(Ranked._currentUser().id) || 0;
        const isHost = Number(tournament.host_user_id) === currentUserId;
        const my = tournament.my_participation || null;
        const participants = tournament.participants || [];
        const rounds = tournament.bracket?.rounds || [];
        const results = tournament.results || [];
        const isInline = !!options.inline;
        const accessLabel = tournament.access_mode === 'invite_only' ? 'Invite only' : 'Open';
        const checkInLabel = tournament.check_in_required ? 'Check-in required' : 'No check-in';
        const noShowLabel = tournament.no_show_policy === 'host_mark' ? 'Host marks' : 'Auto-forfeit';
        const registered = Number(tournament.registered_count || 0);
        const maxPlayers = Number(tournament.max_players || 0);
        const fillPct = maxPlayers ? Math.min(100, Math.round((registered / maxPlayers) * 100)) : 0;
        const statusValue = String(tournament.status || 'upcoming').toLowerCase();

        const actions = [];
        if (tournament.status === 'upcoming' && !my && tournament.access_mode === 'open') {
            actions.push(`<button class="btn-primary btn-sm" onclick="Ranked.joinTournament(${tournament.id})">Join Tournament</button>`);
        }
        if (tournament.status === 'upcoming' && my && my.invite_status === 'invited') {
            actions.push(`<button class="btn-primary btn-sm" onclick="Ranked.respondTournamentInvite(${tournament.id}, 'accept')">Accept Invite</button>`);
            actions.push(`<button class="btn-danger btn-sm" onclick="Ranked.respondTournamentInvite(${tournament.id}, 'decline')">Decline</button>`);
        }
        if (tournament.status === 'upcoming' && my && tournament.check_in_required && !my.checked_in_at && my.participant_status === 'registered') {
            actions.push(`<button class="btn-primary btn-sm" onclick="Ranked.checkInTournament(${tournament.id})">Check In</button>`);
        }
        if (
            tournament.status === 'upcoming'
            && my
            && !isHost
            && ['registered', 'checked_in'].includes(String(my.participant_status || ''))
        ) {
            actions.push(`<button class="btn-outline btn-sm" onclick="Ranked.withdrawTournament(${tournament.id})">Withdraw</button>`);
        }
        if (tournament.status === 'upcoming' && isHost) {
            actions.push(`<button class="btn-secondary btn-sm" onclick="Ranked.openTournamentInviteModal(${tournament.id})">Invite Players</button>`);
            actions.push(`<button class="btn-primary btn-sm" onclick="Ranked.startTournament(${tournament.id})">Start Tournament</button>`);
        }
        if (isHost && ['upcoming', 'live'].includes(String(tournament.status || ''))) {
            actions.push(`<button class="btn-danger btn-sm" onclick="Ranked.cancelTournament(${tournament.id})">Cancel</button>`);
        }

        const closeButton = isInline
            ? `<button class="btn-secondary btn-sm" onclick="Ranked.closeTournamentInlineView()">Close</button>`
            : `<button class="modal-close" onclick="Ranked.closeTournamentInlineView();document.getElementById('match-modal').style.display='none'">&times;</button>`;
        const wrapperClass = isInline
            ? 'ranked-sub-section tournament-detail-panel'
            : 'modal-content tournament-detail-modal';
        const heroClass = statusValue === 'live' ? 't-hero-live' : (statusValue === 'upcoming' ? 't-hero-upcoming' : '');

        return `
            <div class="${wrapperClass}">
                ${!isInline ? closeButton : ''}
                <div class="tournament-detail-hero ${heroClass}">
                    <div class="tournament-detail-title-row">
                        <h5>${Ranked._e(tournament.name || 'Tournament')}</h5>
                        <div style="display:flex;gap:6px;align-items:center">
                            ${Ranked._tournamentStatusBadge(tournament.status)}
                            ${isInline ? `<button class="btn-secondary btn-sm" onclick="Ranked.closeTournamentInlineView()" style="margin-left:4px">Close</button>` : ''}
                        </div>
                    </div>
                    ${tournament.description ? `<p class="muted" style="margin-bottom:8px">${Ranked._e(tournament.description)}</p>` : ''}
                    <div class="tournament-detail-meta">
                        <div class="t-meta-item">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
                            <strong>${Ranked._e(Ranked._formatTournamentDate(tournament.start_time))}</strong>
                        </div>
                        <div class="t-meta-item">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                            <strong>${registered}/${maxPlayers}</strong> players
                        </div>
                        <div class="t-meta-item">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>
                            Min ${Number(tournament.min_participants || 0)}
                        </div>
                        <div class="t-meta-item">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s-8-4.5-8-11.8A8 8 0 0 1 12 2a8 8 0 0 1 8 8.2c0 7.3-8 11.8-8 11.8z"/></svg>
                            ${Ranked._e(accessLabel)}
                        </div>
                        <div class="t-meta-item">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                            ${tournament.affects_elo ? 'ELO on' : 'ELO off'}
                        </div>
                        <div class="t-meta-item">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
                            ${Ranked._e(checkInLabel)}
                        </div>
                    </div>
                    ${maxPlayers ? `
                        <div class="tournament-player-bar" style="margin-top:10px">
                            <div class="tournament-player-bar-track" style="height:6px">
                                <div class="tournament-player-bar-fill" style="width:${fillPct}%;background:${statusValue === 'live' ? '#22c55e' : '#6366f1'}"></div>
                            </div>
                        </div>
                    ` : ''}
                    ${actions.length ? `<div class="tournament-actions">${actions.join('')}</div>` : ''}
                </div>

                <div class="ranked-sub-section">
                    <div class="section-header" style="margin-bottom:8px">
                        <h5>Participants</h5>
                        <span class="muted">${registered} registered</span>
                    </div>
                    <div class="tournament-participant-list">
                        ${participants.length
                            ? participants.map(p => Ranked._renderTournamentParticipantRow(tournament, p, currentUserId, isHost)).join('')
                            : '<p class="muted">No participants yet.</p>'}
                    </div>
                </div>

                <div class="ranked-sub-section">
                    <div class="section-header" style="margin-bottom:8px">
                        <h5>${statusValue === 'live' ? 'Live Bracket' : 'Bracket'}</h5>
                        ${rounds.length ? `<span class="muted">${rounds.length} round${rounds.length !== 1 ? 's' : ''}</span>` : ''}
                    </div>
                    ${rounds.length ? `
                        <div class="tournament-round-grid">
                            ${rounds.map((round, idx) => {
                                const matches = round.matches || [];
                                const roundLabel = idx === rounds.length - 1 && rounds.length > 1 ? 'Final' : `Round ${Number(round.round) || idx + 1}`;
                                return `
                                    <div class="tournament-round-column">
                                        <div class="tournament-round-header">
                                            <h6>${Ranked._e(roundLabel)}</h6>
                                            <span class="t-round-count">${matches.length} match${matches.length !== 1 ? 'es' : ''}</span>
                                        </div>
                                        ${matches.map(match => Ranked._renderTournamentMatchCard(match, currentUserId)).join('')}
                                    </div>
                                `;
                            }).join('')}
                        </div>
                    ` : '<p class="muted">Bracket appears when tournament starts.</p>'}
                </div>

                <div class="ranked-sub-section">
                    <div class="section-header" style="margin-bottom:8px">
                        <h5>Results</h5>
                    </div>
                    ${results.length ? `
                        <div class="tournament-results-list">
                            ${results
                                .slice()
                                .sort((a, b) => (a.placement || 999) - (b.placement || 999))
                                .map(result => {
                                    const placement = Number(result.placement) || 0;
                                    const placementClass = placement <= 3 ? `t-placement-${placement}` : '';
                                    return `
                                        <div class="tournament-result-row ${placementClass}">
                                            <div class="tournament-result-placement">${placement || '-'}</div>
                                            <div class="tournament-result-info">
                                                <div class="tournament-result-name">${Ranked._e(result.user?.name || result.user?.username || 'Player')}</div>
                                                <div class="tournament-result-stats">
                                                    <span>${Number(result.points) || 0} pts</span>
                                                    <span>${Number(result.wins) || 0}W-${Number(result.losses) || 0}L</span>
                                                </div>
                                            </div>
                                        </div>
                                    `;
                                }).join('')}
                        </div>
                    ` : '<p class="muted">Results update as rounds complete.</p>'}
                </div>
            </div>
        `;
    },

    async joinTournament(tournamentId) {
        try {
            await API.post(`/api/ranked/tournaments/${tournamentId}/join`, {});
            App.toast('Joined tournament.');
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
            Ranked.refreshOpenTournamentModal();
        } catch (err) {
            App.toast(err.message || 'Could not join tournament', 'error');
        }
    },

    async respondTournamentInvite(tournamentId, action) {
        try {
            await API.post(`/api/ranked/tournaments/${tournamentId}/respond`, { action });
            App.toast(action === 'accept' ? 'Tournament invite accepted.' : 'Tournament invite declined.');
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
            Ranked.refreshOpenTournamentModal();
        } catch (err) {
            App.toast(err.message || 'Could not respond to invite', 'error');
        }
    },

    async checkInTournament(tournamentId) {
        try {
            await API.post(`/api/ranked/tournaments/${tournamentId}/check-in`, {});
            App.toast('Checked in for tournament.');
            Ranked.refreshOpenTournamentModal();
        } catch (err) {
            App.toast(err.message || 'Tournament check-in failed', 'error');
        }
    },

    async startTournament(tournamentId) {
        if (!confirm('Start this tournament now?')) return;
        try {
            await API.post(`/api/ranked/tournaments/${tournamentId}/start`, {});
            App.toast('Tournament started.');
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
            Ranked.refreshOpenTournamentModal();
        } catch (err) {
            App.toast(err.message || 'Could not start tournament', 'error');
        }
    },

    async withdrawTournament(tournamentId) {
        if (!confirm('Withdraw from this tournament?')) return;
        try {
            await API.post(`/api/ranked/tournaments/${tournamentId}/withdraw`, {});
            App.toast('Withdrawn from tournament.');
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
            Ranked.refreshOpenTournamentModal();
        } catch (err) {
            App.toast(err.message || 'Could not withdraw', 'error');
        }
    },

    async cancelTournament(tournamentId) {
        if (!confirm('Cancel this tournament for all players?')) return;
        try {
            await API.post(`/api/ranked/tournaments/${tournamentId}/cancel`, {});
            App.toast('Tournament cancelled.');
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
            Ranked.refreshOpenTournamentModal();
        } catch (err) {
            App.toast(err.message || 'Could not cancel tournament', 'error');
        }
    },

    async markTournamentNoShow(tournamentId, userId) {
        if (!confirm('Mark this player as no-show?')) return;
        try {
            await API.post(`/api/ranked/tournaments/${tournamentId}/participants/${userId}/no-show`, {});
            App.toast('Participant marked no-show.');
            Ranked.refreshOpenTournamentModal();
        } catch (err) {
            App.toast(err.message || 'Could not mark no-show', 'error');
        }
    },

    async openTournamentInviteModal(tournamentId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        await App.loadFriendsCache();
        Ranked._resetTournamentInviteSelection();
        const modal = document.getElementById('match-modal');
        modal.style.display = 'flex';
        modal.innerHTML = `
            <div class="modal-content tournament-invite-modal">
                <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none';Ranked.refreshOpenTournamentModal()">&times;</button>
                <h2>Invite Players</h2>
                <p class="muted" style="margin-bottom:12px">Select friends or search for players to invite.</p>
                <div class="tournament-create-section">
                    <span class="tournament-create-section-label">Friends</span>
                    <div class="friend-picker">${Ranked._friendInviteOptionsHtml()}</div>
                </div>
                <div class="tournament-create-section">
                    <span class="tournament-create-section-label">Search Players</span>
                    <div class="form-group" style="margin-bottom:0">
                        <input type="text" placeholder="Search by name..." oninput="Ranked.searchTournamentUsers(this.value, 'tournament-search-results')">
                        <div id="tournament-search-results" class="friend-picker"></div>
                    </div>
                </div>
                <button class="btn-primary btn-full" style="min-height:44px;font-size:14px" onclick="Ranked.sendTournamentInvites(${tournamentId})">Send Invites</button>
            </div>
        `;
    },

    async sendTournamentInvites(tournamentId) {
        const userIds = Ranked._selectedTournamentInviteIds();
        if (!userIds.length) {
            App.toast('Select at least one player', 'error');
            return;
        }
        try {
            await API.post(`/api/ranked/tournaments/${tournamentId}/invite`, { user_ids: userIds });
            App.toast('Invites sent.');
            const modal = document.getElementById('match-modal');
            if (modal) modal.style.display = 'none';
            Ranked.refreshOpenTournamentModal();
        } catch (err) {
            App.toast(err.message || 'Failed to send invites', 'error');
        }
    },

    _wait(ms) {
        const delay = Number(ms) || 0;
        return new Promise(resolve => setTimeout(resolve, Math.max(0, delay)));
    },

    async _openTournamentDeepLink(courtId, tournamentId) {
        const targetCourtId = Number(courtId) || 0;
        const targetTournamentId = Number(tournamentId) || 0;
        if (!targetCourtId || !targetTournamentId) return;

        App.openCourtDetails(targetCourtId);
        App.setCourtTab('ranked');

        // Wait for ranked tournament container so deep-links consistently
        // open inline under the Ranked tab (instead of racing into modal fallback).
        for (let attempt = 0; attempt < 10; attempt += 1) {
            const inlineView = document.getElementById('ranked-tournament-view');
            if (inlineView) {
                await Ranked.openTournament(targetTournamentId, targetCourtId, { silent: attempt > 0 });
                return;
            }
            await Ranked._wait(150);
        }

        // Final fallback still opens the tournament if ranked view is delayed.
        Ranked.openTournament(targetTournamentId, targetCourtId);
    },

    openTournamentFromSchedule(courtId, tournamentId) {
        Ranked._openTournamentDeepLink(courtId, tournamentId);
    },

    _renderCompactTournamentsSection(data, courtId) {
        const live = data.tournaments_live || [];
        const upcoming = data.tournaments_upcoming || [];
        if (!live.length && !upcoming.length) return '';

        let html = '<div style="margin-bottom:12px">';
        html += `<div class="compact-ranked-header">
            <h4>Tournaments</h4>
            <button class="btn-secondary btn-sm" onclick="Ranked.showCreateTournamentModal(${courtId})">+ New</button>
        </div>`;
        if (live.length) {
            html += live.map(t => Ranked._renderTournamentCard(t, courtId)).join('');
        }
        if (upcoming.length) {
            html += upcoming.map(t => Ranked._renderTournamentCard(t, courtId)).join('');
        }
        html += '<div id="ranked-tournament-view"></div>';
        html += '</div>';
        return html;
    },
});
