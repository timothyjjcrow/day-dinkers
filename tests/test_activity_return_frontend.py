"""Activity must refresh current decisions without dropping its browsing window."""
import json
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def helper(start, end):
    at = APP.index(start)
    return APP[at:APP.index(end, at)]


def run_js(source):
    result = subprocess.run(['node', '-e', source], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_return_reloads_all_previously_visible_rows_and_updated_decisions():
    result = run_js('''
      const calls=[];
      const api=async url=>{
        calls.push(url);
        return calls.length===1 ? {items:Array.from({length:20},(_,i)=>({id:100-i,needs_action:false})),
          next_cursor:'older',has_more:true,unread:3} :
          {items:[{id:81,needs_action:true},...Array.from({length:15},(_,i)=>({id:80-i,needs_action:false}))],
           next_cursor:'last',has_more:true,unread:2};
      };
    ''' + helper('async function loadActivityWindow(', 'async function openActivity(') + '''
      (async()=>console.log(JSON.stringify({page:await loadActivityWindow('games',30),calls})))();
    ''')
    assert len(result['page']['items']) == 35
    assert result['page']['items'][19] == {'id': 81, 'needs_action': True}
    assert result['page']['next_cursor'] == 'last'
    assert result['page']['unread'] == 2
    assert all('filter=games' in call for call in result['calls'])
    assert result['calls'][1].endswith('before_id=older')


def test_expired_filter_request_stops_paging_and_repeated_cursor_cannot_loop():
    result = run_js('''
      let calls=0;
      const api=async()=>{calls++;return {items:[{id:1}],has_more:true,next_cursor:'same'}};
    ''' + helper('async function loadActivityWindow(', 'async function openActivity(') + '''
      (async()=>{
        await loadActivityWindow('action',50,()=>false); const stopped=calls;
        calls=0;const page=await loadActivityWindow('action',50);
        console.log(JSON.stringify({stopped,calls,page}));
      })();
    ''')
    assert result['stopped'] == 1
    assert result['calls'] == 2
    assert result['page']['has_more'] is False
    assert len(result['page']['items']) == 1


def test_only_the_parent_that_survives_a_real_return_resumes():
    source = helper('function syncModalStack(', 'function focusAfterModalChange(')
    result = run_js('''
      const overlayStack=[],events=[];
      const currentOverlayEntry=()=>overlayStack.at(-1),$=()=>null;
      const document={documentElement:{classList:{add(){},remove(){}}}};
      const el=name=>({toggleAttribute(){},setAttribute(){},querySelector(){return null},
        classList:{toggle(){}},_onResume:()=>events.push(name)});
      const parent={el:el('parent')},child={el:el('child')},replacement={el:el('replacement')};
    ''' + source + '''
      (async()=>{
        overlayStack.push(parent);syncModalStack();await Promise.resolve();const initial=events.length;
        overlayStack.push(child);syncModalStack();overlayStack.pop();syncModalStack();syncModalStack();
        await Promise.resolve();const returned=events.length;
        overlayStack.push(child);syncModalStack();overlayStack.pop();syncModalStack();
        overlayStack.push(replacement);syncModalStack();await Promise.resolve();const transitioned=events.length;
        overlayStack.pop();syncModalStack();await Promise.resolve();
        console.log(JSON.stringify({initial,returned,transitioned,events}));
      })();
    ''')
    assert result == {'initial': 0, 'returned': 1, 'transitioned': 1, 'events': ['parent', 'parent']}
