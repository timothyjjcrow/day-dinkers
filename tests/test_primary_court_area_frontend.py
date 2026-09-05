"""The primary-court shortcut saves an area only after an explicit, valid choice."""
import json
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def test_primary_court_area_shortcut_success_failure_and_missing_court():
    source = 'function openHomeAreaSheet(' + APP.split('  function openHomeAreaSheet(', 1)[1].split('  async function maybeSuggestStarterCourts', 1)[0]
    script = '''
      const state={me:{id:1,home_court_id:7,home_court_name:'Sunset Park'}};
      const esc=String,uiIcon=()=>'',requestAnimationFrame=fn=>fn();
      const calls=[];
      let modal, nodes, html, resolveCourt;
      function node(){return {disabled:false,isConnected:true,handlers:{},classList:{add(){},remove(){}},
        addEventListener(k,fn){this.handlers[k]=fn;},setAttribute(){},removeAttribute(){},focus(){},querySelectorAll(){return [];}};}
      function openModal(markup){
        html=markup;nodes=Object.fromEntries(['#ha-loc','#ha-city','#ha-results','.modal-close','#ha-save-status','.checkin-sheet'].map(k=>[k,node()]));
        if(markup.includes('id="ha-primary-court"'))nodes['#ha-primary-court']=node();
        modal={_cleanupFns:[],querySelector:k=>nodes[k]||null};return modal;
      }
      const api=path=>{calls.push(['read',path]);return new Promise(resolve=>resolveCourt=resolve);};
      const saveHomeArea=async(...args)=>{calls.push(['save',...args.slice(0,3)]);return true;};
      const beginButtonAction=(trigger,label,peers)=>{const controls=[trigger,...peers].filter(Boolean);controls.forEach(c=>c.disabled=true);return ()=>controls.forEach(c=>c.disabled=false);};
      const bindCitySearch=()=>{},fetchCourtsInView=()=>{},currentOverlayEntry=()=>({el:modal}),closeModal=()=>true;
      const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
    ''' + source + '''
      (async()=>{
        openHomeAreaSheet();
        const before=calls.length;
        nodes['#ha-primary-court'].handlers.click();
        const saving=nodes['#ha-loc'].disabled&&nodes['#ha-city'].disabled&&modal._dismissBlocked();
        nodes['#ha-primary-court'].handlers.click();
        const onlyOneRead=calls.length===1;
        resolveCourt({latitude:33.66,longitude:-117.91,city:'Costa Mesa',state:'CA'});await flush();
        const saved=calls.find(c=>c[0]==='save');
        openHomeAreaSheet();nodes['#ha-primary-court'].handlers.click();
        resolveCourt({latitude:null,longitude:null});await flush();
        const invalid={restored:!nodes['#ha-city'].disabled&&!nodes['#ha-primary-court'].disabled,
          message:nodes['#ha-save-status'].textContent,saveCount:calls.filter(c=>c[0]==='save').length};
        state.me.home_court_id=null;openHomeAreaSheet();
        process.stdout.write(JSON.stringify({before,saving,onlyOneRead,saved,invalid,missing:!nodes['#ha-primary-court']}));
      })().catch(error=>{console.error(error);process.exitCode=1;});
    '''
    result = json.loads(subprocess.run(['node', '-e', script], check=True,
                                      capture_output=True, text=True).stdout)
    assert result['before'] == 0
    assert result['saving'] and result['onlyOneRead']
    assert result['saved'] == ['save', 33.66, -117.91, 'Costa Mesa, CA']
    assert result['invalid']['restored']
    assert result['invalid']['saveCount'] == 1
    assert 'Choose a city instead' in result['invalid']['message']
    assert result['missing']
