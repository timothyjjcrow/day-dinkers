"""Focused source contracts for Community, groups, clubs, and profile UI consistency."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
INDEX = (ROOT / "public" / "index.html").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_people_filters_expose_selection_and_use_the_app_picker():
    nearby = section("async function renderNearbyPlayers", "async function renderFriends")
    friends = section("async function renderFriends", "async function openThread")

    assert "const skills = [['', 'All'], ['3.0', '3.0'], ['3.5', '3.5'], ['4.0', '4.0+']]" in nearby
    assert "&level=${encodeURIComponent(skill)}" in nearby
    assert 'section-label section-label-icon' in nearby
    assert "uiIcon('bell')" in nearby
    assert "uiIcon('map-pin', 'community-inline-icon')" in section(
        "function nearbyPlayerLocationHtml", "async function renderNearbyPlayers"
    )
    assert 'Recently active in Third Shot' in nearby
    assert 'class="community-presence-dot" aria-hidden="true"' in nearby
    assert "uiIcon('map')} Browse courts" in nearby
    assert "uiIcon('zap', 'community-streak-icon')" in nearby
    assert "🟢 active now" not in nearby
    assert "📣 Can play this hour" not in nearby

    assert 'id="friend-slots" role="group" aria-label="Filter friends by usual play time"' in friends
    assert 'aria-pressed="${v === slotFilter}"' in friends
    assert "uiIcon('send')} Invite" in friends
    assert "uiIcon('trophy')} Friend results" in friends
    assert "b.innerHTML = `${uiIcon('check-circle')} Sent`;" in friends
    assert "uiIcon('check-circle')} Friends" in friends
    assert "Friends ✓" not in friends
    assert '.quick-times button[aria-pressed="true"]' in STYLES


def test_private_play_groups_use_named_targets_and_product_state_icons():
    inbox = section("function universalInboxHtml", "function bindCommunityConversationRows")
    create = section("async function openCreatePlayGroupSheet", "function crewPlannerOptions")
    detail = section("async function openCrewScreen", "function openRenameCrewSheet")
    chat = section("async function openCrewChat(crew", "// ---------- Clubs")

    assert '<span class="inbox-room-icon crew" aria-hidden="true">${uiIcon(\'users\')}</span>' in inbox
    assert "uiIcon('check-circle')} Join group" in inbox
    assert create.count("<span class=\"play-group-check\" aria-hidden=\"true\">${uiIcon('check')}</span>") == 2
    assert "uiIcon('map-pin', 'community-inline-icon')" in detail
    assert 'aria-label="View ${esc(member.display_name || \'player\')}\'s profile"' in detail
    assert "${modalHead(crew.name)}" in detail
    assert 'class="community-role-tag">Owner' in detail
    assert "👑" not in detail
    assert "🚪 Leave group" not in detail
    assert '<span class="inbox-room-icon crew" aria-hidden="true">${uiIcon(\'users\')}</span>' in chat
    assert 'id="crew-chat-head" aria-label="Open ${esc(crew.name)} play group info"' in chat

    crew_head = STYLES[STYLES.index(".crew-chat-head {"):]
    crew_head = crew_head[:crew_head.index("}")]
    assert "min-height: var(--tap-min)" in crew_head
    assert ".play-group-check .ui-icon" in STYLES


def test_club_controls_and_membership_states_share_product_icons():
    club = section("function openClubInfo", "async function openClubInviteSheet")
    invite = section("async function openClubInviteSheet", "function clubCourtPicker")
    edit = section("function openEditClubSheet", "async function openFindClubsSheet")
    find = section("async function openFindClubsSheet", "async function openCourtGallery")

    assert "community-announcement-icon" in club
    assert "uiIcon('bell')" in club
    assert '<summary>Members <span>' in club
    assert "uiIcon('lock')" in club
    assert "Leaderboard" not in club
    assert 'class="community-rank"' not in club
    assert 'class="community-role-tag">Owner' in club
    assert "🥇" not in club
    assert "👑" not in club
    assert "🚪 Leave community" not in club
    assert "${modalHead(`Invite players to ${club.name}`)}" in invite
    assert "uiIcon('send')} Invite" in invite
    assert "button.innerHTML = `${uiIcon('check-circle')} Invited`;" in invite
    assert 'id="club-announcement"' in club
    assert "Announcement <span class=\"field-help\">" not in edit
    assert "uiIcon('check-circle')} Member" in find
    assert "${cl.joined ? ', member' : ''}" in find
    assert "cl.description" in find
    assert ".community-announcement-icon .ui-icon" in STYLES


def test_player_profiles_have_clear_disclosures_icons_and_filter_state():
    public_profile = section("async function openUserProfile", "// ---------- My profile tab")
    own_profile = section("async function renderProfile", "function openEditProfile")

    for icon in ("sliders", "trophy", "x", "lock", "alert-triangle"):
        assert f"uiIcon('{icon}')" in public_profile
    assert 'class="community-presence-status"' in public_profile
    assert "uiIcon('home', 'community-inline-icon')" in public_profile
    assert "profile-compare-line" in public_profile
    assert "profile-availability-title" in public_profile
    assert "🟢 active now" not in public_profile
    assert "⚑ Report" not in public_profile

    for control, icon in (("pf-invite", "send"), ("pf-feedback", "message")):
        assert f'id="{control}">${{uiIcon(\'{icon}\')}}' in own_profile
    assert 'id="profile-edit"' in INDEX
    assert 'id="profile-activity"' in INDEX
    assert 'id="profile-avatar-edit" aria-label="Change profile photo"' in own_profile
    assert 'id="profile-availability-edit" aria-label="Edit when you usually play"' in own_profile
    assert "stats.games_total || 0" in own_profile
    assert "stats.badge_progress || []" in own_profile
    assert "uiIcon('send')} Share my season" in own_profile
    assert "uiIcon('trophy', 'profile-stat-icon')" in own_profile
    assert "uiIcon('calendar', 'profile-stat-icon')" in own_profile
    assert 'role="group" aria-label="Filter play history"' in own_profile
    assert 'aria-pressed="${k === active}"' in own_profile
    assert "uiIcon('trophy')} Wins" in own_profile
    assert "use Save on a court to keep it here" in own_profile

    assert '.profile-more-actions > [aria-haspopup="menu"]::after' in STYLES
    assert '.profile-more-actions.is-open > [aria-haspopup="menu"]::after' in STYLES
    assert ".profile-dashboard-actions .btn" in STYLES
    assert ".profile-avatar-edit" in STYLES
    assert ".profile-availability-summary" in STYLES
    assert ".profile-next-milestones" in STYLES
    assert ".community-role-tag" in STYLES
    assert ".community-rank" in STYLES
    assert ".profile-stat-icon" in STYLES


def test_direct_and_group_chat_loading_and_seen_states_use_product_ui():
    direct = section("async function openThread", "function courtOpenCallFingerprint")
    crew = section("async function openCrewChat(crew", "// ---------- Clubs")
    club = section("async function openClubChat", "function openClubInfo")

    assert "chat-image-loading" in direct
    assert "chat-image-loading" in crew
    assert "chat-image-loading" in club
    image_hydration = section("function hydrateChatImages", "function addPhotoToComposer")
    assert "img.alt = 'Photo'" in image_hydration
    assert "Photo couldn’t load." in image_hydration
    assert "chat-image-retry" in image_hydration
    assert "class=\"chat-seen\"" in direct
    assert "uiIcon('check-circle')" in direct
    assert "✓✓" not in direct
    assert ".chat-image-loading" in STYLES
    assert ".chat-seen .ui-icon" in STYLES


def test_community_has_race_safe_new_message_compose_and_profile_drill_in():
    setup = section("function setupChat", "function inboxMessagePreviewText")
    compose = section("function openNewMessageSheet", "function inboxMessagePreviewText")
    direct = section("async function openThread", "function courtOpenCallFingerprint")

    assert 'id="chat-compose"' in INDEX
    assert 'aria-label="New message"' in INDEX
    assert "$('#chat-compose')?.addEventListener('click', openNewMessageSheet)" in setup
    assert 'id="new-message-search"' in compose
    assert 'id="new-message-results"' in compose
    assert "let searchSeq = 0" in compose
    assert "const seq = ++searchSeq" in compose
    assert "seq !== searchSeq || !modal.isConnected || input.value.trim() !== query" in compose
    assert "api(`/users/search?q=${encodeURIComponent(query)}`)" in compose
    assert 'data-compose-user="${user.id}"' in compose
    assert "transitionModal(modal, () => openThread(userId))" in compose
    assert "timer = setTimeout(() => runSearch(query), 300)" in compose

    assert 'id="thread-profile"' in direct
    assert "openChildModal(modal, () => openUserProfile(userId))" in direct
    assert ".thread-profile-link" in STYLES
