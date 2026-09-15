"""Focus follows a completed action only while its session is still on top."""
from tests.test_private_session_links_frontend import section, run_js


def test_session_action_focus_waits_for_render_and_does_not_follow_navigation():
    result = run_js('''
      const pending=[],focused=[];
      const requestAnimationFrame=callback=>pending.push(callback);
      const modal={isConnected:true};let top=modal;
      const currentOverlayEntry=()=>({el:top});
      const box={querySelector:selector=>selector==='missing'?null:{focus:()=>focused.push(selector)}};
    ''' + section('function focusGameControl(', 'async function openGameScreen(') + '''
      focusGameControl(modal,box,'waitlist-status');const before=focused.length;
      pending.shift()();
      focusGameControl(modal,box,'stale-target');top={};pending.shift()();
      top=modal;focusGameControl(modal,box,'detached-target');modal.isConnected=false;pending.shift()();
      modal.isConnected=true;focusGameControl(modal,box,'missing');pending.shift()();
      console.log(JSON.stringify({before,focused}));
    ''')
    assert result == {'before': 0, 'focused': ['waitlist-status']}


def test_decline_keeps_button_across_confirmation_and_only_commits_explicit_acceptance():
    handler = section("box.querySelector('#gs-decline-invite')?.addEventListener", "box.querySelector('#gs-complete-no-score')")
    result = run_js('''
      let listener,resolve,mode='success',top;
      const calls=[],resets=[],errors=[],closed=[];
      const modal={isConnected:true};top=modal;
      const box={querySelector:()=>({addEventListener:(_event,callback)=>listener=callback})};
      const game={invited_by:{display_name:'Sam'}},gameId=10,state={tab:'play'};
      const currentOverlayEntry=()=>({el:top});
      const openActionConfirmation=()=>new Promise(done=>resolve=done);
      const beginButtonAction=button=>button ? ()=>resets.push(button.id) : null;
      const api=async(url,options)=>{calls.push({url,method:options.method});if(mode==='error')throw new Error('Try again');};
      const toast=()=>{},refreshMe=()=>{},renderPlay=()=>{};
      const closeModal=()=>closed.push(true),showInlineActionError=(_box,message)=>errors.push(message);
    ''' + handler + '''
      (async()=>{
        const run=async(accept,navigate=false)=>{
          top=modal;const event={currentTarget:{id:'decline',disabled:false}};
          const action=listener(event);event.currentTarget=null;
          if(navigate)top={};resolve(accept);await action;
        };
        await run(false);await run(true,true);const before=calls.length;
        await run(true);mode='error';await run(true);
        console.log(JSON.stringify({before,calls,closed,resets,errors}));
      })();
    ''')
    assert result['before'] == 0
    assert result['calls'] == [{'url': '/games/10/invites/decline', 'method': 'POST'}] * 2
    assert result['closed'] == [True]
    assert result['resets'] == ['decline']
    assert result['errors'] == ['Try again']
