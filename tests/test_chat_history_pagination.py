"""Focused contracts for backward chat and DM-inbox pagination."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import (
    Court, Crew, Game, GamePlayer, League, LeagueMember, Message, Tournament,
    User, utcnow,
)


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Pagination Park',
            city='Costa Mesa',
            state='CA',
            county_slug='orange-county',
            latitude=33.66,
            longitude=-117.91,
            num_courts=6,
        ))
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, suffix='viewer'):
    response = client.post('/api/auth/register', json={
        'email': f'{suffix}@example.com',
        'password': 'secret123',
        'display_name': suffix.title(),
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def headers(player):
    return {'Authorization': f"Bearer {player['token']}"}


def _assert_backward_pages(client, endpoint, auth, expected_ids):
    first = client.get(f'{endpoint}?limit=2', headers=auth)
    assert first.status_code == 200, first.get_json()
    first_data = first.get_json()
    assert [item['id'] for item in first_data['items']] == expected_ids[-2:]
    assert first_data['has_more'] is False
    assert first_data['has_older'] is True
    assert first_data['next_before_id'] == expected_ids[-2]

    second = client.get(
        f"{endpoint}?limit=2&before_id={first_data['next_before_id']}",
        headers=auth,
    )
    assert second.status_code == 200, second.get_json()
    second_data = second.get_json()
    assert [item['id'] for item in second_data['items']] == expected_ids[1:3]
    assert second_data['has_older'] is True
    assert second_data['next_before_id'] == expected_ids[1]

    last = client.get(
        f"{endpoint}?limit=2&before_id={second_data['next_before_id']}",
        headers=auth,
    )
    assert last.status_code == 200, last.get_json()
    last_data = last.get_json()
    assert [item['id'] for item in last_data['items']] == expected_ids[:1]
    assert last_data['has_older'] is False
    assert last_data['next_before_id'] is None

    # Existing forward polling remains chronological and keeps its independent
    # has_more contract rather than reinterpreting the backward cursor fields.
    delta = client.get(
        f'{endpoint}?since_id={expected_ids[2]}', headers=auth,
    )
    assert delta.status_code == 200, delta.get_json()
    delta_data = delta.get_json()
    assert [item['id'] for item in delta_data['items']] == expected_ids[3:]
    assert delta_data['has_more'] is False
    assert delta_data['has_older'] is False
    assert delta_data['next_before_id'] is None


def test_every_non_club_chat_room_and_dm_pages_backward(client, app):
    viewer = register(client)
    viewer_id = viewer['user']['id']
    auth = headers(viewer)

    with app.app_context():
        court = Court.query.one()
        partner = User(
            email='partner@example.com',
            password_hash='not-used',
            display_name='Partner',
        )
        game = Game(
            court_id=court.id,
            creator_id=viewer_id,
            scheduled_at=utcnow() + timedelta(hours=1),
        )
        tournament = Tournament(
            name='Paged Tournament',
            court_id=court.id,
            organizer_id=viewer_id,
            starts_at=utcnow() + timedelta(days=1),
        )
        crew = Crew(owner_id=viewer_id, name='Paged Crew')
        league = League(
            name='Paged League',
            court_id=court.id,
            organizer_id=viewer_id,
            starts_at=utcnow() + timedelta(days=1),
        )
        db.session.add_all([partner, game, tournament, crew, league])
        db.session.flush()
        db.session.add(GamePlayer(game_id=game.id, user_id=viewer_id))
        db.session.add(LeagueMember(league_id=league.id, user_id=viewer_id))

        scopes = {
            f'/api/chat/{partner.id}': {'recipient_id': partner.id},
            f'/api/courts/{court.id}/chat': {'court_id': court.id},
            f'/api/games/{game.id}/chat': {'game_id': game.id},
            f'/api/tournaments/{tournament.id}/chat': {
                'tournament_id': tournament.id,
            },
            f'/api/crews/{crew.id}/chat': {'crew_id': crew.id},
            f'/api/leagues/{league.id}/chat': {'league_id': league.id},
        }
        ids_by_endpoint = {}
        for endpoint, scope in scopes.items():
            rows = [
                Message(
                    sender_id=viewer_id,
                    body=f'{endpoint} message {index}',
                    **scope,
                )
                for index in range(5)
            ]
            db.session.add_all(rows)
            db.session.flush()
            ids_by_endpoint[endpoint] = [row.id for row in rows]
        db.session.commit()

    for endpoint, expected_ids in ids_by_endpoint.items():
        _assert_backward_pages(client, endpoint, auth, expected_ids)

    ambiguous = client.get(
        f'{next(iter(ids_by_endpoint))}?since_id=1&before_id=2',
        headers=auth,
    )
    assert ambiguous.status_code == 400
    assert ambiguous.get_json()['error'] == 'conflicting_chat_cursors'


def test_dm_inbox_pages_all_partners_even_after_one_chat_exceeds_old_cap(
        client, app):
    viewer = register(client, 'inbox-viewer')
    viewer_id = viewer['user']['id']
    auth = headers(viewer)

    with app.app_context():
        partners = [
            User(
                email=f'inbox-partner-{index}@example.com',
                password_hash='not-used',
                display_name=f'Partner {index}',
            )
            for index in range(1, 6)
        ]
        db.session.add_all(partners)
        db.session.flush()
        for partner in partners:
            db.session.add(Message(
                sender_id=partner.id,
                recipient_id=viewer_id,
                body=f'hello from {partner.display_name}',
            ))
        # The former 500-message pre-cap saw only this noisy conversation and
        # silently dropped every older partner from the inbox.
        db.session.add_all([
            Message(
                sender_id=partners[-1].id,
                recipient_id=viewer_id,
                body=f'noisy message {index}',
            )
            for index in range(505)
        ])
        db.session.commit()
        expected_partner_ids = [partner.id for partner in reversed(partners)]
        noisy_partner_id = partners[-1].id

    seen = []
    before_id = None
    first_page = None
    while True:
        suffix = '?limit=2'
        if before_id is not None:
            suffix += f'&before_id={before_id}'
        response = client.get(f'/api/chat{suffix}', headers=auth)
        assert response.status_code == 200, response.get_json()
        data = response.get_json()
        if first_page is None:
            first_page = data
        seen.extend(item['user']['id'] for item in data['items'])
        if not data['has_older']:
            assert data['next_before_id'] is None
            break
        assert data['next_before_id'] is not None
        before_id = data['next_before_id']

    assert seen == expected_partner_ids
    assert len(seen) == len(set(seen)) == 5
    noisy = next(
        item for item in first_page['items']
        if item['user']['id'] == noisy_partner_id
    )
    assert noisy['unread'] == 506

    invalid = client.get('/api/chat?before_id=not-an-id', headers=auth)
    assert invalid.status_code == 400
    assert invalid.get_json()['error'] == 'invalid_before_id'

    oversized = client.get(
        f"/api/chat?before_id={'9' * 100}", headers=auth,
    )
    assert oversized.status_code == 400
    assert oversized.get_json()['error'] == 'invalid_before_id'
