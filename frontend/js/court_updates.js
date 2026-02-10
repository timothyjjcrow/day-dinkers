/**
 * Court update contributions — user submissions + reviewer queue actions.
 */
const CourtUpdates = {
    currentCourtId: null,
    maxImages: 8,
    maxImageBytes: 2 * 1024 * 1024,

    openModal(courtId, courtName) {
        const token = localStorage.getItem('token');
        if (!token) {
            Auth.showModal();
            return;
        }

        CourtUpdates.currentCourtId = courtId;
        const modal = document.getElementById('court-update-modal');
        const title = document.getElementById('court-update-title');
        const form = document.getElementById('court-update-form');
        const hiddenCourtId = document.getElementById('court-update-court-id');
        const imageRows = document.getElementById('court-update-image-rows');
        const eventRows = document.getElementById('court-update-event-rows');

        if (!modal || !form || !hiddenCourtId || !title || !imageRows || !eventRows) return;

        form.reset();
        hiddenCourtId.value = String(courtId);
        title.textContent = `Suggest Update: ${courtName}`;
        imageRows.innerHTML = '';
        eventRows.innerHTML = '';
        CourtUpdates.addImageRow();
        CourtUpdates.addEventRow();

        modal.style.display = 'flex';
        CourtUpdates.loadMySubmissions(courtId);
    },

    hideModal() {
        const modal = document.getElementById('court-update-modal');
        if (modal) modal.style.display = 'none';
    },

    addImageRow(url = '', caption = '') {
        const rows = document.getElementById('court-update-image-rows');
        if (!rows) return;
        const row = document.createElement('div');
        row.className = 'court-update-row court-update-image-row';
        row.innerHTML = `
            <input type="url" class="court-update-image-url" placeholder="https://example.com/court-photo.jpg" value="${CourtUpdates._escapeAttr(url)}">
            <input type="text" class="court-update-image-caption" placeholder="Caption (optional)" value="${CourtUpdates._escapeAttr(caption)}">
            <button type="button" class="btn-secondary btn-sm" onclick="CourtUpdates.removeRow(this)">Remove</button>
        `;
        rows.appendChild(row);
    },

    addEventRow() {
        const rows = document.getElementById('court-update-event-rows');
        if (!rows) return;
        const row = document.createElement('div');
        row.className = 'court-update-row court-update-event-row';
        row.innerHTML = `
            <div class="form-row">
                <div class="form-group">
                    <label>Event title</label>
                    <input type="text" class="court-update-event-title" placeholder="Open tournament">
                </div>
                <div class="form-group">
                    <label>Organizer</label>
                    <input type="text" class="court-update-event-organizer" placeholder="Club / organizer">
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Start time</label>
                    <input type="datetime-local" class="court-update-event-start">
                </div>
                <div class="form-group">
                    <label>End time</label>
                    <input type="datetime-local" class="court-update-event-end">
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Contact</label>
                    <input type="text" class="court-update-event-contact" placeholder="Email / phone">
                </div>
                <div class="form-group">
                    <label>Event link</label>
                    <input type="url" class="court-update-event-link" placeholder="https://...">
                </div>
            </div>
            <div class="form-group">
                <label>Recurring pattern</label>
                <input type="text" class="court-update-event-recurring" placeholder="Weekly, monthly, one-time...">
            </div>
            <div class="form-group">
                <label>Description</label>
                <textarea rows="2" class="court-update-event-description" placeholder="Details, level, signup notes..."></textarea>
            </div>
            <button type="button" class="btn-secondary btn-sm" onclick="CourtUpdates.removeRow(this)">Remove Event</button>
        `;
        rows.appendChild(row);
    },

    removeRow(buttonEl) {
        const row = buttonEl.closest('.court-update-row');
        if (row) row.remove();
    },

    async submit(e) {
        e.preventDefault();
        const token = localStorage.getItem('token');
        if (!token) {
            Auth.showModal();
            return;
        }

        const form = e.target;
        const courtId = parseInt(document.getElementById('court-update-court-id')?.value || '0');
        if (!courtId) {
            App.toast('Court not found for this update', 'error');
            return;
        }

        const submitBtn = form.querySelector('button[type="submit"]');
        const originalBtnText = submitBtn ? submitBtn.textContent : '';
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.textContent = 'Submitting...';
        }

        try {
            const payload = await CourtUpdates._buildPayload(form);
            const res = await API.post(`/api/courts/${courtId}/updates`, payload);
            App.toast(res.message || 'Update submitted for review.');

            await CourtUpdates.loadMySubmissions(courtId);
            CourtUpdates.hideModal();

            // Refresh active court details to show latest approved info if auto-applied.
            if (MapView.currentCourtId === courtId) {
                if (App.currentView === 'court-detail') await MapView._refreshFullPage(courtId);
                else await MapView.openCourtDetail(courtId);
            }
            MapView.loadCourts();
        } catch (err) {
            if (err.details && Array.isArray(err.details) && err.details.length) {
                App.toast(err.details[0], 'error');
            } else {
                App.toast(err.message || 'Failed to submit update', 'error');
            }
        } finally {
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.textContent = originalBtnText || 'Send Update For Review';
            }
        }
    },

    async _buildPayload(form) {
        const get = (name) => (form.elements[name]?.value || '').trim();
        const pick = (name) => {
            const v = get(name);
            return v ? v : '';
        };

        const location = {
            address: pick('loc_address'),
            city: pick('loc_city'),
            state: pick('loc_state'),
            zip_code: pick('loc_zip'),
            latitude: pick('loc_latitude'),
            longitude: pick('loc_longitude'),
        };

        const courtInfo = {
            name: pick('court_name'),
            description: pick('court_description'),
            num_courts: pick('court_num_courts'),
            surface_type: pick('court_surface_type'),
            fees: pick('court_fees'),
            phone: pick('court_phone'),
            website: pick('court_website'),
            email: pick('court_email'),
            skill_levels: pick('court_skill_levels'),
            court_type: pick('court_type'),
        };
        const indoorValue = pick('court_indoor');
        if (indoorValue !== '') courtInfo.indoor = indoorValue;

        // Amenity checkboxes only submit explicit "true" updates when checked.
        if (form.elements.amenity_lighted?.checked) courtInfo.lighted = true;
        if (form.elements.amenity_has_restrooms?.checked) courtInfo.has_restrooms = true;
        if (form.elements.amenity_has_parking?.checked) courtInfo.has_parking = true;
        if (form.elements.amenity_has_water?.checked) courtInfo.has_water = true;
        if (form.elements.amenity_has_pro_shop?.checked) courtInfo.has_pro_shop = true;
        if (form.elements.amenity_has_ball_machine?.checked) courtInfo.has_ball_machine = true;
        if (form.elements.amenity_wheelchair_accessible?.checked) courtInfo.wheelchair_accessible = true;
        if (form.elements.amenity_nets_provided?.checked) courtInfo.nets_provided = true;
        if (form.elements.amenity_paddle_rental?.checked) courtInfo.paddle_rental = true;

        const hours = {
            hours: pick('hours_text'),
            open_play_schedule: pick('open_play_schedule'),
            hours_notes: pick('hours_notes'),
        };

        const communityNotes = {
            location_notes: pick('location_notes'),
            parking_notes: pick('parking_notes'),
            access_notes: pick('access_notes'),
            court_rules: pick('court_rules'),
            best_times: pick('best_times'),
            closure_notes: pick('closure_notes'),
            additional_info: pick('additional_info'),
        };

        const images = await CourtUpdates._collectImages();
        const events = CourtUpdates._collectEvents();

        return {
            summary: pick('summary'),
            source_notes: pick('source_notes'),
            confidence_level: pick('confidence_level') || 'medium',
            location,
            court_info: courtInfo,
            hours,
            community_notes: communityNotes,
            images,
            events,
        };
    },

    async _collectImages() {
        const images = [];
        document.querySelectorAll('.court-update-image-row').forEach(row => {
            const url = (row.querySelector('.court-update-image-url')?.value || '').trim();
            const caption = (row.querySelector('.court-update-image-caption')?.value || '').trim();
            if (!url) return;
            images.push({ image_url: url, caption });
        });

        const fileInput = document.getElementById('court-update-image-files');
        const fileItems = Array.from(fileInput?.files || []);
        const remainingSlots = Math.max(0, CourtUpdates.maxImages - images.length);
        if (fileItems.length > remainingSlots) {
            throw new Error(`You can upload up to ${CourtUpdates.maxImages} images total.`);
        }

        for (const file of fileItems) {
            if (!file.type.startsWith('image/')) {
                throw new Error(`"${file.name}" is not an image file.`);
            }
            if (file.size > CourtUpdates.maxImageBytes) {
                throw new Error(`"${file.name}" is too large. Keep each image under 2MB.`);
            }
            const dataUrl = await CourtUpdates._fileToDataUrl(file);
            images.push({ image_url: dataUrl, caption: file.name });
        }
        return images;
    },

    _collectEvents() {
        const events = [];
        document.querySelectorAll('.court-update-event-row').forEach(row => {
            const title = (row.querySelector('.court-update-event-title')?.value || '').trim();
            const organizer = (row.querySelector('.court-update-event-organizer')?.value || '').trim();
            const start_time = (row.querySelector('.court-update-event-start')?.value || '').trim();
            const end_time = (row.querySelector('.court-update-event-end')?.value || '').trim();
            const contact = (row.querySelector('.court-update-event-contact')?.value || '').trim();
            const link = (row.querySelector('.court-update-event-link')?.value || '').trim();
            const recurring = (row.querySelector('.court-update-event-recurring')?.value || '').trim();
            const description = (row.querySelector('.court-update-event-description')?.value || '').trim();

            const hasAnyValue = title || organizer || start_time || end_time || contact || link || recurring || description;
            if (!hasAnyValue) return;
            if (!title || !start_time) {
                throw new Error('Each event needs at least a title and start time.');
            }

            events.push({
                title,
                organizer,
                start_time,
                end_time,
                contact,
                link,
                recurring,
                description,
            });
        });
        return events;
    },

    _fileToDataUrl(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(new Error(`Failed to read image: ${file.name}`));
            reader.readAsDataURL(file);
        });
    },

    async loadMySubmissions(courtId) {
        const list = document.getElementById('court-update-my-submissions');
        if (!list) return;
        list.innerHTML = '<p class="muted">Loading...</p>';
        try {
            const res = await API.get(`/api/courts/${courtId}/updates/mine`);
            const submissions = res.submissions || [];
            if (!submissions.length) {
                list.innerHTML = '<p class="muted">No submissions yet for this court.</p>';
                return;
            }
            list.innerHTML = submissions.map(s => {
                const created = s.created_at ? new Date(s.created_at).toLocaleString() : '';
                return `
                    <div class="court-update-status-card">
                        <div>
                            <strong>${CourtUpdates._escapeHtml(s.summary || 'Court update')}</strong>
                            <p class="muted">${created}</p>
                        </div>
                        <span class="court-update-status ${s.status}">${s.status}</span>
                    </div>
                `;
            }).join('');
        } catch {
            list.innerHTML = '<p class="muted">Unable to load your submissions.</p>';
        }
    },

    async loadReviewerPanel() {
        const section = document.getElementById('profile-court-review-section');
        if (!section) return;
        try {
            const status = await API.get('/api/courts/updates/reviewer-status');
            if (!status.is_reviewer) {
                section.style.display = 'none';
                return;
            }
            section.style.display = 'block';
            const note = document.getElementById('profile-review-auto-apply-note');
            if (note) {
                note.textContent = status.auto_apply_enabled
                    ? `Auto-apply enabled (threshold ${status.auto_apply_threshold}).`
                    : 'Auto-apply is disabled. Manual review required.';
            }
            CourtUpdates.refreshReviewQueue();
        } catch {
            section.style.display = 'none';
        }
    },

    async refreshReviewQueue() {
        const list = document.getElementById('profile-court-review-list');
        const statusFilter = document.getElementById('profile-review-status-filter');
        if (!list || !statusFilter) return;

        const status = statusFilter.value || 'pending';
        list.innerHTML = '<p class="muted">Loading review queue...</p>';
        try {
            const res = await API.get(`/api/courts/updates/review?status=${encodeURIComponent(status)}&limit=40`);
            const items = res.submissions || [];
            if (!items.length) {
                list.innerHTML = '<p class="muted">No submissions in this queue.</p>';
                return;
            }
            list.innerHTML = items.map(item => CourtUpdates._reviewCardHTML(item)).join('');
        } catch (err) {
            list.innerHTML = `<p class="muted">${CourtUpdates._escapeHtml(err.message || 'Unable to load review queue')}</p>`;
        }
    },

    _reviewCardHTML(item) {
        const payload = item.payload || {};
        const sections = [];
        if (Object.keys(payload.location || {}).length) sections.push('Location');
        if (Object.keys(payload.court_info || {}).length) sections.push('Court info');
        if (Object.keys(payload.hours || {}).length) sections.push('Hours');
        if (Object.keys(payload.community_notes || {}).length) sections.push('Community notes');
        if ((payload.images || []).length) sections.push(`${payload.images.length} image(s)`);
        if ((payload.events || []).length) sections.push(`${payload.events.length} event(s)`);

        const analysis = item.analysis || {};
        const created = item.created_at ? new Date(item.created_at).toLocaleString() : '';
        const reviewerActions = item.status === 'pending'
            ? `
                <div class="court-review-actions">
                    <button class="btn-primary btn-sm" onclick="CourtUpdates.reviewSubmission(${item.id}, 'approve')">Approve</button>
                    <button class="btn-danger btn-sm" onclick="CourtUpdates.reviewSubmission(${item.id}, 'reject')">Reject</button>
                </div>
              `
            : '';

        return `
            <div class="court-review-card">
                <div class="court-review-top">
                    <strong>${CourtUpdates._escapeHtml(item.court_name || 'Court')} (${CourtUpdates._escapeHtml(item.court_city || 'Unknown city')})</strong>
                    <span class="court-update-status ${item.status}">${item.status}</span>
                </div>
                <p>${CourtUpdates._escapeHtml(item.summary || '')}</p>
                <p class="muted">Submitted ${created} · by ${CourtUpdates._escapeHtml(item.submitted_by?.username || `User #${item.user_id}`)}</p>
                <p class="muted">Sections: ${CourtUpdates._escapeHtml(sections.join(', ') || 'Summary only')}</p>
                <p class="muted">Analysis: score ${analysis.score ?? 'n/a'} · ${CourtUpdates._escapeHtml(analysis.recommendation || 'manual review')}</p>
                ${(analysis.flags || []).length ? `<p class="muted">Flags: ${CourtUpdates._escapeHtml((analysis.flags || []).join('; '))}</p>` : ''}
                ${item.reviewer_notes ? `<p class="muted">Reviewer note: ${CourtUpdates._escapeHtml(item.reviewer_notes)}</p>` : ''}
                ${reviewerActions}
            </div>
        `;
    },

    async reviewSubmission(submissionId, action) {
        const reviewerNotes = prompt(
            action === 'approve'
                ? 'Optional note for approval'
                : 'Optional reason for rejection',
            ''
        );
        if (reviewerNotes === null) return;

        try {
            const res = await API.post(`/api/courts/updates/${submissionId}/review`, {
                action,
                reviewer_notes: reviewerNotes,
            });
            App.toast(res.message || `Submission ${action}d.`);
            CourtUpdates.refreshReviewQueue();
            if (MapView.currentCourtId) {
                if (App.currentView === 'court-detail') MapView._refreshFullPage(MapView.currentCourtId);
                else MapView.openCourtDetail(MapView.currentCourtId);
            }
            MapView.loadCourts();
        } catch (err) {
            App.toast(err.message || 'Review action failed', 'error');
        }
    },

    _escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    },

    _escapeAttr(value) {
        return CourtUpdates._escapeHtml(value).replace(/`/g, '&#96;');
    },
};

document.addEventListener('click', (e) => {
    const modal = document.getElementById('court-update-modal');
    if (!modal) return;
    if (e.target === modal) CourtUpdates.hideModal();
});
