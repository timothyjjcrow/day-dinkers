"""Search and quotes cannot widen a conversation's audience or duplicate a send."""
from datetime import timedelta

import pytest

from backend.app import db
from backend.models import (Club, ClubMember, Court, Crew, CrewMember, Friendship,
    Game, GamePlayer, League, LeagueMember, Message, MessageSendAttempt, Tournament,
    TournamentEntry, utcnow)
from tests.test_community_chat_completion import app, client, register, auth


def pair(client):
    left = register(client, 'reply-left@example.test', 'Left')
    right = register(client, 'reply-right@example.test', 'Right')
    db.session.add(Friendship(requester_id=left['user']['id'], addressee_id=right['user']['id'], status='accepted'))
    db.session.commit()
    return left, right


def test_message_search_precedes_pagination_escapes_wildcards_and_does_not_mark_read(client, app):
    left, right = pair(client)
    ids = []
    for index in range(35):
        message = Message(sender_id=right['user']['id'], recipient_id=left['user']['id'], body=f'North gate code 10%_{index}')
        db.session.add(message); db.session.flush(); ids.append(message.id)
    db.session.add_all([Message(sender_id=right['user']['id'], recipient_id=left['user']['id'], body='New unrelated note') for _ in range(105)])
    db.session.commit()
    path = f"/api/messages/search?channel=dm:{right['user']['id']}&q=North%20gate"
    first = client.get(path, headers=auth(left)).get_json()
    assert [m['id'] for m in first['items']] == list(reversed(ids[-30:]))
    assert first['has_more']
    older = client.get(path + f"&before_id={first['next_before_id']}", headers=auth(left)).get_json()
    assert [m['id'] for m in older['items']] == list(reversed(ids[:5]))
    assert not older['has_more']
    literal = client.get(f"/api/messages/search?channel=dm:{right['user']['id']}&q=10%25_", headers=auth(left)).get_json()
    assert len(literal['items']) == 30
    assert Message.query.filter(Message.read_at.isnot(None)).count() == 0
    assert client.get(path).status_code == 401
    assert client.get(path + '&before_id=-1', headers=auth(left)).status_code == 400
    client.post(f"/api/users/{right['user']['id']}/block", headers=auth(left))
    assert client.get(path, headers=auth(left)).status_code == 403


def test_reply_requires_exact_direct_pair_and_retry_keeps_original_target(client, app):
    left, right = pair(client)
    outsider = register(client, 'reply-outsider@example.test', 'Outsider')
    original = Message(sender_id=right['user']['id'], recipient_id=left['user']['id'], body='Meet at the north gate')
    private = Message(sender_id=right['user']['id'], recipient_id=outsider['user']['id'], body='Other private message')
    db.session.add_all([original, private]); db.session.commit()
    path = f"/api/chat/{right['user']['id']}"
    payload = {'body': 'See you there', 'reply_to_id': original.id, 'client_attempt_id': 'reply-one'}
    sent = client.post(path, json=payload, headers=auth(left))
    assert sent.status_code == 201, sent.get_json()
    assert sent.get_json()['reply_to']['body'] == original.body
    replay = client.post(path, json=payload, headers=auth(left))
    assert replay.status_code == 200 and replay.get_json()['id'] == sent.get_json()['id']
    assert client.post(path, json={**payload, 'reply_to_id': private.id}, headers=auth(left)).status_code == 409
    assert client.post(path, json={'body': 'Leak?', 'reply_to_id': private.id}, headers=auth(left)).status_code == 409
    assert client.post(path, json={'body': 'Bad', 'reply_to_id': True}, headers=auth(left)).status_code == 400
    assert MessageSendAttempt.query.filter_by(client_attempt_id='reply-one').count() == 1
    db.session.delete(original); db.session.commit(); db.session.expire_all()
    # A lost-response retry remains resolved even if its original was removed.
    replay = client.post(path, json=payload, headers=auth(left))
    assert replay.status_code == 200
    assert replay.get_json()['reply_to'] is None or replay.get_json()['reply_to'] == {'unavailable': True}
    assert client.post(path, json={**payload, 'client_attempt_id': 'new-after-delete'}, headers=auth(left)).status_code == 409


