from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "public" / "styles-v15.css").read_text()


def test_missing_venue_action_stacks_cleanly_on_narrow_phones():
    narrow = CSS[CSS.index("@media (max-width: 360px)"):]
    assert ".business-missing-venue { align-items: flex-start; flex-direction: column; gap: 0; }" in narrow
    assert ".business-missing-venue .btn-link" in narrow
    assert "width: 100%; justify-content: flex-start" in narrow
