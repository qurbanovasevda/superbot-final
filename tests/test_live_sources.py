"""Offline parser and failure checks; live collection is a separate CLI action."""
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import create_app
from app.live_sources import Page, SourceError, allowed_url, robots_allowed, parse_product, parse_showtimes, collect, save_snapshot, load_snapshot

URL='https://birmarket.az/product/123-example'
CINEMA='https://cinemastercard.az/az/cinema/about-cinemaplus/azerbaijan-cinema/'


def product_html(**updates):
    row={'@type':'Product','sku':123,'name':'Example laptop','brand':{'name':'Example'},
         'offers':{'@type':'AggregateOffer','lowPrice':101.25,'highPrice':130,'priceCurrency':'AZN','availability':'https://schema.org/InStock'}}
    row.update(updates)
    return '<script type="application/ld+json">'+json.dumps(row)+'</script>'


def test_product_uses_offer_price_not_discount_or_installment_and_unknown_stock():
    result=parse_product('<p>Old price 500; monthly 5</p>'+product_html(),URL,'computers','2026-11-03T00:00:00+00:00')
    assert result['price_azn']=='101.25'
    assert result['stock'] is None and result['in_stock'] is True
    assert result['source_url']==URL and result['data_mode']=='scraped'
    assert result['can_reserve'] is False


@pytest.mark.parametrize('price',[None,-1,'NaN','Infinity','not a price'])
def test_missing_or_invalid_prices_are_not_invented(price):
    html=product_html(offers={'price':price,'priceCurrency':'AZN'})
    with pytest.raises(SourceError):parse_product(html,URL,'computers')


def test_unsupported_currency_never_silently_converts():
    with pytest.raises(SourceError,match='currency'):parse_product(product_html(offers={'price':50,'priceCurrency':'USD'}),URL,'computers')


def test_product_parser_detects_changed_structure():
    with pytest.raises(SourceError,match='JSON-LD'):parse_product('<h1>Challenge or changed page</h1>',URL,'computers')


def test_showtime_date_is_taken_from_row_not_today_or_reference_date():
    html='''<table><tr data-date="06.11.2026"><td><a href="/movie">Example Movie</a></td><td>19:30</td><td>Example Cinema</td><td>Hall 1</td><td>2D</td><td>8 AZN</td><td><div data-session="456"></div></td></tr></table>'''
    result=parse_showtimes(html+html,CINEMA)
    assert len(result)==1 and result[0]['date']=='2026-11-06'
    assert result[0]['time']=='19:30' and result[0]['price_azn']=='8.00'
    assert result[0]['seats_available'] is None and not result[0]['can_reserve']


def test_showtime_placeholders_do_not_become_free_tickets():
    with pytest.raises(SourceError):parse_showtimes('<p>One ticket: 0 AZN</p>',CINEMA)


def test_robot_globs_queries_specific_groups_and_allow_ties():
    rules='User-agent: *\nDisallow: /search\nDisallow: /*?*\nAllow: /*?page=*\n'
    assert robots_allowed(rules,'https://birmarket.az/categories/16-noutbuklar')
    assert robots_allowed(rules,'https://birmarket.az/categories/16-noutbuklar?page=2')
    assert not robots_allowed(rules,'https://birmarket.az/search?q=laptop')
    assert not robots_allowed(rules,'https://birmarket.az/categories/16?sort=price')
    own='User-agent: *\nDisallow: /\nUser-agent: SuperChatResearch\nAllow: /products\n'
    assert robots_allowed(own,'https://birmarket.az/products')
    assert robots_allowed('User-agent: *\nDisallow: /x\nAllow: /x\n','https://birmarket.az/x')


@pytest.mark.parametrize('url',['http://birmarket.az/','https://birmarket.az.evil.test/','https://127.0.0.1/','https://user:secret@birmarket.az/'])
def test_only_known_public_https_sources_are_fetchable(url):
    with pytest.raises(SourceError):allowed_url(url)


def test_source_failures_do_not_substitute_phase1_mock_records():
    class Failing:
        def get(self,url):raise SourceError('HTTP 403')
    result=collect(Failing(),1)
    assert result['products']==[] and result['showtimes']==[]
    assert all(v['count']==0 for v in result['sources'].values())
    assert result['sources']['flights']['status']=='not_connected'


def test_snapshot_roundtrip_is_separate_from_official_mock_files(tmp_path):
    path=tmp_path/'live_sources.json'
    save_snapshot({'products':[],'showtimes':[]},path)
    assert load_snapshot(path)=={'products':[],'showtimes':[]}
    assert not path.with_suffix('.tmp').exists()


def test_phase2_endpoints_do_not_claim_chat_is_switched(tmp_path,monkeypatch):
    snapshot={'fetched_at':'2026-11-03T00:00:00+00:00','products':[],'showtimes':[],'sources':{}}
    monkeypatch.setattr('app.live_sources.load_snapshot',lambda:snapshot)
    with TestClient(create_app(Settings(database_path=tmp_path/'test.sqlite3',chat_mode='rules'))) as client:
        response=client.get('/api/live/status').json()
        assert response['data_mode']=='scraped' and response['chat_connected'] is False
        assert client.get('/api/live/products').json()['items']==[]
        assert client.get('/health').json()['phase']==1


