"""Executable coverage for complete plans and stable interaction identities."""

import json
from pathlib import Path
import subprocess


APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def functions_between(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def run_js(source):
    result = subprocess.run(['node', '-e', source], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_schedule_keeps_later_dates_without_duplicating_the_featured_plan():
    source = (functions_between('function playGameDecision(', 'function gameRosterStatus(')
              + functions_between('function playScheduleHtml(', 'function playNearbyNowHtml('))
    html = run_js('''
      Date.now = () => Date.parse('2030-01-01T12:00:00Z');
      const esc = value => String(value), uiIcon = () => '';
      const instantRallyClosed = () => false;
      const upcomingDayLabel = value => value.slice(0, 10);
      const fmtDateTime = value => value, fmtTimeShort = value => value.slice(11, 16);
    ''' + source + '''
      const game = (id,day) => ({id, status:'upcoming', scheduled_at:`2030-01-${day}T18:00:00Z`,
        title:`Plan ${id}`,court:{name:'Park'},is_joined:true,max_players:4,game_type:'ranked'});
      console.log(JSON.stringify(playScheduleHtml([
        game(1,'02'),game(2,'09'),game(3,'21'), {...game(4,'22'),status:'cancelled'}
      ], new Set([1]))));
    ''')
    assert 'data-open-game="2"' in html
    assert 'data-open-game="3"' in html
    assert 'data-open-game="1"' not in html
    assert 'data-open-game="4"' not in html
    assert html.index('Plan 2') < html.index('Plan 3')
    assert 'Ranked doubles' in html
    assert 'Upcoming plans' in html


def test_focus_identity_follows_the_same_action_not_its_position_or_attribute_order():
    source = functions_between('function feedControlIdentity(', 'function captureFeedInteraction(')
    result = run_js(source + '''
      const node = entries => ({tagName:'BUTTON',attributes:entries.map(([name,value])=>({name,value}))});
      const first=feedControlIdentity(node([['data-id','1'],['data-action','join'],['class','active']]));
      const same=feedControlIdentity(node([['class','updated'],['data-action','join'],['data-id','1']]));
      const other=feedControlIdentity(node([['data-action','join'],['data-id','2']]));
      const summary=feedControlIdentity({tagName:'SUMMARY',closest:()=>({dataset:{viewStateKey:'play-now'}})});
      console.log(JSON.stringify({first,same,other,summary}));
    ''')
    assert result['first'] == result['same']
    assert result['first'] != result['other']
    assert result['summary'] == 'summary:play-now'


def test_create_choice_opens_the_correct_planner_without_immediate_side_effects():
    source = functions_between('function openCreatePlaySheet()', 'function rallyLauncherHtml()')
    result = run_js('''
      const clicks={}, calls=[];
      const modal={querySelectorAll:()=>['casual','ranked','competition'].map(kind=>({
        dataset:{createKind:kind},addEventListener:(_,fn)=>{clicks[kind]=fn;}
      }))};
      const modalHead=()=>'',uiIcon=()=>'',openModal=()=>modal;
      const transitionModal=(_,fn)=>fn();
      const openNewGameModal=opts=>calls.push(opts);
      const openCompetitionCreateSheet=()=>calls.push({competition:true});
    ''' + source + '''
      openCreatePlaySheet(); const before=calls.length;
      clicks.casual(); clicks.ranked(); clicks.competition();
      console.log(JSON.stringify({before,calls}));
    ''')
    assert result['before'] == 0
    assert result['calls'][0]['sessionMode'] is True
    assert result['calls'][0]['gameType'] == 'casual'
    assert result['calls'][1]['rankedMatchMode'] is True
    assert result['calls'][1]['gameType'] == 'ranked'
    assert result['calls'][2] == {'competition': True}


def test_planner_sharing_uses_the_created_game_not_an_unrelated_cached_game():
    handler = functions_between("let shareCreatedPlan = false;", 'refreshPlannerInviteChoices();')
    assert "requestSubmit()" in handler
    assert 'shareInviteLink' not in handler
    created = functions_between("const createdGame = await api('/games'", '} catch (err)')
    assert "openGameScreen(createdGame.id" in created
    assert "openRosterBoostSheet(createdGame, { initialChannel: 'share' })" in created
    assert 'maybeOfferPhoneNotifications' not in created


def test_competition_plans_show_opponent_context_and_true_pending_actions():
    source = functions_between('function playScheduleHtml(', 'function playNearbyNowHtml(')
    result = run_js('''
      Date.now=()=>Date.parse('2030-01-01T12:00:00Z');
      const esc=String,uiIcon=()=>'',instantRallyClosed=()=>false;
      const upcomingDayLabel=value=>value.slice(0,10),fmtDateTime=value=>value,fmtTimeShort=value=>value.slice(11,16);
    ''' + source + '''
      const scheduled={kind:'league_match',id:10,league_id:2,league_name:'Autumn Singles',
        opponent:{display_name:'Jordan'},scheduled_at:'2030-01-20T18:00:00Z',schedule_status:'scheduled',
        scheduled_court:{name:'Court Park'}};
      const proposal={...scheduled,id:11,scheduled_at:null,schedule_status:'waiting_reply',can_respond_schedule:true};
      const tournament={kind:'tournament_match',id:12,tournament_id:3,name:'Weekend Open',
        opponent_name:'Sam & Riley',starts_at:'2030-01-21T18:00:00Z',court_number:2};
      console.log(JSON.stringify({schedule:playScheduleHtml([],new Set(),[scheduled,proposal,tournament]),
        action:playCompetitionRowHtml(proposal),needsAction:playCompetitionNeedsAction(proposal)}));
    ''')
    assert 'vs Jordan' in result['schedule'] and 'Autumn Singles · League' in result['schedule']
    assert 'data-match-id="10"' in result['schedule'] and 'data-match-id="12"' in result['schedule']
    assert 'data-match-id="11"' not in result['schedule']
    assert 'Estimated' in result['schedule'] and 'Court 2' in result['schedule']
    assert result['needsAction'] is True
    assert 'Review times' in result['action'] and 'data-match-id="11"' in result['action']
    assert 'Invalid Date' not in result['action']


def test_refresh_restores_the_loaded_window_without_duplicating_overlapping_rows():
    source = functions_between('function mergePlayFeedPage(', 'function playNearbyNowHtml(')
    result=run_js('''
      const calls=[];
      const api=async url=>{calls.push(url);return {items:[{id:2,title:'Updated'},{id:3}],has_more:false,next_cursor:null}};
    '''+source+'''
      (async()=>{const feed=await restorePlayFeedWindow({items:[{id:1},{id:2,title:'Old'}],
        has_more:true,next_cursor:'second'},3,'/games?mine=1');
        console.log(JSON.stringify({feed,calls}));})();
    ''')
    assert [item['id'] for item in result['feed']['items']] == [1,2,3]
    assert result['feed']['items'][1]['title'] == 'Updated'
    assert result['feed']['has_more'] is False
    assert len(result['calls']) == 1 and 'cursor=second' in result['calls'][0]


def test_rankings_show_the_independent_position_and_distinct_recovery_actions():
    source = functions_between('function rankingViewerHtml(', 'async function renderPlay(')
    output = run_js('''
      const state={me:{id:66,rating:700}},esc=String,avatarHtml=()=>'';
      const rankRowHtml=(player,rank,options)=>`Rank ${rank}: ${options.name} (${player.rating})`;
    '''+source+'''
      const html=status=>rankingViewerHtml({items:[],viewer:{status}},rankRowHtml);
      console.log(JSON.stringify({
        ranked:rankingViewerHtml({items:[{id:1}],viewer:{status:'ranked',rank:66,player:state.me}},rankRowHtml),
        outside:html('outside_area'), month:html('no_results_this_month'),
        fresh:html('no_ranked_results'),missing:html('location_not_set'),
        oldServer:rankingViewerHtml({items:[{id:1}]},rankRowHtml)
      }));
    ''')
    assert 'Rank 66: You (700)' in output['ranked']
    assert 'data-rankings-reset="area"' in output['outside']
    assert 'data-rankings-reset="period"' in output['month']
    assert 'data-set-rankings-area' in output['missing']
    assert 'Complete a ranked match' in output['fresh']
    assert 'data-rankings-retry' in output['oldServer']
    assert 'Complete a ranked match' not in output['oldServer']
