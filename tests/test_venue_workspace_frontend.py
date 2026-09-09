"""Execute venue presentation, concurrent edits, and asynchronous form saves."""

from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public/app-v15.js").read_text()
VENUE = ROOT / "public/venue-workspace-v15.js"


DETAILS_DOM = """
  function detailsDom(business) {
    const nodes = {}, navigation = [], sections = ['about', 'visit', 'contact'];
    const modal = {
      querySelector(selector) {
        if (nodes[selector]) return nodes[selector];
        const field = selector.replace('#business-', '').replaceAll('-', '_');
        const key = selector.replace('#venue-section-', '');
        const section = selector.replace('#venue-field-', '');
        const view = selector === '#venue-jump-preview' ? 'preview' : 'edit';
        return nodes[selector] = {
          value: Array.isArray(business[field]) ? business[field].join(', ') : String(business[field] ?? ''),
          validity: {valid: true}, handlers: {}, attributes: {},
          dataset: selector.startsWith('#venue-section-') ? {venueSection: key}
            : selector.startsWith('#venue-field-') ? {venueFieldSection: section}
            : selector === '#business-details-form' ? {view: 'edit'} : {venueEditorView: view},
          hidden: selector.startsWith('#venue-field-') && section !== 'about',
          classList: {add() {}},
          querySelector: selector => modal.querySelector(selector),
          setAttribute(name, value) {this.attributes[name] = value;},
          addEventListener(name, handler) {this.handlers[name] = handler;},
          closest(query) {
            if (query !== '[data-venue-field-section]') return null;
            const parent = ['hours', 'amenities'].includes(field) ? 'visit'
              : ['name', 'description', 'announcement'].includes(field) ? 'about' : 'contact';
            return modal.querySelector(`#venue-field-${parent}`);
          },
          scrollIntoView(options) {navigation.push({selector, scroll: options.block});},
          focus(options = {}) {navigation.push({selector, focused: true, preventScroll: options.preventScroll});},
        };
      },
      querySelectorAll(selector) {
        return (selector === '[data-venue-section]' ? sections.map(key => `#venue-section-${key}`)
          : selector === '[data-venue-editor-view]' ? ['#venue-back-edit', '#venue-jump-preview'] : [])
          .map(selector => modal.querySelector(selector));
      },
    };
    return {modal, nodes, navigation};
  }
"""


