"""Execute the composer to verify contact/search races and allowed actions."""
import json
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()
COMPOSER = 'function openNewMessageSheet(' + APP.split('  function openNewMessageSheet(', 1)[1].split('  function inboxMessagePreviewText', 1)[0]


def test_contacts_never_overwrite_search_and_rows_follow_message_permission():
    script = r'''
      const handlers = {};
      const input = {value: '', focus() {throw new Error('Mobile keyboard opened');}, addEventListener: (name, f) => handlers[name] = f};
      const attrs = {};
      const results = {innerHTML: '', setAttribute: (k,v) => attrs[k] = v, removeAttribute: k => delete attrs[k], querySelectorAll: () => []};
      const modal = {isConnected: true, _cleanupFns: [], querySelector: s => s === '#new-message-search' ? input : results};
      const openModal = () => modal;
      const modalHead = () => '', skeletonHtml = () => 'Loading', uiIcon = () => '';
      const esc = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
      const avatarHtml = () => '', playerSkillIdentityHtml = () => '3.5 self-rating';
      const matchMedia = () => ({matches: false});
      let debounce, friendResolve;
      const setTimeout = callback => {debounce = callback; return 1;};
      const clearTimeout = () => {};
      const friendRequest = new Promise(resolve => friendResolve = resolve);
      const api = path => path === '/friends' ? friendRequest : Promise.resolve({items: [
        {id: 2, display_name: 'Jordan', can_message: true},
        {id: 3, display_name: '<Casey>', can_message: false},
      ]});
      const renderError = () => {throw new Error('Unexpected load error');};
      const settle = async () => {for (let n=0;n<8;n++) await Promise.resolve();};
    ''' + COMPOSER + r'''
      (async () => {
        openNewMessageSheet();
        input.value = 'Jo'; handlers.input(); debounce();
        await settle();
        const search = results.innerHTML;
        friendResolve({friends: [{id: 4, display_name: 'Sam', can_message: true}]});
        await settle();
        const afterContacts = results.innerHTML;
        input.value = ''; handlers.input();
        await settle();
        const cleared = results.innerHTML;
        process.stdout.write(JSON.stringify({search, afterContacts, cleared, busy: attrs['aria-busy'] || null}));
      })();
    '''
    response = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True)
    result = json.loads(response.stdout)
    assert result['search'] == result['afterContacts']
    assert 'data-compose-user="2"' in result['search']
    assert 'data-compose-profile="3"' in result['search']
    assert 'data-compose-user="3"' not in result['search']
    assert '&lt;Casey>' in result['search']
    assert 'Your friends' in result['cleared']
    assert 'data-compose-user="4"' in result['cleared']
    assert result['busy'] is None
