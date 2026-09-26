"""Maybe RSVPs on the game page, Play list, cards and invite launcher."""
from pathlib import Path

from tests.test_private_session_links_frontend import APP, run_js, section

STYLES = (Path(__file__).resolve().parents[1] / 'public/styles-v15.css').read_text()


def test_roster_line_mentions_maybes_only_when_there_are_some():
    result = run_js(section('function gameRosterStatus(', 'function gameRosterStatusHtml(') + '''
      const base={status:'upcoming',max_players:4,players:[{}, {}, {}],is_creator:true,is_joined:true};
      console.log(JSON.stringify([
        gameRosterStatus({...base,spots_left:1,rsvp_counts:{maybe:2}}),
        gameRosterStatus({...base,spots_left:1,rsvp_counts:{maybe:0}}),
        gameRosterStatus({...base,players:[{}, {}, {}, {}],spots_left:0,rsvp_counts:{maybe:1}}),
        gameRosterStatus({...base,spots_left:1,rsvp_counts:{confirmed:1,needs_confirmation:1,reserved:1,maybe:2}}),
        gameRosterStatus({...base,is_creator:false,attendance_confirmation_due:true,rsvp_counts:{maybe:2}}),
      ]));
    ''')
    assert result[0] == {'tone': 'forming', 'label': '3 joined', 'detail': '2 maybe · 1 spot left'}
    assert result[1] == {'tone': 'forming', 'label': '3 joined', 'detail': '1 spot left'}
    assert result[2] == {'tone': 'ready', 'label': '4 joined', 'detail': '1 maybe · Full'}
    assert result[3]['detail'] == '1 to confirm · 1 reserved · 2 maybe · 1 spots left'
    assert result[4]['label'] == 'Confirm your spot' and 'maybe' not in result[4]['detail']


def test_invite_launcher_nudges_with_maybes_and_keeps_its_copy_otherwise():
    result = run_js('const uiIcon=()=>"";\n' + section('function rosterBoostLauncherHtml(', 'function openRosterBoostSheet(') + '''
      console.log(JSON.stringify([
        rosterBoostLauncherHtml({spots_left:1,rsvp_counts:{maybe:2}}),
        rosterBoostLauncherHtml({spots_left:2,rsvp_counts:{maybe:0}}),
      ]));
    ''')
    assert '<span>2 maybe · 1 open spot</span>' in result[0]
    assert 'choose how to reach players' not in result[0]
    assert '<span>2 open spots · choose how to reach players</span>' in result[1]


def test_play_list_tags_maybe_and_keeps_it_out_of_to_do():
    result = run_js('const playGameDecision=()=>null;\nDate.now=()=>Date.parse("2030-01-01T12:00:00Z");\n'
        + section('function playPlanStatus(', '// Decorative calendar tile') + '''
      const base={status:'upcoming',scheduled_at:'2030-01-02T18:00:00Z',is_joined:false};
      console.log(JSON.stringify([
        playPlanStatus({...base,my_invite_status:'maybe'}),
        playPlanStatus({...base,my_invite_status:'pending'}),
        playPlanStatus({...base,my_invite_status:'maybe',scheduled_at:'2030-01-01T10:00:00Z'}).label,
      ]));
    ''')
    assert result[0] == {'label': 'Maybe', 'tone': ''}
    assert result[1] == {'label': 'Reply to invite', 'tone': 'warn'}
    assert result[2] == 'Started'
    todo = section('const invitations = mine.items.filter(', 'const waiting = mine.items.filter(')
    assert "game.my_invite_status === 'pending'" in todo


def test_invitee_sees_accept_then_maybe_and_cant_make_it():
    detail = section('function gameScreenHtml(', 'async function openGameScreen(')
    assert '${uiIcon(\'check\')} Accept invitation' in detail
    assert '${uiIcon(\'check\')} I’m in' in detail
    assert ('<div class="session-invite-replies"><button class="btn btn-secondary" id="gs-maybe-invite">Maybe</button>'
            '<button class="btn btn-secondary" id="gs-decline-invite">Can’t make it</button></div>') in detail
    assert "game.my_invite_status === 'pending' && !isChallenge" in detail
    assert "subline = game.my_invite_status === 'maybe' ? 'You said maybe'" in detail
    assert '${playersHtml}${sessionMaybeGroupHtml(game)}' in detail
    # A full game still lets a Maybe invitee say they can't make it.
    assert "if (game.my_invite_status && !game.waitlist_position && !game.waitlist_offer)" in detail