def run_js(script):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", f"""
          import fs from 'node:fs';
          import vm from 'node:vm';
          const fixedNow = '2026-09-08T12:00:00Z';
          const FrozenDate = class extends Date {{constructor(...args) {{super(...(args.length ? args : [fixedNow]));}}}};
          const sandbox = {{module: {{exports: {{}}}}, Date: FrozenDate, URL}};
          vm.runInNewContext(fs.readFileSync({json.dumps(str(VENUE))}, 'utf8'), sandbox);
          const Venue = sandbox.module.exports;
        """ + DETAILS_DOM + script], check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class Markup(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.words = []
        self.parents = {}
        self.stack = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.tags.append((tag, attributes))
        if attributes.get("id"):
            self.parents[attributes["id"]] = tuple(self.stack)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag):]

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


def test_extracted_presentation_helpers_are_exported_and_app_wrappers_forward_arguments():
    wrappers = []
    for name in ('businessHubEmptyHtml', 'venueTaskHtml', 'businessUnavailableHtml',
                 'businessRevisionDiffHtml', 'businessFileSize', 'businessFileDescription', 'setBusinessFilePickerState'):
        body = APP.split(f'  function {name}(', 1)[1].split('\n  }', 1)[0]
        wrappers.append(f'function {name}(' + body + '\n}')
    output = run_js('\n'.join(wrappers) + """
      const window = {VenueWorkspace: Venue}, uiIcon = name => `<span data-icon="${name}"></span>`;
      const names = ['welcome', 'task', 'unavailable', 'revisionDiff', 'fileSize', 'fileDescription', 'setFilePickerState'];
      const court = {name: 'Sunset & Coast'}, options = {tool: 'team', icon: 'users', title: 'Team', copy: 'Invite staff'};
      const error = {status: 503, message: 'Temporary failure'}, revision = {before_snapshot: {}, after_snapshot: {}};
      const file = {type: 'image/png', name: 'courts.png', size: 2048};
      console.log(JSON.stringify({
        exported: names.map(name => typeof Venue[name]), global: sandbox.VenueWorkspace === Venue,
        forwarding: [
          businessHubEmptyHtml(court) === Venue.welcome(court, uiIcon),
          venueTaskHtml(options) === Venue.task(options, uiIcon),
          businessUnavailableHtml('Bookings', error) === Venue.unavailable('Bookings', error, uiIcon),
          businessRevisionDiffHtml(revision) === Venue.revisionDiff(revision),
          businessFileSize(2048) === Venue.fileSize(2048),
          businessFileDescription(file, 'Image') === Venue.fileDescription(file, 'Image'),
          setBusinessFilePickerState(null) === Venue.setFilePickerState(null, {}, uiIcon),
        ],
      }));
    """)
    assert output == {'exported': ['function'] * 7, 'global': True, 'forwarding': [True] * 7}


def test_extracted_welcome_task_and_unavailable_states_preserve_escaping_and_semantics():
    malicious = '<img src=x onerror="attack()"> & " onclick="attack()'
    output = run_js(f'const malicious = {json.dumps(malicious)};' + """
      const icon = name => `<span data-icon="${name}" aria-hidden="true"></span>`;
      console.log(JSON.stringify({
        welcome: Venue.welcome({name: malicious}, icon),
        initial: Venue.welcome(null, icon),
        task: Venue.task({tool: 'ownership', icon: 'lock', title: malicious, copy: malicious, disabled: true}, icon),
        failures: [404, 501, 503].map(status => Venue.unavailable(malicious, {status, message: malicious}, icon)),
      }));
    """)
    for source in [output['welcome'], output['task'], *output['failures']]:
        markup = Markup(source)
        assert malicious in markup.text
        assert not [tag for tag, _ in markup.tags if tag in {'img', 'script'}]
        assert not [attrs for _, attrs in markup.tags if any(key.startswith('on') for key in attrs)]
    welcome = Markup(output['welcome'])
    assert len([tag for tag, _ in welcome.tags if tag == 'li']) == 3
    assert any(tag == 'button' and attrs.get('id') == 'business-claim-start' for tag, attrs in welcome.tags)
    assert 'Find my venue' in Markup(output['initial']).text
    task = Markup(output['task'])
    assert any(tag == 'button' and attrs.get('data-business-tool') == 'ownership' and 'disabled' in attrs for tag, attrs in task.tags)
    for index, source in enumerate(output['failures']):
        markup = Markup(source)
        assert any(attrs.get('role') == ('status' if index < 2 else 'alert') for _, attrs in markup.tags)
        assert ('not enabled yet' in markup.text) == (index < 2)


def test_extracted_revision_diff_keeps_changed_values_and_collection_counts_readable():
    data = {
        'before_snapshot': {'profile': {'name': 'Old name', 'published': False, 'unchanged': 'Same'}, 'schedule': []},
        'after_snapshot': {'profile': {'name': '<img src=x onerror="attack()">', 'published': True, 'unchanged': 'Same'},
                           'schedule': [{'id': 7}]},
    }
    markup = Markup(run_js(f'console.log(JSON.stringify(Venue.revisionDiff({json.dumps(data)})));'))
    for text in ['Name', 'Old name', data['after_snapshot']['profile']['name'], 'Published', 'No', 'Yes', 'Schedule', 'Not listed', 'Listed']:
        assert text in markup.text
    assert 'Unchanged' not in markup.text
    assert not [tag for tag, _ in markup.tags if tag in {'img', 'script'}]
    assert any(attrs.get('aria-label') == 'Changed business fields' for _, attrs in markup.tags)
    empty = Markup(run_js('console.log(JSON.stringify(Venue.revisionDiff({})));'))
    assert 'No value-level difference' in empty.text


def test_extracted_file_metadata_retains_size_units_type_names_and_fallbacks():
    output = run_js("""
      console.log(JSON.stringify({
        sizes: [null, -10, 42, 1024, 2097152, 12582912].map(Venue.fileSize),
        descriptions: [
          Venue.fileDescription({type: 'IMAGE/PNG', name: 'venue.png', size: 1024}),
          Venue.fileDescription({type: 'application/json', name: 'catalog.txt', size: 42}),
          Venue.fileDescription({name: 'schedule.csv', size: 2048}),
          Venue.fileDescription({name: 'unknown-extension', size: 0}, 'Document'),
          Venue.fileDescription(null),
        ],
      }));
    """)
    assert output['sizes'] == ['0 B', '0 B', '42 B', '1 KB', '2.0 MB', '12 MB']
    assert output['descriptions'] == ['PNG image · 1 KB', 'JSON · 42 B', 'CSV · 2 KB', 'Document · 0 B', 'File · 0 B']


def test_extracted_file_picker_renderer_preserves_busy_errors_recovery_and_literal_filenames():
    output = run_js("""
      const nodes = {};
      const picker = {
        dataset: {idleIcon: 'upload'}, attributes: {},
        toggleAttribute(name, on) {this.attributes[name] = on;},
        querySelector(selector) {
          return nodes[selector] ||= {attributes: {},
            setAttribute(name, value) {this.attributes[name] = value;},
            removeAttribute(name) {delete this.attributes[name];},
          };
        },
      };
      const transitions = [];
      for (const state of ['loading', 'error', 'success', 'idle']) {
        Venue.setFilePickerState(picker, {state, name: '<img src=x onerror="attack()">', meta: 'PNG image · 1 KB', badge: state},
          name => `icon:${name}`);
        transitions.push({state: picker.dataset.state, busy: picker.attributes['aria-busy'],
          role: nodes['[data-file-feedback]'].attributes.role,
          live: nodes['[data-file-feedback]'].attributes['aria-live'],
          invalid: nodes['[data-file-button]'].attributes['aria-invalid'] || null,
          icon: nodes['[data-file-state-icon]'].innerHTML,
          name: nodes['[data-file-name]'].textContent,
          nameHtml: nodes['[data-file-name]'].innerHTML || null,
        });
      }
      console.log(JSON.stringify(transitions));
    """)
    for index, row in enumerate(output):
        assert row['name'] == '<img src=x onerror="attack()">'
        assert row['nameHtml'] is None
        assert row['busy'] == (index == 0)
        assert row['role'] == ('alert' if index == 1 else 'status')
        assert row['live'] == ('assertive' if index == 1 else 'polite')
        assert row['invalid'] == ('true' if index == 1 else None)
    assert [row['icon'] for row in output] == ['icon:refresh', 'icon:alert-triangle', 'icon:check-circle', 'icon:upload']


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
        assert "Sat, Sep 12" in markup.text and "America/Los_Angeles" in markup.text
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


def test_detail_sections_keep_backend_limits_labels_and_save_inside_the_form():
    markup = Markup(run_js(f"""
      console.log(JSON.stringify(Venue.detailsForm({json.dumps(venue())},
        {{head: '', icon: () => '', hasManagedLogo: false}})));
    """))
    nodes = {attrs["id"]: (tag, attrs) for tag, attrs in markup.tags if "id" in attrs}
    expected_limits = {"name": "120", "description": "2000", "announcement": "500", "hours": "1000", "email": "255"}
    for field, limit in expected_limits.items():
        assert nodes[f"business-{field}"][1]["maxlength"] == limit
    for section in ["about", "visit", "contact"]:
        tab = nodes[f"venue-section-{section}"][1]
        panel = nodes[tab["aria-controls"]][1]
        assert tab["aria-selected"] == str(section == "about").lower()
        assert tab["tabindex"] == ("0" if section == "about" else "-1")
        assert panel["aria-labelledby"] == f"venue-section-{section}"
        assert ("hidden" in panel) == (section != "about")
    labels = {attrs.get("for") for tag, attrs in markup.tags if tag == "label"}
    assert {f"business-{field}" for field in expected_limits} <= labels
    # The save action belongs to the same form in both mobile editor views.
    structure = [tag for tag, _ in markup.tags]
    assert structure.count("form") == 1
    assert "form" in markup.parents["business-details-save"]
    assert "footer" in markup.parents["business-details-save"]
    assert nodes["business-details-save"][1]["type"] == "submit"
    assert nodes["business-details-form"][1]["data-view"] == "edit"
    assert nodes["venue-back-edit"][1]["aria-pressed"] == "true"
    assert nodes["venue-jump-preview"][1]["aria-pressed"] == "false"
    assert not [attrs for _, attrs in markup.tags if any(key.startswith("on") for key in attrs)]


def test_mobile_preview_keeps_edits_and_validation_reveals_the_right_section():
    result = run_js("""
      const business = {name: 'Venue', description: 'Saved copy', amenities: []};
      const {modal, nodes, navigation} = detailsDom(business), errors = [];
      const formUX = {isDirty: () => true, showError(message, target) {
        const section = target.closest('[data-venue-field-section]');
        errors.push({message, visible: !section.hidden, view: nodes['#business-details-form'].dataset.view});
        target.focus();
      }};
      const sync = Venue.bindDetailsEditor(modal, business, {formUX, icon: () => '', verified: true, publicNow: false});
      const active = () => ['about', 'visit', 'contact'].filter(key => !modal.querySelector(`#venue-field-${key}`).hidden);
      const states = [];
      nodes['#business-description'].value = 'My unsaved introduction';
      nodes['#business-details-form'].handlers.input();
      nodes['#venue-jump-preview'].handlers.click();
      states.push({view: nodes['#business-details-form'].dataset.view,
        pressed: nodes['#venue-jump-preview'].attributes['aria-pressed'], draft: nodes['#business-description'].value});
      formUX.showError('Enter a valid email.', nodes['#business-email']);
      states.push({view: nodes['#business-details-form'].dataset.view, active: active(),
        pressed: nodes['#venue-back-edit'].attributes['aria-pressed']});
      sync.reveal(nodes['#business-hours']); states.push({active: active()});
      const prevented = [];
      nodes['#venue-section-visit'].handlers.keydown({key: 'End', preventDefault() {prevented.push('End');}});
      states.push({active: active()});
      nodes['#venue-section-contact'].handlers.keydown({key: 'ArrowRight', preventDefault() {prevented.push('ArrowRight');}});
      states.push({active: active()});
      nodes['#venue-section-about'].handlers.keydown({key: 'Escape', preventDefault() {prevented.push('Escape');}});
      console.log(JSON.stringify({states, errors, prevented, navigation,
        draft: nodes['#business-description'].value, preview: nodes['#venue-details-preview'].innerHTML}));
    """)
    assert result["states"] == [
        {"view": "preview", "pressed": "true", "draft": "My unsaved introduction"},
        {"view": "edit", "active": ["contact"], "pressed": "true"},
        {"active": ["visit"]}, {"active": ["contact"]}, {"active": ["about"]},
    ]
    assert result["errors"] == [{"message": "Enter a valid email.", "visible": True, "view": "edit"}]
    assert result["prevented"] == ["End", "ArrowRight"]
    assert [event["selector"] for event in result["navigation"]] == ["#business-email", "#venue-section-contact", "#venue-section-about"]
    assert result["draft"] in result["preview"]


def test_changed_details_omits_untouched_fields_and_preserves_explicit_clears_and_arrays():
    result = run_js("""
      const original = {name: 'Venue', description: 'Saved', phone: '555-0100', amenities: ['Lights', 'Water'],
        announcement: null, updated_at: 'old', is_public: true};
      const fields = {name: 'Venue', description: 'New introduction', phone: '', amenities: ['Lights', 'Water'], announcement: '', website_url: ''};
      const before = JSON.stringify({original, fields});
      const changed = Venue.changedDetails(original, fields);
      console.log(JSON.stringify({changed, unchangedInputs: before === JSON.stringify({original, fields}),
        unchanged: Venue.changedDetails(original, {name: 'Venue', amenities: ['Lights', 'Water']}),
        reordered: Venue.changedDetails(original, {amenities: ['Water', 'Lights']}),
        cleared: Venue.changedDetails(original, {amenities: []}),
        emptyMissing: Venue.changedDetails({}, {amenities: [], description: ''}),
        added: Venue.changedDetails({}, {amenities: ['Parking']})}));
    """)
    assert result == {
        "changed": {"description": "New introduction", "phone": ""}, "unchangedInputs": True,
        "unchanged": {}, "reordered": {"amenities": ["Water", "Lights"]},
        "cleared": {"amenities": []}, "emptyMissing": {}, "added": {"amenities": ["Parking"]},
    }


def test_bulk_collection_guard_accepts_metadata_refresh_but_rejects_added_removed_or_changed_rows():
    result = run_js("""
      const original = [{id: 1, title: 'Open play', active: true, source_updated_at: 'old', sort_order: 0},
        {id: 2, title: 'Hidden clinic', active: false, booking_url: 'https://venue.test/book', freshness: {updated_at: 'old'}}];
      const refreshed = [{freshness: {updated_at: 'new'}, booking_url: 'https://venue.test/book', active: false, title: 'Hidden clinic', id: 2},
        {...original[0], source_updated_at: 'new', created_at: 'today', updated_at: 'today', sort_order: 1}];
      const before = JSON.stringify({original, refreshed});
      const attempt = items => {try {Venue.assertCollectionUnchanged(items, original); return null;} catch (error) {return error.message;}};
      console.log(JSON.stringify({accepted: attempt(refreshed), unchangedInputs: before === JSON.stringify({original, refreshed}),
        added: attempt([...original, {id: 3, title: 'Added elsewhere'}]), removed: attempt(original.slice(0, 1)),
        changedTitle: attempt([{...original[0], title: 'Renamed elsewhere'}, original[1]]),
        changedHidden: attempt([original[0], {...original[1], active: true}]),
        changedLink: attempt([original[0], {...original[1], booking_url: 'https://venue.test/new'}]),
        changedId: attempt([original[0], {...original[1], id: 3}])}));
    """)
    assert result.pop("accepted") is None
    assert result.pop("unchangedInputs") is True
    assert all("changed elsewhere" in message for message in result.values())


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
        assert "Each logo upload saves a draft immediately" in markup.text


def test_details_preserve_multiline_hours_and_save_only_changed_fields():
    start = APP.index("function openBusinessDetailsEditor(")
    source = APP[start:APP.index("function openBusinessIntegrationRequest", start)]
    result = run_js("""
      const business = {content_version: 'version-1', id: 7, name: 'Venue', hours: 'Monday 8 AM–8 PM\\nTuesday closed', amenities: [], logo_url: ''};
      const {modal, nodes} = detailsDom(business), requests = [];
      let html, saved;
      const window = {VenueWorkspace: Venue}, uiIcon = () => '', esc = String, modalHead = () => '';
      const normalizeBusinessProfile = value => value, businessCourtName = () => '', businessVerificationState = () => 'verified';
      const businessWorkspaceState = () => ({publicNow: false});
      const openModal = markup => {html = markup; return modal;};
      const bindModalFormUX = () => ({isDirty: () => true, clearError() {}, clearDraft() {},
        showError(message) {throw new Error(message);}, startSubmitting: () => () => {}});
      const bindModalDiscardConfirmation = () => {};
      const optionalBusinessUrl = () => '', closeModal = () => {}, toast = () => {};
      const api = async (path, options) => {if (!options) return business; requests.push({path, body: JSON.parse(options.body)}); return requests.at(-1).body;};
    """ + source + """
      openBusinessDetailsEditor(business, updated => {saved = updated;});
      const initialPreview = nodes['#venue-details-preview'].innerHTML;
      nodes['#business-hours'].value = ' Monday 7 AM–9 PM\\nTuesday closed\\nSaturday 9 AM–2 PM ';
      nodes['#business-details-form'].handlers.input();
      const editedPreview = nodes['#venue-details-preview'].innerHTML;
      await nodes['#business-details-form'].handlers.submit({preventDefault() {}});
      console.log(JSON.stringify({html, initialPreview, editedPreview, requests, saved}));
    """)
    nodes = {attrs["id"]: (tag, attrs) for tag, attrs in Markup(result["html"]).tags if "id" in attrs}
    assert nodes["business-hours"][0] == "textarea"
    assert "Monday 8 AM–8 PM\nTuesday closed" in result["html"]
    assert "Monday 8 AM–8 PM\nTuesday closed" in result["initialPreview"]
    edited = "Monday 7 AM–9 PM\nTuesday closed\nSaturday 9 AM–2 PM"
    assert edited in result["editedPreview"]
    assert result["requests"][0]["path"] == "/businesses/7"
    assert result["requests"][0]["body"] == result["saved"] == {"hours": edited}
    styles = (ROOT / "public/styles-v15.css").read_text()
    facts = styles[styles.index(".venue-facts dd {"):].split("}", 1)[0]
    assert "white-space: pre-wrap" in facts


@pytest.mark.parametrize("operation", ["upload", "remove"])
def test_logo_mutation_response_keeps_text_edits_and_reviewed_listing_live(operation):
    start = APP.index("function openBusinessDetailsEditor(")
    source = APP[start:APP.index("function openBusinessIntegrationRequest", start)]
    result = run_js("""
      const business = {content_version: 'version-1', id: 7, name: 'Venue', amenities: [], is_public: true, published: true,
        content_review_status: 'approved', logo_url: '/api/businesses/7/logo', has_logo_upload: true};
      const {modal, nodes} = detailsDom(business), requests = [], updates = [], errors = [];
      let confirm, cleared = 0;
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
        return {logo_url: '/api/businesses/7/logo', business: {...business, logo_url: options.method === 'DELETE' ? '' : '/api/businesses/7/logo', has_logo_upload: options.method !== 'DELETE', content_version: 'version-2', has_unpublished_changes: true, content_review_status: 'pending'}};
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
    assert result["initial"] == "Saved changes appear on your listing."
    assert result["after"] == "Saved to your draft. Your approved listing stays live."
    assert result["description"] == "My unsaved description"
    assert result["cleared"] == 0 and result["errors"] == []
    assert result["requests"] == [
        {"path": "/businesses/7/logo", "method": "POST" if operation == "upload" else "DELETE"},
    ]
    saved = result["updates"][0]
    assert saved["is_public"] is True and saved["published"] is True
    assert saved["content_review_status"] == "pending"
    assert saved["has_logo_upload"] == (operation == "upload")
    assert saved["logo_url"] == ("/api/businesses/7/logo" if operation == "upload" else "")


