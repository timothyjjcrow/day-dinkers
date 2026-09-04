"""Execute workspace rendering and switching against representative states."""
import json
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def run_js(script):
    result = subprocess.run(['node', '--input-type=module', '-e', script],
                            check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_play_lanes_switch_after_staged_nodes_move_to_live_view():
    start = APP.index('      const selectPlayLane =')
    end = APP.index("      el.querySelectorAll('[data-play-lane]')", start)
    output = run_js('''
      const state = {};
      const panels = {'#play-find-panel': {}, '#play-plans-panel': {}};
      const buttons = ['find', 'plans'].map(lane => ({
        dataset: {playLane: lane}, classList: {toggle() {}},
        setAttribute(name, value) { this[name] = value; }
      }));
      const liveEl = {querySelector: key => panels[key], querySelectorAll: () => buttons};
      const el = {childElementCount: 0, querySelector() { throw Error('Detached staging root'); }};
    ''' + APP[start:end] + '''
      selectPlayLane('plans');
      const first = [panels['#play-find-panel'].hidden, panels['#play-plans-panel'].hidden,
                     buttons.map(b => b['aria-selected']), buttons.map(b => b.tabIndex)];
      selectPlayLane('find');
      console.log(JSON.stringify({first, second: [panels['#play-find-panel'].hidden,
        panels['#play-plans-panel'].hidden], lane: state.playLane}));
    ''')
    assert output == {'first': [True, False, ['false', 'true'], [-1, 0]],
                      'second': [False, True], 'lane': 'find'}


def business_html(**overrides):
    start = APP.index('  function renderBusinessHubDashboard(')
    end = APP.index('    const updateBusiness', start)
    business = dict(name='Test club', offerings=[], schedule=[], manager_role='owner',
                    claim_status='verified', published=False, content_review_status='approved')
    business.update(overrides)
    return run_js('''
      const businessCompletion = () => ({percent: 100, complete: 0, checks: []});
      const businessVerificationState = b => b.claim_status;
      const businessHasBookingLink = () => true;
      const businessConnectionHealth = () => ({tone:'',icon:'link',label:'No feed',detail:'Add a feed'});
      const businessStatusHtml = () => '';
      const businessCourtName = () => 'Test court';
      const businessStateIconName = () => 'clock';
      const uiIcon = () => '';
      const esc = value => String(value || '');
    ''' + APP[APP.index('  function businessWorkspaceState('):APP.index('  function openBusinessBookingSetup(')] + APP[start:end] + '}\n' + f'''
      const body = {{classList: {{add() {{}}}}}};
      renderBusinessHubDashboard(null, body, {json.dumps(business)}, {{businesses: []}});
      console.log(JSON.stringify(body.innerHTML));
    ''')


def test_booking_status_reflects_player_visibility_not_just_saved_url():
    assert 'Booking links saved' in business_html()
    assert 'Booking links available to players' in business_html(published=True)
    for changes in [dict(published=True, suspended=True),
                    dict(published=True, content_review_status='pending'),
                    dict(published=True, claim_status='pending')]:
        html = business_html(**changes)
        assert 'Booking links saved' in html
        assert 'Booking links available to players' not in html
        assert 'Live on the court map' not in html


def test_business_home_focuses_on_content_and_preserves_role_permissions():
    html = business_html()
    main, settings = html.split('<details class="simple-disclosure venue-management">')
    for tool in ['details', 'booking', 'schedule', 'offerings']:
        assert f'data-business-tool="{tool}"' in main
    for tool in ['team', 'security', 'ownership']:
        assert f'data-business-tool="{tool}"' in settings
        assert f'data-business-tool="{tool}"' not in main
    viewer = business_html(manager_role='viewer')
    assert 'data-business-tool="details" disabled' in viewer
    assert 'data-business-tool="schedule" disabled' in viewer
    assert 'id="business-publish-toggle" disabled' in viewer
    assert 'data-business-tool="booking"' in viewer


def test_venue_next_action_follows_verification_review_and_visibility():
    assert 'Publish venue' in business_html()
    assert 'Confirm your management role' in business_html(claim_status='pending')
    assert 'Changes are being reviewed' in business_html(content_review_status='pending')
    assert 'Your claim needs attention' in business_html(claim_status='rejected')
    assert 'Live on the court map' in business_html(published=True)
    suspended = business_html(published=True, suspended=True, manager_role='editor')
    assert 'Publishing paused' in suspended
    assert 'data-business-tool="verification">Review status' in suspended


def test_claim_requires_a_selected_court_and_supports_back_navigation():
    start = APP.index("    let claimStep = court ? 'role' : 'venue';")
    end = APP.index("    modal.querySelector('#business-claim-form').addEventListener", start)
    output = run_js('''
      const court = null;
      let error = '', focused = '';
      const nodes = {};
      for (const id of ['claim-step-venue','claim-step-role','claim-selected-name',
        'claim-trail-venue','claim-trail-role','bh-court-search','bh-court-id',
        'business-claim-role','claim-next','claim-back']) {
        nodes['#'+id] = {value: '',dataset: {},setAttribute(k,v) {this[k]=v;},
          focus() {focused=id;},addEventListener(type,fn) {this[type]=fn;}};
      }
      nodes['#business-claim-role']._appSelectButton = {focus() {focused='role-picker';}};
      const modal = {querySelector: selector => nodes[selector]};
      const formUX = {clearError() {error='';},showError(message) {error=message;}};
    ''' + APP[start:end] + '''
      nodes['#claim-next'].click();
      const invalid = {step:claimStep,error};
      nodes['#bh-court-search'].value='Neighborhood courts';
      nodes['#bh-court-search'].dataset.selectedCourtId='27';
      nodes['#claim-next'].click();
      const valid = {step:claimStep,error,focused,venueHidden:nodes['#claim-step-venue'].hidden,
        roleHidden:nodes['#claim-step-role'].hidden,name:nodes['#claim-selected-name'].textContent};
      nodes['#claim-back'].click();
      console.log(JSON.stringify({invalid,valid,back:claimStep}));
    ''')
    assert output['invalid']['step'] == 'venue'
    assert output['invalid']['error']
    assert output['valid'] == dict(step='role', error='', focused='role-picker',
        venueHidden=True, roleHidden=False, name='Neighborhood courts')
    assert output['back'] == 'venue'
