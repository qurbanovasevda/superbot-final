"""Mocked LLM integration tests. These do not measure live model quality."""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm_chat import Action, LLMConversation, GeminiPlanner, Plan, PlannerError
from app.main import create_app


class FakePlanner:
    def __init__(self, plans):
        self.plans=iter(plans)
        self.inputs=[]

    def plan(self,payload):
        self.inputs.append(json.loads(json.dumps(payload)))
        value=next(self.plans)
        if isinstance(value,Exception):raise value
        return value


def plan(tool=None,args=None,category='Shopping',clarification=''):
    return Plan(category=category,clarification=clarification,
                actions=[] if tool is None else [Action(tool=tool,arguments=json.dumps(args or {}))])


@pytest.fixture
def env(tmp_path):
    # Rules are explicitly selected so existing local environment variables do not
    # cause unintended live API traffic in the test suite.
    with TestClient(create_app(Settings(database_path=tmp_path/'llm.sqlite3',chat_mode='rules'))) as c:
        sid=c.post('/api/sessions').json()['session_id']
        state=c.app.state.store
        yield c,sid,state


def attach(env,plans):
    c,sid,state=env
    fake=FakePlanner(plans)
    state.chat=LLMConversation(state,Settings().reference_date,fake)
    def send(text):
        r=c.post(f'/api/sessions/{sid}/chat',json={'message':text})
        assert r.status_code==200,r.text
        return r.json()
    return send,fake


def test_semantic_plan_executes_directly_and_sends_context(env):
    send,fake=attach(env,[plan('search_products',{'category':'computers','sort':'rating','descending':True}),
                          plan('details',{'domain':'products','record_id':'P003'},category='Follow-up')])
    result=send('I need a computer for my university work; which one has better reviews?')
    assert [r['id'] for r in result['results'][0]['items']]==['P003','P004']
    assert send('Tell me more about the first one')['results'][0]['items'][0]['id']=='P003'
    assert fake.inputs[1]['domain_state']['Shopping']['ids']==['P003','P004']
    assert fake.inputs[1]['reference_date']=='2026-11-02'


def test_mixed_actions_have_independent_constraints(env):
    p=Plan(category='Mixed',clarification='',actions=[
        Action(tool='search_flights',arguments=json.dumps({'destination':'Istanbul','departure_date':'2026-11-06','max_price':500})),
        Action(tool='search_products',arguments=json.dumps({'query':'headphone','max_price':100}))])
    send,_=attach(env,[p])
    result=send('Find a flight Friday and some affordable headphones')
    assert len(result['results'])==2
    assert all(part['items'] for part in result['results'])


def test_reservation_requires_confirmation_once(env):
    c,sid,state=env
    send,_=attach(env,[plan('reserve',{'domain':'flights','record_id':'F001','quantity':2},category='Flights'),
                      plan(clarification='What would you like next?')])
    assert 'Confirm' in send('Book the flight for two')['message']
    assert state.reservations(sid)==[]
    assert 'confirmed' in send('Confirm')['message']
    assert len(state.reservations(sid))==1
    send('Confirm')
    assert len(state.reservations(sid))==1


def test_cart_cancel_and_commit(env):
    _,sid,state=env
    proposal=plan('set_cart',{'product_id':'P001','quantity':2})
    send,_=attach(env,[proposal,proposal])
    send('Add two headphones')
    send('Cancel')
    assert state.cart(sid)['items']==[]
    send('Add two headphones')
    send('Yes')
    assert state.cart(sid)['items'][0]['quantity']==2


def test_invalid_tool_arguments_do_not_mutate(env):
    _,sid,state=env
    send,_=attach(env,[plan('set_cart',{'product_id':'P001','quantity':-1}),
                      plan('search_products',{'unknown_filter':'x'}),
                      plan('reserve',{'domain':'flights','record_id':'F999','quantity':1})])
    for text in ('one','two','three'):
        assert send(text)['error']
    assert state.reservations(sid)==[]
    assert state.cart(sid)['items']==[]


def test_llm_outage_is_explicit_not_rules_fallback(env):
    send,_=attach(env,[PlannerError('Cannot reach the language model.')])
    result=send('Show me laptops')
    assert result['error'] and result['engine']=='llm'
    assert result['results']==[]


