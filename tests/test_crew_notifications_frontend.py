from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
CSS = (ROOT / 'public' / 'styles-v15.css').read_text()


def section(start, end):
    return APP[APP.index(start):APP.index(end, APP.index(start))]


def test_play_group_notification_picker_is_accessible_and_persisted():
    detail = section('async function openCrewScreen', 'function openRenameCrewSheet')
    picker = section(
        'function openCrewNotificationSheet', 'async function openCrewChatById',
    )

    assert 'id="crew-notifications"' in detail
    assert 'Notifications: ${notificationLabel}' in detail
    assert 'openCrewNotificationSheet(crew' in detail
    assert 'role="radiogroup"' in picker
    assert 'role="radio"' in picker
    assert "['all', 'All messages'" in picker
    assert "['mentions', 'Mentions only'" in picker
    assert "['off', 'Mute this group'" in picker
    assert '`/crews/${crew.id}/notification-settings`' in picker
    assert "method: 'PATCH'" in picker
    assert '.crew-manage-actions #crew-notifications { grid-column: 1 / -1; }' in CSS
