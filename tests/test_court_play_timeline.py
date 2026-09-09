"""One court timeline preserves visibility, provenance and review gates."""
import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from test_business_reviewed_versions import app, client, live, current, versioned
from test_business_governance import auth, register
from backend.app import db
from backend.models import Court, Game, GamePlayer, BusinessProfile, BusinessScheduleItem, BlockedUser, utcnow
from backend.integrations.models import BusinessLinkHealthCheck


def seed(app, client, live):
    owner, bid = live
    day = (utcnow()+timedelta(days=2)).date()
    with app.app_context():
        court = db.session.get(Court, 1)
        court.structured_hours = json.dumps({'timezone':'America/Chicago'})
        court.open_play_schedule_rows = json.dumps([{'weekday':day.strftime('%a').lower(),'start':'08:00','end':'10:00','level':'All levels','cost':'Free'}])
        game = Game(court_id=1, creator_id=owner['user']['id'], title='Doubles together',
            scheduled_at=datetime.combine(day,time(9),ZoneInfo('America/Chicago')).astimezone(UTC).replace(tzinfo=None),
            max_players=4, visibility='open')
        db.session.add(game)
        db.session.flush()
        db.session.add(GamePlayer(game=game,user_id=owner['user']['id']))
        for status, clock in [('scheduled','10:00'),('cancelled','11:00')]:
            db.session.add(BusinessScheduleItem(business_id=bid,title=f'Clinic {status}',kind='lesson',
                recurrence='dated',event_date=day, start_time=clock,end_time='12:00',timezone='America/Chicago',
                status=status,booking_url='https://official.example/clinic'))
        db.session.commit()
    return owner,bid,day


def test_dated_sources_sort_together_and_actions_do_not_imply_a_reservation(app, client, live):
    owner,bid,day = seed(app,client,live)
    result = client.get(f'/api/courts/1/play?from={day}&to={day}').get_json()
    assert result['timezone'] == 'America/Chicago'
    assert [row['source'] for row in result['items']] == ['community','player','venue','venue']
    assert [row['action'] for row in result['items']] == ['plan','open_session','external','none']
    assert result['items'][1]['game']['players'] == []
    assert result['items'][1]['game']['spots_left'] == 3
    assert 'creator_id' not in result['items'][1]['game']
    assert result['items'][3]['status'] == 'cancelled'
    signed = client.get(f'/api/courts/1/play?from={day}&to={day}',headers=auth(owner['token'])).get_json()
    assert signed['items'][1]['action_label'] == 'Open session'


def test_public_timeline_keeps_reviewed_dates_and_holds_only_unsafe_link(app,client,live):
    owner,bid,day = seed(app,client,live)
    data = current(client,owner,bid)
    schedule = data['schedule']
    changed = client.put(f'/api/businesses/{bid}/schedule', json={'items':[{**row,'title':'Private new title','booking_url':'https://official.example/private-clinic'} for row in schedule]},headers=versioned(owner,data))
    assert changed.status_code == 200, changed.get_json()
    with app.app_context():
        db.session.add(BusinessLinkHealthCheck(business_id=bid,link_kind='schedule_booking',url_hash=hashlib.sha256(b'https://official.example/clinic').hexdigest(),status='unsafe'))
        db.session.commit()
    result = client.get(f'/api/courts/1/play?from={day}&to={day}').get_json()
    venue = [row for row in result['items'] if row['source']=='venue']
    assert [row['title'] for row in venue] == ['Clinic scheduled','Clinic cancelled']
    assert all(row['action']=='none' and not row['schedule']['booking_url'] for row in venue)


def test_private_blocked_closed_and_pending_courts_do_not_leak_play(app,client,live):
    owner,bid,day = seed(app,client,live)
    viewer=register(client,'timeline-viewer@example.test')
    with app.app_context():
        game=Game.query.filter_by(court_id=1).first()
        game.visibility='private'
        db.session.commit()
    url=f'/api/courts/1/play?from={day}&to={day}'
    assert all(row['source']!='player' for row in client.get(url).get_json()['items'])
    with app.app_context():
        game=Game.query.filter_by(court_id=1).first()
        game.visibility='open'
        db.session.add(BlockedUser(blocker_id=viewer['user']['id'],blocked_id=owner['user']['id']))
        db.session.commit()
    assert all(row['source']!='player' for row in client.get(url,headers=auth(viewer['token'])).get_json()['items'])
    with app.app_context():
        db.session.get(Court,1).closed=True
        db.session.commit()
    assert client.get(url).get_json()['items']==[]
    with app.app_context():
        db.session.get(Court,1).pending_submission=True
        db.session.commit()
    assert client.get(url).status_code == 404


def test_timeline_rejects_unbounded_or_invalid_dates(client):
    for suffix in ('?from=invalid','?from=2027-01-01&to=2027-03-01','?from=2027-01-02&to=2027-01-01'):
        assert client.get('/api/courts/1/play'+suffix).status_code==400


