"""Uploaded photo results and cover identities remain useful after every change."""
import base64
from test_business_governance import app, client, auth, register
from backend.app import db
from backend.models import Court, CourtPhoto, UserReport
from backend.routes.moderation import _remove_reported_content


def add(client, token, category, byte):
    result=client.post('/api/courts/1/photo',headers=auth(token),json={
        'photo':'data:image/png;base64,'+base64.b64encode(byte*200).decode(),
        'category':category,'caption':'North gate','captured_on':'2026-01-02'})
    assert result.status_code==201,result.get_json()
    return result.get_json()


def test_upload_returns_the_saved_gallery_card_and_stable_selected_cover(client):
    token=register(client,'upload-result@example.test')['token']
    first=add(client,token,'court',b'a')
    photo=first['photo']
    assert photo['id']==first['photo_id'] and photo['url']==first['photo_url']
    assert photo['can_delete'] and not photo['liked_by_me'] and photo['likes']==0
    assert photo['caption']=='North gate' and photo['category']=='court' and photo['captured_on']=='2026-01-02'
    assert photo['created_at']
    parking=add(client,token,'parking',b'p')
    assert parking['photo_url']==first['photo_url']
    newest=add(client,token,'court',b'b')
    assert newest['photo_url']!=first['photo_url']
    assert client.get(newest['photo_url']).data==b'b'*200
    assert client.get(first['photo_url']).data==b'a'*200
    deleted=client.delete(f"/api/courts/1/photos/{newest['photo_id']}",headers=auth(token)).get_json()
    assert deleted['photo_url']==first['photo_url'] and deleted['photo_count']==2
    assert client.get('/api/courts/1').get_json()['photo_url']==first['photo_url']
    legacy=client.get('/api/courts/1/photo')
    assert legacy.data==b'a'*200 and 'must-revalidate' in legacy.headers['Cache-Control']
    assert 'max-age=86400' in client.get(first['photo_url']).headers['Cache-Control']
    page=client.get('/api/courts/1/photos').get_json()
    assert page['limit']==12
    anon=page['items']
    assert all(row['user_name']=='Player' and not row['can_delete'] for row in anon)


def test_report_removal_reselects_a_remaining_cover_and_preserves_external_cover(app,client):
    token=register(client,'reported-cover@example.test')['token']
    fallback=add(client,token,'parking',b'p')
    cover=add(client,token,'court',b'c')
    with app.app_context():
        report=UserReport(content_type='court_photo',content_id=cover['photo_id'])
        assert _remove_reported_content(report)
        db.session.commit()
        assert db.session.get(Court,1).photo_url==fallback['photo_url']
        court=db.session.get(Court,1);court.photo_url='https://example.test/venue-cover.jpg';db.session.commit()
    added=add(client,token,'court',b'n')
    assert added['photo_url']=='https://example.test/venue-cover.jpg'
    removed=client.delete(f"/api/courts/1/photos/{added['photo_id']}",headers=auth(token)).get_json()
    assert removed['photo_url']=='https://example.test/venue-cover.jpg'