def test_clarification_and_history(env):
    send,fake=attach(env,[plan(category='Ambiguous',clarification='Where would you like to fly?'),
                        plan(category='Ambiguous',clarification='Which date?'),
                        plan('search_flights',{'destination':'Istanbul','departure_date':'2026-11-06'},category='Follow-up')])
    send('I want to travel')
    send('Istanbul')
    assert send('Friday')['results'][0]['items']
    assert len(fake.inputs[-1]['history'])==4


def test_no_key_is_controlled():
    p=GeminiPlanner(Settings(gemini_api_key=''))
    with pytest.raises(PlannerError,match='GEMINI_API_KEY'):
        p.plan({})


def test_structured_output_wire_format():
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):
            return json.dumps({'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':plan(clarification='Hello').model_dump_json()}]}}]}).encode()
    planner=GeminiPlanner(Settings(gemini_api_key='test-placeholder'))
    with patch('app.llm_chat.urlopen',return_value=Response()) as call:
        assert planner.plan({'user_message':'Hello'}).clarification=='Hello'
    request=call.call_args.args[0]
    payload=json.loads(request.data)
    assert payload['generationConfig']['responseMimeType']=='application/json'
    assert payload['generationConfig']['responseJsonSchema']['type']=='object'
    assert 'responseFormat' not in payload['generationConfig']
    assert request.full_url.startswith('https://generativelanguage.googleapis.com/')
    assert 'test-placeholder' not in request.full_url
    assert 'test-placeholder' not in json.dumps(payload)


def test_mixed_write_is_rejected(env):
    _,sid,state=env
    p=Plan(category='Mixed',clarification='',actions=[
        Action(tool='reserve',arguments=json.dumps({'domain':'flights','record_id':'F001','quantity':1})),
        Action(tool='search_products',arguments='{}')])
    send,_=attach(env,[p])
    assert send('Book and search')['error']
    assert state.reservations(sid)==[]


def test_llm_mode_reports_missing_key_without_fallback(tmp_path):
    with TestClient(create_app(Settings(database_path=tmp_path/'missing.sqlite3',chat_mode='llm',gemini_api_key=''))) as c:
        health=c.get('/health').json()
        assert health['chat_mode']=='llm' and health['llm_configured'] is False
        sid=c.post('/api/sessions').json()['session_id']
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Show laptops'}).json()
        assert result['error'] and 'GEMINI_API_KEY' in result['message']


def test_app_llm_wiring_with_injected_provider(tmp_path):
    with TestClient(create_app(Settings(database_path=tmp_path/'wire.sqlite3',chat_mode='llm',gemini_api_key='test-placeholder'))) as c:
        sid=c.post('/api/sessions').json()['session_id']
        with patch.object(c.app.state.store.chat.planner,'plan',return_value=plan('search_products',{'category':'computers'})):
            result=c.post(f'/api/sessions/{sid}/chat',json={'message':'I need a machine for coding'}).json()
        assert result['engine']=='llm'
        assert len(result['results'][0]['items'])==2


def test_gemini_quota_error_never_calls_another_provider():
    from urllib.error import HTTPError
    planner=GeminiPlanner(Settings(gemini_api_key='test-placeholder'))
    with patch('app.llm_chat.urlopen',side_effect=HTTPError('https://generativelanguage.googleapis.com',429,'quota',{},None)) as call:
        with pytest.raises(PlannerError,match='No paid fallback'):
            planner.plan({'user_message':'Hello'})
    assert call.call_count==1


def test_gemini_blocked_response_is_controlled():
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):return b'{"promptFeedback":{"blockReason":"SAFETY"}}'
    with patch('app.llm_chat.urlopen',return_value=Response()):
        with pytest.raises(PlannerError,match='declined'):
            GeminiPlanner(Settings(gemini_api_key='test-placeholder')).plan({})


