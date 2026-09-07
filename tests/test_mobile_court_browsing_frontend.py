"""Execute the mobile map/list contract against the production JavaScript.

These regressions exercise the transitions that previously hid the selected
court and trapped the map behind a partially visible list.
"""

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public/app-v15.js").read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def run_js(script):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


DOM_HARNESS = r"""
  class Element {
    constructor(name) {
      this.name = name; this.dataset = {}; this.attributes = {};
      this.handlers = {}; this.children = new Map(); this.scrollTop = 0;
      this.innerHTML = ''; this.textContent = ''; this.inert = false;
      const values = new Set();
      this.classList = {
        add: (...names) => names.forEach(name => values.add(name)),
        remove: (...names) => names.forEach(name => values.delete(name)),
        contains: name => values.has(name),
        toggle(name, active) {
          if (active === undefined) active = !values.has(name);
          active ? values.add(name) : values.delete(name);
        },
      };
      this.style = {removeProperty() {}, setProperty() {}};
    }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    getAttribute(key) { return this.attributes[key] ?? null; }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(type, handler) { this.handlers[type] = handler; }
    focus() { document.activeElement = this; }
    contains(element) { return element === this || element?.parent === this; }
    replaceChildren() { this.innerHTML = ''; this.children.clear(); }
    getBoundingClientRect() { return {top: 100, bottom: 720, height: 620, left: 0, width: 390}; }
    querySelector(selector) {
      if (!this.children.has(selector)) this.children.set(selector, new Element(selector));
      return this.children.get(selector);
    }
    querySelectorAll() { return []; }
  }
  const nodes = Object.fromEntries([
    '#court-list', '#court-list-items', '#map', '#court-sheet-cycle',
    '#main-screen', '#court-preview', '#court-selection-status',
    '#court-sheet-status', '#court-search', '#tab-courts', '#court-list-context',
    '#court-area-settings', '#use-map-area',
  ].map(selector => [selector, new Element(selector)]));
  nodes['#court-preview'].classList.add('hidden');
  const viewButtons = ['map', 'list'].map(view => {
    const button = new Element(view); button.dataset.courtView = view; return button;
  });
  const $ = selector => nodes[selector] || null;
  const document = {
    activeElement: null,
    querySelector: selector => selector.startsWith('[data-court-view=')
      ? viewButtons.find(button => selector.includes(`"${button.dataset.courtView}"`))
      : nodes[selector] || null,
    querySelectorAll: selector => selector.includes('data-court-view') ? viewButtons : [],
  };
  const window = {innerWidth: 390, innerHeight: 844};
  const requestAnimationFrame = fn => fn();
  const state = {
    courtSheetSnap: 'peek', courtListExpandedScrollTop: 0,
    courtsInView: [], courtListPlaces: [], courtListSavedOnly: false,
    selectedCourtId: null, selectedCourt: null, courtMarkers: new Map(), courtFetchSeq: 0,
    map: null, areaLoc: [33, -117],
    areaLabel: 'Saved local area', playGamesCache: {saved: true},
    chatFriendsCache: {saved: true}, courtFilters: {}, searchQ: '',
  };
  const rendered = [], selectedMarkers = [], details = [], remembered = [];
  const renderCourtList = (...args) => { rendered.push(args); nodes['#court-list-items'].scrollTop = 0; };
  const hideSearchSuggest = () => {};
  const setCourtMarkerSelected = (id, selected) => selectedMarkers.push([id, selected]);
  const revealSelectedCourtOnMap = () => {};
  const courtBusinessDiscoveryLabel = () => '';
  const courtBusinessDiscoveryAvailable = () => false;
  const courtDirectionsUrl = court => `https://maps.example.test/${court.id}`;
  const uiIcon = () => '';
  const esc = value => String(value ?? '');
  const openCourtDetail = (id, options) => { details.push({id, options}); return {id}; };
  const rememberCourtSearch = query => remembered.push(query);
  const openCourtPlayMenu = () => {};
  const moveCourtMapWithoutRefresh = fn => fn();
"""


