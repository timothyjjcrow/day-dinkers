"""Return-to-plan controls must respect confirmed participation and browser consent."""
import subprocess
from pathlib import Path

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def run(script):
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)


def test_return_controls_only_offer_calendar_for_confirmed_future_dates():
    source = APP[APP.index('  function appRunsStandalone('):APP.index('  function openInstallApp(')]
    run('''
const assert = require('node:assert/strict');
const state = {}, navigator = {}, window = {matchMedia: () => ({matches: false})};
const uiIcon = () => '';
''' + source + '''
const game = {is_joined: true, status: 'upcoming', scheduled_at: new Date(Date.now()+86400000).toISOString()};
assert.match(sessionReturnToolsHtml(game), /id="gs-calendar"/);
assert.match(sessionReturnToolsHtml(game), /id="gs-install"/);
for (const patch of [{is_joined:false}, {status:'cancelled'}, {status:'completed'}, {is_instant:true},
 {scheduled_at:'invalid'}, {scheduled_at:new Date(Date.now()-60000).toISOString()}]) {
 assert.equal(sessionReturnToolsHtml({...game, ...patch}), '');
}
navigator.standalone = true;
assert.doesNotMatch(sessionReturnToolsHtml(game), /id="gs-install"/);
assert.match(sessionReturnToolsHtml(game), /id="gs-calendar"/);
navigator.standalone = false; state.appInstalled = true;
assert.doesNotMatch(sessionReturnToolsHtml(game), /id="gs-install"/);
''')


def test_install_requires_a_click_and_recovers_from_decline_and_failure():
    source = APP[APP.index('  function openInstallApp('):APP.index('  function openAccountSettings(')]
    run('''
const assert = require('node:assert/strict');
const state = {}; let prompts = 0, handler, html;
const button = {disabled:false, remove(){this.removed=true;}};
const status = {textContent:'', setAttribute(){}, focus(){this.focused=true;}};
const modal = {isConnected:true, querySelector: id => id === '#install-status' ? status :
 {addEventListener: (_, callback) => {handler=callback;}}};
const openModal = value => {html=value;return modal;};
const modalHead = () => ''; const appRunsStandalone = () => false;
''' + source + '''
(async () => {
 for (const outcome of ['dismissed', 'accepted', 'error']) {
  state.installPrompt = {prompt: async () => {prompts++; if(outcome==='error')throw Error('no');}, userChoice:Promise.resolve({outcome})};
  const before=prompts;
  openInstallApp(); assert.equal(prompts,before, 'Opening instructions cannot trigger a browser prompt');
  await handler({currentTarget:button});
  assert.equal(prompts,before+1); assert.equal(state.installPrompt,null);
  assert.equal(status.focused,true); assert.equal(button.removed,true);
  assert.match(status.textContent, outcome==='accepted' ? /Install requested/ : outcome==='error' ? /could not open/ : /No changes made/);
  await handler({currentTarget:button}); assert.equal(prompts,before+1);
 }
 state.installPrompt = null; openInstallApp();
 assert.doesNotMatch(html, /id="install-native"/);
 assert.match(html, /bookmark Third Shot/);
})().catch(error => {console.error(error);process.exit(1);});
''')


def test_session_help_expands_only_the_requested_topic():
    source = APP[APP.index('  function openHelpSafety('):APP.index('  function openProfileEditorHub(')]
    run('''
const assert = require('node:assert/strict');let html;
const esc = s => String(s).replaceAll('<','&lt;'); const modalHead = () => '';
const openModal = value => {html=value;return {querySelector:()=>({addEventListener(){}})};};
''' + source + '''
openHelpSafety({topic:'Change or cancel a plan'});
assert.equal((html.match(/<details[^>]* open>/g)||[]).length,1);
assert.match(html, /<details class="simple-disclosure" open><summary>Change or cancel a plan/);
openHelpSafety();assert.equal((html.match(/<details[^>]* open>/g)||[]).length,0);
openHelpSafety({topic:'<script>bad</script>'});assert.doesNotMatch(html, /<script>/);
''')


def test_visit_facts_keep_unknowns_honest_and_attribute_host_booking_claims():
    source = APP[APP.index('  function sessionCourtAccessLabel('):APP.index('  function sessionReturnToolsHtml(')]
    run("const assert = require('node:assert/strict'); const esc = s => String(s);" + source + r"""
assert.match(sessionVisitFactsHtml({}), /Cost not listed/);
assert.match(sessionVisitFactsHtml({}), /Court booking not listed/);
assert.match(sessionVisitFactsHtml({cost_cents:0}), /Free session/);
assert.match(sessionVisitFactsHtml({cost_cents:1250,court_count:2}), /\$12.50 per player/);
assert.match(sessionVisitFactsHtml({court_count:2}), /Host says 2 courts are reserved/);
assert.match(sessionVisitFactsHtml({court_count:1}), /Host says 1 court is reserved/);
""")
