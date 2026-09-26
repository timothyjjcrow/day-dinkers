"""“On my way” on a scheduled game page: one small button in the status pill."""
import json
from pathlib import Path
import subprocess


APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


DETAIL = section('function gameScreenHtml(', 'async function openGameScreen(')
SCREEN = section('async function openGameScreen(', 'function safeNotificationOverlayRoute(')
ESC = section('const esc =', 'const UI_ICON_NAMES')
ETA_LABEL = section('function arrivalEtaLabel(', 'function arrivalStatusCopy(')
ROSTER_ETA = section('    const etaWindow = !game.is_instant', '    const playerRow = ')
PILL = section('    const myEta = etaWindow', '    const [whenDay, whenTime]')
STOP = section('    const stopEta = async', '    // Arrival is collected in a child sheet.')
HANDLERS = section("      box.querySelector('#gs-on-my-way')", "      box.querySelector('#gs-join')")


def run_js(script):
    result = subprocess.run(
        ['node', '--input-type=module', '-e',
         "import assert from 'node:assert/strict';\n" + script],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else None


def pill(game, *, joined_now=False, checked_in=False):
    return run_js(ESC + f'''
      const game = {json.dumps(game)}, joinedNow = {json.dumps(joined_now)};
      const court = game.court, closedRally = false;
      const etaWindow = !game.is_instant && Array.isArray(game.arrivals);
      const uiIcon = (name) => `[${{name}}]`;
      const isCheckedInAtCourt = (id) => {json.dumps(checked_in)} && id === 7;
    ''' + PILL + 'console.log(JSON.stringify(joinedState));')


BASE = {
    'id': 5, 'status': 'upcoming', 'is_instant': False, 'is_joined': True,
    'is_creator': False, 'court': {'id': 7}, 'players': [{'user_id': 1}, {'user_id': 2}],
}


def test_pill_offers_on_my_way_only_inside_the_window_and_away_from_the_court():
    offered = pill({**BASE, 'arrivals': [], 'my_arrival': None})
    assert 'You’re in' in offered
    assert '<button type="button" id="gs-on-my-way">On my way</button>' in offered

    # Outside the window the server sends no arrivals: nothing new appears.
    assert 'gs-on-my-way' not in pill(BASE)
    assert 'gs-on-my-way' not in pill({**BASE, 'arrivals': []}, checked_in=True)
    assert 'gs-on-my-way' not in pill({**BASE, 'arrivals': [], 'players': [{'user_id': 1}]})
    assert 'gs-on-my-way' not in pill({**BASE, 'arrivals': [], 'is_instant': True})
    # Right after joining, Undo for the join wins; the button comes back next render.
    just_joined = pill({**BASE, 'arrivals': []}, joined_now=True)
    assert 'id="gs-undo-join">Undo</button>' in just_joined
    assert 'gs-on-my-way' not in just_joined


def test_active_eta_reads_on_the_way_with_a_small_undo():
    eta = {'id': 3, 'active': True, 'eta_minutes': 10}
    active = pill({**BASE, 'arrivals': [{**eta, 'user_id': 1}], 'my_arrival': eta})
    assert '[map-pin] On the way · 10 min' in active
    assert '<button type="button" id="gs-eta-undo">Undo</button>' in active
    assert 'gs-on-my-way' not in active and 'You’re in' not in active

    host = pill({**BASE, 'is_creator': True, 'arrivals': [], 'my_arrival': None})
    assert 'You’re hosting' in host and 'gs-on-my-way' in host


def test_roster_rows_show_minutes_away_and_running_late_after_start():
    result = run_js(ETA_LABEL + '''
      const now = Date.now();
      const rows = (game, live) => {
    ''' + ROSTER_ETA + '''
        return Object.fromEntries(etaText);
      };
      const arrivals = [
        {user_id: 2, eta_minutes: 10, arrives_at: new Date(now + 9.5 * 60000).toISOString()},
        {user_id: 3, eta_minutes: 10, arrives_at: new Date(now - 1000).toISOString()},
      ];
      console.log(JSON.stringify({
        before: rows({is_instant: false, arrivals}, false),
        after: rows({is_instant: false, arrivals}, true),
        rally: rows({is_instant: true, arrivals}, false),
        closed: rows({is_instant: false}, false),
      }));
    ''')
    assert result['before'] == {'2': '10 min away', '3': 'On the way now'}
    assert result['after'] == {
        '2': 'Running late · 10 min away', '3': 'Running late · on the way now',
    }
    assert result['rally'] == {} and result['closed'] == {}
    assert '${etaText.has(p.user_id) ? `<span class="row-sub">${esc(etaText.get(p.user_id))}</span>`' in DETAIL


def test_tap_shares_ten_minutes_confirms_with_undo_and_recovers_from_conflicts():
    result = run_js(f'''
      const gameId = 5, requests = [], toasts = [], renders = [], log = [];
      let failWith = null;
      const newGameAttemptId = () => 'abc123';
      const modal = {{isConnected: true}};
      const pill = {{focus: () => log.push('focus')}};
      const button = {{addEventListener(_type, fn) {{ this.click = fn; }}}};
      const box = {{querySelector: (selector) => selector === '#gs-on-my-way' ? button
        : selector === '#gs-joined-state' ? pill : null}};
      const beginButtonAction = () => () => log.push('reset');
      const api = async (url, options) => {{
        requests.push([url, options.method, JSON.parse(options.body)]);
        if (failWith) throw Object.assign(new Error('Server copy'), failWith);
        return {{game: {{id: 5, my_arrival: {{active: true}}}}}};
      }};
      const render = (fresh, options) => renders.push([fresh.id, options]);
      const toast = (message, options = {{}}) => toasts.push([message, options]);
      const refreshMe = async () => log.push('me');
      const reopenFresh = async () => log.push('fresh');
      const stopEta = () => log.push('stop');
    ''' + HANDLERS + '''
      await button.click({currentTarget: button});
      toasts[0][1].action.onClick();
      failWith = {code: 'already_at_court', status: 409};
      await button.click({currentTarget: button});
      await new Promise((resolve) => setTimeout(resolve, 0));
      console.log(JSON.stringify({requests, toasts, renders, log}));
    ''')
    assert result['requests'][0] == [
        '/games/5/arrival', 'PUT', {'eta_minutes': 10, 'client_attempt_id': 'arrival-abc123'},
    ]
    assert result['renders'] == [[5, {'preserve': True}]]
    message, options = result['toasts'][0]
    assert message == 'Players know you’re about 10 min away'
    assert options['action']['label'] == 'Undo' and options['tone'] == 'success'
    assert result['toasts'][1][0] == 'You’re already checked in at this court.'
    assert result['log'] == ['focus', 'me', 'stop', 'reset', 'me', 'fresh']


def test_undo_stops_sharing_and_repaints_the_page():
    result = run_js(STOP + '''
      const log = [];
      const gameId = 5, modal = {isConnected: true};
      const box = {querySelector: () => ({focus: () => log.push('focus')})};
      const api = async (url, options) => log.push([url, options.method]);
      const reopenFresh = async (options) => log.push(['fresh', options]);
      const beginButtonAction = () => () => log.push('reset');
      const toast = (message) => log.push(['toast', message]);
      await stopEta();
      console.log(JSON.stringify(log));
    ''')
    assert result == [['/games/5/arrival', 'DELETE'], ['fresh', {'preserve': True}], 'focus']
    assert "box.querySelector('#gs-eta-undo')?.addEventListener('click', (event) => stopEta(event.currentTarget));" in SCREEN


def test_copy_error_text_and_poll_fingerprint():
    assert "arrival_window_closed: 'On my way opens an hour before start and closes 30 minutes after.'," in APP
    fingerprint = section('function gameFingerprint(', 'function captureGameViewState(')
    # The window opening or closing re-renders the open page on the next poll.
    assert 'Array.isArray(game.arrivals)' in fingerprint
