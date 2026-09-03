from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
CSS = (ROOT / "public" / "styles-v15.css").read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_challenge_actions_use_product_icons_and_an_explicit_dialog_label():
    actions = section("function openCourtPlayerActions", "function maybeAskHours")
    challenge = section("function openChallengeSheet", "function maybeAskHours")

    assert "${uiIcon('trophy')} Challenge to a ranked match" in actions
    assert 'class="challenge-hero-icon" aria-hidden="true">${uiIcon(\'trophy\')}' in challenge
    assert "${uiIcon('trophy')} Send challenge" in challenge
    assert "label: `Challenge ${player.display_name} to a ranked match`" in challenge
    assert "The result will change both players’ ratings." in challenge
    assert "⚔️" not in challenge
    assert ".challenge-hero-icon" in CSS


def test_challenge_success_feedback_uses_semantic_toast_icons():
    assert "toast(`Challenge sent to ${player.display_name}`, { tone: 'success', icon: 'trophy' })" in APP
    assert "icon: isChallenge ? 'trophy' : 'pickleball'" in APP
    assert "showJoinedToast(gameId, isChallenge ? 'Challenge accepted' : \"You're in\"" in APP
    # Play again now opens a reviewable planner; it must not claim a rematch or
    # invitations already exist before the player submits that plan.
    planner = section("async function openPostGamePlanner", "function completedCrewConnectionsHtml")
    assert "openNewGameModal(options)" in planner
    assert "Rematch is on" not in APP
    assert "${uiIcon('calendar')} Play again" in APP