@pytest.fixture
def live_app(tmp_path,monkeypatch):
    from app.live_sources import parse_product, parse_showtimes
    data={'fetched_at':'2026-11-03T00:00:00+00:00','sources':{},
          'products':[parse_product(product_html(),URL,'computers')],
          'showtimes':parse_showtimes('<tr data-date="06.11.2026"><td>Movie A</td><td>19:30</td><td>Cinema A</td><td>Hall 1</td><td>2D</td><td>8 AZN</td><td><div data-session="456"></div></td></tr>',CINEMA)}
    for target in ('app.live_chat.load_snapshot','app.main.load_snapshot','app.live_sources.load_snapshot'):
        monkeypatch.setattr(target,lambda:data)
    path=tmp_path/'state.sqlite3'
    with TestClient(create_app(Settings(database_path=path,gemini_api_key='test-placeholder'),data_mode='live')) as client:
        sid=client.post('/api/sessions').json()['session_id']
        yield client,sid,client.app.state.store,data,path


def live_plan(tool,args):
    from app.llm_chat import TurnPlan,Action
    return TurnPlan(category='Shopping',clarification='',response_language='en',actions=[Action(tool=tool,arguments=json.dumps(args))])


def test_live_chat_uses_real_snapshot_not_phase1_catalog(live_app):
    from unittest.mock import patch
    c,sid,state,data,path=live_app
    with patch.object(state.chat.planner,'plan',return_value=live_plan('search_products',{'category':'computers'})) as call:
        body=c.post(f'/api/sessions/{sid}/chat',json={'message':'Find available computers'}).json()
    assert body['data_mode']=='scraped'
    assert [r['id'] for r in body['results'][0]['items']]==['BM_123']
    assert body['results'][0]['items'][0]['price_azn']=='101.25'
    assert call.call_args.args[0]['catalog']['flights']==[]
    assert not path.exists() and path.with_name('state-live.sqlite3').exists()
    assert c.get('/health').json()['phase']==2
    assert c.get('/api/live/status').json()['chat_connected'] is True


def test_live_routes_block_mock_purchases_and_mock_catalog(live_app):
    c,sid,_,_,_=live_app
    assert c.get('/api/flights').status_code==409
    assert c.get('/api/products').status_code==409
    assert c.put(f'/api/sessions/{sid}/cart/P001',json={'quantity':1}).status_code==409
    assert c.post(f'/api/sessions/{sid}/reservations',json={'domain':'flights','record_id':'F001','quantity':1,'request_key':'x'}).status_code==409


def test_live_chat_reservation_returns_source_link_without_booking(live_app):
    from unittest.mock import patch
    c,sid,state,_,_=live_app
    with patch.object(state.chat.planner,'plan',return_value=live_plan('reserve',{'domain':'showtimes','record_id':'CP_456','quantity':2})):
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Book two tickets'}).json()
    assert 'No transaction' in result['message']
    assert result['results'][0]['items'][0]['source_url']==CINEMA
    assert state.reservations(sid)==[]


def test_live_flight_search_never_returns_mock_fares(live_app):
    from unittest.mock import patch
    c,sid,state,_,_=live_app
    with patch.object(state.chat.planner,'plan',return_value=live_plan('search_flights',{'destination':'Istanbul','departure_date':'2026-11-06'})):
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Flights to Istanbul Friday'}).json()
    assert 'not connected' in result['message']
    assert all('price_azn' not in r for r in result['results'][0]['items'])
    assert result['results'][0]['items'][0]['source']=='Aviasales'


def test_public_http_client_uses_urllib_request_and_checks_policy_first():
    from app.live_sources import PublicClient
    from email.message import Message
    class Response:
        status=200
        headers=Message()
        def __init__(self,body):self.body=body
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):return self.body
    class Opener:
        def __init__(self):self.urls=[]
        def open(self,request,timeout):
            self.urls.append(request.full_url)
            assert request.get_header('User-agent')=='SuperChatResearch/0.1'
            return Response(b'User-agent: *\nAllow: /\n' if request.full_url.endswith('/robots.txt') else b'<html>ok</html>')
    client=PublicClient(interval=0);client.opener=Opener()
    assert client.get(URL)=='<html>ok</html>'
    assert client.opener.urls==['https://birmarket.az/robots.txt',URL]


def test_pagination_follows_published_next_link_and_deduplicates():
    from app.live_sources import SEEDS
    class Client:
        def __init__(self):self.calls=[]
        def get(self,url):
            self.calls.append(url)
            if '/categories/' in url:
                if '?page=2' in url:return '<a href="/product/123-first">one</a><a href="/product/124-second">two</a>'
                return '<a href="/product/123-first">one</a><a href="'+url+'?page=2">next</a>'
            if '/product/' in url:return product_html(sku=124 if '124-' in url else 123)
            raise SourceError('unavailable')
    client=Client();result=collect(client,per_category=2,pages=2)
    assert result['sources']['birmarket_computers']['count']==2
    assert client.calls.count(SEEDS['computers']+'?page=2')==1
    assert client.calls.count('https://birmarket.az/product/123-first')==len(SEEDS)


