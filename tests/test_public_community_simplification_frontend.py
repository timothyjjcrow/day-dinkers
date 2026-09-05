"""Public community actions preserve membership and organizer boundaries."""
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def test_rendered_community_roles_keep_primary_actions_and_private_management_separate():
    start = APP.index('  function openClubInfo(')
    helpers_start = APP.index('  function groupSessionContentHtml(')
    source = APP[helpers_start:APP.index('  async function openCrewScreen(', helpers_start)]
    copy_start = APP.index('  function crewChatPlanCopy(')
    source += APP[copy_start:APP.index('  function syncCrewChatPlan(', copy_start)]
    source += APP[start:APP.index('  async function openClubInviteSheet(', start)]
    subprocess.run(['node', '-e', '''
      const assert=require('node:assert/strict');
      const esc=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
      const uiIcon=()=>'',avatarHtml=()=>'',playerSkillIdentityHtml=()=>'';
      const fmtDateTime=value=>value,modalHead=name=>`<h1>${esc(name)}</h1>`;
      const routedOverlayLoadIsCurrent=()=>true,state={me:{id:1}};
      const CTA_LABELS={invitePlayers:'Invite players'};
      let html;
      const done=Symbol('rendered');
      const openModal=markup=>{html=markup;throw done;};
    ''' + source + '''
      const base={id:1,name:'Sunset <community>',member_count:2,joined:true,
        roster_visible:true,my_role:'member',home_court_id:1,home_court_name:'Park',
        members:[{id:1,display_name:'Alex',role:'member'},{id:2,display_name:'Jordan',role:'member'}],
        upcoming_games:[{id:9,scheduled_at:'Tomorrow',court:{name:'Park'},players:[{id:1}],max_players:6,spots_left:5}]};
      const render=changes=>{try{openClubInfo({...base,...changes});}catch(e){if(e!==done)throw e;}return html;};
      const has=id=>html.includes(`id="${id}"`);
      render({});
      assert.ok(html.includes('Sunset &lt;community>'));
      assert.ok(has('club-plan')&&has('club-chat-btn')&&has('club-leave'));
      assert.ok(!has('club-edit')&&!has('club-bans')&&!has('club-announcement'));
      assert.ok(html.indexOf('data-open-game="9"')<html.indexOf('id="club-plan"'));
      assert.ok(html.indexOf('id="club-members"')<html.indexOf('id="club-invite"'));
      assert.ok(html.indexOf('id="club-settings"')<html.indexOf('id="club-notifications"'));
      assert.equal((html.match(/id="club-share"/g)||[]).length,1);
      assert.ok(!html.includes('<details open'));
      render({my_role:'owner',pending_join_requests:2});
      for(const id of ['club-edit','club-delete','club-bans','club-announcement','club-join-requests'])assert.ok(has(id),id);
      assert.ok(!has('club-leave'));
      assert.ok(html.indexOf('id="club-join-requests"')<html.indexOf('id="club-settings"'));
      render({my_role:'admin'});
      assert.ok(has('club-bans')&&has('club-announcement')&&has('club-leave'));
      assert.ok(!has('club-edit')&&!has('club-delete'));
      for(const pending of [false,true]){
        render({joined:false,roster_visible:false,my_role:null,join_policy:'request',join_request_status:pending?'pending':null});
        assert.ok(has('club-join-btn')&&has('club-share'));
        assert.equal(has('club-cancel-request'),pending);
        for(const id of ['club-plan','club-chat-btn','club-settings','club-invite','club-leave'])assert.ok(!has(id),id);
        assert.ok(!html.includes('data-view-user='));
        assert.ok(html.includes('Member list is private'));
        assert.ok(html.includes(pending?'Request pending':'Request to join'));
        assert.equal((html.match(/id="club-share"/g)||[]).length,1);
      }
    '''], check=True, capture_output=True, text=True)
