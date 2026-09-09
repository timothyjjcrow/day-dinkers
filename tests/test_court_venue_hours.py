"""Public map/detail filters use reviewed hours, never private drafts."""
import json
from datetime import datetime, UTC
import pytest
from test_business_reviewed_versions import app, client, live, current, versioned
from test_business_governance import auth
from backend.app import db
from backend.models import BusinessProfile, Court
from backend.services.court_hours import hours_status, normalize_hours, public_hours_for


def week(start='00:00', end='00:00', zone='UTC'):
    return {'timezone':zone, **{day:[{'open':start,'close':end}] for day in ('mon','tue','wed','thu','fri','sat','sun')}}


def test_holiday_replaces_overnight_hours_and_missing_days_are_unknown():
    schedule={'timezone':'America/Los_Angeles','mon':[{'open':'22:00','close':'02:00'}], 'tue':[], 'exceptions':{'2026-09-08':[]}}
    assert hours_status(schedule, as_of=datetime(2026,9,8,6,tzinfo=UTC))['is_open'] is True
    assert hours_status(schedule, as_of=datetime(2026,9,8,8,tzinfo=UTC))['is_open'] is False
    assert hours_status(schedule, as_of=datetime(2026,9,9,19,tzinfo=UTC))['is_open'] is None
    del schedule['timezone']
    assert hours_status(schedule)['is_open'] is None
    for invalid in [{**week(),'timezone':'Mars/Phobos'}, {**week(),'exceptions':{'bad':[]}}, {**week(),'mon':[{'open':'25:00','close':'08:00'}]}]:
        with pytest.raises(ValueError): normalize_hours(invalid)


def test_published_reviewed_hours_override_both_map_detail_and_filter(app, client, live):
    owner,bid=live
    with app.app_context():
        business=db.session.get(BusinessProfile,bid); court=business.court
        court.structured_hours=json.dumps(week())
        business.structured_hours=json.dumps({**week(),'exceptions':{datetime.now(UTC).date().isoformat():[]}})
        business.timezone='UTC'; db.session.commit(); cid=court.id
    detail=client.get(f'/api/courts/{cid}').get_json()
    assert detail['hours_source']=='venue' and detail['open_status']['is_open'] is False
    items=client.get('/api/courts?q=Official').get_json()['items']
    card=next(item for item in items if item['id']==cid)
    assert card['open_status']==detail['open_status']
    assert cid not in {row['id'] for row in client.get('/api/courts?open_now=1').get_json()['items']}
    saved=client.patch(f'/api/businesses/{bid}',json={'structured_hours':week()},headers=versioned(owner,current(client,owner,bid)))
    assert saved.status_code==200,saved.get_json()
    assert saved.get_json()['has_unpublished_changes'] is True
    assert client.get(f'/api/courts/{cid}').get_json()['open_status']['is_open'] is False
    manager=current(client,owner,bid)
    assert manager['effective_hours']['open_status']['is_open'] is True
    unpublished=client.patch(f'/api/businesses/{bid}',json={'published':False},headers=versioned(owner,manager))
    assert unpublished.status_code==200
    fallback=client.get(f'/api/courts/{cid}').get_json()
    assert fallback['hours_source']=='community' and fallback['open_status']['is_open'] is True
    with app.app_context():
        assert db.session.get(Court,cid).structured_hours_dict()==week()


def test_hours_save_requires_version_and_rejects_ambiguous_modes(client, live):
    owner,bid=live
    assert client.patch(f'/api/businesses/{bid}',json={'structured_hours':week()},headers=auth(owner['token'])).status_code==428
    for body in [{'structured_hours':{'mon':[]}}, {'structured_hours':week(),'hours_dawn_to_dusk':True}]:
        result=client.patch(f'/api/businesses/{bid}',json=body,headers=versioned(owner,current(client,owner,bid)))
        assert result.status_code==400,result.get_json()


def test_owner_hours_form_preserves_split_periods_closed_and_unknown_days():
    import subprocess
    subprocess.run(['node','-e',r'''
      const assert=require('node:assert/strict');require('./public/venue-workspace-v15.js');const venue=globalThis.VenueWorkspace;
      const row=(open,close)=>({hidden:false,querySelector:selector=>({value:selector==='[data-hours-open]'?open:close})});
      const day=(key,mode,windows,on='')=>({dataset:{hoursWeekday:key},querySelector:selector=>({value:selector==='[data-hours-date]'?on:mode}),querySelectorAll:()=>windows.map(values=>row(...values))});
      const days=[day('mon','open',[['08:00','12:00'],['15:00','20:00']]),day('tue','closed',[]),day('wed','unknown',[])];
      const dates=[day('','closed',[],'2026-12-25')];
      const modal={querySelector:selector=>({value:selector==='#venue-hours-mode'?'weekly':selector==='#venue-hours-note'?'Enter through the east gate.':'America/Los_Angeles'}),querySelectorAll:selector=>selector==='[data-hours-weekday]'?days:dates};
      const saved=venue.readHoursForm(modal);
      assert.equal(saved.timezone,'America/Los_Angeles');
      assert.equal(saved.structured_hours.mon.length,2);
      assert.deepEqual(saved.structured_hours.tue,[]);
      assert.ok(!('wed' in saved.structured_hours));
      assert.deepEqual(saved.structured_hours.exceptions['2026-12-25'],[]);
      dates.push(day('','closed',[],'2026-12-25'));
      assert.throws(()=>venue.readHoursForm(modal),/one hours entry per special date/);
      const html=venue.hoursForm({structured_hours:saved.structured_hours});
      assert.ok(html.includes('value="15:00"'));
      assert.ok(html.includes('value="2026-12-25"'));
      assert.ok(html.includes('Use community court hours'));
      assert.ok(!html.includes('JSON'));
    '''],check=True,capture_output=True,text=True)
