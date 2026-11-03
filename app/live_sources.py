"""Phase 2 read-only public-page collectors. No mock data or invented inventory."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import json
import os
import tempfile
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from fastapi import APIRouter, Query, Request as ApiRequest
from fastapi.responses import JSONResponse

ROOT=Path(__file__).resolve().parents[1]
SNAPSHOT=ROOT/'var'/'live_sources.json'
AGENT='SuperChatResearch/0.1'
HOSTS={'compzone.az','birmarket.az','www.birmarket.az','cinemastercard.az','www.cinemastercard.az','cinemaplus.az','www.aviasales.com'}
SEEDS={
    'computers':'https://birmarket.az/categories/16-noutbuklar',
    'audio':'https://birmarket.az/categories/6-qulaqliqlar-ve-qarniturlar',
    'phones':'https://birmarket.az/categories/3-mobil-telefonlar-ve-smartfonlar',
}
CINEMA_URL='https://cinemastercard.az/az/cinema/about-cinemaplus/azerbaijan-cinema/'


class SourceError(Exception):pass


def now():return datetime.now(timezone.utc).isoformat()


def allowed_url(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname not in HOSTS or p.username or p.password or p.port not in (None,443):
        raise SourceError('Only approved HTTPS source hosts are supported.')
    return url


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        allowed_url(newurl)
        # A redirected destination has not had its robots policy checked.
        raise SourceError('Source redirected. Review the new canonical URL before collecting.')


def robots_allowed(text,url):
    """Longest matching Allow/Disallow rule in the applicable user-agent group."""
    groups=[];agents=[];rules=[];started=False
    for line in text.splitlines()+['User-agent: __end__']:
        line=line.split('#',1)[0].strip()
        if ':' not in line:continue
        key,value=(part.strip() for part in line.split(':',1));key=key.casefold()
        if key=='user-agent':
            if started:groups.append((agents,rules));agents=[];rules=[];started=False
            agents.append(value.casefold())
        elif key in ('allow','disallow') and agents:
            started=True
            if value:rules.append((key,value))
    specific=[g for g in groups if any(a!='*' and a in AGENT.casefold() for a in g[0])]
    applicable=specific or [g for g in groups if '*' in g[0]]
    p=urlsplit(url);target=p.path or '/'
    if p.query:target+='?'+p.query
    matches=[]
    for _,group_rules in applicable:
        for key,pattern in group_rules:
            end=pattern.endswith('$');pattern=pattern[:-1] if end else pattern
            regex='^'+'.*'.join(re.escape(part) for part in pattern.split('*'))+('$' if end else '')
            if re.search(regex,target):matches.append((len(pattern.replace('*','')),key=='allow'))
    return max(matches,default=(0,True))[1]


class PublicClient:
    def __init__(self,timeout=15,interval=1.0):
        self.timeout=timeout;self.interval=interval;self.policies={};self.last={};self.lock=threading.Lock()
        self.opener=build_opener(SafeRedirect())
    def _read(self,url):
        allowed_url(url);host=urlsplit(url).hostname
        with self.lock:
            wait=self.interval-(time.monotonic()-self.last.get(host,0))
            if wait>0:time.sleep(wait)
            self.last[host]=time.monotonic()
        try:
            request=Request(url,headers={'User-Agent':AGENT,'Accept':'text/html,text/plain;q=0.9'})
            with self.opener.open(request,timeout=self.timeout) as response:
                raw=response.read(3_000_001)
                if len(raw)>3_000_000:raise SourceError('Source page exceeds the size limit.')
                return raw.decode(response.headers.get_content_charset() or 'utf-8',errors='replace')
        except HTTPError as exc:raise SourceError(f'Source returned HTTP {exc.code}.') from None
        except (URLError,TimeoutError,OSError):raise SourceError('Source network request failed.') from None
    def get(self,url):
        allowed_url(url);p=urlsplit(url);origin=f'https://{p.netloc}'
        if origin not in self.policies:
            try:self.policies[origin]=self._read(origin+'/robots.txt')
            except SourceError as exc:raise SourceError('Could not verify source robots policy: '+str(exc)) from None
        if not robots_allowed(self.policies[origin],url):raise SourceError('Source robots policy disallows this path.')
        return self._read(url)


class Page(HTMLParser):
    def __init__(self,html):
        super().__init__(convert_charrefs=True);self.links=[];self.scripts=[];self.script=None;self.row=None;self.cell=None;self.rows=[];self.feature_row=None;self.feature_cell=None;self.features={}
        self.feed(html)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='tr' and a.get('data-info')=='product-features-row':self.feature_row=[]
        if tag=='td' and self.feature_row is not None:self.feature_cell=[]
        if tag=='a' and a.get('href'):
            self.links.append(a['href'])
            if self.cell is not None:self.row['links'].append(a['href'])
        if tag=='script' and a.get('type')=='application/ld+json':self.script=[]
        if tag=='tr' and a.get('data-date'):self.row={'date':a['data-date'],'cells':[],'links':[],'session':None}
        if tag=='td' and self.row is not None:self.cell=[]
        if self.row is not None and a.get('data-session'):self.row['session']=a['data-session']
    def handle_data(self,data):
        if self.feature_cell is not None:self.feature_cell.append(data)
        if self.script is not None:self.script.append(data)
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag=='td' and self.feature_cell is not None:
            self.feature_row.append(' '.join(' '.join(self.feature_cell).split()));self.feature_cell=None
        if tag=='tr' and self.feature_row is not None:
            if len(self.feature_row)==2:
                key,value=self.feature_row
                if key and value:self.features[key.rstrip(': ').strip()[:120]]=value[:300]
            self.feature_row=None;self.feature_cell=None
        if tag=='script' and self.script is not None:self.scripts.append(''.join(self.script));self.script=None
        if tag=='td' and self.cell is not None:
            self.row['cells'].append(' '.join(' '.join(self.cell).split()));self.cell=None
        if tag=='tr' and self.row is not None:self.rows.append(self.row);self.row=None;self.cell=None


def money(value):
    try:
        result=Decimal(str(value).replace(',','.'))
        if not result.is_finite() or result<0:raise ValueError
        return str(result.quantize(Decimal('.01')))
    except (InvalidOperation,ValueError,TypeError):raise SourceError('Source price is missing or invalid.') from None


def json_nodes(value):
    if isinstance(value,list):
        for item in value:yield from json_nodes(item)
    elif isinstance(value,dict):
        yield value
        if '@graph' in value:yield from json_nodes(value['@graph'])


def published_color(item):
    """Use explicit product attributes only, never photo/title guesses."""
    value=item.get('color')
    if not value:
        properties=item.get('additionalProperty',[])
        if isinstance(properties,dict):properties=[properties]
        if isinstance(properties,list):
            for prop in properties:
                if isinstance(prop,dict) and str(prop.get('name','')).strip().lower() in ('color','colour','rəng','reng','цвет'):
                    value=prop.get('value');break
    if isinstance(value,list):value=', '.join(v.strip() for v in value if isinstance(v,str) and v.strip())
    return unescape(value.strip())[:120] if isinstance(value,str) and value.strip() else None


def parse_product(html,url,category,fetched_at=None):
    allowed_url(url)
    host=urlsplit(url).hostname
    if host not in ('birmarket.az','www.birmarket.az','compzone.az'):raise SourceError('Not a supported product URL.')
    source='CompZone' if host=='compzone.az' else 'Birmarket'
    page=Page(html)
    for script in page.scripts:
        try:document=json.loads(script)
        except ValueError:continue
        for item in json_nodes(document):
            types=item.get('@type',[]);types=[types] if isinstance(types,str) else types
            if not isinstance(types,list) or 'Product' not in types:continue
            name=item.get('name');offers=item.get('offers')
            if not isinstance(name,str) or not name.strip() or not isinstance(offers,dict):continue
            if offers.get('priceCurrency')!='AZN':raise SourceError('Source currency is not AZN; no implicit conversion is performed.')
            price=money(offers.get('lowPrice',offers.get('price')))
            brand=item.get('brand');brand=brand.get('name') if isinstance(brand,dict) else brand
            aggregate=item.get('aggregateRating')
            rating=aggregate.get('ratingValue') if isinstance(aggregate,dict) else None
            try:
                rating=float(rating)
                if not 0<=rating<=5:rating=None
            except (TypeError,ValueError):rating=None
            availability=offers.get('availability')
            availability=availability.rsplit('/',1)[-1] if isinstance(availability,str) else ''
            identity=str(item.get('sku') or sha256(url.encode()).hexdigest()[:12])
            properties=item.get('additionalProperty',[])
            specs=dict(page.features)
            if isinstance(properties,list):
                for prop in properties:
                    if isinstance(prop,dict) and isinstance(prop.get('name'),str) and isinstance(prop.get('value'),(str,int,float)):
                        specs[prop['name'][:120]]=str(prop['value'])[:300]
            prefix='BM_CZ_' if source=='CompZone' else 'BM_'
            if source=='CompZone':identity=sha256(url.encode()).hexdigest()[:16]
            return {'id':prefix+identity,'title':unescape(name.strip()),'category':category,
                    'price_azn':price,'price_kind':'lowest_listed_offer','currency':'AZN',
                    'brand':brand if isinstance(brand,str) else None,'rating':rating,'color':published_color(item) or next((v for k,v in page.features.items() if k.casefold() in ('rəng','reng','color','colour','цвет','korpusun rəngi','цвет корпуса')),None),
                    'specifications':specs,
                    'stock':None,'in_stock':True if availability=='InStock' else False if availability=='OutOfStock' else None,
                    'source':source,'source_url':url,'fetched_at':fetched_at or now(),
                    'data_mode':'scraped','can_reserve':False}
    raise SourceError('No supported Product JSON-LD was found; source structure may have changed.')


def parse_showtimes(html,url,fetched_at=None):
    allowed_url(url);records={}
    for row in Page(html).rows:
        c=row['cells']
        if len(c)<6:continue
        try:
            day=datetime.strptime(row['date'],'%d.%m.%Y').date().isoformat()
            clock=datetime.strptime(c[1],'%H:%M').time().isoformat(timespec='minutes')
            amount=re.fullmatch(r'\s*(\d+(?:[.,]\d{1,2})?)\s*AZN\s*',c[5])
            if not amount:continue
            price=money(amount[1])
        except (ValueError,SourceError):continue
        ident=row['session'] or sha256('|'.join([day,clock,c[0],c[2],c[3]]).encode()).hexdigest()[:16]
        records[ident]={'id':'CP_'+ident,'movie_title':c[0],'cinema_name':c[2],'hall':c[3],
                        'date':day,'time':clock,'price_azn':price,'currency':'AZN',
                        'seats_available':None,'source':'CinemaPlus / CineMastercard',
                        'source_url':url,'fetched_at':fetched_at or now(),'data_mode':'scraped','can_reserve':False}
    if not records:raise SourceError('No dated showtime rows found; no mock showtimes were substituted.')
    return sorted(records.values(),key=lambda r:(r['date'],r['time'],r['id']))


def collect_compzone(client=None,limit=150,progress=None):
    """Published laptop links only; never query the site's private /api/ paths."""
    if not 1<=limit<=200:raise ValueError('limit must be 1-200')
    client=client or PublicClient();progress=progress or (lambda message:None)
    url='https://compzone.az/noutbuklar';rows=[];errors=[]
    try:
        page=Page(client.get(url));links=[]
        for href in page.links:
            full=urljoin(url,href);parsed=urlsplit(full)
            if parsed.hostname=='compzone.az' and re.fullmatch(r'/mehsul/[a-z0-9-]+',parsed.path) and not parsed.query and full not in links:links.append(full)
        for index,link in enumerate(links[:limit],1):
            try:rows.append(parse_product(client.get(link),link,'computers'))
            except SourceError as exc:errors.append(str(exc))
            progress(f'CompZone: processed {index}/{min(limit,len(links))}; collected {len(rows)}')
        if not links:errors.append('No published product links found.')
    except SourceError as exc:errors.append(str(exc))
    return rows,{'status':'ok' if rows and not errors else 'partial' if rows else 'unavailable',
                 'count':len(rows),'url':url,'errors':errors}


