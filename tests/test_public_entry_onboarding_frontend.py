"""Execute public detail, minimal onboarding and radio interaction contracts."""
import json
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def section(start, end):
    offset = APP.index(start)
    return APP[offset:APP.index(end, offset)]


def run_js(script):
    return json.loads(subprocess.run(['node', '-e', script], text=True, capture_output=True, check=True).stdout)


def test_public_details_show_real_decisions_and_escape_untrusted_content():
    helpers = section('const COURT_OPEN_PLAY_PLAN_SOURCE', 'function openCourtWindowPlan') + section('function publicSessionFacts(', 'function renderSignedOutShareContext(')
    result = run_js("""
      const esc=x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
      const fmtDateTime=x=>x, gameLevelRangeLabel=x=>'2.0–3.0', courtDirectionsUrl=x=>'https://maps.google.com/?q=court', uiIcon=x=>'';
    """ + helpers + """
      const details={id:3,status:'upcoming',scheduled_at:'2099-01-01T18:00:00Z',game_type:'casual',max_players:6,player_count:4,spots_left:2,cost_cents:500,duration_minutes:90,court:{id:1,name:'<Court>',city:'Town'}};
      const game=publicShareDetailHtml({public:true,kind:'game',title:'<Session>',details});
      const full=publicShareDetailHtml({public:true,kind:'game',title:'Full',details:{...details,spots_left:0}});
      const ended=publicShareDetailHtml({public:true,kind:'game',title:'Old',details:{...details,status:'cancelled'}});
      const court=publicShareDetailHtml({public:true,kind:'court',details:{court:{id:1,name:'<Court>',num_courts:2,photo_url:'javascript:alert(1)'},sessions:[]}});
      console.log(JSON.stringify({game,full,ended,court,hidden:publicShareDetailHtml({public:false,details})}));
    """)
    assert '2 places available' in result['game'] and '4 going · 6 places' in result['game']
    assert '$5.00 per player' in result['game'] and 'Your local time · 90 minutes' in result['game']
    assert '&lt;Session&gt;' in result['game'] and '<Court>' not in result['game']
    assert 'Join the waitlist' in result['full'] and 'Join this session' not in result['full']
    assert 'Cancelled' in result['ended'] and 'Join this session' not in result['ended']
    assert 'javascript:' not in result['court'] and 'No public sessions listed yet' in result['court']
    assert result['hidden'] == ''


def test_first_visit_asks_only_area_and_keeps_profile_incomplete():
    helper = section('function runNewPlayerOnboarding(', 'function startPlayLiveRefresh(')
    result = run_js("""
      const state={me:{id:1,onboarding_complete:false}};
      const saved=new Map(), localStorage={getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)};
      let pendingNewPlayerOnboardingAccountId=1, areaOptions=[], browsed=0, resumes=0;
      const openHomeAreaOnboarding=o=>areaOptions.push(o), switchTab=()=>browsed++, resumePlayerInviteIntentAfterAuth=()=>resumes++;
      const openPlayerBasicsOnboarding=()=>{throw Error('No automatic profile chain');};
    """ + helper + """
      runNewPlayerOnboarding();
      areaOptions[0].onComplete();
      runNewPlayerOnboarding();
      state.me={id:2,home_court_id:4,onboarding_complete:false};
      runNewPlayerOnboarding();
      console.log(JSON.stringify({areaPrompts:areaOptions.length,browsed,resumes,pending:pendingNewPlayerOnboardingAccountId,completed:state.me.onboarding_complete,pauses:[saved.get('pp_setup_paused:1'),saved.get('pp_setup_paused:2')]}));
    """)
    assert result == {'areaPrompts': 1, 'browsed': 2, 'resumes': 2, 'pending': None,
                      'completed': False, 'pauses': ['1', '1']}


def test_radio_choices_have_one_tab_stop_and_keyboard_changes_real_selection():
    helper = section('function bindChoiceRadioKeys(', 'function openCrewNotificationSheet(')
    result = run_js("""
      let selected=null, focused=null, prevented=0;
      const choices=[0,1,2].map(index=>({index,tabIndex:99,handlers:{},getAttribute:()=>null,
        addEventListener(type,fn){(this.handlers[type]||=[]).push(fn);},
        click(){selected=this.index;for(const fn of this.handlers.click||[])fn();},focus(){focused=this.index;}}));
      const container={querySelectorAll:()=>choices};
    """ + helper + """
      bindChoiceRadioKeys(container,'[data-choice]');
      const initial=choices.map(x=>x.tabIndex);
      function press(index,key){choices[index].handlers.keydown[0]({key,preventDefault:()=>prevented++});}
      press(0,'ArrowLeft'); const wrapped=[selected,focused];
      press(2,'Home'); press(0,'End');
      console.log(JSON.stringify({initial,wrapped,selected,focused,prevented,tabs:choices.map(x=>x.tabIndex)}));
    """)
    assert result == {'initial': [0, -1, -1], 'wrapped': [2, 2], 'selected': 2,
                      'focused': 2, 'prevented': 3, 'tabs': [-1, -1, 0]}
    for selector in ('data-crew-notification', 'data-club-notification', 'data-self-rating', 'data-onboarding-rating'):
        assert f"bindChoiceRadioKeys(modal, '[{selector}]')" in APP


def test_after_join_prompt_requires_a_place_and_keeps_account_and_editor_context():
    helpers = section('function playerProfileSetupProgress(', 'function playerProfileSetupCardHtml(')
    result = run_js("""
      const SELF_RATING_CHOICES=[[2,'Beginner']], state={me:{id:1}};
      const saved=new Map(), localStorage={getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)};
      const prompts=[], editors=[], toast=(message,options)=>prompts.push({message,options});
      const openEditProfile=o=>{editors.push(o);return {};};
    """ + helpers + """
      const outsider=maybeOfferPlayerDetailsAfterJoin({players:[{user:{id:2}}]});
      const photo=maybeOfferPlayerDetailsAfterJoin({players:[{user:{id:1}}]});
      prompts[0].options.action.onClick();
      const level=maybeOfferPlayerDetailsAfterJoin({players:[{user_id:1}]});
      prompts[1].options.action.onClick();
      const repeat=maybeOfferPlayerDetailsAfterJoin({players:[{user_id:1}]});
      state.me={id:2};prompts[0].options.action.onClick();
      console.log(JSON.stringify({outsider,photo,level,repeat,editors,labels:prompts.map(p=>p.options.action.label)}));
    """)
    assert result == {'outsider': False, 'photo': True, 'level': True, 'repeat': False,
                      'editors': [{'section': 'photo'}, {'section': 'level'}],
                      'labels': ['Add photo', 'Add self-rating']}
