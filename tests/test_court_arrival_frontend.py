from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public/app-v15.js').read_text()


def test_presence_labels_match_privacy_and_never_invent_verification():
    start = APP.index('  function courtPresenceAudience(')
    end = APP.index('  async function freshCourtPresenceLocation(', start)
    script = "const assert=require('node:assert/strict'); const state={me:{nearby_visibility:'everyone'}};\n" + APP[start:end] + """
    assert.match(courtPresenceAudience(true), /Signed-in players/);
    assert.match(courtPresenceAudience(false), /Friends/);
    assert.match(courtPresenceAudience(true,'hidden'), /stays hidden/);
    assert.match(courtPresenceAudience(true,'friends'), /only the court total/);
    assert.equal(courtPresenceSourceText({}), 'Self-reported');
    assert.match(courtPresenceSourceText({location_verified_at:new Date().toISOString()}), /^Location confirmed/);
    assert.equal(courtPresenceSummaryText({self_reported:3,location_confirmed:0}), '3 self-reported');
    assert.equal(courtPresenceSummaryText({self_reported:0,location_confirmed:0}), '');
    """
    result = subprocess.run(['node','-e',script],text=True,capture_output=True)
    assert result.returncode == 0, result.stderr


def test_fallback_is_deliberate_and_limited_to_manual_arrival():
    start = APP.index('  function openCheckInSheet(')
    end = APP.index('  function gameCancellationVariant(', start)
    sheet = APP[start:end]
    assert "presenceIntent === 'manual_checkin'" in sheet
    assert "confirm_at_court: true" in sheet
    assert "'self_reported' : presenceIntent" in sheet
    assert 'selfReport ? null : await freshCourtPresenceLocation(court)' in sheet
    assert "selfReportButton?.addEventListener('click', () => submitCheckIn(true))" in sheet
    assert "form.addEventListener('submit', event => { event.preventDefault(); submitCheckIn(false); })" in sheet