def test_second_venue_failure_preserves_first_showtimes():
    from app.live_sources import CINEMA_URL
    row='<tr data-date="06.11.2026"><td>A</td><td>19:30</td><td>Cinema A</td><td>Hall 1</td><td>2D</td><td>8 AZN</td><td><div data-session="456"></div></td></tr>'
    class Client:
        def get(self,url):
            if url==CINEMA_URL:return row+'<a href="/az/cinema/about-cinemaplus/ganjlik-mall/">Ganjlik</a>'
            raise SourceError('HTTP 403')
    result=collect(Client(),1,cinemas=2)
    assert len(result['showtimes'])==1
    assert result['sources']['cinema']['status']=='partial'
    assert result['sources']['cinema']['venues_attempted']==2
    assert result['sources']['cinema']['errors'][0]['url'].endswith('ganjlik-mall/')


@pytest.mark.parametrize('kwargs',[{'pages':0},{'pages':13},{'cinemas':21},{'per_category':201}])
def test_collection_limits_reject_unbounded_requests(kwargs):
    with pytest.raises(ValueError):collect(**kwargs)


def test_failed_next_page_preserves_products_from_first_page():
    class Client:
        def get(self,url):
            if '?page=2' in url:raise SourceError('HTTP 503')
            if '/categories/' in url:return '<a href="/product/123-first">one</a><a href="'+url+'?page=2">next</a>'
            if '/product/' in url:return product_html()
            raise SourceError('unavailable')
    data=collect(Client(),per_category=2,pages=2)
    assert data['sources']['birmarket_computers']['status']=='partial'
    assert data['sources']['birmarket_computers']['count']==1
    assert data['products']


def test_failed_refresh_retains_old_records_with_original_timestamps(tmp_path):
    path=tmp_path/'snapshot.json'
    old={'fetched_at':'2026-11-03T00:00:00+00:00','products':[{'id':'BM_1','category':'audio','fetched_at':'2026-11-03T00:00:00+00:00','price_azn':'50.00'}],'showtimes':[],
         'sources':{'birmarket_audio':{'status':'ok','count':1}}}
    save_snapshot(old,path)
    failed={'fetched_at':'2026-11-03T01:00:00+00:00','products':[],'showtimes':[],
            'sources':{'birmarket_audio':{'status':'unavailable','count':0,'errors':['HTTP 503']}}}
    merged=save_snapshot(failed,path)
    assert merged['products'][0]['stale'] is True
    assert merged['products'][0]['fetched_at']==old['products'][0]['fetched_at']
    assert merged['sources']['birmarket_audio']['status']=='stale'
    assert merged['fetched_at']==old['fetched_at']
    assert merged['last_attempt_at']==failed['fetched_at']


def test_successful_refresh_replaces_old_records_without_stale_flag(tmp_path):
    path=tmp_path/'snapshot.json'
    old={'products':[{'id':'old','category':'audio','fetched_at':'2026-11-03T00:00:00+00:00'}],'showtimes':[],'sources':{}}
    save_snapshot(old,path)
    fresh={'products':[{'id':'new','category':'audio','fetched_at':'2026-11-03T01:00:00+00:00'}],'showtimes':[],'fetched_at':'2026-11-03T01:00:00+00:00','sources':{'birmarket_audio':{'status':'ok','count':1}}}
    saved=save_snapshot(fresh,path)
    assert [r['id'] for r in saved['products']]==['new']
    assert not saved['products'][0].get('stale')


def test_refresh_manager_single_job_and_cooldown(tmp_path):
    import threading
    from app.live_sources import RefreshManager
    entered=threading.Event();release=threading.Event();calls=[]
    def collector(**kwargs):
        calls.append(kwargs);entered.set();release.wait(3)
        return {'products':[],'showtimes':[],'sources':{},'fetched_at':'2026-11-03T00:00:00+00:00'}
    manager=RefreshManager(path=tmp_path/'snapshot.json',collector=collector)
    assert manager.start()['started'] is True
    assert entered.wait(1)
    assert manager.start()['started'] is False
    release.set();manager.thread.join(3)
    assert not manager.status()['running'] and len(calls)==1
    with pytest.raises(SourceError,match='Wait'):manager.start()


def test_refresh_manager_failure_does_not_replace_previous_file(tmp_path):
    from app.live_sources import RefreshManager
    path=tmp_path/'snapshot.json';path.write_text('{"previous":true}')
    def fail(**kwargs):raise RuntimeError('private traceback')
    manager=RefreshManager(path=path,collector=fail)
    manager.start();manager.thread.join(3)
    assert json.loads(path.read_text())=={'previous':True}
    assert manager.status()['error'] and 'private' not in manager.status()['error']


def test_refresh_route_requires_live_mode(tmp_path):
    with TestClient(create_app(Settings(database_path=tmp_path/'test.sqlite3',chat_mode='rules'))) as c:
        assert c.post('/api/live/refresh').status_code==409


