"""Photo viewer state checks: cancelled work and confirmed identity stay consistent."""
from tests.test_product_audit_play_frontend import functions_between, run_js


def viewer_harness():
    return '''
    const state={me:{id:1}},esc=String,uiIcon=()=>'',modalHead=()=>'',galleryPhotoMetaHtml=()=>'';
    const setDialogLabel=()=>{},decorateFlowChildModal=()=>{},toast=()=>{};
    let nodes={},closed=false,deleted=[],changed=[];
    const makeNode=disabled=>({disabled,isConnected:true,handlers:{},classList:{add(){},remove(){}},
      addEventListener(name,fn){this.handlers[name]=fn;},focus(){},setAttribute(){},removeAttribute(){}});
    const box={classList:{add(){}},set innerHTML(value){
      nodes=Object.fromEntries(['[data-gallery-prev]','[data-gallery-next]','[data-lightbox-like]','[data-lightbox-delete]','[data-photo-error]','.gallery-lightbox-stage'].map(key=>[key,makeNode(false)]));
      nodes['[data-gallery-prev]'].disabled=value.includes('data-gallery-prev disabled');
      nodes['[data-gallery-next]'].disabled=value.includes('data-gallery-next disabled');
    },querySelector:s=>nodes[s],querySelectorAll:()=>Object.entries(nodes).filter(([key])=>!key.includes('error')&&!key.includes('stage')).map(([,value])=>value)};
    const modal={isConnected:true,querySelector:()=>box};
    const openModal=()=>modal,currentOverlayEntry=()=>({el:modal}),requestAnimationFrame=fn=>fn();
    const dismissModal=()=>{closed=true;};
    const openContentReport=()=>{};
    const photos=[{id:11,can_delete:true},{id:12,can_delete:true}];
    '''


def test_cancelled_delete_restores_controls_and_keeps_the_photos():
    source=functions_between('function openCourtPhotoLightbox(', 'async function openCourtGallery(')
    result=run_js(viewer_harness()+'''
    const deleteCourtPhoto=async()=>null;
    '''+source+'''
    (async()=>{openCourtPhotoLightbox({id:1},photos,0,{onChange:event=>changed.push(event)});
      const button=nodes['[data-lightbox-delete]'];
      await button.handlers.click({currentTarget:button});
      console.log(JSON.stringify({ids:photos.map(p=>p.id),deleteDisabled:button.disabled,nextDisabled:nodes['[data-gallery-next]'].disabled,previousDisabled:nodes['[data-gallery-prev]'].disabled,changed}));
    })();
    ''')
    assert result==dict(ids=[11,12],deleteDisabled=False,nextDisabled=False,previousDisabled=True,changed=[])


def test_pending_delete_blocks_navigation_and_removes_only_the_confirmed_photo():
    source=functions_between('function openCourtPhotoLightbox(', 'async function openCourtGallery(')
    result=run_js(viewer_harness()+'''
    let finish;
    const deleteCourtPhoto=(_court,photo)=>{deleted.push(photo.id);return new Promise(resolve=>finish=resolve);};
    '''+source+'''
    (async()=>{openCourtPhotoLightbox({id:1},photos,0,{onChange:event=>changed.push(event)});
      const button=nodes['[data-lightbox-delete]'];
      const pending=button.handlers.click({currentTarget:button});
      nodes['[data-gallery-next]'].handlers.click();
      box.onkeydown({key:'ArrowRight',target:{closest:()=>null},preventDefault(){}});
      finish({deleted:true});await pending;
      console.log(JSON.stringify({ids:photos.map(p=>p.id),deleted,changed,closed}));
    })();
    ''')
    assert result==dict(ids=[12],deleted=[11],changed=[dict(deletedId=11)],closed=False)


def test_late_like_response_cannot_update_a_different_accounts_photo_state():
    source=functions_between('async function toggleCourtPhotoLike(', 'async function deleteCourtPhoto(')
    result=run_js('''
    const state={me:{id:1}},uiIcon=()=>'',toast=()=>{};let finish;
    const api=()=>new Promise(resolve=>finish=resolve);
    const button={isConnected:true,disabled:false,setAttribute(){},removeAttribute(){},classList:{remove(){},add(){}}};
    const photo={id:2,liked_by_me:false,likes:0};
    '''+source+'''
    (async()=>{const pending=toggleCourtPhotoLike({id:1},photo,button);state.me={id:9};finish({liked:true,likes:1});
      const result=await pending;console.log(JSON.stringify({result,photo,disabled:button.disabled}));})();
    ''')
    assert result==dict(result=False,photo=dict(id=2,liked_by_me=False,likes=0),disabled=False)
