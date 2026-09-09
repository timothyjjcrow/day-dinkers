"""Tournament capacity is held only by explicit, expiring place offers."""
import json
from datetime import timedelta

from test_tournament_operations import app, client, register, auth, field, start
from backend.app import db
from backend.models import Tournament, TournamentEntry, TournamentWaitlist, Notification, utcnow
from backend.routes.tournaments import maintain_tournament_waitlists


def full_field(client, app):
    organizer, entered, url = field(client, app, count=2, max_entries=2)
    waiting = [register(client, f'waiting-{i}') for i in range(3)]
    for person in waiting:
        joined = client.post(url+'/waitlist',headers=auth(person))
        assert joined.status_code == 201, joined.get_json()
    return organizer, entered, waiting, url


def test_queue_does_not_register_and_acceptance_reserves_capacity(client, app):
    host, entered, waiting, url = full_field(client, app)
    assert TournamentEntry.query.count() == 2
    first = client.get(url,headers=auth(waiting[0])).get_json()
    assert first['my_waitlist']['position'] == 1 and first['waitlist_count'] == 3
    assert first['my_entry_id'] is None and first['waitlist'] == []
    assert len(client.get(url,headers=auth(host)).get_json()['waitlist']) == 3
    client.delete(url+'/register',headers=auth(entered[0]))
    offer = client.get(url,headers=auth(waiting[0])).get_json()
    assert offer['my_waitlist']['status'] == 'offered'
    assert offer['held_offer_count'] == 1 and offer['registration_spots_left'] == 0
    assert offer['entry_count'] == 1 and offer['my_entry_id'] is None
    assert client.post(url+'/register',headers=auth(waiting[1]),json={}).status_code == 409
    assert client.post(url+'/register',headers=auth(waiting[0]),json={}).get_json()['error'] == 'offer_acceptance_required'
    accepted = client.post(url+'/waitlist/respond',headers=auth(waiting[0]),json={'accept':True})
    assert accepted.status_code == 201, accepted.get_json()
    assert accepted.get_json()['my_entry_id'] and accepted.get_json()['my_waitlist']['status'] == 'accepted'
    assert TournamentEntry.query.count() == 2
    assert client.post(url+'/waitlist/respond',headers=auth(waiting[0]),json={'accept':True}).status_code == 200
    assert TournamentEntry.query.count() == 2


def test_pass_and_expiry_offer_next_player_without_automatic_registration(client, app):
    host, entered, waiting, url = full_field(client, app)
    client.delete(url+'/register',headers=auth(entered[0]))
    passed = client.post(url+'/waitlist/respond',headers=auth(waiting[0]),json={'accept':False}).get_json()
    assert passed['my_waitlist']['status'] == 'left'
    second = TournamentWaitlist.query.filter_by(user_id=waiting[1]['user']['id']).one()
    assert second.status == 'offered'
    second.expires_at = utcnow()-timedelta(seconds=1)
    db.session.commit()
    maintain_tournament_waitlists()
    assert second.status == 'expired'
    third = client.get(url,headers=auth(waiting[2])).get_json()
    assert third['my_waitlist']['status'] == 'offered' and third['entry_count'] == 1
    assert client.post(url+'/waitlist/respond',headers=auth(waiting[1]),json={'accept':True}).get_json()['error'] == 'offer_expired'
    assert TournamentEntry.query.count() == 1
    assert Notification.query.filter_by(kind='tournament_waitlist_offer',user_id=waiting[2]['user']['id']).count() == 1


def test_leaving_and_rejoining_preserves_history_and_moves_to_back(client, app):
    host, entered, waiting, url = full_field(client, app)
    client.delete(url+'/waitlist',headers=auth(waiting[0]))
    again = client.post(url+'/waitlist',headers=auth(waiting[0])).get_json()
    assert again['my_waitlist']['position'] == 3
    repeated = client.post(url+'/waitlist',headers=auth(waiting[0])).get_json()
    assert repeated['my_waitlist']['position'] == 3
    assert TournamentWaitlist.query.count() == 3
    history = json.loads(TournamentWaitlist.query.filter_by(user_id=waiting[0]['user']['id']).one().history)
    assert [event['status'] for event in history] == ['queued','left','queued']


def test_start_preview_discloses_closing_queue_and_stale_field_is_rejected(client, app):
    host, entered, waiting, url = full_field(client, app)
    assert client.post(url+'/start',headers=auth(host),json={}).get_json()['error'] == 'preview_required'
    preview = client.get(url+'/preview',headers=auth(host)).get_json()
    assert preview['can_start'] and preview['preview_notes']
    client.delete(url+'/waitlist',headers=auth(waiting[0]))
    assert client.post(url+'/start',headers=auth(host),json={'preview_fingerprint':preview['preview_fingerprint']}).status_code == 409
    started = start(client,host,url)
    assert started['status'] == 'active' and started['waitlist_count'] == 0
    assert client.get(url,headers=auth(waiting[1])).get_json()['my_waitlist']['status'] == 'closed'
    assert client.post(url+'/waitlist/respond',headers=auth(waiting[1]),json={'accept':True}).status_code == 409
    assert TournamentEntry.query.count() == 2


