from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'frontend' / 'app.js').read_text()
CSS = (ROOT / 'frontend' / 'styles.css').read_text()


def test_every_chat_surface_uses_the_durable_outbox():
    channels = ('dm', 'court', 'game', 'tournament', 'club', 'league')
    for channel in channels:
        assert f'`{channel}:${{' in APP
    assert APP.count('chatUX.activateOutbox(') == len(channels)
    assert APP.count('await chatUX.send(body)') == len(channels)


def test_outbox_is_durable_idempotent_and_account_scoped():
    assert "indexedDB.open(CHAT_OUTBOX_DB_NAME, 1)" in APP
    assert "client_attempt_id: item.attemptId" in APP
    assert "delivered: terminalDeleted ? null : delivered" in APP
    assert "chatOutboxRecordId(accountId, attemptId)" in APP
    assert "purgeAccountChatOutbox(accountId);" in APP
    assert "flushChatOutboxForAccount(state.me && state.me.id);" in APP
    assert "status >= 500" in APP


def test_pending_and_failed_messages_are_actionable_on_mobile():
    assert 'data-client-attempt-id' in APP
    assert 'data-outbox-retry' in APP
    assert 'data-outbox-remove' in APP
    assert '.chat-outbox-item.is-failed' in CSS
    assert '.chat-outbox-actions button' in CSS
    assert 'min-height: 44px' in CSS
