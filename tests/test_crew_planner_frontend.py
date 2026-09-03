"""Deterministic contracts for the browser-side same-crew scheduler."""

import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'public' / 'crew-planner-v15.js'
APP = (ROOT / 'public' / 'app-v15.js').read_text()


def run_planner(expression):
    script = f"""
      const {{ pathToFileURL }} = require('node:url');
      (async () => {{
        await import(pathToFileURL({json.dumps(str(MODULE))}).href);
        const planner = globalThis.CrewPlanner;
        const result = ({expression});
        process.stdout.write(JSON.stringify(result));
      }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
    """
    env = {**os.environ, 'TZ': 'America/Los_Angeles'}
    result = subprocess.run(
        ['node', '-e', script], check=True, capture_output=True, text=True, env=env,
    )
    return json.loads(result.stdout)


def test_crew_slot_requires_host_compatible_highest_coverage():
    result = run_planner("""
      planner.bestSlot([
        {id: 1, availability: ['sat-am']},
        {id: 2, availability: ['wed-eve', 'sat-am']},
        {id: 3, availability: ['wed-eve']},
        {id: 4, availability: ['wed-eve']},
      ], {hostId: 1, now: new Date(2026, 7, 29, 8, 0), minLeadMinutes: 50})
    """)
    assert result['slot'] == 'sat-am'
    assert result['coverage'] == 2
    assert result['total'] == 4
    assert result['usedFallback'] is False


def test_crew_slot_tie_breaks_by_nearest_future_occurrence():
    result = run_planner("""
      planner.bestSlot([
        {id: 1, availability: []},
        {id: 2, availability: ['fri-pm', 'tue-am']},
        {id: 3, availability: ['tue-am', 'fri-pm']},
      ], {hostId: 1, now: new Date(2026, 7, 31, 9, 0)})
    """)
    assert result['slot'] == 'tue-am'
    assert result['coverage'] == 2


def test_passed_same_day_slot_rolls_to_next_week_but_future_slot_stays_today():
    passed = run_planner("""
      (() => {
        const d = planner.nextOccurrence('mon-eve', new Date(2026, 7, 31, 19, 0), 50);
        return {year: d.getFullYear(), month: d.getMonth(), day: d.getDate(), hour: d.getHours()};
      })()
    """)
    assert passed == {'year': 2026, 'month': 8, 'day': 7, 'hour': 18}

    future = run_planner("""
      (() => {
        const d = planner.nextOccurrence('mon-eve', new Date(2026, 7, 31, 16, 0), 50);
        return {month: d.getMonth(), day: d.getDate(), hour: d.getHours()};
      })()
    """)
    assert future == {'month': 7, 'day': 31, 'hour': 18}


def test_invalid_duplicate_and_unknown_availability_has_honest_fallback():
    result = run_planner("""
      planner.bestSlot([
        {id: 1, availability: []},
        {id: 1, availability: ['sat-am']},
        {id: 2, availability: ['not-a-slot', 'tue-pm', 'tue-pm']},
        {id: 3},
      ], {hostId: 1, now: new Date(2026, 7, 31, 9, 0)})
    """)
    assert result['slot'] == 'tue-pm'
    assert result['coverage'] == 1
    assert result['total'] == 3

    fallback = run_planner("""
      planner.bestSlot([{id: 1}, {id: 2, availability: []}], {
        hostId: 1,
        now: new Date(2026, 7, 31, 9, 0),
        fallbackScheduledAt: new Date(2026, 7, 25, 14, 0),
      })
    """)
    assert fallback['slot'] == 'tue-pm'
    assert fallback['coverage'] == 0
    assert fallback['total'] == 2
    assert fallback['usedFallback'] is True


def test_postgame_ctas_open_the_reviewable_planner_before_any_mutation():
    assert 'id="cel-play-again"' in APP
    assert 'id="gs-play-again"' in APP
    assert APP.count('openPostGamePlanner(') >= 3
    planner = APP[APP.index('async function openPostGamePlanner'):APP.index('function completedCrewConnectionsHtml')]
    assert "crewRequest || api(`/games/${game.id}/crew`)" in planner
    assert "method: 'POST'" not in planner
    assert 'invitees: invitePeople.filter((person) => inviteIds.has(person.id))' in APP
    assert 'require_all_invitees: visibility === \'private\' && requireAllInvitees' in APP
    assert "err.code === 'crew_changed'" in APP
    assert "for (let i = 0; i < 8; i++)" in APP
    assert 'let plannerDirty = false;' in APP
    assert 'const plannedPlayerCount = inviteIds.size + 1;' in APP
    assert 'plannedPlayerCount > effectiveCapacity' in APP
    assert "title.textContent = 'Same players as last time';" in APP
    assert 'id="ng-save-group"' in APP
    assert 'Group invitations are sent only after you schedule this game.' in APP


def test_uncertain_create_recovery_replays_immutable_payload_across_reload():
    assert "const submittedPayload = sanitizeGameCreatePayload(raw.submittedPayload, clientAttemptId)" in APP
    assert "submittedPayload: status === 'submitting' ? frozenSubmitPayload : null" in APP
    assert "persistent.setItem(key, value);" in APP
    assert "fallback.setItem(key, value);" in APP
    assert 'id="ng-retry-exact">Try same plan again</button>' in APP
    assert "body: JSON.stringify(requestPayload)" in APP
    assert "const exactPayload = exactRetry" in APP
    assert "if (!exactRetry && scheduledAt.getTime() <= Date.now())" in APP
    assert 'data-mode="now"' not in APP
    assert "[408, 425, 429].includes(Number(err.status))" in APP
    assert "err.data && err.data.existing_game_id" in APP
    assert "plannerAttemptId = newGameAttemptId()" not in APP
    assert "status !== 'submitting' && Date.now() - raw.updatedAt > GAME_DRAFT_TTL" in APP
    assert "if (status === 'submitting' && !clientAttemptId) return null;" in APP
    assert "plannerSubmitting && !frozenSubmitPayload && restoredDraft && restoredDraft.scheduledAt" in APP
    assert "function readGameDrafts(userId" in APP
    assert "if (a.status !== b.status) return a.status === 'submitting' ? -1 : 1;" in APP
    assert "if (!flushPlannerDraft('submitting'))" in APP
    assert 'ng-review-retry' not in APP
    assert 'ambiguousDraftAccepted' not in APP
    assert "showPlannerAttemptRecovery(" in APP
    assert "clearGameDraft(plannerAttemptId);" in APP


def test_play_again_never_creates_a_game_or_group_before_schedule_submit():
    planner = APP[APP.index('async function openPostGamePlanner'):APP.index('function completedCrewConnectionsHtml')]
    assert "crewRequest || api(`/games/${game.id}/crew`)" in planner
    assert "method: 'POST'" not in planner
    assert 'options.offerSaveGroup = !savedGroup;' in planner
    assert 'openNewGameModal(options)' in planner
    assert 'rematchAttemptKey' not in APP
    assert 'gs-rematch' not in APP
    assert "api(`/games/${sourceGameId}/crew`, {" in APP
    assert "body: JSON.stringify({ name: saveGroupName })" in APP
