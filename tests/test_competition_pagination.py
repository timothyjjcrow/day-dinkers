"""Competition discovery returns stable cursor pages without silent caps."""

from datetime import timedelta
from pathlib import Path

import pytest

from backend.app import create_app, db
from backend.models import (
    Court, League, LeagueMatch, LeagueMember, Tournament, User, utcnow,
)


ROOT = Path(__file__).resolve().parents[1]


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


def register(client):
    response = client.post('/api/auth/register', json={
        'email': 'pager@example.com',
        'password': 'secret123',
        'display_name': 'Pager',
    })
    assert response.status_code == 201
    return response.get_json()


def headers(account):
    return {'Authorization': f"Bearer {account['token']}"}


def collect_pages(client, path, auth_headers, limit=2):
    cursor = None
    items = []
    pages = []
    while True:
        separator = '&' if '?' in path else '?'
        url = f'{path}{separator}limit={limit}'
        if cursor:
            url += f'&cursor={cursor}'
        response = client.get(url, headers=auth_headers)
        assert response.status_code == 200, response.get_json()
        page = response.get_json()
        pages.append(page)
        items.extend(page['items'])
        cursor = page['next_cursor']
        if not cursor:
            break
    return items, pages


def test_tournament_mine_and_nearby_lists_page_without_duplicates(client, app):
    account = register(client)
    auth_headers = headers(account)
    with app.app_context():
        user = db.session.get(User, account['user']['id'])
        court = Court(
            name='Pager Courts', city='Irvine', state='CA',
            latitude=33.68, longitude=-117.82, num_courts=8,
        )
        db.session.add(court)
        db.session.flush()
        for index in range(5):
            db.session.add(Tournament(
                name=f'Paged Tournament {index}',
                court_id=court.id,
                organizer_id=user.id,
                starts_at=utcnow() + timedelta(days=index + 1),
            ))
        db.session.commit()

    mine, mine_pages = collect_pages(
        client, '/api/tournaments?mine=1', auth_headers,
    )
    nearby, nearby_pages = collect_pages(
        client, '/api/tournaments?lat=33.68&lng=-117.82&radius=10',
        auth_headers,
    )
    assert len(mine) == len({item['id'] for item in mine}) == 5
    assert len(nearby) == len({item['id'] for item in nearby}) == 5
    assert [page['count'] for page in mine_pages] == [2, 2, 1]
    assert [page['has_more'] for page in nearby_pages] == [True, True, False]
    assert mine_pages[-1]['total'] == 5
    assert nearby_pages[-1]['total'] == 5


def test_league_list_pages_public_and_member_history_together(client, app):
    account = register(client)
    auth_headers = headers(account)
    with app.app_context():
        user = db.session.get(User, account['user']['id'])
        court = Court(
            name='League Pager Courts', city='Irvine', state='CA',
            latitude=33.69, longitude=-117.83, num_courts=6,
        )
        db.session.add(court)
        db.session.flush()
        leagues = []
        for index in range(4):
            league = League(
                name=f'Paged League {index}',
                court_id=court.id,
                organizer_id=user.id,
                starts_at=utcnow() + timedelta(days=index + 1),
            )
            db.session.add(league)
            leagues.append(league)
        history = League(
            name='Completed Member League',
            court_id=court.id,
            organizer_id=user.id,
            starts_at=utcnow() - timedelta(days=30),
            status='completed',
        )
        db.session.add(history)
        db.session.flush()
        db.session.add_all([
            LeagueMember(league=league, user_id=user.id)
            for league in [*leagues, history]
        ])
        db.session.commit()

    items, pages = collect_pages(client, '/api/leagues', auth_headers)
    assert len(items) == len({item['id'] for item in items}) == 5
    assert [page['count'] for page in pages] == [2, 2, 1]
    assert pages[-1]['total'] == 5
    assert items[-1]['status'] == 'completed'


def test_competition_lists_reject_malformed_cursors(client):
    auth_headers = headers(register(client))
    assert client.get(
        '/api/tournaments?mine=1&cursor=bad', headers=auth_headers,
    ).status_code == 400
    assert client.get(
        '/api/leagues?cursor=bad', headers=auth_headers,
    ).status_code == 400


