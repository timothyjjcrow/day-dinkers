"""Source contracts for the shared frontend interaction foundation."""

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
INDEX = (ROOT / "public" / "index.html").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def app_section(start: str, end: str) -> str:
    start_at = APP.index(start)
    return APP[start_at:APP.index(end, start_at)]


def js_function(name: str) -> str:
    """Return one top-level function from the app bundle."""
    match = re.search(rf"^  (?:async )?function {re.escape(name)}\b", APP, flags=re.MULTILINE)
    assert match, f"Missing JavaScript function: {name}"
    next_match = re.search(r"^  (?:async )?function [A-Za-z_$]", APP[match.end():], flags=re.MULTILINE)
    end = match.end() + next_match.start() if next_match else len(APP)
    return APP[match.start():end]


_CLEAN_STYLES = re.sub(r"/\*.*?\*/", "", STYLES, flags=re.DOTALL)


def css_declarations(selector: str) -> list[str]:
    """Return declaration blocks whose selector list contains an exact selector."""
    blocks = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", _CLEAN_STYLES):
        selectors = [re.sub(r"\s+", " ", item).strip() for item in match.group(1).split(",")]
        if selector in selectors:
            blocks.append(match.group(2))
    return blocks


def test_shared_ui_icon_helper_is_backed_by_one_accessible_svg_sprite():
    helper = app_section("const UI_ICON_NAMES", "// Public business profiles")
    names_source = helper[helper.index("new Set([") : helper.index("]);", helper.index("new Set(["))]
    names = set(re.findall(r"'([a-z0-9-]+)'", names_source))

    assert {"users", "trophy", "calendar", "map-pin", "arrow-left", "x", "pickleball"} <= names
    for name in names:
        symbol_id = "pb" if name == "pickleball" else f"ui-{name}"
        assert INDEX.count(f'id="{symbol_id}"') == 1

    literal_calls = set(re.findall(r"(?:uiIcon|markerUiIcon)\('([a-z0-9-]+)'", APP))
    option_names = set(re.findall(r'data-icon-name="([a-z0-9-]+)"', APP + INDEX))
    assert literal_calls <= names
    assert option_names <= names

    assert "function uiIcon(name, className = '')" in helper
    assert "UI_ICON_NAMES.has(name) ? name : 'pickleball'" in helper
    assert "aria-hidden=\"true\" focusable=\"false\"" in helper
    assert '<use href="#${symbol}"></use>' in helper

    icon_css = "\n".join(css_declarations(".ui-icon"))
    assert "stroke: currentColor" in icon_css
    assert "pointer-events: none" in icon_css
    assert "var(--icon-md)" in icon_css

    # The two universal modal navigation glyphs use the same product icon API.
    assert re.search(r"modalHead\s*=.*uiIcon\('x'\)", APP)
    assert "back.innerHTML = uiIcon('arrow-left');" in APP


def test_product_disclosures_share_a_geometric_tap_safe_indicator():
    direct_disclosures = (
        ".play-game-depth",
        "#ng-other-time",
        ".roster-boost-more",
        ".game-more-actions",
        ".game-danger-actions",
        ".business-offering-more",
        ".business-schedule-more",
        ".rankings-more",
    )
    disclosure_selectors = [
        (
            f"{base} > summary",
            f"{base}[open] > summary::after",
        )
        for base in direct_disclosures
    ]
    disclosure_selectors.append((".cd-hours summary", ".cd-hours[open] summary::after"))

    assert "--tap-min: 44px" in STYLES
    for summary_selector, open_selector in disclosure_selectors:
        summary_css = "\n".join(css_declarations(summary_selector))
        marker_css = "\n".join(css_declarations(f"{summary_selector}::-webkit-details-marker"))
        indicator_css = "\n".join(css_declarations(f"{summary_selector}::after"))
        open_css = "\n".join(css_declarations(open_selector))

        assert "min-height: var(--tap-min)" in summary_css
        assert "list-style: none" in summary_css
        assert "display: none" in marker_css
        assert "content: ''" in indicator_css
        assert "border-right: 2px solid currentColor" in indicator_css
        assert "border-bottom: 2px solid currentColor" in indicator_css
        assert "rotate(45deg)" in indicator_css
        assert "rotate(225deg)" in open_css

    # Product disclosure/refresh indicators must be CSS geometry, not font glyphs.
    assert not re.search(r"content\s*:\s*(['\"])[›▾↻]\1", STYLES)


def test_custom_select_keeps_roving_focus_separate_from_committed_selection():
    picker = app_section("function openAppSelectSheet", "function enhanceAppSelect")
    active = app_section("const setActiveChoice", "const choose")

    assert "const selectedIndex = String(select.selectedIndex);" in active
    assert "const active = candidate === choice;" in active
    assert "const selected = candidate.dataset.appSelectIndex === selectedIndex;" in active
    assert "candidate.tabIndex = active ? 0 : -1;" in active
    assert "candidate.classList.toggle('is-active', active);" in active
    assert "candidate.classList.toggle('is-selected', selected);" in active
    assert "candidate.setAttribute('aria-selected', String(selected));" in active
    assert "candidate.setAttribute('aria-selected', String(active));" not in picker

    # Arrow keys may move the active row, while Enter/Space alone commits it.
    assert "setActiveChoice(target, { focus: true });" in picker
    assert "select.value = option.value;" in picker
    assert "choose(choice);" in picker

    active_css = "\n".join(css_declarations(".app-select-option.is-active"))
    selected_css = "\n".join(css_declarations(".app-select-option.is-selected"))
    assert active_css and selected_css
    assert active_css != selected_css


def test_custom_select_and_shared_status_controls_use_product_icons():
    picker = app_section("function openAppSelectSheet", "function enhanceAppSelect")
    map_setup = app_section("async function ensureMapReady", "async function fetchCourtsInView")

    assert "uiIcon('check')" in picker
    assert 'aria-hidden="true">✓</span>' not in picker
    assert 'id="ui-check"' in INDEX
    assert 'class="offline-status"' in INDEX
    assert '<use href="#ui-alert-triangle"' in INDEX
    assert "useMapAreaButton.innerHTML = `${uiIcon('map-pin')}" in map_setup
    assert ".court-context-strip > :not(.hidden) ~ :not(.hidden)" in STYLES
    assert 'class="court-context-strip"' in INDEX
    assert re.search(r"modalHead\s*=.*<button type=\"button\"", APP)
    for control_id in ("auth-toggle", "search-clear", "locate-btn", "looking-banner"):
        assert re.search(rf'<button\s+[^>]*type="button"[^>]*id="{control_id}"', INDEX)


def test_high_frequency_controls_use_a_shared_44px_or_larger_token():
    token_values = {
        name: float(value)
        for name, value in re.findall(
            r"(--[a-z0-9-]*(?:tap|touch|hit|target|control)[a-z0-9-]*)\s*:\s*(\d+(?:\.\d+)?)px\b",
            STYLES,
            flags=re.IGNORECASE,
        )
    }
    usable_tokens = {name for name, value in token_values.items() if value >= 44}
    assert usable_tokens, "Define a semantic interaction-size token of at least 44px"

    high_frequency_selectors = (
        ".app-select-trigger",
        ".btn-sm",
        ".header-icon-btn",
        ".icon-btn",
        ".map-filters button",
        ".nav-btn",
        ".segmented button",
        ".modal-close",
    )
    missing = []
    for selector in high_frequency_selectors:
        declarations = "\n".join(css_declarations(selector))
        if not declarations or not any(f"var({token})" in declarations for token in usable_tokens):
            missing.append(selector)

    assert not missing, (
        "High-frequency controls must consume a shared >=44px token instead of "
        f"drifting back to one-off dimensions: {', '.join(missing)}"
    )


def test_async_detail_drill_ins_mount_a_retryable_destination_before_fetching():
    helper = app_section("function openDetailLoadShell", "function emptyStateHtml")
    assert "modal = openModal" in helper
    assert "beginRoutedOverlayLoad(route)" in helper
    assert helper.index("modal = openModal") < helper.index("beginRoutedOverlayLoad(route)")
    assert 'aria-live="polite"' in helper
    assert "setAttribute('aria-busy', 'true')" in helper
    assert "function renderDetailLoadError" in helper
    assert "renderError(shell.body, message, retryFn)" in helper

    for start, end, endpoint in (
        ("async function openGameScreen", "function safeNotificationOverlayRoute", "/games/${gameId}"),
        ("async function openUserProfile", "function gameFingerprint", "/users/${userId}"),
        ("async function openCourtGallery", "async function openGameChat", "/courts/${court.id}/photos"),
        ("async function openActivity", "// ---------- Presence banner", "/notifications"),
        ("async function openClubScreen", "async function openClubChat", "/clubs/${clubId}"),
        ("async function openCrewScreen", "async function openCrewChatById", "/crews/${crewId}"),
    ):
        source = app_section(start, end)
        assert "openDetailLoadShell({" in source
        assert endpoint in source
        assert source.index("openDetailLoadShell({") < source.index("await api(")
        assert "renderDetailLoadError(" in source


