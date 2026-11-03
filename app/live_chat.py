"""Read-only Phase 2 conversation over scraped snapshots, isolated from mocks."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import logging
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.llm_chat import LLMConversation, PlannerError, TurnPlan, Action
from app.live_sources import load_snapshot, SourceError
from app.flight_source import FlightSourceError, CITY_CODES
from app.flight_providers import get_flight_provider
from app.services.catalog_service import normalized, DomainError


def baku_today():
    """Use Baku time even on Windows installations without an IANA database."""
    try:
        local_zone = ZoneInfo('Asia/Baku')
    except ZoneInfoNotFoundError:
        # Current Baku civil time is UTC+4, without seasonal clock changes.
        local_zone = timezone(timedelta(hours=4))
    return datetime.now(local_zone).date().isoformat()


PRODUCT_TYPES = {
    'laptop': ('laptop', 'laptops', 'notebook', 'notebooks', 'noutbuk', 'noutbuklar', 'noutbuklari', 'notbuk', 'komputer', 'komputerler', 'komputerleri', 'computer', 'computers', 'компьютер', 'компьютеры', 'ноутбук', 'ноутбуки'),
    'headphone': ('headphone', 'headphones', 'qulaqliq', 'qulaqliqlar', 'qulaqliqlari', 'nausnik', 'наушники'),
    'smartphone': ('smartphone', 'smartphones', 'telefon', 'telefonlar', 'telefonlari', 'smartfon', 'смартфоны'),
}
PRODUCT_TERMS = {
    'laptop': ('noutbuk', 'notbuk', 'laptop', 'notebook', 'ноутбук'),
    'headphone': ('qulaq', 'headphone', 'earbud', 'науш'),
    'smartphone': ('telefon', 'smartfon', 'smartphone', 'смартфон'),
}


def product_type(query):
    query = normalized(query).strip()
    return next((kind for kind, aliases in PRODUCT_TYPES.items() if query in aliases), None)


COLOR_ALIASES = {
    'black': ('black','qara','черный','чёрный'),
    'white': ('white','ag','белый'),
    'silver': ('silver','gumusu','серебристый'),
    'gray': ('gray','grey','boz','серый'),
    'blue': ('blue','mavi','синий'),
    'red': ('red','qirmizi','красный'),
    'green': ('green','yasil','зеленый','зелёный'),
}


def canonical_color(value):
    text=normalized(value or '')
    return next((color for color,aliases in COLOR_ALIASES.items() if text in {normalized(alias) for alias in aliases}),text)


def color_matches(published,requested):
    if not published:return False
    requested=canonical_color(requested)
    text=normalized(published)
    # Match whole color terms, so "black" never matches an unrelated brand.
    terms={canonical_color(t) for t in re.findall(r'\w+',text)}
    return requested==canonical_color(published) or requested in terms


def simple_product_request(query):
    """A complete AZ color/type/range search starts fresh, including after no hits."""
    match=re.fullmatch(
        r'(?:(?P<color>qara|ag|gumusu|boz|mavi|qirmizi|yasil)(?: reng(?:de)?)? )?'
        r'(?:qiymet(?:i)? )?'
        r'(?:(?P<low>\d+(?:[.,]\d+)?)\s*(?:ve|ile|-)\s*(?P<high>\d+(?:[.,]\d+)?) '
        r'(?:azn )?(?:araliginda|arasinda)(?: olan)? )?'
        r'(?P<type>komputer(?:ler|leri)?|noutbuk(?:lar|lari)?|notbuk|qulaqliq(?:lar|lari)?|telefon(?:lar|lari)?)'
        r'(?: goster)?',query)
    if not match:return None
    kind=product_type(match['type'])
    if not kind:return None
    args={'query':kind,'sort':'price'}
    if match['color']:args['color']=canonical_color(match['color'])
    if match['low']:args.update(min_price=match['low'].replace(',','.'),max_price=match['high'].replace(',','.'))
    return args


def product_filters(args):
    """Normalize product-type aliases without discarding brands or model terms."""
    args=dict(args)
    category=normalized(args.get('category') or '')
    kind=product_type(category)
    if kind:
        args['category']={'laptop':'computers','headphone':'audio','smartphone':'phones'}[kind]
        args.setdefault('query',kind)
    if args.get('color'):args['color']=canonical_color(args['color'])
    query_kind=product_type(args.get('query') or '')
    if query_kind and not args.get('category'):
        args['category']={'laptop':'computers','headphone':'audio','smartphone':'phones'}[query_kind]
    return args


def live_products(data,args):
    args=product_filters(args)
    query=normalized(args.get('query') or '')
    tokens=query.split()
    def matches(row):
        title=normalized(row['title'])
        # Every query term must match; aliases expand product types, never brands.
        query_ok=all(any(term in title for term in PRODUCT_TERMS.get(product_type(token),(token,))) for token in tokens)
        if args.get('brand') and normalized(args['brand']) not in normalized(row.get('brand') or ''):return False
        if args.get('color') and not color_matches(row.get('color'),args['color']):return False
        if args.get('in_stock') and row.get('in_stock') is not True:return False
        return query_ok and (not args.get('category') or args['category']==row.get('category')) and (args.get('min_price') is None or Decimal(row['price_azn'])>=Decimal(str(args['min_price']))) and (args.get('max_price') is None or Decimal(row['price_azn'])<=Decimal(str(args['max_price'])))
    rows=[r for r in data['products'] if matches(r)]
    sorting=args.get('sort','price')
    if sorting=='rating':rows=[r for r in rows if r.get('rating') is not None]
    key=lambda r:Decimal(r['price_azn']) if sorting=='price' else r['rating'] if sorting=='rating' else r['title'].casefold()
    return sorted(rows,key=key,reverse=args.get('descending',False))


def read_plan(tool=None,args=None,category='Follow-up',message=''):
    return TurnPlan(category=category,clarification=message,
        actions=[] if tool is None else [Action(tool=tool,arguments=json.dumps(args or {},default=str))])


def price_change(query):
    """Parse complete, price-only refinements; other requests stay with the planner."""
    number=r'(\d+(?:[.,]\d+)?)'
    patterns=[
        ('max_price',True,rf'(?:yalniz )?{number}\s*(?:azn|manat)(?:-?den| dan)? ucuz(?: olanlari)?(?: goster)?'),
        ('min_price',True,rf'(?:yalniz )?{number}\s*(?:azn|manat)(?:-?den| dan)? baha(?: olanlari)?(?: goster)?'),
        ('max_price',True,rf'(?:show |show only |only )?(?:those |ones )?(?:under|below|cheaper than|less than) {number}(?: azn)?'),
        ('min_price',True,rf'(?:price )?(?:over|above|greater than|greather than|more than) {number}(?: azn)?'),
        ('max_price',True,rf'(?:покажи )?(?:только )?(?:дешевле|меньше) {number}(?: azn| манат)?'),
        ('min_price',True,rf'(?:покажи )?(?:только )?(?:дороже|больше) {number}(?: azn| манат)?'),
        ('max_price',False,rf'(?:at most|up to) {number}(?: azn)?'),
        ('min_price',False,rf'at least {number}(?: azn)?'),
    ]
    for key,strict,pattern in patterns:
        match=re.fullmatch(pattern,query)
        if match:
            value=Decimal(match[1].replace(',','.'))
            if strict:value+=Decimal('0.01') if key=='min_price' else Decimal('-0.01')
            return {key:str(value)}
    match=re.fullmatch(rf'(?:between )?{number}\s*(?:-|and|ile)\s*{number}(?: azn| manat)?(?: arasinda)?(?: olanlari)?(?: goster)?',query)
    if match:return {'min_price':match[1].replace(',','.'),'max_price':match[2].replace(',','.')}
    return None


def obvious_keyboard_input(text):
    """Conservative keyboard-row runs, not a general language classifier."""
    tokens=re.findall(r'[a-z]+',text.lower())
    if not tokens:return bool(text.strip()) and not re.search(r'\w',text)
    if re.search(r'[^a-z\s.!?,]',text.lower()):return False
    rows=('qwertyuiop','asdfghjkl','zxcvbnm')
    return all(len(token)>=4 and any(token in row or token in row[::-1] for row in rows) for token in tokens)


def compact_live_catalog(data,flights,metadata):
    """Keep every searchable ID, omit duplicate specs, images and URL query strings."""
    fields={
        'products':('id','title','category','brand','color','price_azn','in_stock','rating'),
        'showtimes':('id','movie_title','cinema_name','hall','date','time','price_azn'),
        'flights':('id','origin_code','destination_code','departure_local','price_azn','airline','stops','duration_min','baggage_kg','seats_available','price_kind'),
    }
    result={name:[{key:row[key] for key in keys if key in row} for row in (flights if name=='flights' else data[name])] for name,keys in fields.items()}
    result['movie_ids']=LiveConversation.movie_ids(data)
    result['source_status']={k:{field:value for field,value in status.items() if field in ('status','count')} for k,status in data['sources'].items()}
    result['source_status']['flights']=metadata
    return result


def compact_live_domains(domains):
    fields=('kind','filters','ids','search_ids','selected','provider_id')
    return {name:{key:context[key] for key in fields if key in context} for name,context in domains.items()}


class LiveConversation(LLMConversation):
    def __init__(self,state,reference,planner,flight_provider=None):
        super().__init__(state,reference,planner)
        self._flight_provider=flight_provider

    @property
    def flight_provider(self):
        return self._flight_provider if self._flight_provider is not None else get_flight_provider()

    def validate(self,tool,args):
        # Month searches extend the live engine only; Phase 1 stays unchanged.
        month=args.get('departure_month') if isinstance(args,dict) else None
        if tool=='search_flights' and month is not None:
            values=dict(args);values.pop('departure_month')
            if values.get('departure_date'):raise DomainError('Choose either a date or a month.')
            if not isinstance(month,str) or not re.fullmatch(r'\d{4}-\d{2}',month):raise DomainError('Use YYYY-MM for a month.')
            date.fromisoformat(month+'-01')
            values=super().validate(tool,values);values['departure_month']=month
            return values
        return super().validate(tool,args)

    def turn(self,session,text):
        with self.lock:
            state=self.load(session);state['language']=self.detect_language(text) or state.get('language','en')
            if state['language'] not in ('az','ru','en'):state['language']='en'
            try:
                data=load_snapshot()
                today=baku_today()
                metadata=self.flight_provider.metadata()
                prior=state['domains'].get('Flights',{})
                prior_provider=prior.get('provider_id') or ('travelpayouts' if prior.get('records') else None)
                if prior_provider and prior_provider!=metadata['provider']:
                    state['domains'].pop('Flights',None)
                catalog=compact_live_catalog(data,state['domains'].get('Flights',{}).get('records',[]),metadata)
                plan=self.local_read_plan(state,text,data)
                used_planner=plan is None
                planner_started=time.perf_counter()
                if plan is None:plan=self.planner.plan({'reference_date':today,'user_message':text,'response_language':state['language'],
                    'active_domain':state.get('active'),'domain_state':compact_live_domains(state['domains']),
                    'history':self.compact_history(state['history']),'catalog':catalog,
                    'capabilities':{'search_flights':self.flight_provider.configured(),'flight_prices':metadata,'real_booking':False},
                    'data_mode':'scraped','execution_policy':'Current runtime capabilities override prior assistant refusals and scrape-snapshot statuses. An empty catalog.flights only means no flight search has been executed in this session. When flight search is configured, emit search_flights for a flight request; do not claim it is unsupported. Real booking is unsupported, but price search is supported. Use the complete saved search_ids for ordinal/cheapest/most-expensive references, not the last detail card. A new complete product search clears old filters; a short refinement retains the product type and other filters. Never infer in_stock=true unless asked. Greet or clarify ambiguous/unrelated text without replaying old searches. Read-only real-source snapshots. No real purchases or reservations. Flight search uses the selected provider described in capabilities.flight_prices. Supply origin/destination and departure_date OR departure_month (YYYY-MM, only when supports_calendar is true). Respect provider price_kind and published inventory; cached prices are never live confirmations. Airline filters must use IATA codes. Unknown stock/seats/rating/language/genre must stay unknown; use source links. Dates use today in Asia/Baku, not Phase 1 reference date.'})
                planner_seconds=time.perf_counter()-planner_started if used_planner else 0
                execution_started=time.perf_counter()
                parsed=[(a.tool,self.validate(a.tool,json.loads(a.arguments))) for a in plan.actions]
                if len(parsed)>6:raise DomainError('Please limit your request to six operations.')
                parts=[self.execute_live(state,data,tool,args) for tool,args in parsed]
                if parts:message='\n\n'.join(p['message'] for p in parts)
                else:
                    message=plan.clarification or self.say(state,'Uçuş, məhsul, yoxsa kino ilə maraqlanırsınız?','Are you looking for flights, products or cinema?','Вас интересуют рейсы, товары или кино?')
                    language=self.detect_language(message)
                    if language and language!=state['language']:message=self.say(state,'Uçuş, məhsul, yoxsa kino ilə maraqlanırsınız?','Are you looking for flights, products or cinema?','Вас интересуют рейсы, товары или кино?')
                result={'category':plan.category,'message':message,'results':parts,'engine':'llm','data_mode':'scraped','fetched_at':data['fetched_at'],'timings':{'used_planner':used_planner,'planner_seconds':round(planner_seconds,3),'execution_seconds':round(time.perf_counter()-execution_started,3)}}
            except (PlannerError,SourceError,DomainError,ValueError,TypeError,KeyError) as exc:
                if isinstance(exc,PlannerError):
                    message=str(exc)
                    code=re.search(r'GEMINI_[A-Z_0-9]+',message)
                    if not code:message='GEMINI_RESPONSE: '+message
                    elif code[0]=='GEMINI_HTTP_429':message='GEMINI_HTTP_429: '+self.say(state,
                        'Gemini sorğu və ya kvota limitinə çatıb. Bir qədər sonra yenidən sınayın. Sadə məhsul, uçuş və kino axtarışları yerli qaydalarla işləyə bilər.',
                        'Gemini request or quota limit reached. Try again later. Simple product, flight and cinema searches may still work through local rules.',
                        'Достигнут лимит запросов или квота Gemini. Повторите позже. Простые поиски товаров, рейсов и кино могут работать по локальным правилам.')
                    logging.getLogger(__name__).warning('%s',code[0] if code else 'GEMINI_RESPONSE')
                elif isinstance(exc,FlightSourceError):message=self.flight_error(state,str(exc))
                else:
                    code='LIVE_SOURCE_UNAVAILABLE' if isinstance(exc,SourceError) else 'LIVE_SNAPSHOT_FORMAT' if isinstance(exc,KeyError) else 'LIVE_INVALID_REQUEST'
                    logging.getLogger(__name__).warning('%s (%s)',code,type(exc).__name__)
                    message=code+': '+self.say(state,
                        'Mənbə məlumatını və sorğu filtrlərini yoxlayın. Heç bir əməliyyat edilmədi.',
                        'Check the source snapshot and request filters. No action was taken.',
                        'Проверьте данные источника и фильтры запроса. Операции не выполнены.')
                result={'category':'Ambiguous','message':message,'results':[],'engine':'llm','error':True,'data_mode':'scraped'}
            return self.finish(session,state,text,result)

    def local_read_plan(self,state,text,data):
        """Narrow deterministic reads; semantic and mixed requests use Gemini."""
        query=normalized(text).strip(' .!?')
        if obvious_keyboard_input(text):
            return read_plan(category='Ambiguous',message=self.say(state,
                'Sorğunu başa düşmədim. Məhsul, uçuş və ya kino ilə bağlı nə axtardığınızı yazın.',
                'I could not understand that. Please say what product, flight or cinema information you need.',
                'Не удалось понять запрос. Уточните, какой товар, рейс или сеанс вам нужен.'))
        clauses=re.split(r'\s+(?:and|ve|və|и)\s+',query)
        if len(clauses)==2:
            plans=[self.local_read_plan(state,clause,data) for clause in clauses]
            if all(plan is not None and len(plan.actions)==1 and plan.actions[0].tool.startswith('search_') for plan in plans):
                actions=[plan.actions[0] for plan in plans]
                domains={self.domain(action.tool,json.loads(action.arguments)) for action in actions}
                if len(domains)==2:return TurnPlan(category='Mixed',clarification='',actions=actions)
        greetings={'salam','hello','hi','hey','privet','bolsoy privet','привет'}
        smalltalk={'kaqdela','kak dela','kakdela','как дела','how are you','necesen'}
        if query in greetings or query in smalltalk:
            return read_plan(message=self.say(state,'Salam! Uçuş, məhsul və kino ilə bağlı necə kömək edə bilərəm?',
                'Hello! How can I help with flights, products or cinema?',
                'Привет! Чем помочь с перелётами, товарами или кино?'))
        if query in ('yaxsi bir sey goster','show me something good','покажи что-нибудь хорошее'):
            return read_plan(category='Ambiguous',message=self.say(state,'Məhsul, film, yoxsa uçuş axtarırsınız?',
                'Are you looking for products, cinema or flights?','Вас интересуют товары, кино или рейсы?'))
        if query in ('in english','answer in english','ingilisce cavab ver','rusca cavab ver','на русском','azerbaycanca cavab ver'):
            return read_plan(message=self.say(state,'Azərbaycan dilində davam edəcəyəm.','I will continue in English.','Продолжу на русском языке.'))
        flight=self.local_flight_plan(state,query)
        if flight is not None:return flight
        cinema=self.local_cinema_plan(state,query,data)
        if cinema is not None:return cinema
        previous=state['domains'].get('Shopping',{})
        if query in ('evvelki noutbuklari goster','evvelki mehsullari goster','back to products','show previous products','вернись к товарам'):
            if query=='evvelki noutbuklari goster' and product_type(previous.get('filters',{}).get('query') or '')!='laptop':return None
            if previous.get('kind')=='products':return read_plan('search_products',previous.get('filters',{}))
            return read_plan(message=self.say(state,'Əvvəlki məhsul axtarışı yoxdur. Hansı məhsulu istəyirsiniz?',
                'There is no previous product search. Which product would you like?', 'Предыдущего поиска товаров нет. Какой товар нужен?'))
        complete=simple_product_request(query)
        if complete is not None:return read_plan('search_products',complete,category='Shopping')
        noun=re.fullmatch(r'(?:show me |show |find |покажи |показать )?([\w]+)(?: goster)?',query)
        if noun and product_type(noun[1]):
            return read_plan('search_products',{'query':product_type(noun[1]),'sort':'price'},category='Shopping')
        if state.get('active')!='Shopping' or previous.get('kind')!='products':return None
        filters=dict(previous.get('filters',{}))
        if query in ('qiymet ferqi yoxdur','qiymetin ferqi yoxdur','qiymet onemli deyil','qiymet mehdudiyyetini sil','any price',                     'price does not matter','no price limit','цена не важна','любая цена'):
            filters.pop('min_price',None);filters.pop('max_price',None)
            return read_plan('search_products',filters)
        if query in ('reng ferqi yoxdur','rengin ferqi yoxdur','reng onemli deyil','any color','color does not matter','любой цвет'):
            filters.pop('color',None)
            return read_plan('search_products',filters)
        change=price_change(query)
        if change is not None:
            filters.update(change)
            if Decimal(str(filters.get('min_price',0)))>Decimal(str(filters.get('max_price','Infinity'))):
                return read_plan(category='Ambiguous',message=self.say(state,
                    'Qiymət aralığı ziddiyyətlidir. Filtrləri sıfırlayın və ya uyğun aralıq yazın.',
                    'The price bounds conflict. Clear the filters or provide a compatible range.',
                    'Границы цены противоречат друг другу. Сбросьте фильтры или задайте подходящий диапазон.'))
            return read_plan('search_products',filters)
        sorts={'ucuzdan bahaya':False,'ucuzdan bahaya sirala':False,'sort by price ascending':False,'от дешевых к дорогим':False,
               'bahadan ucuza':True,'bahadan ucuza sirala':True,'sort by price descending':True,'от дорогих к дешевым':True}
        if query in sorts:
            filters.update(sort='price',descending=sorts[query])
            return read_plan('search_products',filters)
        if query in ('filtrleri sifirla','clear filters','сбрось фильтры'):
            return read_plan('search_products',{k:v for k,v in filters.items() if k in ('query','category')})
        if query in ('neticeleri goster','show results','покажи результаты'):
            return read_plan('search_products',filters)
        ids=previous.get('search_ids',[])
        records={r['id']:r for r in data['products']}
        rows=[records[i] for i in ids if i in records]
        compare=query in ('ilk iki noutbuku muqayise et','ilk ikisini muqayise et','compare the first two','сравни первые два')
        expensive=re.fullmatch(r'en (?:bahasinin|bahalisinin) detallari(?:ni)?(?: goster)?|(?:show )?details of the most expensive|подробности самого дорогого',query)
        cheapest=re.fullmatch(r'en ucuzunun detallari(?:ni)?(?: goster)?|(?:show )?details of the cheapest|подробности самого дешевого',query)
        ordinal={'birincinin detallari':0,'ikincinin detallari':1,'ucuncunun detallari':2,'sonuncunun detallari':-1,
                 'details of the first':0,'details of the second':1,'details of the last':-1,
                 'подробности первого':0,'подробности второго':1}
        if not (compare or expensive or cheapest or query in ordinal):return None
        if not rows:
            return read_plan(category='Ambiguous',message=self.say(state,'Cari siyahı boşdur. Filtrləri dəyişin.',
                'The current result list is empty. Change the filters.','Текущий список пуст. Измените фильтры.'))
        if compare:
            if len(rows)<2:
                return read_plan(category='Ambiguous',message=self.say(state,'Müqayisə üçün cari siyahıda ən azı iki məhsul olmalıdır.',
                    'The current list needs at least two products for a comparison.', 'Для сравнения в текущем списке нужны хотя бы два товара.'))
            return read_plan('compare',{'domain':'products','ids':[r['id'] for r in rows[:2]]})
        if expensive or cheapest:
            row=(max if expensive else min)(rows,key=lambda r:Decimal(r['price_azn']))
        else:
            index=ordinal[query]
            if index>=len(rows):
                return read_plan(category='Ambiguous',message=self.say(state,'Cari siyahıda həmin sıra nömrəli məhsul yoxdur.',
                    'There is no product at that position in the current list.','В текущем списке нет товара с таким номером.'))
            row=rows[index]
        return read_plan('details',{'domain':'products','record_id':row['id']})

    def local_flight_plan(self,state,query):
        """Route explicit simple searches without model capability guesses."""
        previous=state['domains'].get('Flights',{})
        if state.get('active')=='Flights':
            if query in ('neticeleri goster','show results','покажи результаты') and previous.get('filters'):
                return read_plan('search_flights',previous['filters'])
            refs={'en ucuzunun detallari':'min','en ucuzunun detallarini goster':'min',
                  'en bahasinin detallari':'max','en bahalisinin detallari':'max',
                  'details of the cheapest':'min','details of the most expensive':'max',
                  'en ucuzunun baqaji nece kilodur':'min','en ucuzunun baqaji':'min'}
            if query in refs:
                rows=previous.get('records',[])
                if not rows:return read_plan(message=self.say(state,'Cari uçuş siyahısı boşdur. Əvvəlcə istiqamət və tarixlə axtarın.',
                    'The current flight list is empty. Search by route and date first.','Текущий список рейсов пуст. Сначала задайте маршрут и дату.'))
                row=(min if refs[query]=='min' else max)(rows,key=lambda r:Decimal(r['price_azn']))
                return read_plan('details',{'domain':'flights','record_id':row['id']})
        locations='|'.join(re.escape(name) for name in sorted(set(CITY_CODES)|{'bak','gyd','ist'},key=len,reverse=True))
        route=re.search(rf'(?:from )?(?P<origin>{locations})(?:dan|den)? (?:to )?(?P<destination>{locations})(?:ya|ye|a|e)?',query)
        if not route:return None
        iso=re.search(r'\b(\d{4}-\d{2}-\d{2})\b',query)
        months={'yanvar':1,'fevral':2,'mart':3,'aprel':4,'may':5,'iyun':6,'iyul':7,'avqust':8,'sentyabr':9,'oktyabr':10,'noyabr':11,'dekabr':12,
                'january':1,'february':2,'march':3,'april':4,'june':6,'july':7,'august':8,'september':9,'october':10,'november':11,'december':12}
        names='|'.join(months)
        written=re.search(rf'\b(?P<day>\d{{1,2}}) (?P<month>{names}) (?P<year>\d{{4}})\b',query)
        english=re.search(rf'\b(?P<month>{names}) (?P<day>\d{{1,2}}),? (?P<year>\d{{4}})\b',query)
        month_request=re.search(rf'\b(?P<month>{names}) (?P<year>\d{{4}})(?: ayinda)?\b',query)
        found=iso or written or english or month_request
        if not found:return None
        try:
            monthly=not (iso or written or english)
            day=date(int(found['year']),months[found['month']],1) if monthly else date.fromisoformat(iso[1]) if iso else date(int(found['year']),months[found['month']],int(found['day']))
        except ValueError:return None
        remaining=query
        for match in sorted([route,found],key=lambda m:m.start(),reverse=True):
            remaining=remaining[:match.start()]+' '+remaining[match.end():]
        direct=bool(re.search(r'\b(?:birbasa|direct|nonstop)\b',remaining))
        remaining=re.sub(r'\b(?:ucun|ucuslari|ucus|goster|flights|flight|on|in|please|show|me|find|birbasa|direct|nonstop)\b',' ',remaining)
        # Leave additional filters, mixed requests and unknown constraints to Gemini.
        if remaining.strip(' ,.!?'):return None
        args={'origin':route['origin'],'destination':route['destination']}
        args['departure_month' if monthly else 'departure_date']=day.strftime('%Y-%m') if monthly else day.isoformat()
        if direct:args['max_stops']=0
        return read_plan('search_flights',args,category='Flights')

    def local_cinema_plan(self,state,query,data=None):
        days={'bu gun hansi filmler var':0,'bugun hansi filmler var':0,'sabah hansi filmler var':1,
              'what movies are on today':0,'what movies are on tomorrow':1,
              'какие фильмы сегодня':0,'какие фильмы завтра':1}
        if query in days:
            day=date.fromisoformat(baku_today())+timedelta(days=days[query])
            return read_plan('search_showtimes',{'on_date':day.isoformat()},category='Cinema')
        previous=state['domains'].get('Cinema',{})
        # Match only published venue names; never silently discard extra filters.
        rest=query;venue=None
        for name in sorted({r['cinema_name'] for r in (data or {}).get('showtimes',[])},key=len,reverse=True):
            words=normalized(name).split()
            pattern=r'(?<!\w)'+r'\s*'.join(re.escape(w) for w in words)+r'(?:[- ]?(?:da|de|да))?(?!\w)'
            if re.search(pattern,rest):
                venue=name;rest=re.sub(pattern,' ',rest,count=1);break
        day_match=re.search(r'\b(sabaha|sabah|tomorrow|завтра|bugun|bu gun|today|сегодня)\b',rest)
        offset=None
        if day_match:
            offset=1 if day_match[0] in ('sabaha','sabah','tomorrow','завтра') else 0
            rest=rest[:day_match.start()]+' '+rest[day_match.end():]
        cinema_words=r'\b(?:sinema|cinema|kino|kinoteatr|kinoteatrda|кино|кинотеатр|фильмы|фильмов|сеансы|сеансов|filmler|filmleri|seanslar|seanslari|movies|showtimes|screenings)\b'
        has_cinema=bool(re.search(cinema_words,rest))
        rest=re.sub(cinema_words,' ',rest)
        rest=re.sub(r'\b(?:goster|var|hansi|istəyirəm|isteyirem|show|me|at|in|on|покажи|показать|в|на)\b',' ',rest)
        if not rest.strip(' .!?,-') and (has_cinema or venue or (day_match and state.get('active')=='Cinema')):
            args=dict(previous.get('filters',{})) if state.get('active')=='Cinema' and previous.get('kind')=='showtimes' and not has_cinema and not venue else {}
            if offset is not None:args['on_date']=(date.fromisoformat(baku_today())+timedelta(days=offset)).isoformat()
            elif state.get('active')=='Cinema' and previous.get('kind')=='showtimes':
                args=dict(previous.get('filters',{}))
            if venue:args['cinema_id']=venue
            if 'on_date' not in args:
                return read_plan(category='Cinema',message=self.say(state,'Hansı gün üçün kino seanslarını göstərim?',
                    'Which day would you like cinema showtimes for?', 'На какой день показать сеансы?'))
            return read_plan('search_showtimes',args,category='Cinema')
        if state.get('active')!='Cinema' or previous.get('kind')!='showtimes':return None
        after=re.fullmatch(r'(?:yalniz )?(\d{1,2}):(\d{2})(?:-?dan|den) sonra(?: olanlari)?(?: goster)?|after (\d{1,2}):(\d{2})|после (\d{1,2}):(\d{2})',query)
        if after:
            groups=after.groups();hour,minute=next((groups[i:i+2] for i in (0,2,4) if groups[i] is not None))
            args=dict(previous.get('filters',{}));args['after']=f'{int(hour):02}:{minute}'
            return read_plan('search_showtimes',args)
        return None

    @staticmethod
    def detect_language(text):
        language=LLMConversation.detect_language(text)
        if language:return language
        # Short product/color requests often omit the verbs in the base detector.
        if re.search(r'\b(?:qara|reng\w*|komputer\w*|noutbuk\w*|qiymet\w*)\b',normalized(text)):
            return 'az'
        if re.search(r'\b(?:color|colour|black|white|computer\w*|laptop\w*|headphones|any price)\b',text.lower()):
            return 'en'
        return None

    @staticmethod
    def localize(message,language,error=False):
        if message.startswith(('LIVE_','FLIGHT_')):return message
        return LLMConversation.localize(message,language,error)

    @staticmethod
    def say(state,az,en,ru):return {'az':az,'en':en,'ru':ru}.get(state.get('language'),en)

    @staticmethod
    def movie_ids(data):
        return {'LM_'+sha256(r['movie_title'].encode()).hexdigest()[:12]:r['movie_title'] for r in data['showtimes']}

    @staticmethod
    def flight_error(state,detail):
        code=re.match(r'FLIGHT_[A-Z_0-9]+',detail)
        code=code[0] if code else 'FLIGHT_ERROR'
        specific={
            'FLIGHT_PROVIDER':('Seçilmiş uçuş adapteri qoşulmayıb. Mənbə konfiqurasiyasını yoxlayın.',
                'The selected flight adapter is not installed. Check provider configuration.',
                'Выбранный адаптер рейсов не установлен. Проверьте настройку источника.'),
            'FLIGHT_CAPABILITY':('Bu mənbə ay üzrə axtarışı dəstəkləmir. Dəqiq tarix seçin.',
                'This source does not support a month search. Choose an exact date.',
                'Этот источник не поддерживает поиск по месяцу. Выберите точную дату.'),
            'FLIGHT_CONTRACT':('Uçuş mənbəyinin cavabı yoxlamadan keçmədi; nəticələr göstərilmədi.',
                'Flight-source output failed validation; no results were displayed.',
                'Ответ источника не прошёл проверку; результаты не показаны.'),
        }
        if code in specific:return code+': '+LiveConversation.say(state,*specific[code])
        return code+': '+LiveConversation.say(state,
            'Uçuş mənbəyinin token, giriş, valyuta və şəbəkə vəziyyətini yoxlayın. Heç bir sınaq qiyməti göstərilmədi.',
            'Check flight-provider token, access, currency and network. No mock fare was substituted.',
            'Проверьте токен, доступ, валюту и сеть поставщика. Тестовые цены не подставлены.')

    def execute_flights(self,state,tool,args):
        state['active']='Flights'
        if tool=='reset_filters':
            state['domains'].pop('Flights',None)
            return {'domain':'Flights','message':self.say(state,'Uçuş filtrləri sıfırlandı.','Flight filters cleared.','Фильтры рейсов сброшены.'),'items':[]}
        source=self.flight_provider
        metadata=source.metadata()
        if not source.configured():
            return {'domain':'Flights','message':self.say(state,
                'Seçilmiş uçuş mənbəyi konfiqurasiya edilməyib. Yerli giriş məlumatlarını yoxlayın; sınaq qiyməti göstərilmir.',
                'Flight prices are not connected. Configure the selected provider locally; no mock fares are substituted.',
                'Источник рейсов не настроен. Проверьте локальные настройки; тестовые цены не подставляются.'),
                'items':[{'id':'AVIASALES','title':'Aviasales','source':'Aviasales','source_url':'https://www.aviasales.com/','data_mode':'api_cache'}]}
        previous=state['domains'].get('Flights',{})
        cached=metadata['price_kind']=='cached_search_price'
        notice=self.say(state,
            'Bunlar mənbədə saxlanmış keş qiymətləridir; canlı qiymət, baqaj və boş yer təsdiqi deyil. Mənbə saytda yenidən yoxlayın.',
            'These are cached prices from the source, not live fare, baggage or seat confirmations. Recheck on the source website.',
            'Это сохранённые цены из источника, не подтверждение текущей цены, багажа или мест. Перепроверьте на сайте.')
        if not cached:
            notice=self.say(state,'Qiymətlər mənbənin sorğu vaxtındakı təklifləridir. Alışdan əvvəl qiyməti və şərtləri mənbədə təsdiqləyin.',
                'These are source offers retrieved for this request. Reconfirm price and conditions before purchase.',
                'Это предложения источника на момент запроса. Перед покупкой подтвердите цену и условия.')
        if tool=='search_flights':
            if not all(args.get(k) for k in ('origin','destination')) or not (args.get('departure_date') or args.get('departure_month')):
                return {'domain':'Flights','message':self.say(state,'Çıxış şəhəri, istiqamət və dəqiq gediş tarixini yazın.',
                    'Please specify origin, destination and an exact departure date.', 'Укажите город вылета, направление и точную дату.'),'items':[]}
            rows=source.search(args)
            state['domains']['Flights']={'kind':'flights','provider_id':metadata['provider'],'filters':json.loads(json.dumps(args,default=str)),
                'ids':[r['id'] for r in rows],'search_ids':[r['id'] for r in rows],'records':rows,'recent_results':rows}
            message=(self.say(state,f'{len(rows)} keş qiyməti tapıldı. ',f'{len(rows)} cached prices found. ',f'Найдено сохранённых цен: {len(rows)}. ') if cached else self.say(state,f'{len(rows)} mənbə təklifi tapıldı. ',f'{len(rows)} source offers found. ',f'Найдено предложений: {len(rows)}. '))+notice
            if args.get('departure_month') and len({r['departure_local'][:10] for r in rows})>1:
                message+=self.say(state,' Tarixlər fərqlidir; hər nəticənin gediş tarixini ayrıca müqayisə edin.',
                    ' Departure dates differ; compare each result by its displayed date.',
                    ' Даты вылета различаются; сравнивайте результаты по указанным датам.')
            if not rows:message+=self.say(state,' Mənbədə nəticə tapılmaması uçuşun olmadığı demək deyil.',
                ' No source result does not prove that no flight exists.', ' Отсутствие результата не доказывает отсутствие рейса.')
            return {'domain':'Flights','message':message,'items':rows}
        ids=args.get('ids',[args.get('record_id')])
        records={r['id']:r for r in previous.get('records',[])}
        if not ids or any(i not in records for i in ids):
            return {'domain':'Flights','message':self.say(state,'Əvvəlcə real mənbədə uçuş axtarın və nəticə ID-sini seçin.',
                'Search the flight source first and choose a returned ID.', 'Сначала выполните поиск рейсов и выберите ID результата.'),'items':[]}
        if tool=='compare' and (not 2<=len(ids)<=4 or len(set(ids))!=len(ids)):raise DomainError('Choose two to four distinct flight records.')
        rows=[records[i] for i in ids]
        if tool in ('reserve','availability'):
            message=self.say(state,'Burada rezervasiya edilmir; dərc edilməyən baqaj və yer sayı bilinmir. Mənbə keçidindən yoxlayın. ',
                'No booking is made here; unpublished baggage and seat counts remain unknown. Check the source link. ',
                'Здесь бронирование не производится; багаж и число мест API не подтверждает. Проверьте по ссылке. ')+notice
        else:
            message='\n'.join(f"{r['title']} · {r['departure_local']} · {r['price_azn']} AZN" for r in rows)+'\n'+notice
        previous.update(ids=ids,recent_results=rows)
        if tool=='details':previous['selected']=ids[0]
        state['domains']['Flights']=previous
        return {'domain':'Flights','message':message,'items':rows}

    def execute_live(self,state,data,tool,args):
        domain=self.domain(tool,args);items=[]
        if tool=='search_flights' or args.get('domain')=='flights' or (tool=='reset_filters' and args.get('domain')=='Flights'):return self.execute_flights(state,tool,args)
        movies=self.movie_ids(data)
        records={r['id']:r for r in data['products']+data['showtimes']}
        for mid,title in movies.items():
            row=next(r for r in data['showtimes'] if r['movie_title']==title)
            records[mid]={'id':mid,'title':title,'source':row['source'],'source_url':row['source_url'],'fetched_at':row['fetched_at'],'data_mode':'scraped'}
        if tool=='search_products':
            args=product_filters(args)
            category=args.get('category')
            relevant=[v for k,v in data['sources'].items() if k.startswith(('birmarket_','compzone_')) and (not category or k.endswith('_'+category))]
            if relevant and all(r.get('status')=='unavailable' for r in relevant):raise SourceError('Shopping source collection failed; no reliable results are available.')
            items=live_products(data,args)
        elif tool in ('search_showtimes','search_movies','search_cinemas'):
            if data['sources'].get('cinema',{}).get('status')=='unavailable':raise SourceError('Cinema source collection failed; no reliable results are available.')
            rows=data['showtimes']
            if any(args.get(k) is not None for k in ('genre','language','min_rating','district')):
                return {'domain':'Cinema','message':self.say(state,'Bu mənbədə janr, reytinq, film dili və rayon yoxlanmayıb. Tarix və film adı ilə axtarın.','Genre, rating, film language and district have not been verified for this source. Search by date or title.','Жанр, рейтинг, язык фильмов и район не проверены для этого источника. Ищите по дате или названию.'),'items':[]}
            if args.get('on_date'):rows=[r for r in rows if r['date']==args['on_date'].isoformat()]
            if args.get('after'):rows=[r for r in rows if r['time']>=args['after'].strftime('%H:%M')]
            if args.get('query'):rows=[r for r in rows if normalized(args['query']) in normalized(r['movie_title'] if tool!='search_cinemas' else r['cinema_name'])]
            if args.get('movie_id'):rows=[r for r in rows if r['movie_title']==movies.get(args['movie_id'])]
            if args.get('cinema_id'):rows=[r for r in rows if args['cinema_id']==r['cinema_name']]
            if tool=='search_movies':
                seen=set()
                for r in rows:
                    if r['movie_title'] in seen:continue
                    seen.add(r['movie_title']);mid=next(k for k,v in movies.items() if v==r['movie_title'])
                    items.append({'id':mid,'title':r['movie_title'],'source_url':r['source_url'],'source':r['source'],'fetched_at':r['fetched_at'],'data_mode':'scraped'})
            elif tool=='search_cinemas':
                unique={r['cinema_name']:r for r in rows}
                items=[{'id':name,'title':name,'source_url':r['source_url'],'source':r['source'],'fetched_at':r['fetched_at'],'data_mode':'scraped'} for name,r in unique.items()]
            else:items=rows
        elif tool in ('details','compare','availability','reserve','set_cart'):
            ids=args.get('ids',[args.get('record_id',args.get('product_id'))])
            if tool=='compare' and (not 2<=len(ids)<=4 or len(set(ids))!=len(ids)):
                raise DomainError('Choose two to four distinct records for comparison.')
            if any(i not in records for i in ids):raise DomainError('Record not found in the scraped snapshot.')
            items=[records[i] for i in ids]
            requested=args.get('domain')
            allowed={'products':'BM_','showtimes':'CP_','movies':'LM_'}
            if requested in allowed and any(not i.startswith(allowed[requested]) for i in ids):
                raise DomainError('The record ID does not match the requested domain.')
            if tool in ('reserve','set_cart'):
                return {'domain':domain,'message':self.say(state,'Alış və rezervasiya mənbə saytda tamamlanır. Burada əməliyyat edilmədi; keçiddən istifadə edin.','Complete purchases and reservations on the source website. No transaction was made here; use the source link.','Покупки и бронирования оформляются на сайте источника. Здесь операция не выполнена; воспользуйтесь ссылкой.'),'items':items}
            if tool=='availability':
                return {'domain':domain,'message':self.say(state,'Mənbə dəqiq stok və ya boş yer sayı vermir. Cari vəziyyəti keçiddən yoxlayın.','The source does not provide exact stock or seat counts. Check current availability on its website.','Источник не сообщает точный остаток или число мест. Проверьте наличие на его сайте.'),'items':items}
        elif tool in ('cart','reservations'):
            return {'domain':domain,'message':self.say(state,'Bu rejimdə alış və rezervasiya mənbə saytda edilir; burada real sifariş qeydi yoxdur.','Purchases and reservations are completed on source websites; no real order is recorded here.','В этом режиме покупки и бронирования оформляются на сайтах источников; реальных заказов здесь нет.'),'items':[]}
        elif tool=='reset_filters':state['domains'].pop(args['domain'],None)
        else:raise DomainError('This operation is not supported by live sources.')
        if domain in ('Flights','Shopping','Cinema'):
            state['active']=domain
            previous=state['domains'].get(domain,{})
            if tool.startswith('search_'):
                state['domains'][domain]={'kind':tool.removeprefix('search_'),'filters':json.loads(json.dumps(args,default=str)),
                    'ids':[r['id'] for r in items],'search_ids':[r['id'] for r in items],'recent_results':items}
            else:
                previous.update(ids=[r['id'] for r in items],recent_results=items)
                if tool=='details' and items:previous['selected']=items[0]['id']
                state['domains'][domain]=previous
        message=self.say(state,f'Mənbədən toplanmış {len(items)} nəticə. Qiymət və mövcudluğu mənbə saytda yenidən yoxlayın.',f'{len(items)} scraped results. Recheck prices and availability on the source website.',f'Результатов из источника: {len(items)}. Перепроверьте цены и наличие на сайте источника.')
        if tool=='search_products' and args.get('max_price') is not None:
            bound=Decimal(str(args['max_price']))
            message+=self.say(state,f' Tətbiq olunan yuxarı qiymət həddi: {bound:.2f} AZN.',f' Applied maximum price: {bound:.2f} AZN.',f' Верхняя граница цены: {bound:.2f} AZN.')
        if tool.startswith('search_') and not items:
            message=self.say(state,'Toplanmış mənbə məlumatında bu filtrlərə uyğun nəticə yoxdur. Bu, bütün saytda seçim olmadığı demək deyil.',
                'No matches for these filters in the collected snapshot. This does not mean the entire website has no options.',
                'В собранном снимке нет совпадений с этими фильтрами. Это не означает отсутствие вариантов на всём сайте.')
            if tool=='search_showtimes':
                dates=sorted({r['date'] for r in data['showtimes']})
                if dates:
                    message+=self.say(state,' Toplanmış tarixlər: ',' Collected dates: ',' Собранные даты: ')+', '.join(dates)+'.'
        if tool=='search_products' and args.get('color'):
            without_color={k:v for k,v in args.items() if k!='color'}
            candidates=live_products(data,without_color)
            unknown=sum(not row.get('color') for row in candidates)
            if unknown:
                message+=self.say(state,
                    f' Digər filtrlərə uyğun {unknown} məhsulun rəngi mənbədə göstərilməyib; onları istənən rəng kimi təsdiqləmək olmur. Rəngi nəzərə almamaq üçün “rəng fərqi yoxdur” yazın.',
                    f' Color is unpublished for {unknown} products matching the other filters; they cannot be confirmed as the requested color. Say “any color” to remove the color filter.',
                    f' У {unknown} товаров, подходящих по остальным фильтрам, цвет не опубликован; подтвердить нужный цвет нельзя. Напишите «любой цвет», чтобы убрать фильтр цвета.')
        if tool=='search_products' and args.get('min_price') is None and args.get('max_price') is None:
            message+=self.say(state,' Qiymət məhdudiyyəti tətbiq edilmir.', ' No price limit is applied.', ' Ограничений по цене нет.')
        if tool=='compare' and args.get('domain')=='products' and len(items)>=2:
            lines=[f"{r['id']}: {r['title']} — {r['price_azn']} AZN" for r in items]
            difference=max(Decimal(r['price_azn']) for r in items)-min(Decimal(r['price_azn']) for r in items)
            message='\n'.join(lines)+self.say(state,f'\nQiymət fərqi: {difference:.2f} AZN. Dərc olunmayan xüsusiyyətlər müqayisə edilmir.',
                f'\nPrice difference: {difference:.2f} AZN. Unpublished specifications are not compared.',
                f'\nРазница в цене: {difference:.2f} AZN. Неопубликованные характеристики не сравниваются.')
        if tool=='details' and args.get('domain')=='products' and items:
            row=items[0]
            message=self.say(state,f"{row['title']} — {row['price_azn']} AZN. Dəqiq stok sayı mənbədə göstərilmir.",f"{row['title']} — {row['price_azn']} AZN. Exact stock count is not published.",f"{row['title']} — {row['price_azn']} AZN. Точный остаток не опубликован.")
        if tool=='details' and args.get('domain')=='products' and items:
            specs=items[0].get('specifications',{})
            if specs:message+='\n'+'\n'.join(f'{key}: {value}' for key,value in list(specs.items())[:35])
        stale=sum(bool(r.get('stale')) for r in items)
        if stale:
            message+=self.say(state,' Mənbə yenilənmədiyi üçün əvvəlki məlumat göstərilir; qiyməti keçiddən yoxlayın.',
                'Older records are shown because source refresh failed; check the price at the source.',
                'Показаны прежние записи: обновление источника не удалось. Проверьте цену на сайте.')
        return {'domain':domain,'message':message,'items':items}
