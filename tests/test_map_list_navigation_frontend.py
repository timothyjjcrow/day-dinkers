"""Court lists open details; explicit map actions preserve preview navigation."""
from tests.test_product_audit_play_frontend import functions_between, run_js


def test_list_detail_and_map_buttons_take_distinct_paths():
    source=functions_between("el.querySelectorAll('[data-court]').forEach", "el.querySelectorAll('[data-court-business]').forEach")
    result=run_js('''
      const calls=[],byId=new Map([[1,{id:1}],[2,{id:2}]]);
      const make=(id,list,key='court')=>({dataset:{[key]:String(id)},classList:{contains:()=>list},addEventListener(_event,fn){this.click=fn;}});
      const list=make(1,true),peek=make(2,false),map=make(1,false,'courtMap');
      const el={querySelectorAll:selector=>selector==='[data-court]' ? [list,peek] : selector==='[data-court-map]' ? [map] : []};
      const openCourtFromDiscovery=court=>calls.push(['details',court.id]),activateCourtFromDiscovery=court=>calls.push(['map',court.id]);
    '''+source+'''
      list.click();peek.click();map.click();console.log(JSON.stringify(calls));
    ''')
    assert result==[['details',1],['map',2],['map',1]]


def test_compact_map_session_keeps_price_capacity_date_and_external_booking_context():
    source=functions_between('function courtTimelineItemHtml(', 'function courtEntryDescriptionParts(')
    result=run_js('''
      const esc=value=>String(value).replaceAll('<','&lt;'),uiIcon=()=>'',gameLevelRangeLabel=()=> '3.0–3.5';
      const businessActionHref=value=>value||'',businessTrackingAttributes=()=>'',courtTimelineTime=()=> '6 PM',upcomingDayLabel=()=> 'Tomorrow';
    '''+source+'''
      const player={source:'player',source_label:'Player-organized',title:'Doubles <friends>',starts_at:'2030-01-01T18:00:00Z',
        action:'open_session',action_label:'View waitlist',game:{id:7,game_type:'ranked',max_players:4,spots_left:0,cost_cents:1500}};
      const venue={source:'venue',source_label:'Venue event',title:'Evening clinic',starts_at:player.starts_at,action:'external',action_label:'Register externally',
        schedule:{price_text:'$25',booking_url:'https://venue.example/book'}};
      console.log(JSON.stringify([player,venue].map(item=>courtTimelineItemHtml(item,{compact:true,mapPreview:true}))));
    ''')
    player,venue=result
    assert 'Next · Tomorrow · 6 PM' in player and 'Doubles &lt;friends>' in player
    assert 'Ranked match' in player and '3.0–3.5' in player and 'Full' in player and '$15.00 per player' in player
    assert 'data-court-timeline-game="7"' in player and 'View waitlist' in player
    assert 'Venue event' in venue and '$25' in venue and 'href="https://venue.example/book"' in venue
    assert 'Register externally' in venue and 'venue.example' in venue
