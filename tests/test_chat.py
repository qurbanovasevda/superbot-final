import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings


@pytest.fixture
def chat(tmp_path):
    with TestClient(create_app(Settings(database_path=tmp_path/'chat.sqlite3'))) as c:
        sid=c.post('/api/sessions').json()['session_id']
        def send(message):
            r=c.post(f'/api/sessions/{sid}/chat',json={'message':message})
            assert r.status_code == 200, r.text
            return r.json()
        yield send,c,sid


def test_brief_switches(chat):
    send,_,_=chat
    for text,category in [('Find me a flight from Baku to Istanbul on Friday.','Flights'),('What is playing tonight?','Cinema'),('Do you have wireless headphones under 100 AZN?','Shopping')]:
        result=send(text)
        assert result['category']==category
        assert result['results'][0]['items']
    assert result['results'][0]['items'][0]['id']=='P001'


def test_brief_return_to_cinema(chat):
    send,_,_=chat
    send('What is playing tonight?')
    send('Show me laptops.')
    result=send('Okay, and the sci-fi one, any seats on Saturday?')
    assert result['category']=='Cinema'
    assert result['results'][0]['filters']['on_date']=='2026-11-07'
    assert result['results'][0]['items']
    assert all(x['movie_id'] in ('M001','M008') for x in result['results'][0]['items'])


def test_brief_second_product(chat):
    send,_,_=chat
    rows=send('Show me laptops.')['results'][0]['items']
    result=send('The second one, is it in stock?')
    assert result['category']=='Follow-up'
    assert result['results'][0]['items'][0]['id']==rows[1]['id']


def test_brief_cheaper_flight(chat):
    send,_,_=chat
    send('Flights to Istanbul on 6 November.')
    result=send('The cheaper one, is it direct?')
    assert result['category']=='Follow-up'
    assert 'Direct:' in result['message']


def test_brief_mixed(chat):
    send,_,_=chat
    result=send('I need a flight to Istanbul on 6 November and a backpack for the trip.')
    assert result['category']=='Mixed'
    assert len(result['results'])==2
    assert all(x['items'] for x in result['results'])


@pytest.mark.parametrize('text,category', [('Show me something good.','Ambiguous'),('What is the weather in Ganja?','Out of scope'),('Book two seats.','Ambiguous')])
def test_brief_fallbacks(chat,text,category):
    assert chat[0](text)['category']==category


def test_cart_booking_history_and_isolation(chat):
    send,c,sid=chat
    send('Show me headphones')
    assert '89.90' in send('Add the first one to cart')['message']
    send('Flights to Istanbul on Friday')
    result=send('Book F001 two seats')
    assert result['results'][0]['items'][0]['quantity']==2
    assert len(c.get(f'/api/sessions/{sid}/history').json())==8
    second=c.post('/api/sessions').json()['session_id']
    assert c.get(f'/api/sessions/{second}/history').json()==[]


def test_no_arbitrary_booking_and_invalid_date(chat):
    send,_,_=chat
    send('What is playing tonight?')
    assert 'select a specific' in send('Book two seats')['message']
    assert 'valid date' in send('Flights on 2026-11-99')['message']


def test_context_survives_restart(tmp_path):
    settings=Settings(database_path=tmp_path/'persist.sqlite3')
    with TestClient(create_app(settings)) as c:
        sid=c.post('/api/sessions').json()['session_id']
        c.post(f'/api/sessions/{sid}/chat',json={'message':'Show me laptops'})
    with TestClient(create_app(settings)) as c:
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'The second one, stock?'}).json()
        assert result['category']=='Follow-up'


def test_mixed_constraints_are_independent(chat):
    send,_,_=chat
    result=send('Find flights to Istanbul on Friday under 500 AZN and headphones under 100 AZN')
    assert result['category']=='Mixed'
    flight,product=result['results']
    assert flight['filters']['max_price']=='500'
    assert product['filters']['max_price']=='100'
    assert 'departure_date' not in product['filters']
    assert flight['items'] and product['items']


def test_explicit_id_works_without_prior_context(chat):
    send,_,_=chat
    assert send('Details P001')['results'][0]['items'][0]['id']=='P001'
    assert send('Compare F001 and F002')['results'][0]['items'][1]['id']=='F002'


