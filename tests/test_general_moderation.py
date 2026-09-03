"""General moderation queue, enforcement, and content-removal coverage."""
import time

import pytest

from backend.app import create_app, db
from backend.models import (
    Court,
    CourtPhoto,
    CourtPhotoLike,
    CourtReview,
    Message,
    ModerationAction,
    Notification,
    PlayerFeedback,
    User,
    UserReport,
)
from backend.services.mfa import _totp_at


PASSWORD = 'secret123'


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        db.session.add(Court(
            name='Safety Court', city='Austin', state='TX', county_slug='travis',
            latitude=30.1, longitude=-97.1,
        ))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def auth(token):
    return {'Authorization': f'Bearer {token}'}


def register(client, email, name):
    response = client.post('/api/auth/register', json={
        'email': email, 'password': PASSWORD, 'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def operator_with_mfa(app, client, role='admin'):
    account = register(client, f'{role}@example.com', role.title())
    with app.app_context():
        user = db.session.get(User, account['user']['id'])
        user.operator_role = role
        db.session.commit()
    setup = client.post('/api/auth/mfa/setup', json={
        'current_password': PASSWORD,
    }, headers=auth(account['token']))
    assert setup.status_code == 200, setup.get_json()
    secret = setup.get_json()['secret']
    enabled = client.post('/api/auth/mfa/enable', json={
        'code': _totp_at(secret, time.time()),
    }, headers=auth(account['token']))
    assert enabled.status_code == 200, enabled.get_json()
    return enabled.get_json()['token'], secret, account['user']['id']


def mfa(secret):
    return _totp_at(secret, time.time())


def test_feedback_and_reports_are_durable_operator_queue_items(app, client):
    player = register(client, 'player@example.com', 'Player')
    target = register(client, 'target@example.com', 'Target')
    feedback = client.post('/api/feedback', json={
        'message': 'The report flow needs a clearer outcome.',
        'context': '#player/2',
    }, headers=auth(player['token']))
    assert feedback.status_code == 200
    assert feedback.get_json()['feedback_id']
    report = client.post(f"/api/users/{target['user']['id']}/report", json={
        'reason': 'Repeated abusive messages',
    }, headers=auth(player['token']))
    assert report.status_code == 200

    assert client.get('/api/admin/moderation/queue', headers=auth(player['token'])).status_code == 403
    token, _, _ = operator_with_mfa(app, client, 'reviewer')
    queue = client.get(
        '/api/admin/moderation/queue?status=open&limit=1', headers=auth(token),
    )
    assert queue.status_code == 200, queue.get_json()
    body = queue.get_json()
    assert len(body['items']) == 1
    assert body['has_more'] is True
    assert body['next_cursor']
    next_page = client.get(
        f"/api/admin/moderation/queue?status=open&limit=10&cursor={body['next_cursor']}",
        headers=auth(token),
    )
    assert next_page.status_code == 200
    assert {body['items'][0]['kind'], *(item['kind'] for item in next_page.get_json()['items'])} == {
        'feedback', 'user_report',
    }
    with app.app_context():
        stored = db.session.get(PlayerFeedback, feedback.get_json()['feedback_id'])
        assert stored.message.startswith('The report flow')
        assert stored.context == '#player/2'


def test_admin_can_resolve_suspend_and_restore_with_audited_mfa(app, client):
    reporter = register(client, 'reporter@example.com', 'Reporter')
    target = register(client, 'unsafe@example.com', 'Unsafe')
    client.post(f"/api/users/{target['user']['id']}/report", json={
        'reason': 'Unsafe conduct at a court',
    }, headers=auth(reporter['token']))
    token, secret, _ = operator_with_mfa(app, client, 'admin')
    queue = client.get(
        '/api/admin/moderation/queue?kind=reports', headers=auth(token),
    ).get_json()
    report_id = queue['items'][0]['id']

    missing_mfa = client.patch(
        f'/api/admin/moderation/reports/{report_id}',
        json={'status': 'resolved', 'action': 'suspend', 'outcome': 'Seven-day safety suspension'},
        headers=auth(token),
    )
    assert missing_mfa.status_code == 403
    resolved = client.patch(
        f'/api/admin/moderation/reports/{report_id}',
        json={
            'status': 'resolved', 'action': 'suspend',
            'outcome': 'Seven-day safety suspension', 'mfa_code': mfa(secret),
        }, headers=auth(token),
    )
    assert resolved.status_code == 200, resolved.get_json()
    assert resolved.get_json()['status'] == 'resolved'
    assert client.get('/api/me', headers=auth(target['token'])).status_code == 403
    blocked_login = client.post('/api/auth/login', json={
        'email': 'unsafe@example.com', 'password': PASSWORD,
    })
    assert blocked_login.status_code == 403
    assert blocked_login.get_json()['error'] == 'account_suspended'

    restored = client.post(
        f"/api/admin/moderation/users/{target['user']['id']}/restore",
        json={'reason': 'Appeal approved', 'mfa_code': mfa(secret)},
        headers=auth(token),
    )
    assert restored.status_code == 200, restored.get_json()
    assert client.post('/api/auth/login', json={
        'email': 'unsafe@example.com', 'password': PASSWORD,
    }).status_code == 200
    actions = client.get('/api/admin/moderation/actions', headers=auth(token))
    assert actions.status_code == 200
    assert {'suspend', 'restore'} <= {item['action'] for item in actions.get_json()['items']}


def test_reviewers_can_remove_unsafe_court_content_with_audit(app, client):
    owner = register(client, 'author@example.com', 'Author')
    token, secret, _ = operator_with_mfa(app, client, 'reviewer')
    with app.app_context():
        photo = CourtPhoto(
            court_id=1, user_id=owner['user']['id'], photo_data='data:image/png;base64,AA==',
        )
        review = CourtReview(
            court_id=1, user_id=owner['user']['id'], rating=1, comment='unsafe content',
        )
        db.session.add_all([photo, review])
        db.session.flush()
        db.session.add(CourtPhotoLike(user_id=owner['user']['id'], photo_id=photo.id))
        photo_id, review_id = photo.id, review.id
        db.session.commit()

    removed_photo = client.delete(
        f'/api/admin/moderation/court-photos/{photo_id}',
        json={'reason': 'Privacy violation', 'mfa_code': mfa(secret)},
        headers=auth(token),
    )
    assert removed_photo.status_code == 204
    removed_review = client.delete(
        f'/api/admin/moderation/court-reviews/{review_id}',
        json={'reason': 'Harassment', 'mfa_code': mfa(secret)},
        headers=auth(token),
    )
    assert removed_review.status_code == 204
    with app.app_context():
        assert db.session.get(CourtPhoto, photo_id) is None
        assert db.session.get(CourtReview, review_id) is None
        assert CourtPhotoLike.query.filter_by(photo_id=photo_id).count() == 0


def test_players_can_report_visible_content_and_evidence_survives_removal(app, client):
    reporter = register(client, 'content-reporter@example.com', 'Reporter')
    author = register(client, 'content-author@example.com', 'Author')
    outsider = register(client, 'content-outsider@example.com', 'Outsider')
    with app.app_context():
        message = Message(
            sender_id=author['user']['id'], recipient_id=reporter['user']['id'],
            body='A threatening direct message',
        )
        photo = CourtPhoto(
            court_id=1, user_id=author['user']['id'],
            photo_data='data:image/png;base64,AA==', caption='Private information',
        )
        review = CourtReview(
            court_id=1, user_id=author['user']['id'], rating=1,
            comment='Harassing review text',
        )
        db.session.add_all([message, photo, review])
        db.session.commit()
        message_id, photo_id, review_id = message.id, photo.id, review.id

    hidden_message = client.post('/api/reports/content', json={
        'content_type': 'message', 'content_id': message_id,
        'reason': 'Threats or unsafe conduct',
    }, headers=auth(outsider['token']))
    assert hidden_message.status_code == 403

    ids = {}
    for content_type, content_id in (
        ('message', message_id),
        ('court_photo', photo_id),
        ('court_review', review_id),
    ):
        response = client.post('/api/reports/content', json={
            'content_type': content_type,
            'content_id': content_id,
            'reason': 'Privacy violation',
            'details': f'Context for {content_type}',
        }, headers=auth(reporter['token']))
        assert response.status_code == 201, response.get_json()
        ids[content_type] = response.get_json()['report_id']

    duplicate = client.post('/api/reports/content', json={
        'content_type': 'message', 'content_id': message_id,
        'reason': 'Privacy violation',
    }, headers=auth(reporter['token']))
    assert duplicate.status_code == 200
    assert duplicate.get_json()['report_id'] == ids['message']
    self_report = client.post('/api/reports/content', json={
        'content_type': 'court_photo', 'content_id': photo_id,
        'reason': 'Privacy violation',
    }, headers=auth(author['token']))
    assert self_report.status_code == 400
    assert self_report.get_json()['error'] == 'cannot_report_self'

    token, secret, _ = operator_with_mfa(app, client, 'reviewer')
    queue = client.get(
        '/api/admin/moderation/queue?kind=reports&status=open',
        headers=auth(token),
    )
    assert queue.status_code == 200, queue.get_json()
    by_type = {item['content_type']: item for item in queue.get_json()['items']}
    assert {'message', 'court_photo', 'court_review'} <= set(by_type)
    assert 'A threatening direct message' in by_type['message']['content_snapshot']
    assert by_type['court_photo']['details'] == 'Context for court_photo'

    removed = client.patch(
        f"/api/admin/moderation/reports/{ids['message']}",
        json={
            'status': 'resolved', 'action': 'remove_content',
            'outcome': 'The reported message was removed.',
            'mfa_code': mfa(secret),
        }, headers=auth(token),
    )
    assert removed.status_code == 200, removed.get_json()
    with app.app_context():
        assert db.session.get(Message, message_id) is None
        report = db.session.get(UserReport, ids['message'])
        assert 'A threatening direct message' in report.content_snapshot
        assert ModerationAction.query.filter_by(
            user_report_id=report.id, action='remove_content',
        ).count() == 1
        reporter_updates = Notification.query.filter_by(
            user_id=reporter['user']['id'], kind='safety_report_update',
        ).all()
        author_notices = Notification.query.filter_by(
            user_id=author['user']['id'], kind='safety_notice',
        ).all()
        assert reporter_updates and 'removed' in reporter_updates[-1].body.lower()
        assert author_notices and 'removed' in author_notices[-1].title.lower()
