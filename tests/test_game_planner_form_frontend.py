from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def planner_source():
    start = APP.index("async function openNewGameModal")
    end = APP.index("async function renderTournaments", start)
    return APP[start:end]


def test_planner_is_a_semantic_form_with_native_submit_behavior():
    planner = planner_source()
    assert '<form id="ng-form" novalidate>' in planner
    assert 'type="submit" class="btn btn-primary btn-block" id="ng-submit"' in planner
    assert "plannerForm.addEventListener('submit', async (event) =>" in planner
    assert "event.preventDefault();" in planner
    assert "querySelector('#ng-submit').addEventListener('click'" not in planner


def test_planner_links_persistent_errors_to_the_invalid_control():
    planner = planner_source()
    assert 'id="ng-submit-error" role="alert" aria-live="assertive"' in planner
    assert "control.setAttribute('aria-invalid', 'true')" in planner
    assert "describedBy.add(error.id)" in planner
    assert "plannerSubmitErrorTarget.contains?.(event.target)" in planner
    assert "clearPlannerSubmitError();" in planner
