"""Execute timing labels and the two-step schedule-delay interaction."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public/app-v15.js').read_text()

def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]

def run(script):
    subprocess.run(['node','-e',script],cwd=ROOT,check=True,capture_output=True,text=True)

def test_bracket_and_schedule_distinguish_estimated_called_playing_and_final():
    run('''const assert=require('node:assert/strict');
      require('./public/tournament-bracket-v15.js'); const bracket=globalThis.TournamentBracket;
      const esc=String,uiIcon=()=>'',fmtDateTime=x=>x;
      const normalizeCompetitionResult=m=>({terminal:m.result_state==='confirmed'});
    ''' + section('function tournamentMatchScheduleHtml', 'function tournamentGameScoresText') + '''
      const match={entry1_id:1,entry2_id:2,scheduled_at:'tomorrow at 9',court_number:2};
      assert.equal(bracket.resultMeta(match).label,'Estimated');
      assert.match(tournamentMatchScheduleHtml(match),/Estimated.*tomorrow at 9/);
      for(const [state,label] of [['called','Called to court'],['playing','Playing now']]) {
        const value={...match,play_state:state};
        assert.equal(bracket.resultMeta(value).label,label);
        assert.match(tournamentMatchScheduleHtml(value),new RegExp(label));
        assert.ok(!tournamentMatchScheduleHtml(value).includes('tomorrow at 9'));
      }
      assert.equal(bracket.resultMeta({...match,play_state:'playing',result_state:'confirmed'}).label,'Final score');
    ''')

def test_delay_requires_reviewed_confirmation_before_mutation():
    run('''const assert=require('node:assert/strict');
      (async()=>{
      let callback, allow=false, saved=null, closed=0;
      const button={},field={value:'15',classList:{remove:()=>{}}};
      const modal={querySelector:selector=>selector==='#tdl-form'?{addEventListener:(type,fn)=>callback=fn}:selector==='#tdl-review'?button:field};
      const openModal=()=>modal,modalHead=()=>'',beginButtonAction=()=>()=>{},toast=()=>{};
      const closeModal=()=>closed++,openActionConfirmation=async()=>allow;
      const calls=[];
      const api=async(path,options)=>{const body=JSON.parse(options.body);calls.push({path,body});return body.preview?{match_count:3,notification_count:8}:{id:7,schedule_version:5};};
    ''' + section('function openTournamentDelaySheet','async function openTournamentScreen') + '''
      openTournamentDelaySheet({id:7,schedule_version:4},fresh=>saved=fresh);
      assert.equal(calls.length,0);
      await callback({preventDefault(){}});
      assert.equal(calls.length,1);assert.equal(calls[0].body.preview,true);assert.equal(saved,null);
      allow=true;await callback({preventDefault(){}});
      assert.equal(calls.length,3);assert.equal(calls[2].path,'/tournaments/7/schedule/delay');
      assert.deepEqual(calls[2].body,{minutes:15,expected_schedule_version:4});
      assert.equal(saved.schedule_version,5);assert.equal(closed,1);
      })().catch(error=>{console.error(error);process.exit(1)});
    ''')
