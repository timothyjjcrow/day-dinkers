/**
 * Games module — create, RSVP, list games, game detail page with chat.
 */
const Games = {
    async load() {
        const list = document.getElementById('games-list');
        list.innerHTML = '<div class="loading">Loading games...</div>';

        try {
            const res = await API.get('/api/games');
            const games = res.games || [];
            if (!games.length) {
                list.innerHTML = `
                    <div class="empty-state">
                        <h3>No upcoming games</h3>
                        <p>Be the first to create a game at a local court!</p>
                        <button class="btn-primary" onclick="Games.showCreateModal()">+ Create Game</button>
                    </div>`;
                return;
            }
            list.innerHTML = games.map(g => Games._renderGameCard(g)).join('');
        } catch {
            list.innerHTML = '<p class="error">Failed to load games</p>';
        }
    },

    _renderGameCard(game) {
        const dt = new Date(game.date_time);
        const dateStr = dt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
        const timeStr = dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
        const courtName = game.court?.name || 'Unknown Court';
        const courtCity = game.court?.city || '';
        const creator = game.creator?.name || game.creator?.username || 'Unknown';
        const playerCount = game.player_count || 0;
        const skillColor = game.skill_level === 'beginner' ? '#22c55e' :
                          game.skill_level === 'intermediate' ? '#f59e0b' :
                          game.skill_level === 'advanced' ? '#ef4444' : '#6b7280';
        const gameTypeIcon = game.game_type === 'doubles' ? '👥' :
                            game.game_type === 'singles' ? '👤' :
                            game.game_type === 'round_robin' ? '🔄' : '🎯';

        return `
        <div class="game-card" onclick="Games.openDetail(${game.id})">
            <div class="game-card-header">
                <div class="game-date-badge">
                    <span class="game-date-day">${dateStr}</span>
                    <span class="game-date-time">${timeStr}</span>
                </div>
                <div class="game-status-badge ${game.status || 'upcoming'}">${game.status || 'upcoming'}</div>
            </div>
            <h3 class="game-title">${game.title}</h3>
            <div class="game-meta">
                <span>📍 ${courtName}${courtCity ? `, ${courtCity}` : ''}</span>
                <span>${gameTypeIcon} ${game.game_type || 'Open Play'}</span>
            </div>
            <div class="game-meta">
                <span style="color:${skillColor}">⭐ ${game.skill_level || 'All levels'}</span>
                <span>👥 ${playerCount}/${game.max_players}</span>
                <span>Created by ${creator}</span>
            </div>
            ${game.description ? `<p class="game-desc">${game.description}</p>` : ''}
            <div class="game-actions" onclick="event.stopPropagation()">
                <button class="btn-primary btn-sm" onclick="Games.rsvp(${game.id})">Join Game</button>
                <button class="btn-secondary btn-sm" onclick="Games.openDetail(${game.id})">View Details</button>
            </div>
        </div>`;
    },

    // ── Game Detail Page ─────────────────────────────────
    async openDetail(gameId) {
        // Switch the games view to show the detail
        const container = document.getElementById('games-list');
        container.innerHTML = '<div class="loading">Loading game details...</div>';

        try {
            const res = await API.get(`/api/games/${gameId}`);
            const game = res.game;
            document.getElementById('games-view').querySelector('.view-header').innerHTML = `
                <button class="btn-secondary" onclick="Games._backToList()">← Back to Games</button>
                <h2>${game.title}</h2>
            `;
            container.innerHTML = Games._renderGameDetail(game);

            // Load chat messages for this game
            Games._loadGameChat(gameId);

            // Connect to socket room for real-time chat
            if (typeof Chat !== 'undefined' && Chat.socket) {
                Chat.socket.emit('join', { room: `game_${gameId}` });
            }
        } catch {
            container.innerHTML = '<p class="error">Failed to load game details</p>';
        }
    },

    _renderGameDetail(game) {
        const dt = new Date(game.date_time);
        const dateStr = dt.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
        const timeStr = dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
        const court = game.court || {};
        const creator = game.creator || {};
        const players = game.players || [];
        const yesPlayers = players.filter(p => p.rsvp_status === 'yes');
        const maybePlayers = players.filter(p => p.rsvp_status === 'maybe');
        const invitedPlayers = players.filter(p => p.rsvp_status === 'invited');

        const playersHTML = yesPlayers.length > 0
            ? yesPlayers.map(p => {
                const u = p.user;
                const initials = (u.name || u.username || '?')[0].toUpperCase();
                return `<div class="player-chip">
                    <span class="player-avatar">${initials}</span>
                    <span>${u.name || u.username}${u.skill_level ? ` (${u.skill_level})` : ''}</span>
                </div>`;
            }).join('')
            : '<p class="muted">No confirmed players yet. Be the first to join!</p>';

        const maybeHTML = maybePlayers.length > 0
            ? `<div class="maybe-players"><strong>Maybe:</strong> ${maybePlayers.map(p => p.user.name || p.user.username).join(', ')}</div>`
            : '';

        const invitedHTML = invitedPlayers.length > 0
            ? `<div class="invited-players"><strong>Invited:</strong> ${invitedPlayers.map(p => p.user.name || p.user.username).join(', ')}</div>`
            : '';

        return `
        <div class="game-detail">
            <div class="game-detail-hero">
                <div class="game-detail-date">
                    <div class="hero-date">${dateStr}</div>
                    <div class="hero-time">${timeStr}</div>
                </div>
                <div class="game-detail-status ${game.status || 'upcoming'}">${game.status || 'upcoming'}</div>
            </div>

            <div class="game-detail-grid">
                <div class="game-detail-info">
                    <div class="detail-section">
                        <h4>Court</h4>
                        <div class="detail-court-card" onclick="MapView.openCourtDetail(${court.id}); App.showView('map');">
                            <strong>${court.name || 'Unknown'}</strong>
                            <span>${court.address || ''}, ${court.city || ''}</span>
                            <span>${court.indoor ? '🏢 Indoor' : '☀️ Outdoor'} · ${court.num_courts || '?'} courts</span>
                            <a href="https://www.google.com/maps/dir/?api=1&destination=${court.latitude},${court.longitude}" target="_blank" class="btn-secondary btn-sm" onclick="event.stopPropagation()">🗺 Directions</a>
                        </div>
                    </div>

                    <div class="detail-section">
                        <h4>Game Info</h4>
                        <div class="detail-meta-grid">
                            <div><span class="detail-label">Type</span><span>${game.game_type || 'Open Play'}</span></div>
                            <div><span class="detail-label">Skill</span><span>${game.skill_level || 'All'}</span></div>
                            <div><span class="detail-label">Players</span><span>${yesPlayers.length}/${game.max_players}</span></div>
                            <div><span class="detail-label">Open</span><span>${game.is_open ? 'Anyone can join' : 'Invite only'}</span></div>
                        </div>
                    </div>

                    ${game.description ? `<div class="detail-section"><h4>Description</h4><p>${game.description}</p></div>` : ''}

                    <div class="detail-section">
                        <h4>Organized by</h4>
                        <div class="player-chip">
                            <span class="player-avatar">${(creator.name || creator.username || '?')[0].toUpperCase()}</span>
                            <span>${creator.name || creator.username}</span>
                        </div>
                    </div>

                    <div class="detail-section">
                        <h4>Confirmed Players (${yesPlayers.length}/${game.max_players})</h4>
                        <div class="players-list">${playersHTML}</div>
                        ${maybeHTML}
                        ${invitedHTML}
                    </div>

                    <div class="detail-actions">
                        <button class="btn-primary" onclick="Games.rsvp(${game.id})">✅ Join Game</button>
                        <button class="btn-secondary" onclick="Games.rsvp(${game.id}, 'maybe')">🤔 Maybe</button>
                        <button class="btn-secondary" onclick="Games.rsvp(${game.id}, 'no')">❌ Can't Make It</button>
                        <button class="btn-secondary" onclick="Games.inviteFriends(${game.id})">👥 Invite Friends</button>
                    </div>
                </div>

                <div class="game-detail-chat">
                    <div class="game-chat-container">
                        <h4>💬 Game Chat</h4>
                        <div id="game-chat-messages" class="game-chat-messages"></div>
                        <form class="game-chat-input" onsubmit="Games.sendChat(event, ${game.id})">
                            <input type="text" id="game-chat-text" placeholder="Message the group..." autocomplete="off">
                            <button type="submit" class="btn-primary btn-sm">Send</button>
                        </form>
                    </div>
                </div>
            </div>
        </div>`;
    },

    async _loadGameChat(gameId) {
        const container = document.getElementById('game-chat-messages');
        if (!container) return;
        const token = localStorage.getItem('token');
        if (!token) {
            container.innerHTML = '<p class="muted">Sign in to view and send messages</p>';
            return;
        }
        try {
            const res = await API.get(`/api/chat/game/${gameId}`);
            const msgs = res.messages || [];
            if (!msgs.length) {
                container.innerHTML = '<p class="muted">No messages yet. Say hello!</p>';
                return;
            }
            container.innerHTML = msgs.map(m => Games._renderChatMsg(m)).join('');
            container.scrollTop = container.scrollHeight;
        } catch {
            container.innerHTML = '<p class="muted">Sign in to view chat</p>';
        }
    },

    _renderChatMsg(msg) {
        const sender = msg.sender || {};
        const time = new Date(msg.created_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
        const currentUser = JSON.parse(localStorage.getItem('user') || '{}');
        const isMe = sender.id === currentUser.id;
        return `
        <div class="chat-msg ${isMe ? 'chat-msg-me' : ''}">
            <div class="chat-msg-header">
                <strong>${isMe ? 'You' : (sender.name || sender.username)}</strong>
                <span class="chat-msg-time">${time}</span>
            </div>
            <div class="chat-msg-body">${msg.content}</div>
        </div>`;
    },

    async sendChat(e, gameId) {
        e.preventDefault();
        const input = document.getElementById('game-chat-text');
        const content = input.value.trim();
        if (!content) return;
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }

        try {
            await API.post('/api/chat/send', {
                content, game_id: gameId, msg_type: 'game',
            });
            input.value = '';
            Games._loadGameChat(gameId);
        } catch { App.toast('Failed to send message', 'error'); }
    },

    inviteFriends(gameId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        App.showInviteModal('Invite Friends to Game', async (friendIds) => {
            try {
                const res = await API.post(`/api/games/${gameId}/invite`, { friend_ids: friendIds });
                App.toast(res.message || 'Invites sent!');
                App.hideInviteModal();
                Games.openDetail(gameId);
            } catch (err) {
                App.toast(err.message || 'Failed to send invites', 'error');
            }
        });
    },

    _backToList() {
        document.getElementById('games-view').querySelector('.view-header').innerHTML = `
            <h2>Upcoming Games</h2>
            <button class="btn-primary" onclick="Games.showCreateModal()">+ Create Game</button>
        `;
        Games.load();
    },

    // ── RSVP ─────────────────────────────────────────────
    async rsvp(gameId, status = 'yes') {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        try {
            await API.post(`/api/games/${gameId}/rsvp`, { status });
            if (status === 'yes') App.toast("You're in! See you on the court.");
            else if (status === 'maybe') App.toast('Marked as maybe');
            else App.toast('RSVP updated');
            // Refresh the detail if we're on it
            Games.openDetail(gameId);
        } catch (err) {
            App.toast(err.message || 'Failed to RSVP', 'error');
        }
    },

    // ── Create Game ──────────────────────────────────────
    async showCreateModal(preselectedCourtId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        document.getElementById('game-modal').style.display = 'flex';
        const select = document.getElementById('game-court-select');
        try {
            const res = await API.get('/api/courts');
            select.innerHTML = (res.courts || []).map(c =>
                `<option value="${c.id}" ${c.id === preselectedCourtId ? 'selected' : ''}>${c.name} — ${c.city}</option>`
            ).join('');
        } catch { select.innerHTML = '<option>Error loading courts</option>'; }

        // Populate invite friends section
        const inviteSection = document.getElementById('game-invite-section');
        const inviteList = document.getElementById('game-invite-friends');
        if (App.friendsList.length > 0) {
            inviteSection.style.display = 'block';
            inviteList.innerHTML = App.friendsList.map(f => `
                <label class="friend-pick-item">
                    <input type="checkbox" value="${f.id}" name="invite_friends">
                    <span class="friend-pick-avatar">${(f.name || f.username || '?')[0].toUpperCase()}</span>
                    <span class="friend-pick-name">${f.name || f.username}</span>
                </label>
            `).join('');
        } else {
            inviteSection.style.display = 'none';
        }
    },

    hideCreateModal() {
        document.getElementById('game-modal').style.display = 'none';
    },

    async create(e) {
        e.preventDefault();
        const form = e.target;
        const data = {
            title: form.title.value,
            court_id: parseInt(form.court_id.value),
            date_time: form.date_time.value,
            max_players: parseInt(form.max_players.value),
            skill_level: form.skill_level.value,
            game_type: form.game_type.value,
            description: form.description.value,
            is_open: true,
        };
        try {
            const res = await API.post('/api/games', data);
            // Invite selected friends
            const selectedFriends = Array.from(form.querySelectorAll('input[name="invite_friends"]:checked')).map(cb => parseInt(cb.value));
            if (selectedFriends.length > 0) {
                try {
                    await API.post(`/api/games/${res.game.id}/invite`, { friend_ids: selectedFriends });
                    App.toast(`Game created! Invited ${selectedFriends.length} friend(s)`);
                } catch { App.toast('Game created! (some invites failed)'); }
            } else {
                App.toast('Game created!');
            }
            Games.hideCreateModal();
            Games.openDetail(res.game.id);
        } catch { App.toast('Failed to create game', 'error'); }
    },
};
