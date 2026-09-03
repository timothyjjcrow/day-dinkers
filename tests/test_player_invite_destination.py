"""Durable inviter attribution and optional play-destination contracts."""

from datetime import timedelta
from pathlib import Path

import pytest

from backend.app import create_app, db
from backend.models import Court, Friendship, Game, User, utcnow


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = (ROOT / 'public' / 'app-v15.js').read_text()


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, slug, *, invited_by_user_id=None):
    payload = {
        'email': f'{slug}@example.com',
        'password': 'secret123',
        'display_name': slug.replace('-', ' ').title(),
    }
    if invited_by_user_id is not None:
        payload['invited_by_user_id'] = invited_by_user_id
    response = client.post('/api/auth/register', json=payload)
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def headers(account):
    return {'Authorization': f"Bearer {account['token']}"}


def create_court():
    court = Court(
        name='Invite Destination Courts', city='Irvine', state='CA',
        latitude=33.68, longitude=-117.82, num_courts=6,
    )
    db.session.add(court)
    db.session.commit()
    return court


def create_game(client, owner, court, **overrides):
    payload = {
        'court_id': court.id,
        'scheduled_at': (utcnow() + timedelta(hours=4)).isoformat() + 'Z',
        **overrides,
    }
    response = client.post('/api/games', json=payload, headers=headers(owner))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_invite_url_and_public_card_keep_a_valid_open_game_destination(client):
    inviter = register(client, 'destination-host')
    court = create_court()
    game = create_game(client, inviter, court)

    page = client.get(
        f"/u/{inviter['user']['id']}?game={game['id']}",
    )
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert f"#invite/{inviter['user']['id']}/game/{game['id']}" in html
    assert 'Invite Destination Courts' in html

    card = client.get(
        f"/api/invite/{inviter['user']['id']}?game={game['id']}",
    )
    assert card.status_code == 200
    assert card.get_json()['destination'] == {
        'kind': 'game',
        'id': game['id'],
        'action_url': f"/#game/{game['id']}",
        'scheduled_at': game['scheduled_at'],
        'court_name': 'Invite Destination Courts',
        'session_kind': 'session',
    }


def test_joined_player_can_share_their_next_open_game(client):
    host = register(client, 'destination-game-host')
    inviter = register(client, 'destination-joined-player')
    court = create_court()
    game = create_game(client, host, court)
    joined = client.post(
        f"/api/games/{game['id']}/join", headers=headers(inviter), json={},
    )
    assert joined.status_code == 200, joined.get_json()

    page = client.get(
        f"/u/{inviter['user']['id']}?game={game['id']}",
    )
    assert page.status_code == 200
    assert (
        f"#invite/{inviter['user']['id']}/game/{game['id']}"
        in page.get_data(as_text=True)
    )


@pytest.mark.parametrize('mode', [
    'invalid', 'missing', 'unrelated', 'private', 'cancelled', 'past',
])
def test_invalid_private_unrelated_or_dead_destination_falls_back_safely(
        client, mode):
    inviter = register(client, f'fallback-host-{mode}')
    other = register(client, f'fallback-other-{mode}')
    court = create_court()
    owner = other if mode == 'unrelated' else inviter
    overrides = {'visibility': 'private', 'invite_user_ids': [other['user']['id']]} \
        if mode == 'private' else {}
    game = create_game(client, owner, court, **overrides)
    raw_game_id = game['id']
    if mode == 'invalid':
        raw_game_id = 'not-a-number'
    elif mode == 'missing':
        raw_game_id = 999999
    elif mode in ('cancelled', 'past'):
        row = db.session.get(Game, game['id'])
        if mode == 'cancelled':
            row.status = 'cancelled'
        else:
            row.scheduled_at = utcnow() - timedelta(minutes=5)
        db.session.commit()

    page = client.get(
        f"/u/{inviter['user']['id']}?game={raw_game_id}",
    )
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert f"#invite/{inviter['user']['id']}" in html
    assert f"#invite/{inviter['user']['id']}/game/" not in html
    card = client.get(
        f"/api/invite/{inviter['user']['id']}?game={raw_game_id}",
    )
    assert card.status_code == 200
    assert card.get_json()['destination'] is None


def test_referral_attribution_does_not_imply_friendship_consent(client):
    inviter = register(client, 'consent-inviter')
    invitee = register(
        client, 'consent-invitee',
        invited_by_user_id=inviter['user']['id'],
    )
    invitee_row = db.session.get(User, invitee['user']['id'])
    assert invitee_row.invited_by_user_id == inviter['user']['id']
    assert Friendship.query.count() == 0

    profile = client.get(
        f"/api/users/{invitee['user']['id']}", headers=headers(inviter),
    )
    assert profile.status_code == 200
    assert profile.get_json()['invited_by_you'] is True

    requested = client.post(
        '/api/friends/request', headers=headers(invitee),
        json={'user_id': inviter['user']['id']},
    )
    assert requested.status_code == 201
    assert Friendship.query.one().status == 'pending'


def source_section(start, end):
    offset = APP_SOURCE.index(start)
    return APP_SOURCE[offset:APP_SOURCE.index(end, offset)]


def test_frontend_persists_validates_and_resumes_complete_invite_intent():
    share = source_section(
        'async function shareInviteLink()',
        'function setupPullToRefresh()',
    )
    assert "game.visibility === 'open'" in share
    assert '!game.is_instant' in share
    assert "inviteUrl.searchParams.set('game', String(nextGame.id))" in share
    assert 'navigator.share({ title: \'Third Shot\', text, url })' in share

    intent = source_section(
        'function normalizedPlayerInviteIntent',
        'function clearAccountActionHash',
    )
    assert "PLAYER_INVITE_INTENT_KEY = 'pp_player_invite_intent'" in APP_SOURCE
    assert r"/^#invite\/(\d+)(?:\/game\/(\d+))?$/" in intent
    assert "card.destination?.kind === 'game'" in intent
    assert "localStorage.setItem('pp_invite_ref'" in intent

    auth = source_section('function setupAuth()', 'function purgeAccountChatDrafts')
    assert "readPlayerInviteIntent()?.inviter_id" in auth
    assert 'await resumePlayerInviteIntentAfterAuth()' in auth
    assert auth.index('await resumePlayerInviteIntentAfterAuth()') < auth.index(
        'runNewPlayerOnboarding()',
    )

    resume = source_section(
        'async function handleInviteRef',
        'function reportClientError',
    )
    assert "title: 'Send a friend request?'" in resume
    assert "method: 'POST'" in resume and "api('/friends/request'" in resume
    assert "? { kind: 'game', id: validated.game_id }" in resume
    assert ": { kind: 'player', id: validated.inviter_id }" in resume
    assert 'That shared game is no longer available.' in resume
    assert 'finally { clearPlayerInviteIntent(); }' in resume

    onboarding = source_section(
        'function runNewPlayerOnboarding', 'function startPlayLiveRefresh',
    )
    assert 'resumePlayerInviteIntentAfterAuth();' in onboarding
    boot = source_section('async function boot()', 'boot().catch')
    assert 'capturePlayerInviteIntentFromLocation()' in boot
    assert 'loadSignedOutPlayerInvite(capturedPlayerInvite)' in boot
    assert 'await resumePlayerInviteIntentAfterAuth()' in boot
