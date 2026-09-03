from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_court_detail_headings_use_product_trophy_icons():
    detail = section("async function openCourtDetail", "function openCourtPlayerActions")
    assert detail.count("section-label section-label-icon\">${uiIcon('trophy')}") >= 2
    assert "🏆 Court champions" not in detail
    assert "🏆 Tournaments here" not in detail


def test_community_empty_states_use_structured_product_icons_and_copy():
    inbox = section("function universalInboxHtml", "function bindCommunityConversationRows")
    invitations = section("async function openClubInviteSheet", "function bindCourtComboboxNavigation")

    assert "${uiIcon('message')}" in inbox
    assert 'class="empty-state-copy"' in inbox
    assert "<span class=\"big\">💬</span>" not in inbox
    assert "${uiIcon('users')}" in invitations
    assert "No players found" in invitations
    assert "No suggested players yet. Search by name or share the invitation link." in invitations
    assert "<span class=\"big\">🤝</span>" not in invitations
