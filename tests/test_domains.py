from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(database_path=tmp_path / "test.sqlite3"))) as client:
        yield client


def session(client):
    return client.post('/api/sessions').json()['session_id']


def test_flight_filters_and_comparison(client):
    f = client.get('/api/flights/F001').json()
    rows = client.get('/api/flights', params={'origin': f['origin']['code'], 'destination': f['destination']['city'], 'departure_date': f['departure_local'][:10], 'max_price': f['price_azn']}).json()
    assert any(r['id'] == 'F001' for r in rows)
    assert all(r['origin']['code'] == f['origin']['code'] for r in rows)
    assert client.post('/api/flights/compare', json={'ids':['F001','F002']}).status_code == 200
    assert client.post('/api/flights/compare', json={'ids':['F001','F001']}).status_code == 400
    assert client.get('/api/flights/missing').status_code == 404


def test_products_filters_and_sort(client):
    rows = client.get('/api/products', params={'in_stock':True, 'sort':'price', 'max_price':100}).json()
    prices = [Decimal(r['price_azn']) for r in rows]
    assert prices == sorted(prices)
    assert all(r['stock'] > 0 and Decimal(r['price_azn']) <= 100 for r in rows)
    p = client.get('/api/products/P001').json()
    result = client.get('/api/products', params={'brand':p['brand'], 'color':p['color'], 'category':p['category']}).json()
    assert any(x['id']=='P001' for x in result)
    assert client.get('/api/products/P002/stock').json()['in_stock'] is False


def test_cinema_joins(client):
    show = client.get('/api/showtimes/S001').json()
    rows = client.get('/api/showtimes', params={'movie_id':show['movie_id'], 'cinema_id':show['cinema_id'], 'on_date':show['date']}).json()
    assert rows and all(r['movie']['id']==show['movie_id'] for r in rows)
    assert any(r['id']==show['movie_id'] for r in client.get('/api/movies',params={'on_date':show['date']}).json())
    assert len(client.get('/api/cinemas').json()) == 3


def test_cart_totals_isolation_and_limits(client):
    a,b = session(client),session(client)
    url = f'/api/sessions/{a}/cart/P001'
    cart = client.put(url,json={'quantity':2}).json()
    assert Decimal(cart['total_azn']) == Decimal('179.80')
    assert client.get(f'/api/sessions/{b}/cart').json()['items'] == []
    assert client.put(url,json={'quantity':999999}).status_code == 409
    assert client.put(url,json={'quantity':-1}).status_code == 422
    assert client.put(f'/api/sessions/{a}/cart/P002',json={'quantity':1}).status_code == 409
    assert client.delete(url).json()['items'] == []
    assert client.get('/api/sessions/unknown/cart').status_code == 404


@pytest.mark.parametrize('domain,record', [('flights','F001'),('showtimes','S001')])
def test_reservation_idempotency_and_capacity(client,domain,record):
    a,b = session(client),session(client)
    capacity = client.get(f'/api/{domain}/{record}/availability').json()['seats_available']
    payload = dict(domain=domain,record_id=record,quantity=capacity,request_key='once')
    url = f'/api/sessions/{a}/reservations'
    first = client.post(url,json=payload)
    assert first.status_code == 201
    assert client.post(url,json=payload).json() == first.json()
    payload['quantity']=capacity+1
    assert client.post(url,json=payload).status_code == 409
    payload.update(quantity=1,request_key='other')
    assert client.post(f'/api/sessions/{b}/reservations',json=payload).status_code == 409
    assert len(client.get(url).json()) == 1
    assert client.get(f'/api/{domain}/{record}/availability').json()['seats_available'] == 0


def test_sold_out_and_input_validation(client):
    url = f'/api/sessions/{session(client)}/reservations'
    payload = dict(domain='showtimes',record_id='S003',quantity=1,request_key='sold')
    assert client.post(url,json=payload).status_code == 409
    payload['quantity']=0
    assert client.post(url,json=payload).status_code == 422
    assert client.get('/api/flights',params={'sort':'unknown'}).status_code == 422
    assert client.get('/api/movies',params={'min_rating':11}).status_code == 422


def test_concurrent_booking_cannot_oversell(client):
    capacity = client.get('/api/showtimes/S001/availability').json()['seats_available']
    sessions = [session(client), session(client)]
    def book(sid):
        return client.post(f'/api/sessions/{sid}/reservations',json=dict(domain='showtimes',record_id='S001',quantity=capacity,request_key='all')).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(book,sessions)) == [201,409]


def test_persistence(tmp_path):
    settings=Settings(database_path=tmp_path/'persistent.sqlite3')
    with TestClient(create_app(settings)) as c:
        sid=session(c)
        c.put(f'/api/sessions/{sid}/cart/P001',json={'quantity':1})
    with TestClient(create_app(settings)) as c:
        assert len(c.get(f'/api/sessions/{sid}/cart').json()['items']) == 1
