"""The edit-scope list and save share the current-time boundary and host rights."""
from datetime import timedelta
from test_game_recurrence_patterns import app, client, register, auth, recurring_payload, dated_rows
from backend.app import db
from backend.models import Game, Notification


def series(client):
    host=register(client,'scope-host@example.test','Host')
    first=client.post('/api/games',json=recurring_payload(),headers=auth(host)).get_json()
    return host,first,dated_rows(first['id'])


def test_scope_uses_current_time_and_read_does_not_change_data(client):
    host,first,rows=series(client)
    selected=rows[1]
    moved=rows[0]
    moved.scheduled_at=rows[-1].scheduled_at+timedelta(hours=1)
    db.session.commit()
    before=(Game.query.count(),Notification.query.count())
    response=client.get(f'/api/games/{selected.id}/edit-dates',headers=auth(host))
    assert response.status_code==200
    result=response.get_json()
    expected=[selected.id]+[row.id for row in sorted(rows,key=lambda row:(row.scheduled_at,row.id)) if row.id!=selected.id and row.scheduled_at>=selected.scheduled_at]
    assert [row['id'] for row in result['dates']]==expected
    assert result['dates'][0]['is_selected'] is True
    assert result['dates'][-1]['id']==moved.id
    assert before==(Game.query.count(),Notification.query.count())
    token=result['token']
    selected_id=selected.id
    updated=client.patch(f'/api/games/{selected_id}',headers=auth(host),json={'edit_scope':'following_dates','title':'Same group','expected_edit_dates':token})
    assert updated.status_code==200,updated.get_json()
    assert all(db.session.get(Game,ident).title=='Same group' for ident in expected)


def test_scope_change_rejects_old_token_without_applying_edit(client):
    host,first,rows=series(client)
    selected=rows[0]
    snapshot=client.get(f'/api/games/{selected.id}/edit-dates',headers=auth(host)).get_json()
    rows[-1].scheduled_at += timedelta(days=1)
    db.session.commit()
    rejected=client.patch(f'/api/games/{selected.id}',headers=auth(host),json={'edit_scope':'following_dates','title':'Stale change','expected_edit_dates':snapshot['token']})
    assert rejected.status_code==409
    assert rejected.get_json()['error']=='edit_dates_changed'
    assert all(row.title!='Stale change' for row in rows)
    fresh=client.get(f'/api/games/{selected.id}/edit-dates',headers=auth(host)).get_json()
    assert fresh['token']!=snapshot['token']


def test_future_edit_cannot_change_other_hosts_dates(client):
    host,first,rows=series(client)
    other=register(client,'other-scope-host@example.test','Other host')
    rows[-1].creator_id=other['user']['id']
    db.session.commit()
    path=f"/api/games/{first['id']}"
    assert client.get(path+'/edit-dates',headers=auth(host)).get_json()['error']=='future_host_changed'
    update=client.patch(path,headers=auth(host),json={'edit_scope':'following_dates','title':'Not mine'})
    assert update.status_code==409
    assert update.get_json()['error']=='future_host_changed'
    single=client.patch(path,headers=auth(host),json={'edit_scope':'this_date','title':'Only mine'})
    assert single.status_code==200
    assert db.session.get(Game,rows[-1].id).title!='Only mine'
    assert client.get(path+'/edit-dates',headers=auth(other)).status_code==403
    assert client.get(path+'/edit-dates').status_code==401


def test_stopping_cancels_exactly_previewed_following_dates(client):
    host,first,rows=series(client)
    selected=rows[1]
    earlier=rows[0]
    data=client.get(f'/api/games/{selected.id}/edit-dates',headers=auth(host)).get_json()
    result=client.patch(f'/api/games/{selected.id}',headers=auth(host),json={'edit_scope':'following_dates','recurrence':'none','expected_edit_dates':data['token']})
    assert result.status_code==200,result.get_json()
    for row in data['dates']:
        assert db.session.get(Game,row['id']).status==('upcoming' if row['is_selected'] else 'cancelled')
    assert db.session.get(Game,earlier.id).status=='upcoming'
