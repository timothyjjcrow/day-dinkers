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
            <div class="challenge-match-type-toggle" role="group" aria-label="Match type">
                <button
                    type="button"
                    class="challenge-match-type-btn active"
                    data-prefix="${prefix}"
                    data-type="singles"
                    onclick="Ranked._setChallengeMatchType('${prefix}', 'singles')"
                >
                    Singles (1v1)
                </button>
                <button
                    type="button"
                    class="challenge-match-type-btn ${doublesDisabled ? 'disabled' : ''}"
                    data-prefix="${prefix}"
                    data-type="doubles"
                    ${doublesDisabled ? 'disabled' : ''}
                    onclick="${doublesDisabled ? '' : `Ranked._setChallengeMatchType('${prefix}', 'doubles')`}"
                >
                    Doubles (2v2)
                </button>
            </div>
            ${doublesDisabled ? '<p class="field-help">Add more players to enable doubles.</p>' : ''}
            <input type="hidden" id="${prefix}-type" value="singles">
        </div>`;
    },

    _setChallengeMatchType(prefix, nextType) {
        const hiddenInput = document.getElementById(`${prefix}-type`);
        if (!hiddenInput) return;
        const normalizedType = nextType === 'doubles' ? 'doubles' : 'singles';
        hiddenInput.value = normalizedType;

        document.querySelectorAll(`.challenge-match-type-btn[data-prefix="${prefix}"]`).forEach((button) => {
            button.classList.toggle('active', button.dataset.type === normalizedType);
        });

        const doublesFields = document.getElementById(`${prefix}-doubles`);
        if (doublesFields) {
            doublesFields.style.display = normalizedType === 'doubles' ? 'block' : 'none';
        }
    },

    _doublesFormHTML(prefix, partnerOptions) {
        return `
        <div id="${prefix}-doubles" class="challenge-doubles-fields" style="display:none">
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
        return Ranked._toLocalDateTimeValue(d);
    },

    _toLocalDateTimeValue(date) {
        if (!(date instanceof Date) || Number.isNaN(date.getTime())) return '';
        const pad = (n) => String(n).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T`
            + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    },

    _scheduleQuickPresetValues() {
        const now = new Date();

        const tonight = new Date(now);
        tonight.setHours(18, 30, 0, 0);
        if (tonight <= now) tonight.setDate(tonight.getDate() + 1);

        const tomorrowMorning = new Date(now);
        tomorrowMorning.setDate(tomorrowMorning.getDate() + 1);
        tomorrowMorning.setHours(9, 0, 0, 0);

        const tomorrowEvening = new Date(now);
        tomorrowEvening.setDate(tomorrowEvening.getDate() + 1);
        tomorrowEvening.setHours(18, 30, 0, 0);

        const weekendMorning = new Date(now);
        weekendMorning.setHours(10, 0, 0, 0);
        const saturdayOffset = (6 - weekendMorning.getDay() + 7) % 7;
        weekendMorning.setDate(weekendMorning.getDate() + saturdayOffset);
        if (weekendMorning <= now) weekendMorning.setDate(weekendMorning.getDate() + 7);

        return [
            { id: 'tonight', label: 'Tonight 6:30 PM', value: Ranked._toLocalDateTimeValue(tonight) },
            { id: 'tomorrow_am', label: 'Tomorrow 9:00 AM', value: Ranked._toLocalDateTimeValue(tomorrowMorning) },
            { id: 'tomorrow_pm', label: 'Tomorrow 6:30 PM', value: Ranked._toLocalDateTimeValue(tomorrowEvening) },
            { id: 'weekend', label: 'Weekend 10:00 AM', value: Ranked._toLocalDateTimeValue(weekendMorning) },
        ];
    },

    _scheduleQuickPicksHTML(inputId, previewId = '') {
        const presets = Ranked._scheduleQuickPresetValues();
        return `
            <div class="schedule-quick-picks" role="group" aria-label="Quick schedule options">
                ${presets.map(preset => `
                    <button
                        type="button"
                        class="schedule-quick-pick-btn"
                        data-input-id="${inputId}"
                        data-preset-id="${preset.id}"
                        onclick="Ranked._applyScheduleQuickPick('${inputId}', '${preset.id}', '${previewId || ''}')"
                    >
                        ${Ranked._e(preset.label)}
                    </button>
                `).join('')}
            </div>
        `;
    },

    _syncScheduleQuickPickButtons(inputId, activePresetId = '') {
        const buttons = document.querySelectorAll(`.schedule-quick-pick-btn[data-input-id="${inputId}"]`);
        buttons.forEach((button) => {
            button.classList.toggle('active', !!activePresetId && button.dataset.presetId === activePresetId);
        });
    },

    _applyScheduleQuickPick(inputId, presetId, previewId = '') {
        const input = document.getElementById(inputId);
        if (!input) return;
        const preset = Ranked._scheduleQuickPresetValues().find(item => item.id === presetId);
        if (!preset || !preset.value) return;

        if (typeof DateTimePicker !== 'undefined' && typeof DateTimePicker.setValue === 'function') {
            DateTimePicker.setValue(input, preset.value);
        } else {
            input.value = preset.value;
        }

        Ranked._syncScheduleQuickPickButtons(inputId, presetId);
        Ranked._updateSchedulePreview(inputId, previewId);
    },

    _initSchedulePicker(inputId, previewId = '') {
        const input = document.getElementById(inputId);
        if (!input) return;

        const minDate = new Date(Date.now() + 30 * 60 * 1000);
        minDate.setSeconds(0, 0);
        minDate.setMinutes(Math.ceil(minDate.getMinutes() / 15) * 15);
        const minValue = Ranked._toLocalDateTimeValue(minDate);
        input.min = minValue;
        if (typeof DateTimePicker !== 'undefined' && typeof DateTimePicker.setMin === 'function') {
            DateTimePicker.setMin(input, minValue);
        }

        if (!input.value) {
            const defaultValue = Ranked._defaultScheduleTime();
            if (typeof DateTimePicker !== 'undefined' && typeof DateTimePicker.setValue === 'function') {
                DateTimePicker.setValue(input, defaultValue);
            } else {
                input.value = defaultValue;
            }
        }

        const syncPreview = () => {
            Ranked._syncScheduleQuickPickButtons(inputId, '');
            Ranked._updateSchedulePreview(inputId, previewId);
        };
        input.onchange = syncPreview;
        input.oninput = syncPreview;
        Ranked._updateSchedulePreview(inputId, previewId);
    },

    _updateSchedulePreview(inputId, previewId = '') {
        if (!previewId) return;
        const preview = document.getElementById(previewId);
        const input = document.getElementById(inputId);
        if (!preview || !input) return;
        if (!input.value) {
            preview.textContent = 'Pick a date and time for your challenge.';
            return;
        }
        const scheduled = new Date(input.value);
        if (Number.isNaN(scheduled.getTime())) {
            preview.textContent = 'Pick a valid date and time.';
            return;
        }

        const dateLabel = scheduled.toLocaleDateString('en-US', {
            weekday: 'short',
            month: 'short',
            day: 'numeric',
        });
        const timeLabel = scheduled.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

        const minsUntil = Math.round((scheduled.getTime() - Date.now()) / 60000);
        let relative = '';
        if (minsUntil > 0 && minsUntil < 60) relative = `in ${minsUntil} min`;
        else if (minsUntil >= 60 && minsUntil < 24 * 60) relative = `in ${Math.round(minsUntil / 60)}h`;
        else if (minsUntil >= 24 * 60) relative = `in ${Math.round(minsUntil / (24 * 60))} day${Math.round(minsUntil / (24 * 60)) === 1 ? '' : 's'}`;

        preview.textContent = `Selected: ${dateLabel} at ${timeLabel}${relative ? ` (${relative})` : ''}`;
    },

    _bindChallengePartnerSelectors(prefix, opponentSelectId = '', fixedOpponentId = 0) {
        const sync = () => Ranked._syncChallengePartnerAvailability(prefix, opponentSelectId, fixedOpponentId);
        const ids = [`${prefix}-partner1`, `${prefix}-partner2`];
        if (opponentSelectId) ids.push(opponentSelectId);
        ids.forEach((id) => {
            const field = document.getElementById(id);
            if (field) field.onchange = sync;
        });
        sync();
    },

    _syncChallengePartnerAvailability(prefix, opponentSelectId = '', fixedOpponentId = 0) {
        const partnerOne = document.getElementById(`${prefix}-partner1`);
        const partnerTwo = document.getElementById(`${prefix}-partner2`);
        if (!partnerOne || !partnerTwo) return;

        const selectedOpponentId = opponentSelectId
            ? Number(document.getElementById(opponentSelectId)?.value || 0)
            : Number(fixedOpponentId || 0);
        const partnerOneId = Number(partnerOne.value || 0);
        const partnerTwoId = Number(partnerTwo.value || 0);

        const updateSelect = (selectEl, blockedIds) => {
            const blocked = new Set(blockedIds.filter(Boolean).map(Number));
            Array.from(selectEl.options).forEach((option) => {
                if (!option.value) {
                    option.disabled = false;
                    return;
                }
                option.disabled = blocked.has(Number(option.value));
            });
            if (selectEl.value && selectEl.selectedOptions[0]?.disabled) {
                selectEl.value = '';
            }
        };

        updateSelect(partnerOne, [selectedOpponentId, partnerTwoId]);
        updateSelect(partnerTwo, [selectedOpponentId, partnerOneId]);
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

        const canDoubles = allPlayers.length >= 4;
        const defaultType = canDoubles ? 'doubles' : 'singles';

        const modal = document.getElementById('match-modal');
        modal.style.display = 'flex';
        modal.innerHTML = `
        <div class="modal-content match-create-modal">
            <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
            <h2>Create Ranked Match</h2>
            <div class="match-create-hero">
                <div class="match-create-hero-info">
                    <strong>${allPlayers.length} players available</strong>
                    <span class="muted">Queue and checked-in players at this court</span>
                </div>
                <span class="t-badge t-badge-upcoming">Ranked</span>
            </div>
            <form id="create-match-form" onsubmit="Ranked.createMatch(event, ${courtId})">
                <div class="match-create-section">
                    <span class="match-create-section-label">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                        Match Format
                    </span>
                    <div class="match-type-toggle">
                        <button type="button" class="match-type-btn ${defaultType === 'singles' ? 'active' : ''}" data-type="singles" onclick="Ranked._selectMatchType('singles')">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                            Singles (1v1)
                        </button>
                        <button type="button" class="match-type-btn ${canDoubles && defaultType === 'doubles' ? 'active' : ''} ${!canDoubles ? 'disabled' : ''}" data-type="doubles" onclick="${canDoubles ? "Ranked._selectMatchType('doubles')" : ''}">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                            Doubles (2v2)${!canDoubles ? ' — need 4' : ''}
                        </button>
                    </div>
                    <input type="hidden" name="match_type" id="match-type-select" value="${defaultType}">
                </div>
                <div class="match-create-section">
                    <span class="match-create-section-label">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                        Select Players
                    </span>
                    <div class="match-teams-setup">
                        <div class="team-setup" id="team1-setup">
                            <div class="team-setup-header">
                                <span class="team-setup-number team-1-accent">1</span>
                                <h4>Team 1</h4>
                            </div>
                            <div id="team1-slots"></div>
                        </div>
                        <div class="vs-divider">VS</div>
                        <div class="team-setup" id="team2-setup">
                            <div class="team-setup-header">
                                <span class="team-setup-number team-2-accent">2</span>
                                <h4>Team 2</h4>
                            </div>
                            <div id="team2-slots"></div>
                        </div>
                    </div>
                </div>
                <div class="match-create-note">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
                    All players must confirm the score after the match for ELO rankings to update.
                </div>
                <input type="hidden" id="available-players-data" value='${JSON.stringify(allPlayers)}'>
                <button type="submit" class="btn-primary btn-full" style="min-height:44px;font-size:14px">Start Match</button>
            </form>
        </div>`;

        Ranked._updateTeamSlots();
    },

    _selectMatchType(type) {
        const hidden = document.getElementById('match-type-select');
        if (!hidden) return;
        hidden.value = type;
        document.querySelectorAll('.match-type-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.type === type);
        });
        Ranked._updateTeamSlots();
    },

    _updateTeamSlots() {
        const type = document.getElementById('match-type-select').value;
        const count = type === 'singles' ? 1 : 2;
        const players = JSON.parse(document.getElementById('available-players-data').value || '[]');

        const options = players.map(p => {
            const elo = Math.round(p.elo_rating || 1200);
            return `<option value="${p.id}">${Ranked._e(p.name || p.username)} · ELO ${elo}</option>`;
        }).join('');

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
            if (typeof Ranked.refreshOpenTournamentModal === 'function') {
                Ranked.refreshOpenTournamentModal();
            }
        } catch (err) {
            if (btn) btn.disabled = false;
            App.toast(err.message || 'Failed to submit score', 'error');
        }
    },
});