def collect(client=None,per_category=3,pages=1,cinemas=1,progress=None):
    if not 1<=per_category<=200:raise ValueError('per_category must be between 1 and 200')
    if not 1<=pages<=12 or not 1<=cinemas<=20:raise ValueError('pages must be 1-12; cinemas must be 1-20')
    progress=progress or (lambda message:None)
    client=client or PublicClient();snapshot={'version':1,'fetched_at':now(),'products':[],'showtimes':[],'sources':{}}
    for category,url in SEEDS.items():
        key='birmarket_'+category;errors=[];count=0
        try:
            links=[];next_page=url;visited=set()
            for page_number in range(pages):
                if not next_page or next_page in visited:break
                visited.add(next_page);progress(f'{category}: reading category page {page_number+1}')
                try:page=Page(client.get(next_page))
                except SourceError as exc:errors.append(str(exc));break
                next_page=None
                for href in page.links:
                    full=urljoin(url,href);parsed=urlsplit(full)
                    if parsed.hostname!='birmarket.az':continue
                    if re.fullmatch(r'/product/\d+-[^/?#]+',parsed.path) and not parsed.query and full not in links:links.append(full)
                    if parsed.path==urlsplit(url).path and parsed.query==f'page={page_number+2}':next_page=full
                if len(links)>=per_category:break
            def read_product(link):
                try:return parse_product(client.get(link),link,category),None
                except SourceError as exc:return None,str(exc)
            # The shared PublicClient still spaces request starts per host;
            # three workers overlap network waits, not the source rate limit.
            selected=links[:per_category]
            with ThreadPoolExecutor(max_workers=3) as pool:
                for completed,(row,error) in enumerate(pool.map(read_product,selected),1):
                    if row is not None:snapshot['products'].append(row);count+=1
                    else:errors.append(error)
                    progress(f'{category}: processed {completed}/{len(selected)}; collected {count}')
            if not links:errors.append('No product links found.')
        except SourceError as exc:errors.append(str(exc))
        snapshot['sources'][key]={'status':'ok' if count and not errors else 'partial' if count else 'unavailable','count':count,'url':url,'errors':errors}
    extra,status=collect_compzone(client,per_category,progress)
    snapshot['products'].extend(extra);snapshot['sources']['compzone_computers']=status
    venue_errors=[];venues=[];sessions={}
    try:
        progress('cinema: reading primary venue')
        html=client.get(CINEMA_URL);venues=[CINEMA_URL]
        for href in Page(html).links:
            full=urljoin(CINEMA_URL,href);parsed=urlsplit(full)
            if parsed.hostname=='cinemastercard.az' and re.fullmatch(r'/az/cinema/about-cinemaplus/[a-z0-9-]+/',parsed.path) and full not in venues:venues.append(full)
        for index,url in enumerate(venues[:cinemas]):
            try:
                progress(f'cinema: venue {index+1}/{min(cinemas,len(venues))}')
                rows=parse_showtimes(html if index==0 else client.get(url),url)
                sessions.update({r['id']:r for r in rows})
            except SourceError as exc:venue_errors.append({'url':url,'message':str(exc)})
    except SourceError as exc:venue_errors.append({'url':CINEMA_URL,'message':str(exc)})
    snapshot['showtimes']=sorted(sessions.values(),key=lambda r:(r['date'],r['time'],r['id']))
    snapshot['sources']['cinema']={'status':'ok' if sessions and not venue_errors else 'partial' if sessions else 'unavailable',
        'count':len(sessions),'url':CINEMA_URL,'venues_attempted':min(cinemas,len(venues)),'errors':venue_errors}
    snapshot['sources']['flights']={'status':'not_connected','count':0,'url':'https://www.aviasales.com/',
        'errors':['This scrape snapshot contains no flights. The optional configured flight API is queried on demand; see flight_api in live status.']}
    # Never replace unavailable prices or inventory with official Phase 1 records.
    return snapshot


