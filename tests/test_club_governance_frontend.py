"""Phone-first Community governance and discovery contracts."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
CSS = (ROOT / 'public' / 'styles-v15.css').read_text()


def section(start, end):
    return APP[APP.index(start):APP.index(end, APP.index(start))]


def test_join_requests_admin_roles_and_bans_have_designed_flows():
    info = section('function openClubInfo', 'async function openClubInviteSheet')
    assert "club.join_policy === 'request' ? 'Request to join'" in info
    assert "club.join_request_status === 'pending'" in info
    assert "api(`/clubs/${club.id}/join-request`, { method: 'DELETE' })" in info
    assert 'openClubJoinRequestsSheet(club, reopenInfo)' in info
    assert 'openClubMemberAccessSheet(club, member, reopenInfo)' in info

    assert "api(`/clubs/${club.id}/join-requests/${Number(button.dataset.requestId)}/decision`" in APP
    assert "body: JSON.stringify({ decision })" in APP
    assert "api(`/clubs/${club.id}/members/${member.id}`" in APP
    assert "body: JSON.stringify({ role: nextRole })" in APP
    assert 'Remove and block from rejoining' in APP
    assert 'user_id: Number(member.id), ban,' in APP
    assert 'openActionConfirmation({' in APP


def test_announcements_are_separate_dated_posts_not_a_settings_text_field():
    editor = section('function openEditClubSheet', 'async function openFindClubsSheet')
    assert 'ce-announce' not in editor
    assert "join_policy: modal.querySelector('[name=\"ce-join-policy\"]:checked').value" in editor
    assert 'function openClubAnnouncementSheet' in APP
    assert "api(`/clubs/${club.id}/announcement`" in APP
    assert "method: 'DELETE'" in section(
        'function openClubAnnouncementSheet',
        'async function openClubJoinRequestsSheet',
    )
    assert 'club.announcement_author_name' in APP
    assert 'club.announcement_posted_at' in APP
    assert '<details><summary>Read announcement</summary>' in APP


def test_invites_support_player_search_and_an_external_share_link():
    invite = section('async function openClubInviteSheet', 'function bindCourtComboboxNavigation')
    assert 'Search any player by name' in invite
    assert "api(`/users/search?q=${encodeURIComponent(query)}`)" in invite
    assert "api(`/clubs/${club.id}/invite`" in invite
    assert 'id="club-invite-share"' in invite
    assert 'navigator.share' in invite
    assert 'navigator.clipboard.writeText' in invite
    assert 'An invitation never joins someone without their choice.' in invite


def test_per_community_notification_controls_are_explicit_and_accessible():
    notification = section(
        'function openClubNotificationSheet',
        'function openClubMemberAccessSheet',
    )
    assert 'role="radiogroup"' in notification
    assert 'role="radio"' in notification
    for value in ('all', 'mentions', 'off'):
        assert f"['{value}'" in notification or f"'{value}'" in notification
    assert "api(`/clubs/${club.id}/notification-settings`" in notification
    assert "method: 'PATCH'" in notification


def test_discovery_is_location_aware_and_prominent():
    finder = section('async function openFindClubsSheet', 'async function openCourtGallery')
    assert 'committedAreaLatLng()' in finder
    assert "params.set('lat', String(area.lat))" in finder
    assert "params.set('lng', String(area.lng))" in finder
    assert "params.set('court_id', String(Number(courtId)))" in finder
    assert 'distance_miles' in finder
    assert 'community-discovery-hero' in APP
    assert 'Find public groups</button>' in APP


def test_community_close_is_recoverable_and_inline_hardcoded_color_is_gone():
    info = section('function openClubInfo', 'async function openClubInviteSheet')
    assert "api(`/clubs/${club.id}/restore`, { method: 'POST' })" in info
    assert "label: 'Undo'" in info
    assert 'cannot be undone' not in info.lower()
    assert '#c92a2a' not in info
    assert 'style=' not in info
    for selector in (
        '.community-manage-actions', '.community-member-row',
        '.community-request-actions', '.community-notification-options',
        '.community-discovery-hero', '.community-announcement-copy',
    ):
        assert selector in CSS
