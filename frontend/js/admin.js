/**
 * Admin review console for court update submissions and court reports.
 */
const AdminPage = {
    activeTab: 'updates',
    submissions: [],
    filteredSubmissions: [],
    selectedSubmissionId: null,
    reports: [],
    filteredReports: [],
    selectedReportId: null,
    timelineItems: [],
    filteredTimelineItems: [],
    selectedTimelineKey: null,
    timelineSelections: new Set(),
    lastBulkResult: null,
    courtCache: {},

    async load() {
        const container = document.getElementById('admin-page-content');
        if (!container) return;

        const token = localStorage.getItem('token');
        if (!token) {
            container.innerHTML = `
                <div class="empty-state">
                    <h3>Sign in required</h3>
                    <p>Sign in with an admin account to manage court updates.</p>
                    <button class="btn-primary" onclick="Auth.showModal()">Sign In</button>
                </div>
            `;
            return;
        }

        container.innerHTML = AdminPage._layoutHTML();
        AdminPage.lastBulkResult = null;
        AdminPage.setTab(AdminPage.activeTab);
        await AdminPage.refresh();
    },

    _layoutHTML() {
        return `
            <div class="admin-page">
                <div class="view-header">
                    <h2>🛡️ Admin Review Console</h2>
                    <button class="btn-secondary btn-sm" onclick="AdminPage.refresh()">Refresh</button>
                </div>
                <p class="muted">Review and publish community-submitted court updates and court issue reports.</p>

                <div class="admin-tabs">
                    <button id="admin-tab-updates" class="btn-secondary btn-sm" onclick="AdminPage.setTab('updates')">Court Updates</button>
                    <button id="admin-tab-reports" class="btn-secondary btn-sm" onclick="AdminPage.setTab('reports')">Court Reports</button>
                    <button id="admin-tab-timeline" class="btn-secondary btn-sm" onclick="AdminPage.setTab('timeline')">Activity Timeline</button>
                </div>

                <div id="admin-updates-section">
                    <div class="admin-filters">
                        <div class="form-group">
                            <label>Status</label>
                            <select id="admin-filter-status" onchange="AdminPage.applyFilters()">
                                <option value="pending">Pending</option>
                                <option value="approved">Approved</option>
                                <option value="rejected">Rejected</option>
                                <option value="all">All</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Search</label>
                            <input id="admin-filter-search" type="text" placeholder="Court, submitter, summary..." oninput="AdminPage.applyFilters()">
                        </div>
                        <div class="admin-stats" id="admin-review-stats"></div>
                    </div>

                    <div class="admin-review-grid">
                        <div class="admin-review-list-wrap">
                            <div id="admin-review-list" class="admin-review-list"></div>
                        </div>
                        <div class="admin-review-detail-wrap">
                            <div id="admin-review-detail" class="admin-review-detail"></div>
                        </div>
                    </div>
                </div>

                <div id="admin-reports-section" style="display:none">
                    <div class="admin-filters">
                        <div class="form-group">
                            <label>Status</label>
                            <select id="admin-report-filter-status" onchange="AdminPage.applyReportFilters()">
                                <option value="pending">Pending</option>
                                <option value="resolved">Resolved</option>
                                <option value="dismissed">Dismissed</option>
                                <option value="all">All</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Search</label>
                            <input id="admin-report-filter-search" type="text" placeholder="Court, reason, reporter..." oninput="AdminPage.applyReportFilters()">
                        </div>
                        <div class="admin-stats" id="admin-report-stats"></div>
                    </div>

                    <div class="admin-review-grid">
                        <div class="admin-review-list-wrap">
                            <div id="admin-report-list" class="admin-review-list"></div>
                        </div>
                        <div class="admin-review-detail-wrap">
                            <div id="admin-report-detail" class="admin-review-detail"></div>
                        </div>
                    </div>
                </div>

                <div id="admin-timeline-section" style="display:none">
                    <div class="admin-filters">
                        <div class="form-group">
                            <label>Type</label>
                            <select id="admin-timeline-filter-type" onchange="AdminPage.applyTimelineFilters()">
                                <option value="all">All Activity</option>
                                <option value="update">Court Updates</option>
                                <option value="report">Court Reports</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Status</label>
                            <select id="admin-timeline-filter-status" onchange="AdminPage.applyTimelineFilters()">
                                <option value="all">All Statuses</option>
                                <option value="pending">Pending</option>
                                <option value="approved">Approved</option>
                                <option value="rejected">Rejected</option>
                                <option value="resolved">Resolved</option>
                                <option value="dismissed">Dismissed</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Search</label>
                            <input id="admin-timeline-filter-search" type="text" placeholder="Court, summary, reason, user..." oninput="AdminPage.applyTimelineFilters()">
                        </div>
                        <div class="admin-stats" id="admin-timeline-stats"></div>
                    </div>

                    <div class="admin-bulk-actions">
                        <span id="admin-timeline-selected-count" class="muted">0 selected</span>
                        <button id="admin-bulk-select-pending" class="btn-secondary btn-sm" onclick="AdminPage.selectAllPendingTimeline()">Select All Pending</button>
                        <button id="admin-bulk-clear" class="btn-secondary btn-sm" onclick="AdminPage.clearTimelineSelection()">Clear</button>
                        <button id="admin-bulk-approve" class="btn-primary btn-sm" onclick="AdminPage.bulkReviewTimeline('approve')">Approve Updates</button>
                        <button id="admin-bulk-reject" class="btn-danger btn-sm" onclick="AdminPage.bulkReviewTimeline('reject')">Reject Updates</button>
                        <button id="admin-bulk-resolve" class="btn-primary btn-sm" onclick="AdminPage.bulkReviewTimeline('resolve')">Resolve Reports</button>
                        <button id="admin-bulk-dismiss" class="btn-danger btn-sm" onclick="AdminPage.bulkReviewTimeline('dismiss')">Dismiss Reports</button>
                    </div>
                    <div id="admin-bulk-result" class="admin-bulk-result" style="display:none"></div>

                    <div class="admin-review-grid">
                        <div class="admin-review-list-wrap">
                            <div id="admin-timeline-list" class="admin-review-list"></div>
                        </div>
                        <div class="admin-review-detail-wrap">
                            <div id="admin-timeline-detail" class="admin-review-detail"></div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    },

    setTab(tab) {
        AdminPage.activeTab = ['updates', 'reports', 'timeline'].includes(tab) ? tab : 'updates';
        const updatesSection = document.getElementById('admin-updates-section');
        const reportsSection = document.getElementById('admin-reports-section');
        const timelineSection = document.getElementById('admin-timeline-section');
        const updatesTab = document.getElementById('admin-tab-updates');
        const reportsTab = document.getElementById('admin-tab-reports');
        const timelineTab = document.getElementById('admin-tab-timeline');
        if (updatesSection) updatesSection.style.display = AdminPage.activeTab === 'updates' ? 'block' : 'none';
        if (reportsSection) reportsSection.style.display = AdminPage.activeTab === 'reports' ? 'block' : 'none';
        if (timelineSection) timelineSection.style.display = AdminPage.activeTab === 'timeline' ? 'block' : 'none';
        if (updatesTab) updatesTab.classList.toggle('active', AdminPage.activeTab === 'updates');
        if (reportsTab) reportsTab.classList.toggle('active', AdminPage.activeTab === 'reports');
        if (timelineTab) timelineTab.classList.toggle('active', AdminPage.activeTab === 'timeline');
    },

    async refresh() {
        await Promise.all([AdminPage.refreshUpdates(), AdminPage.refreshReports()]);
        AdminPage._buildTimelineItems();
        AdminPage._pruneTimelineSelection();
        AdminPage.applyTimelineFilters();
        AdminPage._renderBulkResult();
    },

    async refreshUpdates() {
        const list = document.getElementById('admin-review-list');
        const detail = document.getElementById('admin-review-detail');
        if (!list || !detail) return;

        list.innerHTML = '<p class="loading">Loading update submissions...</p>';
        detail.innerHTML = '<p class="muted">Select a submission to review details.</p>';
        try {
            const res = await API.get('/api/courts/updates/review?status=all&limit=100');
            AdminPage.submissions = res.submissions || [];
            AdminPage._renderSubmissionStats();
            AdminPage.applyFilters();
        } catch (err) {
            const msg = AdminPage._escapeHtml(err.message || 'Unable to load admin queue');
            list.innerHTML = `<p class="error">${msg}</p>`;
            detail.innerHTML = `<p class="error">${msg}</p>`;
        }
    },

    async refreshReports() {
        const list = document.getElementById('admin-report-list');
        const detail = document.getElementById('admin-report-detail');
        if (!list || !detail) return;

        list.innerHTML = '<p class="loading">Loading court reports...</p>';
        detail.innerHTML = '<p class="muted">Select a report to review details.</p>';
        try {
            const res = await API.get('/api/courts/reports?status=all&limit=100');
            AdminPage.reports = res.reports || [];
            AdminPage._renderReportStats();
            AdminPage.applyReportFilters();
        } catch (err) {
            const msg = AdminPage._escapeHtml(err.message || 'Unable to load court reports');
            list.innerHTML = `<p class="error">${msg}</p>`;
            detail.innerHTML = `<p class="error">${msg}</p>`;
        }
    },

    _renderSubmissionStats() {
        const statsEl = document.getElementById('admin-review-stats');
        if (!statsEl) return;
        const pending = AdminPage.submissions.filter(s => s.status === 'pending').length;
        const approved = AdminPage.submissions.filter(s => s.status === 'approved').length;
        const rejected = AdminPage.submissions.filter(s => s.status === 'rejected').length;
        statsEl.innerHTML = `
            <span class="admin-stat-chip">Pending: <strong>${pending}</strong></span>
            <span class="admin-stat-chip">Approved: <strong>${approved}</strong></span>
            <span class="admin-stat-chip">Rejected: <strong>${rejected}</strong></span>
        `;
    },

    _renderReportStats() {
        const statsEl = document.getElementById('admin-report-stats');
        if (!statsEl) return;
        const pending = AdminPage.reports.filter(r => r.status === 'pending').length;
        const resolved = AdminPage.reports.filter(r => r.status === 'resolved').length;
        const dismissed = AdminPage.reports.filter(r => r.status === 'dismissed').length;
        statsEl.innerHTML = `
            <span class="admin-stat-chip">Pending: <strong>${pending}</strong></span>
            <span class="admin-stat-chip">Resolved: <strong>${resolved}</strong></span>
            <span class="admin-stat-chip">Dismissed: <strong>${dismissed}</strong></span>
        `;
    },

    _buildTimelineItems() {
        const updates = (AdminPage.submissions || []).map(item => ({
            key: `update-${item.id}`,
            type: 'update',
            id: item.id,
            status: item.status,
            court_id: item.court_id,
            court_name: item.court_name,
            court_city: item.court_city,
            summary: item.summary,
            user: item.submitted_by,
            created_at: item.created_at,
            payload: item.payload,
            analysis: item.analysis,
            reviewer_notes: item.reviewer_notes,
            reviewed_at: item.reviewed_at,
            reviewed_by: item.reviewed_by,
        }));
        const reports = (AdminPage.reports || []).map(item => ({
            key: `report-${item.id}`,
            type: 'report',
            id: item.id,
            status: item.status,
            court_id: item.court_id,
            court_name: item.court_name,
            court_city: item.court_city,
            reason: item.reason,
            description: item.description,
            user: item.reported_by,
            created_at: item.created_at,
        }));

        AdminPage.timelineItems = [...updates, ...reports].sort((a, b) => {
            const ta = new Date(a.created_at || 0).getTime();
            const tb = new Date(b.created_at || 0).getTime();
            return tb - ta;
        });
    },

    _pruneTimelineSelection() {
        const pendingKeys = new Set(
            AdminPage.timelineItems
                .filter(item => item.status === 'pending')
                .map(item => item.key)
        );
        for (const key of Array.from(AdminPage.timelineSelections)) {
            if (!pendingKeys.has(key)) AdminPage.timelineSelections.delete(key);
        }
    },

    _renderTimelineStats(items) {
        const statsEl = document.getElementById('admin-timeline-stats');
        if (!statsEl) return;
        const pending = items.filter(i => i.status === 'pending').length;
        const updates = items.filter(i => i.type === 'update').length;
        const reports = items.filter(i => i.type === 'report').length;
        statsEl.innerHTML = `
            <span class="admin-stat-chip">Items: <strong>${items.length}</strong></span>
            <span class="admin-stat-chip">Pending: <strong>${pending}</strong></span>
            <span class="admin-stat-chip">Updates: <strong>${updates}</strong></span>
            <span class="admin-stat-chip">Reports: <strong>${reports}</strong></span>
        `;
    },

    applyFilters() {
        const status = (document.getElementById('admin-filter-status')?.value || 'pending').toLowerCase();
        const query = (document.getElementById('admin-filter-search')?.value || '').trim().toLowerCase();

        let filtered = [...AdminPage.submissions];
        if (status !== 'all') filtered = filtered.filter(item => item.status === status);
        if (query) {
            filtered = filtered.filter(item => {
                const blob = [
                    item.court_name || '',
                    item.court_city || '',
                    item.summary || '',
                    item.submitted_by?.username || '',
                    item.submitted_by?.name || '',
                ].join(' ').toLowerCase();
                return blob.includes(query);
            });
        }
        AdminPage.filteredSubmissions = filtered;
        AdminPage._renderList();

        if (!filtered.length) {
            AdminPage.selectedSubmissionId = null;
            const detail = document.getElementById('admin-review-detail');
            if (detail) detail.innerHTML = '<p class="muted">No submissions match your filters.</p>';
            return;
        }

        if (!filtered.some(item => item.id === AdminPage.selectedSubmissionId)) {
            AdminPage.selectSubmission(filtered[0].id);
        } else {
            AdminPage._renderDetail();
        }
    },

    applyReportFilters() {
        const status = (document.getElementById('admin-report-filter-status')?.value || 'pending').toLowerCase();
        const query = (document.getElementById('admin-report-filter-search')?.value || '').trim().toLowerCase();

        let filtered = [...AdminPage.reports];
        if (status !== 'all') filtered = filtered.filter(item => item.status === status);
        if (query) {
            filtered = filtered.filter(item => {
                const blob = [
                    item.court_name || '',
                    item.court_city || '',
                    item.reason || '',
                    item.description || '',
                    item.reported_by?.username || '',
                    item.reported_by?.name || '',
                ].join(' ').toLowerCase();
                return blob.includes(query);
            });
        }
        AdminPage.filteredReports = filtered;
        AdminPage._renderReportList();

        if (!filtered.length) {
            AdminPage.selectedReportId = null;
            const detail = document.getElementById('admin-report-detail');
            if (detail) detail.innerHTML = '<p class="muted">No reports match your filters.</p>';
            return;
        }

        if (!filtered.some(item => item.id === AdminPage.selectedReportId)) {
            AdminPage.selectReport(filtered[0].id);
        } else {
            AdminPage._renderReportDetail();
        }
    },

    applyTimelineFilters() {
        const typeFilter = (document.getElementById('admin-timeline-filter-type')?.value || 'all').toLowerCase();
        const statusFilter = (document.getElementById('admin-timeline-filter-status')?.value || 'all').toLowerCase();
        const query = (document.getElementById('admin-timeline-filter-search')?.value || '').trim().toLowerCase();

        let filtered = [...AdminPage.timelineItems];
        if (typeFilter !== 'all') filtered = filtered.filter(item => item.type === typeFilter);
        if (statusFilter !== 'all') filtered = filtered.filter(item => item.status === statusFilter);
        if (query) {
            filtered = filtered.filter(item => {
                const blob = [
                    item.court_name || '',
                    item.court_city || '',
                    item.summary || '',
                    item.reason || '',
                    item.description || '',
                    item.user?.username || '',
                    item.user?.name || '',
                ].join(' ').toLowerCase();
                return blob.includes(query);
            });
        }
        AdminPage.filteredTimelineItems = filtered;
        AdminPage._renderTimelineStats(filtered);
        AdminPage._renderTimelineList();
        AdminPage._updateTimelineBulkState();

        if (!filtered.length) {
            AdminPage.selectedTimelineKey = null;
            const detail = document.getElementById('admin-timeline-detail');
            if (detail) detail.innerHTML = '<p class="muted">No timeline items match your filters.</p>';
            return;
        }

        if (!filtered.some(item => item.key === AdminPage.selectedTimelineKey)) {
            AdminPage.selectTimelineItem(filtered[0].key);
        } else {
            AdminPage._renderTimelineDetail();
        }
    },

    _renderList() {
        const list = document.getElementById('admin-review-list');
        if (!list) return;
        if (!AdminPage.filteredSubmissions.length) {
            list.innerHTML = '<p class="muted">No submissions found.</p>';
            return;
        }

        list.innerHTML = AdminPage.filteredSubmissions.map(item => {
            const isActive = item.id === AdminPage.selectedSubmissionId;
            const created = item.created_at ? new Date(item.created_at).toLocaleString() : '';
            return `
                <button class="admin-review-item ${isActive ? 'active' : ''}" onclick="AdminPage.selectSubmission(${item.id})">
                    <div class="admin-review-item-top">
                        <strong>${AdminPage._escapeHtml(item.court_name || 'Court')}</strong>
                        <span class="court-update-status ${item.status}">${item.status}</span>
                    </div>
                    <p>${AdminPage._escapeHtml(item.summary || '')}</p>
                    <p class="muted">${created} · ${AdminPage._escapeHtml(item.submitted_by?.username || `User #${item.user_id}`)}</p>
                </button>
            `;
        }).join('');
    },

    _renderReportList() {
        const list = document.getElementById('admin-report-list');
        if (!list) return;
        if (!AdminPage.filteredReports.length) {
            list.innerHTML = '<p class="muted">No court reports found.</p>';
            return;
        }

        list.innerHTML = AdminPage.filteredReports.map(item => {
            const isActive = item.id === AdminPage.selectedReportId;
            const created = item.created_at ? new Date(item.created_at).toLocaleString() : '';
            return `
                <button class="admin-review-item ${isActive ? 'active' : ''}" onclick="AdminPage.selectReport(${item.id})">
                    <div class="admin-review-item-top">
                        <strong>${AdminPage._escapeHtml(item.court_name || 'Court')}</strong>
                        <span class="court-update-status ${item.status}">${item.status}</span>
                    </div>
                    <p><strong>Reason:</strong> ${AdminPage._escapeHtml(item.reason || 'other')}</p>
                    <p class="muted">${created} · ${AdminPage._escapeHtml(item.reported_by?.username || `User #${item.user_id}`)}</p>
                </button>
            `;
        }).join('');
    },

    _renderTimelineList() {
        const list = document.getElementById('admin-timeline-list');
        if (!list) return;
        if (!AdminPage.filteredTimelineItems.length) {
            list.innerHTML = '<p class="muted">No activity items found.</p>';
            return;
        }

        list.innerHTML = AdminPage.filteredTimelineItems.map(item => {
            const isActive = item.key === AdminPage.selectedTimelineKey;
            const isSelected = AdminPage.timelineSelections.has(item.key);
            const created = item.created_at ? new Date(item.created_at).toLocaleString() : '';
            const headline = item.type === 'update'
                ? (item.summary || 'Court update submission')
                : `Report: ${item.reason || 'other'}`;
            return `
                <button class="admin-review-item ${isActive ? 'active' : ''}" onclick="AdminPage.selectTimelineItem('${item.key}')">
                    <div class="admin-review-item-top">
                        <div class="admin-item-title-wrap">
                            ${item.status === 'pending' ? `<input class="admin-item-checkbox" type="checkbox" ${isSelected ? 'checked' : ''} onclick="event.stopPropagation(); AdminPage.toggleTimelineSelection('${item.key}', this.checked)">` : '<span class="admin-item-checkbox-placeholder"></span>'}
                        <strong>${AdminPage._escapeHtml(item.court_name || 'Court')}</strong>
                        </div>
                        <div class="admin-item-tags">
                            <span class="admin-item-type ${item.type}">${item.type === 'update' ? 'Update' : 'Report'}</span>
                            <span class="court-update-status ${item.status}">${item.status}</span>
                        </div>
                    </div>
                    <p>${AdminPage._escapeHtml(headline)}</p>
                    <p class="muted">${created} · ${AdminPage._escapeHtml(item.user?.username || '')}</p>
                </button>
            `;
        }).join('');
    },

    toggleTimelineSelection(itemKey, checked) {
        const item = AdminPage.timelineItems.find(i => i.key === itemKey);
        if (!item || item.status !== 'pending') return;
        if (checked) AdminPage.timelineSelections.add(itemKey);
        else AdminPage.timelineSelections.delete(itemKey);
        AdminPage._updateTimelineBulkState();
    },

    clearTimelineSelection() {
        AdminPage.timelineSelections.clear();
        AdminPage._renderTimelineList();
        AdminPage._updateTimelineBulkState();
    },

    selectAllPendingTimeline() {
        const visiblePending = AdminPage.filteredTimelineItems.filter(item => item.status === 'pending');
        visiblePending.forEach(item => AdminPage.timelineSelections.add(item.key));
        AdminPage._renderTimelineList();
        AdminPage._updateTimelineBulkState();
    },

    _selectedPendingTimelineItems() {
        return AdminPage.filteredTimelineItems.filter(
            item => item.status === 'pending' && AdminPage.timelineSelections.has(item.key)
        );
    },

    _updateTimelineBulkState() {
        const countEl = document.getElementById('admin-timeline-selected-count');
        const selected = AdminPage._selectedPendingTimelineItems();
        const updateCount = selected.filter(item => item.type === 'update').length;
        const reportCount = selected.filter(item => item.type === 'report').length;
        if (countEl) {
            countEl.textContent = `${selected.length} pending selected (${updateCount} updates, ${reportCount} reports)`;
        }

        const btnApprove = document.getElementById('admin-bulk-approve');
        const btnReject = document.getElementById('admin-bulk-reject');
        const btnResolve = document.getElementById('admin-bulk-resolve');
        const btnDismiss = document.getElementById('admin-bulk-dismiss');
        const btnClear = document.getElementById('admin-bulk-clear');
        if (btnApprove) btnApprove.disabled = updateCount === 0;
        if (btnReject) btnReject.disabled = updateCount === 0;
        if (btnResolve) btnResolve.disabled = reportCount === 0;
        if (btnDismiss) btnDismiss.disabled = reportCount === 0;
        if (btnClear) btnClear.disabled = selected.length === 0;
    },

    clearBulkResult() {
        AdminPage.lastBulkResult = null;
        AdminPage._renderBulkResult();
    },

    _renderBulkResult() {
        const container = document.getElementById('admin-bulk-result');
        if (!container) return;
        const result = AdminPage.lastBulkResult;
        if (!result) {
            container.style.display = 'none';
            container.innerHTML = '';
            return;
        }

        const when = result.timestamp
            ? new Date(result.timestamp).toLocaleString()
            : '';
        const statusClass = result.failedCount > 0 ? 'partial' : 'success';
        const canRetry = result.failedCount > 0
            && ['approve', 'reject', 'resolve', 'dismiss'].includes(result.rawAction)
            && ['update', 'report'].includes(result.targetType);
        const failureRows = (result.failed || []).slice(0, 20).map(item => `
            <li>#${item.id}: ${AdminPage._escapeHtml(item.error || 'Unknown error')}</li>
        `).join('');

        container.style.display = 'block';
        container.innerHTML = `
            <div class="admin-bulk-result-card ${statusClass}">
                <div class="admin-bulk-result-top">
                    <strong>Last Bulk Action: ${AdminPage._escapeHtml(result.action || 'Action')}</strong>
                    <div class="admin-bulk-result-controls">
                        ${canRetry ? '<button class="btn-secondary btn-sm" onclick="AdminPage.retryLastBulkFailures()">Retry Failed Only</button>' : ''}
                        <button class="btn-text btn-sm" onclick="AdminPage.clearBulkResult()">Clear</button>
                    </div>
                </div>
                <p class="admin-bulk-result-summary">
                    Processed ${result.processedCount || 0} of ${result.totalCount || 0}
                    ${result.failedCount ? ` · Failed ${result.failedCount}` : ' · No failures'}
                    ${when ? ` · ${when}` : ''}
                </p>
                ${result.failedCount ? `
                    <div class="admin-bulk-result-failures">
                        <strong>Failed Items</strong>
                        <ul>${failureRows}</ul>
                        ${(result.failed || []).length > 20 ? '<p class="muted">Showing first 20 failures.</p>' : ''}
                    </div>
                ` : ''}
            </div>
        `;
    },

    async selectSubmission(submissionId) {
        AdminPage.selectedSubmissionId = submissionId;
        AdminPage._renderList();
        const selected = AdminPage.filteredSubmissions.find(item => item.id === submissionId)
            || AdminPage.submissions.find(item => item.id === submissionId);
        if (selected) await AdminPage._ensureCourtLoaded(selected.court_id);
        AdminPage._renderDetail();
    },

    async selectReport(reportId) {
        AdminPage.selectedReportId = reportId;
        AdminPage._renderReportList();
        const selected = AdminPage.filteredReports.find(item => item.id === reportId)
            || AdminPage.reports.find(item => item.id === reportId);
        if (selected) await AdminPage._ensureCourtLoaded(selected.court_id);
        AdminPage._renderReportDetail();
    },

    async selectTimelineItem(itemKey) {
        AdminPage.selectedTimelineKey = itemKey;
        AdminPage._renderTimelineList();
        const selected = AdminPage.filteredTimelineItems.find(item => item.key === itemKey)
            || AdminPage.timelineItems.find(item => item.key === itemKey);
        if (selected) await AdminPage._ensureCourtLoaded(selected.court_id);
        AdminPage._renderTimelineDetail();
    },

    async _ensureCourtLoaded(courtId) {
        if (!courtId || Object.prototype.hasOwnProperty.call(AdminPage.courtCache, courtId)) return;
        try {
            const res = await API.get(`/api/courts/${courtId}`);
            AdminPage.courtCache[courtId] = res.court;
        } catch {
            AdminPage.courtCache[courtId] = null;
        }
    },

    _renderDetail() {
        const detail = document.getElementById('admin-review-detail');
        if (!detail) return;

        const item = AdminPage.filteredSubmissions.find(s => s.id === AdminPage.selectedSubmissionId)
            || AdminPage.submissions.find(s => s.id === AdminPage.selectedSubmissionId);
        if (!item) {
            detail.innerHTML = '<p class="muted">Select a submission to review.</p>';
            return;
        }

        const payload = item.payload || {};
        const analysis = item.analysis || {};
        const created = item.created_at ? new Date(item.created_at).toLocaleString() : '';
        const reviewed = item.reviewed_at ? new Date(item.reviewed_at).toLocaleString() : '';
        const court = AdminPage.courtCache[item.court_id] || {};
        const community = court.community_info || {};
        const pendingAction = item.status === 'pending'
            ? `
                <div class="admin-review-actions">
                    <label>Reviewer Notes</label>
                    <textarea id="admin-review-notes" rows="3" placeholder="Optional approval/rejection note"></textarea>
                    <div class="admin-review-actions-row">
                        <button class="btn-primary" onclick="AdminPage.reviewSelected('approve')">Approve & Publish</button>
                        <button class="btn-danger" onclick="AdminPage.reviewSelected('reject')">Reject</button>
                    </div>
                </div>
              `
            : '';

        detail.innerHTML = `
            <div class="admin-detail-card">
                <div class="admin-detail-header">
                    <h3>${AdminPage._escapeHtml(item.court_name || 'Court')}</h3>
                    <span class="court-update-status ${item.status}">${item.status}</span>
                </div>
                <p>${AdminPage._escapeHtml(item.summary || '')}</p>
                <p class="muted">Submitted ${created} by ${AdminPage._escapeHtml(item.submitted_by?.username || `User #${item.user_id}`)}</p>
                ${reviewed ? `<p class="muted">Reviewed ${reviewed}${item.reviewed_by?.username ? ` by ${AdminPage._escapeHtml(item.reviewed_by.username)}` : ''}</p>` : ''}
                ${item.reviewer_notes ? `<p class="muted">Reviewer note: ${AdminPage._escapeHtml(item.reviewer_notes)}</p>` : ''}
                <div class="admin-detail-actions">
                    <button class="btn-secondary btn-sm" onclick="App.openCourtDetails(${item.court_id})">Open Court</button>
                </div>
            </div>

            <div class="admin-detail-card">
                <h4>AI Analysis</h4>
                <p class="muted">Score: ${analysis.score ?? 'n/a'} · Recommendation: ${AdminPage._escapeHtml(analysis.recommendation || 'manual review')}</p>
                ${(analysis.flags || []).length ? `<p class="muted">Flags: ${AdminPage._escapeHtml((analysis.flags || []).join('; '))}</p>` : '<p class="muted">No flags.</p>'}
            </div>

            <div class="admin-detail-card">
                <h4>Location</h4>
                ${AdminPage._keyValueRows(payload.location || {}, {
                    address: court.address,
                    city: court.city,
                    state: court.state,
                    zip_code: court.zip_code,
                    latitude: court.latitude,
                    longitude: court.longitude,
                })}
            </div>

            <div class="admin-detail-card">
                <h4>Court Info</h4>
                ${AdminPage._keyValueRows(payload.court_info || {}, {
                    name: court.name,
                    description: court.description,
                    num_courts: court.num_courts,
                    surface_type: court.surface_type,
                    court_type: court.court_type,
                    indoor: court.indoor,
                    lighted: court.lighted,
                    fees: court.fees,
                    phone: court.phone,
                    website: court.website,
                    email: court.email,
                    skill_levels: court.skill_levels,
                    has_restrooms: court.has_restrooms,
                    has_parking: court.has_parking,
                    has_water: court.has_water,
                    has_pro_shop: court.has_pro_shop,
                    has_ball_machine: court.has_ball_machine,
                    wheelchair_accessible: court.wheelchair_accessible,
                    nets_provided: court.nets_provided,
                    paddle_rental: court.paddle_rental,
                })}
            </div>

            <div class="admin-detail-card">
                <h4>Hours & Schedule</h4>
                ${AdminPage._keyValueRows(payload.hours || {}, {
                    hours: court.hours,
                    open_play_schedule: court.open_play_schedule,
                    hours_notes: community.hours_notes,
                })}
            </div>

            <div class="admin-detail-card">
                <h4>Community Notes</h4>
                ${AdminPage._keyValueRows(payload.community_notes || {}, {
                    location_notes: community.location_notes,
                    parking_notes: community.parking_notes,
                    access_notes: community.access_notes,
                    court_rules: community.court_rules,
                    best_times: community.best_times,
                    closure_notes: community.closure_notes,
                    additional_info: community.additional_info,
                })}
            </div>

            <div class="admin-detail-card">
                <h4>Images</h4>
                ${AdminPage._imagesHTML(payload.images || [])}
            </div>

            <div class="admin-detail-card">
                <h4>Events</h4>
                ${AdminPage._eventsHTML(payload.events || [])}
            </div>

            ${pendingAction}
        `;
    },

    _renderReportDetail() {
        const detail = document.getElementById('admin-report-detail');
        if (!detail) return;

        const item = AdminPage.filteredReports.find(r => r.id === AdminPage.selectedReportId)
            || AdminPage.reports.find(r => r.id === AdminPage.selectedReportId);
        if (!item) {
            detail.innerHTML = '<p class="muted">Select a report to review.</p>';
            return;
        }

        const created = item.created_at ? new Date(item.created_at).toLocaleString() : '';
        const court = AdminPage.courtCache[item.court_id] || {};
        const pendingAction = item.status === 'pending'
            ? `
                <div class="admin-review-actions">
                    <div class="admin-review-actions-row">
                        <button class="btn-primary" onclick="AdminPage.reviewSelectedReport('resolve')">Mark Resolved</button>
                        <button class="btn-danger" onclick="AdminPage.reviewSelectedReport('dismiss')">Dismiss</button>
                    </div>
                </div>
              `
            : '';

        detail.innerHTML = `
            <div class="admin-detail-card">
                <div class="admin-detail-header">
                    <h3>${AdminPage._escapeHtml(item.court_name || 'Court')}</h3>
                    <span class="court-update-status ${item.status}">${item.status}</span>
                </div>
                <p><strong>Reason:</strong> ${AdminPage._escapeHtml(item.reason || 'other')}</p>
                <p>${AdminPage._escapeHtml(item.description || 'No extra description provided.')}</p>
                <p class="muted">Reported ${created} by ${AdminPage._escapeHtml(item.reported_by?.username || `User #${item.user_id}`)}</p>
                <div class="admin-detail-actions">
                    <button class="btn-secondary btn-sm" onclick="App.openCourtDetails(${item.court_id})">Open Court</button>
                </div>
            </div>

            <div class="admin-detail-card">
                <h4>Current Court Snapshot</h4>
                <p><strong>Name:</strong> ${AdminPage._escapeHtml(court.name || item.court_name || '')}</p>
                <p><strong>Location:</strong> ${AdminPage._escapeHtml((court.address || '') + (court.city ? `, ${court.city}` : ''))}</p>
                <p><strong>Hours:</strong> ${AdminPage._escapeHtml(court.hours || 'Unknown')}</p>
            </div>

            ${pendingAction}
        `;
    },

    _renderTimelineDetail() {
        const detail = document.getElementById('admin-timeline-detail');
        if (!detail) return;

        const item = AdminPage.filteredTimelineItems.find(i => i.key === AdminPage.selectedTimelineKey)
            || AdminPage.timelineItems.find(i => i.key === AdminPage.selectedTimelineKey);
        if (!item) {
            detail.innerHTML = '<p class="muted">Select an activity item to review.</p>';
            return;
        }

        if (item.type === 'update') {
            const court = AdminPage.courtCache[item.court_id] || {};
            const community = court.community_info || {};
            const created = item.created_at ? new Date(item.created_at).toLocaleString() : '';
            const reviewed = item.reviewed_at ? new Date(item.reviewed_at).toLocaleString() : '';
            const payload = item.payload || {};
            const analysis = item.analysis || {};
            const pendingAction = item.status === 'pending'
                ? `
                    <div class="admin-review-actions">
                        <label>Reviewer Notes</label>
                        <textarea id="admin-timeline-notes" rows="3" placeholder="Optional approval/rejection note"></textarea>
                        <div class="admin-review-actions-row">
                            <button class="btn-primary" onclick="AdminPage.reviewUpdateById(${item.id}, 'approve', 'admin-timeline-notes')">Approve & Publish</button>
                            <button class="btn-danger" onclick="AdminPage.reviewUpdateById(${item.id}, 'reject', 'admin-timeline-notes')">Reject</button>
                        </div>
                    </div>
                  `
                : '';

            detail.innerHTML = `
                <div class="admin-detail-card">
                    <div class="admin-detail-header">
                        <h3>${AdminPage._escapeHtml(item.court_name || 'Court')} · Update</h3>
                        <span class="court-update-status ${item.status}">${item.status}</span>
                    </div>
                    <p>${AdminPage._escapeHtml(item.summary || '')}</p>
                    <p class="muted">Submitted ${created} by ${AdminPage._escapeHtml(item.user?.username || '')}</p>
                    ${reviewed ? `<p class="muted">Reviewed ${reviewed}${item.reviewed_by?.username ? ` by ${AdminPage._escapeHtml(item.reviewed_by.username)}` : ''}</p>` : ''}
                    ${item.reviewer_notes ? `<p class="muted">Reviewer note: ${AdminPage._escapeHtml(item.reviewer_notes)}</p>` : ''}
                    <div class="admin-detail-actions">
                        <button class="btn-secondary btn-sm" onclick="App.openCourtDetails(${item.court_id})">Open Court</button>
                    </div>
                </div>

                <div class="admin-detail-card">
                    <h4>AI Analysis</h4>
                    <p class="muted">Score: ${analysis.score ?? 'n/a'} · Recommendation: ${AdminPage._escapeHtml(analysis.recommendation || 'manual review')}</p>
                    ${(analysis.flags || []).length ? `<p class="muted">Flags: ${AdminPage._escapeHtml((analysis.flags || []).join('; '))}</p>` : '<p class="muted">No flags.</p>'}
                </div>

                <div class="admin-detail-card">
                    <h4>Location</h4>
                    ${AdminPage._keyValueRows(payload.location || {}, {
                        address: court.address,
                        city: court.city,
                        state: court.state,
                        zip_code: court.zip_code,
                        latitude: court.latitude,
                        longitude: court.longitude,
                    })}
                </div>

                <div class="admin-detail-card">
                    <h4>Court Info</h4>
                    ${AdminPage._keyValueRows(payload.court_info || {}, {
                        name: court.name,
                        description: court.description,
                        num_courts: court.num_courts,
                        surface_type: court.surface_type,
                        court_type: court.court_type,
                        indoor: court.indoor,
                        lighted: court.lighted,
                        fees: court.fees,
                        phone: court.phone,
                        website: court.website,
                        email: court.email,
                        skill_levels: court.skill_levels,
                    })}
                </div>

                <div class="admin-detail-card">
                    <h4>Hours & Community Notes</h4>
                    ${AdminPage._keyValueRows(
                        { ...(payload.hours || {}), ...(payload.community_notes || {}) },
                        {
                            hours: court.hours,
                            open_play_schedule: court.open_play_schedule,
                            hours_notes: community.hours_notes,
                            location_notes: community.location_notes,
                            parking_notes: community.parking_notes,
                            access_notes: community.access_notes,
                            court_rules: community.court_rules,
                            best_times: community.best_times,
                            closure_notes: community.closure_notes,
                            additional_info: community.additional_info,
                        }
                    )}
                </div>

                <div class="admin-detail-card">
                    <h4>Images</h4>
                    ${AdminPage._imagesHTML(payload.images || [])}
                </div>

                <div class="admin-detail-card">
                    <h4>Events</h4>
                    ${AdminPage._eventsHTML(payload.events || [])}
                </div>

                ${pendingAction}
            `;
            return;
        }

        // report timeline detail
        const court = AdminPage.courtCache[item.court_id] || {};
        const created = item.created_at ? new Date(item.created_at).toLocaleString() : '';
        const pendingAction = item.status === 'pending'
            ? `
                <div class="admin-review-actions">
                    <div class="admin-review-actions-row">
                        <button class="btn-primary" onclick="AdminPage.reviewReportById(${item.id}, 'resolve')">Mark Resolved</button>
                        <button class="btn-danger" onclick="AdminPage.reviewReportById(${item.id}, 'dismiss')">Dismiss</button>
                    </div>
                </div>
              `
            : '';

        detail.innerHTML = `
            <div class="admin-detail-card">
                <div class="admin-detail-header">
                    <h3>${AdminPage._escapeHtml(item.court_name || 'Court')} · Report</h3>
                    <span class="court-update-status ${item.status}">${item.status}</span>
                </div>
                <p><strong>Reason:</strong> ${AdminPage._escapeHtml(item.reason || 'other')}</p>
                <p>${AdminPage._escapeHtml(item.description || 'No description provided.')}</p>
                <p class="muted">Reported ${created} by ${AdminPage._escapeHtml(item.user?.username || '')}</p>
                <div class="admin-detail-actions">
                    <button class="btn-secondary btn-sm" onclick="App.openCourtDetails(${item.court_id})">Open Court</button>
                </div>
            </div>

            <div class="admin-detail-card">
                <h4>Current Court Snapshot</h4>
                <p><strong>Name:</strong> ${AdminPage._escapeHtml(court.name || item.court_name || '')}</p>
                <p><strong>Location:</strong> ${AdminPage._escapeHtml((court.address || '') + (court.city ? `, ${court.city}` : ''))}</p>
                <p><strong>Hours:</strong> ${AdminPage._escapeHtml(court.hours || 'Unknown')}</p>
            </div>

            ${pendingAction}
        `;
    },

    _keyValueRows(proposed, current) {
        const keys = Object.keys(proposed || {});
        if (!keys.length) return '<p class="muted">No proposed changes in this section.</p>';
        return `
            <div class="admin-kv-grid">
                ${keys.map(key => `
                    <div class="admin-kv-row">
                        <strong>${AdminPage._humanizeKey(key)}</strong>
                        <div class="admin-kv-values">
                            <span><em>Current:</em> ${AdminPage._formatValue(current?.[key])}</span>
                            <span><em>Proposed:</em> ${AdminPage._formatValue(proposed[key])}</span>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
    },

    _imagesHTML(images) {
        if (!images.length) return '<p class="muted">No images in this submission.</p>';
        return `
            <div class="admin-submission-images">
                ${images.map(img => {
                    const isHttp = /^https?:\/\//.test(img.image_url || '');
                    return `
                        <div class="admin-image-row">
                            ${isHttp ? `<img src="${AdminPage._escapeAttr(img.image_url)}" alt="Submitted image">` : '<div class="admin-image-placeholder">Uploaded Image</div>'}
                            <div>
                                <p>${AdminPage._escapeHtml(img.caption || 'No caption')}</p>
                                <p class="muted">${AdminPage._escapeHtml(img.image_url || '')}</p>
                            </div>
                        </div>
                    `;
                }).join('')}
            </div>
        `;
    },

    _eventsHTML(events) {
        if (!events.length) return '<p class="muted">No events in this submission.</p>';
        return events.map(event => `
            <div class="court-event-card">
                <div class="court-event-header">
                    <strong>${AdminPage._escapeHtml(event.title || 'Event')}</strong>
                    <span class="court-event-time">${AdminPage._escapeHtml(AdminPage._formatEventDate(event.start_time, event.end_time))}</span>
                </div>
                ${event.description ? `<p>${AdminPage._escapeHtml(event.description)}</p>` : ''}
                <div class="court-event-meta">
                    ${event.organizer ? `<span>Organizer: ${AdminPage._escapeHtml(event.organizer)}</span>` : ''}
                    ${event.contact ? `<span>Contact: ${AdminPage._escapeHtml(event.contact)}</span>` : ''}
                    ${event.recurring ? `<span>${AdminPage._escapeHtml(event.recurring)}</span>` : ''}
                    ${event.link ? `<a href="${AdminPage._escapeAttr(event.link)}" target="_blank">Event Link</a>` : ''}
                </div>
            </div>
        `).join('');
    },

    _formatEventDate(startIso, endIso) {
        if (!startIso) return '';
        const start = new Date(startIso);
        if (Number.isNaN(start.getTime())) return String(startIso);
        const startStr = start.toLocaleString('en-US', {
            weekday: 'short', month: 'short', day: 'numeric',
            hour: 'numeric', minute: '2-digit',
        });
        if (!endIso) return startStr;
        const end = new Date(endIso);
        if (Number.isNaN(end.getTime())) return startStr;
        return `${startStr} - ${end.toLocaleString('en-US', {
            month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
        })}`;
    },

    _formatValue(value) {
        if (value === undefined || value === null || value === '') return '<span class="muted">Not set</span>';
        return AdminPage._escapeHtml(String(value));
    },

    _humanizeKey(key) {
        return key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    },

    _actionLabel(action) {
        if (action === 'approve') return 'approve';
        if (action === 'reject') return 'reject';
        if (action === 'resolve') return 'resolve';
        if (action === 'dismiss') return 'dismiss';
        return 'action';
    },

    async reviewSelected(action) {
        const item = AdminPage.submissions.find(s => s.id === AdminPage.selectedSubmissionId);
        if (!item || item.status !== 'pending') return;

        const notes = (document.getElementById('admin-review-notes')?.value || '').trim();
        await AdminPage.reviewUpdateById(item.id, action, null, notes);
    },

    async reviewUpdateById(submissionId, action, notesElementId = null, notesOverride = null) {
        const noteValue = notesOverride !== null
            ? notesOverride
            : ((document.getElementById(notesElementId || '')?.value || '').trim());
        try {
            const res = await API.post(`/api/courts/updates/${submissionId}/review`, {
                action,
                reviewer_notes: noteValue,
            });
            App.toast(res.message || `Submission ${action}d.`);
            await AdminPage.refresh();
            if (typeof CourtUpdates !== 'undefined') CourtUpdates.refreshReviewQueue();
            if (MapView.currentCourtId) {
                if (App.currentView === 'court-detail') MapView._refreshFullPage(MapView.currentCourtId);
                else MapView.openCourtDetail(MapView.currentCourtId);
            }
            MapView.loadCourts();
        } catch (err) {
            App.toast(err.message || 'Review action failed', 'error');
        }
    },

    async reviewSelectedReport(action) {
        const item = AdminPage.reports.find(r => r.id === AdminPage.selectedReportId);
        if (!item || item.status !== 'pending') return;
        await AdminPage.reviewReportById(item.id, action);
    },

    async reviewReportById(reportId, action) {
        try {
            const res = await API.post(`/api/courts/reports/${reportId}/review`, { action });
            App.toast(res.message || `Report ${action}d.`);
            await AdminPage.refresh();
            if (MapView.currentCourtId) {
                if (App.currentView === 'court-detail') MapView._refreshFullPage(MapView.currentCourtId);
                else MapView.openCourtDetail(MapView.currentCourtId);
            }
            MapView.loadCourts();
        } catch (err) {
            App.toast(err.message || 'Report review failed', 'error');
        }
    },

    async retryLastBulkFailures() {
        const result = AdminPage.lastBulkResult;
        if (!result || !result.failedCount) {
            App.toast('No failed bulk items to retry.', 'error');
            return;
        }
        if (!['approve', 'reject', 'resolve', 'dismiss'].includes(result.rawAction)
            || !['update', 'report'].includes(result.targetType)) {
            App.toast('This bulk result cannot be retried.', 'error');
            return;
        }

        const failedIds = Array.from(new Set(
            (result.failed || [])
                .map(item => parseInt(item.id, 10))
                .filter(id => Number.isInteger(id) && id > 0)
        ));
        if (!failedIds.length) {
            App.toast('No valid failed IDs to retry.', 'error');
            return;
        }

        const actionLabel = AdminPage._actionLabel(result.rawAction);
        const confirmed = confirm(`Retry "${actionLabel}" for ${failedIds.length} failed item(s)?`);
        if (!confirmed) return;

        try {
            let res;
            let reviewerNotes = result.reviewerNotes || '';
            if (result.targetType === 'update') {
                const promptText = result.rawAction === 'approve'
                    ? 'Optional note for retried approved updates:'
                    : 'Optional note for retried rejected updates:';
                const noteInput = prompt(promptText, reviewerNotes || 'Bulk action retry from admin timeline.');
                if (noteInput === null) return;
                reviewerNotes = noteInput.trim();
                res = await API.post('/api/courts/updates/review/bulk', {
                    ids: failedIds,
                    action: result.rawAction,
                    reviewer_notes: reviewerNotes,
                });
            } else {
                res = await API.post('/api/courts/reports/review/bulk', {
                    ids: failedIds,
                    action: result.rawAction,
                });
            }

            const success = res.processed_count || 0;
            const failed = res.failed_count || 0;
            AdminPage.lastBulkResult = {
                action: `${actionLabel} (retry failed only)`,
                rawAction: result.rawAction,
                targetType: result.targetType,
                reviewerNotes: reviewerNotes,
                processedCount: success,
                failedCount: failed,
                failed: res.failed || [],
                totalCount: failedIds.length,
                timestamp: new Date().toISOString(),
            };
            App.toast(`Retry ${actionLabel}: ${success} succeeded${failed ? `, ${failed} failed` : ''}.`, failed ? 'error' : 'success');

            await AdminPage.refresh();
            if (typeof CourtUpdates !== 'undefined') CourtUpdates.refreshReviewQueue();
            if (MapView.currentCourtId) {
                if (App.currentView === 'court-detail') MapView._refreshFullPage(MapView.currentCourtId);
                else MapView.openCourtDetail(MapView.currentCourtId);
            }
            MapView.loadCourts();
        } catch (err) {
            AdminPage.lastBulkResult = {
                action: `${actionLabel} (retry failed only)`,
                rawAction: result.rawAction,
                targetType: result.targetType,
                reviewerNotes: result.reviewerNotes || '',
                processedCount: 0,
                failedCount: failedIds.length,
                failed: failedIds.map(id => ({ id, error: err.message || 'Retry request failed' })),
                totalCount: failedIds.length,
                timestamp: new Date().toISOString(),
            };
            AdminPage._renderBulkResult();
            App.toast(err.message || 'Retry failed', 'error');
        }
    },

    async bulkReviewTimeline(action) {
        const selected = AdminPage._selectedPendingTimelineItems();
        if (!selected.length) {
            App.toast('Select at least one pending item first.', 'error');
            return;
        }

        const updateActions = new Set(['approve', 'reject']);
        const reportActions = new Set(['resolve', 'dismiss']);
        let targets = [];
        if (updateActions.has(action)) {
            targets = selected.filter(item => item.type === 'update');
        } else if (reportActions.has(action)) {
            targets = selected.filter(item => item.type === 'report');
        }

        if (!targets.length) {
            App.toast('No matching selected items for this action.', 'error');
            return;
        }

        const actionLabel = AdminPage._actionLabel(action);
        const confirmed = confirm(`Apply "${actionLabel}" to ${targets.length} selected item(s)?`);
        if (!confirmed) return;

        try {
            let res;
            let reviewerNotes = '';
            const targetType = updateActions.has(action) ? 'update' : 'report';
            if (updateActions.has(action)) {
                const defaultNotes = 'Bulk action from admin timeline.';
                const promptText = action === 'approve'
                    ? 'Optional note for all approved updates:'
                    : 'Optional note for all rejected updates:';
                const noteInput = prompt(promptText, defaultNotes);
                if (noteInput === null) return;
                reviewerNotes = noteInput.trim();
                res = await API.post('/api/courts/updates/review/bulk', {
                    ids: targets.map(item => item.id),
                    action,
                    reviewer_notes: reviewerNotes,
                });
            } else {
                res = await API.post('/api/courts/reports/review/bulk', {
                    ids: targets.map(item => item.id),
                    action,
                });
            }

            const success = res.processed_count || 0;
            const failed = res.failed_count || 0;
            AdminPage.lastBulkResult = {
                action: actionLabel,
                rawAction: action,
                targetType: targetType,
                reviewerNotes: reviewerNotes,
                processedCount: success,
                failedCount: failed,
                failed: res.failed || [],
                totalCount: targets.length,
                timestamp: new Date().toISOString(),
            };
            App.toast(`Bulk ${actionLabel}: ${success} succeeded${failed ? `, ${failed} failed` : ''}.`, failed ? 'error' : 'success');

            await AdminPage.refresh();
            AdminPage.clearTimelineSelection();
            if (typeof CourtUpdates !== 'undefined') CourtUpdates.refreshReviewQueue();
            if (MapView.currentCourtId) {
                if (App.currentView === 'court-detail') MapView._refreshFullPage(MapView.currentCourtId);
                else MapView.openCourtDetail(MapView.currentCourtId);
            }
            MapView.loadCourts();
        } catch (err) {
            const targetType = updateActions.has(action) ? 'update' : 'report';
            AdminPage.lastBulkResult = {
                action: actionLabel,
                rawAction: action,
                targetType: targetType,
                reviewerNotes: '',
                processedCount: 0,
                failedCount: targets.length,
                failed: targets.map(item => ({ id: item.id, error: err.message || 'Bulk request failed' })),
                totalCount: targets.length,
                timestamp: new Date().toISOString(),
            };
            AdminPage._renderBulkResult();
            App.toast(err.message || 'Bulk action failed', 'error');
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
        return AdminPage._escapeHtml(value).replace(/`/g, '&#96;');
    },
};
