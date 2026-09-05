"""Capacity and filtering behavior for private-group invitations."""
import json
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def run_js(script):
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_invitation_capacity_counts_pending_consent_without_double_counting():
    source = 'function playGroupInviteCapacity(' + APP.split('  function playGroupInviteCapacity(', 1)[1].split('  function playGroupFriendSearchHtml', 1)[0]
    result = run_js(source + '''
      process.stdout.write(JSON.stringify([
        playGroupInviteCapacity(),
        playGroupInviteCapacity({member_count: 4, members: [{id:1},{id:2},{id:3},{id:4}], pending_invites: [{user:{id:5}},{user:{id:6}}]}),
        playGroupInviteCapacity({member_count: 12}),
        playGroupInviteCapacity({members:[{id:1}], pending_invites:[{user:{id:1}},{user:{id:2}},{user:{id:2}}]}),
        playGroupInviteCapacity({member_count:9, pending_invites:[{user:{id:10}}]}),
      ]));
    ''')
    assert result == [11, 6, 0, 10, 2]


def test_search_hides_unmatched_friends_without_changing_selection_and_can_clear():
    source = 'function bindPlayGroupFriendSearch(' + APP.split('  function bindPlayGroupFriendSearch(', 1)[1].split('  async function openCreatePlayGroupSheet', 1)[0]
    result = run_js('''
      function classes(initial=[]) {const values = new Set(initial); return {
        toggle(k,on) {if(on) values.add(k);else values.delete(k);}, has:k=>values.has(k)
      };}
      const buttons = ['Jordan','Sam','Taylor','Morgan','Dana','Priya','Chris'].map((name,i)=>({
        dataset:{friendName:name}, classList:classes(i===0?['active']:[]), disabled:i===6
      }));
      const input = {value:'', onInput:null, focused:false, addEventListener(_,fn){this.onInput=fn;},focus(){this.focused=true;}};
      const field={classList:classes()}, empty={classList:classes()};
      const clear={onClick:null,addEventListener(_,fn){this.onClick=fn;}};
      const nodes={'#pg-friend-search':input,'#pg-friend-search-field':field,'#pg-friend-search-empty':empty,'#pg-friend-search-clear':clear};
      const modal={querySelector:key=>nodes[key]}, list={querySelectorAll:()=>buttons};
    ''' + source + '''
      const sync=bindPlayGroupFriendSearch(modal,'pg',list,'button');sync();
      const searchVisible=!field.classList.has('hidden');
      input.value='sAM'; input.onInput();
      const matching=buttons.filter(b=>!b.classList.has('hidden')).map(b=>b.dataset.friendName);
      const selectionKept=buttons[0].classList.has('active');
      input.value='nobody';input.onInput();
      const noResults=!empty.classList.has('hidden');
      clear.onClick();
      process.stdout.write(JSON.stringify({searchVisible,matching,selectionKept,noResults,
        restored:buttons.every(b=>!b.classList.has('hidden')),disabledKept:buttons[6].disabled,focused:input.focused}));
    ''')
    assert result == {'searchVisible': True, 'matching': ['Sam'], 'selectionKept': True,
                      'noResults': True, 'restored': True, 'disabledKept': True, 'focused': True}
