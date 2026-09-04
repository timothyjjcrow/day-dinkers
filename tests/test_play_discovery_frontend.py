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
        gameRosterStatus({...base,attendance_confirmed_count:2}),
        gameRosterStatus({...base,attendance_confirmed_count:4}),
        gameRosterStatus({...base,players:[{}],attendance_confirmed_count:1}),
        gameRosterStatus({...base,attendance_confirmation_due:true,is_joined:true}),
        gameRosterStatus({...base,status:'cancelled'}),
        gameRosterStatus({...base,is_instant:true})]));
    ''')
    assert output[0]['label'] == 'Roster full'
    assert output[0]['detail'] == '2 still need to confirm'
    assert output[1]['label'] == 'Roster confirmed'
    assert output[2]['label'] == '3 open spots'
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