def test_unknown_route_does_not_return_unrelated_flights(chat):
    result=chat[0]('Flights from Baku to Atlantis on Friday')
    assert result['results'][0]['items']==[]


def test_single_result_does_not_block_new_search(chat):
    send,_,_=chat
    send('Show me headphones')
    result=send('Show me laptops in stock')
    assert len(result['results'][0]['items'])==2


def test_invalid_position_asks_for_selection(chat):
    send,_,_=chat
    send('Show me headphones')
    assert 'Which result' in send('The fourth one, stock?')['message']


def test_different_dates_in_mixed_request(chat):
    result=chat[0]('Flights to Istanbul on Friday and movies playing on Saturday')
    assert result['results'][0]['filters']['departure_date']=='2026-11-06'
    assert result['results'][1]['filters']['on_date']=='2026-11-07'


def test_negative_quantity_is_not_booked(chat):
    send,c,sid=chat
    assert 'positive' in send('Book F001 -2 seats')['message']
    assert c.get(f'/api/sessions/{sid}/reservations').json()==[]


def test_root_interface(chat):
    _,c,_=chat
    r=c.get('/')
    assert r.status_code==200
    assert '<title>SuperChat</title>' in r.text


def test_flight_time_stops_and_sort(chat):
    send,_,_=chat
    result=send('Flights to Istanbul on Friday after 08:00 before 23:00 max 1 stop fastest')
    rows=result['results'][0]['items']
    assert rows
    assert all('08:00' <= r['departure_local'][11:16] <= '23:00' and r['stops']<=1 for r in rows)
    assert [r['duration_min'] for r in rows]==sorted(r['duration_min'] for r in rows)
    assert 'max_price' not in result['results'][0]['filters']


def test_product_rating_sort_and_reset(chat):
    send,_,_=chat
    send('Show me laptops')
    rows=send('Sort by rating')['results'][0]['items']
    assert rows and all(r['category']=='computers' for r in rows)
    assert [r['rating'] for r in rows]==sorted([r['rating'] for r in rows],reverse=True)
    result=send('Reset filters')
    assert result['results'][0]['filters']=={}
    assert len(result['results'][0]['items'])==12


def test_cinema_filter_and_list(chat):
    send,_,_=chat
    result=send('Showtimes at Ganjlik on Friday after 18:00 under 20 AZN')
    rows=result['results'][0]['items']
    assert rows
    assert all(r['cinema_id']=='C001' and r['time']>='18:00' and float(r['price_azn'])<=20 for r in rows)
    assert len(send('Show cinemas')['results'][0]['items'])==3


def test_movie_language_rating(chat):
    result=chat[0]('Movies in English rating at least 8')
    rows=result['results'][0]['items']
    assert rows and all(r['language']=='English' and r['rating']>=8 for r in rows)


def test_explicit_domain_overrides_filter_followup(chat):
    send,_,_=chat
    send('Show me laptops')
    assert send('Flights to Istanbul on Friday after 08:00')['category']=='Flights'


def test_missing_flight_fields_are_completed(chat):
    send,_,_=chat
    assert 'destination' in send('Find a flight')['message']
    assert 'departure date' in send('Istanbul')['message']
    result=send('Friday')
    assert result['category']=='Follow-up'
    assert result['results'][0]['items']
    assert all(r['destination']['city']=='Istanbul' for r in result['results'][0]['items'])


def test_pending_flight_survives_domain_switch(chat):
    send,_,_=chat
    send('Find a flight')
    send('Show me laptops')
    assert 'departure date' in send('Flights to Istanbul')['message']
    assert send('2026-11-06')['results'][0]['items']


def test_pending_does_not_capture_out_of_scope(chat):
    send,_,_=chat
    send('Find a flight')
    assert send('Weather in Ganja')['category']=='Out of scope'
    assert send('Show me headphones')['category']=='Shopping'


def test_weekday_followup(chat):
    send,_,_=chat
    send('Movies playing tonight')
    result=send('And Sunday?')
    assert result['category']=='Follow-up'
    assert result['results'][0]['filters']['on_date']=='2026-11-08'
