"""Court-only UI contracts for the sixth interaction-quality pass."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
INDEX = (ROOT / "public" / "index.html").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    start_at = APP.index(start)
    return APP[start_at:APP.index(end, start_at)]


def test_map_and_list_switches_use_the_shared_icon_language():
    switch_start = INDEX.index('id="court-view-switch"')
    switch_end = INDEX.index('</div>', switch_start)
    switch = INDEX[switch_start:switch_end]
    assert 'href="#ui-map"' in switch
    assert 'href="#ui-grid"' in switch
    assert '<span>Map</span>' in switch
    assert '<span>List</span>' in switch
    assert '.court-view-switch button .ui-icon' in STYLES
    assert '.court-view-switch #court-result-count' in STYLES


def test_filter_sheet_explains_choices_and_makes_draft_state_visible():
    sheet = section("function openCourtFilterSheet", "async function fetchCourtsInView")
    assert "const filterDescriptions =" in sheet
    assert "const optionHtml =" in sheet
    assert 'class="court-filter-option-copy"' in sheet
    assert 'class="court-filter-option-state" aria-hidden="true">${uiIcon(\'check\')}' in sheet
    assert 'role="group" aria-labelledby="court-filter-venue-label"' in sheet
    assert 'role="group" aria-labelledby="court-filter-amenities-label"' in sheet
    assert 'id="court-filter-draft-status" role="status" aria-live="polite"' in sheet
    assert "clear.disabled = n === 0" in sheet
    assert "const loadedMatches = applyCourtFilters(state.courtsInView, draft).length;" in sheet
    assert "const countIsComplete = !state.courtResultsTruncated && !loosensCurrentResults;" in sheet
    assert "? `Show ${matchCount} court${matchCount === 1 ? '' : 's'}`" in sheet
    assert "apply.innerHTML = `${uiIcon(n ? 'check' : 'refresh')} ${applyLabel}`" in sheet
    assert "state.courtFilters = { ...draft };" in sheet
    assert ".court-filter-option.active .court-filter-option-state" in STYLES
    assert ".court-filter-option:focus-visible" in STYLES


def test_court_results_own_clear_loading_error_and_empty_states():
    refresh = section("function beginCourtContextRefresh", "function mapViewStorageKey")
    assert "list.setAttribute('aria-busy', 'true')" in refresh
    assert 'class="court-result-loading-cards" aria-hidden="true"' in refresh
    assert "list.removeAttribute('aria-busy')" in refresh
    assert 'class="court-result-error" role="alert"' in refresh
    assert 'data-retry-court-results' in refresh
    assert "uiIcon('alert-triangle')" in refresh
    assert "uiIcon('refresh')" in refresh

    listing = section("function renderCourtList", "function openSuggestEditSheet")
    assert "el.removeAttribute('aria-busy');" in listing
    assert 'class="court-result-empty" role="status"' in listing
    assert 'class="court-result-state-icon" aria-hidden="true"' in listing
    assert 'id="court-search-again"' in listing
    assert "setCourtSheetSnap('peek');" in listing
    assert "search?.focus({ preventScroll: true });" in listing
    assert 'type="button" class="btn btn-secondary btn-block list-add-court"' in listing
    assert ".court-result-error" in STYLES
    assert ".court-result-loading-cards" in STYLES
    assert "#court-show-more { min-height: var(--tap-min); }" in STYLES


def test_court_cards_have_a_visible_open_or_selected_affordance():
    card = section("function courtRowHtml", "function sortCourts")
    assert "const selected = state.selectedCourtId === c.id;" in card
    assert 'class="court-card-trailing"' in card
    assert 'class="court-card-open-icon ${selected ? \'is-selected\' : \'\'}"' in card
    assert "uiIcon(selected ? 'check' : 'chevron-right')" in card
    assert 'aria-pressed="${selected}"' in card
    assert ".court-card-open-icon.is-selected" in STYLES


def test_add_court_flow_exposes_the_pin_and_collects_supported_location_fields():
    add = section("function openAddCourtSheet", "function openConditionSheet")
    assert 'class="court-pin-summary" aria-describedby="ac-pin-copy"' in add
    assert 'id="ac-pin-map" role="application"' in add
    assert 'id="ac-confirm-pin"' in add
    assert "pinMarker = L.marker([pin.lat, pin.lng]," in add
    assert "draggable: true" in add
    assert "api(`/geocode/reverse?lat=${pin.lat}&lng=${pin.lng}`)" in add
    assert 'id="ac-address" maxlength="255" autocomplete="street-address"' in add
    assert 'id="ac-city" maxlength="120" autocomplete="address-level2"' in add
    assert 'id="ac-state" maxlength="2" autocomplete="address-level1"' in add
    assert "city: modal.querySelector('#ac-city').value.trim()" in add
    assert "state: modal.querySelector('#ac-state').value.trim().toUpperCase()" in add
    assert "address: modal.querySelector('#ac-address').value.trim()" in add
    assert "Number.isInteger(courtCount)" in add
    assert "Enter a court count from 1 to 100." in add
    assert "bindModalDiscardConfirmation(modal" in add
    assert "isDirty: () => pinMoved || formUX.isDirty()" in add
    assert 'class="court-contribution-actions"' in add
    assert ".court-pin-summary" in STYLES
    assert ".court-contribution-actions" in STYLES


def test_suggest_edit_flow_groups_fields_and_protects_unsaved_changes():
    edit = section("function openSuggestEditSheet", "// ---------- Modal helpers")
    assert "Community-verified details" in edit
    assert 'id="se-setup-label"' in edit
    assert 'id="se-visit-label"' in edit
    assert 'class="court-form-section court-closure-field"' in edit
    assert "Another player confirms a change before it goes live." in edit
    assert "Number.isInteger(courtCount)" in edit
    assert "Enter a court count from 1 to 100." in edit
    assert "title: 'Discard this court suggestion?'" in edit
    assert 'id="se-submit">${uiIcon(\'check\')} Submit suggestion' in edit
    assert ".court-closure-field" in STYLES
    assert ".court-form-section > .choice-check-list { margin: 0; padding: 0; border: 0; }" in STYLES


def test_condition_report_is_typed_inline_and_recovers_without_a_toast_only_error():
    report = section("function openConditionSheet", "function maybeAskConditions")
    assert "What’s it like right now?" in report
    assert 'type="button" class="btn btn-secondary btn-block court-condition-choice"' in report
    assert 'id="court-condition-error" role="alert" tabindex="-1"' in report
    assert "error.textContent = e.message" in report
    assert "error.focus({ preventScroll: true })" in report
    assert "Condition reports expire automatically" in report
    assert "toast(e.message)" not in report


def test_court_detail_uses_product_icons_and_closed_courts_bind_only_live_controls():
    detail = section("async function openCourtDetail", "function openCourtPlayerActions")
    assert 'class="cd-hero-img placeholder" aria-hidden="true">${uiIcon(\'pickleball\')}' in detail
    assert "data-remove-on-error" in detail
    assert "onerror=\"this.remove()\"" not in detail
    assert 'class="cd-closed-banner" role="status"' in detail
    assert "uiIcon('alert-triangle')" in detail
    assert 'class="court-detail-empty"' in detail
    assert "uiIcon('users')" in detail
    assert "uiIcon('calendar')" in detail
    assert 'class="court-claim-action"' in detail
    assert "Claim its profile to add booking, schedules, lessons, and programs." in detail
    assert 'class="cd-weather-summary"' in detail
    assert "modal.querySelector('#cd-condition')?.addEventListener" in detail
    assert "modal.querySelector('#cd-schedule')?.addEventListener" in detail
    assert "modal.querySelector('#cd-ranked')?.addEventListener" in detail
    assert "modal.querySelector('#cd-chat')?.addEventListener" in detail
    assert ".cd-hero-title { position: absolute; z-index: 2;" in STYLES
    assert "position: absolute; z-index: 2; inset: 0;" in STYLES
    assert ".cd-closed-banner" in STYLES
    assert ".court-claim-action" in STYLES
    assert ".cd-weather-summary" in STYLES


def test_court_detail_rating_gallery_and_section_navigation_are_accessible():
    review = section("function starsHtml", "function gameToIcs")
    assert 'type="radio" name="court-review-rating"' in review
    assert 'i === Number(rating) ? \'checked\'' in review
    assert '<fieldset class="star-row" id="cd-stars"><legend class="sr-only">Your rating</legend>' in review
    assert "starRow.addEventListener('change'" in review
    assert "starRow.innerHTML = starsHtml" not in review
    assert '<form class="card" id="cd-review-form" novalidate>' in review
    assert 'for="cd-review-comment">Comment <span>optional</span></label>' in review
    assert '<textarea id="cd-review-comment" maxlength="500" rows="3"' in review
    assert 'id="cd-review-count" aria-live="polite"' in review
    assert 'type="submit" class="btn btn-primary btn-sm" id="cd-review-save"' in review
    assert 'id="cd-review-delete"' in review
    assert "deleteCourtReview(court, mine, button)" in review
    assert 'id="cd-review-all">See all' in review
    assert "params.set('before_id', String(beforeId))" in review
    assert 'id="court-review-more">Load more reviews' in review
    assert "el.querySelector('#cd-review-form').addEventListener('submit'" in review
    assert "e.preventDefault();" in review
    assert ".star-choice input:focus-visible + .star-btn { outline: 3px solid var(--green-accent)" in STYLES

    detail = section("async function openCourtDetail", "function openCourtPlayerActions")
    assert "window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'" in detail
    gallery = section("async function openCourtGallery", "async function openBusinessHub")
    assert "modalHead(court.name, 'camera')" in gallery
    assert "modalHead(`${uiIcon('camera')} ${court.name}`)" not in gallery
    gallery_helpers = section("function galleryPhotoMetaHtml", "async function openBusinessHub")
    assert 'data-delete-photo="${photo.id}"' in gallery_helpers
    assert "method: 'DELETE'" in gallery_helpers
    assert 'class="gallery-lightbox-counter" aria-live="polite">${index + 1} of ${photos.length}' in gallery_helpers
    assert 'class="gallery-lightbox-caption"' in gallery_helpers
    assert "button.classList.add('is-popping')" in gallery_helpers
    assert 'data-gallery-prev' in gallery_helpers and 'data-gallery-next' in gallery_helpers
    assert ".gallery-like.is-popping .ui-icon" in STYLES
    assert "@keyframes gallery-heart-pop" in STYLES
