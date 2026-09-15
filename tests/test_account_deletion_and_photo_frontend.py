"""Regression contracts for destructive account and court-photo flows."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
STYLES = (ROOT / 'public' / 'styles-v15.css').read_text()


def section(start, end):
    start_at = APP.index(start)
    return APP[start_at:APP.index(end, start_at)]


def test_account_deletion_fetches_exact_impact_and_waits_on_success_screen():
    impact_copy = section(
        'function accountDeletionImpactCopy',
        'function openAccountDeletionConfirmation',
    )
    confirmation = section(
        'function openAccountDeletionConfirmation',
        'function showAccountDeletionSuccess',
    )
    success = section(
        'function showAccountDeletionSuccess',
        'function openAccountSettings',
    )
    settings = section(
        'function openAccountSettings',
        'function moderationEvidenceText',
    )

    for consequence in (
        'Player account', 'Match history', 'Tournaments', 'Leagues',
        'Communities', 'Businesses',
    ):
        assert consequence in impact_copy
    assert "item.action === 'transfer'" in impact_copy
    assert 'will be cancelled' in impact_copy
    assert 'will be unpublished, unverified, and relinquished' in impact_copy
    assert 'class="account-deletion-impact-list"' in confirmation
    assert "impact = await api('/me/deletion-impact')" in settings
    assert 'openAccountDeletionConfirmation(impact, button)' in settings

    # The server success is rendered inside the app; logout happens only after
    # the player acknowledges that final state.
    assert 'Your account has been deleted' in success
    assert 'data-account-delete-finish' in success
    assert "localStorage.removeItem('pp_token')" in success
    assert 'authSessionEpoch += 1;' in success
    assert "finish?.addEventListener('click', () => logout(" in success
    assert 'showAccountDeletionSuccess(modal, result.effects || impact' in settings
    assert '.account-deletion-impact-list' in STYLES
    assert '.account-deletion-success' in STYLES


def test_court_photo_upload_keeps_gallery_context_and_uses_a_guarded_child_form():
    picker = section('const uploadCourtPhoto =', 'const courtShareUrl =')
    editor = section('function openCourtPhotoUpload', 'function galleryPhotoMetaHtml')
    gallery = section('async function openCourtGallery', 'async function openGameChat')
    assert 'openChildModal(contextModal,()=>openCourtPhotoUpload' in picker
    assert 'contextBox.innerHTML =' not in picker
    assert 'ERROR_TEXT.photo_too_large' in picker and 'ERROR_TEXT.invalid_photo' in picker
    assert 'routedOverlayLoadIsCurrent(modalLoad)' in picker
    assert 'state.me?.id!==ownerId' in picker
    assert 'bindModalDiscardConfirmation(modal' in editor
    assert "formUX.startSubmitting('Adding photo…')" in editor
    assert 'fields.disabled=true' in editor and 'fields.disabled=false' in editor
    assert 'commitCourtPhoto(court,result)' in editor
    assert 'photos.unshift(result.photo)' in gallery
    assert 'transitionModal(modal,()=>openCourtGallery' not in gallery
    assert 'court-upload-preview' in STYLES
