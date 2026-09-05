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


def test_visit_sheet_keeps_complete_open_play_cost_and_access_notes_and_focuses_requested_section():
    run(r'''
      let html,focused='',scrolled=false,frame;
      const heading={focus(){focused='openplay';},scrollIntoView(){scrolled=true;}};
      const modal={isConnected:true,querySelector:()=>heading};
      const openModal=value=>{html=value;return modal;};
      const requestAnimationFrame=callback=>{frame=callback;};
      const businessActionHref=value=>/^https:\/\//.test(value||'')?value:'';
      const safeHref=value=>value||'';
      const courtDirectionsUrl=()=> 'https://maps.apple.com/?q=Court';
      const notes='Check in at the north gate, bring a paddle, and rotate after each game.';
      const court={name:'Court <unsafe>',fees:'$5 drop-in; residents play free with proof of address.',
        open_play_schedule_rows:[{weekday:'sat',start:'08:00',end:'10:00',level:'All levels',cost:'$5',notes}],
        open_play_schedule:'Call ahead on holidays.',nets_provided:true,
        reservation_url:'javascript:bad()',website:'https://example.com'};
      openCourtVisitSheet(court,'openplay');
      frame();
      assert.equal(focused,'openplay');
      assert.equal(scrolled,true);
      assert.ok(html.includes(notes));
      assert.ok(html.includes(court.fees));
      assert.ok(html.includes('All levels · $5'));
      assert.ok(html.includes('Call ahead on holidays.'));
      assert.ok(html.includes('Nets provided'));
      assert.ok(!html.includes('javascript:'));
      assert.ok(!html.includes('<unsafe>'));
      assert.ok(!html.includes('data-plan-open-play'),'Reading a schedule must not suggest a reservation or RSVP');
      openCourtVisitSheet({name:'Unknown'},'hours');
      assert.match(html,/Fees have not been listed yet/);
      assert.match(html,/No open-play schedule has been listed yet/);
      assert.match(html,/Facility details have not been listed yet/);
      modal.isConnected=false;focused='';frame();
      assert.equal(focused,'','A dismissed sheet cannot take focus');
    ''')
