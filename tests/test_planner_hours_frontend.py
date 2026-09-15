"""Time advice and recurrence summaries retain the player's actual selection."""
import json
import subprocess
from pathlib import Path

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def test_repeat_summary_uses_series_zone_and_order_not_browser_clock():
    source=APP.split('    const plannerRepeatText = ',1)[1].split('    const updatePlannerSummary = ',1)[0]
    script='''
      const chosenPlannerTime=()=>new Date('2026-09-16T01:00:00Z');
      const recurrenceTimezone='America/Los_Angeles';
      const recurrenceDayKeys=['sun','mon','tue','wed','thu','fri','sat'];
      const recurrenceWeekdays=new Set(['thu','tue']);
      const recurrenceEndsOn='2026-10-01';
      const plannerTimeZoneLabel=()=> 'Pacific Time';
      const plannerRepeatText='''+source+'''
      console.log(JSON.stringify([plannerRepeatText(),plannerRepeatText({includeTime:false})]));
    '''
    result=subprocess.run(['node','-e',script],capture_output=True,text=True,check=True)
    full,compact=json.loads(result.stdout)
    assert full == 'Every Tue, Thu · 6:00 PM · Pacific Time · Until Oct 1, 2026'
    assert compact == 'Every Tue, Thu · Pacific Time · Until Oct 1, 2026'


def test_hours_advice_guards_late_results_and_preserves_manual_time():
    code=APP.split('    let hoursTimer = null, hoursSequence = 0;',1)[1].split('    // The accepted group snapshot',1)[0]
    assert "sequence === hoursSequence && modal.isConnected && state.me?.id === hoursViewer" in code
    assert 'duration_minutes: duration' in code
    assert 'conflicts.has(button.dataset.smartTime)' in code
    assert 'button.remove()' in code
    assert 'exactTimeInput.value =' not in code
    assert 'Checking court hours…' in code
    assert 'Couldn’t check court hours.' in code
    assert "{ retry: true }" in code
    assert "hoursSequence += 1" in code
