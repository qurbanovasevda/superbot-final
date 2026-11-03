"""Provider boundary for read-only flight searches.

Add a reviewed adapter to PROVIDERS; do not rewrite the chat or return raw API
responses. The gateway validates the shared format and strips unrecognized data.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import re
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import read_setting
from app.flight_source import FlightSource, FlightSourceError, iata


@dataclass(frozen=True)
class FlightCapabilities:
    provider_id: str
    name: str
    price_kind: Literal['cached_search_price','live_offer']
    source_hosts: tuple[str,...]
    supports_calendar: bool = False
    supports_baggage: bool = False
    supports_seats: bool = False


class FlightAdapter(Protocol):
    capabilities: FlightCapabilities
    def configured(self) -> bool: ...
    def search(self,args:dict) -> list[dict]: ...


class TravelpayoutsAdapter(FlightSource):
    capabilities=FlightCapabilities('travelpayouts','Aviasales / Travelpayouts Data API',
        'cached_search_price',('www.aviasales.com',),supports_calendar=True)


class FlightOffer(BaseModel):
    """Provider-independent fields; absent inventory is unknown, never zero."""
    model_config=ConfigDict(extra='ignore',allow_inf_nan=False)
    id: str = Field(pattern=r'^[A-Z][A-Za-z0-9_]{1,79}$')
    origin_code: str = Field(pattern=r'^[A-Z]{3}$')
    destination_code: str = Field(pattern=r'^[A-Z]{3}$')
    departure_local: datetime
    price_azn: Decimal = Field(gt=0)
    currency: Literal['AZN']
    airline: str
    source_url: str
    fetched_at: datetime
    provider_found_at: datetime | None = None
    expires_at: datetime | None = None
    stops: int | None = Field(default=None,ge=0,strict=True)
    duration_min: int | None = Field(default=None,gt=0,strict=True)
    baggage_kg: Decimal | None = Field(default=None,ge=0)
    seats_available: int | None = Field(default=None,ge=0,strict=True)


class FlightGateway:
    def __init__(self,adapter:FlightAdapter):self.adapter=adapter

    def configured(self):return self.adapter.configured()

    def metadata(self):
        cap=self.adapter.capabilities
        return {'provider':cap.provider_id,'name':cap.name,'configured':self.configured(),
                'status':'configured' if self.configured() else 'not_configured',
                'price_kind':cap.price_kind,'data_mode':'api_cache' if cap.price_kind=='cached_search_price' else 'api_live',
                'supports_calendar':cap.supports_calendar,'supports_baggage':cap.supports_baggage,
                'supports_seats':cap.supports_seats,'real_booking':False,'searched_on_demand':True}

    def search(self,args):
        cap=self.adapter.capabilities
        if not self.configured():raise FlightSourceError('FLIGHT_NOT_CONFIGURED: Configure the selected flight adapter locally.')
        if args.get('departure_month') and not cap.supports_calendar:
            raise FlightSourceError('FLIGHT_CAPABILITY: This provider does not support month searches.')
        origin=iata(args.get('origin'));destination=iata(args.get('destination'))
        raw=self.adapter.search(dict(args))
        if not isinstance(raw,list) or len(raw)>100:
            raise FlightSourceError('FLIGHT_CONTRACT: Provider must return a bounded offer list.')
        records={}
        try:
            for candidate in raw:
                offer=FlightOffer.model_validate(candidate)
                if any(t is not None and t.tzinfo is None for t in (offer.departure_local,offer.fetched_at,offer.provider_found_at,offer.expires_at)):
                    raise ValueError('Timezone is required')
                url=urlsplit(offer.source_url)
                if url.scheme!='https' or url.hostname not in cap.source_hosts or url.username or url.password or url.port not in (None,443) or '\\' in offer.source_url or any(ord(c)<32 for c in offer.source_url):
                    raise ValueError('Unapproved source link')
                if offer.origin_code!=origin or offer.destination_code!=destination:raise ValueError('Wrong route')
                day=offer.departure_local.date()
                exact=args.get('departure_date')
                if exact and day!=(date.fromisoformat(exact) if isinstance(exact,str) else exact):raise ValueError('Wrong date')
                if args.get('departure_month') and day.strftime('%Y-%m')!=args['departure_month']:raise ValueError('Wrong month')
                if offer.departure_local<=datetime.now(timezone.utc):continue
                if offer.expires_at and offer.expires_at<=datetime.now(timezone.utc):continue
                if offer.id in records:raise ValueError('Duplicate offer ID')
                row=offer.model_dump(mode='json')
                row.update(title=f'{offer.origin_code} → {offer.destination_code}',source=cap.name,
                    price_azn=f'{offer.price_azn:.2f}',provider_id=cap.provider_id,price_kind=cap.price_kind,
                    data_mode='api_cache' if cap.price_kind=='cached_search_price' else 'api_live',
                    can_reserve=False,source_url_verified=True)
                if not cap.supports_baggage:row['baggage_kg']=None
                if not cap.supports_seats:row['seats_available']=None
                # Reapply user constraints, even if an adapter forgot a filter.
                if args.get('min_price') is not None and offer.price_azn<Decimal(str(args['min_price'])):continue
                if args.get('max_price') is not None and offer.price_azn>Decimal(str(args['max_price'])):continue
                if args.get('max_stops') is not None and (offer.stops is None or offer.stops>args['max_stops']):continue
                clock=offer.departure_local.time().replace(tzinfo=None)
                if args.get('departure_after') and clock<args['departure_after']:continue
                if args.get('departure_before') and clock>args['departure_before']:continue
                if args.get('airline') and offer.airline.upper()!=args['airline'].upper():continue
                records[row['id']]=row
        except (ValidationError,ValueError,TypeError,KeyError,AttributeError):
            # Never render arbitrary adapter payloads or exception text.
            raise FlightSourceError('FLIGHT_CONTRACT: Provider output failed validation; no results were displayed.') from None
        fields={'price':'price_azn','duration':'duration_min','departure':'departure_local','stops':'stops','airline':'airline'}
        field=fields.get(args.get('sort','price'),'price_azn')
        rows=[r for r in records.values() if r.get(field) is not None]
        return sorted(rows,key=lambda r:Decimal(r[field]) if field=='price_azn' else r[field],reverse=args.get('descending',False))


# Only reviewed Python adapters can be selected. Unknown names never fall back.
PROVIDERS={'travelpayouts':TravelpayoutsAdapter}


def get_flight_provider():
    name=read_setting('SUPERCHAT_FLIGHT_PROVIDER','travelpayouts').strip().lower()
    factory=PROVIDERS.get(name)
    if factory is None:raise FlightSourceError('FLIGHT_PROVIDER: Unknown configured provider; no fallback was used.')
    return FlightGateway(factory())
