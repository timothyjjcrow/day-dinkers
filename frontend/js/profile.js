/**
 * Profile page — full-page view with stats, friends, and edit functionality.
 */
const Profile = {
    _searchTimer: null,
    _searchRequestSeq: 0,
    _activeSearchQuery: '',

    async load() {
        const container = document.getElementById('profile-page-content');
        const token = localStorage.getItem('token');
        if (!token) {
            container.innerHTML = `
                <div class="profile-empty">
                    <h2>Sign in to view your profile</h2>
                    <p>Create an account to track your games, connect with players, and more.</p>
                    <button class="btn-primary" onclick="Auth.showModal()">Sign In / Sign Up</button>
                </div>`;
            return;
        }

        container.innerHTML = '<div class="loading">Loading profile...</div>';

        try {
            const res = await API.get('/api/auth/profile');
            const user = res.user;
            container.innerHTML = Profile._renderProfile(user);
        } catch (err) {
            container.innerHTML = '<p class="error">Failed to load profile</p>';
        }
    },

    _renderProfile(user) {
        const skillLabel = Profile._skillLabel(user.skill_level);
        const initialsRaw = (user.name || user.username || '?').split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2);
        const initials = Profile._e(initialsRaw);
        const displayName = Profile._e(user.name || user.username || 'Player');
        const username = Profile._e(user.username || '');
        const bio = Profile._e(user.bio || '');
        const playStyle = Profile._e(user.play_style || '');
        const preferredTimes = Profile._e(user.preferred_times || '');
        const photoUrl = Profile._safeUrl(user.photo_url);
        const winRate = user.games_played > 0 ? Math.round((user.wins / user.games_played) * 100) : 0;
        const elo = Math.round(user.elo_rating || 1200);
        const eloTier = Profile._eloTier(elo);

        return `
        <div class="profile-page">
            <div class="profile-header-card">
                <div class="profile-avatar-large">${photoUrl ? `<img src="${photoUrl}" alt="avatar">` : `<span>${initials}</span>`}</div>
                <div class="profile-header-info">
                    <h1>${displayName}</h1>
                    <p class="profile-username">@${username}</p>
                    ${user.bio ? `<p class="profile-bio">${bio}</p>` : ''}
                    <div class="profile-tags">
                        <span class="tag tag-elo ${eloTier.class}">${eloTier.icon} ${elo} ELO — ${eloTier.name}</span>
                        ${skillLabel ? `<span class="tag tag-skill">${Profile._e(skillLabel)}</span>` : ''}
                        ${user.play_style ? `<span class="tag tag-style">${playStyle}</span>` : ''}
                        ${user.preferred_times ? `<span class="tag tag-time">${preferredTimes}</span>` : ''}
                    </div>
                </div>
                <button class="btn-secondary" onclick="Profile.showEdit()">Edit Profile</button>
            </div>

            <div class="profile-stats-grid">
                <div class="stat-card stat-elo">
                    <div class="stat-number">${elo}</div>
                    <div class="stat-label">${eloTier.icon} ELO Rating</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">${user.games_played || 0}</div>
                    <div class="stat-label">Games Played</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">${user.wins || 0}</div>
                    <div class="stat-label">Wins</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">${user.losses || 0}</div>
                    <div class="stat-label">Losses</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">${winRate}%</div>
                    <div class="stat-label">Win Rate</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">${user.friends_count || 0}</div>
                    <div class="stat-label">Friends</div>
                </div>
            </div>

            <div class="profile-sections">
                <div class="profile-section">
                    <h3>⚔️ Match History</h3>
                    <div id="profile-match-history">Loading...</div>
                    <button class="btn-secondary btn-sm" onclick="App.setMainTab('map')" style="margin-top:8px">Browse Courts</button>
                </div>
                <div class="profile-section">
                    <h3>🏆 Tournament Results</h3>
                    <div id="profile-tournament-results">Loading...</div>
                </div>
                <div class="profile-section">
                    <h3>🟢 My Sessions</h3>
                    <div id="profile-upcoming-sessions">Loading...</div>
                </div>
                <div class="profile-section">
                    <h3>Messages</h3>
                    <div id="profile-message-threads">Loading...</div>
                </div>
                <div class="profile-section">
                    <h3>Friends</h3>
                    <div id="profile-friends-list">Loading...</div>
                    <div class="friend-search">
                        <input type="text" id="friend-search-input" placeholder="Search players by name or username..." oninput="Profile.queueSearchUsers(this.value)">
                        <div id="friend-search-results"></div>
                    </div>
                </div>
                <div class="profile-section">
                    <h3>Pending Friend Requests</h3>
                    <div id="profile-pending-requests">Loading...</div>
                </div>
                <div class="profile-section" id="profile-court-review-section" style="display:none">
                    <h3>🧾 Court Update Review Queue</h3>
                    <button class="btn-secondary btn-sm" onclick="App.showScreen('admin')" style="margin-bottom:8px">Open Admin Console</button>
                    <p id="profile-review-auto-apply-note" class="muted"></p>
                    <div class="form-row">
                        <div class="form-group">
                            <label>Status</label>
                            <select id="profile-review-status-filter" onchange="CourtUpdates.refreshReviewQueue()">
                                <option value="pending">Pending</option>
                                <option value="approved">Approved</option>
                                <option value="rejected">Rejected</option>
                                <option value="all">All</option>
                            </select>
                        </div>
                        <div class="form-group" style="display:flex;align-items:flex-end">
                            <button class="btn-secondary btn-sm" onclick="CourtUpdates.refreshReviewQueue()">Refresh Queue</button>
                        </div>
                    </div>
                    <div id="profile-court-review-list">Loading...</div>
                </div>
            </div>

            <div class="profile-actions">
                <button class="btn-danger" onclick="Auth.logout()">Sign Out</button>
            </div>
        </div>

        <!-- Edit Profile Modal -->
        <div id="edit-profile-modal" class="modal" style="display:none" onclick="if (event.target === this) Profile.hideEdit()">
            <div class="modal-content profile-edit-modal">
                <button class="modal-close" onclick="Profile.hideEdit()">&times;</button>
                <h2>Edit Profile</h2>
                <p class="muted profile-edit-intro">Keep your profile current so invites, messages, and ranked matches feel more personal.</p>
                <form class="profile-edit-form" onsubmit="Profile.save(event)">
                    <div class="form-group"><label>Display Name</label><input type="text" name="name" value="${Profile._ea(user.name || '')}"></div>
                    <div class="form-group"><label>Bio</label><textarea name="bio" rows="3">${Profile._e(user.bio || '')}</textarea></div>
                    <div class="form-row">
                        <div class="form-group"><label>Skill Level</label>
                            <select name="skill_level">
                                <option value="">Not set</option>
                                <option value="2.0" ${user.skill_level == 2.0 ? 'selected' : ''}>2.0 - Beginner</option>
                                <option value="2.5" ${user.skill_level == 2.5 ? 'selected' : ''}>2.5 - Beginner+</option>
                                <option value="3.0" ${user.skill_level == 3.0 ? 'selected' : ''}>3.0 - Intermediate</option>
                                <option value="3.5" ${user.skill_level == 3.5 ? 'selected' : ''}>3.5 - Intermediate+</option>
                                <option value="4.0" ${user.skill_level == 4.0 ? 'selected' : ''}>4.0 - Advanced</option>
                                <option value="4.5" ${user.skill_level == 4.5 ? 'selected' : ''}>4.5 - Advanced+</option>
                                <option value="5.0" ${user.skill_level == 5.0 ? 'selected' : ''}>5.0 - Pro</option>
                            </select>
                        </div>
                        <div class="form-group"><label>Play Style</label>
                            <select name="play_style">
                                <option value="">Any</option>
                                <option value="doubles" ${user.play_style === 'doubles' ? 'selected' : ''}>Doubles</option>
                                <option value="singles" ${user.play_style === 'singles' ? 'selected' : ''}>Singles</option>
                                <option value="mixed" ${user.play_style === 'mixed' ? 'selected' : ''}>Mixed Doubles</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-group"><label>Preferred Times</label>
                        <select name="preferred_times">
                            <option value="">Flexible</option>
                            <option value="morning" ${user.preferred_times === 'morning' ? 'selected' : ''}>Morning</option>
                            <option value="afternoon" ${user.preferred_times === 'afternoon' ? 'selected' : ''}>Afternoon</option>
                            <option value="evening" ${user.preferred_times === 'evening' ? 'selected' : ''}>Evening</option>
                        </select>
                    </div>
                    <div class="profile-edit-actions">
                        <button type="button" class="btn-secondary" onclick="Profile.hideEdit()">Cancel</button>
                        <button type="submit" class="btn-primary">Save Changes</button>
                    </div>
                </form>
            </div>
        </div>`;
    },

    _skillLabel(level) {
        if (!level) return '';
        if (level <= 2.5) return `⭐ ${level} Beginner`;
        if (level <= 3.5) return `⭐⭐ ${level} Intermediate`;
        return `⭐⭐⭐ ${level} Advanced`;
    },

    _eloTier(elo) {
        if (typeof App !== 'undefined' && typeof App.getEloTier === 'function') {
            return App.getEloTier(elo);
        }
        if (elo >= 1500) return { name: 'Diamond', icon: '💎', class: 'elo-diamond' };
        if (elo >= 1400) return { name: 'Platinum', icon: '🏆', class: 'elo-platinum' };
        if (elo >= 1300) return { name: 'Gold', icon: '🥇', class: 'elo-gold' };
        if (elo >= 1200) return { name: 'Silver', icon: '🥈', class: 'elo-silver' };
        return { name: 'Bronze', icon: '🥉', class: 'elo-bronze' };
    },

    async loadExtras() {
        Profile._loadFriends();
        Profile._loadMessageThreads();
        Profile._loadPendingRequests();
        Profile._loadUpcomingSessions();
        Profile._loadMatchHistory();
        Profile._loadTournamentResults();
        CourtUpdates.loadReviewerPanel();
    },

    async _loadTournamentResults() {
        const el = document.getElementById('profile-tournament-results');
        if (!el) return;
        const user = JSON.parse(localStorage.getItem('user') || '{}');
        if (!user.id) {
            el.innerHTML = '<p class="muted">Sign in to view tournament history.</p>';
            return;
        }
        try {
            const res = await API.get(`/api/ranked/tournaments/results?user_id=${user.id}&limit=10`);
            const results = res.results || [];
            if (!results.length) {
                el.innerHTML = '<p class="muted">No tournament results yet. Join a tournament from a court ranked tab.</p>';
                return;
            }
            el.innerHTML = results.map(result => {
                const tournamentName = Profile._e(result.tournament?.name || 'Tournament');
                const courtName = Profile._e(result.court?.name || 'Court');
                const placement = Number(result.placement) || '-';
                const points = Number(result.points) || 0;
                const wins = Number(result.wins) || 0;
                const losses = Number(result.losses) || 0;
                const date = result.created_at
                    ? new Date(result.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                    : '';
                const tournamentId = Number(result.tournament_id) || 0;
                const courtId = Number(result.court_id) || 0;
                return `
                    <div class="match-history-mini" onclick="${tournamentId && courtId
                        ? `Ranked.openTournamentFromSchedule(${courtId}, ${tournamentId})`
                        : ''}">
                        <span class="match-result-icon">#${placement}</span>
                        <div class="match-mini-info">
                            <span>${tournamentName}</span>
                            <span class="muted">${courtName} · ${wins}W-${losses}L · ${date}</span>
                        </div>
                        <span class="match-elo-change elo-gain">${points} pts</span>
                    </div>
                `;
            }).join('');
        } catch {
            el.innerHTML = '<p class="muted">Unable to load tournament results.</p>';
        }
    },

    async _loadMatchHistory() {
        const el = document.getElementById('profile-match-history');
        if (!el) return;
        const user = JSON.parse(localStorage.getItem('user') || '{}');
        if (!user.id) { el.innerHTML = '<p class="muted">Sign in to see match history</p>'; return; }
        try {
            const res = await API.get(`/api/ranked/history?user_id=${user.id}&limit=5`);
            const matches = res.matches || [];
            if (!matches.length) {
                el.innerHTML = '<p class="muted">No ranked matches yet. Play competitive games to build your record!</p>';
                return;
            }
            const canUseLeaderboardHistoryCards = typeof Ranked !== 'undefined'
                && typeof Ranked._renderMatchHistory === 'function';

            if (canUseLeaderboardHistoryCards) {
                const cardsHtml = matches.map(match => Ranked._renderMatchHistory(match)).join('');
                el.innerHTML = `<div class="recent-games-list">${cardsHtml}</div>`;
                return;
            }

            el.innerHTML = matches.map(m => {
                const t1 = Profile._e(m.team1.map(p => p.user?.name || p.user?.username).join(' & '));
                const t2 = Profile._e(m.team2.map(p => p.user?.name || p.user?.username).join(' & '));
                const myEntry = m.players.find(p => p.user_id === user.id);
                const won = myEntry && myEntry.team === m.winner_team;
                const change = myEntry?.elo_change || 0;
                const sign = change >= 0 ? '+' : '';
                const date = new Date(m.completed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                return `
                <div class="match-history-mini ${won ? 'match-win' : 'match-loss'}">
                    <span class="match-result-icon">${won ? '🏆' : '❌'}</span>
                    <div class="match-mini-info">
                        <span>${t1} vs ${t2}</span>
                        <span class="muted">${m.team1_score}-${m.team2_score} · ${date}</span>
                    </div>
                    <span class="match-elo-change ${change >= 0 ? 'elo-gain' : 'elo-loss'}">${sign}${change}</span>
                </div>`;
            }).join('');
        } catch { el.innerHTML = '<p class="muted">Unable to load match history</p>'; }
    },

    async _loadFriends() {
        const el = document.getElementById('profile-friends-list');
        if (!el) return;
        try {
            const [friendsRes, presenceRes] = await Promise.all([
                API.get('/api/auth/friends'),
                API.get('/api/presence/friends').catch(() => ({ friends_presence: [] })),
            ]);
            const friends = friendsRes.friends || [];
            const presenceRows = presenceRes.friends_presence || [];
            const presenceByUserId = new Map(
                presenceRows.map(row => [Number(row.user?.id || row.user_id), row])
            );
            if (!friends.length) {
                el.innerHTML = '<p class="muted">No friends yet. Search for players to connect!</p>';
                return;
            }
            el.innerHTML = friends.map(f => {
                const presence = presenceByUserId.get(Number(f.id));
                const presenceLabel = presence
                    ? '<span class="friend-presence-chip">Checked in now</span>'
                    : '';
                return `
                <div class="friend-card">
                    <div class="friend-card-main">
                        <div class="friend-avatar">${Profile._e((f.name || f.username)[0].toUpperCase())}</div>
                        <div class="friend-info">
                            <strong>${Profile._e(f.name || f.username)}</strong>
                            <span class="muted">@${Profile._e(f.username)}</span>
                            ${presenceLabel}
                        </div>
                        ${f.skill_level ? `<span class="tag tag-skill">${f.skill_level}</span>` : ''}
                    </div>
                    <div class="friend-card-actions">
                        <button class="btn-secondary btn-sm" onclick="Ranked.viewPlayer(${f.id})">Profile</button>
                        <button class="btn-secondary btn-sm" onclick="Chat.openDirectByUser(${f.id})">Message</button>
                        <button class="btn-secondary btn-sm" onclick="Ranked.openScheduledChallengeModal(${f.id}, 'friends_challenge')">⚔️ Ranked</button>
                    </div>
                </div>
            `;
            }).join('');
        } catch { el.innerHTML = '<p class="muted">Sign in to see friends</p>'; }
    },

    async _loadMessageThreads() {
        const el = document.getElementById('profile-message-threads');
        if (!el) return;
        try {
            const res = await API.get('/api/chat/direct/threads?limit=8');
            const threads = res.threads || [];
            if (!threads.length) {
                el.innerHTML = '<p class="muted">No direct conversations yet. Start one from a friend card or player profile.</p>';
                return;
            }
            el.innerHTML = threads.map(thread => {
                const user = thread.user || {};
                const safeName = Profile._e(user.name || user.username || 'Friend');
                const preview = Profile._e(thread.last_message_preview || 'Open conversation');
                const timeLabel = Profile._relativeTime(thread.last_message_at);
                return `
                    <button class="dm-thread-card" onclick="Chat.openDirectByUser(${Number(user.id) || 0})">
                        <span class="friend-avatar">${Profile._e((safeName[0] || '?').toUpperCase())}</span>
                        <span class="dm-thread-main">
                            <strong>${safeName}</strong>
                            <span class="muted">${preview}</span>
                        </span>
                        <span class="dm-thread-time">${Profile._e(timeLabel)}</span>
                    </button>
                `;
            }).join('');
        } catch {
            el.innerHTML = '<p class="muted">Unable to load conversations.</p>';
        }
    },

    async _loadPendingRequests() {
        const el = document.getElementById('profile-pending-requests');
        if (!el) return;
        try {
            const res = await API.get('/api/auth/friends/pending');
            if (!res.requests.length) {
                el.innerHTML = '<p class="muted">No pending requests</p>';
                return;
            }
            el.innerHTML = res.requests.map(r => `
                <div class="friend-request-card">
                    <div class="friend-info">
                        <strong>${Profile._e(r.user.name || r.user.username)}</strong> wants to be your friend
                    </div>
                    <div class="friend-actions">
                        <button class="btn-primary btn-sm" onclick="Profile.respondRequest(${r.id}, 'accept')">Accept</button>
                        <button class="btn-secondary btn-sm" onclick="Profile.respondRequest(${r.id}, 'decline')">Decline</button>
                    </div>
                </div>
            `).join('');
        } catch { el.innerHTML = '<p class="muted">Unable to load</p>'; }
    },

    async _loadUpcomingSessions() {
        const el = document.getElementById('profile-upcoming-sessions');
        if (!el) return;
        try {
            const res = await API.get('/api/sessions/my');
            const sessions = (res.sessions || []).slice(0, 5);
            if (!sessions.length) {
                el.innerHTML = `
                    <div class="profile-section-empty">
                        <p>No active or upcoming sessions yet. Browse courts to find a game nearby or schedule one with your preferred time and format.</p>
                        <div class="profile-section-actions">
                            <button class="btn-secondary btn-sm" onclick="App.setMainTab('map')">Browse Courts</button>
                            <button class="btn-primary btn-sm" onclick="Sessions.showCreateModal()">Schedule Open to Play</button>
                        </div>
                    </div>
                `;
                return;
            }
            el.innerHTML = sessions.map(s => {
                const isNow = s.session_type === 'now';
                const startDt = s.start_time ? new Date(s.start_time) : null;
                const hasStart = startDt && !Number.isNaN(startDt.getTime());
                const dateStr = hasStart
                    ? startDt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
                    : '';
                const timeStr = hasStart
                    ? startDt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
                    : '';
                const courtName = Profile._e(s.court?.name || '');
                const joinedCount = (s.players || []).filter(p => p.status === 'joined').length + 1; // +1 creator
                const heading = isNow ? 'Open to Play Session' : 'Scheduled Session';
                const gameType = Profile._e((s.game_type || 'open').replace(/_/g, ' '));
                return `
                <div class="session-card-mini ${isNow ? 'session-mini-active' : ''}" onclick="Sessions.openDetail(${s.id})">
                    <div class="session-mini-header">
                        <strong>${Profile._e(heading)}</strong>
                        ${isNow ? '<span class="live-badge-sm">🟢 LIVE</span>' : ''}
                    </div>
                    <span class="muted">${courtName} · ${isNow ? 'Active Now' : (hasStart ? `${dateStr} at ${timeStr}` : 'Scheduled')}</span>
                    <span class="muted">${gameType} · 👥 ${joinedCount}/${s.max_players}</span>
                </div>`;
            }).join('');
        } catch { el.innerHTML = '<p class="muted">Unable to load sessions</p>'; }
    },

    queueSearchUsers(query) {
        const el = document.getElementById('friend-search-results');
        if (!el) return;
        const normalized = String(query || '');
        Profile._activeSearchQuery = normalized;
        if (Profile._searchTimer) {
            window.clearTimeout(Profile._searchTimer);
            Profile._searchTimer = null;
        }
        if (normalized.trim().length < 2) {
            el.innerHTML = '';
            return;
        }
        el.innerHTML = '<p class="search-result-helper">Searching players...</p>';
        Profile._searchTimer = window.setTimeout(() => {
            Profile.searchUsers(normalized);
        }, 180);
    },

    refreshSearchUsers() {
        const input = document.getElementById('friend-search-input');
        const query = (input && typeof input.value === 'string') ? input.value : Profile._activeSearchQuery;
        if (!query || query.trim().length < 2) return;
        Profile.searchUsers(query);
    },

    _searchStatusMeta(user) {
        const state = String(user?.connection_state || 'none');
        if (state === 'friend') {
            return { label: 'Friend', className: 'search-result-chip search-result-chip-friend' };
        }
        if (state === 'pending_incoming') {
            return { label: 'Incoming request', className: 'search-result-chip search-result-chip-pending' };
        }
        if (state === 'pending_outgoing') {
            return { label: 'Request sent', className: 'search-result-chip search-result-chip-muted' };
        }
        return null;
    },

    _searchActionsHTML(user) {
        const userId = Number(user?.id) || 0;
        const friendshipId = Number(user?.friendship_id) || 0;
        const state = String(user?.connection_state || 'none');
        if (state === 'friend') {
            return `
                <button class="btn-secondary btn-sm" onclick="Ranked.viewPlayer(${userId})">Profile</button>
                <button class="btn-secondary btn-sm" onclick="Chat.openDirectByUser(${userId})">Message</button>
            `;
        }
        if (state === 'pending_incoming' && friendshipId) {
            return `
                <button class="btn-secondary btn-sm" onclick="Ranked.viewPlayer(${userId})">Profile</button>
                <button class="btn-primary btn-sm" onclick="Profile.respondRequest(${friendshipId}, 'accept')">Accept</button>
                <button class="btn-secondary btn-sm" onclick="Profile.respondRequest(${friendshipId}, 'decline')">Decline</button>
            `;
        }
        if (state === 'pending_outgoing') {
            return `
                <button class="btn-secondary btn-sm" onclick="Ranked.viewPlayer(${userId})">Profile</button>
                <button class="btn-primary btn-sm" disabled>Request Sent</button>
            `;
        }
        return `
            <button class="btn-secondary btn-sm" onclick="Ranked.viewPlayer(${userId})">Profile</button>
            <button class="btn-primary btn-sm" onclick="Profile.addFriend(${userId})">Add Friend</button>
        `;
    },

    async searchUsers(query) {
        const el = document.getElementById('friend-search-results');
        if (!el) return;
        const normalized = String(query || '').trim();
        Profile._activeSearchQuery = normalized;
        if (normalized.length < 2) {
            el.innerHTML = '';
            return;
        }
        const requestSeq = ++Profile._searchRequestSeq;
        try {
            const res = await API.get(`/api/auth/users/search?q=${encodeURIComponent(normalized)}`);
            if (requestSeq !== Profile._searchRequestSeq) return;
            el.innerHTML = res.users.map(u => {
                const statusMeta = Profile._searchStatusMeta(u);
                return `
                <div class="search-result-card">
                    <div class="search-result-main">
                        <strong>${Profile._e(u.name || u.username)}</strong>
                        <span class="muted">@${Profile._e(u.username)}</span>
                        ${statusMeta ? `<span class="${statusMeta.className}">${statusMeta.label}</span>` : ''}
                    </div>
                    <div class="friend-actions">
                        ${Profile._searchActionsHTML(u)}
                    </div>
                </div>
            `;
            }).join('') || '<p class="search-result-helper">No players found.</p>';
        } catch {
            if (requestSeq !== Profile._searchRequestSeq) return;
            el.innerHTML = '<p class="search-result-helper">Unable to search right now.</p>';
        }
    },

    async addFriend(userId) {
        try {
            await API.post('/api/auth/friends/request', { friend_id: userId });
            App.toast('Friend request sent!');
            Profile.refreshSearchUsers();
        } catch { App.toast('Could not send request', 'error'); }
    },

    async respondRequest(friendshipId, action) {
        const activeQuery = Profile._activeSearchQuery;
        try {
            await API.post('/api/auth/friends/respond', { friendship_id: friendshipId, action });
            App.toast(action === 'accept' ? 'Friend added!' : 'Request declined');
            Profile.load();
            setTimeout(() => {
                Profile.loadExtras();
                const input = document.getElementById('friend-search-input');
                if (input && activeQuery) {
                    input.value = activeQuery;
                    Profile.queueSearchUsers(activeQuery);
                }
            }, 300);
        } catch { App.toast('Failed to respond', 'error'); }
    },

    showEdit() {
        document.getElementById('edit-profile-modal').style.display = 'flex';
    },

    hideEdit() {
        document.getElementById('edit-profile-modal').style.display = 'none';
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const data = {
            name: form.name.value,
            bio: form.bio.value,
            skill_level: form.skill_level.value ? parseFloat(form.skill_level.value) : null,
            play_style: form.play_style.value,
            preferred_times: form.preferred_times.value,
        };
        try {
            const res = await API.put('/api/auth/profile', data);
            if (res?.user) {
                localStorage.setItem('user', JSON.stringify(res.user));
                if (typeof Auth !== 'undefined') Auth.updateUI(res.user);
            }
            Profile.hideEdit();
            App.toast('Profile updated!');
            Profile.load();
        } catch { App.toast('Failed to save', 'error'); }
    },

    _e(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    },

    _ea(value) {
        return Profile._e(value).replace(/`/g, '&#96;');
    },

    _safeUrl(value) {
        const raw = String(value || '').trim();
        if (!raw) return '';
        if (raw.startsWith('data:image/')) return raw;
        try {
            const parsed = new URL(raw, window.location.origin);
            if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
                return raw;
            }
        } catch {}
        return '';
    },

    _relativeTime(isoValue) {
        if (!isoValue) return '';
        const ts = new Date(isoValue).getTime();
        if (!Number.isFinite(ts)) return '';
        const diffMs = Date.now() - ts;
        const diffMinutes = Math.floor(diffMs / 60000);
        if (diffMinutes < 1) return 'now';
        if (diffMinutes < 60) return `${diffMinutes}m`;
        const diffHours = Math.floor(diffMinutes / 60);
        if (diffHours < 24) return `${diffHours}h`;
        const diffDays = Math.floor(diffHours / 24);
        return `${diffDays}d`;
    },
};
