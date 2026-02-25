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
            <div class="modal-content">
                <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
                <h2>Challenge Player</h2>
                <form onsubmit="Ranked.createCourtChallenge(event, ${courtId}, ${targetUserId})">
                    ${Ranked._matchTypeSelectHTML('court-challenge', !extraOptions)}
                    <p class="muted">You: <strong>${Ranked._e(me.name || me.username)}</strong></p>
                    <p class="muted">Opponent: <strong>${Ranked._e(target.name || target.username)}</strong></p>
                    ${Ranked._doublesFormHTML('court-challenge', extraOptions)}
                    <button type="submit" class="btn-primary btn-full">Send Challenge</button>
                </form>
            </div>`;
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

        let courtSelectHTML = '';
        const hasCourtId = courtId && Number(courtId) > 0;
        if (!hasCourtId) {
            try {
                const courtsRes = await API.get(App.buildCourtsQuery());
                const courts = courtsRes.courts || [];
                const courtsOptions = courts.map(c =>
                    `<option value="${c.id}">${Ranked._e(c.name)} — ${Ranked._e(c.city)}</option>`
                ).join('');
                courtSelectHTML = `<div class="form-group">
                    <label>Court</label>
                    <select id="court-scheduled-court" required>${courtsOptions}</select>
                </div>`;
            } catch { /* proceed without court list */ }
        }

        const modal = document.getElementById('match-modal');
        modal.style.display = 'flex';
        modal.innerHTML = `
        <div class="modal-content">
            <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
            <h2>Schedule Ranked Challenge</h2>
            <form onsubmit="Ranked.createCourtScheduledChallenge(event)">
                ${courtSelectHTML}
                <div class="form-group">
                    <label>Opponent</label>
                    <select id="court-scheduled-opponent" required>
                        <option value="">Select opponent...</option>
                        ${opponentOptions}
                    </select>
                </div>
                <div class="form-group">
                    <label>Scheduled Time</label>
                    <input type="datetime-local" id="court-scheduled-time" value="${Ranked._defaultScheduleTime()}" required>
                </div>
                ${Ranked._matchTypeSelectHTML('court-scheduled')}
                ${Ranked._doublesFormHTML('court-scheduled', partnerOptions)}
                <input type="hidden" id="court-scheduled-fixed-court" value="${hasCourtId ? courtId : ''}">
                <button type="submit" class="btn-primary btn-full">Send Scheduled Challenge</button>
            </form>
        </div>`;
        if (typeof DateTimePicker !== 'undefined') {
            DateTimePicker.enhanceWithin(modal);
        }
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

            const modal = document.getElementById('match-modal');
            modal.style.display = 'flex';
            modal.innerHTML = `
            <div class="modal-content">
                <button class="modal-close" onclick="document.getElementById('match-modal').style.display='none'">&times;</button>
                <h2>Schedule Ranked Challenge</h2>
                <form onsubmit="Ranked.createScheduledChallenge(event, ${targetUserId}, '${source}')">
                    <div class="form-group">
                        <label>Court</label>
                        <select id="scheduled-challenge-court" required>
                            ${courtsOptions}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Scheduled Time</label>
                        <input type="datetime-local" id="scheduled-challenge-time" value="${Ranked._defaultScheduleTime()}" required>
                    </div>
                    ${Ranked._matchTypeSelectHTML('scheduled-challenge', !extraOptions)}
                    <p class="muted">Opponent: <strong>${Ranked._e(target.name || target.username || 'Player')}</strong></p>
                    ${Ranked._doublesFormHTML('scheduled-challenge', extraOptions)}
                    <button type="submit" class="btn-primary btn-full">Send Scheduled Challenge</button>
                </form>
            </div>`;
            if (typeof DateTimePicker !== 'undefined') {
                DateTimePicker.enhanceWithin(modal);
            }
        } catch {
            App.toast('Unable to open scheduled challenge', 'error');
        }
    },

    async createScheduledChallenge(e, targetUserId, source = 'scheduled_challenge') {
        e.preventDefault();
        const currentUser = Ranked._currentUser();
        const courtId = parseInt(document.getElementById('scheduled-challenge-court').value, 10);
        const scheduledFor = document.getElementById('scheduled-challenge-time').value;
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
