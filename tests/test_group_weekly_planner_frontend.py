"""The single group planner retains consent, roster, and shared local time."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public/app-v15.js').read_text()


def group_options(with_preferences=True):
    def section(start, end):
        return APP[APP.index(start):APP.index(end, APP.index(start))]
    source = '\n'.join([
        section('  function sanitizePlannerInvitee(', '  function sanitizeGameCreatePayload'),
        section('  function completedCrewPlannerOptions(', '  async function openPostGamePlanner'),
        section('  function crewPlannerOptions(', '  function renderCrewInvitationConsent'),
    ])
    script = f'''
      const {{pathToFileURL}}=require('node:url');
      (async()=>{{
        await import(pathToFileURL({json.dumps(str(ROOT / 'public/crew-planner-v15.js'))}).href);
        const RealDate=Date;
        global.Date=class extends RealDate {{constructor(...args){{super(...(args.length?args:[new RealDate(2026,8,5,15,0).getTime()]));}}}};
        const window=globalThis;
        const slots={str(with_preferences).lower()}?['sat-am','sat-eve']:[];
        const state={{me:{{id:1,availability:slots}}}};
        const crewSummaryFrom=value=>value?.id?value:null;
        {source}
        const result=crewPlannerOptions({{
          id:99,name:'Weekend regulars',roster_version:2,default_court_id:7,
          default_court_name:'Sunset Park',members:[state.me,{{id:2,availability:slots}},{{id:3,availability:[]}}],
          pending_invites:[{{user:{{id:4,availability:slots}}}}]
        }});
        process.stdout.write(JSON.stringify(result));
      }})().catch(error=>{{console.error(error);process.exitCode=1;}});
    '''
    return json.loads(subprocess.run(['node','-e',script],check=True,capture_output=True,
                                    text=True,env={**os.environ,'TZ':'America/Los_Angeles'}).stdout)


def test_group_planner_keeps_accepted_roster_and_future_shared_local_day():
    result = group_options()
    assert result['scheduledAt'] == '2026-09-06T01:00:00.000Z'
    assert result['inviteUserIds'] == [2, 3]
    assert result['crewId'] == 99 and result['crewVersion'] == 2
    assert result['visibility'] == 'private'
    assert result['gameType'] == 'casual' and result['sessionMode'] is True
    assert result['court']['id'] == 7
    assert '2 of 3 usually play then' in result['availabilityLabel']


def test_group_planner_without_preferences_leaves_time_choice_to_planner():
    result = group_options(False)
    assert result['scheduledAt'] is None
    assert 'recurrenceWeekdays' not in result
    assert result['inviteUserIds'] == [2, 3]