def test_modal_drill_ins_communicate_back_and_restore_the_parent_journey():
    helper = app_section("function openDrillInFrom", "function resultRowHtml")
    assert "rootEl?.closest?.('.modal-backdrop')" in helper
    assert "openChildModal(parent, openNext)" in helper
    assert "openDrillInFrom(rootEl, () => openUserProfile" in helper

    games = app_section("function bindGameButtons", "function showCelebration")
    assert "openDrillInFrom(rootEl, () => openGameScreen" in games
    result_openers = app_section("async function openLeagueScreen", "function openEditTournamentSheet")
    assert "openChildModal(box, () => openCompetitionResultSheet('league'" in result_openers
    assert "openChildModal(box, () => openCompetitionResultSheet('tournament'" in result_openers
    assert "openChildModal(box, () => openEditTournamentSheet" in result_openers

    retry = app_section("function retryDetailLoad", "function emptyStateHtml")
    assert "shell.modal.classList.contains('flow-child-modal')" in retry
    assert "currentOverlayEntry()?.el === parent" in retry
    assert "openChildModal(parent, openNext)" in retry


def test_chat_destinations_mount_one_retryable_full_height_shell_before_fetching():
    helper = app_section("function openChatLoadShell", "function emptyStateHtml")
    hydration = app_section("function hydrateDetailLoadShell", "function retryDetailLoad")
    assert "openDetailLoadShell({ route, title, copy, label, rows: 2 })" in helper
    assert "classList.add('chat-modal', 'chat-load-shell')" in helper
    assert "classList.remove('chat-load-shell')" in helper
    assert "hydrateDetailLoadShell(shell, html, label)" in helper
    assert "shell.box.innerHTML = html" in hydration
    assert "querySelector('.thread-msgs')?.setAttribute('data-scroll', '')" in helper
    assert "setDialogLabel(shell.box, label)" in hydration

    endpoints = {
        "openLeagueChat": "/leagues/${lg.id}/chat",
        "openTournamentChat": "/tournaments/${t.id}/chat",
        "openThread": "/chat/${userId}",
        "openCourtChat": "/courts/${court.id}/chat",
        "openGameChat": "/games/${game.id}/chat",
        "openClubChat": "/clubs/${club.id}/chat",
    }
    for name, endpoint in endpoints.items():
        source = js_function(name)
        assert "openChatLoadShell({" in source
        assert endpoint in source
        assert source.index("openChatLoadShell({") < source.index("await api(")
        assert "renderDetailLoadError(" in source
        assert "retryDetailLoad(" in source
        assert "hydrateChatLoadShell(" in source
        assert "return modal;" in source

    by_id = js_function("openCrewChatById")
    crew = js_function("openCrewChat")
    assert by_id.index("openChatLoadShell({") < by_id.index("await api(")
    assert "renderDetailLoadError(" in by_id
    assert "retryDetailLoad(" in by_id
    assert "return openCrewChat({ ...detail, ...summary }, { shell });" in by_id
    assert "existingShell || openChatLoadShell({" in crew
    assert "hydrateChatLoadShell(" in crew
    assert "return modal;" in crew

    for composer_id in ("thread-text", "cc-text", "gc-text", "crew-text", "clb-text"):
        assert re.search(rf'id="{composer_id}"[^>]+aria-label="[^"]+"', APP)

    loading_css = "\n".join(css_declarations(".chat-modal.chat-load-shell .modal"))
    assert "padding:" in loading_css
    assert "overflow-y: auto" in loading_css


def test_async_child_decoration_is_anchored_and_workflow_transitions_keep_back_context():
    helpers = app_section("function transitionModal", "function beginFollowupAfterClosingModal")
    child = app_section("function openChildModal", "function beginFollowupAfterClosingModal")

    assert "const flowParent = el.classList.contains('flow-child-modal')" in helpers
    assert "replacement !== flowParent" in helpers
    assert "decorateFlowChildModal(replacement)" in helpers
    assert "return result || true;" in helpers
    assert "const openedChild = currentOverlayEntry()?.el;" in child
    assert "candidate instanceof HTMLElement ? candidate : openedChild" in child
    assert "candidate instanceof HTMLElement ? candidate : currentOverlayEntry()?.el" not in child
    assert "decorate(openedChild);" in child


def test_cross_entity_drill_ins_keep_the_source_sheet_available_on_back():
    business = app_section("async function loadCourtBusiness", "let pendingCourtDetailOpen")
    court = app_section("async function openCourtDetail", "function openCourtPlayerActions")
    actions = js_function("openCourtPlayerActions")
    court_chat = js_function("openCourtChat")
    finder = js_function("openFindClubsSheet")
    profile = js_function("openUserProfile")

    assert "openChildModal(modal, () => openBusinessHub({ court }))" in business
    assert "openChildModal(modal, () => openChallengeSheet(player, court))" in court
    assert "openChildModal(courtModal, () => openThread(player.id))" in actions
    assert "openChildModal(courtModal, () => openChallengeSheet(player, court))" in actions
    assert "openChildModal(courtModal, () => openUserProfile(player.id))" in actions
    assert "openChildModal(modal, () => openGameScreen(gameId))" in court_chat
    assert "openChildModal(modal, () => openClubScreen(Number(row.dataset.openClub)))" in finder
    assert "openChildModal(modal, () => openThread(userId))" in profile
    assert "openChildModal(modal, () => openChallengeSheet(user, court))" in profile
    assert "openChildModal(modal, () => openNewGameModal({" in profile


def test_community_entity_and_conversation_routes_preserve_their_intent():
    screen = js_function("openClubScreen")
    chat = js_function("openClubChat")
    info = js_function("openClubInfo")
    rows = js_function("bindCommunityConversationRows")
    activity = js_function("openActivity")

    assert "{ destination = 'info' }" in screen
    assert "openClubScreen(clubId, { destination })" in screen
    assert "destination === 'chat' && club.joined" in screen
    assert "openClubScreen(id, { destination: 'chat' })" in rows
    assert "notification.kind === 'club_message'" in activity
    assert "openClubScreen(Number(target.id), { destination: 'chat' })" in activity
    assert "modal.dataset.clubInfoId = String(club.id);" in info
    assert "Number(parent?.dataset.clubInfoId) === Number(club.id)" in chat


def test_dirty_modal_dismissal_restores_browser_history_before_prompting():
    dismiss = app_section("function destroyModal", "function dismissAllModals")
    back = app_section("function restoreBlockedOverlayTraversal", "// Durable, account-scoped chat outbox")
    guard = app_section("function bindModalDiscardConfirmation", "// ---------- Court detail")
    close = app_section("function closeModal", "function transitionModal")

    assert "pendingBlockedDismissPrompt?.el === el" in dismiss
    assert "el._onDismissBlocked?.();" in dismiss
    assert "pendingBlockedDismissPrompt = typeof top.el._onDismissBlocked === 'function'" in back
    assert "history.go(forwardSteps)" in back
    assert "nav.id === pendingBlockedDismissPrompt.id" in back
    assert "currentOverlayEntry()?.el === pending.el" in back
    assert "pending.el._onDismissBlocked?.();" in back
    assert "openActionConfirmation({" in guard
    assert "confirmLabel: 'Discard changes'" in guard
    assert "cancelLabel: 'Keep editing'" in guard
    assert "currentOverlayEntry()?.el !== modal" in guard
    assert "closeModal(modal);" in guard
    assert "_dismissBlocked" not in close


