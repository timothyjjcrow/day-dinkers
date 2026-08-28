"""Focused regression coverage for disbanding a club with linked history."""
from datetime import datetime

import pytest

from backend.app import create_app, db
from backend.models import (
    Club, ClubChatRead, ClubMember, Court, Game, League, Message,
    Notification, Tournament, User,
)
from backend.routes.clubs import _delete_club


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_delete_club_clears_nullable_links_and_removes_owned_rows(app):
    user = User(
        email='owner@example.com',
        password_hash='not-used',
        display_name='Owner',
    )
    court = Court(name='History Court')
    db.session.add_all([user, court])
    db.session.flush()

    club = Club(name='History Club', creator_id=user.id)
    db.session.add(club)
    db.session.flush()

    member = ClubMember(club=club, user_id=user.id, role='owner')
    read_marker = ClubChatRead(
        club_id=club.id,
        user_id=user.id,
        last_read_message_id=0,
    )
    retained_rows = [
        Game(
            court_id=court.id,
            creator_id=user.id,
            club_id=club.id,
            scheduled_at=datetime(2026, 1, 2, 12, 0),
        ),
        Tournament(
            name='History Tournament',
            court_id=court.id,
            organizer_id=user.id,
            club_id=club.id,
            starts_at=datetime(2026, 1, 3, 12, 0),
        ),
        League(
            name='History League',
            court_id=court.id,
            organizer_id=user.id,
            club_id=club.id,
            starts_at=datetime(2026, 1, 4, 12, 0),
        ),
    ]
    club_owned_rows = [
        Message(sender_id=user.id, club_id=club.id, body='Club history'),
        Notification(
            user_id=user.id,
            kind='club_update',
            title='Club history',
            related_club_id=club.id,
        ),
    ]
    db.session.add_all([
        member, read_marker, *retained_rows, *club_owned_rows,
    ])
    db.session.commit()

    club_id = club.id
    member_id = member.id
    read_marker_id = read_marker.id
    retained_ids = [(type(row), row.id) for row in retained_rows]
    club_owned_ids = [(type(row), row.id) for row in club_owned_rows]

    _delete_club(club)
    db.session.commit()
    db.session.remove()

    assert db.session.get(Club, club_id) is None
    assert db.session.get(ClubMember, member_id) is None
    assert ClubChatRead.query.filter_by(id=read_marker_id).count() == 0
    for model, row_id in retained_ids:
        row = db.session.get(model, row_id)
        assert row is not None
        assert row.club_id is None
    for model, row_id in club_owned_ids:
        assert db.session.get(model, row_id) is None
