"""Conversation starters prepare a draft, preserve text, and survive an empty outbox."""
from pathlib import Path
import subprocess


APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def source_between(start, end):
    return APP[APP.index(start):APP.index(end, APP.index(start))]


def run(script):
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)


def test_starters_only_fill_an_empty_composer_and_keep_input_events_and_limits():
    source = source_between('  function applyChatStarter(', '  async function openCrewChat(')
    run('''
      const assert=require('node:assert/strict');
      let events=[],focuses=0,selection;
      const input={value:'',maxLength:12,
        dispatchEvent(event){events.push([event.type,event.bubbles]);},
        focus(){focuses++;},setSelectionRange(...range){selection=range;}};
    ''' + source + '''
      assert.equal(applyChatStarter(input,'Hello pickleball friends!'),true);
      assert.equal(input.value,'Hello pickle');
      assert.deepEqual(events,[['input',true]]);
      assert.equal(focuses,1);
      assert.deepEqual(selection,[12,12]);
      assert.equal(applyChatStarter(input,'Replacement'),false);
      assert.equal(input.value,'Hello pickle');
      input.value='  ';
      assert.equal(applyChatStarter(input,'Replacement'),false);
      assert.equal(input.value,'  ');
      assert.equal(events.length,1);
      input.value='';
      assert.equal(applyChatStarter(input,''),false);
      assert.equal(applyChatStarter(null,'Hello'),false);
    ''')


def test_welcome_distinguishes_pending_players_and_restored_drafts_without_html_injection():
    source = source_between('  function crewChatStarters(', '  function applyChatStarter(')
    run('''
      const assert=require('node:assert/strict');
      const esc=value=>String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      const uiIcon=()=>'';
    ''' + source + '''
      const pending={member_count:1,pending_count:2,default_court_name:'Sunset Park'};
      assert.match(crewChatWelcomeCopy(pending).body,/2 invitations are pending/);
      assert.match(crewChatWelcomeCopy({member_count:1,pending_count:1}).body,/1 invitation is pending/);
      assert.match(crewChatWelcomeCopy({member_count:1,pending_count:0}).body,/Add players from group info/);
      assert.equal(crewChatWelcomeCopy({member_count:2,pending_count:1}).title,'Start with a hello');
      assert.match(crewChatStarters(pending)[2].text,/Sunset Park/);
      assert.match(crewChatStarters({})[2].text,/favorite local court/);
      const restored=crewChatWelcomeHtml(pending,true);
      assert.match(restored,/data-crew-starters hidden/);
      assert.match(restored,/data-crew-draft-note >/);
      assert.equal((crewChatWelcomeHtml(pending).match(/data-crew-chat-starter=/g)||[]).length,3);
      assert.ok(!crewChatWelcomeHtml({default_court_name:'<script>oops</script>'}).includes('<script>'));
    ''')


def test_empty_outbox_restores_the_current_conversation_welcome_without_replacing_messages():
    source = source_between('    const renderOutbox = async () =>', '    const onOutboxAction = async')
    run('''
      const assert=require('node:assert/strict');
      let outboxRenderRevision=0,html='',messagePresent=false,emptyPresent=false,factoryCalls=0;
      const accountId=1,channelKey='crew:2',outboxStatus={textContent:''};
      const listChatOutbox=async()=>[],chatScrollSnapshot=()=>({nearBottom:true}),restoreScroll=()=>{};
      const document={body:{contains:()=>true}};
      const msgsEl={querySelectorAll:()=>[],querySelector:()=>messagePresent||emptyPresent,
        set innerHTML(value){html=value;emptyPresent=true;}};
      const emptyMessageHtml=()=>{factoryCalls++;return '<section class="empty-state">Current group welcome</section>';};
    ''' + source + '''
      (async()=>{
        await renderOutbox();
        assert.equal(factoryCalls,1);
        assert.match(html,/Current group welcome/);
        await renderOutbox();
        assert.equal(factoryCalls,1,'An existing welcome stays intact');
        emptyPresent=false;messagePresent=true;
        await renderOutbox();
        assert.equal(factoryCalls,1,'Messages must never be replaced');
      })().catch(error=>{console.error(error);process.exitCode=1;});
    ''')


def test_empty_chat_can_wait_for_typing_intent_without_changing_other_chat_defaults():
    source = source_between('  function hydrateChatLoadShell(', '  // Actionable empty destinations')
    run('''
      const assert=require('node:assert/strict');
      let focuses=0;
      const composer={isConnected:true,focus(){focuses++;}};
      const shell={modal:{isConnected:true,classList:{add(){},remove(){}}},
        box:{querySelector:selector=>selector==='.thread-input textarea'?composer:{setAttribute(){}}}};
      const hydrateDetailLoadShell=()=>{};
    ''' + source + '''
      (async()=>{
        hydrateChatLoadShell(shell,'','New group',{focusComposer:false});
        await Promise.resolve();
        assert.equal(focuses,0);
        hydrateChatLoadShell(shell,'');
        await Promise.resolve();
        assert.equal(focuses,1);
        composer.isConnected=false;
        hydrateChatLoadShell(shell,'');
        await Promise.resolve();
        assert.equal(focuses,1,'A stale composer must not take focus');
      })().catch(error=>{console.error(error);process.exitCode=1;});
    ''')