def test_stateful_and_text_link_controls_meet_shared_interaction_contracts():
    link_css = "\n".join(css_declarations(".btn-link"))
    assert "min-height: var(--tap-min)" in link_css
    assert "inline-flex" in link_css

    gallery = app_section("async function openCourtGallery", "async function openGameChat")
    like = js_function("toggleCourtPhotoLike")
    assert 'aria-pressed="${photo.liked_by_me}"' in gallery
    assert "uiIcon('heart', photo.liked_by_me ? 'is-filled' : '')" in gallery
    assert "if (button.disabled) return false;" in like
    assert "button.disabled = true;" in like
    assert "button.setAttribute('aria-busy', 'true');" in like
    assert "button.setAttribute('aria-pressed', String(photo.liked_by_me));" in like


def test_game_mutations_lock_conflicting_actions_until_the_request_finishes():
    helper = app_section("function beginButtonAction", "// Keep usable content on-screen")
    assert "button.dataset.actionPending === 'true'" in helper
    assert "const controls = [...new Set([button, ...peers].filter(Boolean))];" in helper
    assert "controls.forEach((control) => { control.disabled = true; });" in helper
    assert "button.setAttribute('aria-busy', 'true');" in helper
    assert "control.disabled = disabled;" in helper
    assert "control.innerHTML = html;" in helper

    cards = app_section("function bindGameButtons", "function showCelebration")
    assert "b.closest('.game-card') || rootEl" in cards
    assert "[...actionRoot.querySelectorAll('[data-game-confirm], [data-game-dispute]')]" in cards
    assert "'Confirming…'" in cards
    assert "'Opening score…'" in cards
    assert cards.count("resetAction();") >= 2

    detail = app_section("async function openGameScreen", "function safeNotificationOverlayRoute")
    assert detail.count("beginButtonAction(") >= 5
    assert "[...box.querySelectorAll('[data-mvp]')]" in detail
    assert "[...box.querySelectorAll('#gs-confirm, #gs-dispute')]" in detail
    for pending_label in ("Saving…", "Voting…", "Confirming…", "Leaving…", "Opening score…"):
        assert f"'{pending_label}'" in detail


def test_single_choice_and_court_mutations_use_the_same_pending_guard():
    conditions = app_section("function openConditionSheet", "function maybeAskConditions")
    assert "const conditionChoices = [...modal.querySelectorAll('[data-cond]')];" in conditions
    assert "beginButtonAction(b, 'Sharing…', conditionChoices)" in conditions
    assert "resetAction();" in conditions

    profile_report = app_section("function openUserSafetyReport", "async function openUserProfile")
    assert "const reportControls = [...sheet.querySelectorAll(" in profile_report
    assert "'input[name=\"profile-report-reason\"], #profile-report-details, #profile-report-block'" in profile_report
    assert "beginButtonAction(submit, 'Sending report…', reportControls)" in profile_report

    court = app_section("async function openCourtDetail", "function openCourtPlayerActions")
    assert "beginButtonAction(event.currentTarget, 'Checking out…')" in court
    assert "resetAction();" in court

    onboarding = app_section("async function maybeSuggestStarterCourts", "function homeAreaOnboardingKey")
    assert "uiIcon('star', saved ? 'is-filled' : '')" in onboarding
    assert 'aria-pressed="${saved}"' in onboarding
    assert 'aria-label="${saved ? \'Remove\' : \'Save\'} ${esc(c.name)}"' in onboarding
    assert "beginButtonAction(btn, 'Saving…')" in onboarding
    assert "btn.setAttribute('aria-pressed', String(!!res.favorited));" in onboarding
    assert "btn.setAttribute('aria-label', `${res.favorited ? 'Remove' : 'Save'} ${btn.dataset.courtName}`);" in onboarding
    assert "resetAction();" in onboarding


def test_tournament_checkin_badges_stay_visible_beside_long_entry_names():
    tournament = app_section("async function openTournamentScreen", "function openEditTournamentSheet")
    assert 'class="tag competition-entry-checkin"' in tournament
    assert "uiIcon('check-circle')" in tournament
    assert 'class="competition-entry-name-row"' in tournament
    assert 'class="row-title competition-entry-name"' in tournament
    assert 'aria-label="${esc(en.name)}, marked here"' in tournament
    assert "${esc(en.name)}${hereTag(en)}" not in tournament
    assert "entryNameHtml(en, { showSeed: true })" in tournament

    row_css = "\n".join(css_declarations(".competition-entry-name-row"))
    name_css = "\n".join(css_declarations(".competition-entry-name"))
    badge_css = "\n".join(css_declarations(".competition-entry-checkin"))
    assert "display: flex" in row_css
    assert "min-width: 0" in row_css
    assert "flex: 1" in name_css
    assert "min-width: 0" in name_css
    assert "flex: 0 0 auto" in badge_css


def test_confirmation_and_single_select_controls_expose_the_actual_state():
    confirmation = app_section("function openActionConfirmation", "// ---------- Court detail")
    assert 'id="action-confirm-title"' in confirmation
    assert "setAttribute('aria-labelledby', 'action-confirm-title')" in confirmation

    court = app_section("async function openCourtDetail", "function openCourtPlayerActions")
    assert 'data-cd-day="${i}" aria-pressed="false"' in court
    assert "b.setAttribute('aria-pressed', String(active));" in court

    planner = app_section("async function openNewGameModal", "// ---------- Tournaments ----------")
    assert 'data-club-id="" class="${initialClubId ? \'\' : \'active\'}" aria-pressed="${!initialClubId}"' in planner
    assert "button.setAttribute('aria-pressed', String(active));" in planner


def test_game_cards_use_a_semantic_main_button_with_sibling_actions():
    card = app_section("function gameCardHtml", "function bindGameButtons")
    template = card[card.index("return `") :]

    article = re.search(r"<article\b[^>]*>", template)
    assert article and re.search(r'class="[^"]*\bcard\b[^"]*\bgame-card\b[^"]*"', article.group(0))
    main = re.search(r'<button\b[^>]*data-open-game="\$\{game\.id\}"[^>]*>', template)
    assert main, "The large detail target must be a native button, not a pressable card container"
    assert 'type="button"' in main.group(0)
    assert re.search(r'class="[^"]*\bgame-card-main\b[^"]*"', main.group(0))
    assert 'class="game-card-actions"' in template
    assert re.search(r'class="[^"]*\bgame-card-footer\b[^"]*"', template)

    main_close = template.index("</button>", main.end())
    actions_at = template.index('class="game-card-actions"')
    dynamic_actions_at = template.index("${action}", actions_at)
    assert main_close < actions_at < dynamic_actions_at
    assert template.index("</article>", dynamic_actions_at) > dynamic_actions_at
    assert 'class="game-card-title-wrap"' in template
    assert 'class="row-title game-card-title">${gameTitle}</span>' in template
    assert 'class="game-card-tags">${typeTag}${visTag}${inviteTag}${recurTag}${clubTag}${levelTag}${chatTag}</span>' in template

    title_wrap_css = "\n".join(css_declarations(".game-card-title-wrap"))
    assert "min-width: 0" in title_wrap_css
    assert "flex-wrap: wrap" in title_wrap_css
    tags_css = "\n".join(css_declarations(".game-card-tags"))
    assert "flex-wrap: wrap" in tags_css
    assert ".game-card-tags .tag" in STYLES

    opening_article = article.group(0)
    assert "data-open-game" not in opening_article
    assert "role=\"button\"" not in opening_article


def _assert_leaflet_icon_has_minimum_hit_size(source: str) -> None:
    icon_size = re.search(r"iconSize:\s*\[\s*([A-Za-z_$][\w$]*|\d+)\s*,\s*\1\s*\]", source)
    assert icon_size, "Leaflet iconSize must expose a square, inspectable hit box"
    expression = icon_size.group(1)
    if expression.isdigit():
        minimum = int(expression)
    else:
        assignment = re.search(
            rf"(?:const|let)\s+{re.escape(expression)}\s*=\s*(\d+)\s*;",
            source,
        ) or re.search(
            rf"(?:const|let)\s+{re.escape(expression)}\s*=\s*(\d+)\s*;",
            APP,
        )
        assert assignment, f"{expression} must resolve to an explicit pixel minimum"
        minimum = int(assignment.group(1))
    assert minimum >= 44


