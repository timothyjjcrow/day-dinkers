"""Execute game presentation and joining against representative API responses."""

from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess

import pytest


APP = (Path(__file__).resolve().parents[1] / "public/app-v15.js").read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


HELPERS = section("const esc =", "const UI_ICON_NAMES") + section(
    "function gameActivityLabel", "function playAgainRowHtml"
)
BINDING = section("function showJoinedToast", "function gameShareText")


def run_js(script, source=HELPERS):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", """
          import assert from 'node:assert/strict';
          const state = {me: {id: 1}, playGamesCache: {cached: true}};
          const uiIcon = () => '<span aria-hidden="true"></span>';
          const avatarHtml = () => '<span class="avatar" aria-hidden="true"></span>';
        """ + source + script], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else None


class Element:
    def __init__(self, tag, attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    @property
    def text(self):
        return " ".join(" ".join(
            child.text if isinstance(child, Element) else child
            for child in self.children
        ).split())

    def descendants(self):
        for child in self.children:
            if isinstance(child, Element):
                yield child
                yield from child.descendants()

    def with_class(self, name):
        return [node for node in self.descendants() if name in node.attrs.get("class", "").split()]


class Markup(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Element("root")
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {"img", "br", "hr", "input", "meta", "link"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if len(self.stack) > 1 and self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def game(**changes):
    return {
        "id": 6, "game_type": "ranked", "max_players": 4,
        "status": "completed", "score_team1": 11, "score_team2": 7,
        "visibility": "open", "players": [
            {"user_id": 3, "team": 2, "display_name": "Sam Patel"},
            {"user_id": 1, "team": "1", "display_name": "Alex Rivera"},
            {"user_id": 4, "team": "2", "display_name": "Morgan Brooks"},
            {"user_id": 2, "team": 1, "display_name": "Jordan Chen"},
        ], **changes,
    }


def scoreboard(data, **options):
    return Markup(run_js(
        f"console.log(JSON.stringify(gameResultScoreboardHtml({json.dumps(data)}, {json.dumps(options)})));"
    )).root


def test_activity_labels_explain_format_without_confusing_capacity_with_attendance():
    cases = [
        ({"game_type": "ranked", "max_players": 2}, "Ranked singles"),
        ({"game_type": "ranked", "max_players": "4"}, "Ranked doubles"),
        ({"game_type": "ranked", "max_players": 8}, "Ranked match"),
        ({"game_type": "casual", "max_players": 2}, "Casual singles"),
        ({"game_type": "casual", "max_players": 4}, "Casual doubles"),
        ({"game_type": "casual", "max_players": 8, "visibility": "open"}, "Pickup session"),
        ({"game_type": "casual", "max_players": 8, "visibility": "friends"}, "Group session"),
        ({"game_type": "casual", "max_players": 8, "visibility": "private"}, "Group session"),
    ]
    actual = run_js(f"console.log(JSON.stringify({json.dumps([data for data, _ in cases])}.map(gameActivityLabel))); ")
    assert actual == [expected for _, expected in cases]


@pytest.mark.parametrize("scores,winner", [((11, 7), 0), ((7, 11), 1)])
def test_final_scoreboard_keeps_each_player_with_their_team_score_and_winner(scores, winner):
    markup = scoreboard(game(score_team1=scores[0], score_team2=scores[1]))
    sides = markup.with_class("match-score-side")
    assert len(sides) == 2
    for index, names in enumerate([{"Alex Rivera", "Jordan Chen"}, {"Sam Patel", "Morgan Brooks"}]):
        side = sides[index]
        people = side.with_class("match-score-player")
        assert {node.attrs["aria-label"] for node in people} == {f"View {name}'s profile" for name in names}
        assert all(name in side.text for name in names)
        number, = side.with_class("match-score-number")
        assert number.text == str(scores[index])
        assert number.attrs["aria-label"] == f"{scores[index]} points"
        assert ("is-winner" in side.attrs["class"].split()) == (index == winner)
        assert bool(side.with_class("match-winner-label")) == (index == winner)
    assert markup.with_class("match-scoreboard")[0].attrs["aria-label"] == "Ranked doubles result"
    assert len([node for node in markup.descendants() if node.tag == "small" and node.text == "You"]) == 1


@pytest.mark.parametrize("status", ["awaiting_confirmation", "unresolved", "upcoming"])
def test_reported_scores_do_not_declare_a_winner_before_completion(status):
    markup = scoreboard(game(status=status))
    assert "reported score" in markup.with_class("match-scoreboard")[0].attrs["aria-label"]
    assert not markup.with_class("is-winner")
    assert not markup.with_class("match-winner-label")
    assert [side.with_class("match-score-number")[0].text for side in markup.with_class("match-score-side")] == ["11", "7"]


def test_series_distinguishes_games_won_from_each_games_points():
    markup = scoreboard(game(score_team1=2, score_team2=1, score_games=[
        {"score_team1": 11, "score_team2": 9},
        {"score_team1": 1, "score_team2": 11},
        {"score_team1": 11, "score_team2": 9},
    ]))
    assert [node.attrs["aria-label"] for node in markup.with_class("match-score-number")] == ["2 games won", "1 games won"]
    assert markup.with_class("match-score-caption")[0].text == "Games won"
    assert markup.with_class("match-score-games")[0].text == "Game 1 11–9 Game 2 1–11 Game 3 11–9"
    # Team 1 won the series despite scoring fewer total points.
    assert "Alex Rivera" in markup.with_class("is-winner")[0].text


@pytest.mark.parametrize("compact", [False, True])
def test_scoreboards_escape_names_in_text_and_accessible_profile_labels(compact):
    data = game()
    data["players"][1]["display_name"] = 'Alex <img src=x onerror="attack()"> & " onclick="attack()'
    markup = scoreboard(data, compact=compact)
    assert data["players"][1]["display_name"] in markup.text
    assert not [node for node in markup.descendants() if node.tag in {"img", "script"}]
    assert not [node for node in markup.descendants() if any(key.startswith("on") for key in node.attrs)]
    if compact:
        assert not [node for node in markup.descendants() if node.tag in {"button", "a"}]
        assert all(node.with_class("match-score-names") for node in markup.with_class("match-score-side"))
    else:
        alex = next(node for node in markup.with_class("match-score-player") if node.attrs["data-view-user"] == "1")
        assert alex.attrs["aria-label"] == f"View {data['players'][1]['display_name']}'s profile"


def test_missing_scores_remain_unreported_instead_of_zero_or_a_winner():
    markup = scoreboard(game(status="upcoming", score_team1=None, score_team2=None))
    assert [node.text for node in markup.with_class("match-score-number")] == ["–", "–"]
    assert all(node.attrs["aria-label"] == "No score" for node in markup.with_class("match-score-number"))
    assert not markup.with_class("is-winner")


def test_roster_counts_do_not_turn_an_ordinary_join_into_an_extra_rsvp():
    run_js("""
      const base = {status: 'upcoming', max_players: 4,
        players: [{attending: true}, {attending: false}], is_joined: true};
      assert.deepEqual(gameRosterStatus(base), {tone: 'forming', label: '2 joined', detail: '2 spots left'});
      assert.deepEqual(gameRosterStatus({...base, max_players: 3}), {tone: 'forming', label: '2 joined', detail: '1 spot left'});
      const full = {...base, max_players: 2};
      assert.deepEqual(gameRosterStatus(full), {tone: 'ready', label: '2 joined', detail: 'Full'});
      assert.equal(gameRosterStatus({...base, attendance_confirmation_due: true}).tone, 'attention');
      assert.notEqual(gameRosterStatus({...base, attendance_confirmation_due: true, is_creator: true}).tone, 'attention');
      assert.notEqual(gameRosterStatus({...base, attendance_confirmation_due: true, is_joined: false}).tone, 'attention');
      assert.deepEqual(gameRosterStatus({...full, attendance_confirmation_due: true, is_creator: true}),
        {tone: 'forming', label: 'Full', detail: '1 still needs to confirm'});
      assert.deepEqual(gameRosterStatus({...full, attendance_confirmation_due: true, is_creator: true,
        attendance_confirmed_count: 2}), {tone: 'ready', label: '2 joined', detail: 'Full'});
      for (const changes of [{is_instant: true}, {status: 'completed'}, {status: 'cancelled'}]) {
        assert.equal(gameRosterStatus({...base, ...changes}), null);
        assert.equal(gameRosterStatusHtml({...base, ...changes}), '');
      }
    """)


BINDER_DOM = """
  const requests = [], notices = [], errors = [], rendered = [], opened = [], refreshed = [];
  let replaceCount = 0, focused = false, refreshedMe = 0, stopped = 0;
  const openButton = {dataset: {openGame: '6'}, focus(options) {focused = options.preventScroll;}};
  const updatedCard = {
    querySelectorAll(selector) {return selector === '[data-open-game]' ? [openButton] : [];},
    querySelector(selector) {return selector === '[data-open-game]' ? openButton : null;},
  };
  const originalCard = {replaceWith(card) {assert.equal(card, updatedCard); replaceCount++;}};
  const button = {
    dataset: {gameJoin: '6', playNoun: 'session'}, textContent: 'Join session', disabled: false,
    attrs: {}, handlers: {},
    closest() {return originalCard;},
    setAttribute(name, value) {this.attrs[name] = value;},
    removeAttribute(name) {delete this.attrs[name];},
    addEventListener(name, callback) {this.handlers[name] = callback;},
  };
  const rootEl = {querySelectorAll(selector) {return selector === '[data-game-join]' ? [button] : [];}};
  const document = {createElement(tag) {
    assert.equal(tag, 'template');
    return {content: {firstElementChild: updatedCard}, set innerHTML(value) {assert.equal(value, 'rendered-card');}};
  }};
  const gameCardHtml = data => {rendered.push(data); return 'rendered-card';};
  const makePressable = (element, callback) => {element.activate = callback;};
  const openDrillInFrom = (_root, callback) => callback();
  const openGameScreen = id => opened.push(id);
  const clearInlineActionError = () => {};
  const showInlineActionError = (card, message) => {assert.equal(card, originalCard); errors.push(message);};
  const toast = (message, options) => notices.push({message, options});
  const refreshMe = async () => {refreshedMe++;};
  const reportClientError = message => {throw Error(message);};
  const refresh = fresh => refreshed.push(fresh);
  const event = {stopPropagation() {stopped++;}};
  const tick = () => new Promise(resolve => setImmediate(resolve));