def test_reply_draft_reference_rechecks_scope_and_redaction_before_send(client, app):
    left, right = pair(client)
    original = Message(sender_id=right['user']['id'], recipient_id=left['user']['id'], body='Original meeting detail')
    court = Court(name='Another room', city='Town', state='CA', latitude=33, longitude=-117)
    db.session.add_all([original, court]); db.session.flush()
    room_message = Message(sender_id=right['user']['id'], court_id=court.id, body='Unrelated court detail')
    db.session.add(room_message); db.session.commit()
    channel = f"dm:{right['user']['id']}"
    reference = f'/api/messages/{original.id}/reference?channel={channel}'
    assert client.get(reference, headers=auth(left)).get_json()['body'] == original.body
    assert client.get(reference).status_code == 401
    assert client.get(f'/api/messages/{room_message.id}/reference?channel={channel}', headers=auth(left)).status_code == 404
    path = f"/api/chat/{right['user']['id']}"
    assert client.post(path, json={'body':'Wrong room', 'reply_to_id':room_message.id}, headers=auth(left)).status_code == 409
    payload = {'body':'My reply', 'reply_to_id':original.id, 'client_attempt_id':'redacted-original'}
    sent = client.post(path, json=payload, headers=auth(left))
    assert sent.status_code == 201
    original.body = ''; original.image_data = None
    db.session.commit()
    assert client.get(reference, headers=auth(left)).status_code == 404
    search = client.get(f'/api/messages/search?channel={channel}&q=My%20reply', headers=auth(left)).get_json()
    assert search['items'][0]['reply_to'] == {'unavailable':True}
    # Replaying a committed send succeeds without copying the now-redacted quote.
    replay = client.post(path, json=payload, headers=auth(left))
    assert replay.status_code == 200 and replay.get_json()['reply_to'] == {'unavailable':True}
    assert client.post(path, json={**payload,'client_attempt_id':'new-redacted'}, headers=auth(left)).status_code == 409
    assert MessageSendAttempt.query.filter_by(client_attempt_id='new-redacted').count() == 0


@pytest.mark.parametrize('kind', ['court', 'game', 'tournament', 'club', 'crew', 'league'])
def test_room_search_and_replies_keep_membership_and_blocked_original_boundaries(client, app, kind):
    left, right = pair(client)
    outsider = register(client, 'room-outsider@example.test', 'Outside')
    me, them = left['user']['id'], right['user']['id']
    court = Court(name='Reply court', city='Town', state='CA', latitude=33, longitude=-117)
    db.session.add(court); db.session.flush()
    if kind == 'court':
        room = court
    elif kind == 'game':
        room = Game(court_id=court.id, creator_id=them, scheduled_at=utcnow()+timedelta(days=3))
    elif kind == 'tournament':
        room = Tournament(name='Reply event', court_id=court.id, organizer_id=them, starts_at=utcnow()+timedelta(days=3))
    elif kind == 'club':
        room = Club(name='Reply public group', creator_id=them)
    elif kind == 'crew':
        room = Crew(name='Reply private group', owner_id=them)
    else:
        room = League(name='Reply league', court_id=court.id, organizer_id=them, starts_at=utcnow()+timedelta(days=3))
    db.session.add(room); db.session.flush()
    for uid in (me, them):
        if kind == 'game': db.session.add(GamePlayer(game_id=room.id, user_id=uid))
        if kind == 'tournament': db.session.add(TournamentEntry(tournament_id=room.id, player1_id=uid))
        if kind == 'club': db.session.add(ClubMember(club_id=room.id, user_id=uid))
        if kind == 'crew': db.session.add(CrewMember(crew_id=room.id, user_id=uid))
        if kind == 'league': db.session.add(LeagueMember(league_id=room.id, user_id=uid))
    original = Message(sender_id=them, body='North gate after warmup', **{f'{kind}_id': room.id})
    db.session.add(original); db.session.commit()
    path = f'/api/messages/search?channel={kind}:{room.id}&q=North'
    assert [m['id'] for m in client.get(path, headers=auth(left)).get_json()['items']] == [original.id]
    if kind != 'court':
        assert client.get(path, headers=auth(outsider)).status_code in (403, 404)
    collection = {'court':'courts','game':'games','tournament':'tournaments','club':'clubs','crew':'crews','league':'leagues'}[kind]
    sent = client.post(f'/api/{collection}/{room.id}/chat', headers=auth(left),
        json={'body':'Confirmed north gate', 'reply_to_id':original.id, 'client_attempt_id':f'{kind}-reply'})
    assert sent.status_code == 201, sent.get_json()
    assert sent.get_json()['reply_to']['body'] == original.body
    # Even a reply authored by the viewer must not quote the blocked author.
    client.post(f'/api/users/{them}/block', headers=auth(left))
    result = client.get(path, headers=auth(left))
    if result.status_code == 200:
        assert all(m['sender_id'] != them for m in result.get_json()['items'])
        mine = next(m for m in result.get_json()['items'] if m['id'] == sent.get_json()['id'])
        assert mine['reply_to'] == {'unavailable': True}
    else:
        assert kind == 'crew' and result.status_code in (403, 404)
