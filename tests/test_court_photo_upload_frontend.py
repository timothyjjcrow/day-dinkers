"""Photo upload commits survive UI refresh failures and ignore an unrelated account."""
from tests.test_product_audit_play_frontend import functions_between, run_js


def harness():
    return '''
    const state={me:{id:1},courts:[{id:1,photo_count:0},{id:2,photo_count:7}]};
    const court={id:1,name:'Cedar',photo_count:0,photo_url:''};
    const esc=String,modalHead=()=>'',courtPhotoCategories=()=>[],calendarDateInTimeZone=()=> '2026-09-15';
    const fields={disabled:false},date={value:'',max:'2026-09-15'},caption={value:'North gate'},category={value:'entrance'};
    const nodes={'fieldset':fields,'#cap-date':date,'#cap-text':caption,'#cap-category':category};
    let submit,closed=false,authorized=false,savedCalls=0,error=null,calls=[];
    const form={querySelector:s=>nodes[s],addEventListener:(_event,fn)=>{submit=fn;}};
    const modal={isConnected:true,querySelector:s=>s==='#cap-form'?form:{classList:{add(){}}}};
    const openModal=()=>modal,dismissModal=()=>{closed=true;},toast=()=>{};
    const bindModalDiscardConfirmation=()=>({authorizeClose(){authorized=true;}});
    const bindModalFormUX=()=>({startSubmitting:()=>()=>{},showError:message=>{error=message;}});
    '''


def test_successful_upload_stays_successful_when_the_gallery_callback_throws():
    source=functions_between('function commitCourtPhoto(', 'function galleryPhotoMetaHtml(')
    result=run_js(harness()+'''
    const api=async(url,options)=>{calls.push({url,payload:JSON.parse(options.body)});return {photo_id:9,photo_url:'/api/courts/1/photos/9',photo_count:1};};
    '''+source+'''
    (async()=>{openCourtPhotoUpload(court,'data:image/png;base64,fixture',{onSaved(){savedCalls++;throw new Error('Gallery render failed');}});
      await submit({preventDefault(){}});
      console.log(JSON.stringify({closed,authorized,savedCalls,error,court,cache:state.courts,calls}));})();
    ''')
    assert result['closed'] and result['authorized'] and result['savedCalls']==1 and result['error'] is None
    assert result['court']['photo_count']==1 and result['cache'][0]['photo_url']=='/api/courts/1/photos/9'
    assert result['cache'][1]['photo_count']==7
    assert len(result['calls'])==1 and result['calls'][0]['payload']['captured_on'] is None


def test_an_upload_response_after_an_account_change_does_not_update_the_new_viewer():
    source=functions_between('function commitCourtPhoto(', 'function galleryPhotoMetaHtml(')
    result=run_js(harness()+'''
    let finish;const api=()=>new Promise(resolve=>finish=resolve);
    '''+source+'''
    (async()=>{openCourtPhotoUpload(court,'data:image/png;base64,fixture',{onSaved(){savedCalls++;}});
      const pending=submit({preventDefault(){}});state.me={id:2};
      finish({photo_id:9,photo_url:'/api/courts/1/photos/9',photo_count:1});await pending;
      console.log(JSON.stringify({closed,authorized,savedCalls,error,court,cache:state.courts}));})();
    ''')
    assert not result['closed'] and not result['authorized'] and result['savedCalls']==0 and result['error'] is None
    assert result['court']['photo_count']==0 and result['cache'][0]['photo_count']==0
