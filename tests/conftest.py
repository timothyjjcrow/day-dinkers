import pytest
from backend.app import create_app, db


@pytest.fixture
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(client):
    """Register a user and return auth headers."""
    res = client.post('/api/auth/register', json={
        'username': 'testuser', 'email': 'test@example.com',
        'password': 'password123', 'name': 'Test User',
    })
    token = res.get_json()['token']
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


@pytest.fixture
def sample_court(app):
    """Create a sample court for testing."""
    from backend.models import Court
    court = Court(
        name='Test Court', address='123 Test St, CA',
        latitude=34.0522, longitude=-118.2437,
        indoor=False, lighted=True, num_courts=4,
        surface_type='Concrete', fees='Free',
    )
    db.session.add(court)
    db.session.commit()
    return court
