"""Invite shortcuts preserve their player and choose a future shared time."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public/app-v15.js').read_text()


def options_for(host_slots, player_slots):
    sanitize = 'function sanitizePlannerInvitee(' + APP.split('  function sanitizePlannerInvitee(', 1)[1].split('  function sanitizeGameCreatePayload', 1)[0]
    helper = 'function playerInvitePlannerOptions(' + APP.split('  function playerInvitePlannerOptions(', 1)[1].split('  function patchVisibleFriendRelationship', 1)[0]
    script = f'''
      const {{pathToFileURL}} = require('node:url');
      (async()=>{{
        await import(pathToFileURL({json.dumps(str(ROOT / 'public/crew-planner-v15.js'))}).href);
        const RealDate = Date;
        global.Date = class extends RealDate {{
          constructor(...args) {{ super(...(args.length ? args : [new RealDate(2026, 8, 5, 15, 0).getTime()])); }}
        }};
        const window = globalThis;
        const state = {{me:{{id:1,availability:{json.dumps(host_slots)}}}}};
        {sanitize}
        {helper}
        const result = playerInvitePlannerOptions({{id:2,display_name:'Jordan',availability:{json.dumps(player_slots)}}});
        process.stdout.write(JSON.stringify(result));
      }})().catch(error=>{{console.error(error);process.exitCode=1;}});
    '''
    result = subprocess.run(['node', '-e', script], check=True, capture_output=True,
                            text=True, env={**os.environ, 'TZ': 'America/Los_Angeles'})
    return json.loads(result.stdout)


def test_shared_time_shortcut_prefers_tonight_over_a_passed_morning():
    result = options_for(['sat-am', 'sat-eve'], ['sat-am', 'sat-eve'])
    assert result['scheduledAt'] == '2026-09-06T01:00:00.000Z'
    assert result['visibility'] == 'private'
    assert result['inviteUserIds'] == [2]
    assert result['invitees'][0]['display_name'] == 'Jordan'


def test_passed_shared_time_rolls_forward_without_choosing_an_unshared_time():
    result = options_for(['sat-am', 'sat-eve'], ['sat-am'])
    assert result['scheduledAt'] == '2026-09-12T17:00:00.000Z'


def test_no_overlap_or_missing_schedule_keeps_normal_planner_choices():
    for host, player in [(['sat-am'], ['sat-eve']), ([], ['sat-am']), (['sat-am'], [])]:
        result = options_for(host, player)
        assert 'scheduledAt' not in result
        assert result['inviteUserIds'] == [2]


def test_planner_snapshots_preserve_numeric_ratings_and_do_not_invent_missing_levels():
    sanitize = 'function sanitizePlannerInvitee(' + APP.split('  function sanitizePlannerInvitee(', 1)[1].split('  function sanitizeGameCreatePayload', 1)[0]
    identity = 'const legacySelfRatingValue' + APP.split('  const legacySelfRatingValue', 1)[1].split('  function openThirdShotRatingExplainer', 1)[0]
    script = sanitize + identity + '''
      const esc = value => String(value);
      const player = sanitizePlannerInvitee({id:2,skill_level:'beginner',skill_rating:3.5,dupr_rating:4.125,dupr_id:'ABC123'});
      const restored = sanitizePlannerInvitee(JSON.parse(JSON.stringify(player)));
      const missing = sanitizePlannerInvitee({id:3});
      const legacy = sanitizePlannerInvitee({id:4,skill_level:'advanced'});
      process.stdout.write(JSON.stringify({
        identity:playerSkillIdentityHtml(player),restored:playerSkillIdentityHtml(restored),
        missing:playerSkillIdentityHtml(missing),legacy:playerSkillIdentityHtml(legacy)
      }));
    '''
    result = json.loads(subprocess.run(['node', '-e', script], check=True,
                                      capture_output=True, text=True).stdout)
    assert result['identity'] == 'Self-rating 3.5 · DUPR 4.125 · DUPR ID ABC123'
    assert result['restored'] == result['identity']
    assert result['missing'] == 'Self-rating not set'
    assert result['legacy'] == 'Self-rating 4.0 (from earlier level)'
