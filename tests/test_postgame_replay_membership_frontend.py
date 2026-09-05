"""Saving a smaller group must not change the original game's replay roster."""
from pathlib import Path
import subprocess


APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def test_replay_attaches_a_group_only_when_every_original_player_has_joined_it():
    source = 'async function openPostGamePlanner(' + APP.split(
        '  async function openPostGamePlanner(', 1
    )[1].split('  function completedCrewConnectionsHtml(', 1)[0]
    script = '''
      const assert=require('node:assert/strict');
      const state={me:{id:1}};
      const beginButtonAction=()=>()=>{}, toast=()=>{};
      const parent={}, planner={_cleanupFns:[]};
      const currentOverlayEntry=()=>({el:planner});
      const openChildModal=(_,open)=>open();
      let savedGroup, members, captured, readFails=false, reads;
      const completedGameCrewSummary=()=>savedGroup;
      const completedCrewPlannerOptions=(game,people,group)=>({
        inviteUserIds:people.map(p=>p.id),group
      });
      const openNewGameModal=options=>{captured=options;return planner;};
      const api=async url=>{
        reads.push(url);
        if(readFails)throw Error('Synthetic failure');
        return {id:9,name:'Regulars',roster_version:7,members:members.map(id=>({id}))};
      };
    ''' + source + '''
      (async()=>{
        for(const scenario of [
          {members:[1],attach:false},
          {members:[1,2],attach:false},
          {members:[1,2,3,4],attach:true},
          {members:[1,2,3],fail:true,attach:false},
          {members:[],pending:true,attach:false}
        ]){
          savedGroup={id:9,joined:!scenario.pending,invitation_pending:!!scenario.pending};
          members=scenario.members;readFails=!!scenario.fail;reads=[];
          await openPostGamePlanner({id:8},parent,{},Promise.resolve({items:[{id:2},{id:3}]}));
          assert.deepEqual(captured.inviteUserIds,[2,3]);
          assert.equal(captured.offerSaveGroup,false);
          if(scenario.attach){
            assert.equal(captured.group.id,9);
            assert.equal(captured.group.roster_version,7);
          }else assert.equal(captured.group.attachCrew,false);
          assert.deepEqual(reads,scenario.pending?[]:['/crews/9']);
        }
      })().catch(error=>{console.error(error);process.exitCode=1;});
    '''
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)
