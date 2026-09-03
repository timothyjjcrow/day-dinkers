from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def arrival_detail_source():
    start = APP.index("function openArrivalDetails")
    end = APP.index("async function confirmArrivalAtCourtAndJoin", start)
    return APP[start:end]


def test_not_coming_requires_a_branded_consequence_confirmation():
    detail = arrival_detail_source()
    handler = detail[detail.index("querySelector('#arrival-cancel')"):]
    assert "addEventListener('click', async (event)" in handler
    assert "await openActionConfirmation({" in handler
    assert "title: 'Not coming?'" in handler
    assert "Players at ${arrival.courtName} will no longer see that you’re on the way." in handler
    assert "This does not affect game capacity. You can share a new ETA later." in handler
    assert "confirmLabel: 'Not coming'" in handler
    assert "cancelLabel: 'Keep sharing'" in handler
    assert "trigger: button" in handler


def test_arrival_is_only_cancelled_after_confirmation_and_parent_survival_check():
    detail = arrival_detail_source()
    handler = detail[detail.index("querySelector('#arrival-cancel')"):]
    confirmation = handler.index("await openActionConfirmation({")
    parent_check = handler.index("if (!modal.isConnected) return;")
    mutation = handler.index("cancelRallyArrival(arrival, modal, button")
    assert confirmation < parent_check < mutation
