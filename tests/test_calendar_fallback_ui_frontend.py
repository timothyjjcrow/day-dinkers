from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def calendar_source():
    start = APP.index("async function subscribeGamesCalendar")
    end = APP.index("function businessMineItems", start)
    return APP[start:end]


def test_calendar_fallback_is_labeled_private_and_back_aware():
    calendar = calendar_source()
    assert 'label for="calendar-feed-url"' in calendar
    assert 'id="calendar-feed-url" readonly' in calendar
    assert "Don’t post it publicly." in calendar
    assert "button?.closest('.modal-backdrop')" in calendar
    assert "openChildModal(parentModal, openCalendarSubscription)" in calendar
    assert 'onclick="this.select()"' not in calendar


def test_calendar_fallback_has_branded_copy_feedback_and_manual_recovery():
    calendar = calendar_source()
    assert 'id="calendar-copy-link">${uiIcon(\'copy\')} Copy calendar link' in calendar
    assert "copyButton.innerHTML = `${uiIcon('check')} Copied`" in calendar
    assert "input.select();" in calendar
    assert "showInlineActionError(sheet, 'Copy was blocked." in calendar
