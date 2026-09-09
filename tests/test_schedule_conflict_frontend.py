"""Execute the shared request boundary: conflicts cannot silently create commitments."""
import json
from pathlib import Path
import subprocess

APP=(Path(__file__).resolve().parents[1]/'public/app-v15.js').read_text()
START=APP.index('async function api(')
API=APP[START:APP.index('// Password and MFA mutations',START)]


def run_case(case):
    source='''
      const state={token:'player-one',me:{id:1},networkFailureCount:0};
      let authSessionEpoch=0,authTokenRevision=0;
      const document={activeElement:{isConnected:true}},navigator={onLine:true};
      const safePositiveId=Number,authenticatedTokenAccountId=()=>1;
      const setConnectionState=()=>{},humanError=code=>code;
      const PUBLIC_CREDENTIAL_AUTH_PATHS=new Set(),localStorage={setItem(){}};
      const requests=[],reviews=[];
      let statuses=[],onReview=()=>true;
      const confirmScheduleConflict=async data=>{reviews.push(data.schedule_conflict_token);return onReview(data)};
      const fetch=async(url,options)=>{
        requests.push({url,method:options.method,body:JSON.parse(options.body||'{}'),headers:options.headers});
        const status=statuses.shift()||200,token='review-'+requests.length;
        return {status,ok:status===200,headers:{get:()=>''},json:async()=>status===409?
          {error:'schedule_conflict',schedule_conflict_token:token,conflicts:[{title:'Existing plan'}]}:{is_joined:true}};
      };
    '''+API+'\n(async()=>{'+case+'})().catch(e=>{console.error(e);process.exitCode=1});'
    completed=subprocess.run(['node','-e',source],capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr
    return json.loads(completed.stdout)


def test_acceptance_preserves_original_attempt_and_requires_fresh_review_for_changed_conflicts():
    result=run_case('''
      statuses=[409,409,200];
      const response=await api('/games',{method:'POST',headers:{'X-Context':'same'},
        body:JSON.stringify({court_id:7,client_attempt_id:'attempt-one',invite_user_ids:[2,3]})});
      console.log(JSON.stringify({requests,reviews,response}));
    ''')
    assert len(result['requests'])==3 and result['reviews']==['review-1','review-2']
    assert result['requests'][0]['body']=={'court_id':7,'client_attempt_id':'attempt-one','invite_user_ids':[2,3]}
    assert result['requests'][1]['body']['schedule_conflict_ack']=='review-1'
    assert result['requests'][2]['body']['schedule_conflict_ack']=='review-2'
    assert all(row['headers']['X-Context']=='same' and row['method']=='POST' for row in result['requests'])
    assert all(row['body']['client_attempt_id']=='attempt-one' for row in result['requests'])
    assert result['response']['is_joined'] is True


def test_cancel_or_closed_action_never_replays_the_mutation():
    for behavior,code in [("return false",'schedule_conflict_cancelled'),
                          ("document.activeElement.isConnected=false;return true",'request_cancelled'),
                          ("authSessionEpoch++;state.token='player-two';state.me={id:2};return true",'stale_session')]:
        result=run_case('''
          statuses=[409];onReview=()=>{'''+behavior+'''};
          let error;try{await api('/games/7/join',{method:'POST',body:'{}'})}catch(e){error=e.code}
          console.log(JSON.stringify({requests,reviews,error}));
        ''')
        assert len(result['requests'])==1 and len(result['reviews'])==1
        assert result['error']==code


def test_aborted_or_repeatedly_changing_reviews_stop_without_unbounded_replay():
    result=run_case('''
      statuses=[409,409,409,409,200];
      let error;try{await api('/games/7/join',{method:'POST',body:'{}'})}catch(e){error=e.code}
      console.log(JSON.stringify({requests,reviews,error}));
    ''')
    assert len(result['requests'])==4 and len(result['reviews'])==3
    assert result['error']=='schedule_changed'
    result=run_case('''
      statuses=[409];const controller=new AbortController();
      onReview=()=>{controller.abort();return true};
      let error;try{await api('/games/7/join',{method:'POST',body:'{}',signal:controller.signal})}catch(e){error=e.code}
      console.log(JSON.stringify({requests,error}));
    ''')
    assert len(result['requests'])==1 and result['error']=='request_cancelled'


def test_saved_overlap_cards_link_to_each_plan_and_escape_untrusted_titles():
    start=APP.index('  function playScheduleConflictsHtml(')
    renderer=APP[start:APP.index('  function playFeedPageControlHtml(',start)]
    source="""
      const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
      const fmtDateTime=s=>s,uiIcon=()=>'';
    """+renderer+"""
      const plans=[
        {title:'<img onerror=bad>',starts_at:'2026-09-10',action_url:'/#game/7'},
        {title:'League match',starts_at:'2026-09-10',action_url:'/#league/2/match/8'},
        {title:'Tournament',starts_at:'2026-09-10',action_url:'/#tournament/3/match/9'},
        {title:'Bad route',starts_at:'2026-09-10',action_url:'javascript:bad()'}];
      console.log(JSON.stringify({empty:playScheduleConflictsHtml([]),
        html:playScheduleConflictsHtml([{plans:plans.slice(0,2)},
          {plans:plans.slice(2),estimated:true},{plans:plans.slice(0,1)}])}));
    """
    completed=subprocess.run(['node','-e',source],capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr
    result=json.loads(completed.stdout)
    assert result['empty']==''
    html=result['html']
    assert 'data-open-game="7"' in html
    assert 'data-play-competition="league" data-competition-id="2" data-match-id="8"' in html
    assert 'data-play-competition="tournament" data-competition-id="3" data-match-id="9"' in html
    assert 'Includes an estimated time' in html and 'Show 1 more overlap' in html
    assert '&lt;img' in html and '<img' not in html and 'javascript:' not in html
