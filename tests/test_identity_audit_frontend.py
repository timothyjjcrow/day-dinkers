"""Focused profile saves must never overwrite other sections of an account."""
import json
import subprocess
from pathlib import Path


APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def test_focused_profile_patch_preserves_unrelated_information():
    start = APP.index('function profileChangesForSection(')
    helper = APP[start:APP.index('function openEditProfile(', start)]
    script = helper + '''
      const draft = { display_name: 'New Name', bio: 'Draft bio', skill_rating: 3.5,
        dupr_rating: 4, dupr_id: 'player-id', avatar_color: '#123456', avatar_data: null,
        availability: ['tue-eve'], home_court_id: 21 };
      console.log(JSON.stringify({photo: profileChangesForSection(draft, 'photo'),
        availability: profileChangesForSection(draft, 'availability'),
        court: profileChangesForSection(draft, 'court'),
        level: profileChangesForSection(draft, 'level'),
        about: profileChangesForSection(draft, 'about'), original: draft}));
    '''
    result = subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
    changes = json.loads(result.stdout)
    assert changes['photo'] == {'avatar_color': '#123456', 'avatar_data': None}
    assert changes['availability'] == {'availability': ['tue-eve']}
    assert changes['court'] == {'home_court_id': 21}
    assert changes['about'] == {'display_name': 'New Name', 'bio': 'Draft bio'}
    assert changes['level'] == {'skill_rating': 3.5, 'dupr_rating': 4, 'dupr_id': 'player-id'}
    assert changes['original']['display_name'] == 'New Name'
    assert changes['original']['home_court_id'] == 21
    assert "$('#profile-edit')?.addEventListener('click', openProfileEditorHub)" in APP
    assert 'aria-label="Your current player card"' in APP


def test_upcoming_plans_are_outside_history_and_shortcuts_open_focused_editors():
    profile = APP[APP.index('async function renderProfile('):APP.index('function profileChangesForSection(')]
    assert profile.index('id="pf-upcoming-more"') < profile.index('summary>Your progress &amp; history')
    assert "openEditProfile({ section: 'photo' })" in profile
    assert "openEditProfile({ section: 'availability' })" in profile
    assert 'See all ${ordered.length} plans' in profile
    assert 'id="pf-find-game"' in profile


def test_activity_and_history_filters_request_complete_server_scope():
    assert '/games/history?limit=30&filter=${encodeURIComponent(filter)}' in APP
    assert '/notifications?limit=20&filter=${encodeURIComponent(filter)}' in APP
    assert 'modal._onResume = () => loadActivityFilter(activityFilter, { preserveWindow: true })' in APP
    assert 'request !== historyRequest' in APP
    assert 'request !== activityRequest' in APP
    assert "notification.needs_action === false" in APP
    assert "notification.needs_action = false" in APP


def test_away_suppresses_usual_time_suggestions_including_in_flight_fetches():
    away = APP[APP.index('  function playerAwayUntil('):APP.index('  function playerAwayLabel(')]
    nudge = APP[APP.index('  async function maybeShowUsualTimeNudge('):APP.index('  function setupConnectivity(')]
    script = '''
      const assert = require('node:assert/strict');
      const future = new Date(Date.now()+86400000).toISOString();
      const state={me:{away_until:future,availability:['tue-eve']}};
      let fetched=0, resolveFetch;
      const sessionStorage={getItem:()=>null,setItem:()=>{}};
      const slotForNow=()=> 'tue-eve';
      const committedAreaLatLng=()=>({lat:33,lng:-117});
      const api=()=>{fetched++;return new Promise(resolve=>{resolveFetch=resolve;});};
      const document={createElement:()=>{throw new Error('Away player must not see a suggestion');}};
    ''' + away + nudge + '''
      (async()=>{
        assert.equal(playerAwayUntil({away_until:'bad'}),null);
        assert.equal(playerAwayUntil({away_until:'2020-01-01T00:00:00Z'}),null);
        await maybeShowUsualTimeNudge();
        assert.equal(fetched,0);
        state.me.away_until=null;
        const pending=maybeShowUsualTimeNudge();
        assert.equal(fetched,1);
        state.me.away_until=future;
        resolveFetch({items:[]});
        await pending;
      })().catch(error=>{console.error(error);process.exit(1);});
    '''
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
