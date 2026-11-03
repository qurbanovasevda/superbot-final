from datetime import date, time
from decimal import Decimal
import unicodedata

from app.repositories.catalog import Catalog


class DomainError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status
        super().__init__(message)


def normalized(value):
    value=value.casefold().replace('ı','i').replace('ə','e')
    return ''.join(c for c in unicodedata.normalize('NFD',value) if not unicodedata.combining(c))


def contains(value: str, query: str | None) -> bool:
    return query is None or normalized(query) in normalized(value)


class CatalogService:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def detail(self, domain: str, record_id: str):
        record = getattr(self.catalog, domain).get(record_id)
        if record is None:
            raise DomainError("Record not found.", 404)
        return record

    def compare(self, domain: str, ids: list[str]):
        if not 2 <= len(ids) <= 4 or len(set(ids)) != len(ids):
            raise DomainError("Choose two to four distinct records for comparison.")
        return [self.detail(domain, record_id) for record_id in ids]

    def flights(self, origin=None, destination=None, departure_date: date | None = None,
                min_price: Decimal | None = None, max_price: Decimal | None = None,
                max_stops: int | None = None, airline=None, departure_after: time | None = None,
                departure_before: time | None = None, sort="price", descending=False):
        rows = [f for f in self.catalog.flights.all()
                if (contains(f.origin.code, origin) or contains(f.origin.city, origin))
                and (contains(f.destination.code, destination) or contains(f.destination.city, destination))
                and (departure_date is None or f.departure_local.date() == departure_date)
                and (min_price is None or f.price_azn >= min_price)
                and (max_price is None or f.price_azn <= max_price)
                and (max_stops is None or f.stops <= max_stops)
                and contains(f.airline, airline)
                and (departure_after is None or f.departure_local.time() >= departure_after)
                and (departure_before is None or f.departure_local.time() <= departure_before)]
        keys = {"price": "price_azn", "duration": "duration_min", "departure": "departure_local", "stops": "stops", "airline": "airline"}
        return sorted(rows, key=lambda f: (f.airline.casefold() if sort == "airline" else getattr(f, keys[sort]), f.id), reverse=descending)

    def products(self, query=None, category=None, brand=None, color=None,
                 min_price=None, max_price=None, in_stock=False, sort="price", descending=False):
        # Canonical attributes remain English, while names retain all official
        # languages. Accept common translated attribute values at the boundary.
        colors={'qara':'black','ag':'white','gumusu':'silver','чёрный':'black','черный':'black','белый':'white','серебристый':'silver'}
        color=colors.get(normalized(color),color) if color else color
        categories={'noutbuk':('computers','laptop'),'ноутбук':('computers','ноутбук'),
                    'laptops':('computers','laptop'),'headphones':('audio','headphone'),
                    'qulaqliq':('audio','qulaqliq'),'qulaqciq':('audio','qulaqciq'),
                    'наушники':('audio','наушники')}
        if category and normalized(category) in categories:
            category,term=categories[normalized(category)]
            if not query:query=term
        headphone_aliases={'nausnik','nausniki','naushnik','naushniki','наушники','наушник','headphones','qulaqliq'}
        def matches(product):
            names=" ".join((product.name.en,product.name.az,product.name.ru))
            if query and normalized(query) in headphone_aliases:
                # Headphone/earbud family; speakers are separate audio products.
                return any(contains(names,term) for term in ('headphone','earbud','наушник','вкладыш','qulaqliq','qulaqciq'))
            return contains(names+" "+product.description,query)
        rows = [p for p in self.catalog.products.all()
                if matches(p)
                and contains(p.category, category) and contains(p.brand, brand)
                and contains(p.color, color)
                and (min_price is None or p.price_azn >= min_price)
                and (max_price is None or p.price_azn <= max_price)
                and (not in_stock or p.stock > 0)]
        keys = {"price": "price_azn", "rating": "rating", "name": "id"}
        return sorted(rows, key=lambda p: (p.name.en.casefold() if sort == "name" else getattr(p, keys[sort]), p.id), reverse=descending)

    def movies(self, query=None, genre=None, language=None, min_rating=None, on_date=None):
        movie_ids = None if on_date is None else {s.movie_id for s in self.catalog.showtimes.all() if s.date == on_date}
        return [m for m in self.catalog.movies.all() if contains(m.title, query)
                and any(contains(g, genre) for g in m.genre) and contains(m.language, language)
                and (min_rating is None or m.rating >= min_rating)
                and (movie_ids is None or m.id in movie_ids)]

    def cinemas(self, query=None, district=None):
        return [c for c in self.catalog.cinemas.all() if contains(c.name, query) and contains(c.district, district)]

    def showtimes(self, movie_id=None, cinema_id=None, on_date=None, after=None):
        return [{"showtime": s, "movie": self.detail("movies", s.movie_id),
                 "cinema": self.detail("cinemas", s.cinema_id)}
                for s in sorted(self.catalog.showtimes.all(), key=lambda s: (s.date, s.time, s.id))
                if (movie_id is None or s.movie_id == movie_id)
                and (cinema_id is None or s.cinema_id == cinema_id)
                and (on_date is None or s.date == on_date) and (after is None or s.time >= after)]