def test_gemini_config_ignores_old_openai_key(monkeypatch, tmp_path):
    # Isolate this check from the user's real .env without reading its secrets.
    monkeypatch.setattr("app.config.ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setenv('OPENAI_API_KEY','old-placeholder')
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    settings=Settings()
    assert settings.gemini_api_key==''
    assert settings.gemini_model=='gemini-3.5-flash-lite'
    assert not hasattr(settings,'openai_api_key')


def test_final_answer_uses_executed_results(env):
    from app.llm_chat import GroundedAnswer
    send,fake=attach(env,[plan('search_products',{'category':'computers'})])
    received=[]
    def respond(payload):
        received.append(payload)
        return GroundedAnswer(message='The Laptop 15" Pro has the higher rating among these options.',record_ids=['P003'])
    fake.respond=respond
    result=send('Dərslərim üçün hansı noutbuk daha yaxşıdır?')
    assert result['message'].startswith('The Laptop')
    assert received[0]['user_message'].startswith('Dərslərim')
    assert len(received[0]['executed_results'][0]['items'])==2
    assert result['results'][0]['items'][0]['category']=='computers'


def test_answer_generation_failure_preserves_results(env):
    send,fake=attach(env,[plan('search_products',{'query':'headphone'})])
    def respond(payload):raise PlannerError('quota')
    fake.respond=respond
    result=send('Find headphones')
    assert result['results'][0]['items']
    assert 'verified results' in result['message']
    assert not result.get('error')


def test_hallucinated_answer_is_rejected(env):
    from app.llm_chat import GroundedAnswer
    send,fake=attach(env,[plan('search_products',{'query':'headphone'})])
    fake.respond=lambda _:GroundedAnswer(message='P999 costs 7777 AZN.',record_ids=['P999'])
    result=send('Find headphones')
    assert 'P999' not in result['message']
    assert result['results'][0]['items'][0]['id']=='P001'


def test_unsupported_price_is_rejected(env):
    from app.llm_chat import GroundedAnswer
    send,fake=attach(env,[plan('search_products',{'query':'headphone'})])
    fake.respond=lambda _:GroundedAnswer(message='P001 costs 7777 AZN.',record_ids=['P001'])
    assert '7777' not in send('Find headphones')['message']


@pytest.mark.parametrize('reason,code', [
    (__import__('socket').gaierror(-2,'private detail'),'GEMINI_DNS'),
    (__import__('ssl').SSLCertVerificationError(1,'private detail'),'GEMINI_TLS_CERT'),
    (TimeoutError('private detail'),'GEMINI_TIMEOUT'),
    (ConnectionResetError('private detail'),'GEMINI_CONNECTION'),
])
def test_network_errors_have_safe_diagnostic_codes(reason,code):
    from urllib.error import URLError
    with patch('app.llm_chat.urlopen',side_effect=URLError(reason)):
        with pytest.raises(PlannerError,match=code) as result:
            GeminiPlanner(Settings(gemini_api_key='test-placeholder')).plan({})
    assert 'private detail' not in str(result.value)
    assert 'test-placeholder' not in str(result.value)


def test_google_400_detail_is_visible_but_key_is_redacted():
    from io import BytesIO
    from urllib.error import HTTPError
    key='AIzaTESTSECRETKEYEXAMPLE'
    error=json.dumps({'error':{'status':'INVALID_ARGUMENT','message':'Unknown field responseFormat. Key '+key}}).encode()
    failure=HTTPError('https://generativelanguage.googleapis.com',400,'bad request',{},BytesIO(error))
    with patch('app.llm_chat.urlopen',side_effect=failure):
        with pytest.raises(PlannerError) as result:
            GeminiPlanner(Settings(gemini_api_key=key)).plan({})
    assert 'INVALID_ARGUMENT' in str(result.value)
    assert 'Unknown field' in str(result.value)
    assert key not in str(result.value)


def test_non_json_google_400_stays_controlled():
    from io import BytesIO
    from urllib.error import HTTPError
    failure=HTTPError('https://generativelanguage.googleapis.com',400,'bad request',{},BytesIO(b'<html>not JSON</html>'))
    with patch('app.llm_chat.urlopen',side_effect=failure):
        with pytest.raises(PlannerError,match='GEMINI_HTTP_400'):
            GeminiPlanner(Settings(gemini_api_key='test-placeholder')).plan({})


def test_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv('GEMINI_TIMEOUT_SECONDS','100')
    assert GeminiPlanner(Settings()).timeout==100
    monkeypatch.setenv('GEMINI_TIMEOUT_SECONDS','bad')
    assert GeminiPlanner(Settings()).timeout==90


def test_history_omits_duplicate_result_payloads():
    history=[{'role':'assistant','message':'Found results','results':[{'items':['x'*10000]}]}]
    compact=LLMConversation.compact_history(history)
    assert compact==[{'role':'assistant','content':'Found results'}]


def test_exact_duration_conversion_is_allowed():
    from app.llm_chat import GroundedAnswer
    evidence=[{'items':[{'id':'F001','duration_min':215,'price_azn':'289.00'}]}]
    answer=GroundedAnswer(message='F001 takes 3 hours 35 minutes and costs 289 AZN.',record_ids=['F001'])
    LLMConversation.validate_answer(answer,evidence)
    with pytest.raises(ValueError):
        LLMConversation.validate_answer(GroundedAnswer(message='F001 takes 3 hours 34 minutes.',record_ids=['F001']),evidence)


def test_flight_writer_failure_shows_useful_summary_and_reason(env):
    send,fake=attach(env,[plan('search_flights',{'origin':'Baku','destination':'Istanbul','departure_date':'2026-11-06'},category='Flights')])
    def respond(_):raise PlannerError('GEMINI_TIMEOUT: no answer')
    fake.respond=respond
    result=send('Flights from Baku to Istanbul on Friday')
    assert '175' in result['message'] and 'GEMINI_TIMEOUT' in result['message']
    assert len(result['results'][0]['items'])==3


def test_single_call_mode_never_calls_answer_endpoint(env):
    from app.llm_chat import TurnPlan, GroundedAnswer
    p=TurnPlan(category='Shopping',clarification='',actions=[Action(tool='search_products',arguments='{"category":"computers"}')],draft_answer=GroundedAnswer(message='P003 has a rating of 4.6.',record_ids=['P003']))
    send,fake=attach(env,[p])
    fake.single_call=True
    def forbidden(_):raise AssertionError('Second API call must not happen')
    fake.respond=forbidden
    result=send('Which laptop has better reviews?')
    assert result['message']=='P003 has a rating of 4.6.'
    assert len(fake.inputs)==1


def test_single_call_invalid_draft_preserves_cards(env):
    from app.llm_chat import TurnPlan, GroundedAnswer
    p=TurnPlan(category='Shopping',clarification='',actions=[Action(tool='search_products',arguments='{"category":"computers"}')],draft_answer=GroundedAnswer(message='P999 costs 9999 AZN.',record_ids=['P999']))
    send,fake=attach(env,[p]);fake.single_call=True
    result=send('Show computers')
    assert 'P999' not in result['message']
    assert len(result['results'][0]['items'])==2


def test_airline_sort_available_in_api_and_llm(env):
    c,_,_=env
    args={'destination':'Istanbul','departure_date':'2026-11-06','sort':'airline'}
    api=c.get('/api/flights',params=args)
    assert api.status_code==200
    send,_=attach(env,[plan('search_flights',args,category='Flights')])
    rows=send('Sort Friday Istanbul flights by airline')['results'][0]['items']
    assert [r['id'] for r in rows]==[r['id'] for r in api.json()]
    names=[r['airline'].casefold() for r in rows]
    assert names==sorted(names)


@pytest.mark.parametrize('domain,record_id', [('flights','F001'),('showtimes','S001')])
def test_current_seats_in_details_and_comparison(env,domain,record_id):
    c,sid,state=env
    before=state.availability(domain,record_id)['seats_available']
    send,_=attach(env,[plan('reserve',{'domain':domain,'record_id':record_id,'quantity':2}),
                      plan('details',{'domain':domain,'record_id':record_id})])
    send('Reserve two seats')
    send('Confirm')
    assert send('Show details')['results'][0]['items'][0]['seats_available']==before-2
    assert c.get(f'/api/{domain}/{record_id}').json()['seats_available']==before-2
    if domain=='flights':
        rows=c.post('/api/flights/compare',json={'ids':['F001','F002']}).json()
        assert rows[0]['seats_available']==before-2
        send,_=attach(env,[plan('compare',{'domain':'flights','ids':['F001','F002']})])
        assert send('Compare both')['results'][0]['items'][0]['seats_available']==before-2


def test_preferences_survive_short_history_and_engine_reload(env):
    from app.llm_chat import TurnPlan
    _,sid,state=env
    statement='I always prefer direct flights'
    preference=TurnPlan(category='Flights',clarification='Preference remembered.',actions=[],
                        remembered_preferences=[statement,'invented preference'])
    send,_=attach(env,[preference]+[plan(clarification='Hello') for _ in range(6)])
    send(statement)
    for _ in range(6):send('Hello')
    send,fake=attach(env,[plan(clarification='Where would you like to fly?')])
    send('Flights please')
    assert fake.inputs[0]['shared_preferences']==[statement]
    assert statement not in json.dumps(fake.inputs[0]['history'])
    assert state.chat.load(sid)['preferences']['statements']==[statement]
    other=env[0].post('/api/sessions').json()['session_id']
    assert state.chat.load(other).get('preferences', {})=={}


def localized_plan(tool=None,args=None,language='az',**kwargs):
    from app.llm_chat import TurnPlan
    return TurnPlan(**plan(tool,args,**kwargs).model_dump(),response_language=language)


def test_azerbaijani_confirmation_is_local_and_persists_language(env):
    send,fake=attach(env,[localized_plan('reserve',{'domain':'flights','record_id':'F001','quantity':2})])
    proposed=send('F001 üçün iki yer rezerv et')
    assert 'təsdiqləyin' in proposed['message']
    assert proposed['response_language']=='az'
    confirmed=send('bəli')
    assert 'təsdiqləndi' in confirmed['message'] and 'AZN' in confirmed['message']
    assert len(fake.inputs)==0  # Explicit ID and confirmation are both local.
    _,sid,state=env
    assert state.chat.load(sid)['language']=='az'


def test_russian_cart_cancel_uses_same_language_without_model(env):
    send,fake=attach(env,[localized_plan('set_cart',{'product_id':'P001','quantity':1},language='ru')])
    assert 'Подтвердите' in send('Добавь наушники в корзину')['message']
    assert 'Отменено' in send('отмена')['message']
    assert len(fake.inputs)==1
    assert env[2].cart(env[1])['items']==[]


def test_language_switch_and_fallback_summaries(env):
    send,fake=attach(env,[localized_plan('search_products',{'category':'computers'},language='az'),
                         localized_plan('search_products',{'category':'computers'},language='ru'),
                         localized_plan('search_products',{'category':'computers'},language='en')])
    assert 'uyğun nəticə' in send('Noutbuk göstər')['message']
    assert 'Найдено' in send('Теперь по-русски')['message']
    assert fake.inputs[1]['response_language']=='ru'
    assert 'Found' in send('Now answer in English')['message']
    assert fake.inputs[2]['response_language']=='en'


def test_azerbaijani_single_call_answer_is_preserved(env):
    from app.llm_chat import GroundedAnswer
    p=localized_plan('search_products',{'query':'headphone'})
    p.draft_answer=GroundedAnswer(message='P001 qulaqlığının qiyməti 89.90 AZN-dir.',record_ids=['P001'])
    send,fake=attach(env,[p]);fake.single_call=True
    assert send('Qulaqlıq neçəyədir?')['message']==p.draft_answer.message
    assert len(fake.inputs)==1


def test_localized_flight_fallback_and_provider_error(env):
    send,fake=attach(env,[localized_plan('search_flights',{'destination':'Istanbul','departure_date':'2026-11-06'}),
                       PlannerError('GEMINI_TIMEOUT: provider did not respond')])
    fake.single_call=True
    response=send('Cümə günü İstanbula uçuşlar')
    assert 'uçuş tapıldı' in response['message'] and 'baqaj' in response['message']
    assert 'Found' not in response['message']
    error=send('Davam et')
    assert 'GEMINI_TIMEOUT' in error['message'] and 'mümkün olmadı' in error['message']
    assert error['response_language']=='az'


def test_cheapest_baggage_uses_saved_direct_flights_without_model(env):
    send,fake=attach(env,[localized_plan('search_flights',{'origin':'Baku','destination':'Istanbul','departure_date':'2026-11-06','max_stops':0},category='Flights')])
    send('Cümə günü Bakıdan İstanbula birbaşa uçuşları göstər')
    result=send('Ən ucuzunun baqajı neçə kilodur?')
    assert result['category']=='Follow-up'
    assert result['results'][0]['items'][0]['id']=='F002'
    assert '15 kq' in result['message'] and '245.00 AZN' in result['message']
    assert 'tarix' not in result['message']
    assert len(fake.inputs)==1
    again=send('neticeleri goster')
    assert {i['id'] for i in again['results'][0]['items']}=={'F001','F002'}
    assert again['response_language']=='az'


def test_baggage_reference_returns_to_flights_after_cinema(env):
    send,fake=attach(env,[localized_plan('search_flights',{'destination':'Istanbul','departure_date':'2026-11-06'}),
                         localized_plan('search_movies',{'genre':'sci-fi'},category='Cinema')])
    send('Flights to Istanbul Friday')
    send('Show sci-fi films')
    result=send('Ən ucuzunun baqajı neçə kilodur?')
    assert result['results'][0]['items'][0]['id']=='F013'
    assert '15 kq' in result['message']
    assert len(fake.inputs)==2
    corrected=send('bileti nezerde tuturdum sinema yox')
    assert corrected['results'][0]['domain']=='Flights'
    assert len(fake.inputs)==2


def test_general_baggage_never_requires_route_or_date(env):
    send,fake=attach(env,[])
    result=send('ümumiyyətlə baqajı soruşuram')
    assert 'Baqaj norması' in result['message']
    assert 'baqaj haqqı göstərilmir' in result['message']
    assert 'gediş tarix' not in result['message']
    assert result['results']==[]
    assert fake.inputs==[]


def test_baggage_new_route_and_mixed_request_still_use_planner(env):
    send,fake=attach(env,[localized_plan(clarification='Hansı tarixdə?'),
                        localized_plan(clarification='Əlavə məlumat verin.')])
    send('Bakıdan İstanbula uçuşun baqajı nə qədərdir?')
    send('Ən ucuzunun baqajını və qulaqlıq qiymətlərini göstər')
    assert len(fake.inputs)==2


def test_baggage_does_not_arbitrarily_select_from_multiple_flights(env):
    send,fake=attach(env,[localized_plan('search_flights',{'destination':'Istanbul','departure_date':'2026-11-06'})])
    send('Flights to Istanbul Friday')
    response=send('Baqaj neçə kilodur?')
    assert response['category']=='Ambiguous' and response['results']==[]
    assert 'Hansı uçuşu' in response['message']
    assert len(fake.inputs)==1


def test_explicit_baggage_id_and_missing_fees_are_grounded(env):
    send,fake=attach(env,[])
    result=send('F001 baqaj qiyməti nə qədərdir?')
    assert '20 kq' in result['message']
    assert 'Ayrıca baqaj haqqı məlumatlarda yoxdur' in result['message']
    assert '289.00 AZN' in result['message']
    assert fake.inputs==[]



def test_ordinal_baggage_uses_original_result_order(env):
    send,fake=attach(env,[localized_plan('search_flights',{'destination':'Istanbul','departure_date':'2026-11-06'})])
    send('Flights to Istanbul Friday')
    result=send('İkincisinin baqajı nə qədərdir?')
    assert result['results'][0]['items'][0]['id']=='F002'
    assert len(fake.inputs)==1


@pytest.mark.parametrize('domain,record_id', [('flights','F001'),('showtimes','S001')])
def test_rest_search_inventory_matches_current_availability(env,domain,record_id):
    c,sid,state=env
    before=state.availability(domain,record_id)['seats_available']
    state.reserve(sid,domain,record_id,2,'search-inventory-check')
    rows=c.get('/api/'+domain).json()
    rows=[row['showtime'] if domain=='showtimes' else row for row in rows]
    found=next(row for row in rows if row['id']==record_id)
    assert found['seats_available']==before-2
    assert found['seats_available']==c.get(f'/api/{domain}/{record_id}/availability').json()['seats_available']


def test_fluent_draft_cannot_hide_missing_route_or_execution_error(env):
    from app.llm_chat import GroundedAnswer
    missing=localized_plan('search_flights',{},language='en')
    missing.draft_answer=GroundedAnswer(message='Here is your flight.',record_ids=[])
    invalid=localized_plan('details',{'domain':'flights','record_id':'F999'},language='en')
    invalid.draft_answer=GroundedAnswer(message='Everything looks good.',record_ids=[])
    send,fake=attach(env,[missing,invalid]);fake.single_call=True
    assert 'Which destination' in send('Find a flight')['message']
    assert 'Record not found' in send('Details F999')['message']


def test_detail_preserves_original_shortlist_for_next_reference(env):
    send,fake=attach(env,[localized_plan('search_products',{'category':'computers'}),
                         localized_plan('details',{'domain':'products','record_id':'P003'}),
                         localized_plan('details',{'domain':'products','record_id':'P004'})])
    search=send('Show laptops')['results'][0]['items']
    send('Details P003')
    send('What about the second one?')
    ctx=fake.inputs[2]['domain_state']['Shopping']
    assert ctx['search_ids']==[item['id'] for item in search]
    assert ctx['selected']=='P003'


def test_partial_flight_slots_survive_friday_then_origin_without_model(env):
    send,fake=attach(env,[])
    first=send('İstanbula uçmaq istəyirəm')
    assert 'gediş tarixini' in first['message']
    assert 'istiqamət' not in first['message']
    friday=send('Cümə günü')
    assert {r['id'] for r in friday['results'][0]['items']}=={'F001','F002','F013'}
    origin=send('Bakıdan')
    assert {r['id'] for r in origin['results'][0]['items']}=={'F001','F002','F013'}
    assert fake.inputs==[]
    _,sid,state=env
    filters=state.chat.load(sid)['domains']['Flights']['filters']
    assert filters['destination']=='Istanbul' and filters['origin']=='Baku'
    assert filters['departure_date']=='2026-11-06'
    assert 'max_stops' not in filters


@pytest.mark.parametrize('query',['qulaqlıq','qulaqliq','наушники'])
def test_multilingual_product_search_finds_official_names(env,query):
    send,_=attach(env,[localized_plan('search_products',{'query':query,'max_price':100})])
    items=send('100 AZN-dən ucuz qulaqlıq göstər')['results'][0]['items']
    assert any(r['id']=='P001' for r in items)
    assert all(float(r['price_azn'])<=100 for r in items)


def test_availability_selects_showtime_then_zero_capacity_blocks_booking(env):
    send,fake=attach(env,[])
    result=send('S003 seansında neçə boş yer var?')
    assert '0 boş yer' in result['message']
    result=send('İki bilet rezerv et')
    assert 'Yalnız 0 boş yer' in result['message']
    assert env[2].reservations(env[1])==[]
    assert fake.inputs==[]


def test_explicit_flight_booking_overrides_cinema_without_losing_quantity(env):
    send,fake=attach(env,[])
    send('S003 seansında neçə boş yer var?')
    result=send('F002 üçün iki yer rezerv et')
    assert 'F002' in result['message'] and 'Say: 2' in result['message']
    assert env[2].reservations(env[1])==[]
    send('xeyr')
    assert env[2].reservations(env[1])==[]
    assert fake.inputs==[]


def test_generic_good_request_does_not_reuse_previous_laptop_search(env):
    send,fake=attach(env,[localized_plan('search_products',{'category':'computers'})])
    send('Noutbukları göstər')
    result=send('Yaxşı bir şey göstər')
    assert result['category']=='Ambiguous' and result['results']==[]
    assert len(fake.inputs)==1


def test_current_message_overrides_stale_model_language_and_draft(env):
    from app.llm_chat import GroundedAnswer
    first=localized_plan('search_products',{'category':'computers'},language='az')
    stale=localized_plan('search_products',{'category':'computers'},language='az')
    stale.draft_answer=GroundedAnswer(message='İki uyğun noutbuk göstərilir.',record_ids=['P003','P004'])
    send,fake=attach(env,[first,stale,stale]);fake.single_call=True
    send('Noutbuk göstər')
    english=send('Show me laptops please')
    assert english['response_language']=='en'
    assert english['message'].startswith('Found')
    russian=send('Покажи ноутбуки')
    assert russian['response_language']=='ru'
    assert russian['message'].startswith('Найдено')
    assert [p['response_language'] for p in fake.inputs]==['az','en','ru']


def test_local_baggage_reply_switches_from_az_to_english_and_russian(env):
    send,fake=attach(env,[localized_plan('search_flights',{'destination':'Istanbul','departure_date':'2026-11-06'})])
    send('Cümə günü Bakıdan İstanbula uçuşları göstər')
    en=send('How much baggage does the cheapest flight include?')
    assert en['response_language']=='en' and 'baggage allowance' in en['message']
    ru=send('Сколько багажа у самого дешёвого?')
    assert ru['response_language']=='ru' and 'норма багажа' in ru['message']
    assert len(fake.inputs)==1


def test_local_booking_uses_current_language_and_neutral_yes_keeps_it(env):
    send,_=attach(env,[localized_plan('search_products',{'category':'computers'})])
    send('Noutbukları göstər')
    proposal=send('Reserve two seats on F002 please')
    assert proposal['response_language']=='en' and 'Please confirm' in proposal['message']
    confirmed=send('yes')
    assert confirmed['response_language']=='en' and 'confirmed' in confirmed['message']


@pytest.mark.parametrize('message,expected',[
    ('How much is it?','en'),('Show the results','en'),('Reserve F002','en'),
    ('Покажи результаты','ru'),('İngilis dilində cavab ver','en'),
    ('Ответь на английском','en'),('İndi azərbaycanca de','az'),
    ('yes',None),('F002',None),('asdfgh 123',None),
])
def test_language_hints_use_words_and_explicit_requests(message,expected):
    assert LLMConversation.detect_language(message)==expected


@pytest.mark.parametrize('message',['privet','bolsoy privet','bolshoy privet','kaqdela','kakdela','kak dela','Привет','Как дела?'])
def test_russian_smalltalk_never_repeats_old_flights_or_mutates_state(env,message):
    send,fake=attach(env,[localized_plan('search_flights',{'origin':'Baku','destination':'Istanbul','departure_date':'2026-11-06','max_stops':0})])
    send('Cümə günü Bakıdan İstanbula birbaşa uçuşları göstər')
    _,sid,state=env
    before=state.chat.load(sid)['domains']
    reply=send(message)
    assert reply['results']==[]
    assert reply['response_language']=='ru'
    assert any(c in reply['message'] for c in 'ПриветСпасибо')
    assert state.chat.load(sid)['domains']==before
    assert state.chat.load(sid)['active']=='Flights'
    assert state.reservations(sid)==[] and state.cart(sid)['items']==[]
    assert len(fake.inputs)==1


def test_smalltalk_changes_language_without_losing_previous_search(env):
    send,fake=attach(env,[localized_plan('search_flights',{'destination':'Istanbul','departure_date':'2026-11-06'})])
    send('Cümə günü Bakıdan İstanbula uçuşları göstər')
    assert send('privet')['response_language']=='ru'
    en=send('how are you')
    assert en['response_language']=='en' and 'doing well' in en['message']
    assert send('Salam')['response_language']=='az'
    assert '15 kq' in send('Ən ucuzunun baqajı neçə kilodur?')['message']
    assert len(fake.inputs)==1


def test_greeting_with_search_is_not_swallowed_by_smalltalk(env):
    send,fake=attach(env,[localized_plan('search_products',{'category':'computers'},language='en')])
    result=send('Hi, show me laptops please')
    assert result['results'][0]['items']
    assert len(fake.inputs)==1


def test_transliterated_headphones_exclude_speakers_and_keep_price_followup(env):
    send,fake=attach(env,[localized_plan('search_products',{'category':'audio','sort':'price','descending':True})])
    first=send('men isteyirem high price nausnik')
    items=first['results'][0]['items']
    assert [p['id'] for p in items]==['P001','P002']
    higher=send('greather than 100')
    assert higher['results'][0]['items']==[]
    assert '59.00' in higher['message'] and '89.90' in higher['message']
    assert higher['response_language']=='en'
    assert len(fake.inputs)==1
    _,sid,state=env
    assert state.chat.load(sid)['domains']['Shopping']['filters']['query']=='nausnik'


def test_expensive_headphone_search_does_not_invent_above_100_option(env):
    send,_=attach(env,[localized_plan('search_products',{'category':'audio','min_price':100})])
    result=send('price greather than 100 nausnik')
    assert result['results'][0]['items']==[]
    assert '89.90' in result['message'] and 'No matching' in result['message']
    assert 'P008' not in result['message']


def test_headphone_filter_adjustment_discards_stale_speaker_draft(env):
    from app.llm_chat import GroundedAnswer
    p=localized_plan('search_products',{'category':'audio'})
    p.draft_answer=GroundedAnswer(message='P008 is a headphone.',record_ids=['P008'])
    send,fake=attach(env,[p]);fake.single_call=True
    result=send('nausnik goster')
    assert {p['id'] for p in result['results'][0]['items']}=={'P001','P002'}
    assert 'P008' not in result['message']
