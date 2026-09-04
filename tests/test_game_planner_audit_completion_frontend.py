"""Focused contracts for the completed scheduled-game planner audit."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def planner_source() -> str:
    return section("async function openNewGameModal", "async function renderTournaments")


def test_saved_plan_requires_an_explicit_resume_or_start_new_choice():
    planner = planner_source()

    assert "const restoredDraft = requestedDraft;" in planner
    assert "&& savedDrafts.length" in planner
    assert 'id="ng-choice-resume">Resume saved plan</button>' in planner
    assert 'id="ng-choice-new">Start new</button>' in planner
    assert "clearGameDraft(saved.clientAttemptId);" in planner
    assert "openNewGameModal({ startFresh: true })" in planner
    assert "let plannerDirty = false;" in planner
    assert "id=\"ng-dismiss-keep\">Keep draft</button>" in planner
    assert "id=\"ng-dismiss-discard\">Discard</button>" in planner
    assert "restoredDraft?.status === 'editing' && !plannerDirty" in planner
    assert "let plannerStep = restoredDraft ? 'where'" in planner


def test_when_step_has_day_strip_half_hour_grid_duration_and_repeat():
    planner = planner_source()

    assert "Array.from({ length: 31 }, (_, index) => 6 + index / 2)" in planner
    assert 'id="ng-day-strip" role="radiogroup" aria-label="Play date"' in planner
    assert 'id="ng-time-grid" role="radiogroup" aria-label="Play time in 30-minute steps"' in planner
    assert 'data-planner-clock="${clock}"' in planner
    assert "function" not in planner[planner.index("const setPlannerClock"):planner.index("const timeLabel")]
    assert 'id="ng-duration-choices"' in planner
    assert "[60, 90, 120].map" in planner
    assert 'id="ng-recurring-row"' in planner
    assert 'id="ng-recurrence-weekdays"' in planner
    assert 'id="ng-recurrence-end"' in planner
    assert "busyWindowContains" in planner
    assert "classList.toggle('is-popular', popular)" in planner
    assert ".planner-day-strip" in STYLES
    assert ".planner-time-grid" in STYLES


def test_audience_options_and_invites_are_visible_searchable_and_additive():
    planner = planner_source()
    who = planner[planner.index('id="ng-step-who"'):planner.index('<details class="planner-advanced')]

    assert 'id="ng-invite-search"' in who
    assert "f.display_name" in planner
    assert '${playerSkillIdentityHtml(f)}' in planner
    assert "planner-availability-match" in planner
    assert 'id="ng-copy-invite-link"' in who
    assert "invite_user_ids: [...inviteIds]" in planner
    assert "visibility === 'private' ? [...inviteIds] : []" not in planner
    assert "button.dataset.vis === 'friends' && friends.length === 0" in planner
    assert "initialVisibility = 'open';" in planner
    advanced = planner[planner.index('<details class="planner-advanced'):planner.index('<div class="planner-submit-bar')]
    assert 'id="ng-level"' in advanced
    assert 'id="ng-club"' in who
    assert 'id="ng-notes"' in advanced
    assert "Post to a community" in who


def test_capacity_is_three_choices_with_group_stepper_to_one_hundred():
    capacity = section("function gameCapacityChoicesHtml", "function openGameFlow")
    planner = planner_source()
    edit = section("function openEditGameSheet", "function gameFingerprint")

    for label in ("Singles", "Doubles", "Group"):
        assert capacity.count(f"label: '{label}'") == 1
    assert 'aria-label="Group player capacity"' in capacity
    assert 'min="6" max="${CASUAL_GAME_MAX_PLAYERS}"' in capacity
    assert "const CASUAL_GAME_MAX_PLAYERS = 100;" in APP
    assert "const maximum = crewId ? 12 : CASUAL_GAME_MAX_PLAYERS" in planner
    assert "Number(raw.maxPlayers) <= CASUAL_GAME_MAX_PLAYERS" in APP
    assert 'max="${CASUAL_GAME_MAX_PLAYERS}"' in edit
    assert ".game-capacity-stepper" in STYLES


def test_past_game_and_postgame_group_preserve_the_new_inputs():
    log_game = section("async function openLogGameSheet", "async function openNewGameModal")
    planner = planner_source()

    assert 'id="lg-played-at"' in log_game
    assert "played_at: playedAt.toISOString()" in log_game
    assert "playedAt.getTime() > Date.now() + 5 * 60000" in log_game
    assert 'id="ng-save-group-name" maxlength="80"' in planner
    assert "body: JSON.stringify({ name: saveGroupName })" in planner
    assert "saveGroupName: modal.querySelector('#ng-save-group-name')" in planner