@pytest.mark.parametrize('query',['laptop','laptops','noutbuk','noutbukları','ноутбуки'])
def test_live_laptop_queries_match_the_same_records(query):
    from app.live_chat import live_products
    data={'products':[
        {'id':'BM_1','title':'Noutbuk Lenovo','price_azn':'1000','category':'computers'},
        {'id':'BM_2','title':'Laptop Asus','price_azn':'1600','category':'computers'},
        {'id':'BM_3','title':'Telefon Samsung','price_azn':'900','category':'phones'}]}
    assert [r['id'] for r in live_products(data,{'query':query})]==['BM_1','BM_2']


def test_live_local_laptop_price_and_details_sequence(live_app):
    from unittest.mock import patch
    c,sid,state,data,path=live_app
    first=data['products'][0]
    first.update(title='Noutbuk Lenovo',price_azn='1000.00')
    data['products'] += [dict(first,id='BM_124',title='Laptop Asus',price_azn='1600.00'),
                         dict(first,id='BM_125',title='Notebook Acer',price_azn='1500.00')]
    with patch.object(state.chat.planner,'plan',side_effect=AssertionError('No model call for these exact reads')):
        def send(text):
            result=c.post(f'/api/sessions/{sid}/chat',json={'message':text}).json()
            assert not result.get('error'),result
            return result
        en=send('Show me laptops')
        az=send('Noutbukları göstər')
        assert [r['id'] for r in en['results'][0]['items']]==[r['id'] for r in az['results'][0]['items']]
        assert len(az['results'][0]['items'])==3
        filtered=send('Yalnız 1500 AZN-dən ucuz olanları göstər')
        assert [r['id'] for r in filtered['results'][0]['items']]==['BM_123']
        assert '1499.99' in filtered['message']
        detail=send('Ən ucuzunun detallarını göstər')
        assert detail['results'][0]['items'][0]['id']=='BM_123'
        assert '1000.00' in detail['message']
        # A fresh full search clears the previous price refinement.
        assert len(send('Show me laptops')['results'][0]['items'])==3


@pytest.mark.parametrize('text',['en bahasinin detallari','Ən bahalısının detallarını göstər','details of the most expensive'])
def test_live_most_expensive_uses_search_shortlist_after_cheapest_details(live_app,text):
    from unittest.mock import patch
    c,sid,state,data,_=live_app
    row=data['products'][0]
    row.update(title='Notbuk HP',price_azn='1299.00')
    data['products'].append(dict(row,id='BM_124',title='Laptop Asus',price_azn='2499.00'))
    with patch.object(state.chat.planner,'plan',side_effect=AssertionError('Must resolve this read locally')):
        def send(message):
            result=c.post(f'/api/sessions/{sid}/chat',json={'message':message}).json()
            assert not result.get('error'),result
            return result['results'][0]['items']
        assert len(send('Noutbukları göstər'))==2
        assert send('Ən ucuzunun detalları')[0]['id']=='BM_123'
        assert send(text)[0]['id']=='BM_124'
        # Respect the active price filter; do not choose a product outside it.
        assert len(send('Yalnız 1500 AZN-dən ucuz olanları göstər'))==1
        assert send(text)[0]['id']=='BM_123'


@pytest.fixture
def shopping_flow(live_app):
    """Source-like fixture with boundary prices and unavailable attributes."""
    c,sid,state,data,_=live_app
    row=data['products'][0]
    row.update(title='Notbuk HP',brand='HP',price_azn='1299.00',rating=None)
    data['products'] += [dict(row,id='BM_124',title='Laptop Asus',brand='Asus',price_azn='1500.00'),
                         dict(row,id='BM_125',title='Notebook Acer',brand='Acer',price_azn='2499.00'),
                         dict(row,id='BM_126',title='Telefon Samsung',category='phones',brand='Samsung',price_azn='999.00')]
    return live_app


def send_live(flow,message):
    c,sid,_,_,_=flow
    result=c.post(f'/api/sessions/{sid}/chat',json={'message':message}).json()
    assert not result.get('error'),result
    return result


def live_ids(result):
    return [r['id'] for part in result['results'] for r in part.get('items',[])]


@pytest.mark.parametrize('search',['Noutbukları göstər','Show me laptops','Покажи ноутбуки'])
def test_live_three_language_search_and_clear(shopping_flow,search):
    from unittest.mock import patch
    state=shopping_flow[2]
    with patch.object(state.chat.planner,'plan',side_effect=AssertionError('Local supported reads')):
        assert live_ids(send_live(shopping_flow,search))==['BM_123','BM_124','BM_125']
        assert live_ids(send_live(shopping_flow,'under 1500'))==['BM_123']
        assert live_ids(send_live(shopping_flow,'clear filters'))==['BM_123','BM_124','BM_125']
        assert live_ids(send_live(shopping_flow,'over 1500'))==['BM_125']


@pytest.mark.parametrize('refinement,expected',[
    ('under 1500',['BM_123']),('up to 1500',['BM_123','BM_124']),
    ('greater than 1500',['BM_125']),('at least 1500',['BM_124','BM_125']),
    ('дешевле 1500',['BM_123']),('дороже 1500',['BM_125']),
    ('between 1300 and 2500',['BM_124','BM_125']),
    ('1500-2500 AZN arasında göstər',['BM_124','BM_125'])])
