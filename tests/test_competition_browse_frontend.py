from test_league_round_operations_frontend import APP, section, run


def test_filters_generate_server_query_and_next_match_uses_named_direct_route():
    run("const assert=require('node:assert/strict'),esc=String,uiIcon=()=>'',fmtDateTime=String;"+section('  let competitionBrowseFilters', '  async function renderTournaments')+'''
      const query=competitionBrowseQuery({format:'doubles',rating:'3.5',when:'week'});
      assert.match(query,/signup=1/);assert.match(query,/event_type=doubles/);assert.match(query,/self_rating=3.5/);assert.match(query,/starts_before=/);
      const html=competitionHubNextHtml('league',{id:3,name:'Local league',personal_match:{id:9,round:2,opponent:'Sam Rivera',state:'unreported',schedule_status:'waiting_reply',can_respond_schedule:true}});
      assert.match(html,/vs Sam Rivera/);assert.match(html,/Choose a time/);assert.match(html,/data-match-id="9"/);
    ''')


def test_hub_leads_with_next_match_before_organizer_tools_and_filters_both_feeds():
    run("const assert=require('node:assert/strict');(async()=>{const calls=[],state={playSeg:'brackets'},esc=String,uiIcon=()=>'',fmtDateTime=String,committedAreaLatLng=()=>({lat:33,lng:-117}),tournamentCardHtml=()=>'<b>Event</b>',leagueCardHtml=()=>'',makePressable=()=>{},renderError=(el,msg)=>{throw Error(msg)},openCompetitionCreateSheet=()=>{};const mine={items:[{id:1,status:'active',starts_at:'2026-09-10',personal_match:{id:2,round:1,opponent:'Sam Rivera',state:'unreported',starts_at:'2026-09-10'}}]};const api=async(path)=>{calls.push(path);return path==='/competitions/next-matches'?{items:[{kind:'tournament',item:mine.items[0]}]}:path==='/tournaments?mine=1'?mine:{items:[]}};const el={innerHTML:'',querySelector:()=>null,querySelectorAll:()=>[]};"+section('  let competitionBrowseFilters','  const COMPETITION_RESULT')+'''
      await renderTournaments(el);assert.ok(el.innerHTML.indexOf('vs Sam Rivera')<el.innerHTML.indexOf('Organize an event'));
      assert.ok(el.innerHTML.indexOf('Your next match')<el.innerHTML.indexOf('Open signups'));
      assert.ok(calls.some(path=>path.startsWith('/tournaments?lat=')&&path.includes('signup=1')));
      assert.ok(calls.some(path=>path.startsWith('/leagues?lat=')&&path.includes('signup=1')));
    })().catch(e=>{console.error(e);process.exit(1)});''')


def test_expired_offer_shows_recovery_with_or_without_a_queue():
    run("const assert=require('node:assert/strict'),esc=String,fmtDateTime=String;"+section('  function tournamentWaitlistHtml','  function tournamentPersonalMatchHtml')+'''
      const event={status:'registration',my_waitlist:{status:'expired'},registration_spots_left:1};
      assert.match(tournamentWaitlistHtml(event),/Your offer expired/);
      assert.match(tournamentWaitlistHtml(event),/signup below/);
      const queued=tournamentWaitlistHtml({...event,registration_spots_left:0,waitlist_count:2});
      assert.match(queued,/Rejoin waitlist/);assert.match(queued,/back of the queue/);
    ''')


def test_unusual_league_score_cancellation_never_posts_and_acceptance_retries_exact_scores():
    run("const assert=require('node:assert/strict');(async()=>{let accepted=false,calls=[];const api=async(path,options)=>{const body=JSON.parse(options.body);calls.push(body);if(!body.accept_nonstandard_score)throw {code:'nonstandard_pickleball_score',data:{can_confirm:true}};return {id:1}};const isConfirmableNonstandardScore=e=>e.code==='nonstandard_pickleball_score';const confirmNonstandardScore=async()=>accepted;"+section('  async function leagueScoreRequest','  function openLeagueClosedReviewSheet')+'''
      const body={score1:5,score2:0,result_version:3};
      assert.equal(await leagueScoreRequest('/league',body),null);assert.equal(calls.length,1);
      accepted=true;const saved=await leagueScoreRequest('/league',body);assert.equal(saved.id,1);
      assert.deepEqual(calls[2],{...body,accept_nonstandard_score:true});
    })().catch(e=>{console.error(e);process.exit(1)});''')


def test_expired_partner_recovery_changes_action_only_after_deadline_reopens():
    run("const assert=require('node:assert/strict');"+section('  function tournamentPartnerUpdateHtml','  function tournamentOperationsHtml')+'''
      const event={my_partner_updates:[{entry_id:7,status:'expired',next_step:'organizer_review'}]};
      const expired=tournamentPartnerUpdateHtml(event);
      assert.match(expired,/invitation expired/);assert.match(expired,/organizer must extend/);assert.ok(!expired.includes('data-partner-offer'));
      const reopened=tournamentPartnerUpdateHtml({...event,my_partner_updates:[{entry_id:7,status:'expired',next_step:'offer_partner'}]});
      assert.match(reopened,/data-partner-offer="7"/);assert.match(reopened,/new partner offer/);
      assert.equal(tournamentPartnerUpdateHtml({...event,my_partner_action:{decision_for_me:true}}),'');
    ''')
