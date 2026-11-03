"""Replaceability tests use local adapter doubles, never a real new provider."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch

import pytest

from app.flight_providers import FlightCapabilities, FlightGateway, get_flight_provider
from app.flight_source import FlightSource, FlightSourceError
from app.live_chat import LiveConversation
from test_live_sources import live_app
from test_flight_source import payload


def offer(**changes):
    row={'id':'NEW_1','origin_code':'BAK','destination_code':'IST','departure_local':'2099-11-06T14:20:00+04:00',
         'price_azn':'245.50','currency':'AZN','airline':'TK','source_url':'https://booking.example.test/offer/1',
         'fetched_at':'2026-11-03T00:00:00+00:00','stops':0,'duration_min':180}
    row.update(changes)
    return row


class NewAdapter:
    capabilities=FlightCapabilities('new_source','A reviewed source (test double)',
        'live_offer',('booking.example.test',),supports_baggage=True,supports_seats=True)
    def configured(self):return True
    def search(self,args):return [offer(baggage_kg=20,seats_available=3),offer(id='NEW_2',price_azn='299.00',baggage_kg=15)]


ARGS={'origin':'BAK','destination':'IST','departure_date':date(2099,11,6)}


def test_new_adapter_returns_multiple_live_offers_with_one_gateway():
    gateway=FlightGateway(NewAdapter())
    rows=gateway.search(ARGS)
    assert len(rows)==2 and rows[0]['provider_id']=='new_source'
    assert rows[0]['price_kind']=='live_offer' and rows[0]['data_mode']=='api_live'
    assert rows[0]['baggage_kg']=='20' and rows[0]['seats_available']==3
    assert rows[1]['seats_available'] is None
    assert all(r['can_reserve'] is False and r['source_url_verified'] for r in rows)


def test_provider_is_injected_without_chat_modifications(live_app):
    c,sid,store,_,_=live_app
    old=store.chat;store.chat=LiveConversation(store,old.reference,old.planner,flight_provider=FlightGateway(NewAdapter()))
    with patch.object(store.chat.planner,'plan',side_effect=AssertionError('Simple route should remain local')):
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Bakıdan İstanbula 6 noyabr 2099 üçün uçuşları göstər'}).json()
    assert not result.get('error'),result
    assert len(result['results'][0]['items'])==2
    assert 'keş' not in result['message'] and 'Aviasales' not in result['message']


@pytest.mark.parametrize('change',[
    {'currency':'USD'}, {'price_azn':'NaN'}, {'price_azn':'-5'},
    {'departure_local':'2099-11-06T14:20:00'}, {'origin_code':'LON'},
    {'departure_local':'2099-11-07T14:20:00+04:00'},
    {'source_url':'http://booking.example.test/offer'},
    {'source_url':'https://evil.test/offer'},
    {'source_url':'https://user:secret@booking.example.test/offer'},
    {'source_url':'https://booking.example.test:444/offer'},
    {'baggage_kg':-1}, {'seats_available':-1},
])
def test_gateway_rejects_invalid_new_provider_outputs(change):
    adapter=NewAdapter();adapter.search=lambda args:[offer(**change)]
    with pytest.raises(FlightSourceError,match='FLIGHT_CONTRACT'):FlightGateway(adapter).search(ARGS)


def test_gateway_strips_unrecognized_secrets_and_unadvertised_inventory():
    adapter=NewAdapter()
    adapter.capabilities=FlightCapabilities('new_source','Source','cached_search_price',('booking.example.test',))
    adapter.search=lambda args:[offer(private_token='never-render',baggage_kg=20,seats_available=4)]
    row=FlightGateway(adapter).search(ARGS)[0]
    assert 'private_token' not in row and 'never-render' not in str(row)
    assert row['baggage_kg'] is None and row['seats_available'] is None


def test_gateway_reapplies_filters_and_sort():
    gateway=FlightGateway(NewAdapter())
    assert len(gateway.search({**ARGS,'max_price':250}))==1
    assert gateway.search({**ARGS,'descending':True})[0]['id']=='NEW_2'


def test_unknown_provider_never_silently_falls_back(monkeypatch):
    monkeypatch.setenv('SUPERCHAT_FLIGHT_PROVIDER','not-installed')
    with pytest.raises(FlightSourceError,match='FLIGHT_PROVIDER'):get_flight_provider()


def test_current_provider_remains_default():
    assert get_flight_provider().metadata()['provider']=='travelpayouts'


def test_no_month_support_never_silently_uses_exact_date():
    with pytest.raises(FlightSourceError,match='FLIGHT_CAPABILITY'):
        FlightGateway(NewAdapter()).search({'origin':'BAK','destination':'IST','departure_month':'2099-11'})


def monthly_payload():
    data=payload()
    second=deepcopy(data['data'][0]);second['departure_at']='2099-11-20T07:00:00+04:00';second['price']=153
    outside=deepcopy(second);outside['departure_at']='2099-12-01T07:00:00+04:00'
    data['data'].extend([second,outside])
    return data


def test_month_query_has_distinct_dates_and_never_includes_other_month(monkeypatch):
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    with patch.object(FlightSource,'fetch',return_value=monthly_payload()) as fetch:
        rows=get_flight_provider().search({'origin':'BAK','destination':'IST','departure_month':'2099-11'})
    params=fetch.call_args.args[0]
    assert params['departure_at']=='2099-11' and params['limit']==31 and params['one_way']=='true'
    assert len(rows)==2 and {r['departure_local'][:10] for r in rows}=={'2099-11-06','2099-11-20'}
    assert rows[0]['price_azn']=='153.00'


def test_exact_date_is_never_broadened(monkeypatch):
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    with patch.object(FlightSource,'fetch',return_value=monthly_payload()):
        rows=get_flight_provider().search(ARGS)
    assert len(rows)==1 and rows[0]['departure_local'].startswith('2099-11-06')


@pytest.mark.parametrize('message',['Bakıdan İstanbula noyabr 2099 ayında uçuşları göstər',
                                   'Show flights from Baku to Istanbul in November 2099'])
def test_explicit_month_chat_labels_different_dates(live_app,monkeypatch,message):
    c,sid,store,_,_=live_app
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    with patch.object(FlightSource,'fetch',return_value=monthly_payload()), patch.object(store.chat.planner,'plan',side_effect=AssertionError('Month query should resolve locally')):
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':message}).json()
    assert not result.get('error'),result
    assert len(result['results'][0]['items'])==2
    assert 'Tarixlər fərqlidir' in result['message'] or 'Departure dates differ' in result['message']


def test_date_and_month_cannot_be_requested_together(live_app):
    engine=live_app[2].chat
    from app.services.catalog_service import DomainError
    with pytest.raises(DomainError):
        engine.validate('search_flights',{'origin':'BAK','destination':'IST','departure_date':'2099-11-06','departure_month':'2099-11'})



def test_provider_switch_does_not_reuse_other_source_records(live_app,monkeypatch):
    c,sid,store,_,_=live_app
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    with patch.object(FlightSource,'fetch',return_value=payload()):
        first=c.post(f'/api/sessions/{sid}/chat',json={'message':'Bakıdan İstanbula 6 noyabr 2099 üçün uçuşları göstər'}).json()
    assert first['results'][0]['items'][0]['provider_id']=='travelpayouts'
    old=store.chat;store.chat=LiveConversation(store,old.reference,old.planner,flight_provider=FlightGateway(NewAdapter()))
    result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Ən ucuzunun detalları'}).json()
    assert not result['results'] and 'boşdur' in result['message']


def test_unsupported_month_has_explicit_message(live_app):
    c,sid,store,_,_=live_app
    old=store.chat;store.chat=LiveConversation(store,old.reference,old.planner,flight_provider=FlightGateway(NewAdapter()))
    result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Bakıdan İstanbula noyabr 2099 ayında uçuşları göstər'}).json()
    assert result['error'] and result['message'].startswith('FLIGHT_CAPABILITY:')
    assert 'Dəqiq tarix' in result['message']