def test_live_price_boundaries(shopping_flow,refinement,expected):
    send_live(shopping_flow,'Show laptops')
    assert live_ids(send_live(shopping_flow,refinement))==expected


def test_live_comparison_sort_and_ordinal_use_search_list(shopping_flow):
    send_live(shopping_flow,'Noutbukları göstər')
    send_live(shopping_flow,'Ən ucuzunun detalları')
    comparison=send_live(shopping_flow,'ilk ikisini müqayisə et')
    assert live_ids(comparison)==['BM_123','BM_124']
    assert '201.00 AZN' in comparison['message']
    assert live_ids(send_live(shopping_flow,'bahadan ucuza sırala'))==['BM_125','BM_124','BM_123']
    assert live_ids(send_live(shopping_flow,'birincinin detalları'))==['BM_125']
    assert live_ids(send_live(shopping_flow,'ikincinin detalları'))==['BM_124']
    assert live_ids(send_live(shopping_flow,'sonuncunun detalları'))==['BM_123']


def test_live_empty_and_single_lists_never_replay_old_search(shopping_flow):
    from unittest.mock import patch
    with patch.object(shopping_flow[2].chat.planner,'plan',side_effect=AssertionError('No old-result fallback')):
        send_live(shopping_flow,'Show laptops')
        send_live(shopping_flow,'under 1500')
        assert send_live(shopping_flow,'compare the first two')['category']=='Ambiguous'
        empty=send_live(shopping_flow,'under 100')
        assert live_ids(empty)==[] and 'snapshot' in empty['message']
        assert live_ids(send_live(shopping_flow,'details of the cheapest'))==[]


def test_live_conflicting_range_does_not_replace_saved_filters(shopping_flow):
    send_live(shopping_flow,'Show laptops')
    send_live(shopping_flow,'under 1500')
    invalid=send_live(shopping_flow,'over 2000')
    assert invalid['category']=='Ambiguous' and 'conflict' in invalid['message']
    assert live_ids(send_live(shopping_flow,'show results'))==['BM_123']


def test_live_topic_switch_return_and_language_requests(shopping_flow,monkeypatch):
    from unittest.mock import patch
    monkeypatch.setattr('app.live_chat.baku_today',lambda:'2026-11-05')
    with patch.object(shopping_flow[2].chat.planner,'plan',side_effect=AssertionError('Supported flow stays local')):
        send_live(shopping_flow,'Noutbukları göstər')
        send_live(shopping_flow,'Yalnız 1500 AZN-dən ucuz olanları göstər')
        cinema=send_live(shopping_flow,'Sabah hansı filmlər var?')
        assert live_ids(cinema)==['CP_456'] and cinema['response_language']=='az'
        assert live_ids(send_live(shopping_flow,'18:00-dan sonra'))==['CP_456']
        assert live_ids(send_live(shopping_flow,'əvvəlki məhsulları göstər'))==['BM_123']
        assert send_live(shopping_flow,'in english')['response_language']=='en'
        assert send_live(shopping_flow,'rusca cavab ver')['response_language']=='ru'
        greeting=send_live(shopping_flow,'kaqdela')
        assert greeting['response_language']=='ru' and not greeting['results']
        assert send_live(shopping_flow,'Noutbukları göstər')['response_language']=='az'


def test_live_missing_cinema_date_reports_collected_dates(shopping_flow,monkeypatch):
    monkeypatch.setattr('app.live_chat.baku_today',lambda:'2026-11-06')
    result=send_live(shopping_flow,'what movies are on tomorrow')
    assert not live_ids(result)
    assert '2026-11-06' in result['message'] and 'entire website' in result['message']


def test_live_stale_search_and_details_are_labelled(shopping_flow):
    shopping_flow[3]['products'][0]['stale']=True
    result=send_live(shopping_flow,'Show laptops')
    assert 'Older records' in result['message']
    assert 'Older records' in send_live(shopping_flow,'details of the cheapest')['message']


def test_live_category_alias_and_brand_are_not_silently_dropped(shopping_flow):
    from app.live_chat import live_products
    data=shopping_flow[3]
    assert [r['id'] for r in live_products(data,{'category':'ноутбуки'})]==['BM_123','BM_124','BM_125']
    assert [r['id'] for r in live_products(data,{'query':'HP laptops'})]==['BM_123']
    assert live_products(data,{'query':'laptops','color':'red'})==[]
    assert live_products(data,{'query':'laptops','sort':'rating'})==[]


def test_live_invalid_snapshot_does_not_blame_key_or_expose_values(shopping_flow,caplog):
    data=shopping_flow[3];data.pop('products')
    c,sid,*_=shopping_flow
    result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Noutbukları göstər'}).json()
    assert result['error'] is True
    assert result['message'].startswith('LIVE_SNAPSHOT_FORMAT:')
    assert 'kvota' not in result['message'] and 'LIVE_SNAPSHOT_FORMAT' in caplog.text


