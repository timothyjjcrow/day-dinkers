"""Focused contracts for business file-picking and schedule CSV UI."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    start_at = APP.index(start)
    return APP[start_at:APP.index(end, start_at)]


def test_business_file_inputs_are_hidden_behind_named_keyboard_buttons():
    catalog = section("function openBusinessCatalogUpload", "function openBusinessAddConnection")
    details = section("function openBusinessDetailsEditor", "function openBusinessIntegrationRequest")

    for markup, stem in ((catalog, "business-catalog-file"), (details, "business-logo-file")):
        assert f'class="business-file-native" type="file" id="{stem}"' in markup
        assert 'tabindex="-1" aria-hidden="true"' in markup
        assert f'id="{stem}-button" data-file-button' in markup

    assert "fileButton.addEventListener('click', () => fileInput.click())" in catalog
    assert "logoFileButton.addEventListener('click', () => logoFileInput.click())" in details

    assert 'class="sr-only" type="file" id="business-catalog-file"' not in catalog
    assert '<input type="file" id="business-logo-file"' not in details


def test_business_file_pickers_expose_selection_metadata_and_live_status():
    helpers = section("function businessFileSize", "function openBusinessCatalogUpload")
    catalog = section("function openBusinessCatalogUpload", "function openBusinessAddConnection")
    details = section("function openBusinessDetailsEditor", "function openBusinessIntegrationRequest")

    assert "businessFileSize(file?.size)" in helpers
    for label in ("JSON", "JPEG image", "PNG image", "WebP image"):
        assert f"'{label}'" in helpers
    assert "picker.dataset.state = state" in helpers
    assert "picker.toggleAttribute('aria-busy', state === 'loading')" in helpers
    assert "feedback.setAttribute('role', 'alert')" in helpers
    assert "button.setAttribute('aria-invalid', 'true')" in helpers
    assert "feedback.setAttribute('aria-live', 'polite')" in helpers

    for markup in (catalog, details):
        assert 'class="business-file-feedback"' in markup
        assert 'data-file-name' in markup
        assert 'data-file-meta' in markup
        assert 'data-file-state' in markup
        assert 'role="status" aria-live="polite" aria-atomic="true"' in markup
        assert "businessFileDescription(file" in markup


def test_catalog_file_selection_validates_before_reading_and_stays_editable():
    catalog = section("function openBusinessCatalogUpload", "function openBusinessAddConnection")

    assert "looksLikeJson" in catalog
    assert "Choose a JSON file ending in .json." in catalog
    assert "That JSON file is empty." in catalog
    assert "file.size > 1024 * 1024" in catalog
    assert "Keep the JSON file under 1 MB." in catalog
    assert "fileButton.disabled = true" in catalog
    assert "input.value = await file.text()" in catalog
    assert "input.dispatchEvent(new Event('input', { bubbles: true }))" in catalog
    assert "loaded into editor" in catalog
    assert "Replace JSON file" in catalog
    assert "That file could not be read. Choose another JSON file." in catalog


def test_schedule_csv_import_is_free_staged_and_accessible():
    importer = section(
        "function businessScheduleCsvTemplate",
        "function openBusinessScheduleEditor",
    )
    editor = section(
        "function openBusinessScheduleEditor",
        "function openNotificationSettings",
    )

    assert "Use any spreadsheet — free." in importer
    assert "Download CSV template" in importer
    assert "new Blob([businessScheduleCsvTemplate(business)]" in importer
    assert 'class="business-file-native" type="file" id="business-schedule-csv-file"' in importer
    assert 'accept=".csv,text/csv,application/vnd.ms-excel,text/plain"' in importer
    assert 'role="status" aria-live="polite" aria-atomic="true"' in importer
    assert "file.size > 256 * 1024" in importer
    assert "input.value = await file.text()" in importer
    assert "/schedule/import-preview" in importer
    assert "Nothing is published until you review the rows" in importer
    assert '<option value="append">' in importer
    assert '<option value="replace">' in importer

    assert 'id="business-schedule-import"' in editor
    assert 'id="business-schedule-import-status"' in editor
    assert "mode === 'replace' ? items : [...schedule, ...items]" in editor
    assert "combined.length > 100" in editor
    assert "Choose Save schedule to publish." in editor


def test_logo_selection_validates_processes_and_reports_every_state():
    details = section("function openBusinessDetailsEditor", "function openBusinessIntegrationRequest")

    assert "looksLikeImage" in details
    assert "Choose a PNG, JPEG, or WebP logo." in details
    assert "That logo file is empty." in details
    assert "file.size > 12 * 1024 * 1024" in details
    assert "Choose a logo smaller than 12 MB." in details
    assert "detailsSaveButton.disabled = true" in details
    assert "imageFileToDataUrl(file, 768)" in details
    assert "uploaded securely" in details
    assert "logoRemoveButton.hidden = false" in details
    assert "could not be compressed below 512 KB" in details
    assert "That image could not be opened." in details
    assert "if (uploadError) formUX.showError(uploadError, logoFileButton)" in details
    assert "logoRemoveButton.hidden = true" in details
    assert "Uploaded logo removed" in details


def test_business_file_picker_styles_preserve_focus_tap_size_and_narrow_layout():
    assert ".business-file-native {" in STYLES
    native = STYLES[STYLES.index(".business-file-native {"):]
    native = native[:native.index("}")]
    for rule in ("position: absolute", "width: 1px", "clip-path: inset(50%)", "pointer-events: none"):
        assert rule in native

    button = STYLES[STYLES.index(".business-file-button {"):]
    button = button[:button.index("}")]
    assert "width: 100%" in button
    assert "min-height: 60px" in button
    assert ".business-file-button:focus-visible" in STYLES
    assert '.business-file-button[aria-invalid="true"]' in STYLES
    for state in ("loading", "success", "error"):
        assert f'.business-file-picker[data-state="{state}"]' in STYLES
    assert "@media (max-width: 360px)" in STYLES
    assert ".business-file-button-cta { display: none; }" in STYLES