def map_functions():
    return (
        section("function syncCourtSheetLabel", "function setupCourtDockLayout")
        + section("function clearCourtSelection", "function courtDirectionsUrl")
        + section("function courtDiscoveryReturnFocus", "function openCourtPlayMenu")
        + section("function selectCourtOnMap", "function autoCheckInStorageKey")
    )


def test_mobile_selection_shows_the_selected_court_without_hiding_or_locking_the_map():
    result = run_js(DOM_HARNESS + map_functions() + r"""
      const firstCourt = {id: 11, name: 'Sunset Courts', city: 'Irvine', players_here: 2};
      const secondCourt = {id: 22, name: 'Harbor Courts', city: 'Newport Beach', active_games: 1};
      const areaBefore = JSON.stringify([state.areaLoc, state.areaLabel,
        state.playGamesCache, state.chatFriendsCache]);
      const snapshot = () => ({
        selected: state.selectedCourtId, snap: state.courtSheetSnap,
        mapInert: nodes['#map'].inert, mapHidden: nodes['#map'].getAttribute('aria-hidden'),
        previewHidden: nodes['#court-preview'].classList.contains('hidden'),
        preview: nodes['#court-preview'].innerHTML, details: details.length,
      });
      activateCourtFromDiscovery(firstCourt);
      const first = snapshot();
      activateCourtFromDiscovery(firstCourt);
      const repeated = snapshot();
      setCourtSheetSnap('full');
      nodes['#court-list-items'].scrollTop = 432;
      activateCourtFromDiscovery(secondCourt);
      const fromList = snapshot();
      const savedScroll = state.courtListExpandedScrollTop;
      nodes['#court-preview'].querySelector('[data-preview-detail]').handlers.click();
      const restoredFocus = details[0].options.returnFocusFallback().name;
      nodes['#court-preview'].querySelector('[data-preview-close]').handlers.click();
      const cleared = {selected: state.selectedCourtId,
        hidden: nodes['#court-preview'].classList.contains('hidden'),
        hasSelection: nodes['#court-list'].classList.contains('has-selection'),
        mapInert: nodes['#map'].inert};
      console.log(JSON.stringify({first, repeated, fromList, savedScroll,
        opened: details.map(item => item.id), selectedMarkers, restoredFocus, cleared,
        areaUnchanged: areaBefore === JSON.stringify([state.areaLoc, state.areaLabel,
          state.playGamesCache, state.chatFriendsCache])}));
    """)
    for key, court_id, court_name in [
        ("first", 11, "Sunset Courts"),
        ("repeated", 11, "Sunset Courts"),
        ("fromList", 22, "Harbor Courts"),
    ]:
        snapshot = result[key]
        assert snapshot["selected"] == court_id
        assert snapshot["snap"] == "peek"
        assert not snapshot["mapInert"]
        assert snapshot["mapHidden"] is None
        assert not snapshot["previewHidden"]
        assert court_name in snapshot["preview"]
        assert snapshot["details"] == 0
    assert result["savedScroll"] == 432
    assert result["opened"] == [22]
    assert result["restoredFocus"] == "[data-preview-detail]"
    assert result["cleared"] == {
        "selected": None, "hidden": True, "hasSelection": False, "mapInert": False,
    }
    assert [11, False] in result["selectedMarkers"]
    assert [22, True] in result["selectedMarkers"]
    assert result["areaUnchanged"]