def test_me_exposes_account_wide_competition_action_badge_count(client, app):
    account = register(client)
    opponent = client.post('/api/auth/register', json={
        'email': 'opponent@example.com',
        'password': 'secret123',
        'display_name': 'Opponent',
    }).get_json()
    with app.app_context():
        court = Court(
            name='Action Courts', city='Irvine', state='CA',
            latitude=33.7, longitude=-117.8, num_courts=4,
        )
        db.session.add(court)
        db.session.flush()
        league = League(
            name='Action League', court_id=court.id,
            organizer_id=account['user']['id'],
            starts_at=utcnow() - timedelta(days=1), status='active',
            current_round=1, round_started_at=utcnow(),
        )
        db.session.add(league)
        db.session.flush()
        db.session.add_all([
            LeagueMember(league=league, user_id=account['user']['id'], box=1),
            LeagueMember(league=league, user_id=opponent['user']['id'], box=1),
            LeagueMatch(
                league=league, round=1, box=1,
                player1_id=account['user']['id'],
                player2_id=opponent['user']['id'],
            ),
        ])
        db.session.commit()

    payload = client.get('/api/me', headers=headers(account)).get_json()
    assert payload['competition_actions'] == {
        'count': 1, 'tournaments': 0, 'leagues': 1,
    }


def test_compete_ui_exposes_action_badge_and_progressive_pages():
    app_source = (ROOT / 'public' / 'app-v15.js').read_text()
    index = (ROOT / 'public' / 'index.html').read_text()
    assert 'id="competition-action-badge"' in index
    for source in (
        'mine-tournaments', 'nearby-tournaments',
        'mine-leagues', 'nearby-leagues',
    ):
        assert f'data-competition-page="{source}"' in app_source
    assert "api(`${baseUrl}&cursor=${encodeURIComponent(cursor)}`)" in app_source
    assert "state.competitionActionCount" in app_source
    assert "Compete, ${actionCount} action" in app_source


def test_play_home_schedule_includes_owned_tournaments_in_the_next_week(client, app):
    account = register(client)
    with app.app_context():
        user = db.session.get(User, account['user']['id'])
        other = User(
            email='other-organizer@example.com', password_hash='not-used',
            display_name='Other Organizer',
        )
        court = Court(
            name='Agenda Courts', city='Irvine', state='CA',
            latitude=33.7, longitude=-117.8, num_courts=4,
        )
        db.session.add_all([other, court])
        db.session.flush()
        mine = Tournament(
            name='My Weekend Tournament', court_id=court.id,
            organizer_id=user.id, starts_at=utcnow() + timedelta(days=2),
        )
        too_late = Tournament(
            name='My Later Tournament', court_id=court.id,
            organizer_id=user.id, starts_at=utcnow() + timedelta(days=9),
        )
        unrelated = Tournament(
            name='Someone Else Tournament', court_id=court.id,
            organizer_id=other.id, starts_at=utcnow() + timedelta(days=2),
        )
        db.session.add_all([mine, too_late, unrelated])
        db.session.commit()
        mine_id = mine.id

    payload = client.get('/api/play/home', headers=headers(account)).get_json()
    assert [item['id'] for item in payload['competitions']] == [mine_id]
    assert payload['competitions'][0]['kind'] == 'tournament'
    assert payload['competitions'][0]['is_organizer'] is True


def test_play_schedule_renders_tournaments_as_drill_in_agenda_rows():
    app_source = (ROOT / 'public' / 'app-v15.js').read_text()
    start = app_source.index('function playScheduleHtml')
    schedule = app_source[start:app_source.index('function playNearbyNowHtml', start)]
    assert "competition.kind === 'tournament'" in schedule
    assert 'data-open-tournament="${tournament.id}"' in schedule
    assert "competitions: homeResult.status === 'fulfilled'" in app_source
    assert 'makePressable(button, () => openTournamentScreen' in app_source
