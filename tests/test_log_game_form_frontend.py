from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def log_game_source():
    start = APP.index("async function openLogGameSheet")
    end = APP.index("async function openNewGameModal", start)
    return APP[start:end]


def test_past_game_logger_is_a_real_keyboard_submittable_form():
    source = log_game_source()
    assert '<form id="lg-form" novalidate>' in source
    assert 'type="submit" class="btn btn-primary btn-block" id="lg-submit"' in source
    assert "querySelector('#lg-form').addEventListener('submit'" in source
    assert "event.preventDefault();" in source
    assert "querySelector('#lg-submit').addEventListener('click'" not in source


def test_past_game_validation_is_persistent_and_linked_to_the_field():
    source = log_game_source()
    assert "let submitErrorTarget = null;" in source
    assert "control.setAttribute('aria-invalid', 'true');" in source
    assert "describedBy.add(submitState.id);" in source
    assert "Pick a court before saving the result." in source
    assert "Scores can’t be tied." in source
    assert "toast('Pick a court')" not in source
    assert "el.dispatchEvent(new Event('input', { bubbles: true }));" in source


def test_past_game_uncertain_save_recovery_remains_intact():
    source = log_game_source()
    assert "logAttemptAmbiguous" in source
    assert "setLogInputsLocked(true);" in source
    assert "Try same result again" in source
    assert "client_attempt_id: logAttemptId" in source


def test_past_game_records_and_validates_the_actual_played_time():
    source = log_game_source()
    assert 'id="lg-played-at"' in source
    assert 'max="${logDateTimeValue(logNow)}"' in source
    assert "played_at: playedAt.toISOString()" in source
    assert "playedAt.getTime() > Date.now() + 5 * 60000" in source
    assert "Use the actual start time so your history stays in order." in source
