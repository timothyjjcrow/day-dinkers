/**
 * Ranked challenge modals — court, scheduled, and leaderboard challenges.
 * Extends the Ranked object defined in ranked.js.
 * Uses shared helpers from ranked_modals.js.
 */
Object.assign(Ranked, {

    async challengeCheckedInPlayer(courtId, targetUserId) {
        await Ranked.openCourtChallengeModal(courtId, targetUserId);
    },

    // ── Court Challenge (checked-in players) ────────────────────────

    async openCourtChallengeModal(courtId, targetUserId) {
        try {
            const courtRes = await API.get(`/api/courts/${courtId}`);
            const checkedIn = courtRes.court?.checked_in_users || [];
            const currentUser = Ranked._currentUser();
            const me = checkedIn.find(u => u.id === currentUser.id);
            const target = checkedIn.find(u => u.id === targetUserId);
            if (!me) { App.toast('Check in at this court before challenging players.', 'error'); return; }
            if (!target) { App.toast('That player is no longer checked in here.', 'error'); return; }

            const extraOptions = checkedIn
                .filter(u => u.id !== currentUser.id && u.id !== targetUserId)
                .map(u => `<option value="${u.id}">${Ranked._e(u.name || u.username)}</option>`)
                .join('');

            const modal = document.getElementById('match-modal');
            modal.style.display = 'flex';
            modal.innerHTML = `
            <div class="modal-content ranked-challenge-modal">
                <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
                <h2>Challenge Player</h2>
                <p class="ranked-challenge-subtitle">Send a ranked invite to someone checked in at this court.</p>
                <form class="ranked-challenge-form" onsubmit="Ranked.createCourtChallenge(event, ${courtId}, ${targetUserId})">
                    <div class="ranked-form-section">
                        <span class="ranked-form-section-label">Players</span>
                        <p class="muted">You: <strong>${Ranked._e(me.name || me.username)}</strong></p>
                        <p class="muted">Opponent: <strong>${Ranked._e(target.name || target.username)}</strong></p>
                    </div>
                    <div class="ranked-form-section">
                        <span class="ranked-form-section-label">Match setup</span>
                        ${Ranked._matchTypeSelectHTML('court-challenge', !extraOptions)}
                        ${Ranked._doublesFormHTML('court-challenge', extraOptions)}
                    </div>
                    <button type="submit" class="btn-primary btn-full">Send Challenge</button>
                </form>
            </div>`;
            Ranked._bindChallengePartnerSelectors('court-challenge', '', Number(targetUserId));
        } catch {
            App.toast('Unable to load challenge setup', 'error');
        }
    },

    async createCourtChallenge(e, courtId, targetUserId) {
        e.preventDefault();
        const currentUser = Ranked._currentUser();
        const parsed = Ranked._parseDoublesTeams('court-challenge', currentUser.id, targetUserId);
        if (!parsed) return;

        const btn = e.target.querySelector('button[type="submit"]');
        if (btn) btn.disabled = true;
        try {
            await API.post('/api/ranked/challenge/court', {
                court_id: courtId,
                match_type: parsed.matchType,
                team1: parsed.team1,
                team2: parsed.team2,
            });
            document.getElementById('match-modal').style.display = 'none';
            App.toast('Challenge sent.');
            Ranked.loadPendingConfirmations();
            Ranked.loadCourtRanked(courtId);
        } catch (err) {
            if (btn) btn.disabled = false;
            App.toast(err.message || 'Failed to send challenge', 'error');
        }
    },

    // ── Scheduled Challenge from Court (friends list) ───────────────

    async openCourtScheduledChallenge(courtId) {
        const token = localStorage.getItem('token');
        if (!token) { Auth.showModal(); return; }
        if (!App.friendsList || !App.friendsList.length) {
            await App.loadFriendsCache();
        }

        const friends = [...(App.friendsList || [])].sort((a, b) => {
            const aName = String(a.name || a.username || '').toLowerCase();
            const bName = String(b.name || b.username || '').toLowerCase();
            return aName.localeCompare(bName);
        });
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

        let courtSelectHTML = '';
        const hasCourtId = courtId && Number(courtId) > 0;
        if (!hasCourtId) {
            try {
                const courtsRes = await API.get(App.buildCourtsQuery());
                const courts = [...(courtsRes.courts || [])].sort((a, b) => {
                    const aName = String(a.name || '').toLowerCase();
                    const bName = String(b.name || '').toLowerCase();
                    return aName.localeCompare(bName);
                });
                const courtsOptions = courts.map(c =>
                    `<option value="${c.id}">${Ranked._e(c.name)} — ${Ranked._e(c.city)}</option>`
                ).join('');
                courtSelectHTML = `<div class="form-group">
                    <label>Court</label>
                    <select id="court-scheduled-court" required>
                        <option value="">Select court...</option>
                        ${courtsOptions}
                    </select>
                </div>`;
            } catch { /* proceed without court list */ }
        }

        const modal = document.getElementById('match-modal');
        modal.style.display = 'flex';
        modal.innerHTML = `
        <div class="modal-content ranked-challenge-modal">
            <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
            <h2>Schedule Ranked Challenge</h2>
            <p class="ranked-challenge-subtitle">Pick your friend, lock in a time, and send the invite.</p>
            <form class="ranked-challenge-form" onsubmit="Ranked.createCourtScheduledChallenge(event)">
                <div class="ranked-form-section">
                    <span class="ranked-form-section-label">Opponent and court</span>
                    ${courtSelectHTML}
                    <div class="form-group">
                        <label>Opponent</label>
                        <select id="court-scheduled-opponent" required>
                            <option value="">Select opponent...</option>
                            ${opponentOptions}
                        </select>
                    </div>
                </div>

                <div class="ranked-form-section">
                    <span class="ranked-form-section-label">Schedule</span>
                    <div class="form-group">
                        <label>Scheduled Time</label>
                        <input type="datetime-local" id="court-scheduled-time" value="${Ranked._defaultScheduleTime()}" required>
                        ${Ranked._scheduleQuickPicksHTML('court-scheduled-time', 'court-scheduled-time-preview')}
                        <div id="court-scheduled-time-preview" class="schedule-time-preview"></div>
                    </div>
                </div>

                <div class="ranked-form-section">
                    <span class="ranked-form-section-label">Match setup</span>
                    ${Ranked._matchTypeSelectHTML('court-scheduled')}
                    ${Ranked._doublesFormHTML('court-scheduled', partnerOptions)}
                </div>
                <input type="hidden" id="court-scheduled-fixed-court" value="${hasCourtId ? courtId : ''}">
                <button type="submit" class="btn-primary btn-full">Send Scheduled Challenge</button>
            </form>
        </div>`;
        if (typeof DateTimePicker !== 'undefined') {
            DateTimePicker.enhanceWithin(modal);
        }
        if (typeof SelectPicker !== 'undefined') {
            SelectPicker.enhanceById('court-scheduled-court', {
                searchPlaceholder: 'Search courts...',
                emptyMessage: 'No courts match your search.',
                searchThreshold: 8,
            });
        }
        Ranked._initSchedulePicker('court-scheduled-time', 'court-scheduled-time-preview');
        Ranked._bindChallengePartnerSelectors('court-scheduled', 'court-scheduled-opponent');
    },

    async createCourtScheduledChallenge(e) {
        e.preventDefault();
        const currentUser = Ranked._currentUser();
        const targetUserId = parseInt(document.getElementById('court-scheduled-opponent').value, 10);
        const scheduledFor = document.getElementById('court-scheduled-time').value;
        if (!targetUserId) { App.toast('Pick an opponent.', 'error'); return; }
        if (!scheduledFor) { App.toast('Pick a scheduled time.', 'error'); return; }

        const fixedCourt = document.getElementById('court-scheduled-fixed-court')?.value;
        const courtId = fixedCourt
            ? parseInt(fixedCourt, 10)
            : parseInt(document.getElementById('court-scheduled-court')?.value, 10);
        if (!Number.isFinite(courtId) || courtId <= 0) {
            App.toast('Please select a court.', 'error');
            return;
        }

        const parsed = Ranked._parseDoublesTeams('court-scheduled', currentUser.id, targetUserId);
        if (!parsed) return;

        const btn = e.target.querySelector('button[type="submit"]');
        if (btn) btn.disabled = true;
        try {
            await API.post('/api/ranked/challenge/scheduled', {
                court_id: courtId,
                match_type: parsed.matchType,
                team1: parsed.team1,
                team2: parsed.team2,
                scheduled_for: scheduledFor,
                source: 'friends_challenge',
            });
            document.getElementById('match-modal').style.display = 'none';
            App.toast('Scheduled ranked challenge sent.');
            Ranked.loadPendingConfirmations();
            if (courtId) Ranked.loadCourtRanked(courtId);
        } catch (err) {
            if (btn) btn.disabled = false;
            App.toast(err.message || 'Failed to schedule ranked challenge', 'error');
        }
    },

    // ── Scheduled Challenge from Leaderboard ────────────────────────

    async openScheduledChallengeModal(targetUserId, source = 'scheduled_challenge') {
        try {
            if (!App.friendsList || !App.friendsList.length) {
                await App.loadFriendsCache();
            }
            const [courtsRes, targetProfile] = await Promise.all([
                API.get(App.buildCourtsQuery()),
                API.get(`/api/auth/profile/${targetUserId}`),
            ]);
            const courts = [...(courtsRes.courts || [])].sort((a, b) => {
                const aName = String(a.name || '').toLowerCase();
                const bName = String(b.name || '').toLowerCase();
                return aName.localeCompare(bName);
            });
            const target = targetProfile.user || {};
            const friends = [...(App.friendsList || [])].sort((a, b) => {
                const aName = String(a.name || a.username || '').toLowerCase();
                const bName = String(b.name || b.username || '').toLowerCase();
                return aName.localeCompare(bName);
            });
            const extraOptions = friends
                .filter(f => f.id !== targetUserId)
                .map(f => `<option value="${f.id}">${Ranked._e(f.name || f.username)}</option>`)
                .join('');
            const courtsOptions = courts.map(c =>
                `<option value="${c.id}">${Ranked._e(c.name)} — ${Ranked._e(c.city)}</option>`
            ).join('');

            const modal = document.getElementById('match-modal');
            modal.style.display = 'flex';
            modal.innerHTML = `
            <div class="modal-content ranked-challenge-modal">
                <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
                <h2>Schedule Ranked Challenge</h2>
                <p class="ranked-challenge-subtitle">Challenge a friend from profile or leaderboard with one clean flow.</p>
                <form class="ranked-challenge-form" onsubmit="Ranked.createScheduledChallenge(event, ${targetUserId}, '${source}')">
                    <div class="ranked-form-section">
                        <span class="ranked-form-section-label">Court and opponent</span>
                        <div class="form-group">
                            <label>Court</label>
                            <select id="scheduled-challenge-court" required>
                                <option value="">Select court...</option>
                                ${courtsOptions}
                            </select>
                        </div>
                        <p class="muted">Opponent: <strong>${Ranked._e(target.name || target.username || 'Player')}</strong></p>
                    </div>

                    <div class="ranked-form-section">
                        <span class="ranked-form-section-label">Schedule</span>
                        <div class="form-group">
                            <label>Scheduled Time</label>
                            <input type="datetime-local" id="scheduled-challenge-time" value="${Ranked._defaultScheduleTime()}" required>
                            ${Ranked._scheduleQuickPicksHTML('scheduled-challenge-time', 'scheduled-challenge-time-preview')}
                            <div id="scheduled-challenge-time-preview" class="schedule-time-preview"></div>
                        </div>
                    </div>

                    <div class="ranked-form-section">
                        <span class="ranked-form-section-label">Match setup</span>
                        ${Ranked._matchTypeSelectHTML('scheduled-challenge', !extraOptions)}
                        ${Ranked._doublesFormHTML('scheduled-challenge', extraOptions)}
                    </div>
                    <button type="submit" class="btn-primary btn-full">Send Scheduled Challenge</button>
                </form>
            </div>`;
            if (typeof DateTimePicker !== 'undefined') {
                DateTimePicker.enhanceWithin(modal);
            }
            if (typeof SelectPicker !== 'undefined') {
                SelectPicker.enhanceById('scheduled-challenge-court', {
                    searchPlaceholder: 'Search courts...',
                    emptyMessage: 'No courts match your search.',
                    searchThreshold: 8,
                });
            }
            Ranked._initSchedulePicker('scheduled-challenge-time', 'scheduled-challenge-time-preview');
            Ranked._bindChallengePartnerSelectors('scheduled-challenge', '', Number(targetUserId));
        } catch {
            App.toast('Unable to open scheduled challenge', 'error');
        }
    },

    async createScheduledChallenge(e, targetUserId, source = 'scheduled_challenge') {
        e.preventDefault();
        const currentUser = Ranked._currentUser();
        const courtId = parseInt(document.getElementById('scheduled-challenge-court').value, 10);
        const scheduledFor = document.getElementById('scheduled-challenge-time').value;
        if (!Number.isFinite(courtId) || courtId <= 0) { App.toast('Pick a court.', 'error'); return; }
        if (!scheduledFor) { App.toast('Pick a scheduled time.', 'error'); return; }

        const parsed = Ranked._parseDoublesTeams('scheduled-challenge', currentUser.id, targetUserId);
        if (!parsed) return;

        const btn = e.target.querySelector('button[type="submit"]');
        if (btn) btn.disabled = true;
        try {
            await API.post('/api/ranked/challenge/scheduled', {
                court_id: courtId,
                match_type: parsed.matchType,
                team1: parsed.team1,
                team2: parsed.team2,
                scheduled_for: scheduledFor,
                source,
            });
            document.getElementById('match-modal').style.display = 'none';
            App.toast('Scheduled ranked challenge sent.');
            Ranked.loadPendingConfirmations();
            if (Ranked.currentCourtId) Ranked.loadCourtRanked(Ranked.currentCourtId);
        } catch (err) {
            if (btn) btn.disabled = false;
            App.toast(err.message || 'Failed to schedule challenge', 'error');
        }
    },
});
