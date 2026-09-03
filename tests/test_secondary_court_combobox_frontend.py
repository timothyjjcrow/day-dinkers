"""Focused contracts for secondary court-search comboboxes."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_shared_court_combobox_keeps_focus_in_the_input_while_navigating():
    navigation = section(
        "function bindCourtComboboxNavigation",
        "function clubCourtPicker",
    )

    assert "searchInput.setAttribute('aria-activedescendant', option.id)" in navigation
    assert "option.setAttribute('aria-selected', 'false')" in navigation
    assert "option.tabIndex = -1" in navigation
    assert "['ArrowDown', 'ArrowUp', 'Home', 'End']" in navigation
    assert "event.key === 'Enter' && activeOption?.isConnected" in navigation
    assert "activeOption.click()" in navigation
    assert "event.key === 'Escape'" in navigation
    assert "event.stopPropagation()" in navigation
    assert "resultsBox.hidden = true" in navigation
    assert "searchInput.removeAttribute('aria-activedescendant')" in navigation
    assert "option.scrollIntoView({ block: 'nearest' })" in navigation


def test_tournament_court_options_have_stable_ids_and_refresh_navigation():
    tournament = section(
        "async function openCreateTournamentSheet",
        "function tournamentTitlesHtml",
    )

    assert tournament.count('id="tc-court-option-${c.id}"') == 2
    assert tournament.count('role="option" aria-selected="false" tabindex="-1"') >= 2
    assert "bindCourtComboboxNavigation(searchInput, resultsBox" in tournament
    assert "courtNavigation.refresh()" in tournament
    assert "courtNavigation.clear()" in tournament
    assert "courtNavigation.destroy()" in tournament
    assert "resultsEl.setAttribute('aria-busy', 'true')" in tournament
    assert "resultsEl.removeAttribute('aria-busy')" in tournament
    assert "No courts found. Try a different name or city." in tournament
    assert "data-court-search-retry aria-label=\"Retry court search\"" in tournament
    assert "searchInput.setAttribute('aria-expanded', 'false')" in tournament


def test_club_business_and_group_forms_share_the_complete_court_combobox():
    picker = section("function clubCourtPicker", "function openCreateClubSheet")

    assert 'id="${prefix}-court-option-${c.id}"' in picker
    assert 'role="option" aria-selected="false" tabindex="-1"' in picker
    assert "bindCourtComboboxNavigation(searchInput, resultsBox" in picker
    assert "courtNavigation.refresh()" in picker
    assert "courtNavigation.clear()" in picker
    assert "courtNavigation.destroy()" in picker
    assert "resultsEl.setAttribute('aria-busy', 'true')" in picker
    assert "resultsEl.removeAttribute('aria-busy')" in picker
    assert "No courts found. Try a different name or city." in picker
    assert "error.message || 'Could not load courts.'" in picker
    assert "requestAnimationFrame(() => searchInput.focus({ preventScroll: true }))" in picker

    for prefix in ("lc", "pg", "cb", "ce", "bh"):
        assert f"clubCourtPicker(modal, '{prefix}')" in APP


def test_active_court_option_uses_the_existing_product_palette():
    start = STYLES.index('.court-suggestion[aria-selected="true"]')
    rule = STYLES[start:STYLES.index("}", start)]

    assert "border-color: var(--green-600)" in rule
    assert "background: var(--green-50)" in rule
    assert ".court-suggestion:focus-visible" in STYLES
    assert "@media (hover: hover)" in STYLES
