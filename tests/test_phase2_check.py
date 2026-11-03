"""Check that the acceptance runner detects bad output without real API calls."""
from datetime import date
from unittest.mock import patch

import pytest

from check_phase2 import check_response, run_checks
from app.llm_chat import TurnPlan,Action
from app.flight_source import FlightSource
from test_live_sources import live_app
from test_flight_source import payload


def body(row=None):
    return {'message':'Source results','response_language':'en','results':[{'domain':'Shopping','items':[] if row is None else [row]}]}


def test_valid_source_response_is_accepted():
    row={'id':'BM_1','price_azn':'100.00','currency':'AZN','source_url':'https://birmarket.az/product/1','data_mode':'scraped','can_reserve':False}
    assert check_response(body(row),'Shopping','en')==[]


@pytest.mark.parametrize('change,issue',[
    ({'id':'P001'},'mock_id_in_phase2'),
    ({'currency':'USD'},'unconfirmed_azn'),
    ({'price_azn':'NaN'},'invalid_price'),
    ({'source_url':'javascript:alert(1)'},'missing_or_unsafe_source_link'),
    ({'can_reserve':True},'real_booking_enabled'),
])
def test_bad_records_do_not_get_a_smoke_pass(change,issue):
    row={'id':'BM_1','price_azn':'100','currency':'AZN','source_url':'https://birmarket.az/product/1','data_mode':'scraped'}
    row.update(change)
    assert issue in check_response(body(row))


def test_wrong_language_and_old_result_replay_are_detected():
    row={'id':'BM_1','source_url':'https://birmarket.az/product/1','data_mode':'scraped'}
    issues=check_response(body(row),language='az',no_cards=True)
    assert 'wrong_language' in issues and 'unexpected_old_results' in issues


def test_phase1_is_blocked_before_any_test_session():
    calls=[]
    def requester(base,path,data=None):
        calls.append(path)
        return {'phase':1} if path=='/health' else {}
    with pytest.raises(ValueError,match='Phase 2'):
        run_checks('http://localhost:8000',date(2099,11,6),requester)
    assert '/api/sessions' not in calls


def test_runner_exercises_local_app_and_reports_coverage_warnings(live_app,monkeypatch):
    c,_,store,_,_=live_app
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    def requester(base,path,data=None):
        response=c.get(path) if data is None else c.post(path,json=data)
        assert response.status_code<400
        return response.json()
    def planner(context):
        message=context['user_message']
        if message.startswith('Find laptops and flights'):
            return TurnPlan(category='Mixed',clarification='',actions=[
                Action(tool='search_products',arguments='{"query":"laptop"}'),
                Action(tool='search_flights',arguments='{"origin":"BAK","destination":"IST","departure_date":"2099-11-06"}')])
        return TurnPlan(category='Out of scope' if message.startswith('Explain') else 'Ambiguous',
            clarification='I can help with flights, shopping and cinema only.',actions=[])
    with patch.object(FlightSource,'fetch',return_value=payload()),patch.object(store.chat.planner,'plan',side_effect=planner):
        result=run_checks('http://localhost:8001',date(2099,11,6),requester)
    assert result['summary']['completed']==14
    assert result['summary']['failed']==0
    assert result['summary']['warnings']>=1  # Tomorrow is absent from the fixture; not proof of no shows.
    assert any(t['check']=='Month flight calendar' for t in result['turns'])



def test_backend_failure_stops_early_instead_of_consuming_more_quota():
    calls=[]
    def requester(base,path,data=None):
        calls.append(path)
        if path=='/health':return {'phase':2,'llm_configured':True}
        if path=='/api/live/status':return {'flight_api':{'configured':True}}
        if path=='/api/sessions':return {'session_id':'test-session'}
        return {'error':True,'message':'Source failed','results':[],'response_language':'en'}
    result=run_checks('http://localhost:8001',date(2099,11,6),requester)
    assert result['summary']['completed']==1 and result['summary']['stopped_early']
    assert result['summary']['failed']==1
    assert sum('/chat' in path for path in calls)==1
