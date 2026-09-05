"""A recurring wall-clock time stays in its stated zone when displayed."""
import json
import os
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def test_recurrence_time_is_readable_without_converting_the_standing_local_hour():
    source = 'function recurrenceClockLabel(' + APP.split('  function recurrenceClockLabel(', 1)[1].split('  function gameScreenHtml', 1)[0]
    script = source + '''
      process.stdout.write(JSON.stringify([
        recurrenceClockLabel({recurrence_local_time:'10:00',recurrence_timezone:'America/Los_Angeles'}),
        recurrenceClockLabel({recurrence_local_time:'18:30:00',recurrence_timezone:'America/New_York'}),
        recurrenceClockLabel({recurrence_local_time:'00:00',recurrence_timezone:'UTC'}),
        recurrenceClockLabel({recurrence_local_time:'29:65',recurrence_timezone:'UTC'}),
        recurrenceClockLabel({recurrence_local_time:null}),
        recurrenceClockLabel({recurrence_local_time:'10:00',recurrence_timezone:'Unknown/Legacy_Zone'}),
      ]));
    '''
    results = []
    for timezone in ['America/Los_Angeles', 'Asia/Tokyo']:
        result = subprocess.run(['node', '-e', script], check=True, capture_output=True,
                                text=True, env={**os.environ, 'TZ': timezone, 'LANG':'en_US.UTF-8'})
        results.append(json.loads(result.stdout))
    assert results[0] == results[1]
    morning, evening, midnight, invalid, missing, legacy = results[0]
    assert '10:00' in morning and 'AM' in morning and morning.endswith('PT')
    assert '6:30' in evening and 'PM' in evening and evening.endswith('ET')
    assert '12:00' in midnight and 'AM' in midnight and midnight.endswith(('GMT', 'GMT+0', 'UTC'))
    assert invalid == missing == ''
    assert 'Unknown/Legacy Zone' in legacy
