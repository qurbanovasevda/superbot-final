"""Mocked vision integration checks; not a live recognition benchmark."""
import base64
from io import BytesIO
import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app.config import Settings
from app.main import create_app
from app.visual_search import VisualAnalysis, prepare_image
from app.llm_chat import PlannerError


def photo():
    stream=BytesIO();Image.new('RGB',(20,20),'black').save(stream,format='PNG')
    return base64.b64encode(stream.getvalue()).decode()


@pytest.fixture
def vision(tmp_path):
    with TestClient(create_app(Settings(chat_mode='llm',gemini_api_key='test-placeholder',database_path=tmp_path/'vision.sqlite3'))) as client:
        sid=client.post('/api/sessions').json()['session_id']
        planner=client.app.state.store.chat.planner
        yield client,sid,planner


@pytest.mark.parametrize('kind,expected',[
    ('headphones',['P001']),('earbuds',['P002']),('laptop',['P004','P003']),
    ('smartphone',['P006','P005']),('smartwatch',['P007']),('speaker',['P008']),
    ('shoes',['P009']),('backpack',['P010']),('coffee_machine',['P011']),('board_game',['P012']),
])
def test_classification_returns_only_grounded_catalog_rows(vision,kind,expected):
    client,sid,planner=vision
    with patch.object(planner,'generate',return_value=VisualAnalysis(product_type=kind,confidence=.91,ambiguous=False)) as call:
        result=client.post(f'/api/sessions/{sid}/visual-search',json={'image_base64':photo(),'response_language':'az'})
    assert result.status_code==200,result.text
    body=result.json()
    assert [row['id'] for row in body['results'][0]['items']]==expected
    assert body['visual_search']['confidence_kind']=='uncalibrated_model_estimate'
    assert '91%' in body['message'] and body['response_language']=='az'
    assert call.call_count==1
    assert call.call_args.kwargs['image']['mimeType']=='image/jpeg'
    state=client.app.state.store
    stored=state.chat.load(sid)
    assert stored['active']=='Shopping'
    assert stored['domains']['Shopping']['search_ids']==expected
    assert 'image_base64' not in json.dumps(stored)
    assert state.cart(sid)['items']==[] and state.reservations(sid)==[]


@pytest.mark.parametrize('kind,confidence,ambiguous',[('laptop',.5,False),('laptop',.95,True),('unknown',.99,False)])
def test_uncertain_or_unsupported_image_asks_instead_of_guessing(vision,kind,confidence,ambiguous):
    client,sid,planner=vision
    with patch.object(planner,'generate',return_value=VisualAnalysis(product_type=kind,confidence=confidence,ambiguous=ambiguous)):
        body=client.post(f'/api/sessions/{sid}/visual-search',json={'image_base64':photo(),'response_language':'ru'}).json()
    assert body['category']=='Ambiguous' and body['results']==[]
    assert body['needs_clarification'] and body['response_language']=='ru'
    assert 'Какой товар' in body['message']


@pytest.mark.parametrize('encoded',['bad base64!!',base64.b64encode(b'not an image').decode()])
def test_invalid_images_fail_without_model_call(vision,encoded):
    client,sid,planner=vision
    with patch.object(planner,'generate') as call:
        result=client.post(f'/api/sessions/{sid}/visual-search',json={'image_base64':encoded})
    assert result.status_code==400
    call.assert_not_called()


def test_unknown_session_does_not_consume_vision_quota(vision):
    client,_,planner=vision
    with patch.object(planner,'generate') as call:
        result=client.post('/api/sessions/missing/visual-search',json={'image_base64':photo()})
    assert result.status_code==404
    call.assert_not_called()


def test_vision_provider_failure_is_controlled_and_never_mutates_cart(vision):
    client,sid,planner=vision
    with patch.object(planner,'generate',side_effect=PlannerError('GEMINI_TIMEOUT: unavailable')):
        body=client.post(f'/api/sessions/{sid}/visual-search',json={'image_base64':photo(),'response_language':'az'}).json()
    assert body['error'] and body['results']==[]
    assert 'GEMINI_TIMEOUT' in body['message']
    assert client.app.state.store.cart(sid)['items']==[]


def test_rules_mode_explains_visual_search_configuration(tmp_path):
    with TestClient(create_app(Settings(chat_mode='rules',database_path=tmp_path/'rules.sqlite3'))) as client:
        sid=client.post('/api/sessions').json()['session_id']
        result=client.post(f'/api/sessions/{sid}/visual-search',json={'image_base64':photo()})
    assert result.status_code==503 and 'Gemini' in result.json()['message']


def test_image_is_reencoded_and_exif_metadata_removed():
    stream=BytesIO();image=Image.new('RGB',(1400,100),'white')
    exif=Image.Exif();exif[270]='private metadata'
    image.save(stream,format='JPEG',exif=exif)
    prepared=prepare_image(base64.b64encode(stream.getvalue()).decode())
    with Image.open(BytesIO(base64.b64decode(prepared['data']))) as output:
        assert max(output.size)<=1280
        assert output.format=='JPEG' and not output.getexif()


def test_gemini_request_uses_inline_image_and_existing_key(vision):
    client,sid,_=vision
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):
            data=VisualAnalysis(product_type='headphones',confidence=.92,ambiguous=False).model_dump_json()
            return json.dumps({'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':data}]}}]}).encode()
    with patch('app.llm_chat.urlopen',return_value=Response()) as call:
        result=client.post(f'/api/sessions/{sid}/visual-search',json={'image_base64':photo()})
    assert result.status_code==200
    assert call.call_count==1
    request=call.call_args.args[0]
    parts=json.loads(request.data)['contents'][0]['parts']
    assert parts[1]['inlineData']['mimeType']=='image/jpeg'
    assert request.get_header('X-goog-api-key')=='test-placeholder'
    assert 'test-placeholder' not in request.full_url
