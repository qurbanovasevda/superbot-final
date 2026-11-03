"""Check reduced planner payloads and narrow safe local handling."""
from datetime import date
import json
from unittest.mock import patch

import pytest

from app.config import Settings
from app.flight_source import FlightSource
from app.live_chat import compact_live_catalog, compact_live_domains, obvious_keyboard_input
from app.llm_chat import GeminiPlanner, Plan, TurnPlan, PlannerError
from test_live_sources import live_app
from test_flight_source import payload


@pytest.mark.parametrize('message',['asdfgh qwerty','qwerty','lkjhgfdsa','zxcvbnm','!!!'])
def test_keyboard_noise_is_locally_clarified(live_app,message):
    c,sid,store,_,_=live_app
    with patch.object(store.chat.planner,'plan',side_effect=AssertionError('No network for keyboard noise')):
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':message}).json()
    assert not result.get('error') and result['category']=='Ambiguous'
    assert result['results']==[]


@pytest.mark.parametrize('text',['what is qwerty','show qwerty keyboard','TF_1234','1500-1800','headphones','Flights from Baku to Istanbul'])
def test_noise_rule_does_not_swallow_normal_requests(text):assert not obvious_keyboard_input(text)


def test_simple_mixed_request_uses_backend_reads_without_gemini(live_app,monkeypatch):
    c,sid,store,_,_=live_app
    monkeypatch.setenv('TRAVELPAYOUTS_TOKEN','fake-test-token-123')
    with patch.object(FlightSource,'fetch',return_value=payload()) as api,patch.object(store.chat.planner,'plan',side_effect=AssertionError('Both clauses are explicit reads')):
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Find laptops and flights from Baku to Istanbul on 2099-11-06'}).json()
    assert not result.get('error'),result
    assert result['category']=='Mixed' and api.call_count==1
    assert {p['domain'] for p in result['results']}=={'Shopping','Flights'}


def test_compact_context_preserves_ids_and_state_without_spec_duplication():
    products=[{'id':f'BM_{i}','title':'Laptop HP','price_azn':'1299.00',
               'specifications':{f'feature_{j}':'x'*300 for j in range(30)},
               'source_url':'https://birmarket.az/product/123'} for i in range(12)]
    data={'products':products,'showtimes':[],'sources':{}}
    original=json.dumps(data)
    compact=compact_live_catalog(data,[],{'configured':True})
    assert [r['id'] for r in compact['products']]==[r['id'] for r in products]
    assert len(json.dumps(compact))<len(original)/5
    assert products[0]['specifications']  # Do not mutate or lose the underlying evidence.
    domains={'Shopping':{'kind':'products','filters':{'query':'laptop'},'selected':'BM_1','search_ids':['BM_1','BM_2'],'recent_results':products}}
    projected=compact_live_domains(domains)
    assert projected['Shopping']['search_ids']==['BM_1','BM_2'] and projected['Shopping']['selected']=='BM_1'
    assert 'recent_results' not in projected['Shopping']
    assert domains['Shopping']['recent_results']


def test_phase2_model_only_routes_while_phase1_keeps_draft_support():
    planner=GeminiPlanner(Settings(gemini_api_key='test-placeholder'))
    with patch.object(planner,'generate',return_value=Plan(category='Ambiguous',clarification='Please clarify',actions=[])) as generate:
        planner.plan({'data_mode':'scraped'})
        assert generate.call_args.args[1] is Plan
        assert 'Do not draft' in generate.call_args.args[2]
    with patch.object(planner,'generate',return_value=TurnPlan(category='Ambiguous',clarification='Please clarify',actions=[])) as generate:
        planner.plan({'data_mode':'mock'})
        assert generate.call_args.args[1] is TurnPlan


def test_planner_failures_keep_a_safe_code_in_az(live_app,caplog):
    c,sid,store,_,_=live_app
    with patch.object(store.chat.planner,'plan',side_effect=PlannerError('Gemini returned an invalid action. Please rephrase.')):
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Mənə fərqli seçimlər təklif et'}).json()
    assert result['error'] and result['message'].startswith('GEMINI_RESPONSE:')
    assert 'GEMINI_RESPONSE' in caplog.text