_snapshot_lock=threading.Lock()


def retain_failed_sources(data,previous):
    """Retain only failed-source records; preserve original retrieval timestamps."""
    if not data.get('sources') or not previous:return data
    data=json.loads(json.dumps(data))
    for key,status in data['sources'].items():
        if status.get('status')!='unavailable':continue
        if key.startswith('birmarket_'):
            category=key.removeprefix('birmarket_');field='products'
            retained=[r for r in previous.get(field,[]) if r.get('category')==category and r.get('source')!='CompZone']
        elif key.startswith('compzone_'):
            field='products';retained=[r for r in previous.get(field,[]) if r.get('source')=='CompZone']
        elif key=='cinema':field='showtimes';retained=previous.get(field,[])
        else:continue
        if not retained:continue
        existing={r['id'] for r in data[field]}
        rows=[{**r,'stale':True} for r in retained if r['id'] not in existing]
        data[field].extend(rows)
        status.update(status='stale',count=len(rows),last_success_at=max(r['fetched_at'] for r in retained))
    data['last_attempt_at']=data.get('fetched_at')
    timestamps=[r.get('fetched_at') for field in ('products','showtimes') for r in data.get(field,[]) if r.get('fetched_at')]
    if timestamps:data['fetched_at']=max(timestamps)
    return data


