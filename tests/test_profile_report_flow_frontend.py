from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
CSS = (ROOT / "public" / "styles-v15.css").read_text()


def profile_source():
    start = APP.index("async function openUserProfile")
    end = APP.index("async function subscribeGamesCalendar", start)
    return APP[start:end]


def report_source():
    start = APP.index("function openUserSafetyReport")
    end = APP.index("function chatBodyHtml", start)
    return APP[start:end]


def test_reporting_is_a_back_aware_child_flow_with_explicit_choices():
    profile = profile_source()
    report = profile[profile.index("querySelector('#up-report')"):profile.index("querySelector('#up-block')")]
    helper = report_source()
    assert "openUserSafetyReport(user, { parentModal: modal });" in report
    assert "parentModal ? openChildModal(parentModal, launch) : launch()" in helper
    assert "modalHead(`Report ${firstName}`, 'alert-triangle')" in helper
    assert '<fieldset class="profile-report-choices">' in helper
    assert '<legend class="sr-only">Reason for reporting ${esc(user.display_name || \'this player\')}</legend>' in helper
    assert 'type="radio" name="profile-report-reason"' in helper
    assert 'id="profile-report-details"' in helper
    assert 'id="profile-report-block"' in helper
    assert "const discardGuard = bindModalDiscardConfirmation" in helper
    assert "discardGuard.authorizeClose();" in helper
    assert "return sheet;" in helper


def test_reporting_keeps_failures_in_the_sheet():
    report = report_source()
    assert "clearInlineActionError(sheet);" in report
    assert "showInlineActionError(sheet, error.message);" in report
    assert "toast(error.message)" not in report
    assert ".profile-report-choice" in CSS
    assert "profile-report-success" in report
