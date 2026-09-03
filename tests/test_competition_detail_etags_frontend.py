"""Static and runtime contracts for bandwidth-safe competition detail polling."""

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()


def section(start, end):
    return APP[APP.index(start):APP.index(end, APP.index(start))]


def test_api_exposes_etag_metadata_and_short_circuits_304_before_json():
    api = section('async function api(path, options = {})', '// Password and MFA mutations')
    assert "ifNoneMatch = ''" in api
    assert 'responseMeta = false' in api
    assert "headers['If-None-Match'] = ifNoneMatch" in api
    assert "const responseEtag = String(res.headers.get('ETag')" in api
    not_modified = api.index('if (res.status === 304)')
    json_read = api.index('data = await res.json()')
    error_boundary = api.index('if (!res.ok)')
    assert not_modified < json_read < error_boundary
    assert 'notModified: true' in api
    assert 'notModified: false' in api


def test_league_and_tournament_polls_revalidate_and_skip_unchanged_renders():
    league = section('async function openLeagueScreen', 'function openEditLeagueSheet')
    tournament = section('async function openTournamentScreen', 'function openEditTournamentSheet')
    for detail in (league, tournament):
        assert "let detailEtag = '';" in detail
        assert 'responseMeta: true' in detail
        assert 'ifNoneMatch: detailEtag' in detail
        assert 'notModified = detailResponse.notModified;' in detail
        assert 'if (notModified) return' in detail
        assert detail.index('if (notModified) return') < detail.index(
            'render(fresh, { preserve: true });'
        )
        assert 'JSON.stringify(fresh)' not in detail
    assert 'const detailPath = `/leagues/${leagueId}' in league
    assert 'const detailPath = `/tournaments/${tournamentId}`;' in tournament


def test_refresh_guard_runtime_serializes_polls_and_rejects_stale_generations():
    guard_source = section(
        'function createCompetitionRefreshGuard()',
        'function openCompetitionResultSheet',
    )
    script = f"""
{guard_source}
const guard = createCompetitionRefreshGuard();
const first = guard.begin({{ poll: true }});
const blocked = guard.begin({{ poll: true }});
const ownedBeforeMutation = guard.owns(first);
guard.invalidate();
const ownedAfterMutation = guard.owns(first);
const blockedWhileStaleRequestFinishes = guard.begin({{ poll: true }});
guard.finish(first);
const second = guard.begin({{ poll: true }});
const secondOwned = guard.owns(second);
const forced = guard.begin();
const secondSuperseded = !guard.owns(second) && guard.owns(forced);
guard.finish(first);
const stillBlockedBySecond = guard.begin({{ poll: true }});
guard.finish(second);
const third = guard.begin({{ poll: true }});
console.log(JSON.stringify({{
  first: !!first,
  blocked: blocked === null,
  ownedBeforeMutation,
  ownedAfterMutation,
  blockedWhileStaleRequestFinishes: blockedWhileStaleRequestFinishes === null,
  secondOwned,
  secondSuperseded,
  stillBlockedBySecond: stillBlockedBySecond === null,
  thirdOwned: guard.owns(third),
}}));
"""
    result = subprocess.run(
        ['node', '-e', script], check=True, capture_output=True, text=True,
    )
    assert json.loads(result.stdout) == {
        'first': True,
        'blocked': True,
        'ownedBeforeMutation': True,
        'ownedAfterMutation': False,
        'blockedWhileStaleRequestFinishes': True,
        'secondOwned': True,
        'secondSuperseded': True,
        'stillBlockedBySecond': True,
        'thirdOwned': True,
    }


def test_each_competition_screen_rechecks_request_ownership_after_await():
    league = section('async function openLeagueScreen', 'function openEditLeagueSheet')
    tournament = section('async function openTournamentScreen', 'function openEditTournamentSheet')

    for detail, model_assignment in ((league, 'lg = fresh;'), (tournament, 't = fresh;')):
        assert 'const refreshGuard = createCompetitionRefreshGuard();' in detail
        assert 'const refreshTicket = refreshGuard.begin({ poll: isPoll });' in detail
        assert 'if (!refreshTicket) return null;' in detail
        assert 'refreshGuard.finish(refreshTicket)' in detail
        assert 'if (busy) refreshGuard.invalidate();' in detail
        ownership_check = detail.index('!refreshGuard.owns(refreshTicket)')
        assignment = detail.index(model_assignment, ownership_check)
        assert ownership_check < assignment
        assert 'isPoll && !competitionOverlayCanRefresh(box)' in detail[
            ownership_check:assignment
        ]
        assert detail.index('let nextEtag = detailEtag;') < ownership_check
        assert ownership_check < detail.index('detailEtag = nextEtag;')
