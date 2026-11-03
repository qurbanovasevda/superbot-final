"""Provider requests are mocked; no account/key is needed for this suite."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from app.flight_source import FlightSource, FlightSourceError, NoRedirect, iata, source_link
from test_live_sources import live_app, live_plan

DAY=date(2099,11,6)


def payload():
    return {'success':True,'currency':'azn','data':[{
        'origin':'BAK','destination':'IST','price':245.50,'airline':'J2','flight_number':'123',
        'departure_at':'2099-11-06T14:20:00+04:00','transfers':0,'duration':180,
        'link':'/search/BAK0611IST1?test=fixture'}]}


def parse(data,args=None):return FlightSource.parse(data,'BAK','IST',DAY,args or {})


@pytest.mark.parametrize('name,code',[('Bakı','BAK'),('BAK','BAK'),('Istanbul','IST'),('Стамбул','IST'),('GYD','GYD')])
def test_city_or_airport_code(name,code):assert iata(name)==code


def test_unknown_city_requires_code():
    with pytest.raises(FlightSourceError,match='FLIGHT_LOCATION'):iata('a city not mapped')


def test_cached_row_has_provenance_and_no_invented_baggage_or_seats():
    row=parse(payload())[0]
    assert row['price_azn']=='245.50' and row['currency']=='AZN'
    assert row['price_kind']=='cached_search_price' and row['data_mode']=='api_cache'
    assert row['baggage_kg'] is None and row['seats_available'] is None
    assert row['id'].startswith('TF_') and row['source_url'].startswith('https://www.aviasales.com/search/')


@pytest.mark.parametrize('currency',['usd','rub',None])
def test_unconfirmed_azn_is_never_relabelled(currency):
    data=payload();data['currency']=currency
    with pytest.raises(FlightSourceError,match='FLIGHT_CURRENCY'):parse(data)


@pytest.mark.parametrize('changes',[
    {'price':-1},{'price':'NaN'},{'price':'Infinity'},
    {'origin':'LON'},{'destination':'DXB'}, {'departure_at':'2099-11-07T14:20:00+04:00'},
    {'departure_at':'2099-11-06T14:20:00'}, {'currency':'usd'},
    {'link':'https://evil.test/search/'},{'link':'//evil.test/search/'},
    {'expires_at':'2000-01-01T00:00:00+00:00'},
])
def test_bad_or_mismatched_offers_are_excluded(changes):
    data=payload();data['data'][0].update(changes)
    assert parse(data)==[]


def test_empty_provider_cache_is_valid():assert parse({'success':True,'currency':'azn','data':[]})==[]


def test_filters_only_use_published_fields():
    data=payload()
    assert parse(data,{'max_price':Decimal('200')})==[]
    assert len(parse(data,{'max_stops':0}))==1
    data['data'][0]['transfers']=None
    assert parse(data,{'max_stops':0})==[]


def test_token_only_in_header_and_one_bounded_request(monkeypatch):
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):
            assert limit==2_000_001
            return json.dumps(payload()).encode()
    class Opener:
        def open(self,request,timeout):
            assert timeout==15
            assert 'fake-test-token-123' not in request.full_url and 'token=' not in request.full_url
            assert request.get_header('X-access-token')=='fake-test-token-123'
            assert 'currency=azn' in request.full_url
            return Response()
    with patch('app.flight_source.build_opener',return_value=Opener()) as build:
        result=FlightSource().search({'origin':'Baku','destination':'Istanbul','departure_date':DAY})
    assert len(result)==1 and build.call_count==1


def test_redirect_never_forwards_token():
    with pytest.raises(FlightSourceError,match='FLIGHT_REDIRECT'):
        NoRedirect().redirect_request(None,None,None,None,None,'https://evil.test')


def test_http_error_does_not_leak_key_or_provider_body(monkeypatch):
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    error=HTTPError('https://example.test/?token=SECRET',403,'SECRET',{},BytesIO(b'SECRET'))
    with patch('app.flight_source.build_opener') as build:
        build.return_value.open.side_effect=error
        with pytest.raises(FlightSourceError,match='FLIGHT_HTTP_403') as exc:FlightSource().fetch({})
    assert 'SECRET' not in str(exc.value) and 'fake-test-token' not in str(exc.value)


def test_provider_network_failure_does_not_fallback(monkeypatch):
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    with patch('app.flight_source.build_opener') as build:
        build.return_value.open.side_effect=URLError('private proxy details')
        with pytest.raises(FlightSourceError,match='FLIGHT_NETWORK'):FlightSource().fetch({})


def test_chat_cached_search_details_reservation_and_reload(live_app,monkeypatch):
    from app.live_chat import LiveConversation
    c,sid,store,_,_=live_app
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    with patch.object(store.chat.planner,'plan',return_value=live_plan('search_flights',{
        'origin':'Baku','destination':'Istanbul','departure_date':DAY.isoformat()})), patch.object(FlightSource,'fetch',return_value=payload()) as fetch:
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Flights from Baku to Istanbul'}).json()
    assert not result.get('error'),result
    assert 'cached prices' in result['message'] and fetch.call_count==1
    fid=result['results'][0]['items'][0]['id']
    engine=store.chat;store.chat=LiveConversation(store,engine.reference,engine.planner)
    for tool in ('details','availability','reserve'):
        args={'domain':'flights','record_id':fid}
        if tool=='reserve':args['quantity']=2
        with patch.object(store.chat.planner,'plan',return_value=live_plan(tool,args)), patch.object(FlightSource,'fetch',side_effect=AssertionError('No new request for details')):
            reply=c.post(f'/api/sessions/{sid}/chat',json={'message':'Show flight information'}).json()
        assert reply['results'][0]['items'][0]['id']==fid and not reply.get('error')
    assert store.reservations(sid)==[]
    assert 'No booking' in reply['message']


def test_chat_missing_slots_does_not_request_provider(live_app,monkeypatch):
    c,sid,store,_,_=live_app
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    with patch.object(store.chat.planner,'plan',return_value=live_plan('search_flights',{'destination':'Istanbul'})), patch.object(FlightSource,'fetch',side_effect=AssertionError('Missing origin/date')):
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Flights to Istanbul'}).json()
    assert not result['results'][0]['items'] and 'origin' in result['message']


def test_chat_provider_error_is_preserved_in_az(live_app,monkeypatch):
    c,sid,store,_,_=live_app
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    with patch.object(store.chat.planner,'plan',return_value=live_plan('search_flights',{'origin':'BAK','destination':'IST','departure_date':DAY.isoformat()})), patch.object(FlightSource,'fetch',side_effect=FlightSourceError('FLIGHT_HTTP_401: private')):
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Uçuşları göstər'}).json()
    assert result['error'] and result['message'].startswith('FLIGHT_HTTP_401:')
    assert 'private' not in result['message'] and result['results']==[]



@pytest.mark.parametrize('message',[
    'Bakıdan İstanbula 6 noyabr 2099 üçün uçuşları göstər',
    'Flights from Baku to Istanbul on 2099-11-06',
    'Show flights from Baku to Istanbul on November 6 2099'])
def test_explicit_flight_search_cannot_be_refused_by_planner(live_app,monkeypatch,message):
    c,sid,store,_,_=live_app
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    state=store.chat.load(sid)
    state['history']=[{'role':'assistant','message':'Uçuşların axtarışı hələ dəstəklənmir.'}]
    store.chat.save(sid,state)
    with patch.object(store.chat.planner,'plan',side_effect=AssertionError('Explicit route/date is a backend capability')), patch.object(FlightSource,'fetch',return_value=payload()) as fetch:
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':message}).json()
    assert not result.get('error'),result
    assert result['results'][0]['items'][0]['id'].startswith('TF_')
    assert fetch.call_count==1 and result['category']=='Flights'


def test_runtime_capabilities_override_scrape_status_for_semantic_queries(live_app,monkeypatch):
    c,sid,store,data,_=live_app
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    data['sources']['flights']={'status':'not_connected','count':0}
    with patch.object(store.chat.planner,'plan',return_value=live_plan('search_flights',{'origin':'BAK','destination':'IST','departure_date':DAY.isoformat()})) as planner, patch.object(FlightSource,'fetch',return_value=payload()):
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Help me arrange an affordable journey by plane'}).json()
    context=planner.call_args.args[0]
    assert context['catalog']['flights']==[]
    assert context['capabilities']['search_flights'] is True
    assert context['capabilities']['real_booking'] is False
    assert context['catalog']['source_status']['flights']['status']=='configured'
    assert result['results'][0]['items']


def test_local_flight_baggage_reference_does_not_invent_allowance(live_app,monkeypatch):
    c,sid,store,_,_=live_app
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    with patch.object(FlightSource,'fetch',return_value=payload()):
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Bakıdan İstanbula 6 noyabr 2099 üçün uçuşları göstər'}).json()
    with patch.object(store.chat.planner,'plan',side_effect=AssertionError('Reference should be local')):
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Ən ucuzunun baqajı neçə kilodur?'}).json()
    assert not result.get('error') and result['results'][0]['items'][0]['baggage_kg'] is None
    assert 'baqaj' in result['message']
