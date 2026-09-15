"""Execute the discovery view's rendering and retry/stale-read behavior."""
from test_dated_session_interactions_frontend import run, section


def test_live_cards_preserve_full_names_distinguish_counts_and_disable_unavailable_actions():
    run(r'''
      const assert=require('node:assert/strict');
      const esc=x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
      const rallyActionState=r=>({label:r.available?'On my way':'Starting soon',enabled:r.available});
      const rallyDatasetAttributes=r=>`data-rally-game-id="${r.gameId}"`;
    ''' + section('function livePlayCountsHtml', 'async function openPlaySoonArrivalChoices') + r'''
      const rally={gameId:41,courtId:2,courtName:'Cedar Park <North> Courts',courtCity:'Portland',
        readyCount:2,onWayCount:3,spotsLeft:2,distanceMiles:0,available:true};
      const html=arrivalRallyCardHtml(rally);
      assert.ok(html.includes('<h4>Cedar Park &lt;North&gt; Courts</h4>'));
      assert.ok(html.includes('<dt>Here</dt><dd>2</dd>'));
      assert.ok(html.includes('<dt>On the way</dt><dd>3</dd>'));
      assert.ok(html.includes('<dt>Open spots</dt><dd>2</dd>'));
      assert.match(html,/0 mi/);
      assert.match(html,/data-live-court="2"/);
      assert.match(html,/data-rally-game-id="41"/);
      assert.ok(!html.includes('disabled'));
      const unavailable=arrivalRallyCardHtml({...rally,available:false,distanceMiles:null});
      assert.match(unavailable,/disabled>Starting soon/);
      assert.ok(!unavailable.includes('0 mi'));
    ''')


def test_discovery_retries_in_place_and_ignores_closed_or_changed_account_responses():
    run(r'''
      const assert=require('node:assert/strict');
      const state={me:{id:1,home_area:'Portland'}}, esc=String, modalHead=()=>'', uiIcon=()=>'';
      const committedAreaLatLng=()=>({lat:45.5,lng:-122.7});
      const normalizeLookingRallies=x=>x.rallies||[], normalizeLookingPlayersWithoutRally=x=>x.players||[];
      const activeArrivalForGame=()=>null, arrivalRallyCardHtml=()=>'<article>LIVE GAME</article>';
      const transitionModal=()=>{}, openPlayPulseCourtPicker=()=>{};
      let modal, nodes, requests=[];
      function node() { return {innerHTML:'',textContent:'',disabled:false,attrs:{},hidden:false,
        classList:{add(){},remove(){},toggle(){}},
        setAttribute(k,v){this.attrs[k]=v;},focus(){this.focused=true;},scrollIntoView(){},
        addEventListener(k,fn){this[k]=fn;},querySelectorAll(){return [];},querySelector(){return nodes.retry;}}; }
      function openModal(){nodes={retry:node()};modal={isConnected:true,_cleanupFns:[],querySelector(key){return nodes[key]||=node();}};return modal;}
      function api(url){return new Promise((resolve,reject)=>requests.push({url,resolve,reject}));}
    ''' + section('async function openPlaySoonArrivalChoices', 'async function openPlayNowCourtPicker') + r'''
      (async()=>{
        const opening=openPlaySoonArrivalChoices();
        const results=nodes['#play-soon-rallies'], refresh=nodes['#live-play-refresh'];
        assert.equal(refresh.disabled,true);
        assert.match(requests[0].url,/lat=45.5&lng=-122.7&radius=25/);
        requests[0].reject(new Error('Offline'));
        await opening;
        assert.match(results.innerHTML,/Couldn’t load nearby play/);
        assert.match(results.innerHTML,/Try again/);
        assert.equal(refresh.disabled,false);
        const retry=nodes.retry.click();
        assert.equal(results.focused,true);
        requests[1].resolve({rallies:[],players:[]});await retry;
        assert.match(results.innerHTML,/No nearby play right now/);
        assert.ok(!results.innerHTML.includes('Couldn’t load'));
        const next=refresh.click();
        state.me.id=2;
        requests[2].resolve({rallies:[{gameId:3,arrivalAvailable:true,spotsLeft:2}]});await next;
        assert.ok(!results.innerHTML.includes('LIVE GAME'));
        state.me.id=1;
        const closed=refresh.click();
        modal._cleanupFns.forEach(fn=>fn());modal.isConnected=false;
        requests[3].resolve({rallies:[{gameId:3,arrivalAvailable:true,spotsLeft:2}]});await closed;
        assert.ok(!results.innerHTML.includes('LIVE GAME'));
      })().catch(error=>{console.error(error);process.exitCode=1;});
    ''')
