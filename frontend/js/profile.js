/**
 * Profile page — full-page view with stats, friends, and edit functionality.
 */
const Profile = {
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
                    <button class="btn-secondary btn-sm" onclick="App.setMainTab('sessions')" style="margin-top:8px">Find a Court</button>
                </div>
                <div class="profile-section">
                    <h3>🟢 My Sessions</h3>
                    <div id="profile-upcoming-sessions">Loading...</div>
                </div>
                <div class="profile-section">
                    <h3>Friends</h3>
                    <div id="profile-friends-list">Loading...</div>
                    <div class="friend-search">
                        <input type="text" id="friend-search-input" placeholder="Search players..." oninput="Profile.searchUsers(this.value)">
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
        <div id="edit-profile-modal" class="modal" style="display:none">
            <div class="modal-content">
                <button class="modal-close" onclick="Profile.hideEdit()">&times;</button>
                <h2>Edit Profile</h2>
                <form onsubmit="Profile.save(event)">
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
                    <button type="submit" class="btn-primary btn-full">Save Changes</button>
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
        if (elo >= 1800) return { name: 'Diamond', icon: '💎', class: 'elo-diamond' };
        if (elo >= 1600) return { name: 'Platinum', icon: '🏆', class: 'elo-platinum' };
        if (elo >= 1400) return { name: 'Gold', icon: '🥇', class: 'elo-gold' };
        if (elo >= 1200) return { name: 'Silver', icon: '🥈', class: 'elo-silver' };
        if (elo >= 1000) return { name: 'Bronze', icon: '🥉', class: 'elo-bronze' };
        return { name: 'Unranked', icon: '⭐', class: 'elo-unranked' };
    },

    async loadExtras() {
        Profile._loadFriends();
        Profile._loadPendingRequests();
        Profile._loadUpcomingSessions();
        Profile._loadMatchHistory();
        CourtUpdates.loadReviewerPanel();
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
            const res = await API.get('/api/auth/friends');
            if (!res.friends.length) {
                el.innerHTML = '<p class="muted">No friends yet. Search for players to connect!</p>';
                return;
            }
            el.innerHTML = res.friends.map(f => `
                <div class="friend-card">
                    <div class="friend-avatar">${Profile._e((f.name || f.username)[0].toUpperCase())}</div>
                    <div class="friend-info">
                        <strong>${Profile._e(f.name || f.username)}</strong>
                        <span class="muted">@${Profile._e(f.username)}</span>
                    </div>
                    ${f.skill_level ? `<span class="tag tag-skill">${f.skill_level}</span>` : ''}
                    <button class="btn-secondary btn-sm" onclick="Ranked.openScheduledChallengeModal(${f.id}, 'friends_challenge')">⚔️ Ranked</button>
                </div>
            `).join('');
        } catch { el.innerHTML = '<p class="muted">Sign in to see friends</p>'; }
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
                el.innerHTML = '<p class="muted">No active or upcoming sessions. <a href="#" onclick="App.setMainTab(\'sessions\')">Browse open sessions</a></p>';
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
                <div class="session-card-mini ${isNow ? 'session-mini-active' : ''}" onclick="App.setMainTab('sessions'); setTimeout(() => Sessions.openDetail(${s.id}), 200);">
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

    async searchUsers(query) {
        const el = document.getElementById('friend-search-results');
        if (query.length < 2) { el.innerHTML = ''; return; }
        try {
            const res = await API.get(`/api/auth/users/search?q=${encodeURIComponent(query)}`);
            el.innerHTML = res.users.map(u => `
                <div class="search-result-card">
                    <span>${Profile._e(u.name || u.username)} (@${Profile._e(u.username)})</span>
                    <button class="btn-primary btn-sm" onclick="Profile.addFriend(${u.id})">Add Friend</button>
                </div>
            `).join('') || '<p class="muted">No players found</p>';
        } catch { el.innerHTML = ''; }
    },

    async addFriend(userId) {
        try {
            await API.post('/api/auth/friends/request', { friend_id: userId });
            App.toast('Friend request sent!');
        } catch { App.toast('Could not send request', 'error'); }
    },

    async respondRequest(friendshipId, action) {
        try {
            await API.post('/api/auth/friends/respond', { friendship_id: friendshipId, action });
            App.toast(action === 'accept' ? 'Friend added!' : 'Request declined');
            Profile.load();
            setTimeout(() => Profile.loadExtras(), 300);
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
};
