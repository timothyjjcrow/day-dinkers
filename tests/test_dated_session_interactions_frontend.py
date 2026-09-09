"""Execute the shipped date controls and completion handlers, beyond source contracts."""
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def run(script):
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)


def test_date_menu_uses_real_ids_escapes_text_and_distinguishes_skipped_from_joined():
    run('''
      const assert = require('node:assert/strict');
      const safePositiveId = x => Number.isInteger(Number(x)) && Number(x) > 0 ? Number(x) : null;
      const esc = x => String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
      const uiIcon = () => '';
      const fmtDateTime = x => x;
    ''' + section('function gameHasDatedSeries', 'function openGameCancellationConfirmation') + '''
      const rows = [
        {id: 11, scheduled_at:'2020-01-01', status:'completed'},
        {id: 12, scheduled_at:'2090-01-01', status:'upcoming', is_skipped:true},
        {id: 13, scheduled_at:'2090-01-08', status:'upcoming', is_joined:true},
        {id: 14, scheduled_at:'<script>', status:'cancelled'},
        {id: 'bad', scheduled_at:'2090-01-15', status:'upcoming'},
      ];
      const html = gameOccurrencesHtml({id:11,status:'completed'}, rows);
      assert.match(html, /value="11" selected/);
      assert.match(html, /value="12"[^>]*>2090-01-01 · Skipped/);
      assert.match(html, /value="13"[^>]*>2090-01-08 · Going/);
      assert.match(html, /href="#game\/12"/);
      assert.match(html, /&lt;script&gt;/);
      assert.ok(!html.includes('value="bad"'));
      assert.equal(recurrenceScopeChoicesHtml({recurrence:'none'},'test'), '');
      const scopes = recurrenceScopeChoicesHtml({recurrence_series_id:11,scheduled_at:'2020-01-01'},'test');
      assert.match(scopes, /value="this_date" checked/);
      assert.match(scopes, /value="following_dates"/);
    ''')


def test_attendance_requires_explicit_selection_and_submits_only_actual_attendees():
    run('''
      const assert = require('node:assert/strict');
      const state={me:{id:1},tab:'profile'};
      const esc=x=>x, uiIcon=()=>'', avatarHtml=()=>'', modalHead=()=>'';
      const toast=()=>{}, refreshMe=()=>{}, closeModal=()=>{}, transitionModal=()=>{};
      const beginButtonAction=()=>()=>{};
      let modal, html, calls=[], selected=[1], refreshed;
      function openModal(value) {
        html=value;
        const controls=new Map();
        modal={querySelector(selector) {
          if(!controls.has(selector)) controls.set(selector, {
            classList:{add(){},remove(){}}, focus(){},
            addEventListener(name,callback){this[name]=callback;}
          });
          return controls.get(selector);
        },querySelectorAll(selector) {
          return selector==='[data-session-attendee]:checked'
            ? selected.map(id=>({dataset:{sessionAttendee:String(id)}})) : [];
        }};
        return modal;
      }
      const api=async(url,request)=>{calls.push({url,body:JSON.parse(request.body)});return {id:44,status:'completed'};};
    ''' + section('function openSessionWrapUpModal', 'function openScoreModal') + '''
      (async()=>{
        openSessionWrapUpModal({id:44,players:[
          {user_id:1,display_name:'Host'}, {user_id:2,display_name:'Player'}, {user_id:3,display_name:'Absent'}
        ]}, value=>{refreshed=value;});
        assert.equal((html.match(/checked disabled/g)||[]).length,1);
        assert.ok(!/data-session-attendee="2" checked/.test(html));
        const save=modal.querySelector('#session-wrap-save');
        await save.click({currentTarget:save});
        assert.equal(calls.length,0);
        assert.match(modal.querySelector('#session-wrap-error').textContent,/at least two/);
        selected=[1,2];
        await save.click({currentTarget:save});
        assert.deepEqual(calls,[{url:'/games/44/complete-session',body:{attendee_user_ids:[1,2]}}]);
        assert.equal(refreshed.status,'completed');
      })().catch(error=>{console.error(error);process.exitCode=1;});
    ''')


def test_consent_controls_dispatch_explicit_responses_to_the_correct_date():
    start = APP.index('    function bind() {', APP.index('async function openGameScreen'))
    body = APP[start + len('    function bind() {'):APP.index('      const datesHost', start)]
    run('''
      const assert=require('node:assert/strict');
      const gameId=27, game={id:27,host_handoff:{id:91}}, state={tab:'profile'};
      const buttons=[{dataset:{waitlistReply:'accept'}},{dataset:{hostReply:'decline'}},
                     {dataset:{attendanceCorrect:'8',attended:'true'}}];
      for(const button of buttons) button.addEventListener=(name,fn)=>{button[name]=fn;};
      const box={querySelector:()=>null,querySelectorAll:()=>buttons};
      let calls=[],rendered=[];
      const api=async(endpoint,request)=>{calls.push([endpoint,request.method,JSON.parse(request.body)]);return {id:27};};
      const beginButtonAction=()=>()=>{},render=value=>rendered.push(value),refreshMe=()=>{},toast=()=>{};
      const showInlineActionError=()=>{throw Error('unexpected failure');};
    ''' + body + '''
      (async()=>{
        assert.equal(calls.length,0,'Rendering must not imply acceptance');
        for(const button of buttons) await button.click();
        assert.deepEqual(calls,[
          ['/games/27/waitlist/respond','POST',{accept:true}],
          ['/games/27/host-handoff/91/respond','POST',{accept:false}],
          ['/games/27/attendance/8','PATCH',{attended:true}],
        ]);
        assert.equal(rendered.length,3);
      })().catch(error=>{console.error(error);process.exitCode=1;});
    ''')


def test_consent_summary_separates_rsvps_attendance_and_pending_responsibility():
    run('''
      const assert=require('node:assert/strict');
      const state={me:{id:8}};
      const esc=x=>String(x).replaceAll('<','&lt;').replaceAll('>','&gt;');
      const fmtDateTime=x=>x;
    ''' + section('function gameConsentHtml', 'function openHostHandoffModal') + '''
      const html=gameConsentHtml({status:'completed',completion_kind:'session',is_creator:false,
        host_handoff:{id:1,can_respond:true,requested_by_name:'<Host>',scope:'this_date',expires_at:'Tomorrow'},
        attendance_record:{signed_up_count:3,played_count:2,people:[
          {user_id:8,display_name:'Me',attended:false}, {user_id:9,display_name:'Another',attended:true}
        ]}});
      assert.match(html,/3 signed up · 2 played/);
      assert.match(html,/current host stays responsible/);
      assert.match(html,/&lt;Host&gt;/);
      assert.match(html,/data-attendance-correct="8"/);
      assert.ok(!html.includes('data-attendance-correct="9"'));
      assert.match(html,/data-host-reply="accept"/);
      assert.match(html,/data-host-reply="decline"/);
    ''')
