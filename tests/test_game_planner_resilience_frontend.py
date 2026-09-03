from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
CSS = (ROOT / "public" / "styles-v15.css").read_text()


def planner_source():
    start = APP.index("async function openNewGameModal")
    end = APP.index("async function renderTournaments", start)
    return APP[start:end]


def test_planner_keeps_partial_setup_failures_distinct_from_empty_results():
    planner = planner_source()
    assert "const plannerFeeds = await Promise.allSettled([" in planner
    assert "plannerFeedErrors[key]" in planner
    assert "plannerFeedErrors.friends" in planner
    assert "Friends couldn’t load." in planner
    assert 'Add players from Community before using invite only.' in planner


def test_planner_exposes_a_recoverable_setup_notice_without_blocking_creation():
    planner = planner_source()
    assert 'class="planner-load-notice" role="status"' in planner
    assert 'id="ng-retry-setup">Reload</button>' in planner
    assert "flushPlannerDraft(plannerSubmitting ? 'submitting' : 'editing')" in planner
    assert "transitionModal(modal, () => openNewGameModal(" in planner
    assert ".planner-load-notice" in CSS


def test_planner_explains_how_to_continue_when_court_shortcuts_fail():
    planner = planner_source()
    assert "Court shortcuts are unavailable. Search by court name to continue." in planner


def test_planner_recovery_is_scoped_to_one_attempt_and_never_blocks_new_plans():
    planner = planner_source()
    assert "const savedDrafts = readGameDrafts();" in planner
    assert "const requestedDraft = resumeAttemptId ? readGameDraft(resumeAttemptId) : null;" in planner
    assert "resumeAttemptId: offeredDraft.clientAttemptId" in planner
    assert "An earlier plan is saved separately" in planner
    assert "protectedSubmittingDraft" not in planner
    assert "clearGameDraft(plannerAttemptId)" in planner
    assert "clearGameDraft(supersededAttemptId)" in planner


def test_ambiguous_planner_submit_stays_inline_with_exact_retry_and_discard():
    planner = planner_source()
    start = planner.index("const ambiguous = err.isNetworkError")
    end = planner.index("// A known non-conflict 4xx", start)
    ambiguous = planner[start:end]
    assert "[408, 425, 429].includes(Number(err.status))" in ambiguous
    assert "flushPlannerDraft('submitting')" in ambiguous
    assert "showPlannerAttemptRecovery(" in ambiguous
    assert "closeModal(modal)" not in ambiguous
    assert "toast(" not in ambiguous
    assert 'id="ng-retry-exact">Try same plan again</button>' in planner
    assert 'id="ng-check-games">Upcoming play</button>' in planner
    assert 'id="ng-discard-recovery">Discard</button>' in planner
    assert "title: 'Discard this recovery?'" in planner
    assert "If the play session was already created, it will still appear in Upcoming play." in planner


def test_game_draft_storage_uses_attempt_scoped_keys_and_account_purge():
    assert "const gameDraftPrefix =" in APP
    assert "function readGameDrafts(userId" in APP
    assert "function clearGameDraft(attemptId" in APP
    assert "function clearGameDrafts(userId" in APP
    assert "clearGameDrafts(accountId);" in APP
    assert "keyAttemptId !== safe.clientAttemptId" in APP
