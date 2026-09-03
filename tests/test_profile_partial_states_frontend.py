from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
CSS = (ROOT / "public" / "styles-v15.css").read_text()


def profile_source():
    start = APP.index("async function renderProfile")
    end = APP.index("function openEditProfile", start)
    return APP[start:end]


def test_each_profile_section_has_recoverable_partial_failure_feedback():
    profile = profile_source()
    assert "const showProfileSectionUnavailable = (section, title, copy)" in profile
    assert 'data-profile-section-retry>Retry</button>' in profile
    assert profile.count("showProfileSectionUnavailable(") == 4
    for copy in (
        "Upcoming play is unavailable right now.",
        "Play stats are unavailable right now.",
        "Saved courts are unavailable right now.",
        "Recent play is unavailable right now.",
    ):
        assert copy in profile
    assert ".profile-section-unavailable .btn" in CSS


def test_new_players_do_not_get_a_blank_stats_section():
    profile = profile_source()
    assert "emptyStateHtml({" in profile
    assert "className: 'profile-stats-empty'" in profile
    assert "primary: { goto: 'log-game', label: 'Log a game'" in profile
    assert "Log a completed game to start seeing patterns" in profile
