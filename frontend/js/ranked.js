/**
 * Ranked competitive play — queue, match creation, scoring with confirmation,
 * pending confirmations, and leaderboard.
 */
const Ranked = {
    currentCourtId: null,

    // ── Leaderboard View ─────────────────────────────────────────────

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

    _renderLeaderboard(players) {
        const rows = players.map((p, i) => {
            const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : `#${p.rank}`;
            const eloClass = p.elo_rating >= 1400 ? 'elo-high' :
                             p.elo_rating >= 1200 ? 'elo-mid' : 'elo-low';
            const safeName = Ranked._e(p.name);
            const safeUsername = Ranked._e(p.username);
            const challengeBtn = `
                <button class="btn-secondary btn-sm" onclick="event.stopPropagation(); Ranked.openScheduledChallengeModal(${p.user_id}, 'leaderboard_challenge')">
                    ⚔️ Schedule
                </button>`;
            return `
            <tr class="lb-row ${i < 3 ? 'lb-top3' : ''}" onclick="Ranked.viewPlayer(${p.user_id})">
                <td class="lb-rank">${medal}</td>
                <td class="lb-player">
                    <strong>${safeName}</strong>
                    <span class="muted">@${safeUsername}</span>
                </td>
                <td class="lb-elo ${eloClass}">${p.elo_rating}</td>
                <td class="lb-record">${p.wins}W-${p.losses}L</td>
                <td class="lb-winrate">${p.win_rate}%</td>
                <td class="lb-games">${p.games_played}</td>
                <td>${challengeBtn}</td>
            </tr>`;
        }).join('');

        return `
        <table class="leaderboard-table">
            <thead><tr>
                <th>Rank</th><th>Player</th><th>ELO</th>
                <th>Record</th><th>Win%</th><th>Games</th><th>Challenge</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
    },

    // ── Pending Confirmations (top of ranked view) ────────────────────

    async loadPendingConfirmations(focusMatchId = null) {
        const container = document.getElementById('pending-confirmations');
        if (!container) return;
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
                    <h3>⚔️ Ranked Challenge Invites</h3>
                    <p class="muted">Accept challenges to move them into the ranked lobby.</p>
                    ${lobbies.map(l => Ranked._renderPendingLobby(l)).join('')}
                </div>` : '';
            const scoreHTML = matches.length ? `
                <div class="pending-confirm-section">
                    <h3>⏳ Pending Score Confirmations</h3>
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

    _renderPendingLobby(lobby) {
        const t1 = Ranked._e((lobby.team1 || []).map(p => p.user?.name || p.user?.username).join(' & '));
        const t2 = Ranked._e((lobby.team2 || []).map(p => p.user?.name || p.user?.username).join(' & '));
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const myEntry = (lobby.players || []).find(p => p.user_id === currentUser.id);
        const courtName = Ranked._e(lobby.court?.name || 'Court');
        const sourceLabel = lobby.source === 'scheduled_challenge' || lobby.source === 'friends_challenge'
            ? 'Scheduled challenge'
            : 'Ranked challenge';
        const accepts = (lobby.players || []).map(p => {
            const label = Ranked._e(p.user?.name || p.user?.username || '?');
            return `<span class="confirm-player ${p.acceptance_status === 'accepted' ? 'confirmed' : 'pending'}">
                ${p.acceptance_status === 'accepted' ? '✅' : '⏳'} ${label}
            </span>`;
        }).join('');
        const scheduledText = lobby.scheduled_for
            ? ` · ${Ranked._e(new Date(lobby.scheduled_for).toLocaleString())}`
            : '';
        const invitedBy = Ranked._e(
            lobby.created_by?.name
            || lobby.created_by?.username
            || 'another player'
        );

        return `
        <div class="pending-match-card">
            <div class="pending-match-score">
                <div class="pending-team"><span class="pending-team-name">${t1}</span></div>
                <span class="match-vs">VS</span>
                <div class="pending-team"><span class="pending-team-name">${t2}</span></div>
            </div>
            <div class="pending-match-confirmations">
                <span class="confirm-progress">${sourceLabel} at ${courtName}${scheduledText}</span>
                <div class="confirm-note muted">Invited by ${invitedBy}</div>
            </div>
            <div class="pending-match-confirmations">
                <span class="confirm-progress">${lobby.accepted_count || 0}/${lobby.total_players || 0} accepted</span>
                <div class="confirm-players-list">${accepts}</div>
            </div>
            ${myEntry && myEntry.acceptance_status === 'pending' ? `
            <div class="pending-match-actions">
                <button class="btn-primary btn-sm" onclick="Ranked.respondToLobby(${lobby.id}, 'accept')">✅ Accept</button>
                <button class="btn-danger btn-sm" onclick="Ranked.respondToLobby(${lobby.id}, 'decline')">❌ Decline</button>
            </div>` : `
            <div class="pending-match-actions">
                <span class="muted">Waiting for player responses...</span>
            </div>`}
        </div>`;
    },

    _renderPendingMatch(match, focusMatchId = null) {
        const t1 = Ranked._e(match.team1.map(p => p.user?.name || p.user?.username).join(' & '));
        const t2 = Ranked._e(match.team2.map(p => p.user?.name || p.user?.username).join(' & '));
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const myMp = match.players.find(p => p.user_id === currentUser.id);
        const alreadyConfirmed = myMp && myMp.confirmed;
        const confirmedCount = Number.isFinite(match.confirmed_count)
            ? Number(match.confirmed_count)
            : match.players.filter(p => p.confirmed).length;
        const totalPlayers = Number.isFinite(match.total_players)
            ? Number(match.total_players)
            : (match.players || []).length;
        const submitterName = Ranked._e(
            match.submitted_by_user?.name
            || match.submitted_by_user?.username
            || 'a player'
        );
        const courtName = Ranked._e(match.court?.name || 'Court');

        const confirmStatus = match.players.map(p => {
            const name = Ranked._e(p.user?.name || p.user?.username || '?');
            return `<span class="confirm-player ${p.confirmed ? 'confirmed' : 'pending'}">
                ${p.confirmed ? '✅' : '⏳'} ${name}
            </span>`;
        }).join('');

        return `
        <div id="pending-match-${match.id}" class="pending-match-card ${match.id === focusMatchId ? 'pending-match-focus' : ''}">
            <div class="pending-match-score">
                <div class="pending-team ${match.winner_team === 1 ? 'winner' : ''}">
                    <span class="pending-team-name">${t1}</span>
                    <span class="pending-team-score">${match.team1_score}</span>
                </div>
                <span class="match-vs">VS</span>
                <div class="pending-team ${match.winner_team === 2 ? 'winner' : ''}">
                    <span class="pending-team-name">${t2}</span>
                    <span class="pending-team-score">${match.team2_score}</span>
                </div>
            </div>
            <div class="pending-match-confirmations">
                <span class="confirm-progress">Submitted by ${submitterName} at ${courtName}</span>
            </div>
            <div class="pending-match-confirmations">
                <span class="confirm-progress">${confirmedCount}/${totalPlayers} confirmed</span>
                <div class="confirm-players-list">${confirmStatus}</div>
            </div>
            ${!alreadyConfirmed ? `
            <div class="pending-match-actions">
                <button class="btn-primary btn-sm" onclick="Ranked.confirmMatch(${match.id})">✅ Confirm Score</button>
                <button class="btn-danger btn-sm" onclick="Ranked.rejectMatch(${match.id})">❌ Reject Score</button>
            </div>` : `
            <div class="pending-match-actions">
                <span class="muted">✅ You confirmed — waiting for others</span>
            </div>`}
        </div>`;
    },

    async focusPending(matchId) {
        if (!matchId) return;
        await Ranked.loadPendingConfirmations(matchId);
    },

    // ── Confirm / Reject Match ────────────────────────────────────────

    async confirmMatch(matchId) {
        try {
            const res = await API.post(`/api/ranked/match/${matchId}/confirm`, {});
            if (res.all_confirmed) {
                App.toast('🏆 All players confirmed! Rankings updated.');
                // Show ELO results
                Ranked._showCompletedResults(res.match);
            } else {
                App.toast('✅ Score confirmed! Waiting for other players...');
            }
            Ranked.loadPendingConfirmations();
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
            Ranked.loadMatchHistory();
        } catch (err) {
            App.toast(err.message || 'Failed to confirm match', 'error');
        }
    },

    async rejectMatch(matchId) {
        if (!confirm('Reject this score? The match will be reset so the score can be re-entered.')) return;
        try {
            await API.post(`/api/ranked/match/${matchId}/reject`, {});
            App.toast('Score rejected. The match has been reset for re-scoring.');
            Ranked.loadPendingConfirmations();
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
        } catch (err) {
            App.toast(err.message || 'Failed to reject match', 'error');
        }
    },

    _showCompletedResults(match) {
        const modal = document.getElementById('match-modal');
        modal.style.display = 'flex';

        const team1Score = match.team1_score;
        const team2Score = match.team2_score;
        const winnerTeam = match.winner_team;

        const resultsHTML = match.players.map(p => {
            const won = p.team === winnerTeam;
            const change = p.elo_change || 0;
            const sign = change >= 0 ? '+' : '';
            const safeName = Ranked._e(p.user?.name || p.user?.username);
            return `
            <div class="elo-result-row ${won ? 'elo-winner' : ''}">
                <span class="elo-result-name">${won ? '🏆' : ''} ${safeName}</span>
                <span class="elo-result-team">Team ${p.team}</span>
                <span class="elo-result-rating">${Math.round(p.elo_before || 0)} → ${Math.round(p.elo_after || 0)}</span>
                <span class="elo-result-change ${change >= 0 ? 'elo-gain' : 'elo-loss'}">${sign}${Math.round(change)}</span>
            </div>`;
        }).join('');

        modal.innerHTML = `
        <div class="modal-content elo-results-modal">
            <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
            <div class="elo-results-header">
                <h2>${winnerTeam === 1 ? '🏆' : ''} Team 1: ${team1Score} — ${team2Score} :Team 2 ${winnerTeam === 2 ? '🏆' : ''}</h2>
                <p class="muted">All players confirmed — rankings updated!</p>
            </div>
            <div class="elo-results-list">
                ${resultsHTML}
            </div>
            <button class="btn-primary btn-full" onclick="document.getElementById('match-modal').style.display='none'" style="margin-top:16px">Done</button>
        </div>`;
    },

    // ── Court Competitive Play Section ────────────────────────────────

    async loadCourtRanked(courtId, options = {}) {
        Ranked.currentCourtId = courtId;
        const container = document.getElementById('court-ranked-section');
        if (!container) return;
        const silent = !!options.silent;
        if (!silent || !container.innerHTML.trim()) {
            container.innerHTML = '<div class="loading">Loading competitive play...</div>';
        }

        try {
            const token = localStorage.getItem('token');
            const pendingPromise = token
                ? API.get('/api/ranked/pending').catch(() => ({ matches: [] }))
                : Promise.resolve({ matches: [] });

            const [queueRes, activeRes, lbRes, lobbyRes, pendingRes] = await Promise.all([
                API.get(`/api/ranked/queue/${courtId}`),
                API.get(`/api/ranked/active/${courtId}`),
                API.get(`/api/ranked/leaderboard?court_id=${courtId}&limit=10`),
                API.get(`/api/ranked/court/${courtId}/lobbies`),
                pendingPromise,
            ]);
            const myPendingMatches = (pendingRes.matches || []).filter(m =>
                Number(m.court_id) === Number(courtId)
            );

            container.innerHTML = Ranked._renderCourtRanked(
                queueRes.queue || [],
                activeRes.matches || [],
                lbRes.leaderboard || [],
                courtId,
                lobbyRes || {},
                myPendingMatches,
            );
        } catch (err) {
            console.error('Failed to load ranked data:', err);
            container.innerHTML = `
                <div class="ranked-header"><h4>⚔️ Competitive Play</h4></div>
                <p class="muted">No competitive data yet. Check in and join the queue to get started!</p>
                <button class="btn-primary btn-sm" onclick="Ranked.joinQueue(${courtId}, 'doubles')" style="margin-top:8px">⚔️ Join Ranked Queue</button>
            `;
        }
    },

    _renderCourtRanked(queue, activeMatches, leaderboard, courtId, lobbyData = {}, myPendingMatches = []) {
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const inQueue = queue.some(q => q.user_id === currentUser.id);
        const readyLobbies = lobbyData.ready_lobbies || [];
        const scheduledLobbies = lobbyData.scheduled_lobbies || [];
        const pendingLobbies = (lobbyData.pending_lobbies || []).filter(l =>
            (l.players || []).some(p => p.user_id === currentUser.id)
        );
        const myActionLobbies = pendingLobbies.filter(lobby => {
            const me = (lobby.players || []).find(player => player.user_id === currentUser.id);
            return !!(me && me.acceptance_status === 'pending');
        });
        const waitingChallengeLobbies = pendingLobbies.filter(lobby => {
            const me = (lobby.players || []).find(player => player.user_id === currentUser.id);
            return !!(me && me.acceptance_status !== 'pending');
        });

        // Separate in-progress from pending-confirmation matches
        const inProgressMatches = activeMatches.filter(m => m.status === 'in_progress');
        const pendingMatches = activeMatches.filter(m => m.status === 'pending_confirmation');
        const myPendingById = new Set((myPendingMatches || []).map(m => m.id));
        const myPendingFromActive = pendingMatches.filter(m => myPendingById.has(m.id));
        const myPendingOnly = (myPendingMatches || []).filter(m =>
            !pendingMatches.some(pm => pm.id === m.id)
        );
        const myPendingCourtMatches = [...myPendingFromActive, ...myPendingOnly];
        const awaitingOthersMatches = pendingMatches.filter(m => !myPendingById.has(m.id));

        // Queue section
        const queueHTML = queue.length > 0
            ? queue.map(q => {
                const u = q.user;
                const isMe = u.id === currentUser.id;
                const safeName = Ranked._e(u.name || u.username);
                const safeInitial = Ranked._e((u.name || u.username)[0].toUpperCase());
                return `<div class="queue-player ${isMe ? 'queue-player-me' : ''}">
                    <span class="queue-avatar">${safeInitial}</span>
                    <div class="queue-info">
                        <strong>${safeName}${isMe ? ' (You)' : ''}</strong>
                        <span class="muted">ELO ${Math.round(u.elo_rating || 1200)} · ${q.match_type}</span>
                    </div>
                </div>`;
            }).join('')
            : '<p class="muted">No players in queue. Be the first to join!</p>';

        // Active matches (in progress)
        const activeHTML = inProgressMatches.length > 0
            ? inProgressMatches.map(m => Ranked._renderActiveMatch(m)).join('')
            : '';

        // Pending confirmation matches
        const myPendingHTML = myPendingCourtMatches.length > 0
            ? myPendingCourtMatches.map(m => Ranked._renderPendingCourtMatch(m)).join('')
            : '';
        const pendingHTML = awaitingOthersMatches.length > 0
            ? awaitingOthersMatches.map(m => Ranked._renderPendingCourtMatch(m)).join('')
            : '';

        // Mini leaderboard
        const lbHTML = leaderboard.length > 0
            ? leaderboard.map((p, i) => {
                const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : `#${p.rank}`;
                return `<div class="mini-lb-row">
                    <span class="mini-lb-rank">${medal}</span>
                    <span class="mini-lb-name">${Ranked._e(p.name)}</span>
                    <span class="mini-lb-elo">${p.elo_rating}</span>
                    <span class="mini-lb-record muted">${p.wins}W-${p.losses}L</span>
                </div>`;
            }).join('')
            : '<p class="muted">No ranked players at this court yet. Play a match to start!</p>';

        const queueActions = inQueue
            ? `<button class="btn-danger btn-sm" onclick="Ranked.leaveQueue(${courtId})">🔴 Leave Queue</button>`
            : `<div class="create-match-actions">
                    <button class="btn-secondary btn-sm" onclick="Ranked.joinQueue(${courtId}, 'singles')">🎾 Join Singles Queue</button>
                    <button class="btn-primary btn-sm" onclick="Ranked.joinQueue(${courtId}, 'doubles')">⚔️ Join Doubles Queue</button>
               </div>`;

        // How many for a match?
        const neededForDoubles = Math.max(0, 4 - queue.length);
        let matchReadyMsg = '';
        if (queue.length >= 4) {
            matchReadyMsg = '<div class="match-ready-banner">🎉 Enough players for doubles! Create a match below.</div>';
        } else if (queue.length >= 2) {
            matchReadyMsg = `<div class="match-ready-banner singles">🎾 Enough for singles! ${neededForDoubles} more needed for doubles.</div>`;
        } else if (queue.length === 1) {
            matchReadyMsg = '<p class="muted">Waiting for 1 more player for singles, 3 more for doubles...</p>';
        }

        const readyLobbyHTML = readyLobbies.length
            ? readyLobbies.map(l => Ranked._renderLobbyCard(l)).join('')
            : '<p class="muted">No ranked lobbies ready yet.</p>';
        const scheduledLobbyHTML = scheduledLobbies.length
            ? scheduledLobbies.map(l => Ranked._renderLobbyCard(l, true)).join('')
            : '<p class="muted">No accepted scheduled ranked games yet.</p>';
        const actionLobbyHTML = myActionLobbies.length
            ? myActionLobbies.map(l => Ranked._renderPendingLobby(l)).join('')
            : '';
        const waitingLobbyHTML = waitingChallengeLobbies.length
            ? waitingChallengeLobbies.map(l => Ranked._renderPendingLobby(l)).join('')
            : '';
        const actionItemCount = myPendingCourtMatches.length + myActionLobbies.length;

        return `
        <div class="court-ranked">
            <div class="ranked-header">
                <h4>⚔️ Competitive Play</h4>${actionItemCount > 0 ? `<span class="match-type-badge pending-badge">Needs action: ${actionItemCount}</span>` : ''}
            </div>

            <div class="ranked-sub-section">
                <h5>🚨 Needs Your Attention</h5>
                ${actionItemCount > 0 ? '<p class="muted">Handle confirmations and challenge invites here first.</p>' : '<p class="muted">No immediate actions. You are all caught up.</p>'}
                ${myPendingHTML}
                ${actionLobbyHTML}
            </div>

            ${pendingHTML ? `
            <div class="ranked-sub-section">
                <h5>⏳ Awaiting Others' Score Confirmation</h5>
                ${pendingHTML}
            </div>` : ''}

            ${waitingLobbyHTML ? `
            <div class="ranked-sub-section">
                <h5>⏳ Awaiting Challenge Responses</h5>
                ${waitingLobbyHTML}
            </div>
            ` : ''}

            <div class="ranked-sub-section">
                <h5>🎮 Ready Ranked Lobbies</h5>
                ${readyLobbyHTML}
            </div>

            <div class="ranked-sub-section">
                <h5>📅 Scheduled Ranked Games</h5>
                ${scheduledLobbyHTML}
            </div>

            <div class="ranked-queue-section">
                <div class="queue-header">
                    <h5>Players Waiting (${queue.length})</h5>
                    ${queueActions}
                </div>
                <div class="queue-list">${queueHTML}</div>
                ${matchReadyMsg}
                <div class="create-match-actions">
                    <button class="btn-secondary btn-sm" onclick="Ranked.openCourtScheduledChallenge(${courtId})">📅 Schedule Ranked Challenge</button>
                </div>
                ${queue.length >= 2 ? `
                    <div class="create-match-actions">
                        <button class="btn-primary btn-sm" onclick="Ranked.showCreateMatch(${courtId})">🎮 Create Match from Queue</button>
                    </div>
                ` : ''}
            </div>

            ${activeHTML ? `
            <div class="ranked-sub-section">
                <h5>🔴 Live Matches</h5>
                ${activeHTML}
            </div>
            ` : ''}

            <div class="ranked-sub-section">
                <h5>🏆 Court Leaderboard</h5>
                ${lbHTML}
                <button class="btn-secondary btn-sm" onclick="App.showView('ranked'); Ranked.loadLeaderboard(${courtId})" style="margin-top:8px">View Full Leaderboard</button>
            </div>
        </div>`;
    },

    _renderActiveMatch(match) {
        const t1 = Ranked._e(match.team1.map(p => p.user?.name || p.user?.username).join(' & '));
        const t2 = Ranked._e(match.team2.map(p => p.user?.name || p.user?.username).join(' & '));
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const isPlayer = match.players.some(p => p.user_id === currentUser.id);

        return `
        <div class="active-match-card">
            <div class="match-teams">
                <div class="match-team-name">${t1}</div>
                <span class="match-vs">VS</span>
                <div class="match-team-name">${t2}</div>
            </div>
            <div class="match-meta-row">
                <span class="match-type-badge">${match.match_type}</span>
                ${isPlayer ? `<button class="btn-primary btn-sm" onclick="Ranked.showScoreModal(${match.id})">📝 Enter Score</button>` : '<span class="muted">In progress...</span>'}
            </div>
        </div>`;
    },

    _renderPendingCourtMatch(match) {
        const t1 = Ranked._e(match.team1.map(p => p.user?.name || p.user?.username).join(' & '));
        const t2 = Ranked._e(match.team2.map(p => p.user?.name || p.user?.username).join(' & '));
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const myMp = match.players.find(p => p.user_id === currentUser.id);
        const alreadyConfirmed = myMp && myMp.confirmed;
        const confirmedCount = Number.isFinite(match.confirmed_count)
            ? Number(match.confirmed_count)
            : match.players.filter(p => p.confirmed).length;
        const totalPlayers = Number.isFinite(match.total_players)
            ? Number(match.total_players)
            : match.players.length;
        const submitterName = Ranked._e(
            match.submitted_by_user?.name
            || match.submitted_by_user?.username
            || 'a player'
        );
        const courtName = Ranked._e(match.court?.name || 'Court');

        return `
        <div class="active-match-card pending-confirmation-card">
            <div class="match-teams">
                <div class="match-team-name ${match.winner_team === 1 ? 'match-winner' : ''}">${t1}</div>
                <span class="match-score">${match.team1_score}-${match.team2_score}</span>
                <div class="match-team-name ${match.winner_team === 2 ? 'match-winner' : ''}">${t2}</div>
            </div>
            <div class="match-meta-row">
                <span class="muted">Submitted by ${submitterName} at ${courtName}</span>
            </div>
            <div class="match-meta-row">
                <span class="match-type-badge pending-badge">⏳ ${confirmedCount}/${totalPlayers} confirmed</span>
                ${myMp && !alreadyConfirmed ? `
                    <div class="confirm-inline-actions">
                        <button class="btn-primary btn-sm" onclick="Ranked.confirmMatch(${match.id})">✅ Confirm</button>
                        <button class="btn-danger btn-sm" onclick="Ranked.rejectMatch(${match.id})">❌ Reject</button>
                    </div>
                ` : myMp && alreadyConfirmed ? `
                    <span class="muted">✅ Confirmed</span>
                ` : `
                    <span class="muted">Awaiting confirmation...</span>
                `}
            </div>
        </div>`;
    },

    _renderLobbyCard(lobby, scheduled = false) {
        const t1 = Ranked._e((lobby.team1 || []).map(p => p.user?.name || p.user?.username).join(' & '));
        const t2 = Ranked._e((lobby.team2 || []).map(p => p.user?.name || p.user?.username).join(' & '));
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const myEntry = (lobby.players || []).find(p => p.user_id === currentUser.id);
        const canStart = myEntry && myEntry.acceptance_status === 'accepted';
        const scheduledTime = lobby.scheduled_for
            ? new Date(lobby.scheduled_for).toLocaleString()
            : null;

        return `
        <div class="active-match-card ${scheduled ? 'pending-confirmation-card' : ''}">
            <div class="match-teams">
                <div class="match-team-name">${t1}</div>
                <span class="match-vs">VS</span>
                <div class="match-team-name">${t2}</div>
            </div>
            <div class="match-meta-row">
                <span class="match-type-badge">${lobby.match_type}${scheduledTime ? ` · ${Ranked._e(scheduledTime)}` : ''}</span>
                ${canStart
                    ? `<button class="btn-primary btn-sm" onclick="Ranked.startLobbyMatch(${lobby.id})">▶️ Start Game</button>`
                    : '<span class="muted">Participants can start once checked in</span>'}
            </div>
        </div>`;
    },

    // ── Queue Actions ────────────────────────────────────────────────

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

    async respondToLobby(lobbyId, action) {
        try {
            const res = await API.post(`/api/ranked/lobby/${lobbyId}/respond`, { action });
            App.toast(action === 'accept'
                ? (res.all_accepted ? 'Challenge accepted. Lobby is ready to start.' : 'Challenge accepted.')
                : 'Challenge declined.');
            Ranked.loadPendingConfirmations();
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
        } catch (err) {
            App.toast(err.message || 'Failed to respond to challenge', 'error');
        }
    },

    async startLobbyMatch(lobbyId) {
        try {
            const res = await API.post(`/api/ranked/lobby/${lobbyId}/start`, {});
            App.toast('Ranked game started! Enter score when the game ends.');
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
            if (res.match?.id) Ranked.showScoreModal(res.match.id);
        } catch (err) {
            const startAt = err?.payload?.scheduled_for;
            if (startAt) {
                const when = new Date(startAt).toLocaleString();
                App.toast(`This scheduled game opens at ${when}.`, 'error');
                return;
            }
            App.toast(err.message || 'Could not start ranked game', 'error');
        }
    },

    async challengeCheckedInPlayer(courtId, targetUserId) {
        await Ranked.openCourtChallengeModal(courtId, targetUserId);
    },

    async openCourtChallengeModal(courtId, targetUserId) {
        try {
            const courtRes = await API.get(`/api/courts/${courtId}`);
            const checkedIn = courtRes.court?.checked_in_users || [];
            const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
            const me = checkedIn.find(u => u.id === currentUser.id);
            const target = checkedIn.find(u => u.id === targetUserId);
            if (!me) {
                App.toast('Check in at this court before challenging players.', 'error');
                return;
            }
            if (!target) {
                App.toast('That player is no longer checked in here.', 'error');
                return;
            }

            const extraOptions = checkedIn
                .filter(u => u.id !== currentUser.id && u.id !== targetUserId)
                .map(u => `<option value="${u.id}">${Ranked._e(u.name || u.username)}</option>`)
                .join('');

            const modal = document.getElementById('match-modal');
            modal.style.display = 'flex';
            modal.innerHTML = `
            <div class="modal-content">
                <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
                <h2>⚔️ Challenge Player</h2>
                <form onsubmit="Ranked.createCourtChallenge(event, ${courtId}, ${targetUserId})">
                    <div class="form-group">
                        <label>Match Type</label>
                        <select id="court-challenge-type" onchange="document.getElementById('court-challenge-doubles').style.display = this.value === 'doubles' ? 'block' : 'none'">
                            <option value="singles">Singles (1v1)</option>
                            <option value="doubles"${extraOptions ? '' : ' disabled'}>Doubles (2v2)</option>
                        </select>
                    </div>
                    <p class="muted">You: <strong>${Ranked._e(me.name || me.username)}</strong></p>
                    <p class="muted">Opponent: <strong>${Ranked._e(target.name || target.username)}</strong></p>
                    <div id="court-challenge-doubles" style="display:none">
                        <div class="form-group">
                            <label>Your Partner</label>
                            <select id="court-challenge-partner">
                                <option value="">Select partner...</option>
                                ${extraOptions}
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Opponent Partner</label>
                            <select id="court-challenge-opponent2">
                                <option value="">Select opponent partner...</option>
                                ${extraOptions}
                            </select>
                        </div>
                    </div>
                    <button type="submit" class="btn-primary btn-full">Send Challenge</button>
                </form>
            </div>`;
        } catch {
            App.toast('Unable to load challenge setup', 'error');
        }
    },

    async createCourtChallenge(e, courtId, targetUserId) {
        e.preventDefault();
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const matchType = document.getElementById('court-challenge-type').value;
        let team1 = [currentUser.id];
        let team2 = [targetUserId];

        if (matchType === 'doubles') {
            const partnerId = parseInt(document.getElementById('court-challenge-partner').value, 10);
            const opponent2Id = parseInt(document.getElementById('court-challenge-opponent2').value, 10);
            if (!partnerId || !opponent2Id) {
                App.toast('Select both doubles partners.', 'error');
                return;
            }
            const all = [currentUser.id, targetUserId, partnerId, opponent2Id];
            if (new Set(all).size !== all.length) {
                App.toast('Doubles players must all be unique.', 'error');
                return;
            }
            team1 = [currentUser.id, partnerId];
            team2 = [targetUserId, opponent2Id];
        }

        try {
            await API.post('/api/ranked/challenge/court', {
                court_id: courtId,
                match_type: matchType,
                team1,
                team2,
            });
            document.getElementById('match-modal').style.display = 'none';
            App.toast('Challenge sent.');
            Ranked.loadPendingConfirmations();
            Ranked.loadCourtRanked(courtId);
        } catch (err) {
            App.toast(err.message || 'Failed to send challenge', 'error');
        }
    },

    async openCourtScheduledChallenge(courtId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        if (!App.friendsList || !App.friendsList.length) {
            await App.loadFriendsCache();
        }

        const friends = App.friendsList || [];
        if (!friends.length) {
            App.toast('Add friends first to schedule ranked challenges.', 'error');
            return;
        }

        const opponentOptions = friends.map(f =>
            `<option value="${f.id}">${Ranked._e(f.name || f.username)}</option>`
        ).join('');
        const partnerOptions = friends.map(f =>
            `<option value="${f.id}">${Ranked._e(f.name || f.username)}</option>`
        ).join('');
        const defaultTime = (() => {
            const d = new Date(Date.now() + 24 * 60 * 60 * 1000);
            d.setMinutes(0, 0, 0);
            return d.toISOString().slice(0, 16);
        })();

        const modal = document.getElementById('match-modal');
        modal.style.display = 'flex';
        modal.innerHTML = `
        <div class="modal-content">
            <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
            <h2>📅 Schedule Ranked Challenge</h2>
            <form onsubmit="Ranked.createCourtScheduledChallenge(event, ${courtId})">
                <div class="form-group">
                    <label>Opponent</label>
                    <select id="court-scheduled-opponent" required>
                        <option value="">Select opponent...</option>
                        ${opponentOptions}
                    </select>
                </div>
                <div class="form-group">
                    <label>Scheduled Time</label>
                    <input type="datetime-local" id="court-scheduled-time" value="${defaultTime}" required>
                </div>
                <div class="form-group">
                    <label>Match Type</label>
                    <select id="court-scheduled-type" onchange="document.getElementById('court-scheduled-doubles').style.display = this.value === 'doubles' ? 'block' : 'none'">
                        <option value="singles">Singles (1v1)</option>
                        <option value="doubles">Doubles (2v2)</option>
                    </select>
                </div>
                <div id="court-scheduled-doubles" style="display:none">
                    <div class="form-group">
                        <label>Your Partner</label>
                        <select id="court-scheduled-partner1">
                            <option value="">Select partner...</option>
                            ${partnerOptions}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Opponent Partner</label>
                        <select id="court-scheduled-partner2">
                            <option value="">Select opponent partner...</option>
                            ${partnerOptions}
                        </select>
                    </div>
                </div>
                <button type="submit" class="btn-primary btn-full">Send Scheduled Challenge</button>
            </form>
        </div>`;
    },

    async createCourtScheduledChallenge(e, courtId) {
        e.preventDefault();
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const targetUserId = parseInt(document.getElementById('court-scheduled-opponent').value, 10);
        const matchType = document.getElementById('court-scheduled-type').value;
        const scheduledFor = document.getElementById('court-scheduled-time').value;
        if (!targetUserId) {
            App.toast('Pick an opponent.', 'error');
            return;
        }

        let team1 = [currentUser.id];
        let team2 = [targetUserId];
        if (matchType === 'doubles') {
            const p1 = parseInt(document.getElementById('court-scheduled-partner1').value, 10);
            const p2 = parseInt(document.getElementById('court-scheduled-partner2').value, 10);
            if (!p1 || !p2) {
                App.toast('Pick both doubles partners.', 'error');
                return;
            }
            const all = [currentUser.id, targetUserId, p1, p2];
            if (new Set(all).size !== all.length) {
                App.toast('Doubles players must all be unique.', 'error');
                return;
            }
            team1 = [currentUser.id, p1];
            team2 = [targetUserId, p2];
        }

        try {
            await API.post('/api/ranked/challenge/scheduled', {
                court_id: courtId,
                match_type: matchType,
                team1,
                team2,
                scheduled_for: scheduledFor,
                source: 'friends_challenge',
            });
            document.getElementById('match-modal').style.display = 'none';
            App.toast('Scheduled ranked challenge sent.');
            Ranked.loadPendingConfirmations();
            Ranked.loadCourtRanked(courtId);
        } catch (err) {
            App.toast(err.message || 'Failed to schedule ranked challenge', 'error');
        }
    },

    async openScheduledChallengeModal(targetUserId, source = 'scheduled_challenge') {
        try {
            const [courtsRes, targetProfile] = await Promise.all([
                API.get(App.buildCourtsQuery()),
                API.get(`/api/auth/profile/${targetUserId}`),
            ]);
            const courts = courtsRes.courts || [];
            const target = targetProfile.user || {};
            const friends = App.friendsList || [];
            const extraOptions = friends
                .filter(f => f.id !== targetUserId)
                .map(f => `<option value="${f.id}">${Ranked._e(f.name || f.username)}</option>`)
                .join('');
            const courtsOptions = courts.map(c =>
                `<option value="${c.id}">${Ranked._e(c.name)} — ${Ranked._e(c.city)}</option>`
            ).join('');
            const defaultTime = (() => {
                const d = new Date(Date.now() + 24 * 60 * 60 * 1000);
                d.setMinutes(0, 0, 0);
                return d.toISOString().slice(0, 16);
            })();

            const modal = document.getElementById('match-modal');
            modal.style.display = 'flex';
            modal.innerHTML = `
            <div class="modal-content">
                <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
                <h2>📅 Schedule Ranked Challenge</h2>
                <form onsubmit="Ranked.createScheduledChallenge(event, ${targetUserId}, '${source}')">
                    <div class="form-group">
                        <label>Court</label>
                        <select id="scheduled-challenge-court" required>
                            ${courtsOptions}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Scheduled Time</label>
                        <input type="datetime-local" id="scheduled-challenge-time" value="${defaultTime}" required>
                    </div>
                    <div class="form-group">
                        <label>Match Type</label>
                        <select id="scheduled-challenge-type" onchange="document.getElementById('scheduled-challenge-doubles').style.display = this.value === 'doubles' ? 'block' : 'none'">
                            <option value="singles">Singles (1v1)</option>
                            <option value="doubles"${extraOptions ? '' : ' disabled'}>Doubles (2v2)</option>
                        </select>
                    </div>
                    <p class="muted">Opponent: <strong>${Ranked._e(target.name || target.username || 'Player')}</strong></p>
                    <div id="scheduled-challenge-doubles" style="display:none">
                        <div class="form-group">
                            <label>Your Partner</label>
                            <select id="scheduled-challenge-partner1">
                                <option value="">Select partner...</option>
                                ${extraOptions}
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Opponent Partner</label>
                            <select id="scheduled-challenge-partner2">
                                <option value="">Select opponent partner...</option>
                                ${extraOptions}
                            </select>
                        </div>
                    </div>
                    <button type="submit" class="btn-primary btn-full">Send Scheduled Challenge</button>
                </form>
            </div>`;
        } catch {
            App.toast('Unable to open scheduled challenge', 'error');
        }
    },

    async createScheduledChallenge(e, targetUserId, source = 'scheduled_challenge') {
        e.preventDefault();
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const courtId = parseInt(document.getElementById('scheduled-challenge-court').value, 10);
        const matchType = document.getElementById('scheduled-challenge-type').value;
        const scheduledFor = document.getElementById('scheduled-challenge-time').value;

        let team1 = [currentUser.id];
        let team2 = [targetUserId];
        if (matchType === 'doubles') {
            const p1 = parseInt(document.getElementById('scheduled-challenge-partner1').value, 10);
            const p2 = parseInt(document.getElementById('scheduled-challenge-partner2').value, 10);
            if (!p1 || !p2) {
                App.toast('Pick both doubles partners.', 'error');
                return;
            }
            const all = [currentUser.id, targetUserId, p1, p2];
            if (new Set(all).size !== all.length) {
                App.toast('Doubles players must all be unique.', 'error');
                return;
            }
            team1 = [currentUser.id, p1];
            team2 = [targetUserId, p2];
        }

        try {
            await API.post('/api/ranked/challenge/scheduled', {
                court_id: courtId,
                match_type: matchType,
                team1,
                team2,
                scheduled_for: scheduledFor,
                source,
            });
            document.getElementById('match-modal').style.display = 'none';
            App.toast('Scheduled ranked challenge sent.');
            Ranked.loadPendingConfirmations();
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
        } catch (err) {
            App.toast(err.message || 'Failed to schedule challenge', 'error');
        }
    },

    // ── Match Creation Modal ─────────────────────────────────────────

    async showCreateMatch(courtId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }

        // Get queue and checked-in players for selection
        const [queueRes, courtRes] = await Promise.all([
            API.get(`/api/ranked/queue/${courtId}`),
            API.get(`/api/courts/${courtId}`),
        ]);
        const queuePlayers = (queueRes.queue || []).map(q => q.user);
        const checkedIn = (courtRes.court?.checked_in_users || []);
        // Merge unique players from queue + checked-in
        const allPlayers = [];
        const seen = new Set();
        for (const p of [...queuePlayers, ...checkedIn]) {
            if (!seen.has(p.id)) { seen.add(p.id); allPlayers.push(p); }
        }

        if (allPlayers.length < 2) {
            App.toast('Need at least 2 players to create a match. Wait for more players!', 'error');
            return;
        }

        const modal = document.getElementById('match-modal');
        modal.style.display = 'flex';
        modal.innerHTML = `
        <div class="modal-content">
            <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
            <h2>⚔️ Create Ranked Match</h2>
            <p class="muted" style="margin-bottom:8px">${allPlayers.length} players available at this court</p>
            <p class="confirm-note">📋 All players must confirm the score after the match for rankings to update.</p>
            <form id="create-match-form" onsubmit="Ranked.createMatch(event, ${courtId})">
                <div class="form-group">
                    <label>Match Type</label>
                    <select name="match_type" id="match-type-select" onchange="Ranked._updateTeamSlots()">
                        <option value="doubles" ${allPlayers.length >= 4 ? '' : 'disabled'}>Doubles (2v2)${allPlayers.length < 4 ? ' — need 4 players' : ''}</option>
                        <option value="singles">Singles (1v1)</option>
                    </select>
                </div>
                <div class="match-teams-setup">
                    <div class="team-setup" id="team1-setup">
                        <h4>Team 1</h4>
                        <div id="team1-slots"></div>
                    </div>
                    <div class="vs-divider">VS</div>
                    <div class="team-setup" id="team2-setup">
                        <h4>Team 2</h4>
                        <div id="team2-slots"></div>
                    </div>
                </div>
                <input type="hidden" id="available-players-data" value='${JSON.stringify(allPlayers)}'>
                <button type="submit" class="btn-primary btn-full" style="margin-top:16px">🎮 Start Match</button>
            </form>
        </div>`;

        // Auto-select singles if not enough for doubles
        if (allPlayers.length < 4) {
            document.getElementById('match-type-select').value = 'singles';
        }
        Ranked._updateTeamSlots();
    },

    _updateTeamSlots() {
        const type = document.getElementById('match-type-select').value;
        const count = type === 'singles' ? 1 : 2;
        const players = JSON.parse(document.getElementById('available-players-data').value || '[]');

        const options = players.map(p =>
            `<option value="${p.id}">${Ranked._e(p.name || p.username)} (ELO ${Math.round(p.elo_rating || 1200)})</option>`
        ).join('');

        for (let team = 1; team <= 2; team++) {
            const container = document.getElementById(`team${team}-slots`);
            let html = '';
            for (let i = 0; i < count; i++) {
                html += `
                <div class="form-group">
                    <label>Player ${i + 1}</label>
                    <select name="team${team}_player${i}" required>
                        <option value="">Select player...</option>
                        ${options}
                    </select>
                </div>`;
            }
            container.innerHTML = html;
        }
    },

    async createMatch(e, courtId) {
        e.preventDefault();
        const form = e.target;
        const matchType = form.match_type.value;
        const count = matchType === 'singles' ? 1 : 2;

        const team1 = [];
        const team2 = [];
        for (let i = 0; i < count; i++) {
            const t1 = parseInt(form[`team1_player${i}`].value);
            const t2 = parseInt(form[`team2_player${i}`].value);
            if (!t1 || !t2) { App.toast('Select all players', 'error'); return; }
            team1.push(t1);
            team2.push(t2);
        }

        // Check for duplicates
        const allIds = [...team1, ...team2];
        if (new Set(allIds).size !== allIds.length) {
            App.toast('A player cannot be on both teams', 'error');
            return;
        }

        try {
            const res = await API.post('/api/ranked/lobby/queue', {
                court_id: courtId, match_type: matchType,
                team1, team2,
                start_immediately: true,
            });
            document.getElementById('match-modal').style.display = 'none';
            if (res.match?.id) {
                App.toast('Ranked game started! Enter score when the game ends.');
            } else {
                App.toast('Ranked lobby created. Start the game from the court ranked section.');
            }
            Ranked.loadCourtRanked(courtId);
            Ranked.loadPendingConfirmations();
            if (res.match?.id) Ranked.showScoreModal(res.match.id);
        } catch (err) {
            App.toast(err.message || 'Failed to create match', 'error');
        }
    },

    // ── Score Submission Modal ────────────────────────────────────────

    async showScoreModal(matchId) {
        try {
            const res = await API.get(`/api/ranked/match/${matchId}`);
            const match = res.match;
            const t1 = Ranked._e(match.team1.map(p => p.user?.name || p.user?.username).join(' & '));
            const t2 = Ranked._e(match.team2.map(p => p.user?.name || p.user?.username).join(' & '));

            const modal = document.getElementById('match-modal');
            modal.style.display = 'flex';
            modal.innerHTML = `
            <div class="modal-content score-modal">
                <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
                <h2>📝 Enter Match Score</h2>
                <p class="confirm-note">All players must confirm the score before rankings update.</p>
                <div class="score-teams">
                    <div class="score-team">
                        <h4>Team 1</h4>
                        <p class="score-team-names">${t1}</p>
                        <input type="number" id="score-team1" class="score-input" min="0" max="99" value="0" required>
                    </div>
                    <div class="score-divider">—</div>
                    <div class="score-team">
                        <h4>Team 2</h4>
                        <p class="score-team-names">${t2}</p>
                        <input type="number" id="score-team2" class="score-input" min="0" max="99" value="0" required>
                    </div>
                </div>
                <p class="score-hint muted">Standard pickleball: first to 11, win by 2</p>
                <button class="btn-primary btn-full" onclick="Ranked.submitScore(${matchId})" style="margin-top:12px">Submit Score for Confirmation</button>
            </div>`;
        } catch (err) {
            App.toast('Failed to load match details', 'error');
        }
    },

    async submitScore(matchId) {
        const team1Score = parseInt(document.getElementById('score-team1').value);
        const team2Score = parseInt(document.getElementById('score-team2').value);

        if (isNaN(team1Score) || isNaN(team2Score)) {
            App.toast('Enter valid scores', 'error'); return;
        }
        if (team1Score === team2Score) {
            App.toast('Scores cannot be tied — someone must win!', 'error'); return;
        }

        try {
            const res = await API.post(`/api/ranked/match/${matchId}/score`, {
                team1_score: team1Score, team2_score: team2Score,
            });
            document.getElementById('match-modal').style.display = 'none';

            if (res.pending_confirmation) {
                App.toast('📋 Score submitted! Waiting for all players to confirm...');
            } else {
                // All confirmed (solo match edge case)
                Ranked._showCompletedResults(res.match);
            }

            // Refresh court ranked data
            if (Ranked.currentCourtId) {
                Ranked.loadCourtRanked(Ranked.currentCourtId);
            }
            Ranked.loadPendingConfirmations();
        } catch (err) {
            App.toast(err.message || 'Failed to submit score', 'error');
        }
    },

    // ── Player Profile View ──────────────────────────────────────────

    async viewPlayer(userId) {
        try {
            const [userRes, historyRes] = await Promise.all([
                API.get(`/api/auth/profile/${userId}`),
                API.get(`/api/ranked/history?user_id=${userId}&limit=10`),
            ]);
            const u = userRes.user;
            App.toast(`${u.name || u.username}: ELO ${Math.round(u.elo_rating || 1200)}, ${u.wins}W-${u.losses}L`);
        } catch {}
    },

    // ── Match History View ───────────────────────────────────────────

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

    _renderMatchHistory(match) {
        const t1 = match.team1.map(p => {
            const change = p.elo_change ? `(${p.elo_change >= 0 ? '+' : ''}${Math.round(p.elo_change)})` : '';
            return `${Ranked._e(p.user?.name || p.user?.username)} ${change}`;
        }).join(' & ');
        const t2 = match.team2.map(p => {
            const change = p.elo_change ? `(${p.elo_change >= 0 ? '+' : ''}${Math.round(p.elo_change)})` : '';
            return `${Ranked._e(p.user?.name || p.user?.username)} ${change}`;
        }).join(' & ');

        const date = new Date(match.completed_at).toLocaleDateString('en-US', {
            month: 'short', day: 'numeric',
        });

        return `
        <div class="match-history-card">
            <div class="match-history-date">${date}</div>
            <div class="match-history-teams">
                <span class="${match.winner_team === 1 ? 'match-winner' : ''}">${t1}</span>
                <span class="match-score">${match.team1_score}-${match.team2_score}</span>
                <span class="${match.winner_team === 2 ? 'match-winner' : ''}">${t2}</span>
            </div>
            <span class="match-type-badge">${match.match_type}</span>
        </div>`;
    },

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
        if (element.innerHTML !== html) {
            element.innerHTML = html;
        }
    },
};
