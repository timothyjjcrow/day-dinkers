""""When works?" time vote: planner vote mode, game-page chips and "Time TBD" surfaces."""
import json
import os
import re
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public/app-v15.js').read_text()
STYLES = (ROOT / 'public/styles-v15.css').read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def run(script):
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True,
                            env={**os.environ, 'TZ': 'America/Los_Angeles'})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


HELPERS = section('  function fmtDateTime(', '  function scoreAutoConfirmCopy(') + section(
    '  function gameWhenText(', '  // Decorative calendar tile')


def test_game_page_vote_chips_counts_and_host_lock():
    output = run('''
      const esc = (value) => String(value).replaceAll('<', '&lt;');
    ''' + HELPERS + section('  function sessionTimeVoteHtml(', '  function openInstallApp(') + '''
      const vote = {options: [{id: 'a', starts_at: '2030-01-05T17:00:00Z', count: 3},
                              {id: 'b', starts_at: '2030-01-06T18:30:00Z', count: 0}],
        my_votes: ['b'], leader_id: 'a', locks_at: '2030-01-05T14:00:00Z', can_vote: true, can_lock: false};
      const plain = (text) => text.replace(/[\u00a0\u202f]/g, ' ');
      console.log(JSON.stringify({
        voter: plain(sessionTimeVoteHtml({time_vote: vote})),
        host: plain(sessionTimeVoteHtml({time_vote: {...vote, my_votes: [], can_vote: false, can_lock: true}})),
        when: [gameWhenText({time_vote: vote, scheduled_at: vote.options[0].starts_at}), plain(fmtVoteTime(vote.options[1].starts_at))],
      }));
    ''')
    voter, host = output['voter'], output['host']
    assert 'When works?' in voter
    assert '<b>Sat 9 AM</b><small>3 can</small>' in voter
    assert 'data-time-vote="b" class="active" aria-pressed="true"' in voter
    assert 'data-time-vote="a" class="" aria-pressed="false"' in voter
    assert 'disabled' not in voter and 'gs-time-lock' not in voter
    assert 'id="gs-time-lock" data-option-id="a">Lock Sat 9 AM</button>' in host
    assert 'Locks automatically Sat 6 AM' in host
    assert host.count('disabled') == 2
    assert output['when'] == ['Time TBD', 'Sun 10:30 AM'] or output['when'] == ['Time TBD', 'Sun 10:30 AM']


def test_unvoted_invitees_get_a_pick_times_decision():
    output = run(section('  function playGameDecision(', '  function gameRosterStatus(') + '''
      const game = {status: 'upcoming', time_vote: {can_vote: true, my_votes: [], locks_at: '2030-01-05T14:00:00Z'}};
      console.log(JSON.stringify([playGameDecision(game),
        playGameDecision({...game, time_vote: {...game.time_vote, my_votes: ['a']}}),
        playGameDecision({...game, time_vote: {...game.time_vote, can_vote: false}}),
        playGameDecision({status: 'upcoming'})]));
    ''')
    assert output[0] == {'kind': 'vote', 'label': 'Pick times', 'action': 'Pick times',
                         'deadline': 1893852000000}
    assert output[1:] == [None, None, None]


def test_planner_vote_mode_reuses_time_chips_and_requires_invitees():
    planner = section('async function openNewGameModal(', '  function openSessionWrapUpModal(')
    assert 'id="ng-vote-toggle" aria-pressed="false">Not sure? Let friends vote</button>' in planner
    assert "voteTimes ? 'Pick one time instead' : 'Not sure? Let friends vote'" in planner
    assert "voteTimes ? 'Pick 2–3 times' : 'Suggested times'" in planner
    assert "`${[...voteTimes].sort().map(fmtVoteTime).join(' · ')} · vote`" in planner
    assert "else if (voteTimes.size < 3) voteTimes.add(iso);" in planner
    assert "const recurringAllowed = !isRanked && !voteTimes;" in planner
    assert "else if (voteTimes && visibility === 'open') visibility = 'private';" in planner
    assert "showPlannerSubmitError('Invite at least one friend to vote.', friendsWrap);" in planner
    assert "time_options: [...voteTimes].sort(), recurrence_timezone: recurrenceTimezone" in planner
    assert "if (voteTimes && voteTimes.size < 2) {" in planner  # When step needs 2-3 picks


