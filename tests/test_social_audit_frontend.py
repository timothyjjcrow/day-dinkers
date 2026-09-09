"""Player-to-plan intent is usable without a friendship or inferred schedule."""
import json
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def part(start, end):
    at = APP.index(start)
    return APP[at:APP.index(end, at)]


def node_result(source):
    return json.loads(subprocess.run(['node', '-e', source], check=True, capture_output=True, text=True).stdout)


def test_invitation_keeps_player_and_court_without_making_up_shared_availability():
    helpers = part('function sanitizePlannerInvitee(', 'function sanitizeGameCreatePayload(')
    helpers += part('function playerInvitePlannerOptions(', 'function refreshConnectionViews(')
    result = node_result(helpers + """
      const state = {me:{id:1,availability:[],home_court_id:4,home_court_name:'Home court'}};
      const window = {CrewPlanner:{bestSlot:()=>null}};
      const player = {id:2,display_name:'Sam',availability:[],last_played_court:{id:7,name:'Shared court'}};
      const basic = playerInvitePlannerOptions(player);
      window.CrewPlanner.bestSlot = ()=>({coverage:1,scheduledAt:'2026-09-15T10:00:00Z'});
      const onePersonOnly = playerInvitePlannerOptions(player);
      window.CrewPlanner.bestSlot = ()=>({coverage:2,scheduledAt:'2026-09-15T10:00:00Z'});
      const shared = playerInvitePlannerOptions(player);
      console.log(JSON.stringify([basic,onePersonOnly,shared]));
    """)
    basic, one, shared = result
    assert basic['inviteUserIds'] == [2] and basic['invitees'][0]['display_name'] == 'Sam'
    assert basic['visibility'] == 'private' and basic['requireAllInvitees'] is True
    assert basic['court'] == {'id': 7, 'name': 'Shared court'}
    assert basic['sourceLabel'] == 'Played together at Shared court'
    assert 'scheduledAt' not in basic and 'scheduledAt' not in one
    assert shared['scheduledAt'] == '2026-09-15T10:00:00Z'
    assert 'Suggested' in shared['availabilityLabel']


def test_past_players_can_be_invited_during_each_friendship_state():
    helper = part('function recentPlayerActionHtml(', 'async function renderRecentPlayers(')
    outputs = node_result("const esc=x=>String(x); const uiIcon=()=>'';" + helper + """
      console.log(JSON.stringify([
        {id:2,display_name:'Sam'},
        {id:2,display_name:'Sam',is_friend:true},
        {id:2,display_name:'Sam',friendship_status:'pending',outgoing:true},
        {id:2,display_name:'Sam',friendship_status:'pending',outgoing:false,friendship_id:4},
        {id:2,display_name:'Sam',can_invite:false}
      ].map(recentPlayerActionHtml)));
    """)
    assert all('data-recent-invite="2"' in html for html in outputs[:4])
    assert 'data-recent-add="2"' in outputs[0]
    assert 'data-recent-accept="4"' in outputs[3]
    assert 'data-recent-invite' not in outputs[4]


def test_primary_player_actions_precede_history_and_dm_plans_use_same_intent():
    profile = part('async function openUserProfile(', '// ---------- My profile tab ----------')
    assert profile.index('${friendAction}') < profile.index('${publicHeadlineStats}')
    assert '<summary>Play history</summary>' in profile
    assert "modal.querySelector('#up-invite')" in profile
    thread = part('async function openThread(', 'async function openCourtChat(')
    assert 'id="thread-plan-game"' in thread
    assert 'openNewGameModal({ ...playerInvitePlannerOptions(data.user)' in thread
    assert 'renderSharedPlan(fresh.shared_plan)' in thread
    assert 'Not in your friends' in thread and 'Message request' not in thread
    nearby = part('async function renderNearbyPlayers(', 'const pendingFriendDeclines')
    assert 'id="nearby-radius"' in nearby and "['beginner', 'Beginner · under 3.0']" in nearby
    assert 'radius=${radius}' in nearby
    assert 'requestVersion !== el._nearbyRequest' in nearby