@pytest.mark.parametrize("operation", ["upload", "remove"])
@pytest.mark.parametrize("phone_edited", [False, True])
def test_logo_refresh_does_not_turn_untouched_contact_details_into_edits(operation, phone_edited):
    start = APP.index("function openBusinessDetailsEditor(")
    source = APP[start:APP.index("function openBusinessIntegrationRequest", start)]
    result = run_js("""
      const business = {content_version: 'version-1', id: 7, name: 'Venue', description: 'Original description', phone: '111', amenities: [],
        is_public: true, published: true, content_review_status: 'approved', logo_url: '/api/businesses/7/logo', has_logo_upload: true};
      const {modal, nodes} = detailsDom(business), patches = [], updates = [], errors = [];
      let confirm;
      const openBusinessConflictReview = async (modal, conflicts) => Object.fromEntries(conflicts.map(c => [c.key, 'mine']));
      const window = {VenueWorkspace: Venue}, uiIcon = () => '', modalHead = () => '';
      const normalizeBusinessProfile = value => value, businessCourtName = () => '', businessVerificationState = () => 'verified';
      const businessWorkspaceState = value => Venue.state(value, 'verified');
      const openModal = () => modal, openBusinessConfirmAction = options => {confirm = options;};
      const bindModalDiscardConfirmation = () => {}, closeModal = () => {}, toast = () => {};
      const bindModalFormUX = () => ({isDirty: () => true, clearError() {}, clearDraft() {},
        showError: message => errors.push(message), startSubmitting: () => () => {}});
      const businessFileDescription = () => 'PNG image', setBusinessFilePickerState = () => {};
      const imageFileToDataUrl = async () => 'data:image/png;base64,fixture', optionalBusinessUrl = () => '';
      const api = async (path, options) => {
        if (!options) return {...business, phone: '222', is_public: false, published: false, content_review_status: 'pending',
    """ + f"""logo_url: {json.dumps('/api/businesses/7/logo' if operation == 'upload' else '')}}};
    """ + """
        if (options.method === 'PATCH') patches.push(JSON.parse(options.body));
        return ['POST', 'DELETE'].includes(options.method) ? {logo_url: '/api/businesses/7/logo', business: {...business, phone: '222', logo_url: options.method === 'DELETE' ? '' : '/api/businesses/7/logo', has_logo_upload: options.method !== 'DELETE', content_version: 'version-2', has_unpublished_changes: true, content_review_status: 'pending'}} : {...business, ...(options.body ? JSON.parse(options.body) : {})};
      };
    """ + source + f"""
      openBusinessDetailsEditor(business, updated => updates.push({{...updated}}));
      nodes['#business-description'].value = 'My unsaved description';
      if ({str(phone_edited).lower()}) nodes['#business-phone'].value = '333';
      if ({json.dumps(operation)} === 'upload') {{
        const input = nodes['#business-logo-file']; input.files = [{{name: 'logo.png', type: 'image/png', size: 400}}];
        await input.handlers.change({{currentTarget: input}});
      }} else {{ nodes['#business-logo-remove'].handlers.click(); await confirm.onConfirm({{}}); }}
      const impact = nodes['#venue-details-save-impact'].textContent;
      await nodes['#business-details-form'].handlers.submit({{preventDefault() {{}}}});
      console.log(JSON.stringify({{patches, impact, errors, refreshedPhone: updates[0].phone,
        phone: nodes['#business-phone'].value, description: nodes['#business-description'].value}}));
    """)
    expected = {"description": "My unsaved description"}
    if phone_edited:
        expected["phone"] = "333"
    assert result["patches"] == [expected]
    assert result["refreshedPhone"] == "222"
    assert result["phone"] == ("333" if phone_edited else "111")
    assert result["description"] == "My unsaved description"
    assert result["errors"] == []
    assert result["impact"] == ("Saved as a draft for review. Your approved listing stays live." if phone_edited
        else "Saved to your draft. Your approved listing stays live.")


