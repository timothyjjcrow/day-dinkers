"""Conversation history, composer, and reconnect UX contracts."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
CSS = (ROOT / 'public' / 'styles-v15.css').read_text()


def test_every_conversation_can_load_older_history_without_scroll_jump():
    assert 'function attachChatHistoryLoader' in APP
    assert "button.textContent = 'Load earlier messages'" in APP
    assert "page?.has_older === true" in APP
    assert "page?.next_before_id" in APP
    for endpoint in (
        '`/chat/${userId}?before_id=${beforeId}&limit=100`',
        '`/courts/${court.id}/chat?before_id=${beforeId}&limit=60`',
        '`/games/${game.id}/chat?before_id=${beforeId}&limit=60`',
        '`/tournaments/${t.id}/chat?before_id=${beforeId}&limit=60`',
        '`/leagues/${lg.id}/chat?before_id=${beforeId}&limit=60`',
        '`/crews/${crew.id}/chat?before_id=${beforeId}&limit=60`',
        '`/clubs/${club.id}/chat?before_id=${beforeId}&limit=60`',
    ):
        assert endpoint in APP
    assert APP.count('preserveAnchor: prepend') == 7
    assert "msgsEl.insertAdjacentHTML('afterbegin', html)" in APP
    assert '.chat-history-loader' in CSS


def test_composers_are_multiline_with_enter_send_and_shift_enter_newline():
    assert APP.count('maxlength="2000" rows="1"></textarea>') == 7
    assert "inputEl.tagName === 'TEXTAREA'" in APP
    assert "event.key !== 'Enter' || event.shiftKey" in APP
    assert 'form?.requestSubmit();' in APP
    assert 'Math.min(inputEl.scrollHeight, 128)' in APP
    assert '.thread-input input, .thread-input textarea' in CSS


def test_message_links_dates_and_live_regions_are_readable():
    assert 'function chatBodyHtml' in APP
    assert 'target="_blank" rel="noopener noreferrer"' in APP
    assert 'href="tel:${digits}"' in APP
    assert 'function fmtMessageTimestamp' in APP
    assert 'Yesterday · ${time}' in APP
    assert APP.count('class="thread-msgs"') == 7
    assert APP.count('class="thread-msgs" id=') == APP.count('aria-live="off" aria-relevant="additions"')
    hydrate = APP[APP.index('function hydrateChatLoadShell'):APP.index('// Actionable empty destinations')]
    assert "shell.box.querySelector('.thread-input textarea')" in hydrate
    assert "composer.focus({ preventScroll: true })" in hydrate
    assert APP.count('data-img-id="${m.id}"') >= 6
    assert 'class="chat-image-loading" style="margin-bottom:${m.body ? \'6px\' : \'0\'}" role="status"' not in APP


def test_message_actions_do_not_permanently_shrink_every_bubble():
    assert 'function chatMessageActionHtml' in APP
    assert 'onMessageActionReveal' in APP
    assert "row.classList.toggle('actions-visible', show)" in APP
    assert '.chat-message-row.actions-visible .chat-message-actions' in CSS
    action = CSS[CSS.index('.chat-message-actions {'):CSS.index('.chat-message-action {')]
    assert 'position: absolute' in action
    assert 'opacity: 0' in action
    assert 'pointer-events: none' in action
    helper = APP[APP.index('function chatMessageActionHtml'):APP.index('function chatBodyHtml')]
    assert "message.recipient_id == null ? `${heart}${remove}` : remove" in helper


def test_failed_poll_shows_reconnecting_state_and_keeps_backoff():
    poll = APP[APP.index('function startAdaptiveChatPoll'):APP.index('function prepareChatRenderBatch')]
    assert 'consecutiveFailures += 1' in poll
    assert 'Reconnecting… New messages may be delayed.' in poll
    assert 'Still reconnecting… Your draft is safe.' in poll
    assert 'CHAT_POLL_DELAYS_MS' in poll
    assert 'let fastUntil = startedAt + 2 * 60_000;' in poll
    assert 'if (extendFast) fastUntil = Date.now() + 2 * 60_000;' in poll
    assert '.chat-reconnect-notice' in CSS
