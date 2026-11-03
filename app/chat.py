"""Offline conversation routing and persistent domain context."""
import json
import re
from datetime import date, time, timedelta
from decimal import Decimal
from threading import RLock
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.api import store
from app.services.catalog_service import DomainError

router = APIRouter(prefix="/api/sessions")


def has(text, pattern):
    return re.search(pattern, text, re.I) is not None


def resolve_date(text, reference):
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if match:
        return date.fromisoformat(match[1])
    match = re.search(r"\b(\d{1,2})\s+november\b", text, re.I)
    if match:
        return date(reference.year, 11, int(match[1]))
    if has(text, r"\btomorrow\b"):
        return reference + timedelta(days=1)
    if has(text, r"\b(tonight|today)\b"):
        return reference
    for index, day in enumerate(('monday','tuesday','wednesday','thursday','friday','saturday','sunday')):
        if has(text, rf"\b{day}\b"):
            offset = (index-reference.weekday()) % 7
            return reference + timedelta(days=offset or 7)
    return None


class ConversationEngine:
    def __init__(self, state, reference):
        self.store = state
        self.svc = state.service
        self.reference = reference
        self.lock = RLock()
        with state.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS conversations (session TEXT PRIMARY KEY, state TEXT)')

    def load(self, session):
        with self.store.connection() as db:
            self.store.require_session(db, session)
            row = db.execute('SELECT state FROM conversations WHERE session=?', (session,)).fetchone()
        return json.loads(row[0]) if row else {'active':None,'domains':{},'history':[]}

    def save(self, session, state):
        with self.store.connection() as db:
            db.execute('INSERT INTO conversations VALUES (?,?) ON CONFLICT(session) DO UPDATE SET state=excluded.state', (session,json.dumps(state)))

    def classify(self, text, state):
        domains = []
        if has(text,r'\b(flights?|fly|airlines?|baggage|direct|nonstop)\b'):
            domains.append('Flights')
        if has(text,r'\b(shopping|products?|laptops?|headphones?|earbuds?|backpacks?|smartphones?|phones?|shoes|speakers?|smartwatch|coffee machine|board game|cart|stock)\b'):
            domains.append('Shopping')
        if has(text,r'\b(cinema|cinemas|movies?|films?|playing|showtimes?|sci-fi|comedy|horror|thriller|animation|romance)\b'):
            domains.append('Cinema')
        for cinema in self.svc.catalog.cinemas.all():
            short=cinema.name.removeprefix('CinemaPlus ').casefold()
            if short in text.casefold() and 'Cinema' not in domains:
                domains.append('Cinema')
        for movie in self.svc.catalog.movies.all():
            if movie.title.casefold() in text.casefold() and 'Cinema' not in domains:
                domains.append('Cinema')
        for prefix, domain in [('F','Flights'),('P','Shopping'),('M','Cinema'),('S','Cinema')]:
            if has(text,rf'\b{prefix}\d{{3}}\b') and domain not in domains:
                domains.append(domain)
        if len(domains)>1:
            return 'Mixed', domains
        if domains:
            if domains[0] == state['active'] and has(text,r'\b(first|second|third|cheaper|cheapest|it|one)\b'):
                return 'Follow-up', domains
            return domains[0], domains
        active=state.get('active')
        context=state.get('domains',{}).get(active,{})
        pending=context.get('pending')
        if pending=='destination' and len(text.split())<=4 and not has(text,r'\b(weather|recipe|hello|hi|politics)\b'):
            return 'Follow-up', [active]
        if active and (has(text,r'\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tonight|tomorrow)\b') or has(text,r'\b\d{4}-\d{2}-\d{2}\b') or has(text,r'\b\d{1,2} november\b')):
            return 'Follow-up', [active]
        if state.get('active') and has(text,r'\b(reset filters|clear filters|start over|any color|any brand|any airline|sort by|highest rated|in stock only|after \d|before \d)\b'):
            return 'Follow-up', [state['active']]
        if has(text,r'\b(weather|politics|recipe|football|stocks|translate)\b'):
            return 'Out of scope', []
        if has(text,r'\b(second|first|third|fourth|cheaper|cheapest|expensive|it|one|those|these|book|reserve|compare|details|seats?|saturday|friday|tomorrow|under|black|white|silver)\b'):
            return ('Follow-up', [state['active']]) if state['active'] else ('Ambiguous', [])
        return ('Ambiguous', []) if has(text,r'\b(show|good|something|help|hello|hi)\b') else ('Out of scope', [])

    def split_requests(self, text, domains):
        """Split only conjunctions that introduce a different explicit domain."""
        chunks = re.split(r'\s*(?:;|\band\b|\balso\b)\s*', text, flags=re.I)
        mapped = {domain: [] for domain in domains}
        previous = None
        for chunk in chunks:
            if not chunk.strip():
                continue
            _, found = self.classify(chunk, {'active': None})
            if len(found) == 1:
                previous = found[0]
            if previous in mapped:
                mapped[previous].append(chunk)
            elif chunk.strip():
                return None
        return {domain: ' and '.join(parts) for domain, parts in mapped.items()} if all(mapped.values()) else None

    def turn(self, session, text):
        with self.lock:
            state = self.load(session)
            category, domains = self.classify(text,state)
            parts=[]
            split = self.split_requests(text, domains) if category == 'Mixed' else None
            if category == 'Mixed' and split is None:
                domains = []
                category = 'Ambiguous'
            for domain in domains:
                context = state['domains'].setdefault(domain, {'filters':{},'ids':[],'kind':None})
                try:
                    result=self.handle(session,domain,split[domain] if split else text,context)
                except (ValueError, DomainError) as exc:
                    result={'message':exc.message if isinstance(exc,DomainError) else 'Please provide a valid date or number.', 'items':[]}
                parts.append({'domain':domain,**result})
                state['active']=domain
            if not parts:
                message = ('I can help with flights, shopping and cinema. Which would you like?' if category=='Ambiguous'
                           else 'I can only help with flights, shopping and cinema in this demo.')
            else:
                message='\n\n'.join(f"{p['domain']}: {p['message']}" for p in parts)
            result=jsonable_encoder({'category':category,'message':message,'results':parts})
            state['history'].extend([{'role':'user','content':text},{'role':'assistant',**result}])
            self.save(session,state)
            return result

    def selection(self,text,ctx):
        explicit=re.findall(r'\b[FPMS]\d{3}\b',text.upper())
        if explicit:
            return list(dict.fromkeys(explicit))
        for index,word in enumerate(('first','second','third','fourth')):
            if has(text,rf'\b{word}\b'):
                return ctx['ids'][index:index+1]
        if has(text,r'\b(cheaper|cheapest)\b') and ctx['ids']:
            rows=[self.svc.detail(ctx['kind'],i) for i in ctx['ids']]
            return [min(rows,key=lambda r:r.price_azn).id] if hasattr(rows[0],'price_azn') else []
        if len(ctx['ids'])==1:
            return ctx['ids']
        return []

    def handle(self,session,domain,text,ctx):
        selected=self.selection(text,ctx)
        if has(text,r'\b(reset filters|clear filters|start over)\b'):
            ctx.pop('pending',None)
            ctx['filters'].clear()
            ctx['ids']=[]
            selected=[]
        if domain=='Shopping' and has(text,r'\bcart\b'):
            if has(text,r'\b(add|remove|delete)\b'):
                if len(selected)!=1 or not selected[0].startswith('P'):
                    return {'message':'Which product should I change? Give its ID or position in the last results.', 'items':[]}
                quantity=0 if has(text,r'\b(remove|delete)\b') else self.quantity(text)
                cart=self.store.set_cart(session,selected[0],quantity)
            else:
                cart=self.store.cart(session)
            return {'message':f"Your cart total is {Decimal(cart['total_azn']):.2f} AZN. Adding sets the requested quantity.", 'items':cart['items'], 'cart':cart}
        if has(text,r'\b(book|reserve)\b'):
            allowed='flights' if domain=='Flights' else 'showtimes' if domain=='Cinema' else None
            if not allowed or len(selected)!=1 or not selected[0].startswith('F' if allowed=='flights' else 'S'):
                return {'message':'Please select a specific flight or showtime ID before reserving.', 'items':[]}
            booking=self.store.reserve(session,allowed,selected[0],self.quantity(text),str(uuid4()))
            return {'message':f"Mock reservation confirmed: {booking['reservation_id']}. Total {booking['total_azn']} AZN.", 'items':[booking]}
        if has(text,r'\bcompare\b'):
            explicit_kind = {'F':'flights','P':'products','M':'movies'}.get(selected[0][0]) if selected else None
            if explicit_kind:
                ctx['kind'] = explicit_kind
            ids=selected if len(selected)>1 else ctx['ids'][:2]
            if ctx['kind'] not in ('flights','products','movies') or len(ids)<2:
                return {'message':'Choose two to four flights, products or movies to compare.', 'items':[]}
            return {'message':'Here is the comparison.', 'items':self.encode(self.svc.compare(ctx['kind'],ids))}
        dated = resolve_date(text,self.reference)
        referencing = has(text,r'\b(first|second|third|fourth|cheaper|cheapest|it|one|[FPMS]\d{3})\b')
        if selected and referencing and has(text,r'\b(stock|direct|baggage|details|seats|available)\b') and dated is None:
            kind=ctx['kind']
            expected={'F':'flights','P':'products','M':'movies','S':'showtimes'}
            kind=expected.get(selected[0][0],kind)
            row=self.svc.detail(kind,selected[0])
            if kind in ('flights','showtimes'):
                availability=self.store.availability(kind,row.id)
                return {'message':f"{row.id}: {availability['seats_available']} seats available." + (f' Direct: {row.stops==0}. Baggage: {row.baggage_kg} kg.' if kind=='flights' else ''), 'items':[row.model_dump(mode='json')], 'availability':availability}
            return {'message':f'{row.id}: '+(f'{row.stock} in stock.' if kind=='products' else 'Movie details.'), 'items':[row.model_dump(mode='json')]}
        if referencing and not selected and has(text,r'\b(stock|direct|details|baggage)\b'):
            return {'message':'Which result do you mean? Please give a valid ID or position.', 'items':[]}
        filters=ctx['filters']
        when=resolve_date(text,self.reference)
        if has(text,r'\b(any date|all dates)\b'):
            filters.pop('departure_date',None)
            filters.pop('on_date',None)
        price=re.search(r'\b(?:under|below|up to|max)\s+(\d+(?:\.\d+)?)',text,re.I)
        if price and not has(text,r'\b(?:up to|max)\s+\d+\s+stops?\b'):
            filters['max_price']=price[1]
        if domain=='Flights':
            if when: filters['departure_date']=when.isoformat()
            if ctx.get('pending')=='destination' and not has(text,r'\b(to|from)\b'):
                candidate=text.strip().rstrip('.!?')
                if candidate: filters['destination']=candidate
            cities={}
            for f in self.svc.catalog.flights.all():
                for airport in (f.origin,f.destination):
                    cities[airport.city.casefold()]=airport.city
                    cities[airport.code.casefold()]=airport.code
            for direction in ('from','to'):
                match=re.search(rf'\b{direction}\s+([a-z]+)',text,re.I)
                if match and match[1].casefold() not in cities:
                    return {'message':'No matching route in the official mock data.', 'items':[]}
            for token,value in cities.items():
                if has(text,rf'\bfrom\s+{re.escape(token)}\b'): filters['origin']=value
                if has(text,rf'\bto\s+{re.escape(token)}\b'): filters['destination']=value
            if not filters.get('destination'):
                ctx['pending']='destination'
                return {'message':'Which destination city or airport would you like to fly to?', 'items':[], 'filters':dict(filters)}
            known={a.casefold() for a in cities}
            if filters['destination'].casefold() not in known:
                ctx['pending']='destination'
                return {'message':'That destination is not in the official mock data. Which destination city or airport would you like?', 'items':[]}
            if not filters.get('departure_date'):
                ctx['pending']='date'
                return {'message':'What departure date would you like? For example, Friday or 2026-11-06.', 'items':[], 'filters':dict(filters)}
            ctx.pop('pending',None)
            if has(text,r'\b(direct|nonstop)\b'): filters['max_stops']=0
            stops=re.search(r'\b(?:up to|max(?:imum)?)\s+(\d+)\s+stops?\b',text,re.I)
            if stops: filters['max_stops']=int(stops[1])
            if has(text,r'\b(any stops|all stops)\b'): filters.pop('max_stops',None)
            for label,field in [('after','departure_after'),('before','departure_before')]:
                clock=re.search(rf'\b{label}\s+(\d{{1,2}}:\d{{2}})\b',text,re.I)
                if clock: filters[field]=time.fromisoformat(clock[1]).isoformat()
            for airline in {f.airline for f in self.svc.catalog.flights.all()}:
                if airline.casefold() in text.casefold(): filters['airline']=airline
            if has(text,r'\b(any airline|all airlines)\b'): filters.pop('airline',None)
            args=dict(filters)
            for field in ('departure_after','departure_before'):
                if field in args: args[field]=time.fromisoformat(args[field])
            if 'departure_date' in args: args['departure_date']=date.fromisoformat(args['departure_date'])
            if 'max_price' in args: args['max_price']=Decimal(args['max_price'])
            if has(text,r'\b(fastest|shortest)\b'): filters['sort']='duration'
            if has(text,r'\b(cheapest|sort by price)\b'): filters['sort']='price'
            if has(text,r'\b(earliest|sort by departure)\b'): filters['sort']='departure'
            args['sort']=filters.get('sort','price')
            rows=self.svc.flights(**args)
            rows=[{**f.model_dump(mode='json'),**self.store.availability('flights',f.id)} for f in rows]
            kind='flights'
        elif domain=='Shopping':
            words=('laptop','headphone','earbud','backpack','smartphone','phone','shoes','speaker','smartwatch','coffee machine','board game')
            for word in words:
                if has(text,rf'\b{word}s?\b'):
                    if filters.get('query')!=word: filters.clear()
                    filters['query']='smartphone' if word=='phone' else word
                    if word=='laptop': filters['category']='computers'
                    if price: filters['max_price']=price[1]
                    break
            for field in ('brand','category','color'):
                for value in {getattr(p,field) for p in self.svc.catalog.products.all()}:
                    if has(text,rf'\b{re.escape(value)}\b'): filters[field]=value
            if has(text,r'\bin stock\b'): filters['in_stock']=True
            if has(text,r'\b(include out of stock|all stock)\b'): filters.pop('in_stock',None)
            for field in ('brand','color'):
                if has(text,rf'\bany {field}\b'): filters.pop(field,None)
            if has(text,r'\b(highest rated|sort by rating)\b'):
                filters.update(sort='rating',descending=True)
            if has(text,r'\b(cheapest|sort by price)\b'):
                filters.update(sort='price',descending=False)
            if has(text,r'\bsort by name\b'): filters.update(sort='name',descending=False)
            args=dict(filters)
            if 'max_price' in args: args['max_price']=Decimal(args['max_price'])
            rows=self.svc.products(**args)
            kind='products'
        else:
            if when: filters['on_date']=when.isoformat()
            cinema_ids=[]
            for cinema in self.svc.catalog.cinemas.all():
                if cinema.name.removeprefix('CinemaPlus ').casefold() in text.casefold():
                    cinema_ids.append(cinema.id)
            if cinema_ids: filters['cinema_ids']=cinema_ids
            if has(text,r'\b(any cinema|all cinemas)\b'): filters.pop('cinema_ids',None)
            if has(text,r'\b(list cinemas|show cinemas|which cinemas)\b'):
                cinemas=self.svc.cinemas()
                return {'message':f'Found {len(cinemas)} cinemas.', 'items':self.encode(cinemas)}
            clock=re.search(r'\bafter\s+(\d{1,2}:\d{2})\b',text,re.I)
            if clock: filters['after']=time.fromisoformat(clock[1]).isoformat()
            for genre in {g for m in self.svc.catalog.movies.all() for g in m.genre}:
                if has(text,rf'\b{re.escape(genre)}\b'):
                    filters.pop('query',None)
                    filters['genre']=genre
            for m in self.svc.catalog.movies.all():
                if m.title.casefold() in text.casefold():
                    filters.pop('genre',None)
                    filters['query']=m.title
            if has(text,r'\b(all movies|any movie)\b'):
                filters.pop('query',None)
                filters.pop('genre',None)
            for language in {m.language for m in self.svc.catalog.movies.all()}:
                if has(text,rf'\b{language}\b'): filters['language']=language
            rating=re.search(r'\brating\s+(?:above|over|at least)\s+(\d+(?:\.\d+)?)',text,re.I)
            if rating: filters['min_rating']=float(rating[1])
            args={k:v for k,v in filters.items() if k in ('query','genre','language','min_rating','on_date')}
            if 'on_date' in args: args['on_date']=date.fromisoformat(args['on_date'])
            movies=self.svc.movies(**args)
            if has(text,r'\b(seats?|showtimes?|playing|tonight|book)\b') or filters.get('cinema_ids') or filters.get('after'):
                rows=[]
                for m in movies:
                    for entry in self.svc.showtimes(movie_id=m.id,on_date=args.get('on_date'),after=time.fromisoformat(filters['after']) if filters.get('after') else None):
                        s=entry['showtime']
                        if filters.get('cinema_ids') and s.cinema_id not in filters['cinema_ids']: continue
                        if filters.get('max_price') and s.price_azn>Decimal(filters['max_price']): continue
                        rows.append({**s.model_dump(mode='json'),'movie_title':m.title,'cinema_name':entry['cinema'].name,**self.store.availability('showtimes',s.id)})
                kind='showtimes'
            else:
                rows=movies
                kind='movies'
        items=self.encode(rows)
        ctx.update(ids=[r['id'] for r in items],kind=kind)
        ambiguous_movie = domain == 'Cinema' and has(text,r'\bone\b') and len(movies)>1
        message = 'More than one movie matches. Please choose a movie title or showtime ID.' if ambiguous_movie else f'Found {len(items)} results.'
        return {'message':message if items else 'No matching results in the official mock data. Try another date or filter.', 'items':items, 'filters':dict(filters)}

    @staticmethod
    def quantity(text):
        match=re.search(r'(?<!\w)(-?\d+)\s+(?:seats?|tickets?|items?|units?)\b',text,re.I)
        if match:
            value=int(match[1])
            if value < 1: raise DomainError('Quantity must be positive.')
            return value
        for word,n in [('two',2),('three',3),('four',4)]:
            if has(text,rf'\b{word}\b'): return n
        return 1

    @staticmethod
    def encode(rows):
        return [r.model_dump(mode='json') if hasattr(r,'model_dump') else r for r in rows]


class ChatMessage(BaseModel):
    message: str = Field(min_length=1,max_length=2000)


@router.post('/{session_id}/chat')
def chat(session_id: str, body: ChatMessage, state=Depends(store)):
    if not body.message.strip():
        raise DomainError('Please enter a message.')
    return state.chat.turn(session_id,body.message)


@router.get('/{session_id}/history')
def history(session_id: str, state=Depends(store)):
    with state.chat.lock:
        return state.chat.load(session_id)['history']
