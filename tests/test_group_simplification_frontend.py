"""The group overview prioritizes the next game and reflects personal RSVP state."""
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def section(start, end):
    return APP[APP.index(start):APP.index(end, APP.index(start))]


def run(script):
    source = section('  function crewSessionContentHtml(', '  async function openCrewScreen(')
    source += section('  function crewChatPlanCopy(', '  function syncCrewChatPlan(')
    subprocess.run(['node', '-e', '''
      const assert=require('node:assert/strict');
      const esc=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
      const uiIcon=()=>'';
      const fmtDateTime=value=>value;
    ''' + source + script], check=True, capture_output=True, text=True)


def test_group_session_shows_personal_status_and_respects_an_explicit_full_roster():
    run('''
      const game={id:9,scheduled_at:'Tomorrow at 10 AM',court:{name:'Sunset <Park>'},players:[{id:1}],max_players:4,spots_left:0,is_invited:true};
      const html=crewSessionContentHtml(game);
      assert.ok(html.includes('Tomorrow at 10 AM'));
      assert.ok(html.includes('Sunset &lt;Park>'));
      assert.ok(html.includes('1/4 in · Full'));
      assert.ok(!html.includes('spots left'));
      assert.ok(html.includes('You’re invited · View &amp; RSVP'));
      assert.ok(crewSessionContentHtml({...game,is_joined:true}).includes('You’re in · View session'));
      assert.ok(crewSessionContentHtml({...game,waitlist_position:2}).includes('Waitlist #2 · View session'));
      assert.ok(crewSessionContentHtml({...game,spots_left:null}).includes('3 spots left'));
    ''')


def test_only_the_next_session_is_exposed_before_the_optional_more_dates_disclosure():
    run('''
      const game=id=>({id,scheduled_at:'Day '+id,court:{name:'Park'},max_players:4,spots_left:3,is_joined:true});
      assert.equal(crewUpcomingGamesHtml([]),'');
      assert.ok(!crewUpcomingGamesHtml([game(1)]).includes('<details'));
      const html=crewUpcomingGamesHtml([game(1),game(2),game(3)]);
      const split=html.indexOf('<details');
      assert.ok(html.indexOf('data-open-crew-game="1"')<split);
      assert.ok(html.indexOf('data-open-crew-game="2"')>split);
      assert.ok(html.indexOf('data-open-crew-game="3"')>split);
      assert.ok(html.includes('More upcoming sessions <span>2</span>'));
      assert.ok(!html.includes('<details open'));
    ''')


def test_group_uses_one_planner_and_keeps_invitation_management_inside_players():
    screen = section('  async function openCrewScreen(', '  function openRenameCrewSheet(')
    assert 'crew-plan-weekly' not in APP
    assert 'crewWeeklyPlannerOptions' not in APP
    assert 'crew-weekly-start' not in APP
    assert screen.index('class="crew-upcoming-section"') < screen.index('class="crew-primary-actions')
    assert screen.index('id="crew-players"') < screen.index('id="crew-add-players"') < screen.index('id="crew-pending-management"')
    assert "${!canPlan || showInvitations ? 'open' : ''}" in screen
    assert 'onUpdated: (fresh)' in screen
    assert ".filter((item) => item.status === 'upcoming')" in screen
    detail = APP[APP.index('  async function openGameScreen('):]
    assert 'options.onUpdated?.(game)' in detail


def test_authoritative_rsvp_update_preserves_the_return_focus_target():
    start = APP.index('        onUpdated: (fresh) => {', APP.index('  async function openCrewScreen('))
    end = APP.index('      }));', start)
    callback = APP[start:end].strip().removeprefix('onUpdated: ').removesuffix(',')
    run('''
      let upcomingGames=[{id:9,status:'upcoming',is_invited:true}];
      const row={innerHTML:''};
      const section={innerHTML:'original',hidden:false,querySelector:()=>row};
      const canPlan=true;
      const button=()=>({innerHTML:'',classList:{toggle:()=>{}}});
      const plan=button(),chat=button();
      const modal={isConnected:true,querySelector:selector=>selector==='.crew-upcoming-section'?section:selector==='#crew-plan'?plan:chat};
      const update=''' + callback + ''';
      update({id:9,status:'upcoming',is_joined:true,scheduled_at:'Tomorrow',players:[{},{}],max_players:4,spots_left:2});
      assert.equal(section.innerHTML,'original');
      assert.ok(row.innerHTML.includes('You’re in · View session'));
      assert.ok(row.innerHTML.includes('2/4 in'));
      assert.equal(upcomingGames[0].is_joined,true);
      update({id:9,status:'cancelled'});
      assert.equal(section.hidden,true);
      assert.equal(upcomingGames.length,0);
      assert.ok(plan.innerHTML.includes('Plan with this group'));
      modal.isConnected=false;
      update({id:9,status:'upcoming'});
      assert.equal(section.hidden,true);
    ''')
