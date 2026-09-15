"""Only edited values are submitted; compact summaries preserve expanded detail."""
from tests.test_product_audit_play_frontend import functions_between, run_js


def test_unchanged_defaults_and_unrelated_topics_are_omitted_but_clears_are_kept():
    source=functions_between('function courtCorrectionChanges(', 'function openSuggestEditSheet(')
    result=run_js(source+'''
    const initial={num_courts:1,fees:'$5',hours:'Daily',indoor:true,visitor_info:{parking:'North'},open_play_schedule_rows:[{weekday:'mon',start:'09:00',end:'11:00'}]};
    const values=JSON.parse(JSON.stringify(initial));values.fees='';values.indoor=false;
    console.log(JSON.stringify(courtCorrectionChanges(initial,values)));
    ''')
    assert result=={'fees':'','indoor':False}


def test_open_play_entry_uses_times_without_losing_the_full_accessible_description():
    source=functions_between('function courtOpenPlayTodayFact(', 'function courtOpenPlayScheduleHtml(')
    result=run_js('''
    const courtOpenPlayRows=c=>c.rows,courtTodayKey=()=> 'tue',compactCourtFact=x=>x;
    const courtTimeRangeLabel=(a,b)=>`${a}–${b}`;
    const courtOpenPlayRowLabel=row=>`${row.start}–${row.end} · ${row.level} · ${row.cost} · ${row.notes}`;
    '''+source+'''
    const row={weekday:'tue',start:'09:00',end:'11:00',level:'All levels',cost:'$5',notes:'Pay at the front desk'};
    console.log(JSON.stringify({one:courtOpenPlayTodayFact({rows:[row]}),two:courtOpenPlayTodayFact({rows:[row,{...row,start:'13:00',end:'15:00'}]})}));
    ''')
    assert result['one']['label']=='09:00–11:00'
    assert 'Pay at the front desk' in result['one']['raw']
    assert result['two']['label']=='2 times today'
