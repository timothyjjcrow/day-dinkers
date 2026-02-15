/**
 * Ranked modals — match creation, score entry, and shared challenge helpers.
 * Extends the Ranked object defined in ranked.js.
 */
Object.assign(Ranked, {

    // ── Shared Challenge Helpers ─────────────────────────────────────

    _matchTypeSelectHTML(prefix, doublesDisabled = false) {
        return `
        <div class="form-group">
            <label>Match Type</label>
            <select id="${prefix}-type" onchange="document.getElementById('${prefix}-doubles').style.display = this.value === 'doubles' ? 'block' : 'none'">
                <option value="singles">Singles (1v1)</option>
                <option value="doubles"${doublesDisabled ? ' disabled' : ''}>Doubles (2v2)</option>
            </select>
        </div>`;
    },

    _doublesFormHTML(prefix, partnerOptions) {
        return `
        <div id="${prefix}-doubles" style="display:none">
            <div class="form-group">
                <label>Your Partner</label>
                <select id="${prefix}-partner1">
                    <option value="">Select partner...</option>
                    ${partnerOptions}
                </select>
            </div>
            <div class="form-group">
                <label>Opponent Partner</label>
                <select id="${prefix}-partner2">
                    <option value="">Select opponent partner...</option>
                    ${partnerOptions}
                </select>
            </div>
        </div>`;
    },

    _parseDoublesTeams(prefix, myId, opponentId) {
        const matchType = document.getElementById(`${prefix}-type`).value;
        let team1 = [myId];
        let team2 = [opponentId];

        if (matchType === 'doubles') {
            const p1 = parseInt(document.getElementById(`${prefix}-partner1`).value, 10);
            const p2 = parseInt(document.getElementById(`${prefix}-partner2`).value, 10);
            if (!p1 || !p2) {
                App.toast('Pick both doubles partners.', 'error');
                return null;
            }
            const all = [myId, opponentId, p1, p2];
            if (new Set(all).size !== all.length) {
                App.toast('Doubles players must all be unique.', 'error');
                return null;
            }
            team1 = [myId, p1];
            team2 = [opponentId, p2];
        }
        return { matchType, team1, team2 };
    },

    _defaultScheduleTime() {
        const d = new Date(Date.now() + 24 * 60 * 60 * 1000);
        d.setMinutes(0, 0, 0);
        return d.toISOString().slice(0, 16);
    },

    // ── Match Creation Modal ────────────────────────────────────────

    async showCreateMatch(courtId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }

        const [queueRes, courtRes] = await Promise.all([
            API.get(`/api/ranked/queue/${courtId}`),
            API.get(`/api/courts/${courtId}`),
        ]);
        const queuePlayers = (queueRes.queue || []).map(q => q.user);
        const checkedIn = (courtRes.court?.checked_in_users || []);
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
            <h2>Create Ranked Match</h2>
            <p class="muted" style="margin-bottom:8px">${allPlayers.length} players available at this court</p>
            <p class="confirm-note">All players must confirm the score after the match for rankings to update.</p>
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
                <button type="submit" class="btn-primary btn-full" style="margin-top:16px">Start Match</button>
            </form>
        </div>`;

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
        const btn = form.querySelector('button[type="submit"]');
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

        const allIds = [...team1, ...team2];
        if (new Set(allIds).size !== allIds.length) {
            App.toast('A player cannot be on both teams', 'error');
            return;
        }

        if (btn) btn.disabled = true;
        try {
            const res = await API.post('/api/ranked/lobby/queue', {
                court_id: courtId, match_type: matchType,
                team1, team2, start_immediately: true,
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
            if (btn) btn.disabled = false;
            App.toast(err.message || 'Failed to create match', 'error');
        }
    },

    // ── Score Submission Modal ───────────────────────────────────────

    async showScoreModal(matchId) {
        try {
            const res = await API.get(`/api/ranked/match/${matchId}`);
            const match = res.match;
            const t1 = Ranked._teamNames(match.team1);
            const t2 = Ranked._teamNames(match.team2);

            const modal = document.getElementById('match-modal');
            modal.style.display = 'flex';
            modal.innerHTML = `
            <div class="modal-content score-modal">
                <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
                <h2>Enter Match Score</h2>
                <p class="confirm-note">All players must confirm the score before rankings update.</p>
                <div class="score-teams">
                    <div class="score-team">
                        <h4>Team 1</h4>
                        <p class="score-team-names">${t1}</p>
                        <input type="number" id="score-team1" class="score-input" min="0" max="99" value="" placeholder="0" inputmode="numeric" required>
                    </div>
                    <div class="score-divider">—</div>
                    <div class="score-team">
                        <h4>Team 2</h4>
                        <p class="score-team-names">${t2}</p>
                        <input type="number" id="score-team2" class="score-input" min="0" max="99" value="" placeholder="0" inputmode="numeric" required>
                    </div>
                </div>
                <p class="score-hint muted">Standard pickleball: first to 11, win by 2</p>
                <button class="btn-primary btn-full" onclick="Ranked.submitScore(${matchId}, event)" style="margin-top:12px">Submit Score for Confirmation</button>
            </div>`;
        } catch {
            App.toast('Failed to load match details', 'error');
        }
    },

    async submitScore(matchId, event) {
        const team1Score = parseInt(document.getElementById('score-team1').value);
        const team2Score = parseInt(document.getElementById('score-team2').value);

        if (isNaN(team1Score) || isNaN(team2Score)) {
            App.toast('Enter valid scores', 'error'); return;
        }
        if (team1Score === team2Score) {
            App.toast('Scores cannot be tied — someone must win!', 'error'); return;
        }

        const btn = Ranked._disableBtn(event);
        try {
            const res = await API.post(`/api/ranked/match/${matchId}/score`, {
                team1_score: team1Score, team2_score: team2Score,
            });
            document.getElementById('match-modal').style.display = 'none';

            if (res.pending_confirmation) {
                App.toast('Score submitted. Waiting for all players to confirm.');
            } else {
                Ranked._showCompletedResults(res.match);
            }

            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
            Ranked.loadPendingConfirmations();
        } catch (err) {
            if (btn) btn.disabled = false;
            App.toast(err.message || 'Failed to submit score', 'error');
        }
    },
});