def test_mobile_map_and_list_are_two_modes_with_scroll_recovery_and_desktop_map_access():
    result = run_js(DOM_HARNESS + map_functions() + r"""
      const snapshots = [];
      for (const [width, height] of [[390, 844], [320, 568], [844, 390]]) {
        window.innerWidth = width; window.innerHeight = height;
        setCourtSheetSnap('peek');
        const marker = new Element('map marker'); marker.parent = nodes['#map']; marker.focus();
        setCourtSheetSnap('half');
        const list = {snap: state.courtSheetSnap, inert: nodes['#map'].inert,
          hidden: nodes['#map'].getAttribute('aria-hidden'),
          mapKeepsFocus: nodes['#map'].contains(document.activeElement),
          pressed: viewButtons.map(button => button.getAttribute('aria-pressed'))};
        nodes['#court-list-items'].scrollTop = 517;
        setCourtSheetSnap('peek');
        const map = {snap: state.courtSheetSnap, inert: nodes['#map'].inert,
          hidden: nodes['#map'].getAttribute('aria-hidden'),
          pressed: viewButtons.map(button => button.getAttribute('aria-pressed'))};
        setCourtSheetSnap('full');
        snapshots.push({list, map, restoredScroll: nodes['#court-list-items'].scrollTop});
      }
      window.innerWidth = 1200; window.innerHeight = 900;
      setCourtSheetSnap('full');
      const desktop = {snap: state.courtSheetSnap, inert: nodes['#map'].inert,
        hidden: nodes['#map'].getAttribute('aria-hidden')};
      console.log(JSON.stringify({snapshots, desktop}));
    """)
    for snapshot in result["snapshots"]:
        assert snapshot["list"] == {
            "snap": "full", "inert": True, "hidden": "true",
            "mapKeepsFocus": False, "pressed": ["false", "true"],
        }
        assert snapshot["map"] == {
            "snap": "peek", "inert": False, "hidden": None,
            "pressed": ["true", "false"],
        }
        assert snapshot["restoredScroll"] == 517
    assert result["desktop"] == {"snap": "half", "inert": False, "hidden": None}


def test_remote_selection_creates_a_visible_pin_outside_clusters_and_cleans_up_on_dismiss():
    harness = DOM_HARNESS.replace(
        "const setCourtMarkerSelected = (id, selected) => selectedMarkers.push([id, selected]);", "",
    ).replace(
        "const renderCourtList = (...args) => { rendered.push(args); nodes['#court-list-items'].scrollTop = 0; };", "",
    )
    result = run_js(harness + section("function drawMarkers", "function clearCourtSelection")
                    + section("function renderCourtList", "function openSuggestEditSheet")
                    + map_functions() + r"""
      const sortCourts = courts => courts, activeCourtFilterCount = () => 0;
      const syncCourtSheetSummary = () => {}, openAddCourtSheet = () => {}, clearCourtFilters = () => {};
      const courtRowHtml = court => `<button data-court="${court.id}">${court.name}</button>`;
      const courtPeekCardHtml = courtRowHtml;
      state.courtListLimit = 20;
      function group(name) {
        return {name, layers: new Set(), removeLayer(marker) { this.layers.delete(marker); }};
      }
      state.markers = group('cluster'); state.map = group('map');
      const L = {marker(location, options) {
        const marker = {
          location, icon: options.icon, element: new Element('marker'),
          on() { return this; }, addTo(parent) { parent.layers.add(this); return this; },
          setLatLng(location) { this.location = location; },
          setIcon(icon) { this.icon = icon; }, getElement() { return this.element; },
          setZIndexOffset(value) { this.zIndex = value; },
        };
        return marker;
      }};
      const courtMarkerIcon = (court, selected) => ({selected});
      const courtMarkerVisualKey = (court, selected) => `${court.id}-${selected}`;
      const syncCourtMarkerAccessibility = () => {}, syncClusterMarkerAccessibility = () => {};
      const local = {id: 11, name: 'Local courts', latitude: 33, longitude: -117};
      const remote = {id: 22, name: 'Saved court in another city', latitude: 35, longitude: -119};
      state.courtsInView = [local]; drawMarkers(state.courtsInView);
      setCourtSheetSnap('full');
      activateCourtFromDiscovery(remote);
      const marker = state.courtMarkers.get(remote.id).marker;
      const selected = {id: state.selectedCourtId, court: state.selectedCourt.id,
        visible: state.map.layers.has(marker), clustered: state.markers.layers.has(marker),
        zIndex: marker.zIndex, highlighted: marker.icon.selected, snap: state.courtSheetSnap,
        previewNamed: nodes['#court-preview'].innerHTML.includes(remote.name)};
      clearCourtSelection();
      const dismissed = {visible: state.map.layers.has(marker),
        clustered: state.markers.layers.has(marker), zIndex: marker.zIndex,
        highlighted: marker.icon.selected};
      activateCourtFromDiscovery(remote);
      drawMarkers([local]);
      const removed = {selected: state.selectedCourtId, court: state.selectedCourt,
        tracked: state.courtMarkers.has(remote.id), visible: state.map.layers.has(marker),
        clustered: state.markers.layers.has(marker), localRetained: state.courtMarkers.has(local.id)};
      console.log(JSON.stringify({selected, dismissed, removed}));
    """)
    assert result["selected"] == {
        "id": 22, "court": 22, "visible": True, "clustered": False,
        "zIndex": 1000, "highlighted": True, "snap": "peek", "previewNamed": True,
    }
    assert result["dismissed"] == {
        "visible": False, "clustered": True, "zIndex": 0, "highlighted": False,
    }
    assert result["removed"] == {
        "selected": None, "court": None, "tracked": False, "visible": False,
        "clustered": False, "localRetained": True,
    }


