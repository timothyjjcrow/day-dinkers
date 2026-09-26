"""Rain alerts on the game page: one card, only when rain is likely at an
upcoming outdoor plan, with the host's move/cancel shortcuts."""

import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public/app-v15.js").read_text()
STYLES = (ROOT / "public/styles-v15.css").read_text()


def section(start, end, source=APP):
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


RAIN_CARD = section("function gameRainAlertHtml", "function sessionVisitFactsHtml")


def rain_card(game, rain):
    script = f"""
      const uiIcon = (name) => `<i data-icon="${{name}}"></i>`;
      {section("const esc =", "const UI_ICON_NAMES")}
      {RAIN_CARD}
      const game = {{
        status: 'upcoming', is_instant: false, is_joined: true, is_creator: false,
        court: {{ indoor: false }}, scheduled_at: new Date(Date.now() + 2 * 3600e3).toISOString(),
        ...{json.dumps(game)},
      }};
      process.stdout.write(JSON.stringify(gameRainAlertHtml(game, {json.dumps(rain)})));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


LIKELY = {"chance": 70, "label": "6 PM"}


def test_players_see_the_forecast_and_who_decides():
    html = rain_card({}, LIKELY)
    assert 'class="attendance-confirmation rain-alert"' in html
    assert '<i data-icon="water"></i> Rain likely around 6 PM · 70% chance' in html
    assert "<span>Your host can move or cancel it.</span>" in html
    assert "<button" not in html


def test_the_host_gets_move_and_cancel_only():
    html = rain_card({"is_creator": True}, LIKELY)
    assert "Your host can" not in html
    assert '<button type="button" class="btn btn-primary" data-rain-proxy="gs-edit">Move it</button>' in html
    assert '<button type="button" class="btn btn-secondary" data-rain-proxy="gs-cancel">Cancel game</button>' in html
    assert html.count("btn-primary") == 1


@pytest.mark.parametrize("game, rain", [
    ({}, {"chance": 49, "label": "6 PM"}),
    ({}, None),
    ({"court": {"indoor": True}}, LIKELY),
    ({"is_instant": True}, LIKELY),
    ({"is_joined": False}, LIKELY),
    ({"status": "cancelled"}, LIKELY),
    ({"scheduled_at": "2020-01-01T10:00:00Z"}, LIKELY),
], ids=["dry", "unknown", "indoor", "instant", "not-joined", "cancelled", "started"])
def test_nothing_renders_when_rain_does_not_matter(game, rain):
    assert rain_card(game, rain) == ""


def test_label_from_the_server_is_escaped():
    html = rain_card({}, {"chance": "80", "label": "<b>6 PM</b>"})
    assert "&lt;b&gt;6 PM&lt;/b&gt;" in html and "<b>6 PM</b>" not in html


def test_game_page_asks_for_rain_at_game_time_and_fills_one_slot():
    detail = section("function gameScreenHtml", "async function openGameScreen")
    screen = section("async function openGameScreen", "function safeNotificationOverlayRoute")

    assert "${conditionsExpected ? '<div id=\"gs-rain-alert\" hidden></div>' : ''}" in detail
    assert detail.index('id="gs-rain-alert"') < detail.index("${sessionConfirmationHtml(game)}")
    assert "api(`/courts/${court.id}/weather?at=${encodeURIComponent(game.scheduled_at)}" in screen
    assert "&minutes=${game.duration_minutes}" in screen
    # A response for an older render never paints over a newer one.
    assert "if (!el || !rainSlot?.isConnected) return;" in screen
    assert "const rainCard = gameRainAlertHtml(game, rain);" in screen
    # Host buttons reuse the Manage controls and hand focus back to the card.
    assert "box.querySelector(`#${button.dataset.rainProxy}`)?.click();" in screen
    assert "sheet._returnFocus = button;" in screen


def test_info_strip_uses_the_game_time_chance_once():
    screen = section("async function openGameScreen", "function safeNotificationOverlayRoute")
    assert "rain likely around game time" not in APP
    assert "w.rain_soon ?" not in screen
    assert "rain?.chance >= 50 && !rainCard ? ` · ${uiIcon('water')} rain likely around ${esc(rain.label)}`" in screen


def test_activity_uses_the_water_icon_for_rain_alerts():
    icons = section("const notificationIconFor = (kind) => {", "const notificationAccessibleText")
    assert "if (kind === 'game_weather') return 'water';" in icons


def test_rain_card_styles_live_in_the_game_page_section():
    game_page = section("/* ---------- r83 · Play, planner & game page ---------- */",
                        "/* ---------- r83 · Friends, chat & Me ---------- */", STYLES)
    block = section("/* r85 · Rain alerts */", "\n/*", game_page)
    assert ".rain-alert b .ui-icon" in block
    assert "var(--green-accent)" in block
