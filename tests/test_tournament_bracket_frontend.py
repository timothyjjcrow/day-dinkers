"""Execute bracket rendering against representative tournament payloads."""

from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BRACKET_PATH = ROOT / "public/tournament-bracket-v15.js"
APP = (ROOT / "public/app-v15.js").read_text()


def run_js(script):
    bootstrap = f"""
      import fs from 'node:fs';
      import vm from 'node:vm';
      const sandbox = {{module: {{exports: {{}}}}, console}};
      vm.runInNewContext(fs.readFileSync({json.dumps(str(BRACKET_PATH))}, 'utf8'), sandbox);
      const Bracket = sandbox.module.exports;
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", bootstrap + script],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


class HtmlNode:
    def __init__(self, tag="root", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()

    @property
    def text(self):
        return " ".join(
            child.text if isinstance(child, HtmlNode) else child
            for child in self.children
        ).strip()

    def find_all(self, predicate):
        found = [self] if predicate(self) else []
        for child in self.children:
            if isinstance(child, HtmlNode):
                found.extend(child.find_all(predicate))
        return found


class ParsedHtml(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode()
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = HtmlNode(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {"area", "br", "hr", "img", "input", "link", "meta", "source", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, text):
        self.stack[-1].children.append(text)


def tournament():
    pairs = [
        [(1, "Ana <script>alert(1)</script>"), (2, "Ben & Bea")],
        [(3, "Cam"), (4, "Dani")],
        [(5, "Eli"), (6, "Finn")],
        [(7, "Grey"), (8, "Hope")],
    ]
    entries = [
        {
            "id": 101 + index, "seed": index + 1,
            "name": " & ".join(name for _, name in pair),
            "players": [{"id": player_id, "display_name": name} for player_id, name in pair],
        }
        for index, pair in enumerate(pairs)
    ]
    return {
        "id": 17, "name": "Doubles finals", "event_type": "doubles",
        "format": "single_elim", "game_format": "best_of_3_11",
        "status": "active", "is_organizer": True, "my_entry_id": 101,
        "total_rounds": 2, "entries": entries,
        "matches": [
            {
                "id": 201, "round": 1, "position": 0,
                "entry1_id": 101, "entry2_id": 102,
                "status": "awaiting_confirmation", "result_state": "awaiting_confirmation",
                "winner_entry_id": 101, "score1": 2, "score2": 1,
                "game_scores": [{"score1": 11, "score2": 7}, {"score1": 8, "score2": 11}, {"score1": 11, "score2": 9}],
                "scheduled_at": "2026-09-12T16:00:00Z", "court_number": 2,
            },
            {
                "id": 202, "round": 1, "position": 1,
                "entry1_id": 103, "entry2_id": 104,
                "status": "confirmed", "result_state": "confirmed",
                "winner_entry_id": 103, "score1": 2, "score2": 0,
                "game_scores": [{"score1": 11, "score2": 5}, {"score1": 11, "score2": 8}],
            },
            {
                "id": 203, "round": 2, "position": 0,
                "entry1_id": None, "entry2_id": 103, "status": "unreported",
                "result_state": "unreported", "score1": None, "score2": None,
            },
            {
                "id": 204, "round": 2, "position": 1,
                "entry1_id": None, "entry2_id": 104, "status": "unreported",
                "result_state": "unreported", "score1": None, "score2": None,
            },
        ],
    }


def render(data, **options):
    html = run_js(f"""
      const t = {json.dumps(data)};
      const options = {{...{json.dumps(options)}, formatDateTime: () => 'Saturday at 9 AM'}};
      console.log(JSON.stringify(Bracket.render(t, options)));
    """)
    return ParsedHtml(html).root


def cards(root):
    return {
        int(node.attrs["data-bracket-node"]): node
        for node in root.find_all(lambda node: "data-bracket-node" in node.attrs)
    }


def match_card(slot):
    return slot.find_all(lambda node: node.has_class("bm"))[0]


def test_doubles_cards_render_separate_escaped_players_and_keep_actions_in_match_details():
    root = render(tournament())
    match = cards(root)[201]
    players = match.find_all(lambda node: node.has_class("bm-player"))
    assert [player.text for player in players] == [
        "Ana <script>alert(1)</script>", "Ben & Bea", "Cam", "Dani",
    ]
    assert not root.find_all(lambda node: node.tag == "script")
    assert match_card(match).attrs["data-result-match"] == "201"
    assert match_card(match).attrs["data-match-key"] == "201"
    assert "Saturday at 9 AM" in match.text
    assert "Court 2" in match.text
    assert not match.find_all(lambda node: node.tag in {"button", "a", "input", "select"})
    for forbidden in ["data-edit-tournament-schedule", "data-card-opponent-profile", "data-card-opponent-message"]:
        assert not match.find_all(lambda node: forbidden in node.attrs)


def test_only_confirmed_results_highlight_winners_and_pending_scores_stay_provisional():
    rendered = cards(render(tournament()))
    assert not rendered[201].find_all(lambda node: node.has_class("bm-win"))
    winner_sides = rendered[202].find_all(lambda node: node.has_class("bm-win"))
    assert len(winner_sides) == 1
    assert "Eli" in winner_sides[0].text
    assert "Finn" in winner_sides[0].text
    assert "Awaiting confirmation" in rendered[201].text
    assert "Final" in rendered[202].text
    pending_name = match_card(rendered[201]).attrs["aria-label"]
    final_name = match_card(rendered[202]).attrs["aria-label"]
    assert "Provisional score: 2 to 1 games" in pending_name
    assert "Winner:" not in pending_name
    assert "Score: 2 to 0 games" in final_name
    assert "Winner: Eli & Finn" in final_name
    assert "Open match details" in final_name


def test_unresolved_final_and_bronze_name_their_feeders_without_result_actions():
    root = render(tournament())
    rendered = cards(root)
    assert "Winner of SF 1" in rendered[203].text
    assert "Loser of SF 1" in rendered[204].text
    for match_id in [203, 204]:
        assert rendered[match_id].attrs["data-bracket-source-1"] == "201"
        assert rendered[match_id].attrs["data-bracket-source-2"] == "202"
        assert "data-result-match" not in match_card(rendered[match_id]).attrs
        assert "data-match-key" not in match_card(rendered[match_id]).attrs
    placement = root.find_all(lambda node: node.has_class("bracket-placement"))
    assert len(placement) == 1
    assert 204 in cards(placement[0])
    assert 203 not in cards(placement[0])


def test_bye_advances_without_a_fake_match_schedule_or_not_ready_state():
    data = tournament()
    data["matches"] = [{
        "id": 205, "round": 1, "position": 0,
        "entry1_id": 101, "entry2_id": None,
        "winner_entry_id": 101, "status": "bye", "result_state": "bye",
        "score1": None, "score2": None,
        "scheduled_at": "2026-09-12T16:00:00Z", "court_number": 9,
    }]
    data["total_rounds"] = 1
    match = cards(render(data))[205]
    assert "Advances with a bye" in match.text
    assert "Not ready" not in match.text
    assert "Saturday at 9 AM" not in match.text
    assert "Court 9" not in match.text
    assert "data-result-match" not in match_card(match).attrs
    assert len(match.find_all(lambda node: node.has_class("bm-win"))) == 1


def test_round_and_my_match_filters_do_not_render_unrelated_or_unknown_future_matches():
    data = tournament()
    for options, match_ids in [
        ({"selectedRound": "1"}, {201, 202}),
        ({"selectedRound": "2"}, {203, 204}),
        ({"mineOnly": True}, {201}),
    ]:
        filtered = render(data, **options)
        assert set(cards(filtered)) == match_ids
        assert not filtered.find_all(lambda node: node.has_class("bracket-connectors"))
        assert not filtered.find_all(lambda node: any(key.startswith("data-bracket-source-") for key in node.attrs))
    empty = render(data, selectedRound="2", mineOnly=True)
    assert cards(empty) == {}
    assert "no matches" in empty.text.lower()


def test_best_of_three_shows_games_won_separately_from_each_games_points():
    match = cards(render(tournament()))[201]
    assert "Games" in match.text
    sides = match.find_all(lambda node: node.has_class("bm-side"))
    assert len(sides) == 2
    assert [node.text for node in sides[0].find_all(lambda node: node.has_class("bm-score"))] == ["2"]
    assert [node.text for node in sides[1].find_all(lambda node: node.has_class("bm-score"))] == ["1"]
    # The point ledger must keep both sides and each game in order without
    # mistaking 2–1 games won for a pickleball point score.
    assert [node.text for node in match.find_all(lambda node: node.has_class("bm-game"))] == [
        "G1 11–7", "G2 8–11", "G3 11–9",
    ]


def test_shared_match_status_preserves_terminal_byes_and_forfeits_without_two_sides():
    start = APP.index("const COMPETITION_RESULT_STATES =")
    source = APP[start:APP.index("function competitionMatchContext", start)]
    result = run_js(source + """
      const samples = [
        {result_state: 'bye', entry1_id: 101, entry2_id: null},
        {result_state: 'void', entry1_id: 101, entry2_id: null},
        {result_state: 'confirmed', resolution_kind: 'organizer_forfeit', entry1_id: 101, entry2_id: null},
        {result_state: 'unreported', entry1_id: 101, entry2_id: null},
        {status: 'pending', entry1_id: 101, entry2_id: 102},
        {result_state: 'disputed', entry1_id: 101, entry2_id: 102},
      ];
      console.log(JSON.stringify(samples.map(sample => {
        const {state, label, terminal, blocksProgression} = normalizeCompetitionResult(sample);
        return {state, label, terminal, blocksProgression};
      })));
    """)
    assert result == [
        {"state": "bye", "label": "Bye", "terminal": True, "blocksProgression": False},
        {"state": "void", "label": "Not played", "terminal": True, "blocksProgression": False},
        {"state": "confirmed", "label": "Final", "terminal": True, "blocksProgression": False},
        {"state": "unreported", "label": "Not ready", "terminal": False, "blocksProgression": False},
        {"state": "awaiting_confirmation", "label": "Waiting for opponent", "terminal": False, "blocksProgression": True},
        {"state": "disputed", "label": "Score needs review", "terminal": False, "blocksProgression": True},
    ]


def test_round_robin_keeps_standings_and_round_match_labels_without_elimination_paths():
    data = tournament()
    data.update(format="round_robin", total_rounds=3, game_format="single_11", status="completed")
    data["entries"][0]["name"] = "Ana and Ben"
    data["standings"] = [
        {"entry": data["entries"][0], "wins": 3, "losses": 0, "point_diff": 15},
        {"entry": data["entries"][1], "wins": 2, "losses": 1, "point_diff": 2},
    ]
    data["matches"] = [
        {"id": match_id, "round": round_number, "position": position,
         "entry1_id": side1, "entry2_id": side2, "status": "confirmed",
         "result_state": "confirmed", "winner_entry_id": side1, "score1": 11, "score2": 7}
        for match_id, round_number, position, side1, side2 in [
            (301, 1, 0, 101, 102), (302, 2, 0, 101, 103),
            (303, 2, 1, 102, 104), (304, 3, 0, 101, 104),
        ]
    ]
    source_start = APP.index("function roundRobinHtml")
    source = APP[source_start:APP.index("function tournamentPartnerPickerHtml", source_start)]
    result = run_js(source + f"""
      const window = {{TournamentBracket: Bracket}}, esc = String, uiIcon = () => '', fmtDateTime = String;
      const t = {json.dumps(data)};
      console.log(JSON.stringify({{
        all: roundRobinHtml(t), filtered: roundRobinHtml(t, {{selectedRound: '2', mineOnly: true}})
      }}));
    """)
    root = ParsedHtml(result["all"]).root
    assert root.text.index("Standings") < root.text.index("Round 1")
    assert "3W – 0L · +15 pts" in root.text
    assert len(root.find_all(lambda node: node.attrs.get("aria-label") == "Tournament winner")) == 1
    assert set(cards(root)) == {301, 302, 303, 304}
    assert "Round 3 · Match 1" in cards(root)[304].text
    assert "Winner takes the title" not in root.text
    assert not root.find_all(lambda node: node.has_class("bracket-connectors"))
    assert not root.find_all(lambda node: node.has_class("bracket-placement"))
    filtered = ParsedHtml(result["filtered"]).root
    assert "Standings" in filtered.text
    assert set(cards(filtered)) == {302}


def test_podium_waits_for_confirmed_final_and_confirmed_bronze_results():
    data = tournament()
    final = data["matches"][2]
    final.update(entry1_id=101, entry2_id=103, winner_entry_id=101, score1=2, score2=0,
                 result_state="awaiting_confirmation", status="awaiting_confirmation")
    bronze = data["matches"][3]
    bronze.update(entry1_id=102, entry2_id=104, winner_entry_id=104, score1=0, score2=2,
                  result_state="awaiting_confirmation", status="awaiting_confirmation")
    assert not render(data).find_all(lambda node: node.has_class("bracket-place"))
    final.update(result_state="confirmed", status="confirmed")
    places = render(data).find_all(lambda node: node.has_class("bracket-place"))
    assert len(places) == 2
    assert places[0].text.startswith("1st") and "Ben & Bea" in places[0].text
    assert places[1].text.startswith("2nd") and "Eli" in places[1].text
    bronze.update(result_state="confirmed", status="confirmed")
    places = render(data).find_all(lambda node: node.has_class("bracket-place"))
    assert len(places) == 3
    assert places[2].text.startswith("3rd") and "Hope" in places[2].text


def test_schedule_edit_preserves_score_drafts_unless_another_result_version_arrives():
    schedule_start = APP.index("modal.querySelector('#competition-edit-schedule')?.addEventListener")
    schedule = APP[schedule_start:APP.index("const setMutationBusy", schedule_start)]
    sync_start = APP.index("const syncVisibleResult =", schedule_start)
    sync = APP[sync_start:APP.index("const readScores =", sync_start)]
    refresh_start = APP.index("const refreshStaleResult =", sync_start)
    refresh = APP[refresh_start:APP.index("modal.querySelector('[data-result-nudge]')?.addEventListener", refresh_start)]
    result = run_js("""
      async function scenario(resultVersion) {
        let match = {id: 201, result_version: 3, can_report_result: true};
        let liveParent = {id: 17, matches: [match]};
        const drafts = [['11', '9'], ['9', '11'], ['11', '4']];
        const rows = drafts.map(values => {
          const inputs = values.map(value => ({value, readOnly: false}));
          return {inputs, querySelector: selector => inputs[selector.includes('"1"') ? 0 : 1]};
        });
        const actions = ['score', 'confirm'].map(resultAction => ({
          dataset: {resultAction, resultUnavailable: String(resultAction === 'confirm')},
          disabled: resultAction === 'confirm',
        }));
        const updates = [], errors = [], elements = {
          '#competition-match-scheduling': {innerHTML: ''},
          '#competition-result-summary': {innerHTML: 'Original score'},
          '#competition-result-history': {innerHTML: 'Original history'},
          '#competition-progression-note': {textContent: 'Original progression'},
        };
        let clickSchedule, saveSchedule;
        elements['#competition-edit-schedule'] = {addEventListener: (name, callback) => { clickSchedule = callback; }};
        const modal = {
          _cleanupFns: [], querySelector: selector => elements[selector] || null,
          querySelectorAll: selector => selector === '[data-competition-game-row]' ? rows : actions,
        };
        const hooks = {adoptFresh: (fresh, options) => updates.push({version: fresh.matches[0].result_version, ...options})};
        const openChildModal = (parent, open) => open();
        const openTournamentMatchScheduleSheet = (parent, currentMatch, saved) => { saveSchedule = saved; };
        const tournamentMatchScheduleHtml = currentMatch => `${currentMatch.scheduled_at}, court ${currentMatch.court_number}`;
        const usesTournamentGameLedger = true, kind = 'tournament', plural = 'tournaments';
        const syncTemporalResult = () => {};
        const competitionResultStatusHtml = currentMatch => currentMatch.result_state;
        const competitionResultProvenanceHtml = () => '';
        const competitionResultHistoryHtml = currentMatch => `Version ${currentMatch.result_version}`;
        const progressionNoteFor = () => 'Awaiting confirmation';
        const formUX = {showError: message => errors.push(message)};
        const api = () => { throw new Error('The schedule response already contains the match'); };
    """ + schedule + sync + refresh + """
        clickSchedule();
        saveSchedule({id: 17, matches: [{
          id: 201, result_version: resultVersion,
          result_state: resultVersion === 3 ? 'unreported' : 'awaiting_confirmation',
          can_report_result: resultVersion === 3, can_confirm_result: resultVersion !== 3,
          scheduled_at: 'Saturday 10 AM', court_number: 4,
          game_scores: resultVersion === 3 ? [] : [{score1: 11, score2: 6}, {score1: 11, score2: 8}],
        }]});
        await Promise.resolve();
        modal._cleanupFns.forEach(cleanup => cleanup());
        return {
          version: match.result_version,
          values: rows.map(row => row.inputs.map(input => input.value)),
          readOnly: rows.flatMap(row => row.inputs.map(input => input.readOnly)),
          actions, updates, errors,
          schedule: elements['#competition-match-scheduling'].innerHTML,
          summary: elements['#competition-result-summary'].innerHTML,
          history: elements['#competition-result-history'].innerHTML,
          progression: elements['#competition-progression-note'].textContent,
        };
      }
      console.log(JSON.stringify({same: await scenario(3), changed: await scenario(4)}));
    """)
    same, changed = result["same"], result["changed"]
    assert same["version"] == 3
    assert same["values"] == [["11", "9"], ["9", "11"], ["11", "4"]]
    assert same["readOnly"] == [False] * 6
    assert same["errors"] == []
    assert same["summary"] == "Original score"
    assert changed["version"] == 4
    assert changed["values"] == [[11, 6], [11, 8], ["", ""]]
    assert changed["readOnly"] == [True] * 6
    assert [button["disabled"] for button in changed["actions"]] == [True, False]
    assert changed["summary"] == "awaiting_confirmation"
    assert changed["history"] == "Version 4"
    assert changed["progression"] == "Awaiting confirmation"
    assert len(changed["errors"]) == 1
    assert "result changed while you edited the schedule" in changed["errors"][0]
    for state, version in [(same, 3), (changed, 4)]:
        assert state["schedule"] == "Saturday 10 AM, court 4"
        assert state["updates"] == [{"version": version, "render": False}, {"version": version, "render": True}]


def test_connector_binding_uses_live_winning_rows_and_cleans_up_resize_and_round_controls():
    result = run_js(r"""
      const frames = new Map(), events = new Map(), scrolls = [], verticalScrolls = [], observers = [];
      let nextFrame = 1, reducedMotion = true;
      sandbox.requestAnimationFrame = callback => { const frame = nextFrame++; frames.set(frame, callback); return frame; };
      sandbox.cancelAnimationFrame = frame => frames.delete(frame);
      sandbox.ResizeObserver = class {
        constructor(callback) { this.callback = callback; observers.push(this); }
        observe(target) { this.target = target; }
        disconnect() { this.disconnected = true; }
      };
      sandbox.window = {
        addEventListener: (name, callback) => events.set(name, callback),
        removeEventListener: (name, callback) => { if (events.get(name) === callback) events.delete(name); },
        matchMedia: () => ({matches: reducedMotion}),
        getComputedStyle: () => ({position: 'sticky'}),
      };
      const flush = () => { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(fn => fn()); };
      const box = {left: 100, top: 50};
      let winnerRect = {right: 340, top: 80, height: 60};
      const first = {
        getAttribute: () => null,
        querySelector: selector => selector === '.bm-win'
          ? {getBoundingClientRect: () => winnerRect}
          : {getBoundingClientRect: () => ({right: 340, top: 80, height: 120})},
      };
      const second = {
        getAttribute: () => null,
        querySelector: selector => selector === '.bm-win' ? null
          : {getBoundingClientRect: () => ({right: 340, top: 260, height: 120})},
      };
      const final = {
        getAttribute: name => ({'data-bracket-source-1': '201', 'data-bracket-source-2': '202'})[name] || null,
        querySelector: selector => ({getBoundingClientRect: () => ({
          left: 550, top: selector.includes('"1"') ? 145 : 215, height: 60,
        })}),
      };
      const svg = {attributes: {}, innerHTML: '', setAttribute(name, value) { this.attributes[name] = value; }};
      const columns = {
        '1': {offsetLeft: 0, querySelector: () => ({getBoundingClientRect: () => ({top: 200, bottom: 340})})},
        '2': {offsetLeft: 480, querySelector: () => ({getBoundingClientRect: () => ({top: 900, bottom: 1080})})},
      };
      const track = {
        isConnected: true, offsetWidth: 900, scrollWidth: 900, offsetHeight: 400,
        getBoundingClientRect: () => box,
        querySelector(selector) {
          if (selector === '.bracket-connectors') return svg;
          if (selector.includes('data-bracket-column')) return columns[selector.match(/"(\d+)"/)[1]];
          return {'201': first, '202': second, '203': final}[selector.match(/"(\d+)"/)?.[1]] || null;
        },
        querySelectorAll: () => [first, second, final],
      };
      const modal = {
        getBoundingClientRect: () => ({top: 20, bottom: 780}),
        querySelector: selector => ({offsetHeight: selector === '.modal-head' ? 60 : 90}),
        scrollBy: options => verticalScrolls.push(options),
      };
      const bracket = {querySelector: () => track, closest: () => modal, scrollTo: options => scrolls.push(options)};
      const buttons = ['1', '2'].map(round => ({
        dataset: {bracketRound: round}, handlers: {},
        addEventListener(name, handler) { this.handlers[name] = handler; },
        removeEventListener(name, handler) { if (this.handlers[name] === handler) delete this.handlers[name]; },
      }));
      const root = {querySelector: () => bracket, querySelectorAll: () => buttons};
      const cleanup = Bracket.bind(root);
      flush();
      const firstDrawing = svg.innerHTML;
      winnerRect = {right: 340, top: 140, height: 60};
      track.scrollWidth = 1100; track.offsetHeight = 520;
      observers[0].callback(); flush();
      const resizedDrawing = svg.innerHTML;
      buttons[1].handlers.click(); reducedMotion = false; buttons[0].handlers.click();
      events.get('resize')();
      cleanup(); flush();
      console.log(JSON.stringify({firstDrawing, resizedDrawing, dimensions: svg.attributes, scrolls, verticalScrolls,
        cleaned: observers[0].disconnected && events.size === 0 && frames.size === 0
          && buttons.every(button => !button.handlers.click),
        drawingUnchangedAfterCleanup: svg.innerHTML === resizedDrawing}));
    """)
    assert 'd="M240,60H345V125H450" class="is-decided"' in result["firstDrawing"]
    assert 'd="M240,270H345V195H450"' in result["firstDrawing"]
    assert result["firstDrawing"].count('class="is-decided"') == 1
    assert 'd="M240,120H345V125H450" class="is-decided"' in result["resizedDrawing"]
    assert result["dimensions"] == {"width": "1100", "height": "520", "viewBox": "0 0 1100 520"}
    assert result["scrolls"] == [{"left": 480, "behavior": "auto"}, {"left": 0, "behavior": "smooth"}]
    assert result["verticalScrolls"] == [{"top": 712, "behavior": "auto"}]
    assert result["cleaned"]
    assert result["drawingUnchangedAfterCleanup"]