def test_map_markers_and_clusters_have_44px_hits_and_parent_selected_state():
    cluster = app_section("function setupMap()", "function locateMe")
    marker = app_section("function courtMarkerIcon", "function drawMarkers")
    _assert_leaflet_icon_has_minimum_hit_size(cluster)
    _assert_leaflet_icon_has_minimum_hit_size(marker)

    hit_css = "\n".join(css_declarations(".court-marker-hit"))
    assert "width: var(--tap-min)" in hit_css
    assert "height: var(--tap-min)" in hit_css
    assert "className: 'court-marker-hit court-cluster-hit'" in cluster

    # Selection belongs on Leaflet's stable 44px parent, not only on a visual
    # child whose changing size makes the focus/selection halo jump.
    class_name_at = marker.index("className:")
    class_name = marker[class_name_at:marker.index("html:", class_name_at)]
    assert "court-marker-hit" in class_name
    assert "selected" in class_name or "is-selected" in class_name

    selected_parent_rule = False
    for match in re.finditer(r"([^{}]+)\{", _CLEAN_STYLES):
        for selector in match.group(1).split(","):
            normalized = re.sub(r"\s+", " ", selector).strip()
            selected = (
                ".court-marker-hit.selected" in normalized
                or ".court-marker-hit.is-selected" in normalized
                or re.search(r"\.court-marker-hit\[data-selected(?:=|\])", normalized)
            )
            if selected:
                selected_parent_rule = True
    assert selected_parent_rule, "Selected styling must flow from the hit-box parent to its marker visual"


def test_presence_banner_separates_court_navigation_from_checkout():
    presence = app_section("function renderPresenceBanner", "// ---------- Boot ----------")
    assert 'class="presence-main" id="banner-court"' in presence
    assert 'id="banner-checkout"' in presence
    assert "$('#banner-court').addEventListener('click'" in presence
    assert "$('#banner-checkout').addEventListener('click'" in presence
    assert "el.onclick" not in presence

    for selector in (".presence-banner button", ".presence-banner .presence-main"):
        declarations = "\n".join(css_declarations(selector))
        assert "var(--tap-min)" in declarations or selector.endswith(".presence-main")


def test_compact_free_text_and_profile_actions_cannot_expand_the_viewport():
    row_sub = "\n".join(css_declarations(".row-sub"))
    profile_name = "\n".join(css_declarations(".profile-name"))
    profile_sub = "\n".join(css_declarations(".profile-sub"))
    looking_copy = "\n".join(css_declarations(".looking-banner > span:not(.chev)"))
    inbox_preview = "\n".join(css_declarations(".inbox-row > .row-main > .row-sub:not(.inbox-time)"))
    more_menu = "\n".join(css_declarations(".profile-more-menu"))

    assert "overflow-wrap: anywhere" in row_sub
    assert "overflow-wrap: anywhere" in profile_name
    assert "overflow-wrap: anywhere" in profile_sub
    assert "min-width: 0" in looking_copy
    assert "text-overflow: ellipsis" in looking_copy
    assert "text-overflow: ellipsis" in inbox_preview
    assert "position: absolute" in more_menu
    assert "100vw" in more_menu

    profile_actions = app_section("profileMoreAction =", "const games = user.recent_games")
    assert 'class="profile-more-actions"' in profile_actions
    assert 'aria-haspopup="menu" aria-expanded="false"' in profile_actions
    assert 'role="menu"' in profile_actions
    assert 'class="profile-more-menu"' in profile_actions


def test_player_identity_uses_one_native_profile_target_beside_row_actions():
    profile_css = "\n".join(css_declarations(".player-profile-link"))
    assert "min-height: var(--tap-min)" in profile_css
    assert "flex: 1" in profile_css

    court_players = app_section("const playersHtml =", "const allCourtGames")
    nearby_players = app_section("html += players.length", "html += `<details class=\"nearby-privacy\">")
    friends = app_section("html += shownFriends.length", "// People you've actually played with")
    for section in (court_players, nearby_players, friends):
        assert 'type="button" class="player-profile-link" data-view-user=' in section
        assert 'aria-label="View ${esc(' in section


def test_reselecting_an_active_tab_scrolls_only_that_surface_to_its_start():
    scrolling = app_section("function scrollTabToTop", "function setupTabs")
    setup = app_section("function setupTabs", "function switchTab")
    switching = app_section("function switchTab", "// One share sheet")

    assert "setCourtSheetSnap('peek');" in scrolling
    assert '$(`#tab-${tab} .tab-scroll`)' in scrolling
    assert "'(prefers-reduced-motion: reduce)'" in scrolling
    assert "scroller.scrollTo({ top: 0" in scrolling

    assert "const tab = btn.dataset.tab;" in setup
    assert "if (state.tab === tab) scrollTabToTop(tab);" in setup
    assert "else switchTab(tab);" in setup

    # Cross-tab navigation keeps each feed's position unless a caller makes an
    # explicit first-position request (for example, onboarding/deep-link reset).
    assert "scrollToTop = false" in switching
    assert "if (scrollToTop)" in switching


def test_child_flows_use_back_while_terminal_sheets_keep_close():
    if "function openChildModal(" not in APP:
        pytest.skip("No child-flow navigation helper is implemented yet")

    child = app_section("function openChildModal", "// A successful modal action")
    decorator = app_section("function decorateFlowChildModal", "// Drill-ins keep their parent sheet alive")
    assert "currentOverlayEntry()?.el !== parent" in child
    assert "const result = openNext();" in child
    assert "closeModal(parent)" not in child
    assert "transitionModal(parent" not in child
    assert "decorateFlowChildModal(child)" in child
    assert "child.classList.add('flow-child-modal');" in decorator
    assert "'.modal-head .modal-close'" in decorator
    assert "'.court-detail-load-close.modal-close'" in decorator
    assert "'.cd-hero-actions > .modal-close'" in decorator
    assert "'.thread-head > .modal-close'" in decorator
    assert "back.setAttribute('aria-label', 'Back');" in decorator
    assert "back.innerHTML = uiIcon('arrow-left');" in decorator

    modal_head = app_section("const modalHead", "// ---------- Court detail")
    assert 'aria-label="Close"' in modal_head
    assert "uiIcon('x')" in modal_head

    ranked = app_section("function openGameFlow(options = {})", "async function checkInAndStartRally")
    settings = app_section("function openSettingsHub", "async function renderProfile")
    assert "openChildModal(modal, () => openRankedOpponentPicker({" in ranked
    assert "court: selected, maxPlayers, onCreated: options.onCreated" in ranked
    assert "openChildModal(" in settings


def test_checkout_commit_is_not_reopened_when_account_reconciliation_fails():
    presence = app_section("function renderPresenceBanner", "// ---------- Boot ----------")
    commit = "await api('/checkout', { method: 'POST' });"
    reconcile = "await refreshMe().catch(() => false);"

    assert commit in presence
    assert reconcile in presence
    assert presence.index("} catch (err) {") < presence.index(reconcile)
    assert "return;" in presence[presence.index("} catch (err) {"):presence.index(reconcile)]
    assert "invalidateMeRequests();" in presence
    assert "state.presence = state.presence ? { ...state.presence, checked_in: false } : null;" in presence
    assert "renderPresenceBanner();" in presence


def test_calendar_subscription_has_immediate_single_flight_feedback():
    calendar = app_section("async function subscribeGamesCalendar", "function businessMineItems")

    assert "event = null" in calendar
    assert "const button = event?.currentTarget || null;" in calendar
    assert "beginButtonAction(button, 'Preparing calendar…')" in calendar
    assert "if (!resetAction) return;" in calendar
    assert calendar.index("beginButtonAction") < calendar.index("await api('/calendar/token')")
    assert calendar.count("resetAction();") >= 3


def test_created_game_refreshes_retained_entity_only_after_game_is_visible():
    planner = app_section("async function openNewGameModal", "function openScoreModal")
    court = app_section("async function openCourtDetail", "function openCourtPlayerActions")
    crew = app_section("async function openCrewScreen", "function openRenameCrewSheet")
    club = app_section("function openClubInfo", "async function openFindClubsSheet")
    public_profile = app_section("async function openUserProfile", "async function subscribeGamesCalendar")
    my_profile = app_section("async function renderProfile", "function openEditProfile")

    game_open = "openGameScreen(createdGame.id, { replaceModal: modal });"
    callback = "plannerOptions.onCreated?.(createdGame)"
    assert game_open in planner
    assert callback in planner
    assert planner.index(game_open) < planner.index(callback)
    assert "onCreated: refreshCourtAfterGameCreate" in court
    assert "if (modal.isConnected) refreshCourtDetailPreservingContext(modal, court.id);" in court
    assert "options.onCreated = () =>" in crew
    assert "transitionModal(modal, () => openCrewScreen(crew.id))" in crew
    assert "onCreated: () =>" in club
    assert "transitionModal(modal, () => openClubScreen(club.id))" in club
    assert "transitionModal(modal, () => openUserProfile(userId))" in public_profile
    assert "profileDashboardCache = { userId: null, promise: null, data: null, readyAt: 0 };" in my_profile
    assert "if (state.tab === 'profile') renderProfile();" in my_profile


