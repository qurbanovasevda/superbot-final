import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Product, Showtime
from app.repositories.catalog import Catalog
from app.repositories.json_repository import DataSourceError, JsonRepository

DATA = Settings().data_dir


def test_official_counts_and_no_source_mutation():
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in DATA.glob('*.json')}
    catalog = Catalog.load(DATA)
    assert catalog.counts() == dict(flights=17, products=12, movies=8, cinemas=3, showtimes=16)
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in DATA.glob('*.json')}


def test_join_showtime_to_movie_and_cinema():
    catalog = Catalog.load(DATA)
    for showtime in catalog.showtimes.all():
        assert catalog.movies.get(showtime.movie_id)
        assert catalog.cinemas.get(showtime.cinema_id)


def test_money_and_unavailable_inventory():
    catalog = Catalog.load(DATA)
    assert catalog.products.get('P001').price_azn == Decimal('89.90')
    assert catalog.products.get('P002').stock == 0
    assert catalog.showtimes.get('S003').seats_available == 0
    assert catalog.flights.get('F001').duration_min == 215
    assert catalog.products.get('missing') is None


@pytest.mark.parametrize('payload', ['{', '{}', '[{}]'])
def test_invalid_dataset_is_controlled(tmp_path, payload):
    source = tmp_path / 'products.json'
    source.write_text(payload, encoding="utf-8")
    with pytest.raises(DataSourceError):
        JsonRepository(source, Product)


def test_missing_dataset_is_controlled(tmp_path):
    with pytest.raises(DataSourceError):
        Catalog.load(tmp_path)


def test_duplicate_ids_rejected(tmp_path):
    product = json.loads((DATA / 'products.json').read_text(encoding="utf-8"))[0]
    source = tmp_path / 'products.json'
    source.write_text(json.dumps([product, product]), encoding="utf-8")
    with pytest.raises(DataSourceError):
        JsonRepository(source, Product)


def test_over_capacity_rejected():
    raw = json.loads((DATA / 'showtimes.json').read_text(encoding="utf-8"))[0]
    raw['seats_available'] = raw['seats_total'] + 1
    with pytest.raises(ValueError):
        Showtime.model_validate(raw)


def test_unknown_movie_reference_rejected(tmp_path):
    for path in DATA.glob('*.json'):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    path = tmp_path / 'showtimes.json'
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw[0]['movie_id'] = 'UNKNOWN'
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DataSourceError):
        Catalog.load(tmp_path)


def test_health_ready():
    with TestClient(create_app()) as client:
        response = client.get('/health')
        assert response.status_code == 200
        assert response.json()['datasets']['flights'] == 17
        assert response.json()['reference_date'] == '2026-11-02'


def test_health_unavailable_without_trace(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get('/health')
        assert response.status_code == 503
        assert response.json() == {'status': 'unavailable', 'message': 'Mock datasets are unavailable.'}
