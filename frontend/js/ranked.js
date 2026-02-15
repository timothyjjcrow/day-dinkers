/**
 * Ranked competitive play — core state, helpers, and API actions.
 * Rendering is in ranked_render.js; modals in ranked_modals.js / ranked_challenges.js.
 */
const Ranked = {
    currentCourtId: null,
    actionDigestByCourt: {},
    actionFlashCourtId: null,
    actionFlashUntil: 0,

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
        if (!silent || !container.innerHTML.trim()) {
            container.innerHTML = '<div class="loading">Loading competitive play...</div>';
        }

        try {
            const res = await API.get(`/api/ranked/court/${courtId}/summary`);
            const nextActionState = Ranked._extractActionState(res);
            const prevDigest = Ranked.actionDigestByCourt[courtId] || '';
            const hasNewActions = !!nextActionState.digest && nextActionState.digest !== prevDigest;
            Ranked.actionDigestByCourt[courtId] = nextActionState.digest;
            if (hasNewActions) Ranked._markActionCenterUpdated(courtId);
            container.innerHTML = Ranked._renderCourtRanked(res, courtId);
            if (hasNewActions) Ranked._maybePromoteActionCenter(courtId);
        } catch (err) {
            console.error('Failed to load ranked data:', err);
            container.innerHTML = `
                <div class="ranked-header"><h4>Competitive Play</h4></div>
                <p class="muted">No competitive data yet. Check in and join the queue to get started!</p>
                <button class="btn-primary btn-sm" onclick="Ranked.joinQueue(${courtId}, 'doubles')" style="margin-top:8px">Join Ranked Queue</button>
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
        try {
            const res = await API.get(`/api/auth/profile/${userId}`);
            const u = res.user;
            App.toast(`${u.name || u.username}: ELO ${Math.round(u.elo_rating || 1200)}, ${u.wins}W-${u.losses}L`);
        } catch {}
    },
};
