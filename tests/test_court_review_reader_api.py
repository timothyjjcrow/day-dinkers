"""The review reader can edit its author's older review without paging to it."""
from test_business_governance import app, client, auth, register


def test_own_review_is_independent_of_the_page_and_never_returned_to_other_visitors(client):
    owner=register(client,'reader-owner@example.test')
    mine=client.post('/api/courts/1/reviews',headers=auth(owner['token']),json={'rating':4,'comment':'Own older review'}).get_json()['review']
    for index in range(6):
        person=register(client,f'reader-{index}@example.test')
        assert client.post('/api/courts/1/reviews',headers=auth(person['token']),json={'rating':3,'comment':f'Other review {index}'}).status_code==201
    page=client.get('/api/courts/1/reviews?limit=5',headers=auth(owner['token'])).get_json()
    assert page['my_review']['id']==mine['id']
    assert all(row['id']!=mine['id'] for row in page['items'])
    assert page['has_more'] and page['rating_count']==7
    public=client.get('/api/courts/1/reviews?limit=5').get_json()
    assert public['my_review'] is None
    assert all(row['user_id'] is None and row['user_name']=='Player' for row in public['items'])
    visitor=register(client,'reader-no-review@example.test')
    assert client.get('/api/courts/1/reviews',headers=auth(visitor['token'])).get_json()['my_review'] is None
