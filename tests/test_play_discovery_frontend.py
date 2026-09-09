"""Executable discovery and roster summaries, including timezone boundaries."""
import json
import os
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def function(name, next_name):
    start = APP.index(f'  function {name}(')
    return APP[start:APP.index(f'  function {next_name}(', start)]


def run(script):
    result = subprocess.run(['node', '--input-type=module', '-e', script],
        capture_output=True, check=True, text=True, env={**os.environ, 'TZ':'America/Los_Angeles'})
    return json.loads(result.stdout)


def test_today_ends_at_local_midnight_including_dst_day():
    output = run(function('playDiscoveryWindow', 'playDiscoveryQuery') + '''
      const start = new Date('2026-11-01T00:30:00-07:00');
      const today = playDiscoveryWindow('today', start);
      const now = playDiscoveryWindow('now', start);
      console.log(JSON.stringify([today.startsBefore.toISOString(),
        today.endsAfter.toISOString(),now.startsBefore-start,
        playDiscoveryWindow('any',start).startsBefore]));
    ''')
    assert output == ['2026-11-02T08:00:00.000Z', '2026-11-01T07:30:00.000Z', 3600000, None]


def test_roster_status_distinguishes_capacity_from_confirmed_attendance():
    output = run(function('gameRosterStatus', 'gameRosterStatusHtml') + '''
      const base = {status:'upcoming',max_players:4,players:[{}, {}, {}, {}]};
      console.log(JSON.stringify([
        gameRosterStatus({...base,attendance_confirmed_count:2,attendance_confirmation_due:true}),
        gameRosterStatus({...base,attendance_confirmed_count:4}),
        gameRosterStatus({...base,players:[{}],attendance_confirmed_count:1}),
        gameRosterStatus({...base,attendance_confirmation_due:true,is_joined:true}),
        gameRosterStatus({...base,status:'cancelled'}),
        gameRosterStatus({...base,is_instant:true})]));
    ''')
    assert output[0]['label'] == 'Full'
    assert output[0]['detail'] == '2 still need to confirm'
    assert output[1] == {'tone': 'ready', 'label': '4 joined', 'detail': 'Full'}
    assert output[2] == {'tone': 'forming', 'label': '1 joined', 'detail': '3 spots left'}
    assert output[3]['label'] == 'Confirm your spot'
    assert output[4:] == [None, None]


def test_preferences_are_account_scoped_and_validate_saved_values():
    helpers = function('loadPlayPreferences','playDiscoveryWindow')
    output = run('''
      const state={me:{id:1}};
      const saved={
        'thirdshot-play-preferences:1':JSON.stringify({radius:50,level:3.5}),
        'thirdshot-play-preferences:2':JSON.stringify({radius:-100,level:'bad'})};
      const localStorage={getItem:key=>saved[key],setItem:(key,value)=>saved[key]=value};
      const normalizedGameLevel = value => [2,2.5,3,3.5,4,4.5,5,5.5].includes(Number(value)) ? Number(value) : null;
    ''' + helpers + '''
      loadPlayPreferences();
      const first=[state.playRadius,state.playLevelFilter];
      state.me.id=2; loadPlayPreferences(); savePlayPreferences();
      console.log(JSON.stringify([first,[state.playRadius,state.playLevelFilter],JSON.parse(saved['thirdshot-play-preferences:1'])]));
    ''')
    assert output == [[50,'3.5'],[25,''],{'radius':50,'level':3.5}]


def test_repeat_plan_retains_duration_and_does_not_create_before_review():
    source = APP[APP.index('  function completedCrewPlannerOptions'):APP.index('  function completedCrewConnectionsHtml')]
    assert 'durationMinutes: game.duration_minutes ?? 0' in source
    assert "visibility: 'private'" in source
    assert 'requireAllInvitees: true' in source
    assert "api(`/games/${game.id}/crew`)" in source
    assert "method: 'POST'" not in source
    assert "state.playLane !== 'plans'" in source