def test_live_wrong_domain_id_cannot_appear_as_movie(shopping_flow):
    from unittest.mock import patch
    with patch.object(shopping_flow[2].chat.planner,'plan',return_value=live_plan('details',{'domain':'movies','record_id':'BM_123'})):
        c,sid,*_=shopping_flow
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Show movie details'}).json()
    assert result['error'] and result['message'].startswith('LIVE_INVALID_REQUEST:')
    assert result['results']==[]


def test_live_simple_product_and_cinema_request_uses_local_mixed_plan(shopping_flow):
    from unittest.mock import patch
    from app.llm_chat import TurnPlan,Action
    plan=TurnPlan(category='Mixed',clarification='',actions=[Action(tool='search_products',arguments='{"query":"laptop"}'),
        Action(tool='search_showtimes',arguments='{"on_date":"2026-11-06"}')])
    with patch.object(shopping_flow[2].chat.planner,'plan',return_value=plan) as planner:
        result=send_live(shopping_flow,'Show laptops and cinema tomorrow')
    assert planner.call_count==0 and result['category']=='Mixed'
    assert {p['domain'] for p in result['results']}=={'Shopping','Cinema'}


def test_live_out_of_scope_has_no_cards(shopping_flow):
    from unittest.mock import patch
    from app.llm_chat import TurnPlan
    plan=TurnPlan(category='Out of scope',clarification='I can help with flights, shopping and cinema only.',actions=[])
    with patch.object(shopping_flow[2].chat.planner,'plan',return_value=plan):
        result=send_live(shopping_flow,'Explain quantum physics')
    assert result['category']=='Out of scope' and not result['results']



def test_live_search_and_selection_survive_engine_reload(shopping_flow):
    from app.live_chat import LiveConversation
    c,sid,store,data,_=shopping_flow
    send_live(shopping_flow,'Show laptops')
    send_live(shopping_flow,'sort by price descending')
    old=store.chat
    store.chat=LiveConversation(store,old.reference,old.planner)
    assert live_ids(send_live(shopping_flow,'details of the second'))==['BM_124']
    assert live_ids(send_live(shopping_flow,'details of the cheapest'))==['BM_123']


@pytest.mark.parametrize('ids',[['BM_123'],['BM_123','BM_123']])
def test_live_comparison_rejects_insufficient_or_duplicate_ids(shopping_flow,ids):
    from unittest.mock import patch
    with patch.object(shopping_flow[2].chat.planner,'plan',return_value=live_plan('compare',{'domain':'products','ids':ids})):
        c,sid,*_=shopping_flow
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Compare these products'}).json()
    assert result['error'] and not result['results']


def test_live_laptop_source_failure_is_not_reported_as_zero_matches(shopping_flow):
    shopping_flow[3]['sources']={'birmarket_computers':{'status':'unavailable'},'birmarket_audio':{'status':'ok'}}
    c,sid,*_=shopping_flow
    result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Noutbukları göstər'}).json()
    assert result['error'] and result['message'].startswith('LIVE_SOURCE_UNAVAILABLE:')


@pytest.mark.parametrize('fields,expected',[
    ({'color':'Qara'},'Qara'),
    ({'color':['Black','Silver']},'Black, Silver'),
    ({'additionalProperty':[{'@type':'PropertyValue','name':'Rəng','value':'Qara'}]},'Qara'),
    ({'additionalProperty':{'name':'Color','value':'Black'}},'Black'),
    ({'name':'Black laptop'},None),
    ({'color':{'name':'unverified'}},None),
])
def test_product_color_only_from_explicit_published_attributes(fields,expected):
    row=parse_product(product_html(**fields),URL,'computers')
    assert row['color']==expected


@pytest.mark.parametrize('requested',['black','qara','черный','чёрный'])
def test_live_color_aliases_require_published_color(shopping_flow,requested):
    from app.live_chat import live_products
    data=shopping_flow[3]
    data['products'][0]['color']='Qara'
    data['products'][1]['color']='Silver'
    # A color word in the product name is not a confirmed attribute.
    data['products'][2]['title']='Black Laptop Acer'
    assert [r['id'] for r in live_products(data,{'query':'laptop','color':requested})]==['BM_123']


def test_live_user_color_range_price_cancellation_sequence(shopping_flow):
    from unittest.mock import patch
    c,sid,store,data,_=shopping_flow
    data['products'][0]['color']='Qara'
    data['products'][1]['color']='Black'
    data['products'][2]['color']='Silver'
    with patch.object(store.chat.planner,'plan',side_effect=AssertionError('Exact reported sequence should be local')):
        first=send_live(shopping_flow,'qara rengde qiymet 1500 ve 1800 araliginda olan komputer')
        assert live_ids(first)==['BM_124']
        relaxed=send_live(shopping_flow,'qiymet ferqi yoxdur')
        assert live_ids(relaxed)==['BM_123','BM_124']
        saved=store.chat.load(sid)['domains']['Shopping']['filters']
        assert saved['color']=='black' and 'min_price' not in saved and 'max_price' not in saved
        assert 'Qiymət məhdudiyyəti tətbiq edilmir' in relaxed['message']
        fresh=send_live(shopping_flow,'qara reng komputer')
        assert live_ids(fresh)==['BM_123','BM_124']
        # Color removal must not undo a subsequent price constraint.
        send_live(shopping_flow,'1500-1800 AZN arasında göstər')
        assert live_ids(send_live(shopping_flow,'rəng fərqi yoxdur'))==['BM_124']
        filters=store.chat.load(sid)['domains']['Shopping']['filters']
        assert 'color' not in filters and filters['min_price']=='1500'