def save_snapshot(data,path=SNAPSHOT):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with _snapshot_lock:
        try:previous=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):previous=None
        data=retain_failed_sources(data,previous)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix=path.name+'.',suffix='.tmp',delete=False) as handle:
                temporary=Path(handle.name);json.dump(data,handle,ensure_ascii=False,indent=2);handle.write('\n')
            os.replace(temporary,path)
        finally:
            if temporary and temporary.exists():temporary.unlink()
    return data


def load_snapshot(path=SNAPSHOT):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):raise SourceError('No readable Phase 2 snapshot. Run python scrape_sources.py first.') from None


router=APIRouter(prefix='/api/live',tags=['Phase 2 sources'])


@router.get('/status')
def live_status(request: ApiRequest):
    try:
        data=load_snapshot()
        from app.flight_providers import get_flight_provider
        return {'data_mode':'scraped','fetched_at':data['fetched_at'],'sources':data['sources'],
                'flight_api':get_flight_provider().metadata(),
                'chat_connected':getattr(request.app.state,'live_mode',False),'note':'Read-only snapshots; source websites complete purchases. Use run_live.py for Phase 2 chat.'}
    except SourceError as exc:return JSONResponse(status_code=503,content={'message':str(exc)})


@router.get('/products')
def live_products(query: str='',limit:int=Query(20,ge=1,le=100)):
    try:
        data=load_snapshot();needle=query.casefold()
        return {'data_mode':'scraped','fetched_at':data['fetched_at'],'items':[p for p in data['products'] if needle in p['title'].casefold()][:limit]}
    except SourceError as exc:return JSONResponse(status_code=503,content={'message':str(exc)})


