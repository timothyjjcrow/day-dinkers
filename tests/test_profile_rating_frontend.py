"""The player-facing profile uses standard pickleball ratings."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
STYLES = (ROOT / 'public' / 'styles-v15.css').read_text()


def test_profile_editor_supports_self_rating_and_optional_dupr():
    start = APP.index('function openEditProfile()')
    editor = APP[start:APP.index('function gameIsChallenge', start)]
    assert 'id="ep-self-rating" role="radiogroup"' in editor
    assert "[2.5, '2.5', 'Learning consistency']" in APP
    assert "[4.5, '4.5', 'Competitive play']" in APP
    assert 'id="ep-dupr" min="2" max="8" step="0.001"' in editor
    assert 'skill_rating: selfRating' in editor
    assert 'dupr_rating: dupr || null' in editor
    assert 'id="ep-skill"' not in editor
    assert 'skill_level:' not in editor
    assert 'How player ratings differ' in editor
    assert '<b>Self-rating</b>' in editor
    assert '<b>DUPR</b>' in editor
    assert '<b>Third Shot match rating</b>' in editor
    assert 'changes only from confirmed ranked match results' in editor
    assert '.profile-skill-rating-choice' in STYLES
    assert '.profile-rating-guide' in STYLES


def test_profile_rating_errors_are_human_readable():
    assert "invalid_skill_rating: 'Choose a self-rating" in APP
    assert "invalid_dupr_rating: 'Enter a DUPR rating" in APP


def test_match_rating_is_opt_in_and_confined_to_rankings_or_completed_results():
    helper_start = APP.index('function playerSkillIdentityHtml')
    helper_end = APP.index('function openThirdShotRatingExplainer', helper_start)
    helper = APP[helper_start:helper_end]
    assert 'includeMatchRating = false' in helper

    public_profile_start = APP.index('async function openUserProfile')
    public_profile_end = APP.index('function profileDashboardRequest', public_profile_start)
    public_profile = APP[public_profile_start:public_profile_end]
    assert 'playerSkillIdentityHtml(user)' in public_profile
    assert '${user.rating}' not in public_profile
    assert 'ratingSparklineHtml' not in public_profile

    self_profile_start = APP.index('async function renderProfile')
    self_profile_end = APP.index('function openEditProfile', self_profile_start)
    self_profile = APP[self_profile_start:self_profile_end]
    assert 'playerSkillIdentityHtml(me)' in self_profile
    assert '${me.rating}' not in self_profile
    assert 'ratingSparklineHtml' not in self_profile
    assert 'Ranked win rate' in self_profile

    game_start = APP.index('function gameScreenHtml')
    game_end = APP.index('async function openGameScreen', game_start)
    game_screen = APP[game_start:game_end]
    assert "includeMatchRating: game.game_type === 'ranked' && game.status === 'completed'" in game_screen
    assert 'Match rating ${Number(mine.rating || 1200)}' not in APP

    rankings_start = APP.index("if (seg === 'scores')")
    rankings_end = APP.index("const activePlayLevel", rankings_start)
    rankings = APP[rankings_start:rankings_end]
    assert 'Third Shot match rating ${u.rating}' in rankings
    assert '${me.rating} · What is this?' in rankings
