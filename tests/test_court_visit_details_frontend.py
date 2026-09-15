"""Visit details preserve complete listing information and honest missing-data states."""
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def run(script):
    source = APP[APP.index('  const COURT_WEEKDAY_LABELS ='):APP.index('  function courtOpenStatusFact(')]
    subprocess.run(['node', '-e', '''
      const assert=require('node:assert/strict');
      const esc=value=>String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
      const uiIcon=()=>'',modalHead=()=>'',compactCourtFact=value=>value;
      const state={me:null};
    ''' + source + script], check=True, capture_output=True, text=True)


def test_visit_hours_support_split_days_overnight_and_full_day_windows():
    run(r'''
      const html=courtVisitHoursHtml({structured_hours:{
        timezone:'America/Los_Angeles',
        mon:[{open:'06:00',close:'10:00'},{open:'17:30',close:'22:00'}],
        tue:{open:'22:00',close:'02:00'},wed:{open:'00:00',close:'00:00'},
        thu:{open:'25:00',close:'02:00'}
      }});
      assert.match(html,/6–10 AM · 5:30–10 PM/);
      assert.match(html,/10 PM–2 AM \(next day\)/);
      assert.match(html,/24 hours/);
      assert.match(html,/<dt>Thu<\/dt><dd>Not listed<\/dd>/);
      assert.match(html,/America\/Los Angeles/);
    ''')


def test_visit_hours_keep_full_freeform_notes_and_do_not_invent_hours():
    run(r'''
      const notes='Weekdays 6 AM–10 PM; weekends 7 AM–9 PM. Holiday hours may differ.';
      assert.ok(courtVisitHoursHtml({hours:notes}).includes(notes));
      assert.match(courtVisitHoursHtml({}),/Hours have not been listed yet/);
      assert.match(courtVisitHoursHtml({hours_dawn_to_dusk:true}),/Dawn to dusk/);
      assert.ok(!courtVisitHoursHtml({hours:'<script>bad()</script>'}).includes('<script>'));
    ''')


def test_visit_panels_keep_complete_cost_access_notes_and_safe_links():
    run(r'''
      const businessActionHref=value=>/^https:\/\//.test(value||'')?value:'';
      const notes='Check in at the north gate, bring a paddle, and rotate after each game.';
      const court={name:'Court <unsafe>',fees:'$5 drop-in; residents play free with proof of address.',
        open_play_schedule_rows:[{weekday:'sat',start:'08:00',end:'10:00',level:'All levels',cost:'$5',notes}],
        open_play_schedule:'Call ahead on holidays.',nets_provided:true,
        reservation_url:'javascript:bad()',website:'https://example.com'};
      const panels=courtVisitPanelsHtml(court),html=Object.values(panels).join('');
      assert.ok(panels.openplay.includes(notes));
      assert.ok(panels.fees.includes(court.fees));
      assert.ok(panels.openplay.includes('All levels · $5'));
      assert.ok(panels.openplay.includes('Call ahead on holidays.'));
      assert.ok(panels.facilities.includes('Nets provided'));
      assert.ok(!html.includes('javascript:'));
      assert.ok(!html.includes('data-plan-open-play'),'Reading a schedule must not suggest a reservation or RSVP');
      const empty=courtVisitPanelsHtml({name:'Unknown'});
      assert.match(empty.fees,/Fees not listed/);
      assert.match(empty.openplay,/No open-play schedule listed/);
      assert.ok(!empty.facilities.includes('Restrooms'));
      assert.match(empty.facilities,/Unlisted facilities are not confirmed/);
    ''')


def test_visit_tabs_show_only_the_requested_topic_and_support_keyboard_navigation():
    run(r'''
      let frame;
      const requestAnimationFrame=fn=>frame=fn;
      const keys=['hours','fees','facilities','openplay'];
      const tabs=keys.map(key=>({dataset:{visitTab:key},attributes:{},handlers:{},
        setAttribute(name,value){this.attributes[name]=value;},addEventListener(name,fn){this.handlers[name]=fn;},
        focus(){focused=key;}}));
      const panels=keys.map(key=>({dataset:{visitPanel:key}}));
      let focused='';
      const modal={isConnected:true,closest:()=>null,querySelectorAll:selector=>selector==='[data-visit-tab]'?tabs:panels};
      const visible=()=>panels.filter(panel=>!panel.hidden).map(panel=>panel.dataset.visitPanel);
      bindCourtVisitTabs(modal,'fees');frame();
      assert.equal(focused,'fees');assert.deepEqual(visible(),['fees']);
      assert.equal(tabs.filter(tab=>tab.tabIndex===0).length,1);
      tabs[1].handlers.keydown({key:'ArrowRight',preventDefault(){}});
      assert.equal(focused,'facilities');assert.deepEqual(visible(),['facilities']);
      tabs[2].handlers.keydown({key:'End',preventDefault(){}});
      assert.equal(focused,'openplay');assert.deepEqual(visible(),['openplay']);
      tabs[3].handlers.keydown({key:'ArrowRight',preventDefault(){}});
      assert.equal(focused,'hours');assert.deepEqual(visible(),['hours']);
      tabs[0].handlers.keydown({key:'ArrowLeft',preventDefault(){}});
      assert.equal(focused,'openplay');
      tabs[3].handlers.keydown({key:'Home',preventDefault(){}});
      assert.equal(focused,'hours');
      tabs[1].handlers.click();assert.deepEqual(visible(),['fees']);
      assert.equal(tabs[1].attributes['aria-selected'],'true');
      assert.equal(tabs[0].attributes['aria-selected'],'false');
      modal.isConnected=false;focused='';frame();assert.equal(focused,'');
    ''')


def test_visit_sources_are_grouped_without_dropping_or_relabeling_facts():
    run(r'''
      const window={VenueWorkspace:{visitingSummary:info=>Object.entries(info).map(([key,value])=>`${key}: ${value}`).join('')}};
      const html=courtVisitingInfoHtml({visitor_info:{entrance:'North <gate>',parking:'Free lot',guest_access:'Members only'},visitor_info_sources:{guest_access:'venue'}});
      assert.equal((html.match(/From the venue/g)||[]).length,1);
      assert.equal((html.match(/Community information/g)||[]).length,1);
      assert.ok(html.includes('Members only') && html.includes('North &lt;gate&gt;') && html.includes('Free lot'));
      assert.ok(html.indexOf('Members only') < html.indexOf('Community information'));
      assert.ok(html.indexOf('North &lt;gate&gt;') < html.indexOf('Free lot'));
    ''')


def test_visit_management_actions_match_the_current_role_and_preview_state():
    run(r'''
      state.me={id:1};
      for(const role of ['owner','admin','editor']) {
        assert.match(courtVisitManagementHtml({business:{is_manager:true,manager_role:role}}),/Manage venue details/);
      }
      assert.match(courtVisitManagementHtml({business:{is_manager:true,manager_role:'viewer'}}),/View venue workspace/);
      assert.equal(courtVisitManagementHtml({business:{is_manager:false}}),'');
      assert.equal(courtVisitManagementHtml({business:{is_manager:true,manager_role:'owner',preview_only:true}}),'');
      state.me=null;
      assert.equal(courtVisitManagementHtml({business:{is_manager:true,manager_role:'owner'}}),'');
    ''')
