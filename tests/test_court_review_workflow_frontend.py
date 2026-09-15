"""Executable review-state checks, including author privacy and late page responses."""
from tests.test_product_audit_play_frontend import functions_between, run_js


def test_committed_review_updates_aggregate_cache_and_delete_removes_only_that_review():
    source=functions_between('function commitCourtReview(', 'async function deleteCourtReview(')
    result=run_js('''
      const state={courts:[{id:1,rating_count:2},{id:2,rating_count:7}]};
      const court={id:1,my_review:{id:4,user_id:10},reviews:[{id:4,user_id:10},{id:5,user_id:11}]};
    '''+source+'''
      commitCourtReview(court,{review:{id:4,user_id:10,rating:1,comment:'Changed'},rating_avg:3,rating_count:2});
      const saved=JSON.parse(JSON.stringify({court,cache:state.courts}));
      commitCourtReview(court,{rating_avg:5,rating_count:1},{deletedId:4});
      console.log(JSON.stringify({saved,deleted:court,cache:state.courts}));
    ''')
    assert result['saved']['court']['reviews'][0]['comment']=='Changed'
    assert len(result['saved']['court']['reviews'])==2
    assert result['deleted']['my_review'] is None
    assert [row['id'] for row in result['deleted']['reviews']]==[5]
    assert result['cache'][0]['rating_count']==1 and result['cache'][1]['rating_count']==7


def reader_harness():
    return '''
      const esc=String,modalHead=()=>'',skeletonHtml=()=>'<loading>',courtReviewSummaryHtml=c=>`${c.rating_avg}:${c.rating_count}`;
      const courtReviewCardHtml=row=>`<article data-review-id="${row.id}">${row.comment}</article>`;
      const node=()=>({innerHTML:'',textContent:'',disabled:false,handlers:{},classList:{toggle(){},add(){}},
        setAttribute(){},removeAttribute(){},addEventListener(name,fn){this.handlers[name]=fn;},querySelectorAll:()=>[],querySelector:()=>null});
      const nodes=Object.fromEntries(['court-review-list','court-review-list-summary','court-review-own','court-review-write','court-review-more','court-review-load-error'].map(key=>[key,node()]));
      const modal={isConnected:true,_destroyed:false,querySelector:s=>nodes[s.slice(1)],closest:()=>null};
      const openModal=()=>modal,document={activeElement:{hasAttribute:()=>false}},requestAnimationFrame=fn=>fn();
      const openChildModal=(_parent,fn)=>fn();
      let change;
      const openCourtReviewEditor=(_court,options)=>{change=options.onSaved;};
    '''


def test_anonymous_reviews_are_not_filtered_as_the_viewers_own_review():
    source=functions_between('function openCourtReviews(', '// Build a calendar event')
    result=run_js(reader_harness()+'''
      const state={me:null},court={id:1};
      const api=async()=>({items:[{id:1,user_id:null,comment:'First'},{id:2,user_id:null,comment:'Second'}],my_review:null,rating_avg:4,rating_count:2,has_more:false});
    '''+source+'''
      openCourtReviews(court);
      setImmediate(()=>console.log(JSON.stringify({html:nodes['court-review-list'].innerHTML,own:nodes['court-review-own'].innerHTML})));
    ''')
    assert 'First' in result['html'] and 'Second' in result['html']
    assert result['own']==''


def test_page_arriving_after_review_save_cannot_replace_new_rating_or_own_review():
    source=functions_between('function openCourtReviews(', '// Build a calendar event')
    result=run_js(reader_harness()+'''
      const state={me:{id:10}},court={id:1};let calls=0,resolvePage;
      const api=async()=>++calls===1 ? {items:[{id:2,user_id:11,comment:'Other'}],my_review:{id:1,user_id:10,comment:'Mine'},rating_avg:4,rating_count:2,has_more:true,next_before_id:2}
        : new Promise(resolve=>resolvePage=resolve);
    '''+source+'''
      (async()=>{
        openCourtReviews(court);await new Promise(setImmediate);
        const pending=nodes['court-review-more'].handlers.click();
        nodes['court-review-write'].handlers.click();
        court.my_review={id:1,user_id:10,comment:'Changed'};court.rating_avg=3;change();
        resolvePage({items:[{id:1,user_id:10,comment:'Old'}],my_review:{id:1,user_id:10,comment:'Old'},rating_avg:4,rating_count:2,has_more:false});
        await pending;
        console.log(JSON.stringify({own:nodes['court-review-own'].innerHTML,summary:nodes['court-review-list-summary'].innerHTML,comment:court.my_review.comment,moreEnabled:!nodes['court-review-more'].disabled}));
      })();
    ''')
    assert 'Changed' in result['own'] and 'Old' not in result['own']
    assert result['summary']=='3:2' and result['comment']=='Changed' and result['moreEnabled']
