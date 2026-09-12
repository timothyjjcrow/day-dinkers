"""Execute saved-plan and invitation rendering with practical session facts."""
import re
from tests.test_private_session_links_frontend import section, run_js


LABELS = section('function sessionPlayStyleLabel(', 'function sessionVisitFactsHtml(')


def test_custom_capacity_keeps_three_and_five_without_implying_match_format():
    result = run_js('const CASUAL_GAME_MAX_PLAYERS=100;\n'
        + section('function gameCapacityChoicesHtml(', 'function openGameFlow(')
        + "console.log(JSON.stringify([2,3,4,5,100].map(n=>gameCapacityChoicesHtml('ng',n))));")
    for capacity, html in zip([2, 3, 4, 5, 100], result):
        checked = re.findall(r'value="([^"]+)" checked', html)
        assert checked == [str(capacity) if capacity in [2, 4] else 'open']
        assert 'Maximum players' in html and 'Singles' not in html and 'Doubles' not in html
        if capacity not in [2, 4]:
            assert f'value="{capacity}"' in html


def test_saved_draft_and_frozen_retry_keep_independent_choices():
    result = run_js('''const CASUAL_GAME_MAX_PLAYERS=100,GAME_DRAFT_VERSION=1,GAME_DRAFT_TTL=86400000;
        const normalizedGameLevel=()=>null,sanitizePlannerInvitee=x=>x;
        ''' + section('function sanitizeGameCreatePayload(', 'function availableStorage(')
        + section('function safeGameDraftRecord(', 'function readGameDrafts(') + '''
        const payload={court_id:1,scheduled_at:'2027-01-01T18:00:00Z',max_players:5,
          game_type:'casual',client_attempt_id:'planner-metadata-001',play_style:'mixed',
          court_access:'booking_needed',cost_cents:0};
        const draft=safeGameDraftRecord({v:1,updatedAt:Date.now(),status:'submitting',
          clientAttemptId:payload.client_attempt_id,maxPlayers:5,playStyle:'mixed',
          courtAccess:'booking_needed',costCents:0,submittedPayload:payload});
        console.log(JSON.stringify({draft,retry:sanitizeGameCreatePayload(draft.submittedPayload),
          legacy:sanitizeGameCreatePayload({...payload,play_style:undefined,court_access:undefined})}));
        ''')
    draft = result['draft']
    assert (draft['maxPlayers'], draft['playStyle'], draft['courtAccess'], draft['costCents']) == (5, 'mixed', 'booking_needed', 0)
    assert result['retry'] == draft['submittedPayload']
    assert result['legacy']['play_style'] is None and result['legacy']['court_access'] is None


def test_private_invitation_discloses_cost_access_and_style_without_roster():
    result = run_js('''const esc=x=>String(x).replaceAll('<','&lt;').replaceAll('>','&gt;');
        const fmtDateTime=()=> 'Sunday',gameActivityLabel=()=> 'Casual session';
        ''' + LABELS + section('function gameInvitationPreviewHtml(', 'function invitationErrorMessage(') + '''
        const base={title:'<script>plan</script>',cost_cents:1250,court_access:'booking_needed',
          play_style:'mixed',court_number:'Court <4>',joined_count:1,spots_left:4};
        console.log(JSON.stringify({paid:gameInvitationPreviewHtml(base),unknown:gameInvitationPreviewHtml({}),
          reserved:gameInvitationPreviewHtml({...base,court_access:'host_reserved',court_count:2})}));
        ''')
    assert '$12.50 per player' in result['paid']
    assert 'Court booking still needed' in result['paid'] and 'Mixed play' in result['paid']
    assert 'Court &lt;4&gt;' in result['paid'] and '<script>' not in result['paid']
    assert 'Cost not listed' in result['unknown'] and 'Court booking not listed' in result['unknown']
    assert 'Host says 2 courts are reserved' in result['reserved']


def test_downloaded_calendar_keeps_price_style_access_and_escapes_details():
    result = run_js("const location={origin:'https://third-shot.example'};\n" + LABELS
        + section('function gameToIcs(', 'function downloadIcs(') + '''
        const base={id:1,scheduled_at:'2027-01-01T18:00:00Z',duration_minutes:60,
          title:'Friday, practice',description:'Meet; then play',players:[{}],max_players:5,
          play_style:'mixed',court_access:'booking_needed',cost_cents:1250};
        console.log(JSON.stringify({paid:gameToIcs(base),free:gameToIcs({...base,cost_cents:0}),
          unknown:gameToIcs({...base,cost_cents:null})}));
        ''')
    assert 'DTEND:20270101T190000Z' in result['paid']
    assert 'SUMMARY:Friday\\, practice' in result['paid']
    assert 'Meet\\; then play' in result['paid']
    for text in ['Mixed play', 'Court booking still needed', '$12.50 per player']:
        assert text in result['paid']
    assert 'Free session' in result['free'] and 'Cost not listed' in result['unknown']