def test_selected_pin_is_panned_inside_the_actual_visible_map_above_the_dock():
    harness = DOM_HARNESS.replace("const revealSelectedCourtOnMap = () => {};", "")
    result = run_js(harness + section("function revealSelectedCourtOnMap", "function syncCourtSheetSummary") + r"""
      const pans = [];
      let obsoleteFetchRan = false;
      state.courtMoveFetchTimer = setTimeout(() => { obsoleteFetchRan = true; }, 0);
      state.map = {panInside: (coordinates, options) => pans.push({coordinates, ...options})};
      state.selectedCourt = {id: 22, latitude: 35, longitude: -119};
      nodes['#map-filters'] = new Element('filters');
      nodes['#map .leaflet-control-zoom'] = new Element('zoom');
      nodes['#map .leaflet-control-zoom'].getBoundingClientRect = () => ({right: 60});
      nodes['#map'].getBoundingClientRect = () => ({top: 0, left: 0, bottom: 756, right: window.innerWidth});
      nodes['#map-filters'].getBoundingClientRect = () => ({bottom: 114});
      nodes['#court-list'].getBoundingClientRect = () => ({top: 506, left: 792});
      revealSelectedCourtOnMap();
      state.courtSheetSnap = 'full'; revealSelectedCourtOnMap();
      const duringFullList = pans.length;
      window.innerWidth = 1200; state.courtSheetSnap = 'half'; revealSelectedCourtOnMap();
      window.innerWidth = 844; window.innerHeight = 390; state.courtSheetSnap = 'peek';
      nodes['#map'].getBoundingClientRect = () => ({top: 0, left: 0, bottom: 302, right: 844});
      nodes['#court-list'].getBoundingClientRect = () => ({top: 114, left: 484});
      revealSelectedCourtOnMap();
      state.selectedCourt = null; revealSelectedCourtOnMap();
      await new Promise(resolve => setTimeout(resolve, 5));
      console.log(JSON.stringify({pans, duringFullList, obsoleteFetchRan,
        fetchSeq: state.courtFetchSeq, timerCleared: state.courtMoveFetchTimer === null}));
    """)
    assert result["duringFullList"] == 1
    assert len(result["pans"]) == 3
    assert not result["obsoleteFetchRan"]
    assert result["timerCleared"]
    assert result["fetchSeq"] == 3
    mobile, desktop, landscape = result["pans"]
    for pan, height in [(mobile, 756), (desktop, 756), (landscape, 302)]:
        assert pan["coordinates"] == [35, -119]
        assert pan["animate"] is False
        assert pan["paddingTopLeft"][0] >= 60 + 24
        assert pan["paddingTopLeft"][1] >= 114 + 24
        # Leave a reachable marker target between the filters and the dock.
        assert height - pan["paddingTopLeft"][1] - pan["paddingBottomRight"][1] >= 44
    assert mobile["paddingBottomRight"][1] >= 756 - 506 + 24
    assert desktop["paddingBottomRight"][0] >= 1200 - 792 + 24
    assert landscape["paddingBottomRight"][0] >= 844 - 484 + 24


