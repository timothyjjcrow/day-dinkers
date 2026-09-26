"""Paddle line on the court page: shown only when relevant, small, and live."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
STYLES = (ROOT / 'public' / 'styles-v15.css').read_text()


def section(source, start, end):
    start_at = source.index(start)
    return source[start_at:source.index(end, start_at)]


def run(script):
    subprocess.run(
        ['node', '-e', "const assert=require('node:assert/strict');" + script],
        check=True, capture_output=True, text=True,
    )


def test_paddle_line_block_renders_only_when_relevant_with_plain_copy():
    esc = section(APP, '  const esc = (s) =>', '  const UI_ICON_NAMES')
    source = section(APP, '  const paddleTeamsText =', '  let pendingCourtDetailOpen')
    run(esc + source + '''
      const here={checkedIn:true,crowded:false,rotation:''};
      const empty={court_count:2,waiting_count:0,courts:[],in_line:false,my_court:null,my_position:null};
      assert.equal(paddleQueueHtml(null,here),'');
      assert.equal(paddleQueueHtml(empty,here),'');
      assert.equal(paddleQueueHtml(empty,{...here,checkedIn:false,crowded:true}),'');
      const crowded=paddleQueueHtml(empty,{...here,crowded:true});
      assert.ok(crowded.includes('<b>Paddle line</b><small>No one waiting</small>'));
      assert.ok(crowded.includes('id="cd-queue-toggle">Join the line</button>'));
      assert.ok(!crowded.includes('is open'));

      const ana={id:1,name:'Ana'},dee={id:4,name:'Dee'},ben={id:2,name:'Ben'};
      const line={court_count:2,waiting_count:5,in_line:true,my_court:null,my_position:3,
        courts:[{court:1,teams:[[ana,dee],[ben,{id:null,name:'Another <player>'}]]}]};
      const html=paddleQueueHtml(line,{...here,rotation:'Paddles <rotate> after one game'});
      assert.ok(html.includes('<small>5 waiting · You’re #3</small>'));
      assert.ok(html.includes('>Leave line</button>'));
      assert.ok(html.includes('Court 1 · Ana &amp; Dee vs Ben &amp; Another &lt;player&gt;'));
      assert.ok(html.includes('data-queue-done="1">Game done</button>'));
      assert.ok(html.includes('<span>Court 2 is open</span>'));
      assert.ok(html.includes('data-queue-call="2">Call next 4</button>'));
      assert.ok(html.includes('<small>Paddles &lt;rotate&gt; after one game</small>'));
      assert.equal((html.match(/btn-primary/g)||[]).length,0);

      const singles=paddleQueueHtml({...line,waiting_count:3,my_position:null,my_court:1,
        courts:[{court:1,teams:[[ana],[ben]]}]},here);
      assert.ok(singles.includes('3 waiting · You’re on Court 1'));
      assert.ok(singles.includes('Court 1 · Ana vs Ben'));
      assert.ok(singles.includes('Call next 2'));
      const full=paddleQueueHtml({...line,courts:[{court:1,teams:[[ana],[ben]]},{court:2,teams:[[dee],[ben]]}]},here);
      assert.ok(!full.includes('is open'));

      // Signed-out and not-checked-in viewers see the line but no controls.
      const counts=paddleQueueHtml({court_count:2,waiting_count:1,courts:[{court:1,player_count:4}]},
        {checkedIn:false,crowded:false,rotation:''});
      assert.ok(counts.includes('Court 1 · 4 playing'));
      assert.ok(!counts.includes('<button'));
    ''')


def test_court_page_places_the_line_in_the_now_card_and_hides_it_when_closed():
    detail = section(APP, 'async function openCourtDetail', 'function openCheckInSheet')
    template = detail[detail.index('${presenceControl}'):]
    assert template.index('<div id="cd-queue">${paddleQueueBlock(court.paddle_queue)}</div>') \
        < template.index('${primaryAction}')
    assert "const paddleQueueBlock = (q) => (courtClosed ? '' : paddleQueueHtml(q, {" in detail
    assert 'crowded: nHere > (q?.court_count || 1) * 4' in detail
    assert 'rotation: court.visitor_info?.rotation' in detail
    # No new dock buttons: the dock still proxies to the same three controls.
    dock = section(detail, 'class="cd-action-dock"', '</div>`}')
    assert dock.count('<button') == 3
    assert 'cd-queue' not in dock


def test_line_actions_undo_converge_and_poll_only_while_in_line():
    detail = section(APP, 'async function openCourtDetail', 'function openCheckInSheet')
    queue = section(detail, '    let paddleQueue = court.paddle_queue;', '    bindPaddleQueue(modal.querySelector')
    assert "api(`/courts/${court.id}/queue`, { method: 'POST', body: JSON.stringify({ action: join ? 'join' : 'leave' }) })" in queue
    assert "toast(`You’re #${next.my_position || next.waiting_count} in line`" in queue
    assert "action: { label: 'Undo', onClick: () => setPaddleLine(null, false) }" in queue
    assert "api(`/courts/${court.id}/queue/next`" in queue
    assert "JSON.stringify({ court: number, expected, score })" in queue
    assert 'if (error.data?.paddle_queue) {' in queue
    assert "toast(`You’re up on Court ${next.my_court}`" in queue
    assert 'if (!renderPaddleQueue(next)) {' in queue
    assert '`Next players called to Court ${number}` : `Court ${number} is open`' in queue
    assert "openChildModal(modal, () => openPaddleGameDone(Number(button.dataset.queueDone)))" in queue
    assert "modalHead(`Court ${number} · Game done`)" in queue
    assert 'Score is optional. It goes in court chat, not ratings.' in queue
    assert 'id="cd-queue-next">Call next players</button>' in queue
    assert "entry.teams.flat().map((p) => p.id)" in queue
    assert "beginButtonAction(button, join ? 'Joining…' : 'Leaving…')" in queue
    # Polling mirrors openGameScreen: visible, online, top sheet, and only in line.
    assert 'const wanted = !courtClosed && !!paddleQueue?.in_line;' in queue
    assert 'modal._paddlePoll = wanted && setInterval(async () => {' in queue
    assert "if (document.hidden || state.connectionState === 'offline' || currentOverlayEntry()?.el !== modal) return;" in queue
    assert 'version === paddleVersion && JSON.stringify(next) !== JSON.stringify(paddleQueue)' in queue
    assert '}, LIVE_DETAIL_POLL_INTERVAL_MS);' in queue
    assert 'if (!reuseModal) modal._cleanupFns.push(() => clearInterval(modal._paddlePoll));' in detail


def test_errors_icon_and_styles_reuse_shared_tokens():
    errors = section(APP, '  const ERROR_TEXT = {', '  function humanError(')
    assert errors.index('court_closed:') < errors.index('checkin_required:') \
        < errors.index('queue_changed:') < errors.index('court_location_unavailable:')
    assert "checkin_required: 'Check in at this court first.'" in errors
    assert "'nearby_games', 'court_up'].includes(kind)) return 'map-pin';" in APP

    court_css = section(STYLES, '/* ---------- r83 · Court page ---------- */', '/* ---------- r83 · Play, planner & game page ---------- */')
    block = section(court_css, '/* r85 · Paddle line */', '.cd-queue-score .score-team-label')
    assert court_css.index('.court-modal .cd-presence-control { border: 0;') < court_css.index('/* r85 · Paddle line */')
    assert 'border-radius: var(--radius-lg); background: var(--surface-2); font-size: var(--text-sm);' in block
    assert '.cd-queue .btn { flex: none; min-height: var(--tap-min); white-space: nowrap; }' in block
    assert '.cd-queue small { color: var(--ink-soft); font-size: var(--text-xs); }' in block
    assert STYLES.count('/* r85 · Paddle line */') == 1


def test_join_undo_poll_and_game_done_flows_run_end_to_end():
    esc = section(APP, '  const esc = (s) =>', '  const UI_ICON_NAMES')
    html = section(APP, '  const paddleTeamsText =', '  let pendingCourtDetailOpen')
    detail = section(APP, 'async function openCourtDetail', 'function openCheckInSheet')
    flow = section(detail, '    // Paddle line: join/leave', "    modal.querySelector('#cd-play-now')")
    run(esc + html + '''
      const LIVE_DETAIL_POLL_INTERVAL_MS=15000, state={connectionState:'online'}, document={hidden:false};
      const intervals=[], cleared=[], toasts=[], errors=[], calls=[], responses=[], closed=[];
      const setInterval=(fn,ms)=>intervals.push({fn,ms}), clearInterval=id=>cleared.push(id);
      const toast=(message,options={})=>toasts.push([message,options]), errorToast=error=>errors.push(error);
      const api=async(path,options={})=>{
        calls.push([path,options.method||'GET',options.body?JSON.parse(options.body):null]);
        const next=responses.shift(); if(next instanceof Error)throw next; return next;
      };
      const beginButtonAction=target=>target?()=>{}:null;
      const button=(dataset={})=>({dataset,handlers:{},addEventListener(name,fn){this.handlers[name]=fn;}});
      const slot={html:'',contains:()=>false,set innerHTML(value){this.html=value;},get innerHTML(){return this.html;},
        querySelector(selector){return selector==='#cd-queue-toggle'&&this.html.includes('cd-queue-toggle')?(this.toggle=button()):null;},
        querySelectorAll(selector){const key=selector==='[data-queue-call]'?'queueCall':'queueDone';
          return [...this.html.matchAll(new RegExp(selector.slice(1,-1)+'="(\\\\d+)"','g'))].map(m=>(this[key]=button({[key]:m[1]})));}};
      const modal={isConnected:true,_cleanupFns:[],querySelector:selector=>selector==='#cd-queue'?slot:null};
      const currentOverlayEntry=()=>({el:modal}), openChildModal=(parent,open)=>open();
      const sheetButton=button(), inputs=[{value:'11'},{value:' 7 '}];
      let sheetHtml='';
      const openModal=markup=>(sheetHtml=markup,{querySelector:()=>sheetButton,querySelectorAll:()=>inputs});
      const modalHead=title=>title, closeModal=sheet=>closed.push(sheet);
      const courtClosed=false, reuseModal=null;
      const waiting={court_count:2,waiting_count:1,courts:[],in_line:false,my_court:null,my_position:null};
      const court={id:7,paddle_queue:waiting};
      const paddleQueueBlock=q=>paddleQueueHtml(q,{checkedIn:true,crowded:false,rotation:''});
      slot.innerHTML=paddleQueueBlock(court.paddle_queue);
    ''' + flow + '''
      (async()=>{
        assert.equal(intervals.length,0);
        assert.equal(modal._cleanupFns.length,1);
        responses.push({...waiting,waiting_count:2,in_line:true,my_position:2});
        await slot.toggle.handlers.click({currentTarget:slot.toggle});
        assert.deepEqual(calls.at(-1),['/courts/7/queue','POST',{action:'join'}]);
        const joined=toasts.at(-1);
        assert.equal(joined[0],'You’re #2 in line');
        assert.equal(joined[1].action.label,'Undo');
        assert.equal(intervals.length,1);
        assert.equal(intervals[0].ms,15000);

        const ana={id:1,name:'Ana'},ben={id:2,name:'Ben'};
        const onCourt={...waiting,waiting_count:0,in_line:true,my_court:1,courts:[{court:1,teams:[[ana],[ben]]}]};
        responses.push(onCourt);
        await intervals[0].fn();
        assert.equal(toasts.at(-1)[0],'You’re up on Court 1');
        assert.ok(slot.innerHTML.includes('Court 1 · Ana vs Ben'));
        responses.push(onCourt);
        await intervals[0].fn();
        assert.equal(toasts.length,2);
        document.hidden=true;
        const polled=calls.length;
        await intervals[0].fn();
        assert.equal(calls.length,polled);
        document.hidden=false;

        slot.queueDone.handlers.click();
        assert.ok(sheetHtml.includes('Court 1 · Game done'));
        assert.ok(sheetHtml.includes('Call next players'));
        const changed=Object.assign(new Error('The paddle line just changed.'),
          {data:{paddle_queue:{...onCourt,courts:[{court:1,teams:[[ben],[ana]]}]}}});
        responses.push(changed);
        await sheetButton.handlers.click({currentTarget:sheetButton});
        assert.deepEqual(calls.at(-1),['/courts/7/queue/next','POST',{court:1,expected:[1,2],score:[11,7]}]);
        assert.equal(errors.at(-1),changed);
        assert.equal(closed.length,1);
        assert.ok(slot.innerHTML.includes('Court 1 · Ben vs Ana'));

        slot.queueDone.handlers.click();
        responses.push({...onCourt,my_court:null,my_position:1,waiting_count:1});
        await sheetButton.handlers.click({currentTarget:sheetButton});
        assert.deepEqual(calls.at(-1)[2].expected,[2,1]);
        assert.equal(toasts.at(-1)[0],'Next players called to Court 1');
        assert.equal(closed.length,2);

        responses.push({...waiting,in_line:false});
        await joined[1].action.onClick();
        assert.deepEqual(calls.at(-1),['/courts/7/queue','POST',{action:'leave'}]);
        assert.ok(cleared.includes(1));
        assert.ok(slot.innerHTML.includes('Join the line'));
      })().catch(error=>{console.error(error);process.exit(1);});
    ''')


def test_redrawing_the_line_keeps_focus_on_the_same_control():
    source = (ROOT / 'public' / 'app-v15.js').read_text()
    start = source.index('const renderPaddleQueue = (next) => {')
    block = source[start:source.index('const setPaddleLine', start)]
    assert "const focused = slot.contains(document.activeElement) ? document.activeElement : null;" in block
    assert "[data-queue-done=\"${focused.dataset.queueDone}\"]" in block
    assert "?.focus({ preventScroll: true });" in block