def test_custom_dates_use_local_day_bounds_and_reject_nonexistent_times():
    output=run(function('playCustomDiscoveryWindow','openPlayDiscoveryFilters') + """
      const serial = filters => {
        try { const w=playCustomDiscoveryWindow(filters,new Date('2026-01-01T00:00:00Z'));
          return [w.startsAfter.toISOString(),w.startsBefore.toISOString()]; }
        catch (error) { return error.message; }
      };
      console.log(JSON.stringify([
        serial({date:'2026-11-01'}),serial({date:'2026-03-08'}),
        serial({date:'2026-11-02',startTime:'17:00',endTime:'19:00'}),
        serial({date:'2026-02-30'}),serial({date:'2026-03-08',startTime:'02:30'}),
        serial({date:'2026-11-02',startTime:'19:00',endTime:'17:00'})]));
    """)
    assert output[:3] == [
        ['2026-11-01T07:00:00.000Z','2026-11-02T08:00:00.000Z'],
        ['2026-03-08T08:00:00.000Z','2026-03-09T07:00:00.000Z'],
        ['2026-11-03T01:00:00.000Z','2026-11-03T03:00:00.000Z']]
    assert all('valid date' in item for item in output[3:5])
    assert 'after' in output[5]


def test_discovery_query_combines_court_date_and_open_spots():
    helpers=function('playDiscoveryWindow','openPlayDiscoveryFilters')
    output=run("""const state={playWhen:'custom',playFilters:{date:'2027-02-04',
      startTime:'18:00',endTime:'20:00',courtId:42,courtName:'Court',openSpots:true}};
    """ + helpers + """
      const custom=Object.fromEntries(new URLSearchParams(playDiscoveryQuery().slice(1)));
      state.playFilters={courtId:43,openSpots:false}; state.playWhen='today';
      const today=Object.fromEntries(new URLSearchParams(playDiscoveryQuery().slice(1)));
      console.log(JSON.stringify([custom,today]));
    """)
    assert output[0]['starts_after']=='2027-02-05T02:00:00.000Z'
    assert output[0]['starts_before']=='2027-02-05T04:00:00.000Z'
    assert output[0]['court_id']=='42' and output[0]['open_spots']=='1'
    assert output[1]['court_id']=='43' and 'starts_before' in output[1]
    assert 'starts_after' not in output[1] and 'open_spots' not in output[1]


def test_held_places_are_not_displayed_as_open_capacity():
    output=run(function('gameRosterStatus','gameRosterStatusHtml')+"""
      const game={status:'upcoming',max_players:4,players:[{},{}],reserved_offer_count:2,spots_left:0};
      console.log(JSON.stringify([gameRosterStatus(game),
        gameRosterStatus({...game,reserved_offer_count:1,spots_left:1}),
        gameRosterStatus({...game,spots_left:null})]));
    """)
    assert output[0]=={'tone':'forming','label':'2 joined','detail':'2 spots held'}
    assert output[1]['detail']=='1 spot left · 1 held'
    assert output[2]['detail']=='2 spots held'


def test_consent_decisions_expire_and_never_treat_host_request_as_joining():
    output=run(function('playGameDecision','gameRosterStatus')+"""
      const now=Date.parse('2026-09-10T12:00:00Z');
      const game={status:'upcoming',is_joined:false,waitlist_offer:{expires_at:'2026-09-10T12:30:00Z'}};
      console.log(JSON.stringify([playGameDecision(game,now),
        playGameDecision(game,now+31*60000),
        playGameDecision({...game,is_joined:true},now),
        playGameDecision({status:'upcoming',is_joined:true,host_handoff:{can_respond:true,expires_at:'2026-09-10T13:00:00Z'}},now),
        playGameDecision({...game,status:'cancelled'},now)]));
    """)
    assert output[0]['kind']=='offer' and output[0]['action']=='Review spot offer'
    assert output[1:3]==[None,None]
    assert output[3]['kind']=='host' and output[3]['action']=='Review host request'
    assert output[4] is None
