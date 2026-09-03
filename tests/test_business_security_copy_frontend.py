from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start, end):
    return APP[APP.index(start):APP.index(end)]


def test_business_mfa_private_values_have_accessible_copy_and_manual_fallbacks():
    security = section("function openBusinessSecurity", "function openBusinessOwnershipControls")

    assert 'onclick="this.select()"' not in security
    assert 'aria-describedby="business-mfa-uri-help"' in security
    assert "id=\"business-mfa-copy-uri\"" in security
    assert "Authenticator setup URI copied." in security
    assert "setupUriInput.focus();" in security
    assert "setupUriInput.select();" in security
    assert "Copy was blocked. The setup URI is selected" in security

    assert 'for="business-recovery-codes"' in security
    assert 'aria-describedby="business-recovery-help"' in security
    assert "id=\"business-recovery-copy-status\"" in security
    assert "Recovery codes copied. Store them somewhere private." in security
    assert "recoveryCodes.focus();" in security
    assert "Copy was blocked. The recovery codes are selected" in security


def test_business_private_copy_controls_match_the_product_ui():
    assert ".business-private-copy" in STYLES
    assert ".business-copy-status" in STYLES
    assert ".business-recovery-label" in STYLES
