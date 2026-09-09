"""The displayed correction and its exact version drive result decisions."""
import json
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def part(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_provenance_renders_each_snapshot_and_escapes_user_content():
    helper = part('function scoreHistoryHtml(', 'function gameScreenHtml(')
    script = "const esc=x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');const fmtDateTime=x=>x;" + helper + """
      const history = [{kind:'reported',at:'first',actor_name:'<Alex>',score_team1:11,score_team2:7,sides:[{team:1,name:'<Alex>'},{team:2,name:'Jordan'}]},
      {kind:'late_disputed',at:'second',actor_name:'Jordan',reason:'<script>bad</script>',score_team1:11,score_team2:7},
      {kind:'correction_confirmed',at:'third',actor_name:'Alex',score_team1:7,score_team2:11}];
      console.log(JSON.stringify([scoreHistoryHtml({score_history:history}), scoreHistoryHtml({})]));
    """
    rendered, empty = json.loads(subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True).stdout)
    assert 'Score reported · 11–7' in rendered and 'Correction agreed · 7–11' in rendered
    assert '<script>' not in rendered and '&lt;Alex&gt;' in rendered and 'vs Jordan' in rendered
    assert empty == ''


def test_corrected_score_copy_never_implies_timeout_agreement():
    helper = part('function scoreAutoConfirmCopy(', 'function fmtMessageTimestamp(')
    script = "const fmtDateTime=x=>x;" + helper + "console.log(JSON.stringify([scoreAutoConfirmCopy({score_correction_pending:true,ranked_correction_deadline_at:'Friday'}),scoreAutoConfirmCopy({score_correction_pending:true,ranked_correction_expired:true})]));"
    pending, expired = json.loads(subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True).stdout)
    assert 'opponent must agree by Friday' in pending and 'No automatic confirmation' in pending
    assert 'expired' in expired and 'unrated' in expired


def test_closed_correction_reason_distinguishes_expiry_limit_and_unavailable():
    helper = part('function scoreCorrectionStatusHtml(', 'function scoreHistoryHtml(')
    script = "const esc=String;const fmtDateTime=x=>x;" + helper + """
      console.log(JSON.stringify([
        {ranked_correction_expired:true}, {score_correction_attempts:2},
        {score_correction_attempts:0},
        {can_propose_score_correction:true,score_correction_attempts:1,ranked_correction_deadline_at:'Friday'}
      ].map(scoreCorrectionStatusHtml)));
    """
    expired, exhausted, unavailable, open_state = json.loads(subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True).stdout)
    assert '7-day correction window has ended' in expired and 'gs-correct-result' not in expired
    assert 'two correction proposals have been used' in exhausted and 'gs-result-help' in exhausted
    assert 'unavailable for this match' in unavailable and 'proposals have been used' not in unavailable
    assert '1 proposal left' in open_state and 'agree by Friday' in open_state and 'gs-correct-result' in open_state


def test_score_forms_preserve_sides_versions_and_button_across_async_reason_sheet():
    editor = part('function openScoreModal(', 'function tournamentDivisionLabel(')
    assert 'expected_score_version: Number(game.score_version || 0)' in editor
    assert 'singles || lockedSides' in editor and '!singles && !lockedSides' in editor
    assert 'Number(fresh.score_version || 0) !== Number(game.score_version || 0)' in editor
    assert 'querySelector(`[data-score-row=' not in editor
    assert 'querySelector(`input[data-score-row=' in editor
    for start, end in [("box.querySelector('#gs-dispute')", "box.querySelector('#gs-late-dispute')"),
                       ("box.querySelector('#gs-late-dispute')", "box.querySelector('#gs-fix-completed-score')")]:
        handler = part(start, end)
        assert handler.index('const disputeButton = event.currentTarget') < handler.index('await requestScoreDisputeReason')
        assert 'beginButtonAction(event.currentTarget' not in handler
        assert 'expected_score_version: Number(game.score_version || 0)' in handler
    assert "action('score-confirm', 'Review score', true)" in APP
