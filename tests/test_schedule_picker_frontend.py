"""Exercise the calendar/clock editor's real event handlers and local-time value."""
import json
import os
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def run_picker(timezone):
    source = 'function bindScheduleDateTimePicker(' + APP.split(
        '  function bindScheduleDateTimePicker(', 1
    )[1].split('  async function openNewGameModal', 1)[0]
    source = APP[APP.index('  function scheduleDateTimeValue('):APP.index('  function scheduleDateTimePickerHtml(')] + source
    harness = r'''
      class Element {
        constructor() { this.value = ''; this.textContent = ''; this.dataset = {}; this.attrs = {}; this.listeners = {}; this.hiddenClasses = new Set(['hidden']); this.classList = { contains: k => this.hiddenClasses.has(k), toggle: (k, v) => v ? this.hiddenClasses.add(k) : this.hiddenClasses.delete(k) }; }
        setAttribute(k,v) { this.attrs[k] = v; }
        getAttribute(k) { return this.attrs[k]; }
        removeAttribute(k) { delete this.attrs[k]; }
        hasAttribute(k) { return k in this.attrs; }
        addEventListener(k, fn) { this.listeners[k] = fn; }
        dispatchEvent(event) { this.listeners[event.type]?.(event); }
        focus() { focused = this; }
        querySelector() { return new Element(); }
        querySelectorAll() { return []; }
        closest() { return this; }
      }
      let focused = null;
      const elements = {};
      const root = { querySelector: s => elements[s] ||= new Element() };
      const el = suffix => root.querySelector('#test' + suffix);
      el('').value = '2026-09-15T12:00';
      const esc = value => String(value);
      const picker = bindScheduleDateTimePicker(root, 'test');
      const initial = el('-clock').value;
      const input = value => { el('-clock').value = value; el('-clock').listeners.input(); };
      const click = dataset => {
        let stopped = false;
        const button = new Element(); button.dataset = dataset;
        el('-editor').listeners.click({target:button,stopPropagation:()=>{stopped=true;}});
        return stopped;
      };
      click({period:'AM'});
      const midnight = el('').value;
      input('6:15'); click({period:'PM'});
      const evening = el('').value;
      input('13:70');
      const invalid = [el('').value, el('-clock').attrs['aria-invalid']];
      input('6:15');
      const stopped = click({date:'2026-12-31'});
      const chosenDay = el('').value;
      const focusRestored = focused === el('-date');
      el('').value = '2027-01-01T09:45'; picker.sync();
      const restored = el('-clock').value;
      input('');
      const cleared = el('').value;
      el('').value = '2027-03-14T00:00'; picker.sync(); input('2:30');
      const springGap = el('').value;
      process.stdout.write(JSON.stringify({initial,midnight,evening,invalid,stopped,chosenDay,focusRestored,restored,cleared,springGap}));
    '''
    result = subprocess.run(['node', '-e', source + harness], check=True,
                            capture_output=True, text=True,
                            env={**os.environ, 'TZ': timezone})
    return json.loads(result.stdout)


def test_exact_minutes_noon_midnight_calendar_and_restoration():
    for timezone in ['America/Los_Angeles', 'UTC', 'Asia/Tokyo']:
        result = run_picker(timezone)
        assert result['initial'] == '12:00'
        assert result['midnight'] == '2026-09-15T00:00'
        assert result['evening'] == '2026-09-15T18:15'
        assert result['invalid'] == ['', 'true']
        assert result['chosenDay'] == '2026-12-31T18:15'
        assert result['stopped'] and result['focusRestored']
        assert result['restored'] == '9:45'
        assert result['cleared'] == ''


def test_nonexistent_spring_dst_time_is_rejected_instead_of_silently_shifted():
    assert run_picker('America/Los_Angeles')['springGap'] == ''
    assert run_picker('UTC')['springGap'] == '2027-03-14T02:30'