def test_branded_action_confirmation_replaces_native_browser_dialogs():
    assert not re.search(r"\b(?:window\.)?confirm\s*\(", APP)

    helper = app_section("function openActionConfirmation", "// ---------- Court detail")
    assert "return new Promise((resolve) =>" in helper
    assert 'class="action-confirmation is-${normalizedTone}"' in helper
    assert "data-action-confirm-cancel" in helper
    assert "data-action-confirm-accept" in helper
    assert "closeModal(sheet);" in helper
    assert "resolve(false);" in helper  # close, Escape, backdrop, and Back cleanup
    assert "sheet._cleanupFns?.push" in helper
    assert APP.count("await openActionConfirmation({") >= 15

    actions = "\n".join(css_declarations(".action-confirm-actions"))
    danger = "\n".join(css_declarations(".action-confirm-danger"))
    assert "display: grid" in actions
    assert "var(--danger-solid)" in danger


def test_shared_results_and_competition_cards_are_native_navigation_buttons():
    result = app_section("function resultRowHtml", "function upcomingDayLabel")
    tournament = app_section("function tournamentCardHtml", "function leagueCardHtml")
    league = app_section("function leagueCardHtml", "function competitionDetailTabsHtml")

    assert '<button type="button" class="result-row" data-open-game=' in result
    assert '<div class="result-row"' not in result
    assert "uiIcon('chevron-right', 'chev')" in result
    assert '<button type="button" class="card competition-nav-card"' in tournament
    assert '<button type="button" class="card row nav-row-button competition-league-card"' in league

    result_css = "\n".join(css_declarations(".result-row"))
    nav_css = "\n".join(css_declarations(".nav-row-button"))
    assert "min-height: var(--tap-min)" in result_css
    assert "text-align: left" in result_css
    assert "min-height: var(--tap-min)" in nav_css


def test_profile_title_and_court_destinations_use_native_navigation_controls():
    titles = app_section("function tournamentTitlesHtml", "function tournamentRoundLabel")
    public_profile = app_section("const courtRow = (c) =>", "const modal = openModal(`")
    own_profile = app_section("const savedCourtRowHtml = (c) =>", "const featuredCourts")

    assert '<button type="button" class="row title-history-row" data-open-tournament=' in titles
    assert '<button type="button" class="row title-history-row" data-open-league=' in titles
    assert '<div class="row" data-open-tournament=' not in titles
    assert "uiIcon('chevron-right', 'chev')" in titles

    for section in (public_profile, own_profile):
        assert '<button type="button" class="card row nav-row-button"' in section
        assert 'aria-label="Open ${esc(c.name)} court"' in section
        assert "uiIcon('chevron-right', 'chev')" in section

    title_row_css = "\n".join(css_declarations(".title-history-row"))
    assert "min-height: var(--tap-min)" in title_row_css


def test_competition_discovery_keeps_filters_errors_and_mobile_forms_honest():
    rankings = app_section("if (seg === 'scores')", "// --- Games:")
    discovery = app_section("async function renderTournaments", "// ---------- Shared competition results")

    assert "const resultsParams = ['game_type=ranked'];" in rankings
    assert "resultsParams.push(`scope=${scope === 'friends' ? 'friends' : 'all'}`)" in rankings
    assert "resultsParams.push('period=month')" in rankings
    assert 'class="rankings-more"' in rankings
    assert 'class="rankings-heading"' in rankings
    assert "const settled = await Promise.allSettled([" in rankings
    assert "const boardFeed = rankingFeed(settled[0]" in rankings
    assert "const resultsFeed = rankingFeed(settled[1]" in rankings
    assert "if (boardFeed.error)" in rankings
    assert "if (resultsFeed.error)" in rankings
    assert "Recent ranked results are still shown below." in rankings
    assert "The leaderboard is still shown above." in rankings
    assert "data-rankings-retry" in rankings
    assert ".rankings-section-error" in STYLES

    assert "nearbyResult.error" in discovery
    assert "myLeaguesResult.error" in discovery
    assert "nearbyLeaguesResult.error" in discovery
    assert "data-competition-retry" in discovery
    assert ".catch(() => ({ items: [] }))" not in discovery

    form_grid = "\n".join(css_declarations(".form-grid"))
    assert "minmax(0, 1fr)" in form_grid


def test_community_leaf_destinations_share_native_navigation_rows():
    club = app_section("function openClubInfo", "async function openClubInviteSheet")
    finder = app_section("async function openFindClubsSheet", "async function openCourtGallery")
    chat = app_section("async function openClubChat", "function openClubInfo")

    for attribute in (
        'id="club-court"',
        'data-open-club-league=',
        'data-open-club-tournament=',
        'data-open-game=',
    ):
        assert re.search(
            rf'<button type="button" class="card row nav-row-button"[^>]*{attribute}',
            club,
        )
    assert '<button type="button" class="card row nav-row-button community-search-result"' in finder
    assert '<button type="button" class="row-main club-thread-head-target" id="club-head"' in chat
    assert "uiIcon('chevron-right', 'chev')" in club
    assert "uiIcon('chevron-right', 'chev')" in finder
    assert 'style="cursor:pointer"' not in club
    assert 'style="cursor:pointer"' not in finder


def test_community_identity_targets_preserve_composite_card_boundaries():
    nearby = app_section("async function renderNearbyPlayers", "async function renderFriends")
    friends = app_section("async function renderFriends", "async function openThread")

    assert '<div class="card play-pulse-nearby-card">' in nearby
    assert '<button type="button" class="play-pulse-nearby-person" data-view-user=' in nearby
    assert "avatarHtml(person, 'sm', 'span')" in nearby
    assert 'data-play-pulse-accept=' in nearby

    assert '<div class="card friend-digest-card">' in friends
    assert '<button type="button" class="row friend-digest-row" data-view-user=' in friends
    assert 'aria-label="View ${esc(t.display_name)}\'s profile"' in friends
    assert "renderError(el, e.message, () => renderNearbyPlayers(el));" in nearby

    pulse_css = "\n".join(css_declarations(".play-pulse-nearby-person"))
    digest_css = "\n".join(css_declarations(".friend-digest-row"))
    assert "width: var(--tap-min)" in pulse_css
    assert "height: var(--tap-min)" in pulse_css
    assert "min-height: var(--tap-min)" in digest_css


def test_activity_is_paged_actionable_and_uses_explicit_read_clear_controls():
    activity = app_section("async function openActivity", "// ---------- Presence banner ----------")

    assert "api('/notifications?limit=20')" in activity
    assert 'before_id=${encodeURIComponent(requestedCursor)}' in activity
    assert "page.has_more === true" in activity
    assert "nextCursor !== requestedCursor" in activity
    assert "new Set(items.map((notification) => Number(notification.id)))" in activity
    assert 'data-activity-more>Load older activity' in activity
    assert 'class="card activity-item"' in activity
    assert 'class="row activity-row is-actionable" data-activity-open=' in activity
    assert "uiIcon(notificationIconFor(notification.kind))" in activity
    assert "uiIcon('chevron-right', 'chev')" in activity
    assert "makePressable(row" not in activity
    assert "'Review request'" in activity
    assert "'Open invitation'" in activity
    assert "'Review score'" in activity
    assert "'View result'" in activity
    assert "openChildModal(modal" in activity
    assert "openNotificationTarget(notification)" in activity
    assert "api(`/notifications/${notification.id}/read`" in activity
    assert "body: JSON.stringify({ ids })" in activity
    assert "data-activity-read-visible" in activity
    assert "data-activity-read=" in activity
    assert "data-activity-clear=" in activity
    assert "setTimeout(async () =>" in activity
    assert "action: { label: 'Undo', onClick: restore }" in activity
    assert "await openActionConfirmation({" in activity
    assert "title: 'Clear all activity?'" in activity
    assert "This cannot be undone." in activity
    assert "notificationAccessibleText" in activity
    assert 'data-unread="${!notification.read}"' in activity
    assert "state.unreadNotifications = 0;" in activity
    assert "if (data.unread)" not in activity

    activity_css = "\n".join(css_declarations(".activity-row"))
    assert "width: 100%" in activity_css
    assert "min-height: 64px" in activity_css
    assert ".activity-item-actions" in STYLES
    assert ".activity-pagination" in STYLES


