"""Execute setup transitions to verify pause, resume, and dismissal behavior."""
import json
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def run_js(source):
    result = subprocess.run(['node', '-e', source], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_pause_is_account_scoped_and_explicit_resume_skips_completed_fields():
    function = APP.split('  function runNewPlayerOnboarding(', 1)[1].split('  function startPlayLiveRefresh', 1)[0]
    result = run_js("""
      const state = {me: {id: 1}, tab: 'play', playSeg: 'games'};
      const saved = new Map();
      const localStorage = {getItem: k => saved.get(k), setItem: (k,v) => saved.set(k,v), removeItem: k => saved.delete(k)};
      let pendingNewPlayerOnboardingAccountId = 1, opens = [], resumes = 0;
      function toast() {}
      function resumePlayerInviteIntentAfterAuth() {resumes++;}
      function openPlayerBasicsOnboarding(next, options) {opens.push({next, options});}
    """ + 'function runNewPlayerOnboarding(' + function + """
      runNewPlayerOnboarding();
      opens[0].options.onPause();
      runNewPlayerOnboarding();
      const afterPaused = opens.length;
      state.me.id = 2;
      runNewPlayerOnboarding();
      const otherAccount = opens.length;
      state.me.id = 1;
      runNewPlayerOnboarding({replay: true, profileOnly: true});
      process.stdout.write(JSON.stringify({afterPaused, otherAccount, total: opens.length,
        onlyMissing: opens[2].options.onlyMissing, paused: saved.has('pp_setup_paused:1'),
        pending: pendingNewPlayerOnboardingAccountId, resumes}));
    """)
    assert result == {'afterPaused': 1, 'otherAccount': 2, 'total': 3,
                      'onlyMissing': True, 'paused': False, 'pending': None, 'resumes': 1}


def test_modal_dismissal_pauses_once_and_continue_does_not_pause():
    binding = APP.split('  function openPlayerBasicsOnboarding(', 1)[1].split('    const finishBinding = ', 1)[1].split('    const openPhotoStep', 1)[0]
    result = run_js("""
      let paused = 0, advanced = 0;
      const stillCurrent = () => true;
      const onPause = () => paused++;
      function dismissModal(modal, done) {modal._cleanupFns.forEach(f => f()); done();}
      function modal() {return {_cleanupFns: [], handlers: {}, querySelector() {
        return {addEventListener: (type, cb) => this.handlers[type] = cb};
      } };}
    """ + 'const finishBinding = ' + binding + """
      (async () => {
        const first = modal();
        finishBinding(first, () => advanced++);
        first._cleanupFns.forEach(f => f());
        await Promise.resolve();
        first._cleanupFns.forEach(f => f());
        await Promise.resolve();
        const dismissed = {paused, advanced};
        const second = modal();
        const next = finishBinding(second, () => advanced++);
        next(); next();
        await Promise.resolve();
        const continued = {paused, advanced};
        const third = modal();
        finishBinding(third, () => advanced++);
        third.handlers.click(); third.handlers.click();
        await Promise.resolve();
        process.stdout.write(JSON.stringify({dismissed, continued, explicit: {paused, advanced}}));
      })();
    """)
    assert result == {'dismissed': {'paused': 1, 'advanced': 0},
                      'continued': {'paused': 1, 'advanced': 1},
                      'explicit': {'paused': 2, 'advanced': 1}}


def test_finishing_with_skipped_fields_does_not_restart_automatic_setup():
    function = APP.split('  function runNewPlayerOnboarding(', 1)[1].split('  function startPlayLiveRefresh', 1)[0]
    result = run_js("""
      const state = {me: {id: 1, home_court_id: 3}, tab: 'play', playSeg: 'games'};
      const saved = new Map();
      const localStorage = {getItem: k => saved.get(k), setItem: (k,v) => saved.set(k,v), removeItem: k => saved.delete(k)};
      let pendingNewPlayerOnboardingAccountId = null, opens = 0, completeAttempts = 0;
      const toast = () => {}, resumePlayerInviteIntentAfterAuth = () => {}, renderPlay = () => {};
      const completeNewPlayerOnboarding = async () => {completeAttempts++; return false;};
      function openPlayerBasicsOnboarding(next) {opens++; next();}
    """ + 'function runNewPlayerOnboarding(' + function + """
      runNewPlayerOnboarding({replay: true, profileOnly: true});
      runNewPlayerOnboarding();
      process.stdout.write(JSON.stringify({opens, completeAttempts, paused: saved.get('pp_setup_paused:1')}));
    """)
    assert result == {'opens': 1, 'completeAttempts': 1, 'paused': '1'}
