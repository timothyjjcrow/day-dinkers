"""Execute invitation routing and retry payloads; access must never become RSVP."""
import json
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def section(start, end):
    at = APP.index(start)
    return APP[at:APP.index(end, at)]


def run_js(source):
    result = subprocess.run(['node', '-e', source], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_invitation_routes_preserve_exact_token_without_aliasing_normal_game_routes():
    result = run_js("const OVERLAY_ROUTE_KINDS=new Set(['game','court','league','tournament']);\n"
        + section('const normalizeOverlayRoute =', 'function showAuthEntryForm(')
        + section('const overlayRouteHash =', 'const overlayUrl =') + '''
        const token='a'.repeat(64),route=normalizeOverlayRoute('#game/17/invite/'+token);
        console.log(JSON.stringify({route,hash:overlayRouteHash(route),
          normalIsSame:sameOverlayRoute(route,{kind:'game',id:17}),
          rotatedIsSame:sameOverlayRoute(route,{...route,inviteToken:'b'.repeat(64)}),
          roundTrip:sameOverlayRoute(route,normalizeOverlayRoute(overlayRouteHash(route))),
          rejected:['#game/0/invite/'+token,'#court/17/invite/'+token,
            '#game/9007199254740993/invite/'+token,'#game/17/invite/'+token+'x',
            '#game/17/match/3','#league/1/match/0'].map(normalizeOverlayRoute),
          match:normalizeOverlayRoute('#tournament/2/match/3')}));
        ''')
    assert result['route'] == {'kind': 'game', 'id': 17, 'inviteToken': 'a' * 64}
    assert result['hash'] == '#game/17/invite/' + 'a' * 64
    assert result['roundTrip'] and not result['normalIsSame'] and not result['rotatedIsSame']
    assert result['rejected'] == [None] * 6
    assert result['match'] == {'kind': 'tournament', 'id': 2, 'matchId': 3}


def test_interrupted_creation_keeps_explicit_link_access_and_legacy_request_identity():
    result = run_js('const CASUAL_GAME_MAX_PLAYERS=24;\n'
        + section('function sanitizeGameCreatePayload(', 'function availableStorage(') + '''
        const base={court_id:7,scheduled_at:'2027-01-01T18:00:00Z',game_type:'casual',
          max_players:4,visibility:'private',client_attempt_id:'private-link-attempt-001'};
        const legacy=sanitizeGameCreatePayload(base),enabled=sanitizeGameCreatePayload({...base,invite_link_enabled:true});
        console.log(JSON.stringify({legacy,enabled,retry:sanitizeGameCreatePayload(enabled),
          falseValue:sanitizeGameCreatePayload({...base,invite_link_enabled:false})}));
        ''')
    assert 'invite_link_enabled' not in result['legacy']
    assert result['falseValue'] == result['legacy']
    assert result['enabled']['invite_link_enabled'] is True
    assert result['retry'] == result['enabled']
    assert result['enabled']['invite_user_ids'] == []
    assert result['enabled']['visibility'] == 'private'


def test_signed_out_preview_never_redeems_and_discards_response_after_navigation():
    result = run_js('''
      const calls=[],events=[],showAuthEntryForm=()=>events.push('auth');
      const container={isConnected:true,innerHTML:'',classList:{add(){}},setAttribute(){},removeAttribute(){},
        querySelector:()=>({addEventListener:(kind,callback)=>events.push(kind)})};
      const gameInvitationPreviewHtml=plan=>'PLAN '+plan.id,esc=value=>value;
      const invitationErrorMessage=error=>error.message;
      let current=true,resolve;
      const api=(url,options)=>{calls.push({url,method:options.method});return new Promise(done=>resolve=done)};
    ''' + section('async function renderSignedOutGameInvitation(', 'async function openGameInvitation(') + '''
      (async()=>{
        const first=renderSignedOutGameInvitation(container,{id:7,inviteToken:'a'.repeat(64)},()=>current);
        resolve({id:7});await first;const visible=container.innerHTML;
        const second=renderSignedOutGameInvitation(container,{id:8,inviteToken:'b'.repeat(64)},()=>current);
        current=false;container.innerHTML='NEW DESTINATION';resolve({id:8});await second;
        console.log(JSON.stringify({calls,events,visible,after:container.innerHTML}));
      })();
    ''')
    assert all(call['method'] == 'POST' and call['url'].endswith('/invite-link/preview') for call in result['calls'])
    assert result['events'] == ['click']
    assert 'Continue to invitation' in result['visible'] and 'PLAN 7' in result['visible']
    assert result['after'] == 'NEW DESTINATION'


def test_auth_continuation_and_hash_changes_do_not_drop_or_alias_invitation_tokens():
    result = run_js('''
      const OVERLAY_ROUTE_KINDS=new Set(['game','court','league','tournament']);
      const location={hash:'',search:'',pathname:'/'},state={token:'signed-in'};
      const calls=[], history={replaceState:()=>calls.push('base')};
      const overlayHistoryState=()=>null,baseAppUrl=()=>'/';
      const openAccountActionDeepLink=()=>false;
      const openGameInvitation=route=>calls.push({open:route});
      const openGameScreen=id=>calls.push({game:id});
      let adoptOverlayEntry=null,suppressNativeHashRoute=null,current=null,handler;
      const currentOverlayEntry=()=>({route:current});
      const navigateOverlayRoute=route=>calls.push({navigate:route});
      const window={addEventListener:(event,callback)=>handler=callback};
    ''' + section('const normalizeOverlayRoute =', 'function showAuthEntryForm(')
        + section('const overlayRouteHash =', 'const overlayUrl =')
        + section('function openDeepLink()', "// A friend's invite link") + '''
      const token='a'.repeat(64),next='b'.repeat(64);
      location.hash='#game/17/invite/'+token;
      const restored=openDeepLink();
      adoptOverlayEntry={route:{kind:'game',id:17,inviteToken:token}};
      location.hash='#game/17';openDeepLink();
      current=adoptOverlayEntry.route;location.hash='#game/17/invite/'+next;handler();
      location.hash='#game/17/invite/'+token;handler();
      location.hash='#game/17';handler();
      console.log(JSON.stringify({restored,calls}));
    ''')
    assert result['restored'] is True
    assert result['calls'] == ['base', {'open': {'kind': 'game', 'id': 17, 'inviteToken': 'a' * 64}},
                               'base', {'game': 17},
                               {'navigate': {'kind': 'game', 'id': 17, 'inviteToken': 'b' * 64}},
                               {'navigate': {'kind': 'game', 'id': 17}}]
