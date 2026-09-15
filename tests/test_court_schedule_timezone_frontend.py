"""The map, saved court and detail schedule share date and clock semantics."""
from tests.test_product_audit_play_frontend import functions_between, run_js


def test_preview_dates_use_the_clock_zone_even_across_midnight():
    helpers = functions_between('function calendarDateInTimeZone(', 'function businessScheduleItemIsCurrent(')
    helpers += functions_between('function courtTimelineDate(', 'function courtTimelineItemHtml(')
    result = run_js(helpers + r''' 
      const now=new Date('2030-01-01T23:30:00Z');
      const east={starts_at:'2030-01-02T01:00:00Z',timezone:'Pacific/Kiritimati'};
      const west={starts_at:'2030-01-02T01:00:00Z',timezone:'America/Los_Angeles'};
      const community={event_date:'2030-01-03',window:{start:'08:00'}};
      const query=new URLSearchParams(courtTimelineQuery('2030-01-01','2030-01-02'));
      console.log(JSON.stringify({east:courtTimelineDay(east,now),west:courtTimelineDay(west,now),
        following:courtTimelineDay({...east,starts_at:'2030-01-02T15:00:00Z'},now),
        clock:courtTimelineTime(west),zone:query.get('viewer_timezone'),from:query.get('from'),to:query.get('to')}));
    ''')
    assert result['east']=='Today' and result['west']=='Today'
    assert result['following']=='Tomorrow'
    assert '5:00' in result['clock']
    assert result['zone'] and result['from']=='2030-01-01' and result['to']=='2030-01-02'


def test_detail_cards_keep_cost_roster_availability_and_local_listing_context():
    helpers=functions_between('function courtTimelineItemHtml(', 'const COURT_OPEN_PLAY_PLAN_SOURCE')
    result=run_js(''' 
      const esc=value=>String(value).replaceAll('<','&lt;'),uiIcon=()=>'',gameLevelRangeLabel=()=> '3.0–3.5';
      const businessActionHref=value=>value||'',businessTrackingAttributes=()=>'',courtTimelineTime=()=> '6 PM';
    '''+helpers+''' 
      const game={source:'player',source_label:'Player-organized',title:'Evening <doubles>',starts_at:'2030-01-02T01:00:00Z',
        action:'open_session',action_label:'View & join',game:{id:7,max_players:4,spots_left:1,cost_cents:1500,
        players:[{display_name:'Sam Rivera'}]}};
      const community={source:'community',source_label:'Community-listed open play',title:'Open play',
        action:'plan',key:'community:1',window:{cost:'$5'},timezone:''};
      console.log(JSON.stringify([courtTimelineItemHtml(game),courtTimelineItemHtml(community)]));
    ''')
    game,community=result
    assert 'court-timeline-card' in game and 'Evening &lt;doubles>' in game
    assert '1 spot left' in game and '$15.00 per player' in game and 'Sam Rivera' in game
    assert 'data-court-timeline-game="7"' in game
    assert 'Local court time' in community and '$5' in community
    assert 'Venue registration is separate' in community and 'data-court-timeline-plan="community:1"' in community
