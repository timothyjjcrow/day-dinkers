"""Confirmation callers must not race the browser's pending Back traversal."""
from pathlib import Path
import subprocess


APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def test_confirmation_resolves_after_dismissal_and_only_once():
    source = 'function openActionConfirmation(' + APP.split(
        '  function openActionConfirmation(', 1
    )[1].split('  function requestScoreDisputeReason(', 1)[0]
    script = '''
      const assert = require('node:assert/strict');
      const uiIcon = () => '', esc = value => value, modalHead = () => '';
      const requestAnimationFrame = () => {};
      let sheet, finishDismissal, dismissals = 0;
      function openModal() {
        const controls = new Map();
        sheet = {
          _cleanupFns: [], classList: {add() {}},
          querySelector(selector) {
            if (!controls.has(selector)) controls.set(selector, {
              setAttribute() {}, addEventListener(event, callback) {this[event] = callback;}
            });
            return controls.get(selector);
          }
        };
        return sheet;
      }
      function dismissModal(element, callback) {
        dismissals++;
        finishDismissal = () => {
          element._cleanupFns.forEach(fn => fn());
          callback();
        };
      }
    ''' + source + '''
      (async () => {
        for (const accepted of [true, false]) {
          let result = 'waiting';
          const promise = openActionConfirmation().then(value => {result = value;});
          const button = sheet.querySelector(accepted
            ? '[data-action-confirm-accept]' : '[data-action-confirm-cancel]');
          const before = dismissals;
          button.click();
          button.click();
          await Promise.resolve();
          assert.equal(result, 'waiting', 'Caller must wait for navigation to finish');
          assert.equal(dismissals, before + 1, 'Double taps must not traverse twice');
          finishDismissal();
          await promise;
          assert.equal(result, accepted);
        }
        const dismissed = openActionConfirmation();
        sheet._cleanupFns.forEach(fn => fn());
        assert.equal(await dismissed, false, 'Back or Escape cancels the action');
      })().catch(error => {console.error(error); process.exitCode = 1;});
    '''
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