def test_create_payload_keeps_time_options_for_exact_retries():
    output = run(section('  function sanitizeGameCreatePayload(', '  function availableStorage(') + '''
      const CASUAL_GAME_MAX_PLAYERS = 100, normalizedGameLevel = () => null;
      const base = {court_id: 3, scheduled_at: '2030-01-05T17:00:00Z', client_attempt_id: 'abcdefghijklmnop'};
      console.log(JSON.stringify([
        sanitizeGameCreatePayload({...base, time_options: ['2030-01-05T17:00:00Z', '2030-01-06T17:00:00Z', 'x', 'y']}).time_options,
        'time_options' in sanitizeGameCreatePayload(base)]));
    ''')
    assert output == [['2030-01-05T17:00:00Z', '2030-01-06T17:00:00Z', 'x'], False]


def test_open_votes_hide_fixed_time_tools_everywhere():
    cards = section('  function gameCardHtml(', '  function bindGameButtons(')
    assert "game.time_vote ? 'Time TBD · voting'" in cards
    assert "planDateTileHtml(game.time_vote ? '' : game.scheduled_at)" in cards
    assert "${game.time_vote ? '' : `" in cards  # no quick Calendar button
    assert "decision.kind === 'vote' ? 'When works for you?'" in cards
    assert "|| game.time_vote\n" in section('  function sessionReturnToolsHtml(', '  function sessionTimeVoteHtml(')
    assert '${game.time_vote ? sessionTimeVoteHtml(game) : when}' in section('function gameScreenHtml(', 'async function openGameScreen(')
    assert 'game.time_vote,' in section('  function gameFingerprint(', '  function captureGameViewState(')
    edit = section('  function openEditGameSheet(', 'bindScheduleDateTimePicker(sheet, \'eg-when\')')
    assert "if (game.time_vote) sheet.querySelectorAll('#eg-when-editor, #eg-repeat-controls')" in edit
    assert ".filter((value) => !game.time_vote || value !== 'open')" in edit
    # Rare race codes (time_vote_open/closed/forbidden) use the generic 409/403 copy.
    errors = section('  const ERROR_TEXT = {', '  };')
    assert "    time_vote_needs_invitees: 'Invite at least one friend to vote.'," in errors
    assert "    invalid_time_options: 'Pick 2–3 different upcoming times.'," in errors
    assert "Join my pickleball game${courtName ? ` at ${courtName}` : ''} — ${gameWhenText(game)}" in APP
    assert "detail: `${game.time_vote ? 'Time TBD' : fmtDateTime(game.scheduled_at)}" in APP


def test_time_vote_styles_live_in_the_game_page_section_on_shared_tokens():
    play = STYLES[STYLES.index('/* ---------- r83 · Play, planner & game page ---------- */'):
                  STYLES.index('/* ---------- r83 · Friends, chat & Me ---------- */')]
    assert STYLES.count('/* r85 · "When works?" time vote */') == 1
    block = play[play.index('/* r85 · "When works?" time vote */'):]
    block = block[:block.index('.session-plan-card.is-hero .session-place-wrap')]
    assert play.index('.session-plan-card.is-hero .session-when b {') < play.index('/* r85 · "When works?" time vote */')
    assert not re.search(r'font-size:\s*\d', block)
    assert 'border-radius' not in block and not re.search(r'#[0-9a-fA-F]{3}', block)
    assert 'var(--control-min)' in block and 'var(--text-xs)' in block