def test_notification_preferences_wrap_inside_the_modal_grid():
    settings = app_section("function openNotificationSettings", "async function loadBlockedPlayers")
    assert '<fieldset class="settings-notification-list">' in settings
    assert '<legend class="sr-only">Optional notification categories</legend>' in settings
    assert 'class="card row settings-notification-row"' in settings
    assert 'style="width:20px;height:20px' not in settings

    grid_css = "\n".join(css_declarations(".settings-notification-list"))
    row_css = "\n".join(css_declarations(".settings-notification-row"))
    title_css = "\n".join(css_declarations(".settings-notification-row .row-title"))
    assert "grid-template-columns: minmax(0, 1fr)" in grid_css
    assert "min-width: 0" in grid_css
    assert "min-width: 0" in row_css
    assert "white-space: normal" in title_css
    assert "overflow-wrap: anywhere" in title_css


def test_notification_preferences_serialize_latest_snapshot_and_report_save_state():
    settings = app_section("function openNotificationSettings", "async function loadBlockedPlayers")

    assert 'role="status" aria-live="polite" aria-atomic="true"' in settings
    assert "let desiredRevision = 0;" in settings
    assert "let settledRevision = 0;" in settings
    assert "let saveLoop = null;" in settings
    assert "if (saveLoop) return saveLoop;" in settings
    assert "fieldset.disabled = true;" in settings
    assert "fieldset.setAttribute('aria-busy', 'true');" in settings
    assert "while (settledRevision < desiredRevision)" in settings
    assert "const revision = desiredRevision;" in settings
    assert "const muted = [...desiredMuted];" in settings
    assert "if (revision !== desiredRevision) continue;" in settings
    assert "desiredMuted = readMuted();" in settings
    assert "desiredRevision += 1;" in settings
    assert "syncToggles(confirmedMuted);" in settings
    assert "Your previous choices were restored." in settings
    assert "fieldset.disabled = false;" in settings
    assert "fieldset.removeAttribute('aria-busy');" in settings
    assert "Saving notification choices…" in settings
    assert "Notification choices saved." in settings

    status_css = "\n".join(css_declarations(".settings-notification-status"))
    error_css = "\n".join(css_declarations(".settings-notification-status.is-error"))
    assert "min-height: 20px" in status_css
    assert "var(--red-700)" in error_css


def test_chat_photo_icons_and_settings_failures_use_shared_recoverable_controls():
    photo = app_section("function addPhotoToComposer", "function clearDeadDeepLink")
    thread = app_section("async function openThread", "function courtOpenCallFingerprint")
    blocked = app_section("async function loadBlockedPlayers", "function openPrivacySafetySettings")

    assert "btn.innerHTML = uiIcon('camera');" in photo
    assert 'aria-label="Send a photo"' in thread
    assert "uiIcon('camera')" in thread
    assert '>📷</button>' not in thread
    assert "renderError(box, 'Could not load blocked players right now.', () => loadBlockedPlayers(root));" in blocked
    assert ".chat-message-action .ui-icon" in STYLES
    assert ".thread-input button .ui-icon" in STYLES


def test_settings_leaf_rows_share_product_navigation_and_preserve_parent_context():
    privacy = app_section("function openPrivacySafetySettings", "function openAppearanceSettings")
    appearance = app_section("function openAppearanceSettings", "function openCalendarSettings")
    calendar = app_section("function openCalendarSettings", "function openAppearanceCalendarSettings")
    account = app_section("function openAccountSettings", "function openSettingsHub")
    hub = app_section("function openSettingsHub", "async function renderProfile")

    assert privacy.count('class="card row nav-row-button"') == 1
    assert 'class="card row settings-inline-row"' in privacy
    assert "uiIcon('home')" in privacy
    assert "uiIcon('map-pin')" in privacy
    assert "openChildModal(modal, () => openHomeAreaSheet" in privacy
    assert "openChildModal(modal, () => openAutoCheckInConsent" in privacy
    assert "transitionModal(modal, () => openHomeArea" not in privacy

    assert 'class="card row nav-row-button" id="settings-calendar"' in calendar
    assert "uiIcon('calendar')" in calendar
    assert 'class="card row nav-row-button" id="account-install"' in account
    assert 'class="card row settings-inline-row"' in account
    assert "uiIcon('external')" in account
    assert "uiIcon('chevron-right', 'chev')" in hub

    scoped = privacy + appearance + calendar + account + hub
    assert '<span class="chev">›</span>' not in scoped
    for emoji in ('🏠', '📍', '👋', '📅', '📲', '📱'):
        assert f'<span aria-hidden="true">{emoji}</span>' not in scoped

    inline_css = "\n".join(css_declarations(".settings-inline-row"))
    inline_title_css = "\n".join(css_declarations(".settings-inline-row .row-title"))
    destinations_css = "\n".join(css_declarations(".settings-destinations"))
    destination_copy_css = "\n".join(css_declarations(".settings-destination .row-sub"))
    assert "min-width: 0" in inline_css
    assert "min-height: 54px" in inline_css
    assert "white-space: normal" in inline_title_css
    assert "grid-template-columns: minmax(0, 1fr)" in destinations_css
    assert "white-space: normal" in destination_copy_css
    assert "overflow-wrap: anywhere" in destination_copy_css


def test_remaining_cross_app_destinations_use_native_controls_and_product_icons():
    competition_create = app_section("function openCompetitionCreateSheet", "async function openLogGameSheet")
    league = app_section("async function openLeagueScreen", "async function openLeagueChat")
    game = app_section("function gameScreenHtml", "async function openGameScreen")
    profile = app_section("async function renderProfile", "function openEditProfile")
    business = app_section("function renderBusinessHubDashboard", "function openBusinessPlayerPreview")

    assert competition_create.count('class="card row nav-row-button"') == 2
    assert "uiIcon('trophy')" in competition_create
    assert "uiIcon('grid')" in competition_create
    assert '<button type="button" class="card row nav-row-button competition-member-row"' in league
    assert "openChildModal(box, () => openLeagueChat(lg))" in league
    assert '<button type="button" class="player-profile-link" data-view-user="${p.user_id}"' in game
    assert 'class="card row nav-row-button" id="gs-court"' in game
    assert '<button type="button" class="profile-relationship-link" data-view-user=' in profile
    assert "openToolChild(() => openBusinessDetailsEditor" in business
    assert "uiIcon(check.done ? 'check-circle' : 'target')" in business
    assert "uiIcon('chevron-right', 'chev')" in business

    assert not re.search(r'<span class="(?:chev|agb-chev)"[^>]*>›</span>', APP)
    assert '>✕</button>' not in APP

    relationship_css = "\n".join(css_declarations(".profile-relationship-link"))
    competition_member_css = "\n".join(css_declarations(".competition-member-row"))
    assert "min-height: var(--tap-min)" in relationship_css
    assert "padding: 9px 14px" in competition_member_css


def test_styles_only_reference_defined_design_tokens():
    defined = set(re.findall(r"(--[a-zA-Z0-9-]+)\s*:", STYLES))
    used = set(re.findall(r"var\(\s*(--[a-zA-Z0-9-]+)", STYLES))
    assert used - defined == set()
    assert "var(--shadow-soft)" not in STYLES
    assert "var(--ink-faint)" not in STYLES


def test_every_enabled_button_has_immediate_touch_feedback():
    button_css = "\n".join(css_declarations("button"))
    active_css = "\n".join(css_declarations("button:not(:disabled):active"))
    assert "touch-action: manipulation" in button_css
    assert "transition: opacity var(--motion-fast) ease" in button_css
    assert "opacity: .78" in active_css


def test_shared_disclosures_hide_native_markers_and_use_one_chevron_treatment():
    for selector in (
        ".game-flow-alternatives > summary",
        ".nearby-filter > summary",
        ".nearby-privacy > summary",
        ".profile-dashboard-more > summary",
        ".settings-account-section > summary",
        ".competition-audit > summary",
    ):
        assert selector in STYLES
        assert f"{selector}::-webkit-details-marker" in STYLES
        assert f"{selector}::after" in STYLES
    assert "border-right: 2px solid currentColor" in STYLES


def test_toasts_wrap_actionable_errors_instead_of_truncating_them():
    toast_css = "\n".join(css_declarations(".toast"))
    assert "white-space: normal" in toast_css
    assert "overflow-wrap: anywhere" in toast_css
    assert "text-overflow: ellipsis" not in toast_css