def test_booking_destination_preview_uses_only_safe_url_hostnames_as_text():
    result = run_js("""
      const input = {value: 'https://www.booking.example.test/courts', handlers: {},
        addEventListener(name, handler) {this.handlers[name] = handler;}};
      const destination = {};
      Venue.bindBookingPreview({querySelector: selector => selector === '#venue-booking-url' ? input : destination});
      const labels = [destination.textContent];
      for (const value of ['', 'https://', 'booking.example.test', 'javascript:alert(1)', 'data:text/html,<script>alert(1)</script>',
        'https://trusted.test@actual-provider.test/book', 'https://venue.test/path?q=<script>']) {
        input.value = value; input.handlers.input(); labels.push(destination.textContent);
      }
      input.value = 'https://new-provider.test/book'; input.handlers.change(); labels.push(destination.textContent);
      console.log(JSON.stringify({labels, properties: Object.keys(destination)}));
    """)
    assert result["labels"] == [
        "Continue to booking.example.test", *(["Add a link to your booking page"] * 5),
        "Continue to actual-provider.test", "Continue to venue.test", "Continue to new-provider.test",
    ]
    assert result["properties"] == ["textContent"]


def test_booking_save_omits_untouched_membership_even_after_child_refresh():
    start = APP.index("function openBusinessBookingSetup(")
    source = APP[start:APP.index("function renderBusinessHubDashboard", start)]
    result = run_js("""
      const business = {content_version: 'version-1', id: 7, name: 'Venue', manager_role: 'owner', booking_url: 'https://old.test/book', membership_url: 'https://venue.test/join'};
      const nodes = {}, requests = [], events = [];
      let childSaved;
      const modal = {querySelector(selector) {return nodes[selector] ||= {
        value: selector === '#venue-booking-url' ? business.booking_url : selector === '#venue-membership-url' ? business.membership_url : '',
        handlers: {}, addEventListener(name, handler) {this.handlers[name] = handler;}};}};
      const window = {VenueWorkspace: Venue}, uiIcon = () => '', modalHead = () => '';
      const normalizeBusinessProfile = value => value, openModal = () => modal, openChildModal = (modal, open) => open();
      const openBusinessScheduleEditor = (business, onSaved) => {childSaved = onSaved;};
      const bindModalFormUX = () => ({isDirty: () => true, clearError() {}, clearDraft() {events.push('clear');},
        startSubmitting: () => () => {}, showError(message) {throw new Error(message);}});
      const bindModalDiscardConfirmation = () => {}, closeModal = () => events.push('close'), toast = () => {};
      const optionalBusinessUrl = (modal, selector) => modal.querySelector(selector).value;
      const api = async (path, options) => {if (!options) return {...business, membership_url: 'https://teammate.test/join'}; requests.push({path, method: options.method, body: JSON.parse(options.body)});
        events.push('persisted'); return {...business, ...JSON.parse(options.body)};};
    """ + source + """
      openBusinessBookingSetup(business, () => events.push('saved'));
      nodes['#venue-booking-schedule'].handlers.click();
      childSaved({...business, membership_url: 'https://teammate.test/join'});
      events.length = 0;
      nodes['#venue-booking-url'].value = 'https://new.test/book';
      await nodes['#venue-booking-form'].handlers.submit({preventDefault() {}});
      console.log(JSON.stringify({requests, events}));
    """)
    assert result["requests"] == [{"path": "/businesses/7", "method": "PATCH", "body": {"booking_url": "https://new.test/book"}}]
    assert result["events"] == ["persisted", "clear", "close", "saved"]


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
        const business = {content_version: 'version-1', id: 7, name: 'Venue', manager_role: 'owner', is_public: false,
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
        const original = {id: 21, name: 'Original lesson', active: true}, business = {id: 7, offerings: [original]};
        const modal = {}, openBusinessConflictReview = async () => null;
        const requests = [], saved = [], fresh = {content_version: 'version-1', id: 7, offerings: [
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
    assert "Your edits are still here" in conflict["error"]


@pytest.mark.parametrize("kind", ["offerings", "schedule"])
@pytest.mark.parametrize("outcome", ["saved", "conflict", "failed"])
def test_bulk_editors_merge_fresh_siblings_before_put_and_keep_drafts_on_failure(kind, outcome):
    start = APP.index(f"    modal.querySelector('#business-{kind}-save').addEventListener('click'")
    source = APP[start:APP.index("    return modal;", start)]
    result = run_js(f"""
      const kind = {json.dumps(kind)}, outcome = {json.dumps(outcome)};
      const original = [{{id: 1, title: 'First', name: 'First', active: true}},
        {{id: 2, title: 'Hidden', name: 'Hidden', active: false}}];
      const business = {{id: 7, [kind]: original}};
      const draft = [{{...original[0], title: 'My edit', name: 'My edit'}}, original[1]];
      const offerings = draft, schedule = draft, requests = [], events = [], errors = [];
      const startImport = false;
      const button = {{addEventListener(name, callback) {{this[name] = callback;}}}};
      const modal = {{querySelector: () => button}}, window = {{VenueWorkspace: Venue}};
      const normalizeBusinessProfile = value => value;
      const formUX = {{startSubmitting() {{events.push('submitting'); return () => events.push('reset');}},
        showError: message => errors.push(message)}};
      const closeModal = () => events.push('close'), toast = () => events.push('toast'), onSaved = () => events.push('saved');
      let finishPut;
      const api = async (path, options) => {{
        requests.push({{path, method: options?.method || 'GET', ...(options ? {{items: JSON.parse(options.body).items}} : {{}})}});
        if (!options) return {{content_version: 'version-1', id: 7, [kind]: outcome === 'conflict'
          ? [...original, {{id: 3, title: 'Added elsewhere', name: 'Added elsewhere', active: false}}] : original}};
        return new Promise((resolve, reject) => {{finishPut = () => outcome === 'failed'
          ? reject(new Error('Save unavailable. Try again.')) : resolve({{id: 7, [kind]: draft}});}});
      }};
    """ + source + """
      const pending = button.click();
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
      const before = [...events];
      finishPut?.(); await pending;
      console.log(JSON.stringify({requests, before, events, errors, draft}));
    """)
    assert result["requests"][0] == {"path": "/businesses/7", "method": "GET"}
    assert result["draft"][0]["title"] == "My edit"
    assert result["draft"][1]["active"] is False
    assert result["before"] == ["submitting"]
    expected_items = result["draft"] + ([{"id": 3, "title": "Added elsewhere", "name": "Added elsewhere", "active": False}] if outcome == "conflict" else [])
    assert result["requests"][1] == {"path": f"/businesses/7/{kind}", "method": "PUT", "items": expected_items}
    if outcome == "failed":
        assert result["events"] == ["submitting", "reset"]
        assert result["errors"] == ["Save unavailable. Try again."]
    else:
        assert result["events"] == ["submitting", "close", "toast", "saved"]
        assert result["errors"] == []


@pytest.mark.parametrize("kind", ["offering", "schedule"])
@pytest.mark.parametrize("failed", [False, True])
def test_direct_forms_wait_for_persistence_and_keep_edits_when_saving_fails(kind, failed):
    function_name = "openBusinessOfferingForm" if kind == "offering" else "openBusinessScheduleItemForm"
    end_name = "function openBusinessOfferingsEditor" if kind == "offering" else "function businessScheduleCsvTemplate"
    start = APP.index("function " + function_name)
    source = APP[start:APP.index(end_name, start)]
    result = run_js("""
      const events = [], errors = [], nodes = {};
      const window = {VenueWorkspace: Venue};
      const values = {
        'business-offering-name': 'My lesson', 'business-offering-category': 'lesson', 'business-offering-duration': '60',
        'business-schedule-title': 'My session', 'business-schedule-kind': 'open_play', 'business-schedule-day': 'monday',
        'business-schedule-start': '09:00', 'business-schedule-end': '11:00', 'business-schedule-recurrence': 'weekly',
        'business-schedule-capacity': '24', 'business-schedule-spots': '0', 'business-schedule-status': 'scheduled',
        'business-schedule-timezone': 'America/Los_Angeles',
      };
      const modal = {querySelectorAll() {return [];}, querySelector(selector) {
        return nodes[selector] ||= {value: values[selector.slice(1)] || '', checked: true, handlers: {},
          closest: () => ({}), addEventListener(name, handler) {this.handlers[name] = handler;}};
      }};
      let html, resolveSave, rejectSave, submitted;
      const openModal = markup => {html = markup; return modal;}, modalHead = () => '', esc = String;
      const BUSINESS_OFFERING_CATEGORIES = {lesson: ['target', 'Lesson']};
      const BUSINESS_SCHEDULE_KINDS = {open_play: ['calendar', 'Open play']};
      const BUSINESS_DAY_LABELS = ['Monday'];
      const businessDayLabel = value => value, optionalBusinessUrl = () => 'https://venue.test/book';
      const safePositiveId = value => Number(value) > 0 && Number.isSafeInteger(Number(value)) ? Number(value) : null;
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
        assert "Save service" in result["html"]
    assert "Update list" not in result["html"]
    if failed:
        assert result["events"] == ["submitting", "persist", "reset"]
        assert result["errors"] == ["Could not save. Retry your changes."]
    else:
        assert result["events"] == ["submitting", "persist", "clear draft", "close"]
        assert result["errors"] == []


def test_reviewed_draft_status_and_null_metrics_do_not_claim_live_changes_or_zero():
    result = run_js("""
      const business = {is_public: true, published: true, content_review_status: 'pending',
        has_unpublished_changes: true, manager_role: 'owner', name: 'Draft venue'};
      console.log(JSON.stringify({status: Venue.state(business, 'verified'),
        saved: Venue.savedStatus(business), preview: Venue.preview(business, () => '', {saved: true, publicNow: true}),
        metrics: [null, undefined, 0, 12, '0%'].map(Venue.metricValue),
        headers: Venue.contentHeaders({content_version: 'abc123'})}));
    """)
    assert result['status']['publicNow'] is True
    assert 'approved listing stays live' in result['status']['copy']
    assert 'Draft saved for review' in result['saved']
    assert 'Private preview' in Markup(result['preview']).text
    assert result['metrics'] == ['—', '—', '0', '12', '0%']
    assert result['headers'] == {'If-Match': '"abc123"'}


def test_analytics_waiter_cannot_replace_newer_range_with_old_response():
    start = APP.index('function openBusinessAnalytics(')
    source = APP[start:APP.index('function openBusinessRevisionHistory', start)]
    result = run_js("""
      let change; const pending=[];
      const results={isConnected:true,innerHTML:'',querySelector(){return null;}};
      const range={value:'30d',addEventListener(type,fn){change=fn;}};
      const modal={querySelector(sel){return sel==='[data-analytics-results]' ? results : range;}};
      const openModal=()=>modal,modalHead=()=>'',skeletonHtml=()=> 'Loading',window={VenueWorkspace:Venue},fmtDateTime=v=>v;
      const api=path=>new Promise((resolve,reject)=>pending.push({path,resolve,reject}));
      const businessUnavailableHtml=()=> 'Request failed';
    """+source+"""
      openBusinessAnalytics({id:7}); range.value='7d'; const latest=change();
      pending[1].resolve({profile_views:12,booking_clicks:4,lesson_clicks:0,previous:{profile_views:10,booking_clicks:1,lesson_clicks:0},top_sessions:[{title:'Latest session',clicks:4}]}); await latest;
      const current=results.innerHTML;
      pending[0].resolve({profile_views:999,top_sessions:[{title:'Old session',clicks:999}]}); await Promise.resolve(); await Promise.resolve();
      console.log(JSON.stringify({current,after:results.innerHTML,requests:pending.map(item=>item.path)}));
    """)
    assert result['after'] == result['current']
    assert 'Latest session' in result['after'] and 'Old session' not in result['after']
    assert '+3 vs previous period' in result['after']
    assert result['requests'][-1].endswith('range=7d')


def test_analytics_and_team_capabilities_do_not_invent_booking_conversion_or_access():
    result = run_js("""
      console.log(JSON.stringify({html:Venue.analyticsHtml({profile_views:0,booking_clicks:0,conversions:null,top_sessions:[]},v=>v),editor:Venue.teamCapabilities('editor'),viewer:Venue.teamCapabilities('viewer'),admin:Venue.teamCapabilities('admin')}));
    """)
    assert 'Booking reports are not connected' in result['html']
    assert 'conversion rate is unavailable' in result['html']
    assert '0%' not in result['html']
    assert 'cannot publish or invite' in result['editor']
    assert 'cannot edit, publish or invite' in result['viewer']
    assert 'publish' in result['admin']


def test_visiting_edits_reconcile_independent_facts_and_name_overlaps():
    result = run_js("""
      const original = {visitor_info:{entrance:'North gate',parking:'Old lot'}};
      const edits = {visitor_info:{entrance:'South gate',parking:'Old lot'}};
      const current = {visitor_info:{entrance:'North gate',parking:'New lot'}};
      const merged = Venue.reconcileProfile(original, edits, current);
      const changed = {visitor_info:{entrance:'East gate',parking:'New lot'}};
      const conflict = Venue.reconcileProfile(original, edits, changed);
      const resolved = Venue.reconcileProfile(original, edits, changed, {'visitor_info:entrance':'mine'});
      console.log(JSON.stringify({merged,conflict,resolved}));
    """)
    assert result['merged']['value']['visitor_info'] == {'entrance':'South gate','parking':'New lot'}
    assert result['merged']['conflicts'] == []
    assert result['conflict']['conflicts'][0]['label'] == 'Finding the entrance'
    assert result['conflict']['conflicts'][0]['mine'] == 'South gate'
    assert result['resolved']['value']['visitor_info'] == {'entrance':'South gate','parking':'New lot'}


def test_revision_explains_visitor_facts_and_service_names():
    result = run_js("""
      console.log(JSON.stringify(Venue.revisionDiff({
        before_snapshot:{profile:{visitor_info:'{"parking":"Old lot"}'},offerings:[{id:7,name:'Beginner clinic'}],schedule:[{id:4,title:'Tuesday class',offering_id:null}]},
        after_snapshot:{profile:{visitor_info:'{"parking":"North lot"}'},offerings:[{id:7,name:'Beginner clinic'}],schedule:[{id:4,title:'Tuesday class',offering_id:7}]},
      })));
    """)
    assert 'Parking: Old lot' in result and 'Parking: North lot' in result
    assert 'Standalone session' in result and 'Beginner clinic' in result
    assert 'Visitor info' not in result