def test_area_sheet_is_read_only_until_apply_and_keeps_the_saved_home_area_separate():
    source = section("function syncUseMapAreaAction", "function areaViewKey")
    result = run_js(DOM_HARNESS + source + r"""
      let center = {lat: 34.05, lng: -118.25}, modal;
      const requests = [], pending = [], notifications = [], homeEdits = [];
      state.map = {getCenter: () => center};
      state.me = {id: 1, home_lat: 33, home_lng: -117, home_area: 'Saved home'};
      const homeBefore = JSON.stringify(state.me);
      const areaBefore = JSON.stringify([state.areaLoc, state.areaLabel,
        state.playGamesCache, state.chatFriendsCache]);
      const modalHead = title => `<h2>${title}</h2>`;
      const openModal = html => {
        modal = new Element('area modal'); modal.isConnected = true; modal.innerHTML = html; return modal;
      };
      const closeModal = () => { modal.isConnected = false; };
      const api = (path, options = {}) => {
        requests.push({path, method: options.method || 'GET'});
        return new Promise((resolve, reject) => pending.push({resolve, reject}));
      };
      const clearLookingBanner = () => notifications.push('clear');
      const updatePlayHeader = () => notifications.push('header');
      const refreshLookingBanner = () => notifications.push('refresh');
      const toast = message => notifications.push(message);
      const openChildModal = (parent, action) => { homeEdits.push(parent.name); return action(); };
      const openHomeAreaSheet = options => { homeEdits.push(typeof options.onSet); };
      const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

      openCourtAreaSheet();
      const openingUnchanged = areaBefore === JSON.stringify([state.areaLoc, state.areaLabel,
        state.playGamesCache, state.chatFriendsCache]);
      closeModal();
      pending.shift().resolve({label: 'Late lookup after dismiss'}); await flush();
      const dismissUnchanged = areaBefore === JSON.stringify([state.areaLoc, state.areaLabel,
        state.playGamesCache, state.chatFriendsCache]);
      const dismissNotifications = notifications.length;

      openCourtAreaSheet();
      pending.shift().resolve({label: 'Los Angeles'}); await flush();
      const lookupUnchanged = areaBefore === JSON.stringify([state.areaLoc, state.areaLabel,
        state.playGamesCache, state.chatFriendsCache]);
      const copy = modal.innerHTML;
      center = {lat: 35, lng: -119};
      modal.querySelector('[data-apply-map-area]').handlers.click();
      const applied = {area: state.areaLoc, label: state.areaLabel,
        playCache: state.playGamesCache, peopleCache: state.chatFriendsCache,
        modalClosed: !modal.isConnected, provisional: state.snapshotAreaProvisional};

      openCourtAreaSheet();
      pending.shift().reject(new Error('Offline')); await flush();
      modal.querySelector('[data-edit-home-area]').handlers.click();
      console.log(JSON.stringify({openingUnchanged, dismissUnchanged, dismissNotifications,
        lookupUnchanged, applied, homeUnchanged: homeBefore === JSON.stringify(state.me),
        requests, homeEdits, copy}));
    """)
    assert result["openingUnchanged"]
    assert result["dismissUnchanged"]
    assert result["lookupUnchanged"]
    assert result["dismissNotifications"] == 0
    assert result["homeUnchanged"]
    assert result["applied"] == {
        "area": [34.05, -118.25], "label": "Los Angeles", "playCache": None,
        "peopleCache": None, "modalClosed": True, "provisional": False,
    }
    assert all(request["method"] == "GET" for request in result["requests"])
    assert result["homeEdits"] == ["area modal", "function"]
    assert "Your saved home area stays the same." in result["copy"]


def test_area_apply_still_works_without_reverse_geocoding():
    source = section("function syncUseMapAreaAction", "function areaViewKey")
    result = run_js(DOM_HARNESS + source + r"""
      let modal;
      state.map = {getCenter: () => ({lat: 34.05, lng: -118.25})};
      const modalHead = () => '';
      const openModal = () => { modal = new Element('area'); modal.isConnected = true; return modal; };
      const closeModal = () => { modal.isConnected = false; };
      const api = () => Promise.reject(new Error('Offline'));
      const clearLookingBanner = () => {}, updatePlayHeader = () => {},
        refreshLookingBanner = () => {}, toast = () => {};
      openCourtAreaSheet();
      for (let i = 0; i < 5; i++) await Promise.resolve();
      modal.querySelector('[data-apply-map-area]').handlers.click();
      console.log(JSON.stringify({area: state.areaLoc, label: state.areaLabel, closed: !modal.isConnected}));
    """)
    assert result == {
        "area": [34.05, -118.25], "label": "Map near 34.05, -118.25", "closed": True,
    }
