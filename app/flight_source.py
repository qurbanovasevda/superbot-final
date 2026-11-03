"""Read-only Aviasales cached prices via the Travelpayouts Data API.

No search-page scraping, bookings, mock substitution or currency conversion.
Run `python -m app.flight_source --date YYYY-MM-DD` for one safe API check.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from app.config import read_setting
from app.live_sources import SourceError
from app.services.catalog_service import normalized

ENDPOINT='https://api.travelpayouts.com/aviasales/v3/prices_for_dates'
CITY_CODES={'baku':'BAK','baki':'BAK','баку':'BAK','istanbul':'IST','истамбул':'IST','стамбул':'IST',
            'tbilisi':'TBS','tiflis':'TBS','тбилиси':'TBS','ankara':'ANK','dubai':'DXB','дубаи':'DXB',
            'london':'LON','londоn':'LON','лондон':'LON','paris':'PAR','париж':'PAR',
            'moscow':'MOW','moskva':'MOW','москва':'MOW'}


class FlightSourceError(SourceError):pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise FlightSourceError('FLIGHT_REDIRECT: Provider redirect was blocked; credentials were not forwarded.')


def iata(value):
    text=normalized(value or '').strip()
    if text in CITY_CODES:return CITY_CODES[text]
    if re.fullmatch('[a-z]{3}',text):return text.upper()
    raise FlightSourceError('FLIGHT_LOCATION: Use a three-letter city/airport code, for example BAK and IST.')


def source_link(value):
    if not isinstance(value,str) or not value.startswith('/search/') or value.startswith('//'):return None
    url='https://www.aviasales.com'+value
    parsed=urlsplit(url)
    if parsed.hostname!='www.aviasales.com' or parsed.username or parsed.password:return None
    return url


class FlightSource:
    def configured(self):return bool(read_setting('TRAVELPAYOUTS_TOKEN').strip())

    def fetch(self,params):
        token=read_setting('TRAVELPAYOUTS_TOKEN').strip()
        if not token:raise FlightSourceError('FLIGHT_NOT_CONFIGURED: Set TRAVELPAYOUTS_TOKEN locally and restart.')
        if not re.fullmatch(r'[A-Za-z0-9._-]{10,250}',token):
            raise FlightSourceError('FLIGHT_TOKEN_FORMAT: The local token format is invalid.')
        request=Request(ENDPOINT+'?'+urlencode(params),headers={
            'X-Access-Token':token,'Accept':'application/json','User-Agent':'SuperChatResearch/0.1'})
        try:
            with build_opener(NoRedirect()).open(request,timeout=15) as response:
                raw=response.read(2_000_001)
                if len(raw)>2_000_000:raise FlightSourceError('FLIGHT_RESPONSE_SIZE: Provider response exceeds the limit.')
                return json.loads(raw.decode('utf-8'))
        except HTTPError as exc:
            # Never expose provider bodies, URLs or request headers (token).
            raise FlightSourceError(f'FLIGHT_HTTP_{exc.code}: The provider rejected the request; check access or quota.') from None
        except (URLError,TimeoutError,OSError):
            raise FlightSourceError('FLIGHT_NETWORK: The flight provider could not be reached.') from None
        except (ValueError,UnicodeError):
            raise FlightSourceError('FLIGHT_RESPONSE_FORMAT: The provider did not return valid JSON.') from None

    def search(self,args):
        origin=iata(args.get('origin'));destination=iata(args.get('destination'))
        day=args.get('departure_date')
        month=args.get('departure_month')
        if day and month:raise FlightSourceError('FLIGHT_DATE: Choose a date or a month, not both.')
        if month:
            if not isinstance(month,str) or not re.fullmatch(r'\d{4}-\d{2}',month):raise FlightSourceError('FLIGHT_DATE: Use YYYY-MM.')
            first=date.fromisoformat(month+'-01')
            if month<datetime.now(timezone.utc).strftime('%Y-%m'):raise FlightSourceError('FLIGHT_DATE: The month is in the past.')
            period=month
        else:
            if isinstance(day,str):day=date.fromisoformat(day)
            if not isinstance(day,date):raise FlightSourceError('FLIGHT_DATE: Choose a departure date or month.')
            if day<datetime.now(timezone.utc).date():raise FlightSourceError('FLIGHT_DATE: The departure date is in the past.')
            period=day.isoformat()
        if args.get('airline') and not re.fullmatch('[A-Z0-9]{2,3}',args['airline'].upper()):
            raise FlightSourceError('FLIGHT_AIRLINE: Use an airline IATA code, for example J2 or TK.')
        params={'origin':origin,'destination':destination,'departure_at':period,
                'currency':'azn','one_way':'true','sorting':'price','limit':31 if month else 30,'page':1,'unique':'false'}
        if args.get('max_stops')==0:params['direct']='true'
        payload=self.fetch(params)
        return self.parse(payload,origin,destination,month or day,args)

    @staticmethod
    def parse(payload,origin,destination,day,args):
        if not isinstance(payload,dict) or payload.get('success') is not True or not isinstance(payload.get('data'),list):
            raise FlightSourceError('FLIGHT_RESPONSE_FORMAT: Provider response was unsuccessful or malformed.')
        if str(payload.get('currency','')).upper()!='AZN':
            raise FlightSourceError('FLIGHT_CURRENCY: AZN was not confirmed in the response; no price was displayed.')
        fetched=datetime.now(timezone.utc).isoformat();rows={}
        for raw in payload['data']:
            if not isinstance(raw,dict):continue
            try:
                departure=datetime.fromisoformat(raw['departure_at'].replace('Z','+00:00'))
                if departure.tzinfo is None:continue
                price=Decimal(str(raw['price']))
                if not price.is_finite() or price<=0:continue
                if isinstance(day,str):
                    if departure.strftime('%Y-%m')!=day or departure<=datetime.now(timezone.utc):continue
                elif departure.date()!=day:continue
                if raw.get('origin')!=origin or raw.get('destination')!=destination:continue
                url=source_link(raw.get('link'))
                if not url:continue
                currency=raw.get('currency',payload['currency'])
                if str(currency).upper()!='AZN':continue
                expires=raw.get('expires_at')
                if expires:
                    expiry=datetime.fromisoformat(expires.replace('Z','+00:00'))
                    if expiry.tzinfo is None or expiry<=datetime.now(timezone.utc):continue
                airline=raw.get('airline')
                if not isinstance(airline,str) or not re.fullmatch('[A-Z0-9]{2,3}',airline):continue
                stops=raw.get('transfers');duration=raw.get('duration_to',raw.get('duration'))
                if type(stops) is not int or stops<0:stops=None
                if type(duration) is not int or duration<=0:duration=None
                clock=departure.time().replace(tzinfo=None)
                if args.get('min_price') is not None and price<Decimal(str(args['min_price'])):continue
                if args.get('max_price') is not None and price>Decimal(str(args['max_price'])):continue
                if args.get('max_stops') is not None and (stops is None or stops>args['max_stops']):continue
                if args.get('departure_after') and clock<args['departure_after']:continue
                if args.get('departure_before') and clock>args['departure_before']:continue
                if args.get('airline') and args['airline'].upper()!=airline:continue
                identity=sha256(f'{origin}|{destination}|{departure.isoformat()}|{airline}|{raw.get("flight_number")}|{url}'.encode()).hexdigest()[:16]
                row={'id':'TF_'+identity,'title':f'{origin} → {destination}',
                     'origin_code':origin,'destination_code':destination,'departure_local':departure.isoformat(),
                     'price_azn':str(price.quantize(Decimal('.01'))),'currency':'AZN','airline':airline,
                     'stops':stops,'duration_min':duration,'baggage_kg':None,'seats_available':None,
                     'source':'Aviasales / Travelpayouts Data API','source_url':url,'fetched_at':fetched,
                     'provider_found_at':raw.get('found_at'),'price_kind':'cached_search_price',
                     'data_mode':'api_cache','can_reserve':False}
                if row['id'] not in rows or price<Decimal(rows[row['id']]['price_azn']):rows[row['id']]=row
            except (KeyError,TypeError,ValueError,InvalidOperation):continue
        records=list(rows.values());sorting=args.get('sort','price')
        fields={'price':'price_azn','duration':'duration_min','departure':'departure_local','stops':'stops','airline':'airline'}
        field=fields.get(sorting,'price_azn')
        records=[r for r in records if r.get(field) is not None]
        return sorted(records,key=lambda r:Decimal(r[field]) if sorting=='price' else r[field],reverse=args.get('descending',False))


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin',default='BAK');parser.add_argument('--destination',default='IST')
    periods=parser.add_mutually_exclusive_group(required=True)
    periods.add_argument('--date');periods.add_argument('--month')
    args=parser.parse_args()
    try:
        from app.flight_providers import get_flight_provider
        provider=get_flight_provider()
        search={'origin':args.origin,'destination':args.destination}
        search['departure_date' if args.date else 'departure_month']=args.date or args.month
        rows=provider.search(search)
        print(json.dumps({'status':'ok','provider':provider.metadata(),'results':rows},ensure_ascii=False,indent=2))
        return 0
    except (SourceError,ValueError) as exc:
        print(str(exc) if isinstance(exc,SourceError) else 'FLIGHT_DATE: Use YYYY-MM-DD.')
        return 1


if __name__=='__main__':raise SystemExit(main())
