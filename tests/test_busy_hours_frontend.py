"""Busy hours: one quiet court-page line and day bars in Before you go › Hours."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public/app-v15.js').read_text()
STYLES = (ROOT / 'public/styles-v15.css').read_text()


def section(source, start, end):
    start_at = source.index(start)
    return source[start_at:source.index(end, start_at)]


def run(script):
    source = section(APP, '  const COURT_WEEKDAY_LABELS =', '  function courtOpenStatusFact(')
    result = subprocess.run(['node', '-e', '''
      const assert=require('node:assert/strict');
      const esc=value=>String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
      const uiIcon=name=>`[${name}]`,modalHead=()=>'',compactCourtFact=value=>value,businessActionHref=()=>'';
      const state={me:null};
      const hours=Array.from({length:7},()=>Array(18).fill(0));
      hours[5][4]=4;hours[5][5]=2;hours[2][13]=3;
      const history={sample_size:42,sufficient_sample:true,timezone_source:'venue_local',peak:'Sat 9–11 AM',
        hours,local_now:{weekday:5,hour:10}};
    ''' + source + script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_court_page_hint_is_one_line_that_opens_the_hours_tab_only_with_a_pattern():
    run(r'''
      const html=courtBusyHintHtml(history);
      assert.equal(html,'<button type="button" class="cd-busy-hint" data-court-visit="hours">[chart]<span>Usually busiest Sat 9–11 AM</span>[chevron-right]</button>');
      assert.equal(courtBusyHintHtml({...history,peak:null}),'');
      assert.equal(courtBusyHintHtml(undefined),'');
      assert.ok(courtBusyHintHtml({peak:'<b>'}).includes('&lt;b&gt;'));
    ''')
    detail = section(APP, 'async function openCourtDetail', 'function openCheckInSheet')
    hint = "${courtClosed ? '' : courtBusyHintHtml(court.checkin_history)}"
    assert detail.index('${visitFactsHtml}') < detail.index(hint) < detail.index('id="cd-play-here"')
    # The data now lives in one place instead of a buried list in Court details.
    assert 'Recent Third Shot check-ins' not in APP
    assert 'court-checkin-history' not in APP + STYLES


def test_hours_tab_shows_todays_bars_with_the_current_hour_and_an_honest_footnote():
    run(r'''
      const html=courtBusyBarsHtml(history);
      assert.match(html,/<b>Busy times<\/b>/);
      const days=[...html.matchAll(/data-busy-day="(\d)" aria-label="(\w+)" aria-pressed="(\w+)"( class="active")?>(\w)</g)];
      assert.deepEqual(days.map(d=>d[5]).join(''),'MTWTFSS');
      assert.deepEqual(days.filter(d=>d[3]==='true').map(d=>[d[2],d[4]]),[['Sat',' class="active"']]);
      const rows=[...html.matchAll(/<div class="court-busy-bars" data-busy-bars="(\d)" role="img" aria-label="([^"]+)"( hidden)?>(.*?)<\/div>/g)];
      assert.equal(rows.length,7);
      assert.deepEqual(rows.filter(r=>!r[3]).map(r=>r[1]),['5']);
      assert.ok(rows.every(r=>(r[4].match(/<i /g)||[]).length===18));
      assert.equal(rows[5][2],'Sat: busiest around 9 AM');
      assert.equal(rows[2][2],'Wed: busiest around 6 PM');
      assert.equal(rows[0][2],'Mon: no recent activity');
      assert.equal((html.match(/is-now/g)||[]).length,1);
      assert.ok(rows[5][4].includes('<i style="--lvl:2" class="is-now"></i>'),'10 AM is the current hour today');
      assert.match(html,/From 42 Third Shot check-ins and games · last 90 days/);
      assert.ok(!html.includes('Times are approximate'));
      assert.match(courtBusyBarsHtml({...history,timezone_source:'approximate'}),/Times are approximate/);
      const tuesday=courtBusyBarsHtml({...history,local_now:{weekday:1,hour:10}});
      assert.match(tuesday,/data-busy-day="1" aria-label="Tue" aria-pressed="true" class="active"/);
      assert.match(tuesday,/data-busy-bars="1" role="img" aria-label="[^"]+"><i style="--lvl:0"><\/i>(<i style="--lvl:0"><\/i>){4}<i style="--lvl:0" class="is-now">/);
      assert.equal(courtBusyBarsHtml({...history,local_now:{weekday:5,hour:3}}).match(/is-now/g),null,'Night hours have no bar');
      assert.equal(courtBusyBarsHtml({...history,hours:[]}),'');
      assert.equal(courtBusyBarsHtml(null),'');
      const panel=courtVisitPanelsHtml({name:'Court',hours:'Dawn to dusk',checkin_history:history}).hours;
      assert.ok(panel.indexOf('Dawn to dusk') < panel.indexOf('Busy times'));
      assert.ok(!courtVisitPanelsHtml({name:'Court'}).hours.includes('Busy times'));
      assert.ok(!courtVisitPanelsHtml({name:'Court',closed:true,checkin_history:history}).hours.includes('Busy times'));
    ''')


def test_day_toggles_switch_the_visible_bars():
    run(r'''
      const button=(day,pressed)=>({dataset:{busyDay:day},attributes:{'aria-pressed':String(pressed)},classes:new Set(pressed?['active']:[]),handlers:{},
        setAttribute(name,value){this.attributes[name]=value;},addEventListener(name,fn){this.handlers[name]=fn;},
        classList:null});
      const buttons=['0','5'].map(day=>button(day,day==='5'));
      buttons.forEach(b=>{b.classList={toggle:(name,on)=>on?b.classes.add(name):b.classes.delete(name)};});
      const rows=['0','5'].map(day=>({dataset:{busyBars:day},hidden:day!=='5'}));
      const root={querySelectorAll:selector=>selector==='[data-busy-day]'?buttons:rows};
      bindCourtBusyDays(root);
      buttons[0].handlers.click();
      assert.deepEqual(rows.map(r=>r.hidden),[false,true]);
      assert.deepEqual(buttons.map(b=>b.attributes['aria-pressed']),['true','false']);
      assert.deepEqual(buttons.map(b=>b.classes.has('active')),[true,false]);
    ''')
    sheet = section(APP, 'function openCourtVisitSheet', 'function courtFeeTypeFact')
    bind = section(sheet, 'const bindActions = () => {', 'bindActions();')
    assert 'bindCourtBusyDays(modal);' in bind


def test_busy_hours_styles_use_tokens_inside_the_court_section():
    court = section(STYLES, '/* ---------- r83 · Court page ---------- */', '/* ---------- r83 · Play, planner & game page ---------- */')
    block = section(court, '/* r85 · Busy hours */', '/* Courts right now')
    hint = section(block, '.court-modal .cd-busy-hint {', '}')
    assert 'min-height: var(--tap-min)' in hint and 'font-size: var(--text-sm)' in hint
    assert '.court-busy-bars[hidden] { display: none; }' in block
    assert 'var(--green-300)' in block and '.court-busy-bars i.is-now { background: var(--green-accent); }' in block
    assert 'repeat(18, minmax(0, 1fr))' in block


def test_the_hint_opens_hours_scrolled_to_the_busy_bars():
    source = (Path(__file__).resolve().parents[1] / 'public' / 'app-v15.js').read_text()
    assert "function openCourtVisitSheet(court, section = 'hours', {onUpdated=null, focusBusy=false} = {})" in source
    assert "if (focusBusy) requestAnimationFrame(()=>modal.querySelector('.court-busy')?.scrollIntoView({block:'nearest'}));" in source
    assert "{focusBusy:button.classList.contains('cd-busy-hint'),onUpdated:" in source
