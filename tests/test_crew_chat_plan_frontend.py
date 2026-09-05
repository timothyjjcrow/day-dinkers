"""Chat planning keeps the next session visible without touching messages or drafts."""
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def run(script):
    source = APP[APP.index('  function crewChatPlanCopy('):APP.index('  async function openCrewChat(')]
    subprocess.run(['node', '-e', "const assert=require('node:assert/strict');const fmtDateTime=value=>'Local '+value;" + source + script],
                   check=True, capture_output=True, text=True)


def test_next_session_distinguishes_attendance_and_only_offers_planning_to_joined_groups():
    run('''
      assert.equal(crewChatPlanCopy({joined:true,member_count:1},null),null);
      assert.equal(crewChatPlanCopy({joined:false,member_count:4},null),null);
      assert.match(crewChatPlanCopy({joined:true,member_count:2},null).title,/Plan with this group/);
      const game={id:7,scheduled_at:'Friday',court:{name:'Sunset Park'},is_invited:true};
      const invited=crewChatPlanCopy({},game);
      assert.equal(invited.gameId,7);
      assert.equal(invited.title,'Local Friday');
      assert.equal(invited.detail,'Sunset Park');
      assert.equal(invited.action,'You’re invited · View & RSVP');
      assert.equal(crewChatPlanCopy({},{...game,is_joined:true}).action,'You’re in · View session');
      assert.equal(crewChatPlanCopy({},{...game,waitlist_position:2}).action,'Waitlist #2 · View session');
      assert.equal(crewChatPlanCopy({},{...game,is_invited:false}).action,'Open to you · View & RSVP');
    ''')


def test_plan_refresh_preserves_button_nodes_and_uses_text_for_untrusted_names():
    run('''
      const fields=Object.fromEntries(['eyebrow','title','detail','action'].map(key=>[
        `[data-crew-plan-${key}]`,{textContent:'',hidden:false}]));
      const button={dataset:{},hidden:true,querySelector:key=>fields[key]};
      const crew={joined:true,member_count:2};
      syncCrewChatPlan(button,crew,null);
      assert.equal(button.hidden,false);
      assert.equal(button.dataset.gameId,'');
      assert.equal(fields['[data-crew-plan-action]'].hidden,true);
      const detail=fields['[data-crew-plan-detail]'];
      syncCrewChatPlan(button,crew,{id:3,scheduled_at:'Friday',court:{name:'<img onerror=bad()>'}});
      assert.equal(fields['[data-crew-plan-detail]'],detail);
      assert.equal(detail.textContent,'<img onerror=bad()>');
      assert.equal(button.dataset.gameId,3);
      syncCrewChatPlan(button,{joined:true,member_count:1},null);
      assert.equal(button.hidden,true);
      assert.equal(button.dataset.gameId,'');
    ''')
