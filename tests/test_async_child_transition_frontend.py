"""Replacing a child sheet retains its Back control after async rendering."""
from pathlib import Path
import subprocess


APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def test_async_child_header_keeps_back_without_decorating_a_newer_screen():
    source = 'function transitionModal(' + APP.split('  function transitionModal(', 1)[1].split(
        '  function decorateFlowChildModal(', 1
    )[0]
    script = '''
      const assert = require('node:assert/strict');
      let overlayStack = [], decorated = [];
      const currentOverlayEntry = () => overlayStack.at(-1);
      const decorateFlowChildModal = element => {decorated.push(element); element.label = 'Back';};
      const closeModal = element => {overlayStack.pop(); element.isConnected = false; return true;};
      const element = child => ({isConnected:true,label:'Close',classList:{contains:()=>child}});
    ''' + source + '''
      (async()=>{
        for (const dismissBeforeReady of [false,true]) {
          const parent = element(false), original = element(true), replacement = element(true);
          overlayStack = [{el:parent},{el:original}];
          decorated = [];
          let ready;
          const promise = transitionModal(original, () => {
            overlayStack.push({el:replacement});
            return new Promise(resolve => {ready=resolve;});
          });
          assert.equal(replacement.label,'Back');
          // The async screen paints a new header after its API response.
          replacement.label = 'Close';
          const newer = element(false);
          if (dismissBeforeReady) {
            replacement.isConnected = false;
            overlayStack = [{el:parent},{el:newer}];
          }
          ready(replacement);
          await promise;
          assert.equal(replacement.label,dismissBeforeReady ? 'Close' : 'Back');
          assert.equal(newer.label,'Close');
          assert.equal(decorated.length,dismissBeforeReady ? 1 : 2);
        }
      })().catch(error=>{console.error(error);process.exitCode=1;});
    '''
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)
