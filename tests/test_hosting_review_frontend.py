"""Execute hosting review rendering: precise terms without exposing raw markup."""
from test_dated_session_interactions_frontend import run, section


def test_hosting_review_shows_all_dates_and_distinct_rule_terms_safely():
    run(r'''
      const assert=require('node:assert/strict');
      const esc=x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
      const fmtDateTime=x=>x, fmtTimeShort=x=>x.slice(11,16), recurrenceClockLabel=()=> '11:00 AM PT';
      const sessionPlayStyleLabel=()=> 'Doubles', sessionCourtAccessLabel=()=> 'Court booking still needed';
    ''' + section('function hostingPlanFactsHtml', 'function openHostingAcceptanceReview') + r'''
      const base={scheduled_at:'2026-09-24T18:00:00Z',ends_at:'2026-09-24T19:30:00Z',
        title:'<img src=x>',notes:'<script>unsafe</script>',description:'Bring a paddle',
        duration_minutes:90,game_type:'casual',visibility:'private',max_players:4,player_count:2,
        court:{name:'<Court>'},court_number:'Court 3',cost_cents:800};
      const dates=Array.from({length:5},(_,i)=>({...base,id:i+1,already_joined:i===0}));
      dates[4].player_count=4;
      const html=hostingReviewHtml({dates,handoff_id:91,rule:{...base,cost_cents:500,
        recurrence_weekdays:['tue','thu'],recurrence_ends_on:'2026-10-08'}});
      assert.equal((html.match(/class="hosting-date-card[^"]*"/g)||[]).length,5);
      assert.match(html,/3 more dates/);
      assert.match(html,/Repeating schedule/);
      assert.match(html,/Tue &amp; Thu/);
      assert.match(html,/\$8.00 per player/);
      assert.match(html,/\$5.00 per player/);
      assert.match(html,/Court 3/);
      assert.match(html,/90 minutes/);
      assert.match(html,/Invite only/);
      assert.match(html,/You’ll join as host/);
      assert.match(html,/Full — no place available/);
      assert.match(html,/class="hosting-date-card is-full"/);
      assert.match(html,/&lt;img src=x&gt;/);
      assert.match(html,/&lt;script&gt;/);
      assert.ok(!html.includes('<script>') && !html.includes('<Court>'));
    ''')


def test_hosting_review_preserves_unknown_terms_and_cross_midnight_end_date():
    run(r'''
      const assert=require('node:assert/strict');
      const esc=String, fmtDateTime=x=>x, fmtTimeShort=()=> 'TIME ONLY';
      const sessionPlayStyleLabel=()=> '', sessionCourtAccessLabel=()=> 'Court booking not listed';
      const recurrenceClockLabel=()=>'';
    ''' + section('function hostingPlanFactsHtml', 'function openHostingAcceptanceReview') + r'''
      const start=new Date(2026,8,24,23,30), end=new Date(2026,8,25,1,0);
      const html=hostingReviewHtml({dates:[{scheduled_at:start.toISOString(),ends_at:end.toISOString(),
        max_players:4,player_count:1,cost_cents:null,duration_minutes:null}],rule:null});
      assert.match(html,/1 scheduled date</);
      assert.ok(html.includes(end.toISOString()) && !html.includes('TIME ONLY'));
      assert.match(html,/Cost not listed/);
      assert.match(html,/Duration not listed/);
      assert.match(html,/Court not listed/);
      assert.ok(!html.includes('Repeating schedule') && !html.includes('Free'));
    ''')