@router.get('/showtimes')
def live_showtimes(on_date: str|None=None):
    try:
        data=load_snapshot()
        return {'data_mode':'scraped','fetched_at':data['fetched_at'],'items':[p for p in data['showtimes'] if on_date is None or p['date']==on_date]}
    except SourceError as exc:return JSONResponse(status_code=503,content={'message':str(exc)})


class RefreshManager:
    """One bounded refresh per process; no automatic retry or recurring job."""
    def __init__(self,path=SNAPSHOT,collector=None,cooldown=60):
        self.path=path;self.collector=collector or collect;self.cooldown=cooldown
        self.lock=threading.RLock();self.running=False;self.last_start=None
        self.message='No refresh started.';self.error=None;self.result=None;self.thread=None
    def status(self):
        with self.lock:return {'running':self.running,'message':self.message,'error':self.error,'result':self.result}
    def start(self):
        with self.lock:
            if self.running:return {**self.status(),'started':False}
            elapsed=float('inf') if self.last_start is None else time.monotonic()-self.last_start
            if elapsed<self.cooldown:raise SourceError(f'Wait {max(1,int(self.cooldown-elapsed))} seconds before refreshing again.')
            self.running=True;self.last_start=time.monotonic();self.error=None;self.result=None;self.message='Starting source refresh…'
            self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
            return {**self.status(),'started':True}
    def progress(self,message):
        with self.lock:self.message=message
    def _run(self):
        try:
            data=self.collector(per_category=150,pages=7,cinemas=20,progress=self.progress)
            saved=save_snapshot(data,self.path)
            with self.lock:
                self.result={'products':len(saved['products']),'showtimes':len(saved['showtimes']),'sources':saved['sources']}
                self.message='Refresh completed. Check source statuses; retained data is marked stale.'
        except Exception:
            with self.lock:self.error='Refresh failed. Previously saved data was not replaced.';self.message=self.error
        finally:
            with self.lock:self.running=False


_manager_lock=threading.Lock()
def refresh_manager(request):
    with _manager_lock:
        if not hasattr(request.app.state,'source_refresh'):
            request.app.state.source_refresh=RefreshManager()
        return request.app.state.source_refresh


@router.post('/refresh',status_code=202)
def refresh_sources(request: ApiRequest):
    if not getattr(request.app.state,'live_mode',False):return JSONResponse(status_code=409,content={'message':'Refresh is available in Phase 2 on port 8001.'})
    try:return refresh_manager(request).start()
    except SourceError as exc:return JSONResponse(status_code=429,content={'message':str(exc)})


@router.get('/refresh-status')
def refresh_status(request: ApiRequest):
    return refresh_manager(request).status()