def test_player_search_invalidates_stale_rows_and_keeps_failures_retryable():
    friends = js_function("renderFriends")
    assert 'id="friend-search-results" aria-live="polite"' in friends
    assert "let playerSearchSeq = 0;" in friends
    assert "playerSearchSeq += 1;" in friends
    assert "const seq = ++playerSearchSeq;" in friends
    assert "resultsEl.setAttribute('aria-busy', 'true');" in friends
    assert "Looking for players…" in friends
    assert "seq !== playerSearchSeq" in friends
    assert "renderError(resultsEl, error.message, () => runPlayerSearch(search.value.trim()))" in friends
    assert "resultsEl?.removeAttribute('aria-busy');" in friends


def test_relationship_and_membership_mutations_lock_conflicting_controls():
    friends = js_function("renderFriends")
    nearby = js_function("renderNearbyPlayers")
    profile = js_function("openUserProfile")
    court_actions = js_function("openCourtPlayerActions")
    my_profile = js_function("renderProfile")
    club = js_function("openClubInfo")
    game = js_function("openGameScreen")

    assert "const peers = [...row.querySelectorAll('[data-respond]')];" in friends
    assert "beginButtonAction(b, accepting ? 'Accepting…' : 'Declining…', peers)" in friends
    assert "beginButtonAction(b, 'Sending…')" in nearby
    assert "showInlineActionError(row, e.message);" in nearby
    assert "beginButtonAction(button, 'Sending…')" in profile
    assert "beginButtonAction(button, 'Blocking…')" in profile
    assert "connectionState === 'incoming' ? 'Accepting…' : 'Sending…'" in court_actions
    assert "showInlineActionError(sheet, error.message);" in court_actions
    assert "formUX.startSubmitting('Sending feedback…')" in my_profile
    assert "await api(`/clubs/${club.id}/join`, { method: 'POST' });" in club
    assert "const fresh = await api(`/clubs/${club.id}`);" in club
    assert "transitionModal(modal, () => openClubInfo(fresh));" in club
    assert "openChildModal(modal, () => openClubChat(fresh));" not in club
    assert "beginButtonAction(event.currentTarget, 'Joining…')" in club
    assert "[...box.querySelectorAll('[data-remove-player]')]" in game
    assert "flex-wrap: wrap" in "\n".join(css_declarations(".row.has-inline-action-error"))


def test_court_contribution_flows_use_back_navigation_and_shared_form_feedback():
    court = js_function("openCourtDetail")
    challenge = js_function("openChallengeSheet")
    hours = js_function("maybeAskHours")
    assert "openChildModal(modal, () => openSuggestEditSheet(" in court
    assert "openChildModal(modal, () => openConditionSheet(" in court
    assert "openChildModal(modal, () => openCourtPlayerActions" in court
    assert 'id="ch-form" novalidate' in challenge
    assert "bindModalFormUX(modal, '#ch-send')" in challenge
    assert "formUX.startSubmitting('Sending challenge…')" in challenge
    assert 'id="hp-form" novalidate' in hours
    assert '<label for="hp-hours">Court hours</label>' in hours
    assert "bindModalFormUX(modal, '#hp-save')" in hours


def test_review_save_commits_before_best_effort_detail_refresh():
    review = js_function("renderReviewSection")
    assert "beginButtonAction(btn, mine ? 'Updating review…' : 'Posting review…')" in review
    commit = review.index("court.my_review = saved.review;")
    refresh = review.index("api(`/courts/${court.id}`).then")
    assert commit < refresh
    assert ".catch(() => { /* the committed review remains valid */ });" in review
    assert "showError(err.message);" in review[:commit]
    assert "showError(err.message);" not in review[refresh:]


def test_invite_join_commits_before_the_best_effort_profile_refresh():
    banner = js_function("renderActiveGameBanner")
    join = banner[banner.index("const status = el.querySelector('.agb-sub')") : banner.index("if (dismissBtn)")]
    assert "beginButtonAction(joinBtn, 'Joining…', [dismissBtn])" in join
    assert "openGameScreen(joinedGame?.id || game.id);" in join
    assert "refreshMe().catch(() => {});" in join
    assert join.index("openGameScreen(joinedGame?.id || game.id);") < join.index("refreshMe().catch(() => {});")
    assert "await refreshMe()" not in join
    assert "status.setAttribute('role', 'alert');" in join


def test_leave_failure_restores_the_action_and_stays_visible_in_the_game_sheet():
    game = js_function("openGameScreen")
    leave = game[game.index("box.querySelectorAll('#gs-leave, #gs-not-coming, #gs-leave-series')") : game.index("box.querySelector('#gs-cancel')")]
    assert "beginButtonAction(btn, 'Leaving…')" in leave
    assert "resetAction();" in leave
    assert "showInlineActionError(box, err.message);" in leave
    assert "reopenFresh()" not in leave


def test_small_modal_actions_use_semantic_forms_and_shared_feedback():
    court = js_function("openCourtDetail")
    profile = js_function("renderProfile")
    game = js_function("openGameScreen")

    assert 'id="cap-form" novalidate' in court
    assert '<label for="cap-text">Caption' in court
    assert "bindModalFormUX(activeContext, '#cap-save')" in court
    assert "formUX.startSubmitting('Adding photo…')" in court
    assert 'const previewDismissBlocked = () => previewActive;' in court
    assert 'Back without adding' in court

    assert 'id="feedback-form" novalidate' in profile
    assert '<label for="fb-text">What should we know?</label>' in profile
    assert "bindModalFormUX(sheet, '#fb-send')" in profile
    assert "formUX.startSubmitting('Sending feedback…')" in profile
    assert "formUX.showError(err.message);" in profile

    assert 'id="rs-form" novalidate' in game
    assert 'type="datetime-local" id="rs-when"' in game
    assert "bindModalFormUX(sheet, '#rs-save')" in game
    assert "formUX.startSubmitting('Saving new time…')" in game
    assert "Number.isFinite(when.getTime())" in game
    assert "title: 'Discard the new time?'" in game


def test_score_and_account_security_actions_are_real_forms():
    score = js_function("openScoreModal")
    account = js_function("openAccountSettings")

    assert 'id="sc-form" novalidate' in score
    assert '<fieldset class="score-series-game"' in score
    assert '<legend>Game ${index + 1}</legend>' in score
    assert 'for="sc-${index + 1}-1"' in score
    assert 'for="sc-${index + 1}-2"' in score
    assert "bindModalFormUX(modal, '#sc-submit')" in score
    assert "formUX.startSubmitting(" in score
    assert "'Sending score…'" in score and "'Saving result…'" in score
    assert "scoreSubmitting" in score
    assert "formUX.showError(err.message);" in score
    assert "title: 'Discard this score?'" in score

    assert 'id="account-password-form" novalidate' in account
    assert 'id="account-delete-form" novalidate' in account
    assert "bindModalFormUX(modal, '#account-password-save')" in account
    assert "bindModalFormUX(modal, '#account-delete')" in account
    assert "passwordUX.startSubmitting('Updating password…')" in account
    assert "deleteUX.startSubmitting('Deleting account…')" in account
    assert "passwordUX.showError(" in account and "deleteUX.showError(" in account


def test_home_area_mutations_are_single_flight_and_recover_inline():
    home = js_function("openHomeAreaSheet")
    search = js_function("bindCitySearch")
    assert "let saving = false;" in home
    assert "const commitHomeArea = async" in home
    assert "if (saving) return;" in home
    assert "beginButtonAction(trigger, pendingLabel" in home
    assert "cityInput.disabled = true;" in home
    assert "modal._dismissBlocked = () => saving;" in home
    assert "status.setAttribute('role', 'alert');" in home
    assert "setHomeAreaFromLocation({ silent: true, onError })" in home
    assert "saveHomeArea(p.lat, p.lng, p.label, { silent: true, onError })" in home
    assert "onPick(places[Number(row.dataset.city)], row)" in search
    assert "${input.disabled ? 'disabled' : ''}" in search


