"""Profile results explain their scope without deriving unrelated statistics."""

from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess

import pytest


APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def run_js(source, script):
    result = subprocess.run(['node', '-e', source + script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class Markup(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.words, self.labels = [], []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        label = dict(attrs).get('aria-label')
        if label:
            self.labels.append(label)

    def handle_data(self, value):
        self.words.extend(value.split())

    @property
    def text(self):
        return ' '.join(self.words)


@pytest.mark.parametrize('visible', [False, True])
def test_recent_result_strip_names_scope_and_preserves_newest_first_order(visible):
    source = section('function formStripHtml', 'function weatherIcon')
    rendered = run_js(source, f"""
      const form = Object.freeze(['W', 'L', 'L', 'W']);
      console.log(JSON.stringify(formStripHtml(form, {json.dumps(visible)})));
    """)
    markup = Markup(rendered)
    assert ('Visible results:' if visible else 'Recent results:') in markup.text
    assert 'Ranked + casual · newest first' in markup.text
    assert markup.labels == ['Win', 'Loss', 'Loss', 'Win']
    assert [word for word in markup.words if word in {'W', 'L'}] == ['W', 'L', 'L', 'W']
    assert 'streak' not in markup.text.lower()


def test_no_result_history_has_no_empty_or_misleading_form_strip():
    source = section('function formStripHtml', 'function weatherIcon')
    assert run_js(source, "console.log(JSON.stringify([formStripHtml(null), formStripHtml([]), formStripHtml([], true)]));") == ['', '', '']


def test_profile_keeps_ranked_streak_separate_from_the_shared_scored_record():
    source = section('const rankedTotal = Number(user.ranked_wins', 'const availLines = availabilitySummary(user.availability)')
    output = run_js("""
      const userId = 2, state = {me: {id: 1}};
      const user = {display_name: 'Jordan Chen', ranked_wins: 8, ranked_losses: 3,
        current_streak: 2, form: ['W', 'W', 'W', 'W'], as_teammates: {wins: 4, losses: 0}};
      const uiIcon = () => '', esc = value => String(value);
    """ + source, "console.log(JSON.stringify({stats: publicHeadlineStats, shared: h2hHtml}));")
    stats, shared = Markup(output['stats']), Markup(output['shared'])
    assert '8–3 Ranked wins–losses' in stats.text
    assert '2 Current ranked win streak' in stats.text
    assert '4 wins · 0 losses' in shared.text
    assert 'You + Jordan as teammates' in shared.text
    assert 'Ranked + casual' in shared.text


def test_public_and_self_profiles_use_the_correct_history_scope():
    public = section('async function openUserProfile', 'function profileDashboardRequest')
    own = section('async function renderProfile', 'function openEditProfile')
    assert 'formStripHtml(user.form, true)' in public
    assert 'formStripHtml(stats.form)' in own
    assert 'role="group" aria-label="Earned badges"' in public
    assert 'title="Earned badge: ${esc(b.label)}"' in public
