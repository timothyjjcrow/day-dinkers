"""Execute per-person roster grouping and the prominent confirmation action."""
from tests.test_private_session_links_frontend import section, run_js

HELPERS=section('function sessionRsvpStatus(', 'function sessionVisitFactsHtml(')


def test_confirmation_states_are_per_person_and_large_groups_expand():
    result=run_js(HELPERS+'''
      const people=[...Array.from({length:9},(_,i)=>({user_id:i,rsvp_status:'confirmed'})),
        {user_id:10,rsvp_status:'needs_confirmation'},{user_id:11,rsvp_status:'reserved'}];
      console.log(JSON.stringify({html:sessionRosterGroupsHtml({players:people},p=>`<p>Player ${p.user_id}</p>`),
        legacy:[{attending:true},{attending:false,attendance_confirmation_requested_at:'2027-01-01'},{}].map(sessionRsvpStatus)}));
    ''')
    html=result['html']
    assert result['legacy']==['confirmed','needs_confirmation','reserved']
    assert 'Confirmed <span>9</span>' in html and 'Confirm again <span>1</span>' in html
    assert 'Place reserved <span>1</span>' in html and 'Show 3 more' in html
    assert html.index('Player 5') < html.index('<details') < html.index('Player 6')
    assert html.count('Player ')==11


def test_only_an_unconfirmed_joined_player_sees_confirmation_before_long_rosters():
    result=run_js(HELPERS+'''
      const base={is_joined:true,status:'upcoming',scheduled_at:'2099-01-01T18:00:00Z',
        attendance_confirmation_due:true,commitment_confirmation_due:true};
      console.log(JSON.stringify({changed:sessionConfirmationHtml(base),reminder:sessionConfirmationHtml({...base,commitment_confirmation_due:false}),
        absent:[{is_creator:true},{is_joined:false},{attendance_confirmation_due:false},{is_instant:true},{status:'cancelled'},{scheduled_at:'2000-01-01'}]
          .map(extra=>sessionConfirmationHtml({...base,...extra}))}));
    ''')
    assert 'The plan changed' in result['changed'] and 'Confirm this plan' in result['changed']
    assert 'Still coming?' in result['reminder'] and 'Yes, I’m coming' in result['reminder']
    assert result['absent']==['']*6
    detail=section('function gameScreenHtml(', 'async function openGameScreen(')
    assert detail.index('${sessionVisitFactsHtml(game)}') < detail.index('${sessionConfirmationHtml(game)}') < detail.index('class="session-roster"')


def test_host_sees_pending_counts_without_the_host_needing_to_confirm():
    result=run_js(section('function gameRosterStatus(', 'function gameRosterStatusHtml(')+'''
      console.log(JSON.stringify(gameRosterStatus({status:'upcoming',players:[{}, {}, {}],
        max_players:3,is_creator:true,is_joined:true,spots_left:0,
        attendance_confirmation_due:false,rsvp_counts:{confirmed:1,needs_confirmation:1,reserved:1}})));
    ''')
    assert result['label']=='1 confirmed'
    assert '1 to confirm' in result['detail'] and '1 reserved' in result['detail']


def test_refresh_keeps_the_same_named_roster_expanded_when_groups_change():
    source=section('function captureGameViewState(', 'function injectScoreConflictBanner(')
    result=run_js("const document={activeElement:null};\n"+source+'''
      const old=[{id:'session-roster-more-confirmed',open:true},{id:'',open:true}];
      const makeBox=rows=>({scrollTop:120,querySelectorAll:q=>q==='details[id]'?rows.filter(r=>r.id):rows});
      const snapshot=captureGameViewState(makeBox(old));
      const rows=[{id:'session-roster-more-needs_confirmation',open:true},
        {id:'session-roster-more-confirmed',open:false},{id:'',open:false}];
      const box=makeBox(rows);box.scrollTop=0;restoreGameViewState(box,snapshot);
      console.log(JSON.stringify({rows,scrollTop:box.scrollTop}));
    ''')
    assert [row['open'] for row in result['rows']]==[False,True,True]
    assert result['scrollTop']==120
