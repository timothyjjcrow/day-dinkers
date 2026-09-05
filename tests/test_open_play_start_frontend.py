"""Open-play shortcuts preserve the next court-local time, including clock changes."""
import json
import os
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def section(start, end):
    return APP[APP.index(start):APP.index(end, APP.index(start))]


def run(script, timezone='America/Los_Angeles'):
    return subprocess.run(['node', '-e', script], check=True, capture_output=True,
                          text=True, env={**os.environ, 'TZ': timezone})


def test_next_open_play_uses_court_timezone_without_skipping_a_start_that_is_soon():
    source = section('  function nextCourtOpenPlayStart(', '  function courtOpenPlayDuration(')
    script = source + '''
      const next=(weekday,start,now,zone)=>nextCourtOpenPlayStart({weekday,start},new Date(now),zone)?.toISOString()||null;
      process.stdout.write(JSON.stringify([
        next('sat','10:00','2026-09-05T16:40:00Z','America/Los_Angeles'),
        next('sat','10:00','2026-09-05T17:05:00Z','America/Los_Angeles'),
        next('sat','10:00','2026-09-05T03:00:00Z','Asia/Kathmandu'),
        next('sat','10:00','2026-09-05T16:40:00Z','Invalid/Timezone'),
        next('sat','29:65','2026-09-05T16:40:00Z','UTC'),
      ]));
    '''
    expected = ['2026-09-05T17:00:00.000Z', '2026-09-12T17:00:00.000Z',
                '2026-09-05T04:15:00.000Z', None, None]
    for browser_zone in ['America/Los_Angeles', 'Asia/Tokyo', 'Europe/London']:
        assert json.loads(run(script, browser_zone).stdout) == expected


def test_open_play_clock_changes_match_the_recurrence_first_fold_and_skip_gap_rules():
    source = section('  function nextCourtOpenPlayStart(', '  function courtOpenPlayDuration(')
    run("const assert=require('node:assert/strict');" + source + '''
      const next=(start,now,zone)=>nextCourtOpenPlayStart({weekday:'sun',start},new Date(now),zone)?.toISOString();
      assert.equal(next('02:30','2026-03-07T12:00:00Z','America/Los_Angeles'),'2026-03-15T09:30:00.000Z');
      assert.equal(next('01:30','2026-10-31T12:00:00Z','America/Los_Angeles'),'2026-11-01T08:30:00.000Z');
      assert.equal(next('01:30','2026-11-01T09:00:00Z','America/Los_Angeles'),'2026-11-08T09:30:00.000Z');
      assert.equal(next('02:15','2026-10-03T00:00:00Z','Australia/Lord_Howe'),'2026-10-10T15:15:00.000Z');
    ''')


def test_supplied_planner_times_remain_exact_even_when_they_are_not_suggestions():
    source = section('  function plannerInitialTimeSelection(', '  async function openNewGameModal(')
    run("const assert=require('node:assert/strict');" + source + '''
      const now=new Date('2026-09-05T09:40:00');
      const days=Array.from({length:8},(_,i)=>new Date(2026,8,5+i));
      const presets=Array.from({length:31},(_,i)=>6+i/2);
      const select=value=>plannerInitialTimeSelection(value,days,presets,now);
      for(const value of ['2026-09-05T09:41:00','2026-09-05T10:00:00','2026-09-05T10:10:00','2026-10-05T10:00:00']){
        const result=select(value);
        assert.equal(result.date.getTime(),new Date(value).getTime());
        assert.equal(result.preset,false);
      }
      assert.equal(select('2026-09-06T10:30:00').preset,true);
      assert.equal(select('2026-09-05T09:30:00'),null);
      assert.equal(select('invalid'),null);
      assert.equal(select(null),null);
    ''')


def test_repeat_end_date_uses_the_schedule_calendar_in_both_creation_and_editing():
    source = section('  function calendarDateInTimeZone(', '  function businessScheduleItemIsCurrent(')
    run("const assert=require('node:assert/strict');" + source + '''
      const start=new Date('2026-09-05T23:10:00Z');
      assert.equal(calendarDateInTimeZone(start,'Asia/Tokyo'),'2026-09-06');
      assert.equal(calendarDateInTimeZone(start,'America/Los_Angeles'),'2026-09-05');
      assert.ok('2026-09-05' < calendarDateInTimeZone(start,'Asia/Tokyo'));
      assert.ok('2026-09-06' >= calendarDateInTimeZone(start,'Asia/Tokyo'));
    ''')
    assert 'calendarDateInTimeZone(scheduledAt, recurrenceTimezone)' in APP
    assert 'calendarDateInTimeZone(when, editRecurrenceTimezone)' in APP


def test_schedule_banner_does_not_claim_previous_teammates_will_be_invited():
    source = section('    const updateCrewPresetBanner = () => {', '    const syncAudienceChoices = () => {')
    run("const assert=require('node:assert/strict');" + '''
      const title={textContent:''},copy={textContent:''};
      const modal={querySelector:selector=>selector==='#ng-crew-title'?title:copy};
      const crewId=null,crewName='',sourceGameId=null,sourceLabel='Sun open-play schedule';
      const visibility='open',inviteIds=new Set();
    ''' + source + '''
      updateCrewPresetBanner();
      assert.equal(title.textContent,'Sun open-play schedule');
      assert.equal(copy.textContent,'Review the time and details before scheduling.');
      inviteIds.add(2);updateCrewPresetBanner();
      assert.equal(copy.textContent,'1 selected player will get a direct invitation.');
    ''')
