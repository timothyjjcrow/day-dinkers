from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def test_dynamic_app_select_observers_are_owned_by_their_modal():
    assert "const ownerModal = select.closest('.modal-backdrop');" in APP
    assert "ownerModal._cleanupFns.push(() => disconnectAppSelect(select));" in APP
    assert "select._appSelectCleanupRegistered = true;" in APP


def test_removed_app_selects_disconnect_observers_and_sheet_callbacks():
    assert "function disconnectAppSelect(select)" in APP
    assert "select._appSelectObserver?.disconnect();" in APP
    assert "delete select._refreshAppSelectSheet;" in APP
    assert "function disconnectAppSelects(root)" in APP
    assert "mutation.removedNodes.forEach" in APP
    assert "!node.isConnected" in APP