"""


@pytest.mark.parametrize("recurring", [False, True])
def test_join_uses_post_roster_prevents_duplicate_requests_rebinds_focus_and_can_undo(recurring):
    run_js(BINDER_DOM + f"const recurring = {json.dumps(recurring)};" + """
      const preflight = {id: 6, status: 'upcoming', spots_left: 2, players: [{user_id: 2}]};
      const joined = {...preflight, spots_left: 0, is_joined: true, recurrence: recurring ? 'weekly' : null,
        players: [{user_id: 2}, {user_id: 3}, {user_id: 1}]};
      const left = {...preflight, is_joined: false};
      let finishJoin;
      const pendingJoin = new Promise(resolve => {finishJoin = resolve;});
      const api = async (url, options = {}) => {
        requests.push([url, options.method || 'GET']);
        if (url.endsWith('/join')) return pendingJoin;
        if (url.endsWith('/leave') || url.endsWith('/skip-occurrence')) return left;
        return preflight;
      };
      bindGameButtons(rootEl, refresh);
      const joining = button.handlers.click(event);
      await tick();
      assert.equal(button.disabled, true);
      assert.equal(button.attrs['aria-busy'], 'true');
      assert.equal(replaceCount, 0);
      await button.handlers.click(event);
      assert.deepEqual(requests, [['/games/6', 'GET'], ['/games/6/join', 'POST']]);
      finishJoin(joined);
      await joining;
      assert.equal(replaceCount, 1);
      assert.deepEqual(rendered, [joined]);
      assert.equal(rendered[0].players.length, 3);
      assert.equal(rendered[0].spots_left, 0);
      assert.equal(state.playGamesCache, null);
      assert.equal(focused, true);
      assert.deepEqual(opened, []);
      openButton.activate();
      assert.deepEqual(opened, [6]);
      assert.equal(requests.length, 2);
      assert.equal(notices[0].options.action.label, 'Undo');
      notices[0].options.action.onClick();
      await tick();
      assert.deepEqual(requests.at(-1), [recurring ? '/games/6/skip-occurrence' : '/games/6/leave', 'POST']);
      assert.deepEqual(refreshed, [left]);
      assert.equal(refreshedMe, 2);
      assert.equal(stopped, 2);
      assert.deepEqual(errors, []);
    """, source=BINDING)


@pytest.mark.parametrize("scenario", [
    {"id": "gs-undo-join", "weekly": True, "skip": True},
    {"id": "gs-not-coming", "weekly": True, "skip": True},
    {"id": "gs-leave-series", "weekly": True, "series": True},
    {"id": "gs-leave-series", "weekly": True, "series": True, "host": True, "transfer": 9},
    {"id": "gs-undo-join", "weekly": False},
    {"id": "gs-undo-join", "weekly": True, "skip": True, "cancel": True},
    {"id": "gs-leave-series", "weekly": True, "series": True, "host": True, "cancel": True},
    {"id": "gs-undo-join", "weekly": True, "skip": True, "failure": True},
], ids=["undo-date", "decline-date", "leave-series", "transfer-series", "undo-once", "keep-date", "keep-hosting", "failed-undo"])
def test_detail_leave_and_undo_preserve_occurrence_series_and_host_transfer_scope(scenario):
    binding = section(
        "box.querySelectorAll('#gs-leave, #gs-not-coming, #gs-leave-series, #gs-undo-join')",
        "box.querySelector('#gs-cancel')",
    )
    run_js(f"const scenario = {json.dumps(scenario)};" + """
      const gameId = 6, playNoun = 'session';
      const game = {id: gameId, recurrence: scenario.weekly ? 'weekly' : null, is_creator: !!scenario.host};
      const button = {id: scenario.id, addEventListener(_event, callback) {this.activate = callback;}};
      const requests = [], confirmations = [], renders = [], errors = [];
      let closed = 0, started = 0, reset = 0;
      const box = {querySelectorAll() {return [button];}}, modal = {isConnected: true};
      const fresh = {id: gameId, is_joined: false,
        left_series: !!scenario.series && !scenario.host,
        leave_outcome: scenario.transfer ? 'host_transferred' : null, new_host_name: 'Jordan'};
      const api = async (url, options) => {
        requests.push({url, method: options.method, body: JSON.parse(options.body)});
        if (scenario.failure) throw Error('Connection interrupted');
        return fresh;
      };
      const confirmGameLeave = async (_game, noun, trigger) => {
        assert.equal(trigger, button);
        confirmations.push({kind: 'guarded-leave', noun});
        return {accepted: !scenario.cancel, transferToUserId: scenario.transfer || null};
      };
      const openActionConfirmation = async options => {
        assert.equal(options.trigger, button);
        confirmations.push({kind: 'scope-dialog', title: options.title});
        return !scenario.cancel;
      };
      const clearInlineActionError = () => {};
      const beginButtonAction = trigger => {assert.equal(trigger, button); started++; return () => reset++;};
      const showInlineActionError = (_box, message) => errors.push(message);
      const render = value => renders.push(value);
      const closeModal = () => closed++;
      const refreshMe = () => {}, renderPlay = () => {}, toast = () => {};
    """ + binding + """
      await button.activate({currentTarget: button});
      const expectedDialog = scenario.skip ? {kind: 'scope-dialog', title: 'Skip only this date?'}
        : scenario.series && !scenario.host ? {kind: 'scope-dialog', title: 'Leave this series?'}
        : {kind: 'guarded-leave', noun: scenario.series ? 'weekly series' : 'session'};
      assert.deepEqual(confirmations, [expectedDialog]);
      assert.deepEqual(requests, scenario.cancel ? [] : [{
        url: `/games/6/${scenario.skip ? 'skip-occurrence' : 'leave'}`, method: 'POST',
        body: scenario.transfer ? {transfer_to_user_id: scenario.transfer} : {},
      }]);
      assert.equal(started, scenario.cancel ? 0 : 1);
      assert.equal(reset, scenario.failure ? 1 : 0);
      assert.deepEqual(errors, scenario.failure ? ['Connection interrupted'] : []);
      assert.equal(closed, !scenario.cancel && !scenario.failure && fresh.left_series ? 1 : 0);
      assert.deepEqual(renders, scenario.cancel || scenario.failure || fresh.left_series ? [] : [fresh]);
    """, source="")


@pytest.mark.parametrize("failure", ["full", "closed", "preflight", "join"])
def test_failed_join_preserves_original_card_and_restores_retry_action(failure):
    run_js(BINDER_DOM + f"const failure = {json.dumps(failure)};" + """
      const api = async (url, options = {}) => {
        requests.push([url, options.method || 'GET']);
        if (failure === 'preflight' || (failure === 'join' && url.endsWith('/join'))) throw Error('Connection interrupted');
        return {id: 6, status: failure === 'closed' ? 'completed' : 'upcoming', spots_left: failure === 'full' ? 0 : 1};
      };
      bindGameButtons(rootEl, refresh);
      await button.handlers.click(event);
      assert.deepEqual(requests, failure === 'join'
        ? [['/games/6', 'GET'], ['/games/6/join', 'POST']] : [['/games/6', 'GET']]);
      assert.equal(replaceCount, 0);
      assert.deepEqual(rendered, []);
      assert.equal(button.disabled, false);
      assert.equal(button.textContent, 'Join session');
      assert.equal(button.attrs['aria-busy'], undefined);
      assert.equal(errors.length, 1);
      assert.ok(errors[0].length > 10);
      assert.deepEqual(notices, []);
      assert.equal(focused, false);
      assert.equal(state.playGamesCache, null);
    """, source=BINDING)


@pytest.mark.parametrize("accepted", [False, True])
def test_skip_date_keeps_its_button_after_the_async_confirmation_event_ends(accepted):
    binding = section(
        "box.querySelector('#gs-skip-occurrence')?.addEventListener",
        "// Mirror the server's team-average Elo estimate",
    )
    run_js(f"const accepted = {json.dumps(accepted)};" + """
      const requests = [], renders = [];
      const game = {id: 6, recurrence: 'weekly', my_recurrence_rsvp: {standing_rsvp: true}};
      const button = {addEventListener(_name, callback) {this.activate = callback;}};
      const box = {querySelector() {return button;}};
      let confirm, started = 0;
      const pending = new Promise(resolve => {confirm = resolve;});
      const openActionConfirmation = options => {assert.equal(options.trigger, button); return pending;};
      const beginButtonAction = target => {assert.equal(target, button); started++; return () => {};};
      const fresh = {id: 6, is_joined: false, my_recurrence_rsvp: {standing_rsvp: true}};
      const api = async (url, options) => {requests.push([url, options.method]); return fresh;};
      const render = value => renders.push(value);
      const refreshMe = () => {}, renderPlay = () => {}, toast = () => {};
      const showInlineActionError = (_box, message) => {throw Error(message);};
    """ + binding + """
      const event = {currentTarget: button};
      const skipping = button.activate(event);
      // Native events clear currentTarget after synchronous dispatch finishes.
      event.currentTarget = null;
      assert.equal(started, 0);
      assert.deepEqual(requests, []);
      confirm(accepted);
      await skipping;
      assert.equal(started, accepted ? 1 : 0);
      assert.deepEqual(requests, accepted ? [['/games/6/skip-occurrence', 'POST']] : []);
      assert.deepEqual(renders, accepted ? [fresh] : []);
    """, source="")