def test_capacity_increase_offers_existing_queue_before_new_signups(client, app):
    host, entered, waiting, url = full_field(client, app)
    updated = client.patch(url,headers=auth(host),json={'max_entries':3})
    assert updated.status_code == 200
    assert updated.get_json()['held_offer_count'] == 1
    assert updated.get_json()['registration_spots_left'] == 0
    assert client.patch(url,headers=auth(host),json={'max_entries':2}).status_code == 400


def test_doubles_place_acceptance_does_not_assume_partner_consent(client, app):
    host, entered, waiting, url = full_field(client, app)
    # Rebuild the isolated fixture field as two complete doubles teams.
    tournament = Tournament.query.one()
    tournament.event_type = 'doubles'
    extras = [register(client,f'doubles-partner-{i}') for i in range(3)]
    for entry, partner in zip(tournament.entries,extras[:2]):
        entry.player2_id = partner['user']['id']
        entry.partner_status = 'accepted'
    db.session.commit()
    client.delete(url+'/register',headers=auth(entered[0]))
    assert client.post(url+'/waitlist/respond',headers=auth(waiting[0]),json={'accept':True}).get_json()['error'] == 'partner_choice_required'
    accepted = client.post(url+'/waitlist/respond',headers=auth(waiting[0]),json={'accept':True,'partner_id':extras[2]['user']['id']})
    assert accepted.status_code == 201, accepted.get_json()
    entry = TournamentEntry.query.filter_by(player1_id=waiting[0]['user']['id']).one()
    assert entry.partner_status == 'pending' and entry.player2_id is None
    partner_detail = client.get(url,headers=auth(extras[2])).get_json()
    assert partner_detail['my_entry_id'] is None and partner_detail['my_partner_action']
    assert client.get(url+'/preview',headers=auth(host)).get_json()['can_start'] is False
    assert client.post(url+'/partner/respond',headers=auth(extras[2]),json={'accept':True}).status_code == 200
    assert entry.partner_status == 'accepted' and entry.player2_id == extras[2]['user']['id']


def test_queue_requires_full_event_and_never_bypasses_entry_or_response_validation(client, app):
    host, entered, url = field(client,app,count=2,max_entries=4)
    person = register(client,'open-place')
    assert client.post(url+'/waitlist',headers=auth(person)).get_json()['error'] == 'registration_available'
    assert client.post(url+'/waitlist',headers=auth(entered[0])).status_code == 409
    assert client.post(url+'/waitlist/respond',headers=auth(person),json={'accept':'true'}).status_code == 400
    assert client.post(url+'/waitlist/respond',headers=auth(person),json={'accept':True}).status_code == 409
    assert TournamentWaitlist.query.count() == 0


def test_maintenance_visits_events_beyond_first_page(client, app):
    host, entered, url = field(client, app, count=2, max_entries=2)
    base = Tournament.query.one()
    for index in range(201):
        event = Tournament(name=f'Queue {index}', court_id=base.court_id,
            organizer_id=host['user']['id'], starts_at=utcnow()+timedelta(days=7), max_entries=2)
        db.session.add(event); db.session.flush()
        db.session.add(TournamentWaitlist(tournament=event, user_id=entered[0]['user']['id']))
    db.session.commit()
    assert maintain_tournament_waitlists()['updated_tournaments'] == 201
    assert TournamentWaitlist.query.filter_by(status='offered').count() == 201
    assert TournamentEntry.query.count() == 2


def test_offer_window_matches_event_lead_time(client, app):
    from backend.routes.tournaments import _maintain_tournament_waitlist
    host, entered, waiting, url = full_field(client, app)
    tournament = Tournament.query.one()
    removed = tournament.entries.pop(0)
    db.session.delete(removed); db.session.flush()
    now = utcnow()
    for lead, expected in ((timedelta(days=7),timedelta(hours=24)), (timedelta(hours=20),timedelta(hours=4)),
                           (timedelta(hours=2),timedelta(minutes=30)), (timedelta(minutes=10),timedelta(minutes=10))):
        for row in tournament.waitlist:
            row.status = 'queued'; row.expires_at = None
        tournament.starts_at = now+lead
        _maintain_tournament_waitlist(tournament, now=now)
        offered = next(row for row in tournament.waitlist if row.status == 'offered')
        assert offered.expires_at == now+expected