def test_roster_lists_maybes_without_rsvp_state_or_remove():
    result = run_js('''
      const esc=value=>String(value).replace(/</g,'&lt;'),avatarHtml=person=>`<i>${person.user_id}</i>`;
    ''' + section('function sessionMaybeGroupHtml(', 'function sessionConfirmationHtml(') + '''
      const people=[{user_id:4,display_name:'Ana <b>'},{user_id:5,display_name:'Ben'}];
      console.log(JSON.stringify([
        sessionMaybeGroupHtml({status:'upcoming',maybe_people:people}),
        sessionMaybeGroupHtml({status:'upcoming',maybe_people:[]}),
        sessionMaybeGroupHtml({status:'completed',maybe_people:people}),
        sessionMaybeGroupHtml({status:'upcoming'}),
      ]));
    ''')
    html = result[0]
    assert '<div class="session-roster-group" data-rsvp-state="maybe"><h5>Maybe <span>2</span></h5>' in html
    assert 'data-view-user="4"' in html and 'Ana &lt;b>' in html and 'Ana <b>' not in html
    assert 'data-remove-player' not in html and 'rsvp_status' not in html
    assert result[1:] == ['', '', '']


def test_maybe_saves_immediately_with_undo_and_refreshes_the_page():
    handler = section("box.querySelector('#gs-maybe-invite')?.addEventListener",
                      "box.querySelectorAll('#gs-score, #gs-wrap-session')")
    result = run_js('''
      let listener,mode='ok';
      const calls=[],renders=[],toasts=[],errors=[],resets=[],focus=[];let refreshes=0,plays=0;
      const box={querySelector:()=>({addEventListener:(_event,callback)=>listener=callback}),querySelectorAll:()=>[]};
      const modal={isConnected:true},state={tab:'play',playGamesCache:{}},gameId=7;
      const beginButtonAction=button=>button ? ()=>resets.push(true) : null;
      const api=async(url,options)=>{calls.push([url,options.method]);if(mode==='error')throw new Error('Try again');
        return {id:7,my_invite_status:options.method==='POST'?'maybe':'pending'};};
      const render=game=>renders.push(game.my_invite_status),refreshMe=()=>refreshes++,renderPlay=()=>plays++;
      const focusGameControl=(_modal,_box,selector)=>focus.push(selector);
      const toast=(message,options={})=>toasts.push({message,options}),showInlineActionError=(_box,message)=>errors.push(message);
    ''' + handler + '''
      (async()=>{
        await listener({currentTarget:{id:'maybe'}});
        const first=toasts[0];
        await first.options.action.onClick();
        mode='error';await listener({currentTarget:{id:'maybe'}});
        console.log(JSON.stringify({calls,renders,refreshes,plays,focus,errors,resets,
          message:first.message,label:first.options.action.label,cache:state.playGamesCache}));
      })();
    ''')
    assert result['calls'] == [['/games/7/invites/maybe', 'POST'], ['/games/7/invites/maybe', 'DELETE'],
                               ['/games/7/invites/maybe', 'POST']]
    assert result['renders'] == ['maybe', 'pending']
    assert result['message'] == 'Marked as maybe' and result['label'] == 'Undo'
    assert result['refreshes'] == 2 and result['plays'] == 2
    assert result['focus'] == ['#gs-join']
    assert result['errors'] == ['Try again'] and result['resets'] == [True]
    assert result['cache'] is None


def test_maybe_handler_stays_outside_pinned_decline_ranges():
    decline = section("box.querySelector('#gs-decline-invite')?.addEventListener",
                      "box.querySelector('#gs-complete-no-score')")
    assert '#gs-maybe-invite' not in decline
    assert APP.index("box.querySelector('#gs-complete-no-score')") < APP.index("box.querySelector('#gs-maybe-invite')")


def test_refresh_icon_and_styles_follow_the_shared_system():
    fingerprint = section('function gameFingerprint(', 'function captureGameViewState(')
    assert '(game.maybe_people || []).map((person) => person.user_id)' in fingerprint
    assert "'invite_declined', 'invite_maybe'].includes(kind)) return 'pickleball'" in APP
    play_section = STYLES[STYLES.index('/* ---------- r83 · Play, planner & game page'):
                          STYLES.index('/* ---------- r83 · Friends, chat & Me')]
    block = play_section[play_section.index('/* r85 · Maybe RSVPs */'):]
    block = block[:block.index('/* Rankings and Events')]
    assert '.session-invite-replies' in block
    assert '.session-roster-group[data-rsvp-state="maybe"]' in block
    assert 'border-radius: var(--radius-pill)' in block
    assert '#' not in block.replace('/* r85', '')
