"""Execute the request boundary and visible plan review, including cancellation."""
import json,subprocess
from tests.test_schedule_conflict_frontend import API
from tests.test_private_session_links_frontend import section


def run_case(case):
    code='''
      const state={token:'player-one',me:{id:1},networkFailureCount:0};
      let authSessionEpoch=0,authTokenRevision=0;
      const document={activeElement:{isConnected:true}},navigator={onLine:true};
      const safePositiveId=Number,authenticatedTokenAccountId=()=>1;
      const setConnectionState=()=>{},humanError=code=>code;
      const PUBLIC_CREDENTIAL_AUTH_PATHS=new Set(),localStorage={setItem(){}};
      const requests=[],reviews=[];let replies=[],onReview=()=>true;
      const confirmGamePlanReview=async game=>{reviews.push(game);return onReview(game)};
      const fetch=async(url,options)=>{
        requests.push({url,method:options.method,body:JSON.parse(options.body||'{}')});
        const data=replies.shift()||{is_joined:true};
        return {status:data.error?409:200,ok:!data.error,headers:{get:()=>''},json:async()=>data};
      };
      const changed=(price,token)=>({error:'game_plan_review_required',game:{id:7,cost_cents:price,plan_token:token.repeat(64)}});
    '''+API+'\n(async()=>{'+case+'})().catch(e=>{console.error(e);process.exitCode=1});'
    result=subprocess.run(['node','-e',code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def test_each_changed_plan_requires_review_and_replay_preserves_the_original_offer_action():
    r=run_case('''
      replies=[changed(1200,'a'),changed(1500,'b'),{is_joined:true}];
      const result=await api('/games/7/waitlist/respond',{method:'POST',body:JSON.stringify({accept:true,schedule_conflict_ack:'same-schedule'})});
      console.log(JSON.stringify({requests,reviews,result}));
    ''')
    assert [v['cost_cents'] for v in r['reviews']]==[1200,1500]
    assert len(r['requests'])==3
    assert r['requests'][1]['body']=={'accept':True,'schedule_conflict_ack':'same-schedule','expected_plan_token':'a'*64}
    assert r['requests'][2]['body']['expected_plan_token']=='b'*64
    assert r['result']['is_joined']


def test_cancel_closed_trigger_and_changed_account_never_retry_the_join():
    for behavior,code in [('return false','game_plan_review_cancelled'),
                           ('document.activeElement.isConnected=false;return true','game_plan_review_cancelled'),
                           ("authSessionEpoch++;state.token='player-two';return true",'stale_session')]:
        r=run_case('''
          replies=[changed(1200,'a')];onReview=()=>{'''+behavior+'''};
          let error;try{await api('/games/7/join',{method:'POST',body:'{}'})}catch(e){error=e.code}
          console.log(JSON.stringify({requests,error}));
        ''')
        assert len(r['requests'])==1 and r['error']==code


def test_repeated_changes_have_a_bounded_review_loop():
    r=run_case('''
      replies=[changed(1200,'a'),changed(1300,'b'),changed(1400,'c'),changed(1500,'d')];
      let error;try{await api('/games/7/join',{method:'POST',body:'{}'})}catch(e){error=e.code}
      console.log(JSON.stringify({requests,reviews,error}));
    ''')
    assert len(r['requests'])==4 and len(r['reviews'])==3 and r['error']=='game_plan_changed'


def test_review_displays_current_price_court_time_and_named_roster_safely():
    code='''
      const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
      const modalHead=s=>`<header>${s}</header>`,uiIcon=()=>'',fmtDateTime=s=>s,fmtTimeShort=s=>s;
    '''+section('function sessionPlayStyleLabel(', 'function sessionReturnToolsHtml(')+section('function gamePlanReviewHtml(', 'function confirmGamePlanReview(')+'''
      const game={title:'<img onerror=bad>',court:{name:'Cedar Park'},scheduled_at:'Tomorrow 6 PM',
        cost_cents:1200,court_access:'booking_needed',play_style:'mixed',max_players:6,players:[{display_name:'Alex'},{display_name:'Sam'}]};
      console.log(JSON.stringify({paid:gamePlanReviewHtml(game),unknown:gamePlanReviewHtml({...game,cost_cents:null}),free:gamePlanReviewHtml({...game,cost_cents:0})}));
    '''
    result=subprocess.run(['node','-e',code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    r=json.loads(result.stdout)
    for text in ['Cedar Park','Tomorrow 6 PM','Court booking still needed','Alex · Sam','2 signed up · 6 places','Join · $12.00 per player','&lt;img']:
        assert text in r['paid']
    assert '<img' not in r['paid']
    assert 'Cost not listed' in r['unknown'] and 'Join free session' not in r['unknown']
    assert 'Join free session' in r['free']
