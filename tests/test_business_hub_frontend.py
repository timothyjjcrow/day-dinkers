"""Focused source contracts for business onboarding and player-facing venue tools."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()
COURTS_BACKEND = (ROOT / "backend" / "routes" / "courts.py").read_text()


def section(start: str, end: str) -> str:
    start_at = APP.index(start)
    return APP[start_at:APP.index(end, start_at)]


def test_business_hub_has_obvious_reentry_without_a_fifth_primary_tab():
    assert 'id="pf-business-hub"' in APP
    assert "Manage a venue" in APP
    assert "['business', 'building', 'Business tools'" in APP
    assert "uiIcon(icon)" in section("function openSettingsHub", "async function renderProfile")
    assert 'Your listing, bookings and events' in APP
    assert "business: openBusinessHub" in APP
    assert "function openBusinessHub(" in APP
    assert "{ page: true, label: 'Business Hub' }" in APP
    assert ".profile-business-entry" in STYLES
    assert ".business-tools-grid" in STYLES
    assert "context.showLocations()" in APP
    assert "const renderHome = ({ forceList = false } = {})" in APP
    assert "businessId = null" in APP
    assert "court = null" in APP


def test_business_hub_functional_navigation_uses_shared_product_icons():
    dashboard = section("function renderBusinessHubDashboard", "function openBusinessPlayerPreview")
    for icon in (
        "building", "eye", "edit", "target", "calendar", "link",
        "shield", "users", "chart", "clock", "lock", "settings",
    ):
        assert f"uiIcon('{icon}')" in dashboard or f"? '{icon}'" in dashboard or f": '{icon}'" in dashboard or f"icon:'{icon}'" in dashboard
    for glyph in ("✏️", "🎯", "📅", "🔗", "🛡️", "👥", "⇄", "📈", "↶", "🔐", "⚙️"):
        assert glyph not in dashboard
    assert ".business-tools-grid button > span .ui-icon" in STYLES
    assert ".business-operations-grid button > span .ui-icon" in STYLES


def test_business_statuses_and_manager_actions_use_semantic_product_ui():
    statuses = section("function businessStateIconName", "function businessDayLabel")
    assert "function businessStateIconName(value)" in statuses
    for icon in ("check-circle", "alert-triangle", "mail", "clock", "activity", "building", "eye", "edit"):
        assert f"'{icon}'" in statuses
    for old_glyph in ("✓", "⏳", "✉️", "○", "👁"):
        assert old_glyph not in statuses

    business = section("function renderBusinessHubDashboard", "function openNotificationSettings")
    for old_glyph in ("✓", "⏳", "✉️", "○", "🏢", "👤", "👥", "📍", "🔐", "⚠️", "🔗", "🎯", "📅", "↶", "●", "👁"):
        assert old_glyph not in business
    assert 'data-select-title="Role for ${esc(member.display_name' in business
    assert 'data-select-prefix="Access"' in business
    assert 'class="business-manager-action"' in business
    assert 'class="business-manager-action is-danger"' in business
    assert "${uiIcon('edit')}<span>Edit</span>" in business
    assert "${uiIcon('trash')}<span>Remove</span>" in business
    assert "${uiIcon(businessStateIconName(health))}<span>${esc(label)}</span>" in business
    assert "${uiIcon(business.suspended ? 'alert-triangle' : 'check-circle')}" in business

    for selector in (
        ".business-status .ui-icon",
        ".business-manager-action",
        ".business-manager-action.is-danger",
        ".business-pending-claim .btn",
        ".business-evidence-row .btn",
    ):
        assert selector in STYLES


def test_claim_list_deduplicates_owned_profiles_and_respects_review_status():
    hub = section("async function openBusinessHub", "function openBusinessClaimSheet")
    assert "const representedCourts = new Set" in hub
    assert "!representedCourts.has(Number(claim.court_id))" in hub
    assert "claimStatus === 'verified'" in hub
    assert "claimStatus === 'rejected'" in hub
    assert "Claim needs attention" in hub
    assert "Submitted for review" in hub
    assert "claim.feedback" in hub
    assert 'data-business-claim-resubmit="${claim.id}"' in hub
    assert "Review &amp; resubmit" in hub
    assert "visibleClaims.find" in hub
    assert "court: { id: claim.court_id" in hub


def test_claim_flow_is_explicit_private_and_never_overstates_pending_status():
    status = section("function businessVerificationState", "function businessStatusHtml")
    assert status.index("['pending', 'submitted', 'in_review'].includes(claim)") < status.index("business?.verified === true")
    assert "Submitted for review" in APP
    assert "Claim submitted — only you can see this draft." in APP
    assert "I confirm I’m authorized to represent this business." in APP
    assert "api('/businesses/claims'" in APP
    assert "court_id: courtId" in APP
    assert "role: modal.querySelector('#business-claim-role').value" in APP
    assert "verification_contact_email: contactInput.value.trim()" in APP
    assert "evidence_url: evidenceUrl" in APP
    assert "evidence_notes: modal.querySelector('#business-claim-evidence-notes').value.trim()" in APP
    assert "Used privately to review your request." in APP
    assert "An official page can help us confirm your role." in APP
    assert "Your listing stays private until your management role is approved." in APP
    assert "Manage this venue" in APP
    assert "Claim its profile to add booking, schedules, lessons, and programs." in APP
    assert 'id="business-claim-authorized" data-no-draft' in APP
    assert "Can’t find your venue?" in APP
    assert "Add it on the Courts map" in APP
    assert "Missing a court? Add it." in APP


def test_business_profile_has_explicit_publish_control_and_private_preview():
    dashboard = section("function renderBusinessHubDashboard", "async function openBusinessHub")
    workspace = section("function businessWorkspaceState", "function venueTaskHtml")
    assert "business.published === true" in workspace
    assert "verified && business.published === true && review === 'approved' && !business.suspended" in workspace
    assert "JSON.stringify({ published: !isPublic })" in dashboard
    assert 'id="business-player-preview"' in dashboard
    assert "Only business managers can see this draft preview" in dashboard
    assert "transitionModal(modal, () => openCourtDetail(business.court_id))" not in dashboard
    assert 'id="business-resubmit-claim">Update and resubmit claim' in dashboard
    assert "!canAdminister || (!isPublic && !canPublish) ? 'disabled'" in dashboard
    assert "const isPublic = workspace.publicNow" in dashboard


def test_business_management_uses_complete_rest_contracts():
    for contract in (
        "api('/businesses/mine')",
        "api(`/businesses/${business.id}`",
        "api(`/businesses/${business.id}/offerings`",
        "api(`/businesses/${business.id}/schedule`",
    ):
        assert contract in APP
    assert "method: 'PATCH'" in APP
    assert APP.count("method: 'PUT'") >= 2
    assert "amenities: modal.querySelector('#business-amenities').value.split(',').map" in APP
    assert "booking_url" in APP
    assert "membership_url" in APP
    assert "announcement" in APP
    assert "Request a custom integration" in APP
    integrations = section("function openBusinessIntegrationRequest", "const BUSINESS_OFFERING_CATEGORIES")
    assert "const role = String(business.manager_role" in integrations
    assert "!['owner', 'admin'].includes(role)" in integrations
    assert "Owner or admin access is required for integration requests." in integrations
    assert "api(`/businesses/${business.id}/integration-requests`" in integrations
    assert "capabilities: needs" in integrations
    assert "details: notes" in integrations
    assert "contact_email: emailInput.value.trim()" in integrations
    assert "Never paste passwords, API keys, access tokens" in integrations
    assert "does not install a connector or start a sync" in integrations
    assert "Request handled" in integrations
    assert "api('/feedback'" not in integrations
    assert "Previous requests" in integrations


def test_offerings_and_schedule_send_backend_canonical_values():
    assert "court_rental: ['calendar', 'Court booking']" in APP
    assert "court_booking:" not in APP
    assert "clinic: ['users', 'Clinic']" in APP
    assert "reservation: ['calendar', 'Reservation block']" not in APP
    assert 'data-icon-name="${icon}"' in APP
    assert "value=\"${label.toLowerCase()}\"" in APP
    assert "day_of_week: modal.querySelector('#business-schedule-day').value" in APP
    assert "day_of_week: Number(modal.querySelector('#business-schedule-day').value)" not in APP
    assert 'id="business-schedule-skill" maxlength="40"' in APP
    assert 'id="business-offering-duration" min="5" max="1440" step="1"' in APP
    assert "duration < 5 || duration > 1440" in APP
    assert "Use a whole number from 5 to 1,440 minutes." in APP


def test_court_detail_loads_verified_business_value_before_social_sections():
    detail = section("async function openCourtDetail", "function openChallengeSheet")
    assert "api(`/courts/${court.id}/business`)" in APP
    business_slot = detail.index('id="cd-business"')
    players = detail.index('id="cd-sec-players"')
    games = detail.index('id="cd-sec-games"')
    assert business_slot < games < players
    assert "loadCourtBusiness(modal, court, { expanded: focusBusiness });" in detail
    assert "Official information from" in APP
    assert "Venue-submitted information from" in APP
    assert "Continue to ${esc(business.name || 'the venue’s provider')} to finish booking" in APP
    assert "Verified means Third Shot confirmed who manages this listing" in APP
    assert "Once approved, verification will confirm who manages this listing" in APP
    assert "View all ${activeOfferings.length} offerings" in APP
    assert "activeSchedule.slice(0, 8).map((item) => businessScheduleLine" in APP
    assert "businessActionHref(item.booking_url)" in APP
    assert 'aria-label="${bookingLabel} ${esc(title)}"' in APP
    assert "Book a court" in APP
    assert "Book a lesson" in APP
    assert "Book venue open play" in APP
    assert "Venue services &amp; schedule" in APP
    assert "Official facility hours" in APP
    assert ".court-business-card" in STYLES
    assert ".business-action-grid" in STYLES
    assert "function bindBusinessLogoFallback" in APP
    assert "data-business-logo" in APP
    assert ".business-logo-frame img" in STYLES
    assert "(!venueBusiness || !venueBusiness.hours)" in detail
    assert "(!venueBusiness || !venueBusiness.website_url)" in detail
    assert "(!venueBusiness || !venueBusiness.phone)" in detail
    assert "!venueBusiness.schedule.some((item) => item && item.active !== false)" in detail


def test_public_business_section_never_silently_disappears_on_load_failure():
    loader = section("async function loadCourtBusiness", "let pendingCourtDetailOpen")
    assert "const loadPublicProfile = async ()" in loader
    assert "Checking venue information" in loader
    assert 'role="status"' in loader
    assert "Venue information couldn’t load" in loader
    assert 'role="alert"' in loader
    assert "data-retry-court-business" in loader
    assert "addEventListener('click', loadPublicProfile)" in loader
    assert "slot.innerHTML = ''" not in loader
    assert ".court-business-load-state" in STYLES


def test_every_configured_public_business_action_remains_reachable():
    public = section("function courtBusinessHtml", "async function loadCourtBusiness")
    assert "const allActions = actionCandidates.map" in public
    assert "const actions = allActions.slice(0, primaryActionLimit)" in public
    assert "const moreActions = allActions.slice(primaryActionLimit)" in public
    assert "More venue actions" in public
    assert "${moreActions.join('')}" in public
    assert ".filter(Boolean).slice(" not in public
    assert ".business-more-actions" in STYLES


def test_player_facing_business_schedule_uses_human_calendar_order():
    public = section("function comparePublicBusinessSchedule", "async function loadCourtBusiness")
    assert "if (!leftDate) return 1;" in public
    assert "if (!rightDate) return -1;" in public
    assert "'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'" in public
    assert "businessDayLabel(item?.day_of_week ?? item?.day)" in public
    assert ".sort(comparePublicBusinessSchedule)" in public


def test_non_owner_business_staff_receive_a_visible_management_entry():
    public = section("function courtBusinessHtml", "async function loadCourtBusiness")
    assert "business.is_manager || business.is_owner" in public
    assert "court-business-manage" in public
    assert public.count("data-manage-venue-business") == 1

    backend = COURTS_BACKEND[
        COURTS_BACKEND.index("def _public_business_detail"):
        COURTS_BACKEND.index("@courts_bp.post('/courts')")
    ]
    assert "business_access_role(profile, current_user.id)" in backend
    assert "data['is_owner'] = manager_role == 'owner'" in backend
    assert "data['is_manager'] = bool(manager_role)" in backend
    assert "data['manager_role'] = manager_role" in backend


def test_business_dashboard_counts_use_human_singular_and_plural_copy():
    assert "offering${activeOfferingCount === 1 ? '' : 's'}" in APP
    assert "session${activeScheduleCount === 1 ? '' : 's'}" in APP
    completion = section("function businessCompletion", "function businessCourtName")
    assert "item.active !== false" in completion


def test_booking_readiness_counts_active_item_links_and_preview_hides_owner_control():
    readiness = section("function businessHasBookingLink", "function businessCompletion")
    assert "business.offerings" in readiness
    assert "business.schedule" in readiness
    assert "item.active !== false" in readiness
    assert "businessActionHref(item.booking_url)" in readiness
    dashboard = section("function businessCompletion", "function businessCourtName")
    assert "businessHasBookingLink(business)" in dashboard
    preview = section("function openBusinessPlayerPreview", "async function openBusinessHub")
    assert "courtBusinessHtml({ ...business, is_owner: false, is_manager: false, preview_only: true })" in preview


def test_operator_links_are_filtered_before_entering_player_facing_hrefs():
    safe = section("function safeHref", "// Game plans are account-scoped")
    assert "new URL(candidate, location.origin)" in safe
    assert "new Set(['http:', 'https:'])" in safe
    assert "allowed.add('tel:')" in safe
    assert "allowed.add('mailto:')" in safe
    business_safe = section("function businessActionHref", "function businessActionLink")
    assert "href.startsWith('https://')" in business_safe
    public = section("function businessOfferingRowHtml", "async function loadCourtBusiness")
    assert "business.booking_url || (courtRental && courtRental.booking_url)" in public
    assert "lesson && lesson.booking_url" in public
    assert "openPlay && openPlay.booking_url" in public
    assert "&& businessActionHref(item.booking_url)" in public
    assert "item.category === 'court_rental'" in public
    assert "lesson.booking_url || business.booking_url" not in public
    assert "openPlay.booking_url || business.booking_url" not in public
    assert "businessActionLink(label, icon, url, tone, { action, business_id: business.id })" in public
    assert "businessActionHref(item.booking_url)" in public
    assert 'target="_blank" rel="noopener"' in public


def test_claim_submit_sends_server_verified_authorization_attestation():
    claim = section("function openBusinessClaimSheet", "function optionalBusinessUrl")
    assert 'id="business-claim-authorized"' in claim
    assert "authorized_attestation: true" in claim


def test_business_mobile_actions_meet_touch_target_baseline():
    assert "--tap-min: 44px" in STYLES
    for selector in (
        ".business-missing-venue .btn-link",
        ".business-manager-actions button",
        ".business-offering-row > a",
        ".business-offering-more > summary",
        ".business-schedule-row > a",
        ".business-contact-links a",
    ):
        start = STYLES.index(selector)
        rule = STYLES[start:STYLES.index("}", start)]
        assert "min-height: 44px" in rule or "min-height: var(--tap-min)" in rule


def test_court_picker_never_submits_a_stale_hidden_selection():
    picker = section("function clubCourtPicker", "function openCreateClubSheet")
    assert "const selectedName = e.target.dataset.selectedCourtName || ''" in picker
    assert "if (q !== selectedName)" in picker
    assert "delete e.target.dataset.selectedCourtId" in picker
    assert picker.index("input.dataset.selectedCourtName = row.dataset.pickName") < picker.index("modal.querySelector(`#${prefix}-court-id`).value = row.dataset.pickCourt")
    assert "input.dataset.selectedCourtName = row.dataset.pickName" in picker
    assert "input.dataset.selectedCourtId = row.dataset.pickCourt" in picker
    assert "requestSeq !== searchSeq" in picker
    assert "searchInput.setAttribute('role', 'combobox')" in picker
    assert "resultsBox.setAttribute('role', 'listbox')" in picker
    assert "[c.address, c.city, c.state].filter(Boolean).join(', ')" in picker
    assert "error.message || 'Could not load courts.'" in picker
    assert "data-court-search-retry" in picker
    assert "runCourtSearch(q, retrySeq)" in picker


def test_account_mfa_uses_global_contracts_and_adopts_rotated_tokens_first():
    assert "body.mfa_code = mfaCode" in APP
    assert "err.code === 'mfa_required'" in APP
    assert "api('/auth/mfa/setup'" in APP
    assert "JSON.stringify({ current_password: password })" in APP
    assert "api('/auth/mfa/enable'" in APP
    assert "JSON.stringify({ code })" in APP
    assert "api('/auth/mfa/disable'" in APP
    assert "JSON.stringify({ current_password: password.value, code: code.value.trim() })" in APP
    assert "setup.otpauth_uri" in APP
    helper = section("function persistReplacementToken", "const ERROR_TEXT")
    assert "state.token = replacement" in helper
    assert "localStorage.setItem('pp_token', replacement)" in helper
    password = section("function openAccountSettings", "function openSettingsHub")
    assert password.index("persistReplacementToken(result)") < password.index("toast('Password updated")
    security = section("function openBusinessSecurity", "function openBusinessOwnershipControls")
    assert security.index("persistReplacementToken(result)") < security.index("state.me.mfa = { enabled: true")
    assert security.index("persistReplacementToken(result)", security.index("/auth/mfa/disable")) < security.index("state.me.mfa = { enabled: false")


def test_public_integrated_schedule_and_nonblocking_action_analytics_are_wired():
    public = section("function recordBusinessAction", "async function loadCourtBusiness")
    assert "`/api/businesses/${businessId}/booking-clicks`" in public
    assert "client_event_id: businessClientEventId(), action" in public
    assert "connection_id:" in public
    assert "occurrence_id:" in public
    assert "navigator.sendBeacon" in public
    assert "keepalive: true" in public
    assert "analytics never blocks the player's action" in public
    loader = section("async function loadCourtBusiness", "async function openCourtDetail")
    assert "`/businesses/${business.id}/integrated-schedule?from=" in loader
    assert "Array.isArray(data.items)" in loader
    assert "Array.isArray(data.sources)" in loader
    assert "integrated_schedule_state: 'loading'" in loader
    assert "integrated_schedule_state: 'error'" in loader
    schedule = section("function businessScheduleLine", "function businessActionHref")
    assert "timeZone: item.timezone" in schedule
    assert "item.spots_remaining" in schedule
    assert "item.capacity" in schedule
    assert "item.status" in schedule


def test_structured_feed_connection_uses_real_provider_catalog_and_health_contracts():
    add = section("function openBusinessAddConnection", "function openBusinessConnections")
    assert "api('/business-integrations/providers')" in add
    assert "item.availability === 'active'" in add
    assert "providerKey !== 'link_catalog'" in add
    assert "provider_key: 'link_catalog'" in add
    assert "display_name: displayName.value.trim()" in add
    assert "config: { label: label.value.trim(), source_url: sourceUrl, booking_base_url: bookingUrl }" in add
    catalog = section("function openBusinessCatalogUpload", "function openBusinessAddConnection")
    assert "method: 'PUT', headers: { 'Idempotency-Key': idempotencyKey }" in catalog
    assert "catalog.schema_version !== 1" in catalog
    assert "Array.isArray(catalog.occurrences)" in catalog
    connections = section("function openBusinessConnections", "function openBusinessAnalytics")
    for field in ("display_name", "provider_key", "health_status", "publication_ready", "last_sync_succeeded_at", "last_error_message", "capabilities"):
        assert f"item.{field}" in connections
    for status in ("connected", "degraded", "error", "disconnected", "draft"):
        assert status in connections
    assert "connections/${button.dataset.connectionCheck}/recheck" in connections
    assert "connections/${button.dataset.connectionReconnect}/reconnect" in connections
    assert "method: 'DELETE'" in connections
    assert 'Held from public' in connections


def test_verification_evidence_email_challenge_and_deep_links_match_backend():
    verification = section("function openBusinessVerificationCenter", "function openBusinessTeamManager")
    for value in ("business_email", "business_phone", "website_domain", "documents", "in_person", "other"):
        assert value in verification
    assert "verification.claims" in verification
    assert "claim?.evidence" in verification
    assert "verification/evidence/${challenge.id}/verify" in verification
    assert "JSON.stringify({ token: token.value.trim() })" in verification
    assert "verification/evidence/${challenge.id}/resend" in verification
    assert "JSON.stringify({ type, value: evidenceValue, note })" in verification
    deep_links = section("function acceptBusinessInvitationDeepLink", "async function handleInviteRef")
    assert "`/business-invitations/${encodeURIComponent(rawToken)}/accept`" in deep_links
    assert "^#business-email-verification=(\\d+):(\\d+):(\\d{6})$" in deep_links
    assert "{ evidenceId, token }" in deep_links


def test_team_roles_invitations_org_attachment_and_transfer_are_exact():
    team = section("function openBusinessTeamManager", "function openBusinessCatalogUpload")
    for role in ("owner", "admin", "editor", "viewer"):
        assert role in team
    assert "data.invitations" in team
    assert "team/invitations/${button.dataset.invitationRevoke}" in team
    assert "JSON.stringify({ email: input.value.trim(), role:" in team
    dashboard = section("function renderBusinessHubDashboard", "function openBusinessPlayerPreview")
    assert "`/businesses/${business.id}/organization/locations`" in dashboard
    assert "JSON.stringify({ business_id: Number(newBusiness.id) })" in dashboard
    ownership = section("function openBusinessOwnershipControls", "function currentUserIsBusinessOperator")
    assert "member.role === 'admin'" in ownership
    assert "member_id: Number(sheet.querySelector('#business-transfer-member').value)" in ownership
    assert "current_password: password.value" in ownership
    assert "mfa_code: mfa.value.trim()" in ownership
    assert "all" in ownership.lower() and "location" in ownership.lower()
    assert "method: 'DELETE', body: JSON.stringify({ confirmation: business.name, ...auth })" in ownership


def test_business_dashboard_rbac_matches_governance_roles():
    dashboard = section("function renderBusinessHubDashboard", "function openBusinessPlayerPreview")
    assert "const canEditContent = ['owner', 'admin', 'editor'].includes(managerRole)" in dashboard
    assert "const canAdminister = ['owner', 'admin'].includes(managerRole)" in dashboard
    assert "const canOwn = managerRole === 'owner'" in dashboard
    assert "Viewer access is read-only" in dashboard
    assert "Owner or admin access is required" in dashboard
    assert "Only the primary owner can use ownership controls" in dashboard
    assert "id=\"business-add-location\" ${canOwn ? '' : 'disabled'}" in dashboard
    history = section("function openBusinessRevisionHistory", "function openBusinessSecurity")
    assert "['owner', 'admin', 'editor'].includes" in history
    assert "canRestore ?" in history
    connections = section("function openBusinessConnections", "function openBusinessAnalytics")
    assert "const canConfigure = ['owner', 'admin'].includes(role)" in connections
    assert "const canSync = ['owner', 'admin', 'editor'].includes(role)" in connections


def test_business_hub_drill_ins_keep_parent_context_and_confirm_sensitive_changes():
    dashboard = section("function renderBusinessHubDashboard", "function openBusinessPlayerPreview")
    team = section("function openBusinessTeamManager", "function openBusinessCatalogUpload")
    connections = section("function openBusinessConnections", "function openBusinessAnalytics")
    security = section("function openBusinessSecurity", "function openBusinessOwnershipControls")
    ownership = section("function openBusinessOwnershipControls", "function currentUserIsBusinessOperator")
    offerings = section("function openBusinessOfferingsEditor", "const BUSINESS_SCHEDULE_KINDS")
    schedule = section("function openBusinessScheduleEditor", "function openNotificationSettings")

    assert "const openToolChild = (openNext) => openChildModal(modal, openNext);" in dashboard
    for child in (
        "openBusinessDetailsEditor", "openBusinessOfferingsEditor", "openBusinessScheduleEditor",
        "openBusinessIntegrationRequest", "openBusinessVerificationCenter", "openBusinessTeamManager",
        "openBusinessConnections", "openBusinessAnalytics", "openBusinessRevisionHistory",
        "openBusinessSecurity", "openBusinessOwnershipControls",
    ):
        assert f"openToolChild(() => {child}" in dashboard

    assert "title: 'Unpublish this business profile?'" in dashboard
    assert "confirmLabel: 'Unpublish profile'" in dashboard
    assert "data-current-role" in team
    assert "title: `Change ${memberName} to ${nextRole}?`" in team
    assert "select.value = previousRole;" in team
    assert "syncAppSelect(select);" in team
    assert "openChildModal(modal, () => openBusinessOfferingForm" in offerings
    assert "openChildModal(modal, () => openBusinessScheduleItemForm" in schedule
    assert "openChildModal(modal, () => openBusinessIntegrationRequest" in connections
    assert "openChildModal(modal, () => openBusinessAddConnection" in connections
    assert "openChildModal(modal, () => openBusinessCatalogUpload" in connections
    assert "openChildModal(modal, () => openModal(`${modalHead('Set up MFA')" in security
    assert "openChildModal(modal, () => openModal(`${modalHead('Disable MFA')" in security
    assert "openChildModal(modal, openAccountSettings)" in security
    assert "openChildModal(modal, () => openModal(`${modalHead('Add an admin first')" in ownership
    assert "openChildModal(modal, () => openModal(`${modalHead('Transfer organization')" in ownership
    assert "const transferButton = event.currentTarget;" in ownership
    assert ownership.count("transferButton.disabled = false;") == 3
    assert "event.currentTarget.disabled" not in ownership
    assert ownership.count("returnFocus: transferButton") == 2
    assert ".business-organization-summary b, .business-organization-summary small { display: block; }" in STYLES


def test_staged_business_editors_confirm_before_discarding_every_unsaved_layer():
    offering_form = section("function openBusinessOfferingForm", "function openBusinessOfferingsEditor")
    offerings = section("function openBusinessOfferingsEditor", "const BUSINESS_SCHEDULE_KINDS")
    schedule_form = section("function openBusinessScheduleItemForm", "function openBusinessScheduleEditor")
    schedule = section("function openBusinessScheduleEditor", "function openNotificationSettings")
    form_ux = section("function bindModalFormUX", "// A mutation can be represented")

    assert "const isDirty = () => JSON.stringify(collectDraftFields()) !== JSON.stringify(initialDraftFields);" in form_ux
    assert "return { clearDraft, clearError, showError, startSubmitting, isDirty };" in form_ux

    assert "bindModalDiscardConfirmation(modal, {" in offering_form
    assert "isDirty: formUX.isDirty" in offering_form
    assert "Discard this offering draft?" in offering_form

    assert "const initialOfferings = JSON.stringify(offerings);" in offerings
    assert "isDirty: () => JSON.stringify(offerings) !== initialOfferings" in offerings
    assert "Discard unsaved offering changes?" in offerings

    assert "bindModalDiscardConfirmation(modal, {" in schedule_form
    assert "isDirty: formUX.isDirty" in schedule_form
    assert "Discard this schedule item?" in schedule_form

    assert schedule.index("render();") < schedule.index("const initialSchedule = JSON.stringify(schedule);")
    assert "isDirty: () => JSON.stringify(schedule) !== initialSchedule" in schedule
    assert "Discard unsaved schedule changes?" in schedule

    # Successful submissions remain programmatic closes, so the discard guard
    # protects only user dismissal and cannot interrupt a completed save.
    assert "closeModal(modal);\n      onSave?.(updated, index);" in offering_form
    assert "closeModal(modal);\n      onSave?.(updated, index);" in schedule_form
    assert "closeModal(modal);\n        toast('Offerings updated')" in offerings
    assert "closeModal(modal);\n        toast('Schedule updated')" in schedule


def test_successful_child_edits_refresh_one_retained_community_or_crew_parent():
    crew = section("async function openCrewInviteSheet", "function crewPlannerOptions")
    crew_screen = section("async function openCrewScreen", "function openRenameCrewSheet")
    rename = section("function openRenameCrewSheet", "async function openCrewChatById")
    club = section("function openClubInfo", "async function openClubInviteSheet")
    edit = section("function openEditClubSheet", "async function openFindClubsSheet")

    assert "async function openCrewInviteSheet(crew, onSaved = null)" in crew
    assert "onSaved?.();" in crew
    assert "if (onSaved) closeModal(modal);" in crew
    assert "openCrewInviteSheet(crew, (viewOptions = {}) => {" in crew_screen
    assert "transitionModal(modal, () => openCrewScreen(crew.id, viewOptions));" in crew_screen
    assert "transitionModal(modal, () => openCrewScreen(crew.id));" in crew_screen

    assert "function openRenameCrewSheet(crew, onSaved = null)" in rename
    assert "onSaved();\n          closeModal(modal);" in rename
    assert "openRenameCrewSheet(crew, () => {" in crew_screen

    assert "function openEditClubSheet(club, onSaved = null)" in edit
    assert "onSaved();\n          closeModal(modal);" in edit
    assert "openEditClubSheet(club, () => {" in club
    assert "transitionModal(modal, () => openClubScreen(club.id));" in club


def test_manual_schedule_supports_capacity_availability_status_and_freshness():
    schedule = section("function openBusinessScheduleItemForm", "function openBusinessScheduleEditor")
    assert 'id="business-schedule-spots" min="0" max="10000"' in schedule
    for status in ("scheduled", "sold_out", "cancelled", "completed"):
        assert f'value="{status}"' in schedule
    assert "spotsRemaining > capacity" in schedule
    assert "spots_remaining: spotsText ? spotsRemaining : null" in schedule
    manager = section("function openBusinessScheduleEditor", "function openNotificationSettings")
    assert "Manager-maintained · ${statusLabel}" in manager
    assert "`${item.spots_remaining} of ${item.capacity} spots left`" in manager


def test_reports_use_exact_category_payload_and_minimum_detail():
    report = section("function openBusinessReportSheet", "function openBusinessVerificationCenter")
    for category in ("incorrect_info", "broken_link", "ownership", "safety", "other"):
        assert f'value="{category}"' in report
    assert "details.length < 10" in report
    assert "category: modal.querySelector('#business-report-reason').value, details" in report
    assert "reason:" not in report


def test_operator_queue_assignment_evidence_and_decisions_use_exact_contracts():
    operator = section("function openBusinessOperatorHub", "async function openBusinessHub")
    for key in ("claims", "revisions", "integration_requests", "reports", "actions_requiring_second_admin", "connection_alerts", "profile_link_alerts"):
        assert key in operator
    assert "item.sla_state" in operator
    assert "item.due_at || item.response_due_at || item.expires_at" in operator
    assert "item.assigned_operator_identifier" in operator
    assert "`/operator/business/${segment}/${id}/assign`" in operator
    assert "operator_user_id: Number(state.me.id), mfa_code: mfa.value.trim()" in operator
    assert "`/operator/business/evidence/${evidenceId}/review`" in operator
    assert "decision: modal.querySelector('#operator-evidence-decision').value" in operator
    assert "review_note: note.value.trim(), mfa_code: mfa.value.trim()" in operator
    assert "`/operator/business/claims/${id}/review`" in operator
    assert "verification_method:" in operator
    assert "review_note: note, claimant_feedback: feedback, mfa_code:" in operator
    assert "`/operator/business/integration-requests/${id}`" in operator
    assert "status_message: note" in operator
    assert "`/operator/business/reports/${id}`" in operator
    assert '<option value="reviewing">' in operator
    assert '<option value="resolved">' in operator
    assert '<option value="dismissed">' in operator
    assert "`/operator/business/revisions/${id}/review`" in operator
    assert "decision: modal.querySelector('#operator-business-decision').value, review_note: note, mfa_code:" in operator
    assert "`/operator/business/actions/${id}/confirm`" in operator
    assert "`/operator/business/connections/${connectionId}/recheck`" in operator
    assert "`/operator/businesses/${businessId}/link-health/recheck`" in operator
    assert "kind === 'profile_link_alerts'" in operator
    assert "businessRevisionDiffHtml(item)" in operator
    assert "openChildModal(modal, () => openBusinessOperatorAssignment" in operator
    assert "openChildModal(modal, () => openBusinessOperatorReview" in operator
    assert "openChildModal(modal, () => openBusinessOperatorEvidenceReview" in operator
    assert "openChildModal(modal, () => openBusinessSecurity({}))" in operator
    assert operator.count('<option value="" selected disabled>Choose a decision</option>') >= 3
    assert operator.count('<option value="" selected disabled>Choose a status</option>') >= 2
    assert "Choose whether to accept or reject this evidence." in operator
    assert "Choose a decision before saving." in operator
    assert "before_snapshot" in APP
    assert "after_snapshot" in APP
    assert "/acknowledge" not in operator.lower()
    assert "status: 'acknowledged'" not in operator.lower()


def test_business_governance_and_operator_styles_have_mobile_accessible_controls():
    for selector in (
        ".business-operations-grid button",
        ".business-connection-actions .btn",
        ".business-team-remove",
        ".business-operator-row .btn",
        ".business-report-link",
        ".business-schedule-more > summary",
    ):
        start = STYLES.index(selector)
        rule = STYLES[start:STYLES.index("}", start)]
        assert "min-height:" in rule
    assert "@media (max-width: 430px)" in STYLES
