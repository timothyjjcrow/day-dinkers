"""Frontend contracts for honest pickleball results and group-session wrap-up."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_both_score_forms_start_blank_and_reject_blank_before_number_coercion():
    logger = section('async function openLogGameSheet', 'async function openNewGameModal')
    scorer = section('function openScoreModal', '// ---------- Tournaments ----------')

    assert 'id="lg-s1" min="0" max="99"' in logger
    assert 'id="lg-s2" min="0" max="99"' in logger
    assert 'id="lg-s1" value=' not in logger
    assert 'id="lg-s2" value=' not in logger
    assert '!score1Input.value.trim() || !Number.isInteger(s1)' in logger
    assert '!score2Input.value.trim() || !Number.isInteger(s2)' in logger

    assert 'id="sc-${index + 1}-1" min="0" max="99" inputmode="numeric" required' in scorer
    assert 'id="sc-${index + 1}-2" min="0" max="99" inputmode="numeric" required' in scorer
    assert "[{ score_team1: '', score_team2: '' }]" in scorer
    assert 'if (!row.score_team1 || !Number.isInteger(s1)' in scorer
    assert 'if (!row.score_team2 || !Number.isInteger(s2)' in scorer
    assert 'Game ${index + 1} cannot end tied.' in scorer


def test_match_score_form_supports_accessible_repeatable_game_rows():
    scorer = section('function openScoreModal', '// ---------- Tournaments ----------')

    assert 'id="sc-games" class="score-series" aria-live="polite"' in scorer
    assert '<fieldset class="score-series-game"' in scorer
    assert '<legend>Game ${index + 1}</legend>' in scorer
    assert 'aria-label="Decrease ${esc(teamNames(1))} score for game ${index + 1}"' in scorer
    assert 'aria-label="Increase ${esc(teamNames(2))} score for game ${index + 1}"' in scorer
    assert 'id="sc-add-game"' in scorer
    assert 'data-remove-score-game' in scorer
    assert 'scoreRows.length >= 5' in scorer
    assert "score_games: scoreGames" in scorer
    assert "if (wins1 === wins2)" in scorer
    assert "The match is tied. Add the deciding game or correct a score." in scorer


def test_unusual_scores_require_explicit_review_or_literal_acknowledgement():
    helper = section('function isConfirmableNonstandardScore', '// Log a spontaneous')
    logger = section('async function openLogGameSheet', 'async function openNewGameModal')
    scorer = section('function openScoreModal', '// ---------- Tournaments ----------')

    assert "error?.code === 'nonstandard_pickleball_score'" in helper
    assert 'error?.data?.can_confirm === true' in helper
    assert 'Standard games end at 11, 15, or 21 and must be won by two.' in helper
    for source in (logger, scorer):
        assert 'isConfirmableNonstandardScore(' in source
        assert 'confirmNonstandardScore({' in source
        assert "...(acceptNonstandard ? { accept_nonstandard_score: true } : {})" in source
        assert 'sendLogRequest(true)' in source or 'sendScoreRequest(true)' in source

    first_log_request = logger[logger.index('const sendLogRequest'):logger.index('await sendLogRequest();')]
    first_score_request = scorer[scorer.index('const sendScoreRequest'):scorer.index('await sendScoreRequest();')]
    assert 'accept_nonstandard_score: true' in first_log_request
    assert 'accept_nonstandard_score: true' in first_score_request
    assert 'await sendLogRequest();' in logger
    assert 'await sendScoreRequest();' in scorer


def test_larger_sessions_use_attendee_wrap_up_instead_of_a_forged_score():
    wrap = section('function openSessionWrapUpModal', 'function openScoreModal')
    scorer = section('function openScoreModal', '// ---------- Tournaments ----------')
    detail = section('function gameScreenHtml', 'async function openGameScreen')
    screen = section('async function openGameScreen', 'function safeNotificationOverlayRoute')

    assert 'Choose who played.' in wrap
    assert 'no score, winner, loss, or rating change' in wrap
    assert "JSON.stringify({ attendee_user_ids: attendeeIds })" in wrap
    assert "api(`/games/${game.id}/complete-session`" in wrap
    assert 'attendeeIds.length < 2' in wrap
    assert "Number(game.max_players) > 4" in scorer
    assert 'return openSessionWrapUpModal(game, refresh);' in scorer
    assert 'id="gs-wrap-session"' in detail
    assert 'id="gs-score"' in detail
    assert "querySelector('#gs-score, #gs-wrap-session')" in screen


def test_completed_sessions_never_render_null_scores_or_match_outcomes():
    cards = section('function gameCardHtml', 'function showJoinedToast')
    share = section('function gameShareText', 'async function shareGame')
    rows = section('function resultRowHtml', 'function upcomingDayLabel')
    detail = section('function gameScreenHtml', 'async function openGameScreen')
    fingerprint = section('function gameFingerprint', 'function gameScreenHtml')

    for source in (cards, share, rows, detail):
        assert "game.completion_kind === 'session'" in source
    assert 'Session complete' in cards
    assert 'Wrapped up a ${game.players.length}-player pickleball session' in share
    assert 'Session complete' in rows
    assert 'Session complete' in detail
    assert 'game.can_complete_session' in fingerprint
    assert 'game.completion_kind' in fingerprint
    assert "game.completion_kind === 'session' || [1, 2].includes(player.team)" in detail
    assert "const votables = game.completion_kind === 'session' ? []" in detail
