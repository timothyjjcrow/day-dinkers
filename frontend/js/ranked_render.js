/**
 * Ranked rendering functions — leaderboard, court section, match cards.
 * Extends the Ranked object defined in ranked.js.
 */
Object.assign(Ranked, {

    _renderLeaderboard(players, options = {}) {
        const scopeLabel = Ranked._e(options.scopeLabel || 'Court');
        const currentUser = Ranked._currentUser();
        const currentUserId = Number(currentUser.id) || 0;
        const allPlayers = players || [];
        const featured = allPlayers.slice(0, 3);
        const remainder = allPlayers.slice(3);
        const featuredCards = featured.map(player =>
            Ranked._leaderboardCardHTML(player, {
                currentUserId,
                showChallenge: false,
                compact: true,
                featured: true,
            })
        ).join('');
        const rows = remainder.map(player =>
            Ranked._leaderboardCardHTML(player, {
                currentUserId,
                showChallenge: true,
                compact: false,
            })
        ).join('');

        return `
            <div class="leaderboard-showcase">
                <div class="leaderboard-showcase-header">
                    <span class="match-type-badge">${scopeLabel} standings</span>
                    <span class="muted">${allPlayers.length} ranked player${allPlayers.length === 1 ? '' : 's'}</span>
                </div>
                ${featuredCards ? `<div class="leaderboard-podium">${featuredCards}</div>` : ''}
                ${rows
                    ? `<div class="ranked-leaderboard-list leaderboard-ranked-list">${rows}</div>`
                    : ''}
            </div>
        `;
    },

    _leaderboardCardHTML(player, {
        currentUserId = 0,
        showChallenge = true,
        compact = false,
        featured = false,
    } = {}) {
        const p = player || {};
        const userId = Number(p.user_id) || 0;
        const isCurrentUser = !!(currentUserId && userId === currentUserId);
        const rank = Number(p.rank) || '-';
        const safeName = Ranked._e(p.name || p.username || 'Player');
        const safeUsername = Ranked._e(p.username || '');
        const elo = Math.round(Number(p.elo_rating) || 1200);
        const wins = Number(p.wins) || 0;
        const losses = Number(p.losses) || 0;
        const games = Number(p.games_played) || 0;
        const winRate = Number.isFinite(Number(p.win_rate))
            ? `${Math.round(Number(p.win_rate))}%`
            : '--';
        const eloClass = elo >= 1400 ? 'elo-high' : elo >= 1200 ? 'elo-mid' : 'elo-low';
        const rankClass = rank === 1 ? 'rank-first' : rank === 2 ? 'rank-second' : rank === 3 ? 'rank-third' : '';
        const tier = elo >= 1500
            ? 'Diamond'
            : elo >= 1400
                ? 'Platinum'
                : elo >= 1300
                    ? 'Gold'
                    : elo >= 1200
                        ? 'Silver'
                        : 'Bronze';

        const actionHTML = showChallenge
            ? (isCurrentUser
                ? '<span class="match-type-badge">You</span>'
                : `<button class="btn-secondary btn-sm" onclick="event.stopPropagation(); Ranked.openScheduledChallengeModal(${userId}, 'leaderboard_challenge')">Challenge</button>`)
            : '';

        return `
        <div class="ranked-player-card ${compact ? 'compact' : ''} ${featured ? 'featured' : ''} ${rankClass} ${isCurrentUser ? 'current-user' : ''}" onclick="Ranked.viewPlayer(${userId})">
            <div class="ranked-player-rank">#${rank}</div>
            <div class="ranked-player-main">
                <div class="ranked-player-name-row">
                    <strong>${safeName}</strong>
                    <span class="muted">@${safeUsername}</span>
                </div>
                <div class="ranked-player-metrics">
                    <span class="ranked-player-stat ${eloClass}">ELO ${elo}</span>
                    <span class="ranked-player-stat">${tier}</span>
                    <span class="ranked-player-stat">${wins}W-${losses}L</span>
                    <span class="ranked-player-stat">${winRate} win</span>
                    <span class="ranked-player-stat">${games} games</span>
                </div>
            </div>
            ${actionHTML ? `<div class="ranked-player-action">${actionHTML}</div>` : ''}
        </div>`;
    },

    _renderRankedCheckinPanel({ courtId, currentUserId, courtName, amCheckedInHere, nowSessions }) {
        if (typeof MapView !== 'undefined' && typeof MapView._checkinBarHTML === 'function') {
            return MapView._checkinBarHTML({
                courtId,
                safeCourtName: Ranked._e(courtName || 'this court'),
                amCheckedInHere,
                currentUserId,
                nowSessions: nowSessions || [],
            });
        }

        if (!amCheckedInHere) {
            return `
                <div class="checkin-status-bar">
                    <button class="btn-primary btn-full" onclick="MapView.checkIn(${courtId})">Check In</button>
                    <p class="checkin-hint">Check in to join queue and challenge players at this court.</p>
                </div>
            `;
        }

        return `
            <div class="checkin-status-bar checked-in">
                <div class="checkin-status-info">
                    <span class="checkin-dot"></span>
                    <span>You're checked in here</span>
                </div>
                <div class="checkin-actions">
                    <button class="btn-sm btn-secondary" onclick="MapView.checkOut(${courtId})">Check Out</button>
                </div>
            </div>
        `;
    },

    _renderRankedCheckedInPlayers({ courtId, currentUserId, checkedInPlayers, nowSessions, amCheckedInHere }) {
        const checkedIn = checkedInPlayers || [];
        if (!checkedIn.length) {
            return '<p class="muted">No players are checked in yet. Be the first to get games going.</p>';
        }

        const split = (typeof MapView !== 'undefined' && typeof MapView._splitPlayersByLookingToPlay === 'function')
            ? MapView._splitPlayersByLookingToPlay(checkedIn, nowSessions || [])
            : { lookingToPlayPlayers: [], otherPlayers: checkedIn };

        const renderCard = (user, isLookingToPlay) => {
            if (typeof MapView !== 'undefined' && typeof MapView._playerCard === 'function') {
                return MapView._playerCard(
                    user,
                    isLookingToPlay,
                    currentUserId,
                    courtId,
                    amCheckedInHere,
                    { enableChallenge: true },
                );
            }
            const rawName = user.name || user.username || 'Player';
            const safeName = Ranked._e(rawName);
            const safeInitial = Ranked._e((rawName[0] || '?').toUpperCase());
            return `<div class="queue-player">
                <span class="queue-avatar">${safeInitial}</span>
                <div class="queue-info"><strong>${safeName}</strong></div>
            </div>`;
        };

        const groups = [];
        if (split.lookingToPlayPlayers.length) {
            groups.push(`
                <div class="lfg-group">
                    <h5>Looking to Play Now (${split.lookingToPlayPlayers.length})</h5>
                    ${split.lookingToPlayPlayers.map(user => renderCard(user, true)).join('')}
                </div>
            `);
        }
        if (split.otherPlayers.length) {
            groups.push(`
                <div class="players-group">
                    <h5>At the Court (${split.otherPlayers.length})</h5>
                    ${split.otherPlayers.map(user => renderCard(user, false)).join('')}
                </div>
            `);
        }
        return groups.join('');
    },

    _relativeFromIso(isoString) {
        const ts = Ranked._isoToMs(isoString);
        if (!ts) return '';
        const diff = Date.now() - ts;
        if (diff < 60000) return 'just now';
        const mins = Math.floor(diff / 60000);
        if (mins < 60) return `${mins}m ago`;
        const hours = Math.floor(mins / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.floor(hours / 24);
        return `${days}d ago`;
    },

    _queueStatusText(queueCount) {
        const count = Number(queueCount) || 0;
        if (count >= 4) return 'Queue is ready for doubles. Create a game now.';
        if (count >= 2) return `Queue is ready for singles. ${Math.max(0, 4 - count)} more needed for doubles.`;
        if (count === 1) return 'One player is waiting. Add one more for singles.';
        return 'No one is in queue yet. Join to start the next game.';
    },

    _renderQueueCards(queue, currentUserId) {
        const items = queue || [];
        if (!items.length) return '<p class="muted">No queue entries yet.</p>';

        return items.map((entry, index) => {
            const user = entry.user || {};
            const isMe = Number(user.id) === Number(currentUserId);
            const name = user.name || user.username || 'Player';
            const safeName = Ranked._e(name);
            const safeInitial = Ranked._e((name[0] || '?').toUpperCase());
            const elo = Math.round(Number(user.elo_rating) || 1200);
            const matchType = entry.match_type === 'singles' ? 'Singles' : 'Doubles';
            const joinedAgo = Ranked._relativeFromIso(entry.joined_at);

            return `
                <div class="queue-entry-card ${isMe ? 'queue-entry-self' : ''}">
                    <div class="queue-entry-left">
                        <span class="queue-entry-position">${index + 1}</span>
                        <span class="queue-avatar">${safeInitial}</span>
                        <div class="queue-entry-main">
                            <div class="queue-entry-name-row">
                                <strong>${safeName}${isMe ? ' (You)' : ''}</strong>
                                ${joinedAgo ? `<span class="queue-entry-time">${Ranked._e(joinedAgo)}</span>` : ''}
                            </div>
                            <div class="queue-entry-meta">ELO ${elo}</div>
                        </div>
                    </div>
                    <span class="queue-entry-pill">${matchType}</span>
                </div>
            `;
        }).join('');
    },

    _renderActionGroup(title, cardsHtml) {
        if (!cardsHtml) return '';
        return `
            <div class="ranked-action-group">
                <h6>${Ranked._e(title)}</h6>
                <div class="ranked-action-items">${cardsHtml}</div>
            </div>
        `;
    },

    _renderCourtRanked(data, courtId) {
        const queue = data.queue || [];
        const activeMatches = data.matches || [];
        const leaderboard = data.leaderboard || [];
        const readyLobbies = data.ready_lobbies || [];
        const scheduledLobbies = data.scheduled_lobbies || [];
        const allPendingLobbies = data.pending_lobbies || [];

        const currentUser = Ranked._currentUser();
        const currentUserId = Number(currentUser.id) || 0;
        const inQueue = queue.some(q => Number(q.user_id) === currentUserId);

        const sortedQueue = queue.slice();
        const sortedInProgress = activeMatches
            .filter(match => match.status === 'in_progress')
            .slice()
            .sort((a, b) => Ranked._isoToMs(b.created_at) - Ranked._isoToMs(a.created_at));
        const sortedPendingMatches = activeMatches
            .filter(match => match.status === 'pending_confirmation')
            .slice()
            .sort((a, b) => Ranked._isoToMs(b.created_at) - Ranked._isoToMs(a.created_at));
        const sortedReadyLobbies = readyLobbies
            .slice()
            .sort((a, b) => Ranked._isoToMs(b.created_at) - Ranked._isoToMs(a.created_at));
        const sortedScheduledLobbies = scheduledLobbies
            .slice()
            .sort((a, b) => {
                const aTime = Ranked._isoToMs(a.scheduled_for) || Ranked._isoToMs(a.created_at);
                const bTime = Ranked._isoToMs(b.scheduled_for) || Ranked._isoToMs(b.created_at);
                return aTime - bTime;
            });
        const sortedPendingLobbies = allPendingLobbies
            .slice()
            .sort((a, b) => Ranked._isoToMs(b.created_at) - Ranked._isoToMs(a.created_at));

        const myPendingCourtMatches = sortedPendingMatches.filter(match => {
            const me = (match.players || []).find(player => Number(player.user_id) === currentUserId);
            return !!(me && !me.confirmed);
        });
        const awaitingOthersMatches = sortedPendingMatches.filter(match => {
            const me = (match.players || []).find(player => Number(player.user_id) === currentUserId);
            return !me || me.confirmed;
        });
        const myActionLobbies = sortedPendingLobbies.filter(lobby => {
            const me = (lobby.players || []).find(player => Number(player.user_id) === currentUserId);
            return !!(me && me.acceptance_status === 'pending');
        });
        const waitingChallengeLobbies = sortedPendingLobbies.filter(lobby => {
            const me = (lobby.players || []).find(player => Number(player.user_id) === currentUserId);
            return !!(me && me.acceptance_status !== 'pending');
        });
        const myLiveMatches = sortedInProgress.filter(match =>
            (match.players || []).some(player => Number(player.user_id) === currentUserId)
        );
        const spectatorLiveMatches = sortedInProgress.filter(match =>
            !(match.players || []).some(player => Number(player.user_id) === currentUserId)
        );
        const myReadyLobbies = sortedReadyLobbies.filter(lobby => {
            const me = (lobby.players || []).find(player => Number(player.user_id) === currentUserId);
            return !!(me && me.acceptance_status === 'accepted');
        });

        const actionGroups = [
            Ranked._renderActionGroup(
                'Enter Score',
                myLiveMatches.map(match => Ranked._renderActiveMatch(match, { actionMode: true })).join(''),
            ),
            Ranked._renderActionGroup(
                'Confirm Reported Scores',
                myPendingCourtMatches.map(match => Ranked._renderPendingCourtMatch(match)).join(''),
            ),
            Ranked._renderActionGroup(
                'Respond to Invitations',
                myActionLobbies.map(lobby => Ranked._renderPendingLobby(lobby)).join(''),
            ),
            Ranked._renderActionGroup(
                'Start Ready Games',
                myReadyLobbies.map(lobby => Ranked._renderLobbyCard(lobby, false, { actionMode: true })).join(''),
            ),
        ].filter(Boolean);
        const actionItemCount = myLiveMatches.length + myPendingCourtMatches.length + myActionLobbies.length + myReadyLobbies.length;

        const actionCenterClass = [
            'ranked-action-center',
            actionItemCount > 0 ? 'has-actions' : '',
            Ranked._isActionCenterHighlighted(courtId) ? 'action-center-highlight' : '',
        ].filter(Boolean).join(' ');

        const queueSinglesCount = sortedQueue.filter(entry => entry.match_type === 'singles').length;
        const queueDoublesCount = sortedQueue.filter(entry => entry.match_type === 'doubles').length;
        const queueSummaryText = Ranked._queueStatusText(sortedQueue.length);
        const queueCardsHTML = Ranked._renderQueueCards(sortedQueue, currentUserId);
        const queueActionControls = inQueue
            ? `<div class="queue-join-controls in-queue">
                    <span class="match-type-badge">You are in queue</span>
                    <button class="btn-danger btn-sm" onclick="Ranked.leaveQueue(${courtId})">Leave Queue</button>
               </div>`
            : `<div class="queue-join-controls">
                    <button class="btn-secondary btn-sm" onclick="Ranked.joinQueue(${courtId}, 'singles')">Join Singles</button>
                    <button class="btn-primary btn-sm" onclick="Ranked.joinQueue(${courtId}, 'doubles')">Join Doubles</button>
               </div>`;
        const createGameBtn = sortedQueue.length >= 2
            ? `<div class="create-match-actions">
                    <button class="btn-primary btn-sm" onclick="Ranked.showCreateMatch(${courtId})">Create Game from Queue</button>
               </div>`
            : '';

        const readyLobbyHTML = sortedReadyLobbies.length
            ? sortedReadyLobbies.map(lobby => Ranked._renderLobbyCard(lobby)).join('')
            : '<p class="muted">No games are ready to start yet.</p>';
        const scheduledLobbyHTML = sortedScheduledLobbies.length
            ? sortedScheduledLobbies.map(lobby => Ranked._renderLobbyCard(lobby, true)).join('')
            : '<p class="muted">No scheduled ranked games yet.</p>';
        const liveGamesHTML = spectatorLiveMatches.length
            ? spectatorLiveMatches.map(match => Ranked._renderActiveMatch(match)).join('')
            : '<p class="muted">No live games to watch right now.</p>';
        const awaitingScoreHTML = awaitingOthersMatches.length
            ? awaitingOthersMatches.map(match => Ranked._renderPendingCourtMatch(match)).join('')
            : '';
        const awaitingChallengeHTML = waitingChallengeLobbies.length
            ? waitingChallengeLobbies.map(lobby => Ranked._renderPendingLobby(lobby)).join('')
            : '';

        const courtContext = Ranked._courtContext(courtId);
        const checkinPanelHTML = Ranked._renderRankedCheckinPanel({
            courtId,
            currentUserId,
            courtName: courtContext.courtName,
            amCheckedInHere: courtContext.amCheckedInHere,
            nowSessions: courtContext.nowSessions,
        });
        const checkedInHint = courtContext.amCheckedInHere
            ? '<p class="muted">Challenge checked-in players instantly, or schedule friend challenges.</p>'
            : '<p class="muted">Check in first to challenge checked-in players. You can still add friends now.</p>';
        const checkedInPlayersHTML = Ranked._renderRankedCheckedInPlayers({
            courtId,
            currentUserId,
            checkedInPlayers: courtContext.checkedInPlayers,
            nowSessions: courtContext.nowSessions,
            amCheckedInHere: courtContext.amCheckedInHere,
        });

        const leaderboardHTML = leaderboard.length
            ? leaderboard.map(player =>
                Ranked._leaderboardCardHTML(player, {
                    currentUserId,
                    showChallenge: true,
                    compact: true,
                })
            ).join('')
            : '<p class="muted">No ranked results yet at this court.</p>';

        const actionCenterBody = actionGroups.length
            ? actionGroups.join('')
            : '<p class="muted">No actions pending. New ranked updates will appear here automatically.</p>';

        return `
        <div class="court-ranked">
            <div class="ranked-header">
                <div>
                    <h4>Competitive Play</h4>
                    <p class="muted ranked-header-copy">Live ranked updates and quick actions</p>
                </div>
                ${actionItemCount > 0 ? `<span class="match-type-badge pending-badge">${actionItemCount} pending</span>` : ''}
            </div>

            <div id="ranked-action-center" class="${actionCenterClass}">
                <div class="section-header">
                    <h5>Action Center</h5>
                    ${actionItemCount > 0 ? `<span class="player-count-badge">${actionItemCount}</span>` : ''}
                </div>
                <p class="muted">Updates are surfaced here first so you can respond quickly.</p>
                ${actionCenterBody}
            </div>

            <div class="ranked-checkin-shell">
                ${checkinPanelHTML}
            </div>

            <div class="ranked-sub-section">
                <div class="section-header">
                    <h5>Checked-In Players</h5>
                    <button class="btn-secondary btn-sm" onclick="Ranked.openCourtScheduledChallenge(${courtId})">Schedule Friend Challenge</button>
                </div>
                ${checkedInHint}
                ${checkedInPlayersHTML}
            </div>

            <div class="ranked-queue-section">
                <div class="queue-header">
                    <h5>Match Queue</h5>
                    ${queueActionControls}
                </div>
                <div class="queue-metrics">
                    <div class="queue-metric">
                        <span class="queue-metric-label">In Queue</span>
                        <strong>${sortedQueue.length}</strong>
                    </div>
                    <div class="queue-metric">
                        <span class="queue-metric-label">Singles</span>
                        <strong>${queueSinglesCount}</strong>
                    </div>
                    <div class="queue-metric">
                        <span class="queue-metric-label">Doubles</span>
                        <strong>${queueDoublesCount}</strong>
                    </div>
                </div>
                <p class="muted">${queueSummaryText}</p>
                <div class="queue-list">${queueCardsHTML}</div>
                ${createGameBtn}
            </div>

            <div class="ranked-sub-section">
                <h5>Ready Games</h5>
                ${readyLobbyHTML}
            </div>

            <div class="ranked-sub-section">
                <h5>Scheduled Games</h5>
                ${scheduledLobbyHTML}
            </div>

            <div class="ranked-sub-section">
                <h5>Live Games</h5>
                ${liveGamesHTML}
            </div>

            ${(awaitingScoreHTML || awaitingChallengeHTML) ? `
            <div class="ranked-sub-section">
                <h5>Awaiting Other Players</h5>
                ${awaitingScoreHTML}
                ${awaitingChallengeHTML}
            </div>` : ''}

            <div class="ranked-sub-section">
                <h5>Court Rankings</h5>
                <p class="muted">View player form, win rate, and total games at this court.</p>
                <div class="ranked-mini-leaderboard">${leaderboardHTML}</div>
                <button class="btn-secondary btn-sm" onclick="App.setCourtTab('leaderboard')" style="margin-top:8px">Open Full Leaderboard</button>
            </div>
        </div>`;
    },

    _renderActiveMatch(match, options = {}) {
        const t1 = Ranked._teamNames(match.team1);
        const t2 = Ranked._teamNames(match.team2);
        const currentUserId = Number(Ranked._currentUser().id) || 0;
        const isPlayer = (match.players || []).some(player => Number(player.user_id) === currentUserId);
        const started = Ranked._relativeFromIso(match.created_at);

        return `
        <div class="active-match-card ${options.actionMode ? 'ranked-action-card' : ''}">
            <div class="match-teams">
                <div class="match-team-name">${t1}</div>
                <span class="match-vs">VS</span>
                <div class="match-team-name">${t2}</div>
            </div>
            <div class="match-meta-row">
                <span class="match-type-badge">${Ranked._e(match.match_type || 'ranked')}</span>
                ${started ? `<span class="muted">${Ranked._e(started)}</span>` : ''}
                ${isPlayer ? `
                    <button class="btn-primary btn-sm" onclick="Ranked.showScoreModal(${match.id})">Enter Score</button>
                    <button class="btn-outline btn-sm" onclick="Ranked.cancelMatch(${match.id}, event)">Cancel</button>
                ` : '<span class="muted">In progress</span>'}
            </div>
        </div>`;
    },

    _renderPendingCourtMatch(match) {
        const t1 = Ranked._teamNames(match.team1);
        const t2 = Ranked._teamNames(match.team2);
        const currentUser = Ranked._currentUser();
        const myEntry = (match.players || []).find(player => Number(player.user_id) === Number(currentUser.id));
        const alreadyConfirmed = !!(myEntry && myEntry.confirmed);
        const confirmedCount = Number.isFinite(match.confirmed_count)
            ? Number(match.confirmed_count)
            : (match.players || []).filter(player => player.confirmed).length;
        const totalPlayers = Number.isFinite(match.total_players)
            ? Number(match.total_players)
            : (match.players || []).length;
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
                <span class="muted">Reported by ${submitterName} at ${courtName}</span>
            </div>
            <div class="match-meta-row">
                <span class="match-type-badge pending-badge">${confirmedCount}/${totalPlayers} confirmed</span>
                ${myEntry && !alreadyConfirmed ? `
                    <div class="confirm-inline-actions">
                        <button class="btn-primary btn-sm" onclick="Ranked.confirmMatch(${match.id}, event)">Confirm</button>
                        <button class="btn-danger btn-sm" onclick="Ranked.rejectMatch(${match.id}, event)">Reject</button>
                    </div>
                ` : myEntry && alreadyConfirmed ? `
                    <span class="muted">Confirmed. Waiting for others.</span>
                ` : `
                    <span class="muted">Waiting for player confirmations.</span>
                `}
            </div>
        </div>`;
    },

    _renderPendingLobby(lobby) {
        const t1 = Ranked._teamNames(lobby.team1);
        const t2 = Ranked._teamNames(lobby.team2);
        const currentUser = Ranked._currentUser();
        const myEntry = (lobby.players || []).find(player => Number(player.user_id) === Number(currentUser.id));
        const courtName = Ranked._e(lobby.court?.name || 'Court');
        const sourceLabel = lobby.source === 'scheduled_challenge' || lobby.source === 'friends_challenge'
            ? 'Scheduled challenge'
            : 'Ranked challenge';
        const accepts = (lobby.players || []).map(player => {
            const label = Ranked._e(player.user?.name || player.user?.username || '?');
            const accepted = player.acceptance_status === 'accepted';
            return `<span class="confirm-player ${accepted ? 'confirmed' : 'pending'}">
                ${label} · ${accepted ? 'Accepted' : 'Pending'}
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
                <button class="btn-primary btn-sm" onclick="Ranked.respondToLobby(${lobby.id}, 'accept', event)">Accept</button>
                <button class="btn-danger btn-sm" onclick="Ranked.respondToLobby(${lobby.id}, 'decline', event)">Decline</button>
            </div>` : `
            <div class="pending-match-actions">
                <span class="muted">Waiting for remaining responses.</span>
            </div>`}
        </div>`;
    },

    _renderPendingMatch(match, focusMatchId = null) {
        const t1 = Ranked._teamNames(match.team1);
        const t2 = Ranked._teamNames(match.team2);
        const currentUser = Ranked._currentUser();
        const myEntry = (match.players || []).find(player => Number(player.user_id) === Number(currentUser.id));
        const alreadyConfirmed = !!(myEntry && myEntry.confirmed);
        const confirmedCount = Number.isFinite(match.confirmed_count)
            ? Number(match.confirmed_count)
            : (match.players || []).filter(player => player.confirmed).length;
        const totalPlayers = Number.isFinite(match.total_players)
            ? Number(match.total_players)
            : (match.players || []).length;
        const submitterName = Ranked._e(
            match.submitted_by_user?.name || match.submitted_by_user?.username || 'a player'
        );
        const courtName = Ranked._e(match.court?.name || 'Court');

        const confirmStatus = (match.players || []).map(player => {
            const name = Ranked._e(player.user?.name || player.user?.username || '?');
            return `<span class="confirm-player ${player.confirmed ? 'confirmed' : 'pending'}">
                ${name} · ${player.confirmed ? 'Confirmed' : 'Pending'}
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
                <span class="confirm-progress">Reported by ${submitterName} at ${courtName}</span>
            </div>
            <div class="pending-match-confirmations">
                <span class="confirm-progress">${confirmedCount}/${totalPlayers} confirmed</span>
                <div class="confirm-players-list">${confirmStatus}</div>
            </div>
            ${!alreadyConfirmed ? `
            <div class="pending-match-actions">
                <button class="btn-primary btn-sm" onclick="Ranked.confirmMatch(${match.id}, event)">Confirm Score</button>
                <button class="btn-danger btn-sm" onclick="Ranked.rejectMatch(${match.id}, event)">Reject Score</button>
            </div>` : `
            <div class="pending-match-actions">
                <span class="muted">Confirmed. Waiting for other players.</span>
            </div>`}
        </div>`;
    },

    _renderLobbyCard(lobby, scheduled = false, options = {}) {
        const t1 = Ranked._teamNames(lobby.team1);
        const t2 = Ranked._teamNames(lobby.team2);
        const currentUserId = Number(Ranked._currentUser().id) || 0;
        const myEntry = (lobby.players || []).find(player => Number(player.user_id) === currentUserId);
        const canStart = !!(myEntry && myEntry.acceptance_status === 'accepted');
        const scheduledTime = lobby.scheduled_for
            ? new Date(lobby.scheduled_for).toLocaleString()
            : null;

        return `
        <div class="active-match-card ${scheduled ? 'pending-confirmation-card scheduled-card' : ''} ${options.actionMode ? 'ranked-action-card' : ''}">
            <div class="match-teams">
                <div class="match-team-name">${t1}</div>
                <span class="match-vs">VS</span>
                <div class="match-team-name">${t2}</div>
            </div>
            <div class="match-meta-row">
                <span class="match-type-badge">
                    ${Ranked._e(lobby.match_type || 'ranked')}
                    ${scheduledTime ? ` · ${Ranked._e(scheduledTime)}` : ''}
                </span>
                ${canStart
                    ? `<button class="btn-primary btn-sm" onclick="Ranked.startLobbyMatch(${lobby.id}, event)">Start Game</button>`
                    : '<span class="muted">Participants can start once checked in.</span>'}
            </div>
        </div>`;
    },

    _formatRecentMatchDate(isoString) {
        const ts = Ranked._isoToMs(isoString);
        if (!ts) return 'Unknown time';
        return new Date(ts).toLocaleString('en-US', {
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
        });
    },

    _isCurrentUserInMatch(match, currentUserId) {
        if (!currentUserId) return false;
        const players = match?.players || [];
        return players.some(player => Number(player.user_id) === Number(currentUserId));
    },

    _playerProfileChip(player, winnerTeam) {
        const userId = Number(player?.user_id) || 0;
        const safeName = Ranked._e(player?.user?.name || player?.user?.username || 'Player');
        const isWinner = Number(player?.team) === Number(winnerTeam);
        if (!userId) {
            return `<span class="history-player-chip ${isWinner ? 'winner' : ''}">${safeName}</span>`;
        }
        return `<button type="button" class="history-player-chip ${isWinner ? 'winner' : ''}" onclick="event.stopPropagation(); Ranked.viewPlayer(${userId})">${safeName}</button>`;
    },

    _teamPlayerChips(team, winnerTeam) {
        return (team || []).map(player => Ranked._playerProfileChip(player, winnerTeam)).join('');
    },

    _renderRecentMatchCard(match, { currentUserId = 0 } = {}) {
        const winnerTeam = Number(match?.winner_team) || 0;
        const isMine = Ranked._isCurrentUserInMatch(match, currentUserId);
        const t1Players = Ranked._teamPlayerChips(match?.team1 || [], winnerTeam);
        const t2Players = Ranked._teamPlayerChips(match?.team2 || [], winnerTeam);
        const t1Score = Number.isFinite(Number(match?.team1_score)) ? Number(match.team1_score) : '-';
        const t2Score = Number.isFinite(Number(match?.team2_score)) ? Number(match.team2_score) : '-';
        const dateLabel = Ranked._formatRecentMatchDate(match?.completed_at || match?.created_at);
        const rawType = String(match?.match_type || 'ranked');
        const matchTypeLabel = Ranked._e(rawType.charAt(0).toUpperCase() + rawType.slice(1));

        return `
            <article class="recent-game-card ${isMine ? 'is-mine' : ''}">
                <div class="recent-game-head">
                    <span class="recent-game-date">${Ranked._e(dateLabel)}</span>
                    <div class="recent-game-head-right">
                        <span class="match-type-badge">${matchTypeLabel}</span>
                        ${isMine ? '<span class="match-type-badge pending-badge">You played</span>' : ''}
                    </div>
                </div>
                <div class="recent-game-scoreboard">
                    <div class="recent-game-team ${winnerTeam === 1 ? 'winner' : ''}">
                        <div class="recent-game-team-label">Team 1</div>
                        <div class="recent-game-team-players">${t1Players || '<span class="muted">No players</span>'}</div>
                    </div>
                    <div class="recent-game-score">
                        <strong>${t1Score}</strong>
                        <span class="recent-game-score-sep">-</span>
                        <strong>${t2Score}</strong>
                    </div>
                    <div class="recent-game-team ${winnerTeam === 2 ? 'winner' : ''}">
                        <div class="recent-game-team-label">Team 2</div>
                        <div class="recent-game-team-players">${t2Players || '<span class="muted">No players</span>'}</div>
                    </div>
                </div>
            </article>
        `;
    },

    _renderRecentGames(matches, { courtId, filter = 'all', currentUserId = 0 } = {}) {
        const allMatches = Array.isArray(matches) ? matches : [];
        const myMatches = currentUserId
            ? allMatches.filter(match => Ranked._isCurrentUserInMatch(match, currentUserId))
            : [];
        const effectiveFilter = (filter === 'mine' && currentUserId) ? 'mine' : 'all';
        const visibleMatches = effectiveFilter === 'mine' ? myMatches : allMatches;
        const allActive = effectiveFilter === 'all';
        const mineActive = effectiveFilter === 'mine';
        const mineDisabled = !currentUserId;
        const emptyMessage = effectiveFilter === 'mine'
            ? 'You have not played a completed ranked game at this court yet.'
            : 'No completed ranked games at this court yet.';

        const cards = visibleMatches.length
            ? visibleMatches.map(match =>
                Ranked._renderRecentMatchCard(match, { currentUserId })
            ).join('')
            : `<p class="muted">${emptyMessage}</p>`;

        return `
            <div class="recent-games-panel">
                <div class="recent-games-toolbar">
                    <div class="recent-games-filters" role="group" aria-label="Recent games filter">
                        <button type="button" class="btn-secondary btn-sm ${allActive ? 'active' : ''}" onclick="Ranked.setRecentGamesFilter(${Number(courtId) || 0}, 'all')">
                            All Court Games (${allMatches.length})
                        </button>
                        <button type="button" class="btn-secondary btn-sm ${mineActive ? 'active' : ''}" ${mineDisabled ? 'disabled' : ''} onclick="Ranked.setRecentGamesFilter(${Number(courtId) || 0}, 'mine')">
                            My Games (${myMatches.length})
                        </button>
                    </div>
                    <span class="muted">Tap a player name to open their profile.</span>
                </div>
                <div class="recent-games-list">${cards}</div>
            </div>
        `;
    },

    _renderMatchHistory(match) {
        return Ranked._renderRecentMatchCard(match, {
            currentUserId: Number(Ranked._currentUser().id) || 0,
        });
    },

    _showCompletedResults(match) {
        const modal = document.getElementById('match-modal');
        modal.style.display = 'flex';

        const winnerTeam = Number(match.winner_team) || 1;
        const winnerLabel = winnerTeam === 1 ? 'Team 1' : 'Team 2';
        const resultsHTML = (match.players || []).map(player => {
            const won = Number(player.team) === winnerTeam;
            const change = Number(player.elo_change || 0);
            const sign = change >= 0 ? '+' : '';
            const safeName = Ranked._e(player.user?.name || player.user?.username || 'Player');
            return `
            <div class="elo-result-row ${won ? 'elo-winner' : ''}">
                <span class="elo-result-name">${won ? 'Winner: ' : ''}${safeName}</span>
                <span class="elo-result-team">Team ${player.team}</span>
                <span class="elo-result-rating">${Math.round(player.elo_before || 0)} to ${Math.round(player.elo_after || 0)}</span>
                <span class="elo-result-change ${change >= 0 ? 'elo-gain' : 'elo-loss'}">${sign}${Math.round(change)}</span>
            </div>`;
        }).join('');

        modal.innerHTML = `
        <div class="modal-content elo-results-modal">
            <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
            <div class="elo-results-header">
                <h2>${winnerLabel} wins ${match.team1_score}-${match.team2_score}</h2>
                <p class="muted">All players confirmed. Rankings have been updated.</p>
            </div>
            <div class="elo-results-list">
                ${resultsHTML}
            </div>
            <button class="btn-primary btn-full" onclick="document.getElementById('match-modal').style.display='none'" style="margin-top:16px">Done</button>
        </div>`;
    },
});