def test_planner_copy_and_icons_follow_the_selected_play_type():
    planner = js_function("openNewGameModal")
    assert "icon: uiIcon('pickleball')" in planner
    assert "icon: uiIcon('trophy')" in planner
    assert "class=\"vis-choice-icon\"" in planner
    for icon in ("lock", "users", "map-pin"):
        assert f"uiIcon('{icon}')" in planner
    assert "const plannerSubmitLabel = ()" in planner
    assert "restorePlannerSubmitLabel" in planner
    assert "syncPlannerNounLabels" in planner
    assert "textContent = 'Schedule game'" not in planner
    assert "Suggested game times" not in planner
    assert "Game date and time" not in planner

    choice_css = "\n".join(css_declarations(".vis-choice-icon .ui-icon"))
    assert "width: 20px" in choice_css
    assert "height: 20px" in choice_css
    heading_focus_css = "\n".join(css_declarations('.planner-step-title[tabindex="-1"]:focus'))
    assert "outline: none" in heading_focus_css


def test_business_confirmation_uses_shared_form_validation_and_pending_state():
    confirmation = js_function("openBusinessConfirmAction")
    assert 'id="business-confirm-form" novalidate' in confirmation
    assert 'type="submit"' in confirmation
    assert "bindModalFormUX(modal, button)" in confirmation
    assert "bindModalDiscardConfirmation(modal" in confirmation
    assert "formUX.showError(`Type ${requireText} exactly to continue.`, input)" in confirmation
    assert "formUX.startSubmitting('Working…')" in confirmation
    assert "formUX.showError(error.message);" in confirmation
    assert "data-business-confirm-error" not in confirmation


def test_product_pickleball_icon_helper_replaces_raw_inline_fragments():
    assert '<svg class="pb-ic"' not in APP
    assert "uiIcon('pickleball')" in APP


def test_court_detail_facility_tags_use_the_product_icon_system():
    court = js_function("openCourtDetail")
    tag_block = court[court.index("const tags = [];") : court.index("// Use the same destination builder")]
    for icon in ("home", "sun", "lightbulb", "grid", "net", "restroom", "water", "clock", "target"):
        assert f"uiIcon('{icon}')" in tag_block
    for emoji in ("🏠", "☀️", "💡", "🏟", "🥅", "🚻", "🚰", "🕐", "💵", "🎯"):
        assert emoji not in tag_block


def test_league_actions_render_the_committed_mutation_before_optional_refresh():
    league = js_function("openLeagueScreen")
    action = league[league.index("const act = (path, confirmation)") : league.index("content.querySelector('#lg-join')")]
    assert "beginButtonAction(button, actionLabels[path] || 'Saving…', mutationButtons)" in action
    assert "result = await api(`/leagues/${lg.id}/${path}`" in action
    assert "path === 'leave'" in action
    assert "path === 'cancel'" in action
    assert "render(committed, { preserve: true });" in action
    assert "refresh({ force: true }).then((fresh)" in action
    assert action.index("render(committed, { preserve: true });") < action.index("refresh({ force: true }).then")
    assert "showInlineActionError(content, error.message);" in action
    assert "Saved. Live league details will refresh when the connection returns." in action

    sync_css = "\n".join(css_declarations(".competition-sync-note"))
    assert "var(--amber-50)" in sync_css


def test_tournament_entry_forms_and_actions_share_one_recoverable_mutation_boundary():
    tournament = js_function("openTournamentScreen")
    partner_picker = js_function("bindTournamentPartnerPicker")

    assert 'id="td-register-form" novalidate' in tournament
    assert 'id="td-swap-form" novalidate' in tournament
    assert "tournamentPartnerPickerHtml('td-partner'" in tournament
    assert "tournamentPartnerPickerHtml('td-newpartner'" in tournament
    assert "submitId: 'td-register'" in tournament
    assert "submitId: 'td-swap'" in tournament
    assert 'id="td-need-partner"' in tournament
    assert 'data-share-player-invite' in tournament
    assert "bindModalFormUX(registerForm, '#td-register')" in tournament
    assert "bindModalFormUX(swapForm, '#td-swap')" in tournament
    assert "registerForm?.addEventListener('submit'" in tournament
    assert "swapForm?.addEventListener('submit'" in tournament
    assert "registerUX.showError('Search for the player you want to invite.'" in tournament
    assert "swapUX.showError('Search for the player you want to invite.'" in tournament

    action = tournament[tournament.index("const mutationControls") : tournament.index("content.querySelector('#td-chat')")]
    assert "#td-checkin, #td-register, #td-swap, #td-need-partner" in action
    assert "#td-partner-accept, #td-partner-decline" in action
    assert "beginButtonAction(button, pendingLabel, mutationControls())" in action
    assert "box.dataset.competitionMutation === 'true'" in action
    assert "showInlineActionError(content, error.message)" in action
    assert "const committed = authoritative ? result" in action
    assert "render(committed, { preserve: true });" in action
    assert "Saved. Live tournament details will refresh when the connection returns." in action
    assert tournament.index("render(committed, { preserve: true });") < tournament.index("refresh({ force: true }).then((fresh)")

    assert "api(`/users/search?q=${encodeURIComponent(query)}`)" in partner_picker
    assert "playerSkillIdentityHtml(player)" in partner_picker
    assert "skillLabel(player.skill_level)" not in partner_picker
    assert "sharedAvailabilityText(state.me?.availability, player.availability)" in partner_picker
    assert "They’ll be asked to accept before your team is complete." in partner_picker
    assert "api('/friends')" not in tournament

    entry_form_css = "\n".join(css_declarations(".competition-entry-form"))
    partner_status_css = "\n".join(css_declarations(".competition-partner-action"))
    assert "var(--green-50)" in entry_form_css
    assert "var(--violet-50)" in partner_status_css


def test_business_connection_actions_preserve_committed_results_when_refresh_fails():
    connections = js_function("openBusinessConnections")
    assert "const load = async ({ fallbackConnections = null } = {})" in connections
    assert "if (Array.isArray(fallbackConnections))" in connections
    assert "Saved. Live connection details will refresh when the connection returns." in connections
    assert "beginButtonAction(button, 'Checking links…'" in connections
    assert "beginButtonAction(button, 'Reconnecting…'" in connections
    assert connections.count("await load({ fallbackConnections: updatedConnections });") >= 3
    assert "showInlineActionError(row, error.message);" in connections
    assert "onSaved?.({ ...business, connections: updatedConnections });" in connections


def test_business_connection_and_rematch_states_use_product_icons_not_font_arrows():
    health = js_function("businessConnectionHealth")
    dashboard = js_function("renderBusinessHubDashboard")
    connections = js_function("openBusinessConnections")
    game = js_function("openGameScreen")
    game_markup = js_function("gameScreenHtml")

    for icon in ("link", "alert-triangle", "refresh", "check-circle"):
        assert f"icon: '{icon}'" in health
    assert "uiIcon(connectionHealth.icon)" in dashboard
    assert "const statusIcon = problem ? 'alert-triangle'" in connections
    assert "uiIcon(statusIcon)" in connections
    assert "uiIcon('link')" in connections
    assert "openPostGamePlanner(game, modal, event.currentTarget, loadCrew())" in game
    assert "${uiIcon('calendar')} Play again" in game_markup
    assert not any(glyph in APP for glyph in ("↻", "↗", "⇄"))

    health_icon_css = "\n".join(css_declarations(".business-connection-health-icon .ui-icon"))
    row_icon_css = "\n".join(css_declarations(".business-connection-head > span:first-child .ui-icon"))
    assert "width: 19px" in health_icon_css
    assert "width: 18px" in row_icon_css


def test_operator_connection_health_check_is_a_guarded_form():
    review = js_function("openBusinessOperatorReview")
    assert 'id="operator-connection-recheck-form" novalidate' in review
    assert "bindModalFormUX(modal, '#operator-connection-recheck')" in review
    assert "formUX.startSubmitting('Checking links…')" in review
    assert "formUX.showError(error.message);" in review


def test_business_onboarding_value_cards_use_product_icons():
    empty = js_function("businessHubEmptyHtml")
    for icon in ("calendar", "target", "pickleball", "bell"):
        assert f"uiIcon('{icon}')" in empty
    for emoji in ("📅", "🎯", "🏓", "📣"):
        assert emoji not in empty


def test_shared_error_state_is_branded_actionable_and_never_renders_a_dead_retry():
    error_renderer = js_function("renderError")
    assert "uiIcon('alert-triangle')" in error_renderer
    assert 'class="empty-state-icon is-error"' in error_renderer
    assert '<b>That didn’t load</b>' in error_renderer
    assert "retryFn ? '<button type=\"button\"" in error_renderer
    assert "⚠️" not in error_renderer
    assert ".empty-state-icon.is-error" in STYLES
