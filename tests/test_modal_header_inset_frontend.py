"""Focus scrolling follows an enlarged or refreshed sticky dialog header."""
from tests.test_private_session_links_frontend import section, run_js


def test_inset_tracks_header_resize_replacement_and_cleans_up_observers():
    source = section('function observeModalHeaderInset(', 'function openModal(')
    result = run_js('''
      let resizeCallback, mutationCallback, watched=[], stops=0;
      class ResizeObserver {
        constructor(fn){resizeCallback=fn;} observe(x){watched.push(x.id);}
        unobserve(x){watched.push('remove:'+x.id);} disconnect(){stops++;}
      }
      class MutationObserver {
        constructor(fn){mutationCallback=fn;} observe(){} disconnect(){stops++;}
      }
      const window={ResizeObserver,MutationObserver};
    ''' + source + '''
      let height=60, header={id:'loading',getBoundingClientRect:()=>({height})};
      const box={style:{},querySelector:()=>header};
      const cleanup=observeModalHeaderInset(box);
      const initial=box.style.scrollPaddingTop;
      height=192;resizeCallback();const large=box.style.scrollPaddingTop;
      header={id:'loaded',getBoundingClientRect:()=>({height:80})};mutationCallback();
      const replaced=box.style.scrollPaddingTop;
      cleanup();console.log(JSON.stringify({initial,large,replaced,watched,stops}));
    ''')
    assert result == {'initial':'68px','large':'200px','replaced':'88px',
                      'watched':['loading','remove:loading','loaded'],'stops':2}