def test_live_unpublished_color_explains_gap_and_does_not_silently_relax(shopping_flow):
    # Legacy snapshots did not contain a color key at all.
    for row in shopping_flow[3]['products']:row.pop('color',None)
    first=send_live(shopping_flow,'qara rəng kompüter')
    assert live_ids(first)==[]
    assert 'rəngi mənbədə göstərilməyib' in first['message']
    assert '3 məhsulun' in first['message']
    assert live_ids(send_live(shopping_flow,'qiymət fərqi yoxdur'))==[]
    assert len(live_ids(send_live(shopping_flow,'rəng fərqi yoxdur')))==3


@pytest.mark.parametrize('clear',['any price','price does not matter','цена не важна','qiymət önəmli deyil'])
def test_live_remove_price_preserves_other_filters(shopping_flow,clear):
    from unittest.mock import patch
    plan=live_plan('search_products',{'query':'laptop','brand':'HP','min_price':1500,'max_price':1800})
    with patch.object(shopping_flow[2].chat.planner,'plan',return_value=plan):
        send_live(shopping_flow,'Find HP laptops between 1500 and 1800 AZN')
    assert live_ids(send_live(shopping_flow,clear))==['BM_123']


def test_live_computer_type_search_clears_old_color_and_price_filters(shopping_flow):
    send_live(shopping_flow,'qara rengde qiymet 1500 ve 1800 araliginda olan komputer')
    assert len(live_ids(send_live(shopping_flow,'kompüterləri göstər')))==3


def test_product_features_table_collects_specs_and_body_color_only():
    table='''<table><tr data-info="product-features-row"><td data-info="product-features-name">Rəng:</td><td data-info="product-features-value"><div>Qara</div></td></tr>
    <tr data-info="product-features-row"><td>Klaviaturanın rəngi:</td><td>Gümüşü</td></tr>
    <tr data-info="product-features-row"><td>Operativ yaddaşın həcmi, Gb:</td><td>16</td></tr>
    <tr data-info="product-features-row"><td>SSD həcmi, Gb:</td><td>512</td></tr></table>'''
    row=parse_product(product_html()+table,URL,'computers')
    assert row['color']=='Qara'
    assert row['specifications']['Operativ yaddaşın həcmi, Gb']=='16'
    assert row['specifications']['SSD həcmi, Gb']=='512'


def test_keyboard_color_never_becomes_body_color():
    row=parse_product(product_html()+'<table><tr data-info="product-features-row"><td>Klaviaturanın rəngi:</td><td>Qara</td></tr></table>',URL,'computers')
    assert row['color'] is None
    assert row['specifications']['Klaviaturanın rəngi']=='Qara'


def test_product_details_include_published_specs(live_app):
    from unittest.mock import patch
    c,sid,store,data,_=live_app
    data['products'][0]['specifications']={'SSD həcmi, Gb':'512','Rəng':'Gümüşü'}
    with patch.object(store.chat.planner,'plan',return_value=live_plan('details',{'domain':'products','record_id':'BM_123'})):
        result=c.post(f'/api/sessions/{sid}/chat',json={'message':'Show product details'}).json()
    assert '512' in result['message'] and 'Gümüşü' in result['message']



def test_refresh_uses_expanded_bounded_sample(tmp_path):
    from app.live_sources import RefreshManager
    calls=[]
    def collector(**kwargs):
        calls.append(kwargs)
        return {'products':[],'showtimes':[],'sources':{}}
    job=RefreshManager(path=tmp_path/'snapshot.json',collector=collector)
    job.start();job.thread.join(3)
    assert len(calls)==1 and calls[0]['per_category']==150
    assert calls[0]['pages']==7 and calls[0]['cinemas']==20



def test_parallel_product_failure_preserves_other_valid_records():
    class Client:
        def get(self,url):
            if '/categories/' in url:return '<a href="/product/123-first">one</a><a href="/product/124-second">two</a>'
            if '/product/124-' in url:raise SourceError('HTTP 503')
            if '/product/' in url:return product_html()
            raise SourceError('unavailable cinema')
    result=collect(Client(),per_category=2)
    assert len(result['products'])==3
    assert all(v['status']=='partial' for k,v in result['sources'].items() if k.startswith('birmarket_'))


def test_null_product_availability_remains_unknown():
    result=parse_product(product_html(offers={'price':100,'priceCurrency':'AZN','availability':None}),URL,'computers')
    assert result['in_stock'] is None and result['stock'] is None


