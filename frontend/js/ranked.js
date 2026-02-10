/**
 * Ranked competitive play — queue, match creation, scoring with confirmation,
 * pending confirmations, and leaderboard.
 */
const Ranked = {
    currentCourtId: null,

    // ── Leaderboard View ─────────────────────────────────────────────

    async loadLeaderboard(courtId) {
        const container = document.getElementById('leaderboard-content');
        if (!container) return;
        container.innerHTML = '<div class="loading">Loading leaderboard...</div>';

        const url = courtId
            ? `/api/ranked/leaderboard?court_id=${courtId}`
            : '/api/ranked/leaderboard';

        try {
            const res = await API.get(url);
            const lb = res.leaderboard || [];
            if (!lb.length) {
                container.innerHTML = `
                    <div class="empty-state">
                        <h3>No ranked players yet</h3>
                        <p>Play competitive matches to appear on the leaderboard!</p>
                    </div>`;
                return;
            }
            container.innerHTML = Ranked._renderLeaderboard(lb);
        } catch {
            container.innerHTML = '<p class="error">Failed to load leaderboard</p>';
        }
    },

    _renderLeaderboard(players) {
        const rows = players.map((p, i) => {
            const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : `#${p.rank}`;
            const eloClass = p.elo_rating >= 1400 ? 'elo-high' :
                             p.elo_rating >= 1200 ? 'elo-mid' : 'elo-low';
            const safeName = Ranked._e(p.name);
            const safeUsername = Ranked._e(p.username);
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
            </tr>`;
        }).join('');

        return `
        <table class="leaderboard-table">
            <thead><tr>
                <th>Rank</th><th>Player</th><th>ELO</th>
                <th>Record</th><th>Win%</th><th>Games</th>
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
            const res = await API.get('/api/ranked/pending');
            const matches = res.matches || [];
            if (!matches.length) {
                container.style.display = 'none';
                return;
            }
            container.style.display = 'block';
            container.innerHTML = `
                <div class="pending-confirm-section">
                    <h3>⏳ Pending Score Confirmations</h3>
                    <p class="muted">These matches need your confirmation before rankings update.</p>
                    ${matches.map(m => Ranked._renderPendingMatch(m, focusMatchId)).join('')}
                </div>`;

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

    _renderPendingMatch(match, focusMatchId = null) {
        const t1 = Ranked._e(match.team1.map(p => p.user?.name || p.user?.username).join(' & '));
        const t2 = Ranked._e(match.team2.map(p => p.user?.name || p.user?.username).join(' & '));
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const myMp = match.players.find(p => p.user_id === currentUser.id);
        const alreadyConfirmed = myMp && myMp.confirmed;

        const confirmedCount = match.players.filter(p => p.confirmed).length;
        const totalPlayers = match.players.length;

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

    async loadCourtRanked(courtId) {
        Ranked.currentCourtId = courtId;
        const container = document.getElementById('court-ranked-section');
        if (!container) return;
        container.innerHTML = '<div class="loading">Loading competitive play...</div>';

        try {
            const [queueRes, activeRes, lbRes] = await Promise.all([
                API.get(`/api/ranked/queue/${courtId}`),
                API.get(`/api/ranked/active/${courtId}`),
                API.get(`/api/ranked/leaderboard?court_id=${courtId}&limit=10`),
            ]);

            container.innerHTML = Ranked._renderCourtRanked(
                queueRes.queue || [],
                activeRes.matches || [],
                lbRes.leaderboard || [],
                courtId,
            );
        } catch (err) {
            console.error('Failed to load ranked data:', err);
            container.innerHTML = `
                <div class="ranked-header"><h4>⚔️ Competitive Play</h4></div>
                <p class="muted">No competitive data yet. Check in and join the queue to get started!</p>
                <button class="btn-primary btn-sm" onclick="Ranked.joinQueue(${courtId})" style="margin-top:8px">⚔️ Join Ranked Queue</button>
            `;
        }
    },

    _renderCourtRanked(queue, activeMatches, leaderboard, courtId) {
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const inQueue = queue.some(q => q.user_id === currentUser.id);

        // Separate in-progress from pending-confirmation matches
        const inProgressMatches = activeMatches.filter(m => m.status === 'in_progress');
        const pendingMatches = activeMatches.filter(m => m.status === 'pending_confirmation');

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
        const pendingHTML = pendingMatches.length > 0
            ? pendingMatches.map(m => Ranked._renderPendingCourtMatch(m)).join('')
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

        const queueBtnText = inQueue ? '🔴 Leave Queue' : '⚔️ Join Ranked Queue';
        const queueBtnClass = inQueue ? 'btn-danger' : 'btn-primary';
        const queueAction = inQueue
            ? `Ranked.leaveQueue(${courtId})`
            : `Ranked.joinQueue(${courtId})`;

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

        return `
        <div class="court-ranked">
            <div class="ranked-header">
                <h4>⚔️ Competitive Play</h4>
            </div>

            ${pendingHTML ? `
            <div class="ranked-sub-section">
                <h5>⏳ Awaiting Confirmation</h5>
                ${pendingHTML}
            </div>` : ''}

            <div class="ranked-queue-section">
                <div class="queue-header">
                    <h5>Players Waiting (${queue.length})</h5>
                    <button class="${queueBtnClass} btn-sm" onclick="${queueAction}">${queueBtnText}</button>
                </div>
                <div class="queue-list">${queueHTML}</div>
                ${matchReadyMsg}
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
        const confirmedCount = match.players.filter(p => p.confirmed).length;

        return `
        <div class="active-match-card pending-confirmation-card">
            <div class="match-teams">
                <div class="match-team-name ${match.winner_team === 1 ? 'match-winner' : ''}">${t1}</div>
                <span class="match-score">${match.team1_score}-${match.team2_score}</span>
                <div class="match-team-name ${match.winner_team === 2 ? 'match-winner' : ''}">${t2}</div>
            </div>
            <div class="match-meta-row">
                <span class="match-type-badge pending-badge">⏳ ${confirmedCount}/${match.players.length} confirmed</span>
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

    // ── Queue Actions ────────────────────────────────────────────────

    async joinQueue(courtId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        try {
            await API.post('/api/ranked/queue/join', {
                court_id: courtId, match_type: 'doubles'
            });
            App.toast('Joined the ranked queue! Waiting for opponents...');
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
            await API.post('/api/ranked/match', {
                court_id: courtId, match_type: matchType,
                team1, team2,
            });
            document.getElementById('match-modal').style.display = 'none';
            App.toast('Match started! Play your game, then enter the score. All players must confirm.');
            Ranked.loadCourtRanked(courtId);
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

    async loadMatchHistory(userId, courtId) {
        const container = document.getElementById('match-history-content');
        if (!container) return;
        container.innerHTML = '<div class="loading">Loading match history...</div>';

        let url = '/api/ranked/history?';
        if (userId) url += `user_id=${userId}&`;
        if (courtId) url += `court_id=${courtId}&`;

        try {
            const res = await API.get(url);
            const matches = res.matches || [];
            if (!matches.length) {
                container.innerHTML = '<p class="muted">No match history yet</p>';
                return;
            }
            container.innerHTML = matches.map(m => Ranked._renderMatchHistory(m)).join('');
        } catch {
            container.innerHTML = '<p class="error">Failed to load history</p>';
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
};
