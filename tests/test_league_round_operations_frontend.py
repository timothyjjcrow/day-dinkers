"""Execute user-facing league review and period-selection behavior."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT/'public/app-v15.js').read_text()


def section(start, end):
    index = APP.index(start)
    return APP[index:APP.index(end, index)]


def run(code):
    subprocess.run(['node', '-e', code], cwd=ROOT, capture_output=True, text=True, check=True)


def test_round_and_season_views_never_mix_their_stats_or_movement():
    run("const assert=require('node:assert/strict'),esc=String,avatarHtml=()=>'',state={me:{id:1}};" + section('function leagueStandingsHtml', 'async function openLeagueRoundCloseSheet') + '''
    const user={id:1,display_name:'Alex'};
    const lg={status:'active',current_round:2,members:[{user,points:40,wins:12,losses:4}],
      round_standings:[{box:2,players:[{user,user_id:1,points:3,wins:1,losses:0,point_difference:6,place:1}]}],
      movement_preview:[{user_id:1,from_box:2,to_box:1}],standing_rule:'Current round only'};
    const current=leagueStandingsHtml(lg,'round',2),season=leagueStandingsHtml(lg,'season',2);
    assert.match(current,/1–0 this round/);assert.match(current,/3 pts/);assert.match(current,/promotion place/);
    assert.ok(!current.includes('40 pts'));assert.match(season,/12–4 this season/);
    assert.match(season,/40 pts/);assert.ok(!season.includes('promotion place'));
    ''')


def test_personal_opponent_and_action_show_without_competition_jargon():
    run("const assert=require('node:assert/strict'),esc=String,fmtDateTime=String,state={me:{id:1}},normalizeCompetitionResult=m=>({terminal:false,state:m.result_state});" + section('function leaguePersonalMatchHtml', 'function leagueStandingsHtml') + '''
      const lg={status:'active',joined:true,current_round:2,total_rounds:6,my_box:1,matches:[{id:8,round:2,player1:{id:1,display_name:'Alex'},player2:{id:2,display_name:'Sam Rivera'},result_state:'unreported'}]};
      const html=leaguePersonalMatchHtml(lg);assert.match(html,/vs Sam Rivera/);assert.match(html,/Find a time/);assert.match(html,/ROUND 2 OF 6/);assert.ok(!html.includes('box match'));
      assert.match(leaguePersonalMatchHtml({...lg,my_unavailable_round:2}),/I’m available again/);
    ''')


def test_round_close_uses_review_fingerprint_and_does_not_mutate_on_open():
    run("const assert=require('node:assert/strict');(async()=>{let callback,saved=null,closed=0;const calls=[];const esc=String,fmtDateTime=String,modalHead=String,toast=()=>{},showInlineActionError=()=>{},beginButtonAction=()=>()=>{},closeModal=()=>closed++;const modal={querySelector:()=>({addEventListener:(kind,fn)=>callback=fn})};const openModal=()=>modal;const api=async(path,options)=>{calls.push({path,options});return options?{id:5,status:'active'}:{round:2,unplayed_count:1,not_played_count:0,unresolved_count:0,movements:[],movement_notes:[],withdrawals:[],ends_season:false,next_round:3,preview_fingerprint:'reviewed'};};" + section('async function openLeagueRoundCloseSheet', 'async function openLeagueAvailabilitySheet') + '''
      await openLeagueRoundCloseSheet({id:5},false,data=>saved=data);
      assert.equal(calls.length,1);assert.equal(calls[0].options,undefined);assert.equal(saved,null);
      await callback({currentTarget:{}});assert.equal(calls.length,2);
      assert.deepEqual(JSON.parse(calls[1].options.body),{preview_fingerprint:'reviewed',finish:false});
      assert.equal(saved.id,5);assert.equal(closed,1);
    })().catch(e=>{console.error(e);process.exit(1)});''')


def test_availability_requires_specific_review_before_cancelling_matches():
    run("const assert=require('node:assert/strict');(async()=>{let callback,saved=null;const calls=[];const esc=String,fmtDateTime=String,modalHead=String,toast=()=>{},showInlineActionError=()=>{},beginButtonAction=()=>()=>{},closeModal=()=>{};const modal={querySelector:()=>({addEventListener:(kind,fn)=>callback=fn})};const openModal=()=>modal;const api=async(path,options)=>{const body=JSON.parse(options.body);calls.push(body);return body.preview?{round:2,affected_matches:[{opponent:'Sam',scheduled_at:'Tuesday'}],preview_fingerprint:'current-plan'}:{id:5};};" + section('async function openLeagueAvailabilitySheet', 'async function openLeagueScreen') + '''
      await openLeagueAvailabilitySheet({id:5},'unavailable',data=>saved=data);
      assert.deepEqual(calls,[{action:'unavailable',preview:true}]);assert.equal(saved,null);
      await callback({currentTarget:{}});
      assert.deepEqual(calls[1],{action:'unavailable',preview_fingerprint:'current-plan'});assert.equal(saved.id,5);
    })().catch(e=>{console.error(e);process.exit(1)});''')


def test_tournament_match_opens_after_selecting_its_return_round():
    start = APP.index('    const openMatch = (match) => {', APP.index('async function openTournamentScreen'))
    helper = APP[start:APP.index('\n\n    const render', start)]
    run("const assert=require('node:assert/strict');let selectedTournamentRound='all',tournamentMineOnly=true;const events=[],box={},t={id:2},content={querySelector:selector=>selector==='[data-competition-tab=\\\"td-matches\\\"]'?{click:()=>events.push('bracket')}:selector==='[data-result-match=\\\"9\\\"]'?{scrollIntoView:()=>events.push('scroll')}:null},render=()=>events.push('render'),openChildModal=(parent,fn)=>{events.push('child');return fn()},openCompetitionResultSheet=()=>1,setCompetitionMutation=()=>{},refreshGuard={invalidate(){}},currentOverlayEntry=()=>({el:box}),refresh=()=>{};" + helper + '''
      assert.equal(openMatch({id:9,round:3}),1);assert.equal(selectedTournamentRound,'3');assert.equal(tournamentMineOnly,false);assert.deepEqual(events,['render','bracket','scroll','child']);
    ''')


def test_ics_omits_void_history_and_withdrawn_deadlines_and_uses_versioned_extension():
    run("const assert=require('node:assert/strict'),location={origin:'https://third-shot.test'};" + section('function leagueToIcs(', 'function downloadLeagueIcs(') + '''
      const lg={id:2,name:'League',status:'active',current_round:2,round_version:4,round_deadline_at:'2026-10-01T12:00:00Z',matches:[{id:9,scheduled_at:'2026-09-30T12:00:00Z',result_state:'void',resolution_kind:'round_closed_unplayed'}]};
      const output=leagueToIcs(lg);assert.ok(!output.includes('league-match-9'));assert.match(output,/SEQUENCE:4/);assert.match(output,/20261001T120000Z/);
      assert.equal(leagueToIcs({...lg,my_withdrawn_at:'2026-09-20T12:00:00Z'}),'');
    ''')


def test_extension_previews_before_confirmation_and_keeps_the_reviewed_payload():
    run("const assert=require('node:assert/strict');(async()=>{let callback,saved=null,allow=false;const calls=[];const esc=String,fmtDateTime=String,modalHead=String,toast=()=>{},showInlineActionError=()=>{},beginButtonAction=()=>()=>{},closeModal=()=>{};const modal={querySelector:s=>s==='#lre-form'?{addEventListener:(k,fn)=>callback=fn}:s==='#lre-when'?{value:'2026-10-01T12:00'}:s==='#lre-reason'?{value:'Rain'}:{}};const openModal=()=>modal,openActionConfirmation=async()=>allow;const api=async(path,options)=>{const body=JSON.parse(options.body);calls.push(body);return body.preview?{round:1,preview_fingerprint:'rain-plan',deadline_at:body.deadline_at,reason:'Rain',retained_appointments:2,notification_count:5}:{id:4};};"+ section('function openLeagueExtensionSheet', 'async function openLeagueScreen')+'''
      openLeagueExtensionSheet({id:4,round_deadline_at:'2026-09-25T12:00:00Z'},fresh=>saved=fresh);
      await callback({preventDefault(){}});assert.equal(calls.length,1);assert.equal(saved,null);
      allow=true;await callback({preventDefault(){}});assert.equal(calls.length,3);assert.equal(calls[2].preview_fingerprint,'rain-plan');assert.equal(calls[2].reason,'Rain');assert.equal(saved.id,4);
    })().catch(e=>{console.error(e);process.exit(1)});''')


def test_tournament_waitlist_copy_distinguishes_queued_offer_and_partner_consent():
    run("const assert=require('node:assert/strict'),esc=String,fmtDateTime=String;"+section('function tournamentWaitlistHtml','function tournamentPersonalMatchHtml')+'''
      const t={status:'registration',entry_count:2,max_entries:2,registration_spots_left:0,waitlist_count:1,event_type:'singles'};
      assert.match(tournamentWaitlistHtml(t),/Join waitlist/);
      assert.match(tournamentWaitlistHtml({...t,my_waitlist:{status:'queued',position:2}}),/#2 on the waitlist/);
      const offered={...t,my_waitlist:{status:'offered',expires_at:'Tomorrow'},entry_fee_cents:0};
      assert.match(tournamentWaitlistHtml(offered),/Accept place & sign up/);assert.match(tournamentWaitlistHtml(offered),/Free entry/);
      assert.match(tournamentWaitlistHtml({...offered,event_type:'doubles'}),/partner must accept separately/);
      assert.equal(tournamentWaitlistHtml({...offered,my_entry_id:3}),'');
    ''')


def test_personal_match_reflects_pending_schedule_and_keeps_scoring_secondary():
    run("const assert=require('node:assert/strict'),esc=String,fmtDateTime=String,state={me:{id:1}},normalizeCompetitionResult=m=>({terminal:false,state:m.result_state});" + section('function leaguePersonalMatchHtml', 'function leagueStandingsHtml') + '''
      const match={id:8,round:1,player1:{id:1,display_name:'Alex'},player2:{id:2,display_name:'Sam'},result_state:'unreported',schedule_status:'waiting_reply'};
      const lg={status:'active',joined:true,current_round:1,my_box:1,matches:[match]};
      const waiting=leaguePersonalMatchHtml(lg);assert.match(waiting,/Waiting for your opponent to choose a time/);assert.match(waiting,/View proposed times/);assert.ok(!waiting.includes('Find a time'));
      assert.match(waiting,/<details><summary>Already played/);
      assert.match(leaguePersonalMatchHtml({...lg,matches:[{...match,can_respond_schedule:true}]}),/Choose a time/);
    ''')