def test_compzone_product_price_specs_and_source_are_preserved():
    result=parse_product(product_html(sku='shared-sku',offers={'price':'2910.00','priceCurrency':'AZN','availability':'https://schema.org/InStock'},additionalProperty=[{'name':'Prosessor','value':'Intel Ultra 9'},{'name':'Rəng','value':'Black'}]),'https://compzone.az/mehsul/example','computers')
    assert result['id'].startswith('BM_CZ_') and result['source']=='CompZone'
    assert result['price_azn']=='2910.00' and result['color']=='Black'
    assert result['specifications']['Prosessor']=='Intel Ultra 9'
    assert result['stock'] is None and result['in_stock'] is True


def test_compzone_collects_only_published_product_pages():
    from app.live_sources import collect_compzone
    class Client:
        def get(self,url):
            assert '/api/' not in url
            if url.endswith('/noutbuklar'):return '<a href="/mehsul/example">one</a><a href="/mehsul/example">duplicate</a><a href="/api/products">private</a><a href="https://evil.test/mehsul/example">external</a>'
            return product_html()
    rows,status=collect_compzone(Client())
    assert len(rows)==1 and status['status']=='ok'
    assert rows[0]['source_url']=='https://compzone.az/mehsul/example'


def test_failed_birmarket_does_not_relabel_compzone_as_retained_birmarket():
    from app.live_sources import retain_failed_sources
    old={'products':[{'id':'BM_CZ_abc','category':'computers','source':'CompZone','fetched_at':'2026-11-03T00:00:00+00:00'}],'showtimes':[]}
    fresh={'products':[],'showtimes':[],'sources':{'birmarket_computers':{'status':'unavailable'}}}
    merged=retain_failed_sources(fresh,old)
    assert not merged['products']
    assert merged['sources']['birmarket_computers']['status']=='unavailable'


def test_compzone_results_work_when_birmarket_laptop_source_is_unavailable(shopping_flow):
    data=shopping_flow[3]
    data['sources']={'birmarket_computers':{'status':'unavailable'},'compzone_computers':{'status':'ok'}}
    row=data['products'][0];row.update(id='BM_CZ_example',source='CompZone',source_url='https://compzone.az/mehsul/example')
    result=send_live(shopping_flow,'Show laptops')
    assert not result.get('error')
    assert 'BM_CZ_example' in live_ids(result)


@pytest.mark.parametrize('message',[
    'sabaha 28 mallda sinema','Sabah 28 Mall-da kino','cinema at 28 Mall tomorrow',
    'завтра кино в 28 Mall',
])
def test_simple_venue_and_day_cinema_never_calls_gemini(shopping_flow,message):
    from unittest.mock import patch
    data=shopping_flow[3]
    data['showtimes'][0].update(cinema_name='28 Mall',date='2026-11-04')
    with patch('app.live_chat.baku_today',return_value='2026-11-03'), patch.object(shopping_flow[2].chat.planner,'plan',side_effect=AssertionError('Gemini must not be called')):
        result=send_live(shopping_flow,message)
    assert not result.get('error')
    assert result['results'][0]['items']
    assert all(r['cinema_name']=='28 Mall' and r['date']=='2026-11-04' for r in result['results'][0]['items'])


def test_bare_cinema_clarifies_without_gemini(shopping_flow):
    from unittest.mock import patch
    with patch.object(shopping_flow[2].chat.planner,'plan',side_effect=AssertionError('Gemini must not be called')):
        result=send_live(shopping_flow,'cinema')
    assert not result.get('error') and not result['results']
    assert '?' in result['message']


def test_local_cinema_keeps_unsupported_filters_for_planner(shopping_flow):
    chat=shopping_flow[2].chat
    state={'active':None,'domains':{},'language':'en'}
    data={'showtimes':[{'cinema_name':'28 Mall'}]}
    assert chat.local_cinema_plan(state,'tomorrow cinema at 28 mall in english',data) is None
    assert chat.local_cinema_plan(state,'tomorrow cinema at 99 mall',data) is None


def test_cinema_day_refinement_retains_venue(shopping_flow):
    from unittest.mock import patch
    chat=shopping_flow[2].chat
    state={'active':'Cinema','domains':{'Cinema':{'kind':'showtimes','filters':{'cinema_id':'28 Mall','on_date':'2026-11-03'}}},'language':'az'}
    with patch('app.live_chat.baku_today',return_value='2026-11-03'):
        plan=chat.local_cinema_plan(state,'sabah',{'showtimes':[{'cinema_name':'28 Mall'}]})
    args=json.loads(plan.actions[0].arguments)
    assert args=={'cinema_id':'28 Mall','on_date':'2026-11-04'}


def test_quota_error_explains_limit_instead_of_wrong_key(shopping_flow):
    from unittest.mock import patch
    from app.llm_chat import PlannerError
    with patch.object(shopping_flow[2].chat.planner,'plan',side_effect=PlannerError('GEMINI_HTTP_429: provider limit')):
        client,sid,*_=shopping_flow
        result=client.post(f'/api/sessions/{sid}/chat',json={'message':'Find something unusual that suits me'}).json()
    assert result['error'] and result['message'].startswith('GEMINI_HTTP_429:')
    assert 'limit' in result['message'].lower() and not result['results']
