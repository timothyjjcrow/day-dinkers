"""A geocoder service error is retryable, not an empty-city result."""
from tests.test_private_session_links_frontend import section, run_js


def test_city_service_error_can_retry_the_same_query_without_selecting_an_area():
    result = run_js('''
      let inputHandler,retryHandler,timer,call=0,focused=0;const requests=[],picked=[];
      const esc=x=>String(x),uiIcon=()=>'';
      const input={value:'Portland',disabled:false,focus:()=>focused++,addEventListener:(_event,fn)=>inputHandler=fn};
      const results={innerHTML:'',setAttribute(){},removeAttribute(){},
        insertAdjacentHTML(_where,html){this.innerHTML+=html;},
        querySelector:()=>({addEventListener:(_event,fn)=>retryHandler=fn}),querySelectorAll:()=>[]};
      const setTimeout=fn=>{timer=fn;return 1;},clearTimeout=()=>{};
      const api=async path=>{requests.push(path);return ++call===1
        ? {items:[],error:'geocode_unavailable'}
        : {items:[{label:'Portland, Oregon',detail:'Portland, Oregon, United States',lat:45.5,lng:-122.6}]};};
    ''' + section('function bindCitySearch(', '// Home-area picker:') + '''
      (async()=>{
        bindCitySearch(input,results,p=>picked.push(p));inputHandler();await timer();
        const failure=results.innerHTML;retryHandler();await timer();
        console.log(JSON.stringify({failure,success:results.innerHTML,requests,picked,focused}));
      })();
    ''')
    assert 'role="alert"' in result['failure'] and 'data-city-retry' in result['failure']
    assert 'No cities found' not in result['failure']
    assert 'Portland, Oregon' in result['success'] and 'data-city="0"' in result['success']
    assert result['requests'] == ['/geocode?q=Portland'] * 2
    assert result['picked'] == []
    assert result['focused'] == 1
