/**
 * Ranked competitive play — core state, helpers, and API actions.
 * Rendering is in ranked_render.js; modals in ranked_modals.js / ranked_challenges.js.
 */
const Ranked = {
    currentCourtId: null,
    actionDigestByCourt: {},
    summaryDigestByCourt: {},
    tournamentSummaryDigestById: {},
    actionFlashCourtId: null,
    actionFlashUntil: 0,
    recentMatchesByCourt: {},
    recentGamesFilterByCourt: {},

    // ── Helpers ──────────────────────────────────────────────────────

    _e(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    },

    _setHtmlIfChanged(element, html) {
        if (!element) return;
        if (element.innerHTML !== html) element.innerHTML = html;
    },

    _currentUser() {
        return JSON.parse(localStorage.getItem('user') || '{}');
    },

    _teamNames(team) {
        return Ranked._e((team || []).map(p => p.user?.name || p.user?.username).join(' & '));
    },

    _disableBtn(event) {
        const btn = event?.currentTarget || event?.target?.closest?.('button');
        if (btn) btn.disabled = true;
        return btn;
    },

    _isoToMs(value) {
        if (!value) return 0;
        const dt = new Date(value);
        const ts = dt.getTime();
        return Number.isFinite(ts) ? ts : 0;
    },

    _buildCourtSummaryDigest(summary = {}) {
        const payload = {
            queue: summary.queue || [],
            matches: summary.matches || [],
            ready_lobbies: summary.ready_lobbies || [],
            scheduled_lobbies: summary.scheduled_lobbies || [],
            pending_lobbies: summary.pending_lobbies || [],
            leaderboard: summary.leaderboard || [],
            tournaments_live: summary.tournaments_live || [],
            tournaments_upcoming: summary.tournaments_upcoming || [],
            tournaments_completed: summary.tournaments_completed || [],
        };
        try {
            return JSON.stringify(payload);
        } catch {
            return '';
        }
    },

    _tournamentDigest(tournament = {}) {
        const t = tournament || {};
        try {
            return JSON.stringify({
                id: Number(t.id) || 0,
                status: String(t.status || ''),
                registered_count: Number(t.registered_count || t.participants_count || 0),
                checked_in_count: Number(t.checked_in_count || 0),
                start_time: String(t.start_time || ''),
                completed_at: String(t.completed_at || ''),
                cancelled_at: String(t.cancelled_at || ''),
                bracket_size: Number(t.bracket_size || 0),
                total_rounds: Number(t.total_rounds || 0),
            });
        } catch {
            return '';
        }
    },

    _findTournamentInSummary(summary = {}, tournamentId) {
        const targetId = Number(tournamentId) || 0;
        if (!targetId) return null;
        const candidates = []
            .concat(summary.tournaments_live || [])
            .concat(summary.tournaments_upcoming || [])
            .concat(summary.tournaments_completed || []);
        return candidates.find(item => Number(item.id) === targetId) || null;
    },

    _extractActionState(summary = {}) {
        const currentUser = Ranked._currentUser();
        const userId = Number(currentUser.id) || 0;
        if (!userId) return { ids: [], digest: '' };

        const inProgressMatches = (summary.matches || []).filter(m => m.status === 'in_progress');
        const pendingMatches = (summary.matches || []).filter(m => m.status === 'pending_confirmation');
        const pendingLobbies = summary.pending_lobbies || [];
        const readyLobbies = summary.ready_lobbies || [];

        const ids = [];

        inProgressMatches.forEach(match => {
            const isPlayer = (match.players || []).some(p => p.user_id === userId);
            if (isPlayer) ids.push(`live:${match.id}`);
        });
        pendingMatches.forEach(match => {
            const me = (match.players || []).find(p => p.user_id === userId);
            if (me && !me.confirmed) ids.push(`confirm:${match.id}`);
        });
        pendingLobbies.forEach(lobby => {
            const me = (lobby.players || []).find(p => p.user_id === userId);
            if (me && me.acceptance_status === 'pending') ids.push(`invite:${lobby.id}`);
        });
        readyLobbies.forEach(lobby => {
            const me = (lobby.players || []).find(p => p.user_id === userId);
            if (me && me.acceptance_status === 'accepted') ids.push(`start:${lobby.id}`);
        });

        ids.sort();
        return { ids, digest: ids.join('|') };
    },

    _markActionCenterUpdated(courtId) {
        Ranked.actionFlashCourtId = Number(courtId);
        Ranked.actionFlashUntil = Date.now() + 10000;
    },

    _isActionCenterHighlighted(courtId) {
        return Ranked.actionFlashCourtId === Number(courtId)
            && Date.now() <= Ranked.actionFlashUntil;
    },

    _maybePromoteActionCenter(courtId) {
        const tab = document.getElementById('court-ranked-tab');
        if (!tab) return;
        tab.scrollTo({ top: 0, behavior: 'smooth' });

        const center = document.getElementById('ranked-action-center');
        if (!center) return;
        center.classList.add('action-center-highlight');
        setTimeout(() => center.classList.remove('action-center-highlight'), 2200);
    },

    _getRecentGamesFilter(courtId) {
        return Ranked.recentGamesFilterByCourt[Number(courtId)] || 'all';
    },

    _setRecentMatchesForCourt(courtId, matches) {
        Ranked.recentMatchesByCourt[Number(courtId)] = Array.isArray(matches) ? matches : [];
    },

    _getRecentMatchesForCourt(courtId) {
        return Ranked.recentMatchesByCourt[Number(courtId)] || [];
    },

    renderRecentGamesForCourt(courtId) {
        const historyEl = document.getElementById('match-history-content');
        if (!historyEl) return;
        const matches = Ranked._getRecentMatchesForCourt(courtId);
        const filter = Ranked._getRecentGamesFilter(courtId);
        historyEl.innerHTML = Ranked._renderRecentGames(matches, {
            courtId: Number(courtId),
            filter,
            currentUserId: Number(Ranked._currentUser().id) || 0,
        });
    },

    setRecentGamesFilter(courtId, filter = 'all') {
        const normalized = filter === 'mine' ? 'mine' : 'all';
        Ranked.recentGamesFilterByCourt[Number(courtId)] = normalized;
        Ranked.renderRecentGamesForCourt(courtId);
    },

    _courtContext(courtId) {
        const targetCourtId = Number(courtId);
        const hasMapView = typeof MapView !== 'undefined';
        const cachedCourt = hasMapView ? MapView.currentCourtData : null;
        const courtMatches = !!(cachedCourt && Number(cachedCourt.id) === targetCourtId);
        const court = courtMatches ? cachedCourt : null;
        const sessions = (courtMatches && Array.isArray(MapView.currentCourtSessions))
            ? MapView.currentCourtSessions
            : [];
        const nowSessions = sessions.filter(s => s && s.session_type === 'now');
        const checkedInPlayers = (court && Array.isArray(court.checked_in_users))
            ? court.checked_in_users
            : [];
        const myStatus = hasMapView ? (MapView.myCheckinStatus || {}) : {};
        const amCheckedInHere = !!(
            myStatus.checked_in && Number(myStatus.court_id) === targetCourtId
        );
        return {
            court,
            sessions,
            nowSessions,
            checkedInPlayers,
            amCheckedInHere,
            courtName: court?.name || 'this court',
        };
    },

    // ── Leaderboard View ────────────────────────────────────────────

    async loadLeaderboard(courtId, options = {}) {
        const container = document.getElementById('leaderboard-content');
        if (!container) return;
        const silent = !!options.silent;
        if (!silent || !container.innerHTML.trim()) {
            container.innerHTML = '<div class="loading">Loading leaderboard...</div>';
        }

        const url = courtId
            ? `/api/ranked/leaderboard?court_id=${courtId}`
            : '/api/ranked/leaderboard';

        try {
            const res = await API.get(url);
            const lb = res.leaderboard || [];
            if (!lb.length) {
                const emptyHtml = `
                    <div class="empty-state">
                        <h3>No ranked players yet</h3>
                        <p>Play competitive matches to appear on the leaderboard!</p>
                    </div>`;
                Ranked._setHtmlIfChanged(container, emptyHtml);
                return;
            }
            Ranked._setHtmlIfChanged(container, Ranked._renderLeaderboard(lb));
        } catch {
            if (!silent || !container.innerHTML.trim()) {
                container.innerHTML = '<p class="error">Failed to load leaderboard</p>';
            }
        }
    },

    // ── Pending Confirmations (top of ranked view) ──────────────────

    async loadPendingConfirmations(focusMatchId = null) {
        const container = document.getElementById('pending-confirmations');
        if (!container) return;
        if (typeof App !== 'undefined' && App.currentScreen === 'court-details' && App.currentCourtTab === 'ranked') {
            container.style.display = 'none';
            container.innerHTML = '';
            return;
        }
        const token = localStorage.getItem('token');
        if (!token) { container.style.display = 'none'; return; }

        try {
            const [scoreRes, challengeRes] = await Promise.all([
                API.get('/api/ranked/pending'),
                API.get('/api/ranked/challenges/pending'),
            ]);
            const matches = scoreRes.matches || [];
            const lobbies = challengeRes.lobbies || [];

            if (!matches.length && !lobbies.length) {
                container.style.display = 'none';
                return;
            }
            container.style.display = 'block';
            const challengeHTML = lobbies.length ? `
                <div class="pending-confirm-section">
                    <h3>Ranked Challenge Invites</h3>
                    <p class="muted">Accept challenges to move them into the ranked lobby.</p>
                    ${lobbies.map(l => Ranked._renderPendingLobby(l)).join('')}
                </div>` : '';
            const scoreHTML = matches.length ? `
                <div class="pending-confirm-section">
                    <h3>Pending Score Confirmations</h3>
                    <p class="muted">These matches need your confirmation before rankings update.</p>
                    ${matches.map(m => Ranked._renderPendingMatch(m, focusMatchId)).join('')}
                </div>` : '';
            container.innerHTML = `${challengeHTML}${scoreHTML}`;

            if (focusMatchId) {
                const card = document.getElementById(`pending-match-${focusMatchId}`);
                if (card) {
                    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    card.classList.add('pending-match-focus');
                    setTimeout(() => card.classList.remove('pending-match-focus'), 1800);
                }
            }
        } catch {
            container.style.display = 'none';
        }
    },

    async focusPending(matchId) {
        if (!matchId) return;
        await Ranked.loadPendingConfirmations(matchId);
    },

    // ── Confirm / Reject Match ──────────────────────────────────────

    async confirmMatch(matchId, event) {
        const btn = Ranked._disableBtn(event);
        try {
            const res = await API.post(`/api/ranked/match/${matchId}/confirm`, {});
            if (res.all_confirmed) {
                App.toast('All players confirmed. Rankings updated.');
                Ranked._showCompletedResults(res.match);
            } else {
                App.toast('Score confirmed. Waiting for other players.');
            }
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
            Ranked.loadMatchHistory();
            if (typeof Ranked.refreshOpenTournamentModal === 'function') {
                Ranked.refreshOpenTournamentModal();
            }
        } catch (err) {
            if (btn) btn.disabled = false;
            App.toast(err.message || 'Failed to confirm match', 'error');
        }
    },

    async rejectMatch(matchId, event) {
        if (!confirm('Reject this score? The match will be reset so the score can be re-entered.')) return;
        const btn = Ranked._disableBtn(event);
        try {
            await API.post(`/api/ranked/match/${matchId}/reject`, {});
            App.toast('Score rejected. The match has been reset for re-scoring.');
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
            if (typeof Ranked.refreshOpenTournamentModal === 'function') {
                Ranked.refreshOpenTournamentModal();
            }
        } catch (err) {
            if (btn) btn.disabled = false;
            App.toast(err.message || 'Failed to reject match', 'error');
        }
    },

    async cancelMatch(matchId, event) {
        if (!confirm('Cancel this match? This cannot be undone.')) return;
        const btn = Ranked._disableBtn(event);
        try {
            await API.post(`/api/ranked/match/${matchId}/cancel`, {});
            App.toast('Match cancelled.');
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
            if (typeof Ranked.refreshOpenTournamentModal === 'function') {
                Ranked.refreshOpenTournamentModal();
            }
        } catch (err) {
            if (btn) btn.disabled = false;
            App.toast(err.message || 'Failed to cancel match', 'error');
        }
    },

    // ── Court Competitive Play Section ──────────────────────────────

    async loadCourtRanked(courtId, options = {}) {
        Ranked.currentCourtId = courtId;
        const container = document.getElementById('court-ranked-section');
        if (!container) return;
        const silent = !!options.silent;
        const previousTournamentView = document.getElementById('ranked-tournament-view');
        const preservedTournamentHtml = previousTournamentView ? previousTournamentView.innerHTML : '';
        if (!silent || !container.innerHTML.trim()) {
            container.innerHTML = '<div class="loading">Loading competitive play...</div>';
        }

        try {
            const res = await API.get(`/api/ranked/court/${courtId}/summary`);
            const nextSummaryDigest = Ranked._buildCourtSummaryDigest(res);
            const previousSummaryDigest = Ranked.summaryDigestByCourt[courtId] || '';
            if (silent && previousSummaryDigest && previousSummaryDigest === nextSummaryDigest) {
                return;
            }
            Ranked.summaryDigestByCourt[courtId] = nextSummaryDigest;

            const nextActionState = Ranked._extractActionState(res);
            const prevDigest = Ranked.actionDigestByCourt[courtId] || '';
            const hasNewActions = !!nextActionState.digest && nextActionState.digest !== prevDigest;
            Ranked.actionDigestByCourt[courtId] = nextActionState.digest;
            if (hasNewActions) Ranked._markActionCenterUpdated(courtId);
            container.innerHTML = Ranked._renderCourtRanked(res, courtId);
            if (
                Ranked.currentTournamentId
                && Number(Ranked.currentTournamentCourtId || courtId) === Number(courtId)
                && typeof Ranked.openTournament === 'function'
            ) {
                const tournamentId = Number(Ranked.currentTournamentId) || 0;
                const inlineView = document.getElementById('ranked-tournament-view');
                if (inlineView && preservedTournamentHtml.trim()) {
                    inlineView.innerHTML = preservedTournamentHtml;
                }

                const summaryTournament = Ranked._findTournamentInSummary(res, tournamentId);
                const nextTournamentDigest = summaryTournament
                    ? Ranked._tournamentDigest(summaryTournament)
                    : '';
                const previousTournamentDigest = Ranked.tournamentSummaryDigestById[tournamentId] || '';
                const shouldRefreshTournamentDetail = (
                    !silent
                    || !preservedTournamentHtml.trim()
                    || nextTournamentDigest !== previousTournamentDigest
                );

                if (shouldRefreshTournamentDetail) {
                    Ranked.openTournament(tournamentId, courtId, { silent: true });
                }
                Ranked.tournamentSummaryDigestById[tournamentId] = nextTournamentDigest;
            }
            if (hasNewActions) Ranked._maybePromoteActionCenter(courtId);
        } catch (err) {
            console.error('Failed to load ranked data:', err);
            container.innerHTML = `
                <div class="court-ranked">
                    <div class="ranked-hero">
                        <div class="ranked-hero-title-row">
                            <h4>Competitive Play</h4>
                            <span class="t-badge t-badge-upcoming">Active</span>
                        </div>
                        <p class="muted">No competitive data yet. Check in and join the queue to get started!</p>
                        <button class="btn-primary btn-sm" onclick="Ranked.joinQueue(${courtId}, 'doubles')" style="margin-top:10px">Join Ranked Queue</button>
                    </div>
                </div>
            `;
        }
    },

    // ── Queue Actions ───────────────────────────────────────────────

    async joinQueue(courtId, matchType = 'doubles') {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        try {
            await API.post('/api/ranked/queue/join', {
                court_id: courtId, match_type: matchType
            });
            App.toast(`Joined the ${matchType} ranked queue!`);
            Ranked.loadCourtRanked(courtId);
        } catch (err) {
            App.toast(err.message || 'Failed to join queue', 'error');
        }
    },

    async leaveQueue(courtId) {
        try {
            await API.post('/api/ranked/queue/leave', { court_id: courtId });
            App.toast('Left the queue');
            Ranked.loadCourtRanked(courtId);
        } catch { App.toast('Failed to leave queue', 'error'); }
    },

    async respondToLobby(lobbyId, action, event) {
        const btn = Ranked._disableBtn(event);
        try {
            const res = await API.post(`/api/ranked/lobby/${lobbyId}/respond`, { action });
            App.toast(action === 'accept'
                ? (res.all_accepted ? 'Challenge accepted. Lobby is ready to start.' : 'Challenge accepted.')
                : 'Challenge declined.');
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
        } catch (err) {
            if (btn) btn.disabled = false;
            App.toast(err.message || 'Failed to respond to challenge', 'error');
        }
    },

    async startLobbyMatch(lobbyId, event) {
        const btn = Ranked._disableBtn(event);
        try {
            const res = await API.post(`/api/ranked/lobby/${lobbyId}/start`, {});
            App.toast('Ranked game started. Enter score when the game ends.');
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
            if (res.match?.id) Ranked.showScoreModal(res.match.id);
        } catch (err) {
            if (btn) btn.disabled = false;
            const startAt = err?.payload?.scheduled_for;
            if (startAt) {
                const when = new Date(startAt).toLocaleString();
                App.toast(`This scheduled game opens at ${when}.`, 'error');
                return;
            }
            App.toast(err.message || 'Could not start ranked game', 'error');
        }
    },

    // ── Match History View ──────────────────────────────────────────

    async loadMatchHistory(userId, courtId, options = {}) {
        const container = document.getElementById('match-history-content');
        if (!container) return;
        const silent = !!options.silent;
        if (!silent || !container.innerHTML.trim()) {
            container.innerHTML = '<div class="loading">Loading match history...</div>';
        }

        let url = '/api/ranked/history?';
        if (userId) url += `user_id=${userId}&`;
        if (courtId) url += `court_id=${courtId}&`;

        try {
            const res = await API.get(url);
            const matches = res.matches || [];
            if (!matches.length) {
                Ranked._setHtmlIfChanged(container, '<p class="muted">No match history yet</p>');
                return;
            }
            Ranked._setHtmlIfChanged(container, matches.map(m => Ranked._renderMatchHistory(m)).join(''));
        } catch {
            if (!silent || !container.innerHTML.trim()) {
                container.innerHTML = '<p class="error">Failed to load history</p>';
            }
        }
    },

    // ── Player Profile View ─────────────────────────────────────────

    async viewPlayer(userId) {
        const targetUserId = Number(userId);
        if (!targetUserId) return;
        try {
            const res = await API.get(`/api/auth/profile/${targetUserId}`);
            const u = res.user;
            const modal = document.getElementById('match-modal');
            if (!modal) {
                App.toast(`${u.name || u.username}: ELO ${Math.round(u.elo_rating || 1200)}, ${u.wins}W-${u.losses}L`);
                return;
            }

            const currentUserId = Number(Ranked._currentUser().id) || 0;
            const isCurrentUser = targetUserId === currentUserId;
            const safeName = Ranked._e(u.name || u.username || 'Player');
            const safeUsername = Ranked._e(u.username || '');
            const safeBio = Ranked._e(u.bio || '');
            const safePlayStyle = Ranked._e(u.play_style || 'Not set');
            const safeTimes = Ranked._e(u.preferred_times || 'Not set');
            const safeSkill = u.skill_level ? Ranked._e(String(u.skill_level)) : 'Not set';
            const elo = Math.round(Number(u.elo_rating) || 1200);
            const wins = Number(u.wins) || 0;
            const losses = Number(u.losses) || 0;
            const games = Number(u.games_played) || 0;
            const totalCheckins = Number(u.total_checkins) || 0;
            const winRate = games > 0 ? `${Math.round((wins / games) * 100)}%` : '--';
            const initials = Ranked._e((u.name || u.username || '?').split(' ').map(part => part[0]).join('').slice(0, 2).toUpperCase());

            modal.style.display = 'flex';
            modal.innerHTML = `
            <div class="modal-content">
                <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
                <h2>Player Profile</h2>
                <div class="profile-header-card" style="margin-top:8px;">
                    <div class="profile-avatar-large">${initials}</div>
                    <div class="profile-header-info">
                        <h1>${safeName}</h1>
                        <div class="profile-username">@${safeUsername}</div>
                    </div>
                    <div class="profile-tags">
                        <span class="tag tag-elo">ELO ${elo}</span>
                        <span class="tag">Skill ${safeSkill}</span>
                    </div>
                </div>
                <div class="profile-stats-grid">
                    <div class="stat-card"><div class="stat-number">${wins}</div><div class="stat-label">Wins</div></div>
                    <div class="stat-card"><div class="stat-number">${losses}</div><div class="stat-label">Losses</div></div>
                    <div class="stat-card"><div class="stat-number">${winRate}</div><div class="stat-label">Win Rate</div></div>
                </div>
                <div class="profile-stats-grid">
                    <div class="stat-card"><div class="stat-number">${games}</div><div class="stat-label">Games</div></div>
                    <div class="stat-card"><div class="stat-number">${totalCheckins}</div><div class="stat-label">Check-Ins</div></div>
                    <div class="stat-card"><div class="stat-number">${safeSkill}</div><div class="stat-label">Skill</div></div>
                </div>
                <div class="profile-section">
                    <h3>Playing Preferences</h3>
                    <p class="muted">Style: ${safePlayStyle}</p>
                    <p class="muted">Preferred Times: ${safeTimes}</p>
                    ${safeBio ? `<p style="margin-top:8px">${safeBio}</p>` : '<p class="muted" style="margin-top:8px">No bio added yet.</p>'}
                </div>
                <div class="create-match-actions" style="justify-content:flex-end;">
                    ${isCurrentUser
                        ? `<button class="btn-secondary btn-sm" onclick="document.getElementById('match-modal').style.display='none'; App.setMainTab('profile');">Open My Profile</button>`
                        : `<button class="btn-secondary btn-sm" onclick="Ranked.openScheduledChallengeModal(${targetUserId}, 'leaderboard_challenge')">Challenge</button>`}
                </div>
            </div>`;
        } catch {
            App.toast('Unable to load player profile', 'error');
        }
    },
};
