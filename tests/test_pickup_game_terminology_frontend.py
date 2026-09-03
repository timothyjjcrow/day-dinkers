from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
GAMES = (ROOT / 'backend' / 'routes' / 'games.py').read_text()
MODELS = (ROOT / 'backend' / 'models.py').read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_pickup_game_cards_and_details_use_plain_time_aware_copy():
    card = section('function gameCardHtml', 'function bindGameButtons')
    detail = section('function gameScreenHtml', 'async function openGameScreen')

    for old_copy in (
        'Right now',
        'Played recently',
        'Rally ended',
        'This rally is closed',
        'This rally closed',
        'join this rally',
        'This rally is no longer',
        'This rally is wrapping up',
        'This rally is fully committed',
        'Rally now ·',
        'Rally ·',
        'Arrive in 5–15 min',
    ):
        assert old_copy not in card
        assert old_copy not in detail

    assert "game.is_instant\n      ? `${fmtDateTime(game.scheduled_at)}${assembly ? ' · Live' : ''}`" in card
    assert 'Open until ${fmtTimeShort(game.assembly_expires_at)}' in card
    assert "`${assembly ? 'Live pickup game' : 'Pickup game'} · ${gameTypeAndFormat}`" in detail
    assert 'Game didn’t fill up' in detail


def test_on_the_way_flow_uses_one_vocabulary_and_clear_privacy_copy():
    flow = section('function rallyCountsText', 'function openPlayNowCourtPicker')
    arrival = section('function openRallyArrivalSheet', 'async function cancelRallyArrival')
    detail = section('function gameScreenHtml', 'async function openGameScreen')

    for old_copy in (
        'travel spot',
        'held spot',
        'Your spot is held',
        'Cancel trip',
        'View trip details',
        'fully committed',
        'Refresh Nearby',
    ):
        assert old_copy.lower() not in flow.lower()
        assert old_copy.lower() not in detail.lower()

    assert '`${ready} here`' in flow
    assert '`${onWay} on the way`' in flow
    assert '`${spots} open`' in flow
    assert 'On my way · ${initialEta} min' in arrival
    assert 'Only the court is shared, not your location.' in flow
    assert 'Join at the court to see who’s playing.' in detail


def test_court_chat_openings_and_play_pulses_avoid_system_jargon():
    roster = section('function openRosterBoostSheet', 'function crewSummaryFrom')
    pulse = section('function playPulseCommitmentCopy', 'function gameTypeLabel')
    nearby = section('async function renderNearbyPlayers', 'async function renderFriends')

    for old_copy in (
        'court post',
        'Court post',
        'Withdraw court post',
        'intended destination',
        'current presence',
        'Play there',
        'Create quick game',
    ):
        assert old_copy not in roster
        assert old_copy not in pulse
        assert old_copy not in nearby

    assert 'Opening posted in Court chat' in roster
    assert 'Only the court is shared, not your location.' in pulse
    assert '>I’m in</button>' in nearby


def test_server_owns_pickup_expiry_and_notifications_use_product_copy():
    assert "data['assembly_expires_at'] = iso(" in GAMES
    assert "'assembly_expires_at'" in GAMES
    assert "'Your pickup game ended'" in GAMES
    assert 'joined your pickup game' in GAMES
    assert 'started a pickup game at' in GAMES
    assert "f'{user.display_name} is on the way'" in GAMES
    assert "'Players on their way to your pickup games'" in MODELS

    for old_copy in (
        'Your live rally ended',
        'joined your live rally',
        'started a live rally at',
        'your live rallies',
    ):
        assert old_copy not in GAMES
        assert old_copy not in MODELS