def test_reviewed_holiday_closure_warns_all_sources_without_cancelling_existing_sessions(app,client,live):
    owner,bid,day=seed(app,client,live)
    with app.app_context():
        venue=db.session.get(BusinessProfile,bid)
        venue.structured_hours=json.dumps({'timezone':'America/Chicago',day.strftime('%a').lower():[{'open':'06:00','close':'22:00'}],'exceptions':{day.isoformat():[]}})
        db.session.commit()
    # A proposed reopening must not change the public warning before review.
    changed=client.patch(f'/api/businesses/{bid}',json={'structured_hours':{'timezone':'America/Chicago',day.strftime('%a').lower():[{'open':'06:00','close':'22:00'}]}},headers=versioned(owner,current(client,owner,bid)))
    assert changed.status_code==200,changed.get_json()
    data=client.get(f'/api/courts/1/play?from={day}&to={day}').get_json()
    assert all(row['hours_conflict'] and row['hours_warning'].startswith('Venue hours') for row in data['items'])
    assert [row['action'] for row in data['items']]==['none','open_session','external','none']
    assert data['items'][1]['action_label']=='View session'
    assert data['items'][2]['action_label']=='Confirm with venue'
    with app.app_context():
        assert Game.query.filter_by(court_id=1).first().status=='upcoming'
        db.session.get(BusinessProfile,bid).published=False
        db.session.commit()
    # Community fallback is unknown, not a fabricated closure.
    fallback=client.get(f'/api/courts/1/play?from={day}&to={day}').get_json()
    assert not any(row['hours_conflict'] for row in fallback['items'])


def test_hours_conflict_catches_split_day_and_overnight_holiday_but_not_unknown_hours():
    from backend.services.court_hours import interval_hours_conflict
    projection={'structured_hours':{'timezone':'America/Chicago','mon':[{'open':'08:00','close':'10:00'},{'open':'10:30','close':'20:00'}]}}
    assert interval_hours_conflict(projection,'2027-01-04T15:00:00Z','2027-01-04T17:00:00Z') is True
    assert interval_hours_conflict(projection,'2027-01-04T14:00:00Z','2027-01-04T16:00:00Z') is False
    overnight={'structured_hours':{'timezone':'America/Chicago','mon':[{'open':'22:00','close':'02:00'}],'exceptions':{'2027-01-05':[]}}}
    assert interval_hours_conflict(overnight,'2027-01-05T05:00:00Z','2027-01-05T07:00:00Z') is True
    assert interval_hours_conflict({'structured_hours':{'timezone':'America/Chicago'}},'2027-01-04T15:00:00Z','2027-01-04T17:00:00Z') is False
    assert interval_hours_conflict({**projection,'hours_dawn_to_dusk':True},'2027-01-04T05:00:00Z') is False


def test_open_play_plan_entry_text_survives_creation_and_reaches_an_invitee_and_public_preview(app,client):
    import subprocess
    from pathlib import Path
    source=Path('public/app-v15.js').read_text()
    helpers=source[source.index('  const COURT_OPEN_PLAY_PLAN_SOURCE'):source.index('  function openCourtWindowPlan')]
    description=subprocess.check_output(['node','-e',helpers+"console.log(courtOpenPlayPlanDescription({visitor_info:{play_access:'reservation',access_type:'fee'}},{cost:'$5',notes:'Meet at the north gate.'}));"],text=True).strip()
    owner=register(client,'plan-owner@example.test')
    invitee=register(client,'plan-invitee@example.test')
    response=client.post('/api/games',json={'court_id':1,'scheduled_at':(utcnow()+timedelta(days=3)).isoformat()+'Z','game_type':'casual','max_players':8,'session_mode':True,'visibility':'open','title':'Open play together','duration_minutes':120,'description':description},headers=auth(owner['token']))
    assert response.status_code==201,response.get_json()
    game_id=response.get_json()['id']
    received=client.get(f'/api/games/{game_id}',headers=auth(invitee['token'])).get_json()
    assert received['description']==description
    assert 'Joining does not register you' in received['description']
    assert 'Venue entry fees may apply' in received['description']
    assert 'Meet at the north gate.' in received['description']
    preview=client.get(f'/api/share-preview?kind=game&id={game_id}').get_json()
    # Use the shared public preview endpoint contract.
    assert preview['details']['description']==description,preview


def test_entry_notice_is_visible_before_join_and_keeps_other_host_notes_separate():
    import subprocess
    from pathlib import Path
    source=Path('public/app-v15.js').read_text()
    helpers=source[source.index('  const COURT_OPEN_PLAY_PLAN_SOURCE'):source.index('  function openCourtWindowPlan')]
    renderer=source[source.index('  function publicSessionFacts'):source.index('  function renderSignedOutShareContext')]
    subprocess.run(['node','-e',r'''
      const assert=require('node:assert/strict');
      const esc=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
      const fmtDateTime=value=>value,gameLevelRangeLabel=()=> 'Any level',uiIcon=()=>'';
    '''+helpers+renderer+r'''
      const description=courtOpenPlayPlanDescription({visitor_info:{play_access:'reservation'}},{notes:'Bring <water>.'});
      const parts=courtEntryDescriptionParts({description});
      assert.ok(parts.entry.includes('Joining does not register you'));
      assert.equal(parts.note,'Bring <water>.');
      assert.equal(courtEntryDescriptionParts({description:'Ordinary host note.'}).entry,'');
      const html=publicShareDetailHtml({public:true,kind:'game',title:'Open play together',details:{description,status:'upcoming',scheduled_at:'2099-01-01T18:00:00Z',spots_left:3,max_players:4,court:{name:'Court'}}});
      assert.ok(html.indexOf('aria-label="Venue entry requirements"') < html.indexOf('data-public-auth'));
      assert.ok(html.includes('Bring &lt;water&gt;.'));
      assert.ok(!html.includes('Bring <water>.'));
    '''],check=True,capture_output=True,text=True)
    game_screen=source[source.index('  function gameScreenHtml'):source.index('  async function openGameScreen')]
    assert game_screen.index('${courtEntryNoticeHtml(game)}') < game_screen.index('class="session-roster"')
