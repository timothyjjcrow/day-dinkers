/**
 * Ranked rendering functions — leaderboard, court section, match cards.
 * Extends the Ranked object defined in ranked.js.
 */
Object.assign(Ranked, {

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

    _renderCourtRanked(data, courtId) {
        const queue = data.queue || [];
        const activeMatches = data.matches || [];
        const leaderboard = data.leaderboard || [];
        const readyLobbies = data.ready_lobbies || [];
        const scheduledLobbies = data.scheduled_lobbies || [];
        const allPendingLobbies = data.pending_lobbies || [];

        const currentUser = Ranked._currentUser();
        const inQueue = queue.some(q => q.user_id === currentUser.id);

        // Filter lobbies to ones involving the current user
        const pendingLobbies = allPendingLobbies.filter(l =>
            (l.players || []).some(p => p.user_id === currentUser.id)
        );
        const myActionLobbies = pendingLobbies.filter(lobby => {
            const me = (lobby.players || []).find(p => p.user_id === currentUser.id);
            return !!(me && me.acceptance_status === 'pending');
        });
        const waitingChallengeLobbies = pendingLobbies.filter(lobby => {
            const me = (lobby.players || []).find(p => p.user_id === currentUser.id);
            return !!(me && me.acceptance_status !== 'pending');
        });

        // Derive pending status from active matches (no separate API call needed)
        const inProgressMatches = activeMatches.filter(m => m.status === 'in_progress');
        const pendingMatches = activeMatches.filter(m => m.status === 'pending_confirmation');
        const myPendingCourtMatches = pendingMatches.filter(m => {
            const myMp = (m.players || []).find(p => p.user_id === currentUser.id);
            return myMp && !myMp.confirmed;
        });
        const awaitingOthersMatches = pendingMatches.filter(m => {
            const myMp = (m.players || []).find(p => p.user_id === currentUser.id);
            return !myMp || myMp.confirmed;
        });

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

        const activeHTML = inProgressMatches.map(m => Ranked._renderActiveMatch(m)).join('');
        const myPendingHTML = myPendingCourtMatches.map(m => Ranked._renderPendingCourtMatch(m)).join('');
        const pendingHTML = awaitingOthersMatches.map(m => Ranked._renderPendingCourtMatch(m)).join('');

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
        const actionLobbyHTML = myActionLobbies.map(l => Ranked._renderPendingLobby(l)).join('');
        const waitingLobbyHTML = waitingChallengeLobbies.map(l => Ranked._renderPendingLobby(l)).join('');
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
        const t1 = Ranked._teamNames(match.team1);
        const t2 = Ranked._teamNames(match.team2);
        const isPlayer = match.players.some(p => p.user_id === Ranked._currentUser().id);

        return `
        <div class="active-match-card">
            <div class="match-teams">
                <div class="match-team-name">${t1}</div>
                <span class="match-vs">VS</span>
                <div class="match-team-name">${t2}</div>
            </div>
            <div class="match-meta-row">
                <span class="match-type-badge">${match.match_type}</span>
                ${isPlayer ? `
                    <button class="btn-primary btn-sm" onclick="Ranked.showScoreModal(${match.id})">📝 Enter Score</button>
                    <button class="btn-outline btn-sm" onclick="Ranked.cancelMatch(${match.id}, event)">Cancel</button>
                ` : '<span class="muted">In progress...</span>'}
            </div>
        </div>`;
    },

    _renderPendingCourtMatch(match) {
        const t1 = Ranked._teamNames(match.team1);
        const t2 = Ranked._teamNames(match.team2);
        const currentUser = Ranked._currentUser();
        const myMp = match.players.find(p => p.user_id === currentUser.id);
        const alreadyConfirmed = myMp && myMp.confirmed;
        const confirmedCount = Number.isFinite(match.confirmed_count)
            ? Number(match.confirmed_count)
            : match.players.filter(p => p.confirmed).length;
        const totalPlayers = Number.isFinite(match.total_players)
            ? Number(match.total_players)
            : match.players.length;
        const submitterName = Ranked._e(
            match.submitted_by_user?.name || match.submitted_by_user?.username || 'a player'
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
                        <button class="btn-primary btn-sm" onclick="Ranked.confirmMatch(${match.id}, event)">✅ Confirm</button>
                        <button class="btn-danger btn-sm" onclick="Ranked.rejectMatch(${match.id}, event)">❌ Reject</button>
                    </div>
                ` : myMp && alreadyConfirmed ? `
                    <span class="muted">✅ Confirmed</span>
                ` : `
                    <span class="muted">Awaiting confirmation...</span>
                `}
            </div>
        </div>`;
    },

    _renderPendingLobby(lobby) {
        const t1 = Ranked._teamNames(lobby.team1);
        const t2 = Ranked._teamNames(lobby.team2);
        const currentUser = Ranked._currentUser();
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
            lobby.created_by?.name || lobby.created_by?.username || 'another player'
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
                <button class="btn-primary btn-sm" onclick="Ranked.respondToLobby(${lobby.id}, 'accept', event)">✅ Accept</button>
                <button class="btn-danger btn-sm" onclick="Ranked.respondToLobby(${lobby.id}, 'decline', event)">❌ Decline</button>
            </div>` : `
            <div class="pending-match-actions">
                <span class="muted">Waiting for player responses...</span>
            </div>`}
        </div>`;
    },

    _renderPendingMatch(match, focusMatchId = null) {
        const t1 = Ranked._teamNames(match.team1);
        const t2 = Ranked._teamNames(match.team2);
        const currentUser = Ranked._currentUser();
        const myMp = match.players.find(p => p.user_id === currentUser.id);
        const alreadyConfirmed = myMp && myMp.confirmed;
        const confirmedCount = Number.isFinite(match.confirmed_count)
            ? Number(match.confirmed_count)
            : match.players.filter(p => p.confirmed).length;
        const totalPlayers = Number.isFinite(match.total_players)
            ? Number(match.total_players)
            : (match.players || []).length;
        const submitterName = Ranked._e(
            match.submitted_by_user?.name || match.submitted_by_user?.username || 'a player'
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
                <button class="btn-primary btn-sm" onclick="Ranked.confirmMatch(${match.id}, event)">✅ Confirm Score</button>
                <button class="btn-danger btn-sm" onclick="Ranked.rejectMatch(${match.id}, event)">❌ Reject Score</button>
            </div>` : `
            <div class="pending-match-actions">
                <span class="muted">✅ You confirmed — waiting for others</span>
            </div>`}
        </div>`;
    },

    _renderLobbyCard(lobby, scheduled = false) {
        const t1 = Ranked._teamNames(lobby.team1);
        const t2 = Ranked._teamNames(lobby.team2);
        const myEntry = (lobby.players || []).find(p => p.user_id === Ranked._currentUser().id);
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
                    ? `<button class="btn-primary btn-sm" onclick="Ranked.startLobbyMatch(${lobby.id}, event)">▶️ Start Game</button>`
                    : '<span class="muted">Participants can start once checked in</span>'}
            </div>
        </div>`;
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

    _showCompletedResults(match) {
        const modal = document.getElementById('match-modal');
        modal.style.display = 'flex';

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
                <h2>${winnerTeam === 1 ? '🏆' : ''} Team 1: ${match.team1_score} — ${match.team2_score} :Team 2 ${winnerTeam === 2 ? '🏆' : ''}</h2>
                <p class="muted">All players confirmed — rankings updated!</p>
            </div>
            <div class="elo-results-list">
                ${resultsHTML}
            </div>
            <button class="btn-primary btn-full" onclick="document.getElementById('match-modal').style.display='none'" style="margin-top:16px">Done</button>
        </div>`;
    },
});
