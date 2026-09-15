"""Calendar controls handle uncertain reset, concurrent actions and manual copying."""
from tests.test_product_audit_play_frontend import functions_between, run_js

SOURCE = functions_between('async function subscribeGamesCalendar(', 'function businessMineItems(')
HARNESS = r'''
const state={me:{id:1}},location={origin:'https://local.test'};let focused='',selected=false,error='',confirm=true;
const nodes=new Map();
const node=selector=>{
 if(!nodes.has(selector))nodes.set(selector,{id:selector.slice(1),disabled:false,hidden:false,innerHTML:'',textContent:'',value:'',attrs:{},handlers:{},classList:{add(){}},
 addEventListener(event,fn){this.handlers[event]=fn;},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];if(k==='href')this.href='';},
 focus(){focused=this.id;},select(){selected=true;}});
 return nodes.get(selector);
};
const providers=[node('#calendar-google-subscribe'),node('#calendar-device-subscribe')];
const sheet={isConnected:true,querySelector:node,querySelectorAll:()=>providers};
const openModal=()=>sheet,modalHead=()=>'',uiIcon=()=>'',toast=()=>{};
const beginRoutedOverlayLoad=()=>({}),routedOverlayLoadIsCurrent=()=>true;
const beginButtonAction=(button,label)=>{if(button.disabled)return null;button.disabled=true;const html=button.innerHTML;let done=false;return()=>{if(done)return;done=true;button.disabled=false;button.innerHTML=html;};};
const clearInlineActionError=()=>error='',showInlineActionError=(_sheet,message)=>error=message;
const openActionConfirmation=async()=>confirm,requestAnimationFrame=fn=>fn();
let apiHandler=async()=>({token:'first-token'});const calls=[];
const api=async(path,options)=>{calls.push(path);return apiHandler(path,options);};
let clipboardHandler=async()=>{};const navigator={clipboard:{writeText:text=>clipboardHandler(text)}};
const click=selector=>node(selector).handlers.click();
'''


def test_reset_locks_old_links_and_recovers_uncertain_result_without_claiming_success():
    result=run_js(HARNESS+SOURCE+r'''
(async()=>{
 await subscribeGamesCalendar();await click('#calendar-copy-link');
 let rejectReset;apiHandler=()=>new Promise((_,reject)=>{rejectReset=reject;});
 const reset=click('#calendar-reset-link');await Promise.resolve();
 const pending={copy:node('#calendar-copy-link').disabled,href:providers[0].href};
 rejectReset(new Error('response lost'));await reset;
 const failed={error,copy:node('#calendar-copy-link').disabled,reload:node('#calendar-reload-link').hidden,status:node('.calendar-copy-status').textContent};
 apiHandler=async()=>({token:'current-token'});await click('#calendar-reload-link');
 console.log(JSON.stringify({pending,failed,recovered:{copy:node('#calendar-copy-link').disabled,url:node('#calendar-feed-url').value,
 label:node('#calendar-copy-link').innerHTML,reload:node('#calendar-reload-link').hidden,focus:focused}}));
})();
''')
    assert result['pending']=={'copy':True,'href':''}
    assert 'could not be confirmed' in result['failed']['error']
    assert result['failed']['copy'] and not result['failed']['reload']
    assert result['failed']['status']==''
    assert result['recovered']['url'].endswith('/current-token.ics')
    assert not result['recovered']['copy'] and result['recovered']['reload']
    assert 'Copied' not in result['recovered']['label']
    assert result['recovered']['focus']=='calendar-google-subscribe'


def test_reset_cancel_and_account_change_do_not_mutate_or_apply_late_token():
    result=run_js(HARNESS+SOURCE+r'''
(async()=>{
 await subscribeGamesCalendar();confirm=false;await click('#calendar-reset-link');const cancelled=calls.length===1;
 confirm=true;let resolveReset;apiHandler=()=>new Promise(resolve=>resolveReset=resolve);
 const reset=click('#calendar-reset-link');await Promise.resolve();state.me.id=2;resolveReset({token:'late-token'});await reset;
 console.log(JSON.stringify({cancelled,url:node('#calendar-feed-url').value,focused}));
})();
''')
    assert result['cancelled']
    assert result['url'].endswith('/first-token.ics') and result['focused']==''


def test_manual_copy_recovers_focus_and_concurrent_reset_does_not_send_request():
    result=run_js(HARNESS+SOURCE+r'''
(async()=>{
 await subscribeGamesCalendar();let rejectCopy;clipboardHandler=()=>new Promise((_,reject)=>rejectCopy=reject);
 const copy=click('#calendar-copy-link');await click('#calendar-reset-link');const noReset=calls.length===1;
 rejectCopy(new Error('clipboard denied'));await copy;
 console.log(JSON.stringify({noReset,focused,selected,error,copyDisabled:node('#calendar-copy-link').disabled}));
})();
''')
    assert result['noReset'] and result['selected'] and not result['copyDisabled']
    assert result['focused']=='calendar-feed-url' and 'Copy was blocked' in result['error']
