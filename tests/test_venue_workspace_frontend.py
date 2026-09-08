"""Execute venue presentation, concurrent edits, and asynchronous form saves."""

from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public/app-v15.js").read_text()
VENUE = ROOT / "public/venue-workspace-v15.js"


def run_js(script):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", f"""
          import fs from 'node:fs';
          import vm from 'node:vm';
          const fixedNow = '2026-09-08T12:00:00Z';
          const FrozenDate = class extends Date {{constructor(...args) {{super(...(args.length ? args : [fixedNow]));}}}};
          const sandbox = {{module: {{exports: {{}}}}, Date: FrozenDate}};
          vm.runInNewContext(fs.readFileSync({json.dumps(str(VENUE))}, 'utf8'), sandbox);
          const Venue = sandbox.module.exports;
        """ + script], check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


class Markup(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.words = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.words.append(data)

    @property
    def text(self):
        return " ".join(self.words)


def venue():
    return {
        "id": 7, "name": 'A & B <script>alert("venue")</script>',
        "description": '<img src=x onerror="alert(1)"> Friendly courts',
        "hours": "Mon–Fri 8 AM–9 PM", "amenities": ["Lights", "Water & shade"],
        "phone": "555-0100", "email": "venue@example.test", "website_url": "https://example.test",
        "announcement": '<b>Welcome & play</b>',
        "booking_url": 'https://example.test/?q="<script>x</script>',
        "offerings": [
            {"id": 21, "name": 'Beginner "lesson" <script>x</script>', "active": True, "category": "lesson", "duration_minutes": 60},
            {"id": 22, "name": "Hidden private lesson", "active": False, "category": "lesson"},
        ],
        "schedule": [
            {"id": 31, "title": "Open play & learn", "active": True, "recurrence": "dated", "event_date": "2026-09-12", "start_time": "09:00", "end_time": "11:00", "timezone": "America/Los_Angeles", "capacity": 24, "status": "cancelled"},
            {"id": 32, "title": "Hidden winter session", "active": False, "recurrence": "date_range", "day_of_week": "monday", "start_date": "2026-12-01", "end_date": "2027-01-31", "start_time": "10:00", "end_time": "12:00"},
        ],
    }


def render(data, **options):
    return Markup(run_js(f"""
      const options = {{icon: name => `<svg data-icon="${{name}}"></svg>`, day: day => day,
        time: time => time, canEdit: true, workspace: {{publicNow: false}}, ...{json.dumps(options)}}};
      console.log(JSON.stringify(Venue.render({json.dumps(data)}, options)));
    """))


def test_venue_content_and_preview_escape_names_copy_and_saved_links():
    data = venue()
    for markup in [render(data), Markup(run_js(f"console.log(JSON.stringify(Venue.preview({json.dumps(data)}, () => '')));"))]:
        assert data["name"] in markup.text
        assert data["description"] in markup.text
        assert data["announcement"] in markup.text
        assert not [tag for tag, _ in markup.tags if tag in {"script", "img"}]
        assert not [attrs for _, attrs in markup.tags if any(key.startswith("on") for key in attrs)]
    rendered = render(data)
    assert data["booking_url"] in rendered.text
    assert any(attrs.get("aria-label") == f'Edit {data["offerings"][0]["name"]}' for _, attrs in rendered.tags)


def test_owner_lists_keep_hidden_rows_and_distinguish_saved_from_public():
    data = venue()
    for public in [False, True]:
        markup = render(data, workspace={"publicNow": public})
        assert markup.text.count("Hidden") == 4  # Two titles plus two visibility badges.
        assert "Hidden private lesson" in markup.text and "Hidden winter session" in markup.text
        expected = "On your listing" if public else "Saved · listing private"
        assert markup.text.count(expected) == 2
        assert ("Saved · listing private" if public else "On your listing") not in markup.text
        assert "cancelled" in markup.text
        assert "2026-12-01 to 2027-01-31" in markup.text
        assert "2026-09-12" in markup.text and "America/Los_Angeles" in markup.text
        assert {attrs["data-item-id"] for _, attrs in markup.tags if "data-venue-edit" in attrs} == {"21", "22", "31", "32"}
    viewer = render(data, canEdit=False)
    tools = [attrs for _, attrs in viewer.tags if "data-business-tool" in attrs]
    assert tools and all("disabled" in attrs for attrs in tools)
    assert not [attrs for _, attrs in viewer.tags if "data-venue-edit" in attrs or "data-venue-remove" in attrs]
    assert "Hidden private lesson" in viewer.text


def test_past_sessions_remain_editable_without_claiming_they_are_public():
    data = venue()
    data["offerings"] = []
    data["schedule"] = [
        {"id": 1, "title": "Past dated", "active": True, "recurrence": "dated", "event_date": "2026-09-07"},
        {"id": 2, "title": "Past date range", "active": True, "recurrence": "date_range", "end_date": "2026-09-07"},
        {"id": 3, "title": "Completed legacy session", "active": True, "status": "completed"},
        {"id": 4, "title": "Today session", "active": True, "recurrence": "dated", "event_date": "2026-09-08"},
        {"id": 5, "title": "Future session", "active": True, "recurrence": "dated", "event_date": "2026-09-09"},
    ]
    markup = render(data, workspace={"publicNow": True})
    assert markup.text.count("Past session · no longer listed") == 3
    assert markup.text.count("On your listing") == 2
    assert {attrs["data-item-id"] for _, attrs in markup.tags if "data-venue-edit" in attrs} == {"1", "2", "3", "4", "5"}
    result = run_js("""
      const dated = {recurrence: 'dated', event_date: '2026-09-08', timezone: 'America/Los_Angeles'};
      const range = {recurrence: 'date_range', end_date: '2026-09-08', timezone: 'America/Los_Angeles'};
      const beforeMidnight = new Date('2026-09-09T06:30:00Z'), afterMidnight = new Date('2026-09-09T07:01:00Z');
      console.log(JSON.stringify([
        Venue.sessionIsCurrent(dated, beforeMidnight), Venue.sessionIsCurrent(dated, afterMidnight),
        Venue.sessionIsCurrent({...dated, timezone: 'UTC'}, beforeMidnight),
        Venue.sessionIsCurrent(range, beforeMidnight), Venue.sessionIsCurrent(range, afterMidnight),
      ]));
    """)
    assert result == [True, False, False, True, False]


def test_rendered_tabs_have_one_selected_panel_and_safe_fallback():
    for requested, selected in [("schedule", "schedule"), ("unknown", "details")]:
        markup = render(venue(), panel=requested)
        tabs = [attrs for _, attrs in markup.tags if attrs.get("role") == "tab"]
        panels = {attrs["id"]: attrs for _, attrs in markup.tags if attrs.get("role") == "tabpanel"}
        assert len(tabs) == len(panels) == 4
        assert [attrs["data-icon"] for _, attrs in markup.tags if "data-icon" in attrs][:4] == ["building", "calendar", "target", "link"]
        for tab in tabs:
            active = tab["data-venue-panel"] == selected
            assert tab["aria-selected"] == str(active).lower()
            assert tab["tabindex"] == ("0" if active else "-1")
            panel = panels[tab["aria-controls"]]
            assert panel["aria-labelledby"] == tab["id"]
            assert ("hidden" not in panel) == active


def test_extracted_logo_fields_keep_accessible_picker_states_and_escape_saved_urls():
    url = 'https://venue.test/logo.png?q=" onfocus="alert(1)'
    for managed in [False, True]:
        markup = Markup(run_js(f"""
          console.log(JSON.stringify(Venue.logoFields({{logo_url: {json.dumps(url)}}},
            {{icon: () => '', hasManagedLogo: {str(managed).lower()}}})));
        """))
        nodes = {attrs["id"]: (tag, attrs) for tag, attrs in markup.tags if "id" in attrs}
        assert nodes["business-logo-url"][1]["value"] == url
        assert all("onfocus" not in attrs for _, attrs in markup.tags)
        picker = nodes["business-logo-file-picker"][1]
        assert picker["data-state"] == ("success" if managed else "idle")
        native = nodes["business-logo-file"][1]
        assert native["type"] == "file" and native["tabindex"] == "-1" and native["aria-hidden"] == "true"
        button = nodes["business-logo-file-button"][1]
        assert button["type"] == "button"
        assert all(label in nodes for label in button["aria-labelledby"].split())
        assert all(label in nodes for label in button["aria-describedby"].split())
        feedback = nodes["business-logo-upload-status"][1]
        assert feedback["role"] == "status" and feedback["aria-live"] == "polite"
        assert ("hidden" not in nodes["business-logo-remove"][1]) == managed
        assert ("Replace uploaded logo" if managed else "Choose logo image") in markup.text
        assert "Logo uploads save immediately" in markup.text


def test_details_preserve_multiline_hours_in_editor_preview_and_save_and_restore_edit_focus():
    start = APP.index("function openBusinessDetailsEditor(")
    source = APP[start:APP.index("function openBusinessIntegrationRequest", start)]
    result = run_js("""
      const business = {id: 7, name: 'Venue', hours: 'Monday 8 AM–8 PM\\nTuesday closed', amenities: [], logo_url: ''};
      const values = Object.fromEntries(Object.entries(business).map(([key, value]) => [`#business-${key}`, String(value)]));
      const nodes = {}, navigation = [], requests = [];
      const modal = {querySelector(selector) {
        return nodes[selector] ||= {value: values[selector] || '', validity: {valid: true}, handlers: {},
          classList: {add() {}}, addEventListener(name, handler) {this.handlers[name] = handler;},
          scrollIntoView(options) {navigation.push({selector, scroll: options.block});},
          focus(options) {navigation.push({selector, focused: true, preventScroll: options.preventScroll});}};
      }};
      let html, saved;
      const window = {VenueWorkspace: Venue}, uiIcon = () => '', esc = String, modalHead = () => '';
      const normalizeBusinessProfile = value => value, businessCourtName = () => '', businessVerificationState = () => 'verified';
      const businessWorkspaceState = () => ({publicNow: false});
      const openModal = markup => {html = markup; return modal;};
      const bindModalFormUX = () => ({isDirty: () => true, clearError() {}, clearDraft() {},
        showError(message) {throw new Error(message);}, startSubmitting: () => () => {}});
      const bindModalDiscardConfirmation = () => {};
      const optionalBusinessUrl = () => '', closeModal = () => {}, toast = () => {};
      const api = async (path, options) => {requests.push({path, body: JSON.parse(options.body)}); return requests.at(-1).body;};
    """ + source + """
      openBusinessDetailsEditor(business, updated => {saved = updated;});
      const initialPreview = nodes['#venue-details-preview'].innerHTML;
      nodes['#venue-jump-preview'].handlers.click(); nodes['#venue-back-edit'].handlers.click();
      nodes['#business-hours'].value = ' Monday 7 AM–9 PM\\nTuesday closed\\nSaturday 9 AM–2 PM ';
      nodes['#business-details-form'].handlers.input();
      const editedPreview = nodes['#venue-details-preview'].innerHTML;
      await nodes['#business-details-form'].handlers.submit({preventDefault() {}});
      console.log(JSON.stringify({html, initialPreview, editedPreview, requests, saved, navigation}));
    """)
    nodes = {attrs["id"]: (tag, attrs) for tag, attrs in Markup(result["html"]).tags if "id" in attrs}
    assert nodes["business-hours"][0] == "textarea"
    assert "Monday 8 AM–8 PM\nTuesday closed" in result["html"]
    assert "Monday 8 AM–8 PM\nTuesday closed" in result["initialPreview"]
    edited = "Monday 7 AM–9 PM\nTuesday closed\nSaturday 9 AM–2 PM"
    assert edited in result["editedPreview"]
    assert result["requests"][0]["path"] == "/businesses/7"
    assert result["requests"][0]["body"]["hours"] == result["saved"]["hours"] == edited
    assert result["navigation"] == [
        {"selector": ".venue-editor-preview", "scroll": "start"},
        {"selector": ".venue-editor-preview", "focused": True, "preventScroll": True},
        {"selector": "#business-name", "scroll": "center"},
        {"selector": "#business-name", "focused": True, "preventScroll": True},
    ]
    styles = (ROOT / "public/styles-v15.css").read_text()
    facts = styles[styles.index(".venue-facts dd {"):].split("}", 1)[0]
    assert "white-space: pre-wrap" in facts


@pytest.mark.parametrize("operation", ["upload", "remove"])
def test_logo_mutation_refresh_failure_keeps_text_edits_and_updates_private_visibility(operation):
    start = APP.index("function openBusinessDetailsEditor(")
    source = APP[start:APP.index("function openBusinessIntegrationRequest", start)]
    result = run_js("""
      const business = {id: 7, name: 'Venue', is_public: true, published: true,
        content_review_status: 'approved', logo_url: '/api/businesses/7/logo', has_logo_upload: true};
      const nodes = {}, requests = [], updates = [], errors = [];
      let confirm, cleared = 0;
      const modal = {querySelector(selector) {
        return nodes[selector] ||= {value: String(business[selector.replace('#business-', '').replaceAll('-', '_')] || ''),
          validity: {valid: true}, handlers: {}, classList: {add() {}},
          querySelector: selector => modal.querySelector(selector),
          addEventListener(name, handler) {this.handlers[name] = handler;},
          scrollIntoView() {}, focus() {}};
      }};
      const window = {VenueWorkspace: Venue}, uiIcon = () => '', esc = String, modalHead = () => '';
      const normalizeBusinessProfile = value => value, businessCourtName = () => '', businessVerificationState = () => 'verified';
      const businessWorkspaceState = business => Venue.state(business, 'verified');
      const openModal = () => modal, openBusinessConfirmAction = options => {confirm = options;};
      const bindModalFormUX = () => ({isDirty: () => true, clearError() {}, clearDraft() {cleared += 1;},
        showError: message => errors.push(message), startSubmitting: () => () => {}});
      const bindModalDiscardConfirmation = () => {}, closeModal = () => {}, toast = () => {};
      const businessFileDescription = () => 'PNG image', setBusinessFilePickerState = () => {};
      const imageFileToDataUrl = async () => 'data:image/png;base64,fixture';
      const api = async (path, options) => {
        requests.push({path, method: options?.method || 'GET'});
        if (!options) throw new Error('Refresh is offline');
        return {logo_url: '/api/businesses/7/logo'};
      };
    """ + source + f"""
      openBusinessDetailsEditor(business, updated => updates.push({{...updated}}));
      const initial = nodes['#venue-details-save-impact'].textContent;
      nodes['#business-description'].value = 'My unsaved description';
      if ({json.dumps(operation)} === 'upload') {{
        const input = nodes['#business-logo-file'];
        input.files = [{{name: 'logo.png', type: 'image/png', size: 400}}];
        await input.handlers.change({{currentTarget: input}});
      }} else {{
        nodes['#business-logo-remove'].handlers.click(); await confirm.onConfirm({{}});
      }}
      console.log(JSON.stringify({{initial, after: nodes['#venue-details-save-impact'].textContent,
        requests, updates, errors, cleared, description: nodes['#business-description'].value}}));
    """)
    assert result["initial"] == "Saved changes appear on your player listing."
    assert result["after"] == "Changes save to your venue. Your listing is currently private."
    assert result["description"] == "My unsaved description"
    assert result["cleared"] == 0 and result["errors"] == []
    assert result["requests"] == [
        {"path": "/businesses/7/logo", "method": "POST" if operation == "upload" else "DELETE"},
        {"path": "/businesses/7", "method": "GET"},
    ]
    saved = result["updates"][0]
    assert saved["is_public"] is False and saved["published"] is False
    assert saved["content_review_status"] == "pending"
    assert saved["has_logo_upload"] == (operation == "upload")
    assert saved["logo_url"] == ("/api/businesses/7/logo" if operation == "upload" else "")


def test_field_errors_open_nested_optional_sections_before_focusing_the_field():
    start = APP.index("    const showError = (message, target = null) =>")
    source = APP[start:APP.index("    const clearEditedError =", start)]
    result = run_js("""
      const outer = {open: false}, inner = {open: false, parentElement: {closest: () => outer}};
      let attached = false, focusedAfterOpening = false;
      const field = {appendChild: () => {attached = true;}}, attributes = {'aria-describedby': 'field-help'};
      const target = {dataset: {}, closest: selector => selector === 'details' ? inner : field,
        setAttribute: (name, value) => {attributes[name] = value;}, getAttribute: name => attributes[name],
        scrollIntoView() {}, focus() {focusedAfterOpening = outer.open && inner.open;}};
      const error = {id: 'form-error', classList: {remove() {}}}, clearError = () => {};
      const invalidTargets = new Set(), presentationTarget = target => target;
    """ + source + """
      showError('Enter a valid email.', target);
      console.log(JSON.stringify({attached, focusedAfterOpening, attributes, message: error.textContent,
        tracked: invalidTargets.has(target)}));
    """)
    assert result["attached"] and result["focusedAfterOpening"] and result["tracked"]
    assert result["attributes"] == {"aria-describedby": "field-help form-error", "aria-invalid": "true"}
    assert result["message"] == "Enter a valid email."


def test_only_confirmed_discard_clears_persisted_details_and_booking_drafts():
    start = APP.index("  function bindModalDiscardConfirmation(")
    source = APP[start:APP.index("\n  function ", start + 1)]
    bindings = []
    for function in ["openBusinessDetailsEditor", "openBusinessBookingSetup"]:
        at = APP.index("function " + function)
        binding = APP.index("bindModalDiscardConfirmation(modal,", at)
        bindings.append(APP[binding:APP.index("\n", binding)])
    result = run_js("""
      let accepted = false, closed = 0, modal;
      const openActionConfirmation = async () => accepted;
      const currentOverlayEntry = () => ({el: modal});
      const closeModal = () => {closed += 1;};
    """ + source + f"""
      const outputs = [];
      for (const binding of {json.dumps(bindings)}) {{
        const clears = []; closed = 0; accepted = false;
        modal = {{isConnected: true, querySelector: () => null, _cleanupFns: []}};
        const formUX = {{isDirty: () => true, clearDraft: options => clears.push(options)}};
        new Function('modal', 'formUX', 'bindModalDiscardConfirmation', binding)(modal, formUX, bindModalDiscardConfirmation);
        await modal._onDismissBlocked();
        const kept = {{clears: clears.length, closed, blocked: modal._dismissBlocked()}};
        accepted = true; await modal._onDismissBlocked();
        outputs.push({{kept, clears, closed, blocked: modal._dismissBlocked()}});
      }}
      console.log(JSON.stringify(outputs));
    """)
    assert result == [{
        "kept": {"clears": 0, "closed": 0, "blocked": True},
        "clears": [{"disable": True}], "closed": 1, "blocked": False,
    }] * 2


def test_returning_from_booking_children_keeps_newly_saved_sessions_on_reopen():
    start = APP.index("function openBusinessBookingSetup(")
    source = APP[start:APP.index("function renderBusinessHubDashboard", start)]
    result = run_js("""
      const nodes = {}, opened = [], adopted = [];
      let childSaved;
      const modal = {querySelector: selector => nodes[selector] ||= {
        handlers: {}, addEventListener(name, handler) {this.handlers[name] = handler;}}};
      const normalizeBusinessProfile = value => value, openModal = () => modal;
      const window = {VenueWorkspace: Venue};
      const esc = String, uiIcon = () => '', modalHead = () => '';
      const openChildModal = (modal, open) => open();
      const openBusinessScheduleEditor = (business, onSaved) => {opened.push(business); childSaved = onSaved;};
      const openBusinessConnections = (business, onSaved) => {opened.push(business); childSaved = onSaved;};
      const bindModalFormUX = () => ({isDirty: () => false});
      const bindModalDiscardConfirmation = () => {};
    """ + source + """
      const original = {id: 7, manager_role: 'owner', schedule: [{id: 31, title: 'First session'}]};
      openBusinessBookingSetup(original, updated => adopted.push(updated));
      nodes['#venue-booking-schedule'].handlers.click();
      childSaved({...original, schedule: [...original.schedule, {id: 32, title: 'Added session'}]});
      nodes['#venue-booking-schedule'].handlers.click();
      nodes['#venue-booking-feed'].handlers.click();
      childSaved({...opened[2], schedule: [...opened[2].schedule, {id: 33, title: 'Imported session'}]});
      nodes['#venue-booking-schedule'].handlers.click();
      console.log(JSON.stringify({opened: opened.map(business => business.schedule.map(item => item.id)),
        adopted: adopted.map(business => business.schedule.map(item => item.id))}));
    """)
    assert result == {"opened": [[31], [31, 32], [31, 32], [31, 32, 33]], "adopted": [[31, 32], [31, 32, 33]]}


def test_manager_can_unpublish_an_enabled_listing_even_when_players_cannot_see_it():
    start = APP.index("function renderBusinessHubDashboard(")
    markup_source = APP[start:APP.index("    const updateBusiness =", start)]
    binding_start = APP.index("    body.querySelector('#business-publish-toggle').addEventListener", start)
    binding = APP[binding_start:APP.index("    body.querySelector('#business-hub-locations')", binding_start)]
    result = run_js("""
      async function scenario(review) {
        const business = {id: 7, name: 'Venue', manager_role: 'owner', is_public: false,
          published: true, content_review_status: review, offerings: [], schedule: []};
        const requests = [], confirmations = [], updates = [];
        let accepted = false;
        const window = {VenueWorkspace: Venue}, uiIcon = () => '', esc = String;
        const businessVerificationState = () => 'verified', businessCourtName = () => 'Courts';
        const businessWorkspaceState = business => Venue.state(business, 'verified');
        const businessDayLabel = String, businessTimeLabel = String, venueTaskHtml = () => '';
        const openActionConfirmation = async options => {confirmations.push(options.title); return accepted;};
        const toast = () => {};
        const api = async (path, options) => {requests.push({path, method: options.method, body: JSON.parse(options.body)});
          return {...business, published: false};};
    """ + markup_source + """
        const updateBusiness = updated => updates.push(updated);
    """ + binding + """
      }
        const button = {addEventListener(name, callback) {this[name] = callback;}};
        const body = {classList: {add() {}}, querySelector: () => button};
        renderBusinessHubDashboard({querySelector: () => null}, body, business, {businesses: []});
        await button.click({currentTarget: button});
        const requestsAfterCancel = requests.length;
        accepted = true; await button.click({currentTarget: button});
        return {html: body.innerHTML, requestsAfterCancel, requests, confirmations,
          publishedAfterSave: updates[0].published};
      }
      console.log(JSON.stringify([await scenario('approved'), await scenario('pending')]));
    """)
    for state in result:
        markup = Markup(state["html"])
        buttons = [attrs for _, attrs in markup.tags if attrs.get("id") == "business-publish-toggle"]
        assert len(buttons) == 1 and "disabled" not in buttons[0]
        assert "Unpublish venue" in markup.text
        assert "Live on the court map" not in markup.text
        assert state["requestsAfterCancel"] == 0
        assert state["confirmations"] == ["Unpublish this business profile?"] * 2
        assert state["requests"] == [{"path": "/businesses/7", "method": "PATCH", "body": {"published": False}}]
        assert state["publishedAfterSave"] is False


def test_merge_retains_fresh_siblings_and_hidden_rows_without_mutating_inputs():
    result = run_js("""
      const original = {id: 21, name: 'Lesson', active: true, updated_at: 'old',
        source_updated_at: 'old', freshness: 'stale', sort_order: 0};
      const fresh = [{id: 19, name: 'Added elsewhere', active: false},
        {...original, updated_at: 'new', source_updated_at: 'new', freshness: 'fresh', sort_order: 1},
        {id: 22, name: 'Changed elsewhere', active: false}];
      const before = JSON.stringify(fresh), updated = {...original, id: 99, name: 'Edited lesson'};
      const edited = Venue.mergeItem(fresh, updated, original);
      const removed = Venue.mergeItem(fresh, null, original);
      console.log(JSON.stringify({edited, removed, unchanged: before === JSON.stringify(fresh), original, updated}));
    """)
    assert result["edited"][0] == result["removed"][0] == {"id": 19, "name": "Added elsewhere", "active": False}
    assert result["edited"][2] == result["removed"][1] == {"id": 22, "name": "Changed elsewhere", "active": False}
    assert result["edited"][1]["name"] == "Edited lesson"
    assert result["edited"][1]["id"] == 21
    assert {key: result["edited"][1][key] for key in ["updated_at", "source_updated_at", "freshness", "sort_order"]} == {
        "updated_at": "new", "source_updated_at": "new", "freshness": "fresh", "sort_order": 1,
    }
    assert result["original"]["name"] == "Lesson"
    assert result["updated"]["id"] == 99
    assert result["unchanged"]


def test_merge_refuses_changed_or_deleted_targets_instead_of_overwriting_them():
    result = run_js("""
      const original = {id: 21, name: 'Lesson', active: true, booking_url: 'https://venue.test/lesson'};
      const attempt = (fresh, updated, target = original) => {
        try { Venue.mergeItem(fresh, updated, target); return null; } catch (error) { return error.message; }
      };
      console.log(JSON.stringify({
        changedName: attempt([{...original, name: 'New name'}], {...original, name: 'My draft'}),
        changedVisibility: attempt([{...original, active: false}], null),
        changedLink: attempt([{...original, booking_url: 'https://venue.test/new'}], original),
        deleted: attempt([{id: 22, name: 'Other lesson'}], original),
        invalidId: attempt([{id: 0, name: 'Unowned'}], {name: 'Change'}, {id: 0, name: 'Unowned'}),
      }));
    """)
    for key in ["changedName", "changedVisibility", "changedLink"]:
        assert "changed elsewhere" in result[key]
    for key in ["deleted", "invalidId"]:
        assert "removed elsewhere" in result[key]


def test_collection_limit_still_allows_editing_and_removing_and_new_items_cannot_claim_ids():
    result = run_js("""
      const full = Array.from({length: 100}, (_, i) => ({id: i + 1, name: `Item ${i + 1}`}));
      const incoming = {id: 77, name: 'New lesson'};
      let error;
      try { Venue.mergeItem(full, incoming); } catch (caught) { error = caught.message; }
      const added = Venue.mergeItem(full.slice(0, 99), incoming);
      console.log(JSON.stringify({error, addedCount: added.length, newItem: added.at(-1), incoming,
        edited: Venue.mergeItem(full, {id: 999, name: 'Changed'}, full[0])[0],
        remaining: Venue.mergeItem(full, null, full[0]).length}));
    """)
    assert "up to 100 items" in result["error"]
    assert result["addedCount"] == 100 and result["remaining"] == 99
    assert result["newItem"] == {"name": "New lesson"}
    assert result["incoming"]["id"] == 77
    assert result["edited"] == {"id": 1, "name": "Changed"}


def test_tabs_support_click_arrow_wrap_home_end_and_leave_other_keys_alone():
    result = run_js("""
      const changes = [], focused = [], prevented = [], keys = ['details', 'schedule', 'offerings', 'booking'];
      const panels = Object.fromEntries(keys.map(key => [`#venue-panel-${key}`, {hidden: key !== 'details'}]));
      const tabs = keys.map(key => ({dataset: {venuePanel: key}, attributes: {}, handlers: {},
        setAttribute(name, value) {this.attributes[name] = value;},
        addEventListener(name, handler) {this.handlers[name] = handler;}, focus() {focused.push(key);}}));
      Venue.bindTabs({querySelectorAll: () => tabs, querySelector: key => panels[key]}, key => changes.push(key));
      const press = (index, key) => tabs[index].handlers.keydown({key, preventDefault: () => prevented.push(key)});
      const snapshot = () => ({selected: tabs.filter(t => t.attributes['aria-selected'] === 'true').map(t => t.dataset.venuePanel),
        tabbable: tabs.filter(t => t.tabIndex === 0).map(t => t.dataset.venuePanel),
        visible: keys.filter(key => !panels[`#venue-panel-${key}`].hidden)});
      tabs[1].handlers.click(); const click = snapshot();
      press(1, 'End'); const end = snapshot(); press(3, 'ArrowRight'); const wrapRight = snapshot();
      press(0, 'ArrowLeft'); const wrapLeft = snapshot(); press(3, 'Home'); const home = snapshot();
      press(0, 'Escape'); const untouched = snapshot();
      console.log(JSON.stringify({click, end, wrapRight, wrapLeft, home, untouched, changes, focused, prevented}));
    """)
    for key, selected in [("click", "schedule"), ("end", "booking"), ("wrapRight", "details"), ("wrapLeft", "booking"), ("home", "details"), ("untouched", "details")]:
        assert result[key] == {"selected": [selected], "tabbable": [selected], "visible": [selected]}
    assert result["changes"] == ["schedule", "booking", "details", "booking", "details"]
    assert result["focused"] == ["booking", "details", "booking", "details"]
    assert result["prevented"] == ["End", "ArrowRight", "ArrowLeft", "Home"]


def test_direct_workspace_save_merges_a_fresh_get_and_only_reports_success_after_put():
    start = APP.index("    const saveItem = async (kind, updated, original) =>")
    source = APP[start:APP.index("    const editItem =", start)]
    result = run_js("""
      async function scenario(conflict = false) {
        const business = {id: 7}, original = {id: 21, name: 'Original lesson', active: true};
        const requests = [], saved = [], fresh = {id: 7, offerings: [
          {...original, name: conflict ? 'Changed elsewhere' : original.name},
          {id: 22, name: 'New sibling', active: false},
        ]};
        let finish;
        const window = {VenueWorkspace: Venue}, normalizeBusinessProfile = value => value;
        const savedBusiness = value => saved.push(value);
        const api = async (path, options) => {
          requests.push({path, ...(options || {})});
          if (!options) return fresh;
          return new Promise(resolve => {finish = resolve;});
        };
    """ + source + """
        let error = null;
        const pending = saveItem('offerings', {...original, name: 'My change'}, original).catch(caught => {error = caught.message;});
        await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
        const savedBeforePut = saved.length;
        if (finish) finish({id: 7, offerings: JSON.parse(requests[1].body).items, is_public: false});
        await pending;
        return {requests, savedBeforePut, saved, error};
      }
      console.log(JSON.stringify({success: await scenario(), conflict: await scenario(true)}));
    """)
    success, conflict = result["success"], result["conflict"]
    assert success["requests"][0] == {"path": "/businesses/7"}
    assert success["requests"][1]["path"] == "/businesses/7/offerings"
    assert success["requests"][1]["method"] == "PUT"
    assert json.loads(success["requests"][1]["body"])["items"] == [
        {"id": 21, "name": "My change", "active": True}, {"id": 22, "name": "New sibling", "active": False},
    ]
    assert success["savedBeforePut"] == 0 and len(success["saved"]) == 1
    assert len(conflict["requests"]) == 1 and conflict["saved"] == []
    assert "changed elsewhere" in conflict["error"]


@pytest.mark.parametrize("kind", ["offering", "schedule"])
@pytest.mark.parametrize("failed", [False, True])
def test_direct_forms_wait_for_persistence_and_keep_edits_when_saving_fails(kind, failed):
    function_name = "openBusinessOfferingForm" if kind == "offering" else "openBusinessScheduleItemForm"
    end_name = "function openBusinessOfferingsEditor" if kind == "offering" else "function businessScheduleCsvTemplate"
    start = APP.index("function " + function_name)
    source = APP[start:APP.index(end_name, start)]
    result = run_js("""
      const events = [], errors = [], nodes = {};
      const values = {
        'business-offering-name': 'My lesson', 'business-offering-category': 'lesson', 'business-offering-duration': '60',
        'business-schedule-title': 'My session', 'business-schedule-kind': 'open_play', 'business-schedule-day': 'monday',
        'business-schedule-start': '09:00', 'business-schedule-end': '11:00', 'business-schedule-recurrence': 'weekly',
        'business-schedule-capacity': '24', 'business-schedule-spots': '0', 'business-schedule-status': 'scheduled',
        'business-schedule-timezone': 'America/Los_Angeles',
      };
      const modal = {querySelector(selector) {
        return nodes[selector] ||= {value: values[selector.slice(1)] || '', checked: true, handlers: {},
          closest: () => ({}), addEventListener(name, handler) {this.handlers[name] = handler;}};
      }};
      let html, resolveSave, rejectSave, submitted;
      const openModal = markup => {html = markup; return modal;}, modalHead = () => '', esc = String;
      const BUSINESS_OFFERING_CATEGORIES = {lesson: ['target', 'Lesson']};
      const BUSINESS_SCHEDULE_KINDS = {open_play: ['calendar', 'Open play']};
      const BUSINESS_DAY_LABELS = ['Monday'];
      const businessDayLabel = value => value, optionalBusinessUrl = () => 'https://venue.test/book';
      const bindModalDiscardConfirmation = () => {};
      const bindModalFormUX = () => ({
        isDirty: () => true, clearError() {}, showError: message => errors.push(message),
        startSubmitting: () => {events.push('submitting'); return () => events.push('reset');},
        clearDraft: () => events.push('clear draft'),
      });
      const closeModal = () => events.push('close');
    """ + source + f"""
      {function_name}({{}}, -1, updated => {{
        submitted = updated; events.push('persist');
        return new Promise((resolve, reject) => {{resolveSave = resolve; rejectSave = reject;}});
      }}, {{persist: true}});
      const form = nodes[{json.dumps('#business-offering-form' if kind == 'offering' else '#business-schedule-item-form')}];
      const pending = form.handlers.submit({{preventDefault() {{}}}});
      await Promise.resolve(); const before = [...events];
      if ({str(failed).lower()}) rejectSave(new Error('Could not save. Retry your changes.')); else resolveSave();
      await pending;
      console.log(JSON.stringify({{before, events, errors, submitted, html,
        draft: nodes[{json.dumps('#business-offering-name' if kind == 'offering' else '#business-schedule-title')}].value}}));
    """)
    assert result["before"] == ["submitting", "persist"]
    assert result["draft"] == ("My lesson" if kind == "offering" else "My session")
    assert result["submitted"]["active"] is True
    if kind == "schedule":
        assert result["submitted"]["capacity"] == 24 and result["submitted"]["spots_remaining"] == 0
        assert "Save session" in result["html"]
    else:
        assert result["submitted"]["duration_minutes"] == 60
        assert "Save lesson or service" in result["html"]
    assert "Update list" not in result["html"]
    if failed:
        assert result["events"] == ["submitting", "persist", "reset"]
        assert result["errors"] == ["Could not save. Retry your changes."]
    else:
        assert result["events"] == ["submitting", "persist", "clear draft", "close"]
        assert result["errors"] == []
