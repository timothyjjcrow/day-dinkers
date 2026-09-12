"""Frontend contracts for complete scheduled-game planning details."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
STYLES = (ROOT / 'public' / 'styles-v15.css').read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_planner_collects_validates_recovers_and_posts_planning_details():
    planner = section('async function openNewGameModal', 'async function renderTournaments')

    for control in (
        'id="ng-title" maxlength="120"',
        'id="ng-description" maxlength="1000"',
        'id="ng-duration" min="15" max="720"',
        'id="ng-cost" min="0" max="10000"',
        'id="ng-court-number" maxlength="40"',
        'id="ng-court-count" min="1" max="24"',
    ):
        assert control in planner

    for request_field in (
        'title:', 'description:', 'duration_minutes:', 'cost_cents:',
        'court_number:', 'court_count:',
    ):
        assert request_field in planner

    for recovered_field in (
        'title: restoredDraft.title',
        'description: restoredDraft.description',
        'duration_minutes: restoredDraft.durationMinutes',
        'cost_cents: restoredDraft.costCents',
        'court_number: restoredDraft.courtNumber',
        'court_count: restoredDraft.courtCount',
    ):
        assert recovered_field in planner

    assert 'Ends about ${fmtTimeShort' in planner
    assert 'Use a whole duration from 15 to 720 minutes.' in planner
    assert 'Enter a cost from $0 to $10,000 with up to two decimals.' in planner
    assert 'Use a whole number from 1 to 24 courts.' in planner


def test_capacity_uses_three_distinct_choices_and_a_group_stepper():
    capacity = section('function gameCapacityChoicesHtml', 'function openGameFlow')

    assert "gameType === 'ranked' ? 'Singles' : '2 players'" in capacity
    assert "gameType === 'ranked' ? 'Doubles' : '4 players'" in capacity
    assert "label: 'Custom'" in capacity
    assert "'Match format' : 'Maximum players'" in capacity
    assert 'id="${prefix}-open-capacity"' in capacity
    assert 'id="${prefix}-open-player-count" min="2" max="${CASUAL_GAME_MAX_PLAYERS}"' in capacity
    assert 'data-capacity-adjust="-1"' in capacity
    assert 'data-capacity-adjust="1"' in capacity
    assert '.game-capacity-stepper' in STYLES


def test_cards_and_detail_surface_the_saved_plan_without_hiding_time():
    card = section('function gameCardHtml', 'function showJoinedToast')
    detail = section('function gameScreenHtml', 'async function openGameScreen')

    assert 'const customTitle = String(game.title || \'\').trim();' in card
    assert 'customTitle && !game.is_instant ? `<span class="game-card-when"' in card
    assert '${esc(scheduledLabel)}' in card
    assert 'game-card-description' not in card
    assert '${esc(game.court_number)}' in card
    assert '<details class="game-detail-plan simple-disclosure">' in detail
    assert 'courtEntryDescriptionParts(game).note' in detail
    assert '${esc(descriptionNote)}' in detail
    assert '${courtEntryNoticeHtml(game)}' in detail
    assert 'game.ends_at' in detail
    assert '${sessionVisitFactsHtml(game)}' in detail
    assert detail.index('${sessionVisitFactsHtml(game)}') < detail.index('${waitlistHtml}${arrivalsHtml}${gameConsentHtml(game)}')
    visit_facts = section('function sessionCourtAccessLabel', 'function sessionReturnToolsHtml')
    assert 'game.cost_cents' in visit_facts
    assert 'game.court_count' in visit_facts
    assert 'Host says' in visit_facts
    assert "game.court_number || ''" in detail

    assert '.game-money-input' in STYLES
    assert '.game-card-when' in STYLES
    assert '.game-detail-plan' in STYLES
    assert '.game-detail-plan-facts' in STYLES
