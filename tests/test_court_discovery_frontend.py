"""Focused source contracts for the streamlined court-discovery journey."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
INDEX = (ROOT / "public" / "index.html").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    start_at = APP.index(start)
    return APP[start_at:APP.index(end, start_at)]


def test_quick_filters_keep_saved_and_active_first_class_with_detail_sheet_for_venue_amenities():
    setup = section("function setupMap()", "function locateMe")
    assert "quickFilters?.querySelector('[data-court-filter=\"players\"]')" in setup
    assert "legacyPlayers.dataset.courtFilter = 'active'" in setup
    assert "legacyPlayers.innerHTML = `${uiIcon('activity')} Happening now`" in setup
    assert "quickFilters?.querySelector('[data-court-filter=\"saved\"]')?.remove()" not in setup
    assert "quickFilters?.querySelector('[data-court-filter=\"games\"]')?.remove()" in setup
    assert 'data-court-filter="saved"' in INDEX
    assert "$('#bell-btn')?.addEventListener" not in setup
    assert "$('#court-sheet-expand')?.addEventListener" not in setup

    filtering = section("function applyCourtFilters", "function addCourtDistances")
    assert "court.players_here > 0 || court.active_games > 0" in filtering
    assert "filters.games && !(court.upcoming_games > 0)" in filtering
    assert "filters.saved && !(state.favIds && state.favIds.has(court.id))" in filtering

    sheet = section("function openCourtFilterSheet", "async function fetchCourtsInView")
    assert "const venueOptions = [" in sheet
    assert "['business', uiIcon('check-circle'), 'Verified venues']" in sheet
    assert "const amenityOptions = [" in sheet
    assert "Saved courts" not in sheet
    assert "Players here" not in sheet
    assert "Games to join" not in sheet
    assert "const loadedMatches = applyCourtFilters(state.courtsInView, draft).length;" in sheet
    assert "const loosensCurrentResults = COURT_DETAIL_FILTERS.some(" in sheet
    assert "const countIsComplete = !state.courtResultsTruncated && !loosensCurrentResults;" in sheet
    assert "? `Show ${matchCount} court${matchCount === 1 ? '' : 's'}`" in sheet
    assert ": 'Show results';" in sheet
    assert "modal.querySelectorAll('.court-filter-grid')" in sheet
    assert ".court-filter-actions .btn { min-height: var(--tap-min); }" in STYLES


def test_exact_court_matches_precede_places_and_open_with_selection_continuity():
    suggest = section("function renderSearchSuggest", "let searchSeq")
    assert suggest.index("uiIcon('pickleball')} Exact court") < suggest.index("uiIcon('map-pin')} Jump to area")

    listing = section("function renderCourtList", "function openSuggestEditSheet")
    assert listing.index("uiIcon('pickleball')} Exact court") < listing.index("uiIcon('map-pin')} Jump to area")
    assert "activateCourtFromDiscovery(byId.get(Number(row.dataset.court)), { preserveList: true })" in listing
    assert "[data-court-open]" in listing
    assert "openCourtFromDiscovery(byId.get(Number(button.dataset.courtBusiness)), { focusBusiness: true })" in listing
    assert "selectCourtBeforeOpen" not in listing

    markers = section("function drawMarkers", "function setCourtMarkerSelected")
    assert "on('click', () => activateCourtFromDiscovery(court))" in markers


def test_map_discovery_controls_precede_the_map_and_markers_support_keyboard_activation():
    assert INDEX.index('id="court-search"') < INDEX.index('id="map"')
    assert INDEX.index('id="court-more-filters"') < INDEX.index('id="map"')
    assert INDEX.index('id="court-list"') < INDEX.index('id="map"')

    marker_accessibility = section("function syncCourtMarkerAccessibility", "function syncClusterMarkerAccessibility")
    assert "target.setAttribute('role', 'button')" in marker_accessibility
    assert "target.setAttribute('aria-label', label)" in marker_accessibility
    assert "target.tabIndex = 0" in marker_accessibility
    assert "target.addEventListener('keydown'" in marker_accessibility
    assert "['Enter', ' '].includes(event.key)" in marker_accessibility
    assert "event.preventDefault();" in marker_accessibility
    assert "activateCourtFromDiscovery(court);" in marker_accessibility

    clusters = section("function syncClusterMarkerAccessibility", "function drawMarkers")
    assert "#map .court-cluster-hit" in clusters
    assert "target.setAttribute('aria-label'" in clusters
    assert "target.click();" in clusters

    selection = section("function setCourtMarkerSelected", "function clearCourtSelection")
    assert "document.activeElement === current" in selection
    assert "syncCourtMarkerAccessibility(entry.marker, entry.court, { restoreFocus })" in selection


def test_compact_map_keeps_filters_reachable_and_peek_shows_actionable_nearby_cards():
    assert '#tab-courts { --court-sheet-peek: 250px; }' in STYLES
    assert 'bottom: var(--court-sheet-peek);' in STYLES
    assert 'translateY(calc(100% - var(--court-sheet-peek)))' in STYLES
    assert '.map-filters [data-court-filter="business"] { display: none; }' in STYLES
    assert '#tab-courts { --court-sheet-peek: 146px; }' in STYLES
    assert '@media (max-height: 740px) and (orientation: portrait)' in STYLES
    assert '.court-sheet[data-snap="half"] { transform: translateY(0); }' in STYLES
    assert '.court-sheet[data-snap="peek"] .court-sheet-summary' in STYLES
    assert '.court-peek-main' in STYLES

    listing = section("function renderCourtList", "function openSuggestEditSheet")
    assert "const peekCourt = displayCourts.find" in listing
    assert "const peekResultLimit = 3" in listing
    assert "courtPeekCardHtml(court, index)" in listing
    assert 'class="court-peek-strip"' in listing
    assert 'Browse all ${availableCourtCount} court' in listing
    assert "window.innerHeight <= 740" in listing
    peek = section("function courtPeekCardHtml", "function sortCourts")
    assert "? 'Selected court' : index === 0 ? 'Nearest result' : 'Nearby result'" in peek
    assert 'data-court-open="${c.id}"' in peek
    assert '.court-peek-strip' in STYLES
    assert 'flex: 0 0 min(82%, 310px)' in STYLES
    snap = section("function setCourtSheetSnap", "function setupCourtSheetDrag")
    assert "if (snap === 'half' && window.innerHeight <= 740" in snap


def test_court_preview_actions_are_separate_from_the_selection_announcement():
    assert 'id="court-selection-status" class="sr-only" role="status" aria-live="polite"' in INDEX
    assert 'id="court-preview" class="court-preview hidden" role="region"' in INDEX
    preview = section("function selectCourtOnMap", "function autoCheckInStorageKey")
    assert "selectionStatus.textContent = `${court.name} selected. ${live}.`;" in preview
    assert 'data-preview-play>Play options</button>' in preview
    assert "openCourtPlayMenu(court);" in preview
    assert "gameType: 'casual'" not in preview
    assert 'aria-label="Directions to ${esc(court.name)} (opens Maps)"' in preview
    assert "uiIcon('external')" in preview


def test_court_external_actions_share_icons_and_announce_context_switches():
    detail = section("async function openCourtDetail", "function openCheckInSheet")
    assert 'aria-label="Community website for ${esc(court.name)} (opens new tab)"' in detail
    assert detail.count('aria-label="Directions to ${esc(court.name)} (opens Maps)"') >= 2
    assert detail.count("uiIcon('external')") >= 3
    assert "${uiIcon('building')} Public communities here" in detail
    assert "${uiIcon('check')} Member" in detail
    assert "🏛 Public communities here" not in detail
    assert ".court-preview-actions .btn .ui-icon" in STYLES
    assert ".cd-closed-actions .btn .ui-icon" in STYLES


def test_map_play_choice_never_treats_selection_as_physical_presence():
    chooser = section("function openCourtPlayMenu", "function selectCourtOnMap")
    assert "Picking this court does not check you in or create a game." in chooser
    assert "I’m at the court now" in chooser
    assert "Find a ranked match" in chooser
    assert "Schedule play" in chooser
    assert "openPlayNowCourtPicker({ court })" in chooser
    assert "openGameFlow({ court, mode: 'find', gameType: 'ranked' })" in chooser
    assert "openNewGameModal({ court })" in chooser
    assert "data-court-play-now" in chooser
    assert "data-court-play-ranked" in chooser
    assert "data-court-play-schedule" in chooser
    assert "min-height: 66px" in STYLES


def test_location_auto_centers_only_after_an_existing_permission_grant():
    setup = section("function setupMap()", "// ---------- Theme")
    assert "maybeAutoLocateCourts();" in setup
    permission = section("async function maybeAutoLocateCourts", "function locateMe")
    assert "navigator.permissions.query({ name: 'geolocation' })" in permission
    assert "if (permission.state === 'granted') return locateMe(true, { automatic: true });" in permission
    assert "state.courtLocationPermission = 'prompt';" in permission
    assert "getCurrentPosition" not in permission
    locate = section("function locateMe", "function committedAreaLatLng")
    assert "btn?.dataset.locating === 'true'" in locate
    assert "if (state.courtLocationRequest) return state.courtLocationRequest;" in locate
    assert "btn.setAttribute('aria-busy', 'true')" in locate
    assert "btn.disabled = true" in locate
    assert "btn.removeAttribute('aria-busy')" in locate
    assert "maximumAge: 60000" in locate
    assert "currentCenter.distanceTo(centerAtStart) < 25" in locate


def test_court_discovery_selects_before_async_detail_and_restores_fresh_focus():
    helper = section("function courtDiscoveryReturnFocus", "function selectCourtOnMap")
    suggest = section("function renderSearchSuggest", "let searchSeq")
    modal = section("function openModal", "function closeModal")
    detail = section("async function openCourtDetail", "function openCheckInSheet")

    assert '#court-list-items [data-court="${Number(courtId)}"]' in helper
    assert "|| $('#court-search');" in helper
    assert helper.index("selectCourtOnMap(court, { preserveList: true });") < helper.index("return openCourtDetail(court.id")
    assert "returnFocus: courtDiscoveryReturnFocus(court.id)" in helper
    assert "returnFocusFallback: () => courtDiscoveryReturnFocus(court.id)" in helper

    assert "const court = courts.find((candidate) => Number(candidate.id) === courtId);" in suggest
    assert "if (court) openCourtFromDiscovery(court);" in suggest
    assert "openCourtDetail(Number(row.dataset.sugCourt))" not in suggest

    assert "backdrop._returnFocus = opts.returnFocus || previousFocus;" in modal
    assert "backdrop._returnFocusFallback = opts.returnFocusFallback || null;" in modal
    assert "returnFocus = null" in detail
    assert "returnFocusFallback = null" in detail
    assert "returnFocus," in detail
    assert "returnFocusFallback," in detail


def test_court_detail_opens_an_accessible_busy_sheet_and_recovers_in_place():
    detail = section("let pendingCourtDetailOpen = null", "function openCheckInSheet")

    # The dialog appears synchronously, before any network wait, and owns the
    # route while it is loading so closing or superseding it invalidates the
    # eventual response.
    assert detail.index("const modal = reuseModal || openModal(`") < detail.index(
        "await api(`/courts/${normalizedCourtId}`)"
    )
    assert 'role="status" aria-live="polite"' in detail
    assert "modalBox.setAttribute('aria-busy', 'true');" in detail
    assert detail.index("const modal = reuseModal || openModal(`") < detail.index(
        "const routeLoad = beginRoutedOverlayLoad"
    )
    assert "!routedOverlayLoadIsCurrent(routeLoad) || !modal.isConnected" in detail

    # A second tap for the same court reuses the live sheet, while a different
    # court replaces it instead of leaving an abandoned loading overlay.
    assert "pendingCourtDetailOpen.courtId === normalizedCourtId" in detail
    assert "return pendingCourtDetailOpen.modal;" in detail
    assert "transitionModal(previousModal, () => openCourtDetail(normalizedCourtId" in detail
    assert "pendingCourtDetailOpen?.modal === modal" in detail

    # A failed request becomes a persistent, announced error with a real retry.
    # Successful hydration clears busy state and announces the loaded court.
    assert 'class="court-detail-load-error" role="alert"' in detail
    assert "data-retry-court-detail" in detail
    assert "transitionModal(modal, () => openCourtDetail(normalizedCourtId" in detail
    assert "clearDeadDeepLink(`#court/" not in detail
    assert "modalBox.setAttribute('aria-busy', 'false');" in detail
    assert "Court details loaded for ${esc(court.name)}" in detail
    assert "modal.querySelector('.cd-scroll')?.setAttribute('data-scroll', '');" in detail

    assert ".court-detail-load-shell" in STYLES
    assert ".court-detail-load-close" in STYLES
    assert ".court-detail-load-error .btn" in STYLES
    assert "@media (prefers-reduced-motion: reduce)" in STYLES


def test_typed_court_name_prefix_is_a_strong_match_before_places_everywhere():
    matching = section("function splitExactCourtNameMatches", "function renderSearchSuggest")
    assert "const queryName = String(q || '').trim().toLocaleLowerCase();" in matching
    assert "const courtName = String(court.name || '').trim().toLocaleLowerCase();" in matching
    assert "queryName && (courtName === queryName || courtName.startsWith(queryName))" in matching

    suggest = section("function renderSearchSuggest", "let searchSeq")
    assert "const { exact, other } = splitExactCourtNameMatches(courts, q);" in suggest
    assert suggest.index("courtRowsHtml(exact.slice(0, 2))") < suggest.index("places.slice(0, 4)")

    listing = section("function renderCourtList", "function openSuggestEditSheet")
    assert "? splitExactCourtNameMatches(courts, state.searchQ)" in listing
    assert "const displayCourts = [...searchMatches.exact, ...searchMatches.other];" in listing
    assert listing.index("visibleExact.map(courtRowHtml)") < listing.index("html += placesHtml")


def test_search_and_filter_refreshes_do_not_leave_stale_actionable_results():
    setup = section("function setupMap()", "function locateMe")
    refresh = section("function refreshCourtResults", "function clearCourtFilters")
    search = section("async function searchCourts", "function courtMarkerIcon")

    input_handler = setup[setup.index("searchInput.addEventListener('input'") :]
    assert input_handler.index("hideSearchSuggest();") < input_handler.index("setTimeout(() => refreshCourtResults")
    assert "surfaceError = true" in refresh
    assert "if (showLoading) beginCourtContextRefresh(label);" in refresh
    assert "hideSearchSuggest();" in search
    assert "renderCourtContextError(error" in search
    assert "return false;" in search


def test_quiet_cards_hide_zero_metrics_and_zero_results_hide_sorting():
    card = section("function courtRowHtml", "function sortCourts")
    assert "if (c.players_here > 0)" in card
    assert "if (c.active_games > 0)" in card
    assert "c.upcoming_games > 0" in card
    assert "later session" in card
    assert "if (c.rating_count > 0 && c.rating_avg)" in card
    assert 'class="court-card-quiet">No players checked in' in card

    summary = section("function syncCourtSheetSummary", "function openCourtListPanel")
    assert "const activitySortOwnsOrder = state.courtFilters.active && n > 0;" in summary
    assert "sort?.classList.toggle('hidden', n === 0 || activitySortOwnsOrder)" in summary
    assert "sortLabel?.classList.toggle('hidden', n === 0 || activitySortOwnsOrder)" in summary
    assert "activeSortNote?.classList.toggle('hidden', !activitySortOwnsOrder)" in summary
    assert "syncAppSelect(sort)" in summary


def test_court_actions_are_visible_without_opening_a_hero_overflow_menu():
    detail = section("async function openCourtDetail", "function openCheckInSheet")
    actions = "\n".join(
        block for block in STYLES.split("}") if ".cd-hero-actions" in block
    )
    assert "z-index: 3" in actions
    assert 'class="cd-quick-actions" role="group" aria-label="Court actions"' in detail
    assert detail.index("${quickActions}") < detail.index('class="card cd-now-card"')
    assert 'aria-label="More court actions"' not in detail
    assert ".cd-quick-actions button" in STYLES


def test_court_sort_uses_the_shared_app_picker_with_explanatory_choices():
    assert 'id="court-sort"' in INDEX
    assert 'data-select-title="Sort courts"' in INDEX
    assert 'data-select-prefix="Sort"' in INDEX
    for value in ("distance", "rating", "courts"):
        assert f'value="{value}"' in INDEX
    assert 'value="active"' not in INDEX
    for description in (
        "Shortest trip from the center of the map",
        "Highest community ratings first",
        "Largest pickleball facilities first",
    ):
        assert description in INDEX

    activity_sort = section("function syncActiveCourtSort", "function syncCourtFilterControls")
    assert "state.courtSortBeforeActive" in activity_sort
    assert "state.listSort = 'active'" in activity_sort
    assert "state.listSort = allowed.has(state.courtSortBeforeActive)" in activity_sort
    assert 'id="court-active-sort-note"' in INDEX

    picker = section("function openAppSelectSheet", "function enhanceAppSelect")
    assert 'role="listbox"' in picker
    assert 'role="option"' in picker
    assert 'aria-selected="${selected}"' in picker
    assert 'tabindex="${selected ? \'0\' : \'-1\'}"' in picker
    assert "select.dispatchEvent(new Event('change', { bubbles: true }))" in picker
    assert "closeModal(sheet)" in picker
    assert "sheet._returnFocus = trigger" in picker
    assert "['ArrowDown', 'ArrowUp', 'Home', 'End']" in picker
    assert "['Enter', ' '].includes(event.key)" in picker
    assert "choose(choice);" in picker
    assert "candidate.setAttribute('aria-selected', String(selected))" in picker
    assert "candidate.classList.toggle('is-active', active)" in picker
    assert "const activeChoices = () =>" in picker
    assert "const roving = visible.find((choice) => choice.tabIndex === 0)" in picker
    assert "search.addEventListener('keydown'" in picker
    assert "setActiveChoice(target, { focus: true })" in picker
    assert "select._refreshAppSelectSheet = renderChoices" in picker
    assert ".app-select-option.is-selected" in STYLES
    assert ".app-select-option:focus-visible" in STYLES


def test_all_native_selects_are_presented_through_one_branded_picker():
    enhancement = section("function enhanceAppSelect", "function enhanceAppSelects")
    assert "select.classList.add('app-select-native')" in enhancement
    assert "select.setAttribute('aria-hidden', 'true')" in enhancement
    assert "button.setAttribute('aria-haspopup', 'dialog')" in enhancement
    assert "button.setAttribute('aria-expanded', 'false')" in enhancement
    assert "new MutationObserver" in enhancement
    assert "select._refreshAppSelectSheet?.()" in enhancement
    assert "setupAppSelects();" in APP
    assert "enhanceAppSelects(backdrop);" in APP
    assert ".app-select-trigger" in STYLES
    assert "min-height: 48px" in STYLES
    assert ".app-select-search" in STYLES
    assert ".app-select-trigger-chevron" in STYLES


def test_court_cards_expose_the_visible_decision_data_in_their_accessible_name():
    card = section("function courtRowHtml", "function sortCourts")
    assert "const accessibleActivity = [" in card
    assert "quietNow ? 'No players checked in'" in card
    assert "const accessibleAmenities = [" in card
    assert "cond ? cond[1] : ''" in card
    assert "`${c.num_courts} court${c.num_courts === 1 ? '' : 's'}`" in card
    assert "`${c.rating_avg} stars from ${c.rating_count} rating" in card
    assert "const accessibleSummary = [" in card
    assert 'aria-label="${esc(accessibleSummary)}"' in card
    assert 'aria-label="View ${esc(c.name)}"' not in card


def test_verified_venue_programs_have_a_direct_accessible_discovery_path():
    availability = section(
        "function courtBusinessDiscoveryLabel", "function selectCourtOnMap",
    )
    for signal in (
        "business.booking_available", "business.membership_available",
        "business.schedule_available", "business.programs_available",
    ):
        assert signal in availability

    preview = section("function selectCourtOnMap", "function autoCheckInStorageKey")
    assert 'data-preview-business>${esc(businessLabel)}</button>' in preview
    assert "openCourtDetail(court.id, { focusBusiness: true })" in preview
    assert 'court-preview-actions ${businessDiscovery ? \'has-business\'' in preview

    card = section("function courtRowHtml", "function sortCourts")
    assert '<article class="court-decision-card' in card
    assert 'class="court-card-main" data-court=' in card
    assert 'class="court-card-programs" data-court-business=' in card
    assert "`${businessLabel} available from the venue`" in card
    # Court activation must work for keyboard and pointer users alike. Other
    # views may inspect input type solely to restore focus after rendering.
    assert "event.detail === 0" not in preview
    assert "event.detail === 0" not in card
    assert ".court-card-programs" in STYLES
    assert "min-height: 44px" in STYLES
    assert ".court-preview-actions.has-business" in STYLES

    business = section("async function loadCourtBusiness", "async function openCourtDetail")
    assert "{ expanded = false }" in business
    assert "if (services) services.open = true" in business
    assert "if (schedule) schedule.open = true" in business
    assert "slot.querySelector('.business-action[href]')" in business
    assert "render(business, { final: true })" in business
    assert "prefers-reduced-motion: reduce" in business
    assert "slot.scrollIntoView({ behavior: scrollBehavior(), block: 'start' })" in business
    detail = section("async function openCourtDetail", "function openChallengeSheet")
    assert "focusBusiness = false" in detail
    assert "loadCourtBusiness(modal, court, { expanded: focusBusiness })" in detail


def test_business_schedule_actions_exclude_past_inventory():
    current = section(
        "function calendarDateInTimeZone", "function courtBusinessHtml",
    )
    assert "new Intl.DateTimeFormat('en-US'" in current
    assert "timeZone, year: 'numeric', month: '2-digit', day: '2-digit'" in current
    assert "calendarDateInTimeZone(now, item.timezone || 'UTC')" in current
    assert "if (item.event_date) return String(item.event_date) >= today" in current
    assert "if (item.end_date) return String(item.end_date) >= today" in current
    assert "if (recurrence === 'dated' || recurrence === 'date_range') return false" in current
    business = section("function courtBusinessHtml", "async function loadCourtBusiness")
    assert "&& businessScheduleItemIsCurrent(item))" in business

    hydration = section("async function loadCourtBusiness", "async function openCourtDetail")
    assert "date.getUTCFullYear()" in hydration
    assert "date.getUTCMonth()" in hydration
    assert "date.getUTCDate()" in hydration
    assert "from.setUTCDate(from.getUTCDate() - 1)" in hydration
    assert "to.setUTCDate(to.getUTCDate() + 91)" in hydration
    assert "utcDateValue(from)" in hydration
    assert "utcDateValue(to)" in hydration


def test_court_detail_leads_with_now_and_defers_venue_management():
    detail = section("async function openCourtDetail", "function openCheckInSheet")
    now_at = detail.index('id="cd-now-heading"')
    players = detail.index('id="cd-sec-players"')
    games = detail.index('id="cd-sec-games"')
    disclosure = detail.index('class="card cd-progressive cd-court-details"')
    assert now_at < games < players < disclosure
    assert "courtClosed ? 'Closed to new play' : 'Playing and forming now'" in detail
    assert "const nowGames = Array.isArray(court.now_games) ? court.now_games" in detail
    assert "const actionableGames = nowGames.filter((game) => game.is_joined || Number(game.spots_left) > 0);" in detail
    assert "const nGames = actionableGames.length;" in detail
    assert '<b>${nGames}</b><span>open session${nGames === 1 ? \'\' : \'s\'}</span>' in detail
    assert "const myOpenGame = nowGames.find((game) => game.is_joined && game.status === 'upcoming') || null;" in detail
    assert "const primaryAction = courtClosed ? '' : myOpenGame" in detail
    assert 'id="cd-open-game" data-game-id="${myOpenGame.id}">Open your play session' in detail
    assert "checkedIn ? 'Look for a game' : 'Check in to find players'" in detail
    assert "Create game here" in detail
    assert "Set up ranked match" in detail
    assert "const directionsAction = mapsUrl ?" in detail
    assert "${directionsAction}" in detail
    assert 'id="cd-checkout">Check out' in detail
    assert "${venueBusiness ? 'Community court details' : 'Court details'}" in detail
    assert '<span>More at this court</span><small id="cd-more-preview">' in detail
    assert 'class="card cd-progressive cd-reviews-details"' in detail
    assert "modal.querySelector('#cd-checkin')?.addEventListener" in detail
    assert "modal.querySelector('#cd-checkout')?.addEventListener" in detail
    assert "modal.querySelector('#cd-play-now')?.addEventListener" in detail
    assert "const lookingForGame = checkedIn && court.is_looking_for_game === true;" in detail
    assert 'id="cd-looking-toggle" aria-pressed="${lookingForGame}"' in detail
    assert "body: JSON.stringify({ looking_for_game: desiredLooking })" in detail
    assert "{ defaultLooking: false }" in detail
    assert "{ defaultLooking: true }" in detail
    assert "gameType: 'ranked', community: false" in detail


def test_closed_court_replaces_live_actions_and_favorite_writes_are_idempotent():
    detail = section("async function openCourtDetail", "function openCheckInSheet")
    assert "const courtClosed = court.closed === true;" in detail
    assert "const primaryAction = courtClosed ? ''" in detail
    assert "const secondaryActions = courtClosed ?" in detail
    assert "Check-ins, new play sessions, ranked matches, and court chat are paused here." in detail
    assert "Player-organized sessions are paused while this court is marked closed." in detail
    assert "? `<button type=\"button\" data-cd-suggest>" in detail
    assert "p.is_me || court.closed ? ''" in detail
    assert ": `<button type=\"button\" id=\"cd-condition\">" in detail
    assert "uiIcon('activity')" in detail
    assert "courtClosed || venueBusiness ? ''" in detail
    assert "${checkedIn ? '' : `<button type=\"button\" class=\"btn btn-secondary\" id=\"cd-checkin\"" in detail
    assert "${directionsAction}" in detail
    assert "if (!favoriteButton.disabled) saveFavorite(!isFavorite);" in detail
    assert "applyFavoriteLocally(desiredFavorite);" in detail
    assert "method: 'PUT', body: JSON.stringify({ favorited: desiredFavorite })" in detail
    assert "favoriteButton.setAttribute('aria-busy', 'true');" in detail
    assert "favoriteButton.removeAttribute('aria-busy');" in detail
    assert "label: 'Undo'" in detail
    assert ".cd-closed-actions" in STYLES


def test_court_address_is_a_native_directions_link():
    detail = section("async function openCourtDetail", "function openCheckInSheet")
    assert "const mapsUrl = courtDirectionsUrl(court);" in detail
    assert '<a id="cd-address" class="cd-address-copy" href="${mapsUrl}"' in detail
    assert 'target="_blank" rel="noopener" aria-label="Directions to ${esc(court.name)} (opens Maps)"' in detail
    assert '<div id="cd-address" role="button"' not in detail
    assert "modal.querySelector('#cd-address').addEventListener('click'" not in detail
    assert "navigator.clipboard.writeText(courtAddressText)" not in detail
    address_css = next(block for block in STYLES.split("}") if block.lstrip().startswith(".cd-address-copy {"))
    assert "min-height: var(--tap-min)" in address_css
    assert "text-decoration: none" in address_css


def test_user_owned_map_moves_offer_a_meaningful_area_commit_without_swallowing_suppression():
    area = section("function committedAreaLatLng", "function areaViewKey")
    assert "if (state.areaLoc) return" in area
    assert "if (state.userLoc) return" in area
    assert "function courtDistanceOrigin()" in area
    assert "if (!committed)" in area
    assert "button.classList.remove('hidden');" in area
    assert "const movedMiles = milesBetween(" in area
    assert "movedMiles < 0.25" in area

    setup = section("function setupMap()", "function locateMe")
    moveend = setup[setup.index("state.map.on('moveend'") : setup.index("useMapAreaButton?.addEventListener")]
    assert moveend.index("if (state.suppressCourtMoveFetch)") < moveend.index("syncUseMapAreaAction();")
    assert "state.map.on('dragend'" not in setup
    assert "const reference = courtDistanceOrigin();" in section(
        "async function fetchCourtsInView", "function safePositiveId",
    )
    assert "const reference = courtDistanceOrigin();" in section(
        "async function searchCourts", "function courtMarkerIcon",
    )

    jump = section("function jumpToPlace", "async function loadFavIds")
    assert "state.areaLoc" not in jump
    assert "state.areaLabel" not in jump
    assert "state.playGamesCache" not in jump
    assert "state.chatFriendsCache" not in jump
    assert "syncUseMapAreaAction();" in jump
    assert "Viewing ${label} on the court map" in jump


def test_result_region_and_empty_search_popup_keep_accessible_state_current():
    sheet = section("function syncSearchClear", "function setupCourtSheetDrag")
    assert "function syncCourtSheetLabel()" in sheet
    assert "state.courtSheetSnap === 'peek' ? 'map view' : 'list view'" in sheet
    assert "syncCourtSheetLabel();" in sheet

    summary = section("function syncCourtSheetSummary", "function openCourtListPanel")
    assert "syncCourtSheetLabel();" in summary

    suggest = section("function renderSearchSuggest", "let searchSeq")
    assert 'class="sug-empty" role="status" aria-live="polite"' in suggest
    assert "setAttribute('aria-expanded', 'true')" in suggest
    listing = section("function renderCourtList", "function openSuggestEditSheet")
    assert 'aria-label="View ${esc(p.label)} on the court map"' in listing


def test_list_mode_removes_the_covered_map_from_pointer_and_keyboard_navigation():
    snap = section("function setCourtSheetSnap", "function setupCourtSheetDrag")
    assert "const mapHadFocus = !!mapEl?.contains(document.activeElement);" in snap
    assert "mapEl.inert = hideMap;" in snap
    assert "const hideMap = listOpen && !desktop;" in snap
    assert "if (desktop) snap = 'half';" in snap
    assert "mapEl.setAttribute('aria-hidden', 'true')" in snap
    assert "mapEl.removeAttribute('aria-hidden')" in snap
    assert "if (hideMap && mapHadFocus)" in snap
    assert "$('#court-preview')?.querySelector('button, a[href]') || cycle" in snap
    assert "focusTarget?.focus({ preventScroll: true })" in snap


def test_court_sheet_announces_real_mode_changes_and_rerenders_empty_lists():
    assert 'id="court-sheet-status" class="sr-only" role="status" aria-live="polite"' in INDEX
    snap = section("function setCourtSheetSnap", "function setupCourtSheetDrag")
    assert "if (announce && previousSnap !== snap)" in snap
    assert "snap === 'peek' ? 'Map view'" in snap
    assert "snap === 'half' ? 'Court list expanded' : 'Full court list'" in snap
    assert "&& state.courtsInView.length" not in snap
    assert "renderCourtList(state.courtsInView, state.courtListPlaces" in snap


def test_court_sort_reorders_loaded_results_without_refetching_or_losing_selection():
    setup = section("function setupMap()", "// ---------- Theme")
    sort_handler = setup[setup.index("$('#court-sort').addEventListener") : setup.index(
        "setupCourtSheetDrag();"
    )]
    assert "state.listSort = e.target.value;" in sort_handler
    assert "renderCourtList(state.courtsInView, state.courtListPlaces" in sort_handler
    assert "savedOnly: state.courtListSavedOnly" in sort_handler
    assert "refreshCourtResults" not in sort_handler


def test_court_selection_is_cleared_as_one_accessible_state_transition():
    clearing = section("function clearCourtSelection", "function courtDirectionsUrl")
    assert "const previousCourtId = state.selectedCourtId;" in clearing
    assert "state.selectedCourtId = null;" in clearing
    assert "setCourtMarkerSelected(previousCourtId, false)" in clearing
    assert "preview.replaceChildren();" in clearing
    assert "preview.classList.add('hidden');" in clearing
    assert "card.classList.remove('selected');" in clearing
    assert "setAttribute('aria-pressed', 'false')" in clearing
    assert "selectionStatus.textContent = message;" in clearing

    refresh = section("function beginCourtContextRefresh", "function renderCourtContextError")
    assert "clearCourtSelection();" in refresh
    assert "state.selectedCourtId = null;" not in refresh
    listing = section("function renderCourtList", "function openSuggestEditSheet")
    assert "clearCourtSelection('The selected court is not in these results.');" in listing


def test_every_search_suggestion_meets_the_shared_tap_target_minimum():
    suggestion_css = next(
        block for block in STYLES.split("}") if block.lstrip().startswith(".sug-row {")
    )
    assert "min-height: var(--tap-min)" in suggestion_css


def test_court_search_is_one_activedescendant_combobox_and_escape_dismisses_the_query():
    setup = section("function setupMap()", "function locateMe")
    search_keys = setup[setup.index("searchInput.addEventListener('keydown'") :]
    assert "state.dismissedSearchSuggestionQuery = state.searchQ;" in search_keys
    assert "state.dismissedSearchSuggestionQuery !== state.searchQ" in search_keys
    assert "['ArrowDown', 'ArrowUp', 'Home', 'End']" in search_keys
    assert "searchInput.setAttribute('aria-activedescendant'" in search_keys

    suggest = section("function renderSearchSuggest", "let searchSeq")
    assert "state.dismissedSearchSuggestionQuery === q" in suggest
    assert 'role="option" tabindex="-1" aria-selected="false"' in suggest
    assert "uiIcon('pickleball')" in suggest
    assert "uiIcon('map-pin')" in suggest
    assert "uiIcon('chevron-right', 'chev')" in suggest
    assert '<span class="chev">›</span>' not in suggest


def test_court_map_filters_and_results_are_named_regions_or_groups():
    assert 'id="tab-courts" class="tab-panel" aria-label="Find pickleball courts"' in INDEX
    assert 'id="map" role="region" aria-label="Interactive pickleball court map"' in INDEX
    assert 'class="map-topbar" role="search" aria-label="Search pickleball courts and areas"' in INDEX
    assert 'id="map-filters" role="group" aria-label="Quick court filters"' in INDEX
    assert 'id="court-list" class="sheet court-sheet" data-snap="peek" role="region"' in INDEX
    assert 'id="court-list-items" class="court-sheet-results" role="region" aria-label="Court result list"' in INDEX


def test_escape_closes_court_more_disclosure_before_the_detail_dialog():
    modal = section("function openModal", "function armReusableOverlayUnwind")
    escape = modal[modal.index("if (e.key === 'Escape')") :]
    assert "'.cd-hero-more[open], .thread-more-actions[open], .friend-row-more[open], .profile-more-actions.is-open'" in escape
    assert escape.index("openActionMenu.open = false;") < escape.index("dismissModal(backdrop);")
    assert "openActionMenu.querySelector('summary, [aria-haspopup=\"menu\"]')?.focus();" in escape


def test_single_markers_use_a_venue_symbol_while_clusters_keep_counts():
    marker = section("function courtMarkerIcon", "function drawMarkers")
    assert "const integrated = !!court.business;" in marker
    assert "marker-venue-badge" in marker
    assert "Official venue profile from ${court.business.name}" in marker
    assert "integrated ? `<span aria-hidden=\"true\">${markerUiIcon('building', 'court-marker-main-icon')}</span>`" in marker
    assert "markerUiIcon('pickleball', 'court-marker-main-icon is-pickleball')" in marker
    assert "markerUiIcon('users', 'court-marker-people-icon')" in marker
    assert "markerUiIcon('star', 'court-marker-badge-icon')" in marker
    clusters = section("function setupMap()", "function locateMe")
    assert "${n}</div>" in clusters


def test_verified_venues_are_discoverable_and_court_people_have_contextual_actions():
    filtering = section("function applyCourtFilters", "function addCourtDistances")
    assert "filters.business && !court.business" in filtering
    assert "['business', uiIcon('check-circle'), 'Verified venues']" in APP
    assert "Verified venues" in APP
    assert 'role="status" aria-live="polite"' in INDEX
    assert ".court-marker.integrated" in STYLES
    assert ".marker-venue-badge" in STYLES
    assert ".verified-venue-badge" in STYLES

    detail = section("async function openCourtDetail", "function openCourtPlayerActions")
    assert 'data-court-player-actions="${p.id}"' in detail
    assert "p.friendship_state === 'incoming'" in detail
    actions = section("function openCourtPlayerActions", "function openChallengeSheet")
    assert "Accept friend request" in actions
    assert "uiIcon('plus')" in actions
    assert "Add as friend" in actions
    assert "Challenge to a ranked match" in actions
    assert "api(`/friends/${player.friendship_id}/respond`" in actions
