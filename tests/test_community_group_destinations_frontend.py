"""Groups opens membership/plans while Messages preserves conversation intent."""
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def section(start, end):
    return APP[APP.index(start):APP.index(end, APP.index(start))]


def run(script):
    subprocess.run(['node', '-e', "const assert=require('node:assert/strict');"+script],
                   check=True, capture_output=True, text=True)


def test_group_cards_show_membership_and_open_info_before_discovery_controls():
    source = section('  function inboxMessagePreviewText(', '  function bindCommunityConversationRows(')
    run('''
      const state={me:{id:1}};
      const esc=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
      const avatarHtml=()=>'',uiIcon=()=>'';
      const fmtDateTime=value=>value,fmtInboxTimestamp=()=> 'Today';
      const crewSummaryFrom=value=>value;
      const pendingCrewInvitationEntries=()=>[];
    ''' + source + '''
      const empty={items:[]};
      const crews={items:[{id:12,name:'Weekend <players>',member_count:3,default_court_name:'Sunset Park',last_message:{id:7,body:'Meet at 10',sender_name:'Jordan',sender_id:2,created_at:'2026-09-05T10:00Z'},unread:2}]};
      const clubs={items:[{id:15,name:'Park community',member_count:20,home_court_name:'Park',last_message:null}]};
      const groups=universalInboxHtml(empty,empty,clubs,empty,crews,{filter:'groups',groupPage:true});
      assert.equal((groups.match(/data-inbox-destination="info"/g)||[]).length,2);
      assert.ok(groups.includes('3 players · Sunset Park'));
      assert.ok(groups.includes('Weekend &lt;players>'));
      assert.ok(!groups.includes('Meet at 10'));
      assert.ok(groups.includes('2 unread messages, group info'));
      assert.ok(groups.indexOf('data-inbox-id="12"')<groups.indexOf('id="club-find"'));
      assert.ok(groups.indexOf('data-inbox-id="15"')<groups.indexOf('id="club-find"'));
      assert.ok(!groups.includes('data-chat-filter'));
      assert.ok(!groups.includes('class="inbox-kind"'));

      const messages=universalInboxHtml(empty,empty,clubs,empty,crews);
      assert.equal((messages.match(/data-inbox-destination="chat"/g)||[]).length,2);
      assert.ok(messages.includes('Jordan: Meet at 10'));
      assert.ok(messages.includes('Today'));
      assert.ok(!messages.includes('data-inbox-destination="info"'));
      const noGroups=universalInboxHtml(empty,empty,empty,empty,empty,{filter:'groups',groupPage:true});
      assert.ok(noGroups.includes('id="club-find"'));
      assert.ok(noGroups.includes('id="group-new"'));
    ''')


def test_private_and_public_groups_route_to_info_without_opening_chat_or_marking_read():
    source = section('  function bindCommunityConversationRows(', '  function openCreateGroupChoiceSheet(')
    run('''
      const calls=[];
      const state={tab:'chat',chatSeg:'groups'};
      let refreshes=0;
      const renderChat=()=>refreshes++;
      const view={_cleanupFns:[]};
      const openCrewScreen=async id=>(calls.push(['crew-info',id]),view);
      const openCrewChatById=async id=>(calls.push(['crew-chat',id]),view);
      const openClubScreen=async(id,options)=>(calls.push(['club',id,options.destination]),view);
      const errorToast=error=>{throw error;};
    ''' + source + '''
      (async()=>{
        for(const [kind,destination,expected] of [
          ['crew','info',['crew-info',12]],['crew','chat',['crew-chat',12]],
          ['club','info',['club',12,'info']],['club','chat',['club',12,'chat']],
          ['crew',undefined,['crew-chat',12]],
        ]){
          let click;
          const row={disabled:false,dataset:{inboxKind:kind,inboxDestination:destination,inboxId:'12'},addEventListener:(name,handler)=>click=handler};
          bindCommunityConversationRows({querySelectorAll:()=>[row]});
          await click();
          assert.deepEqual(calls.at(-1),expected);
          assert.equal(row.disabled,false);
          const cleanup=view._cleanupFns.at(-1);
          const before=refreshes;
          cleanup();cleanup();await Promise.resolve();
          assert.equal(refreshes,before+1);
        }
      })().catch(error=>{console.error(error);process.exitCode=1;});
    ''')
