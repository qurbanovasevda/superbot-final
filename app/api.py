from datetime import date, time
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.services.catalog_service import DomainError

router = APIRouter(prefix="/api")
MoneyQuery = Annotated[Decimal | None, Query(ge=0)]


def service(request: Request):
    if request.app.state.catalog is None:
        raise DomainError("Mock datasets are unavailable.", 503)
    return request.app.state.service


def store(request: Request, unused=Depends(service)):
    return request.app.state.store


@router.get("/flights")
def flights(origin: str | None = None, destination: str | None = None,
            departure_date: date | None = None, min_price: MoneyQuery = None,
            max_price: MoneyQuery = None, max_stops: Annotated[int | None, Query(ge=0)] = None,
            airline: str | None = None, departure_after: time | None = None,
            departure_before: time | None = None,
            sort: Literal["price", "duration", "departure", "stops", "airline"] = "price",
            descending: bool = False, svc=Depends(service), state=Depends(store)):
    rows = svc.flights(origin, destination, departure_date, min_price, max_price,
                       max_stops, airline, departure_after, departure_before, sort, descending)
    return [current_record(row, "flights", state) for row in rows]


@router.get("/products")
def products(query: str | None = None, category: str | None = None,
             brand: str | None = None, color: str | None = None,
             min_price: MoneyQuery = None, max_price: MoneyQuery = None,
             in_stock: bool = False, sort: Literal["price", "rating", "name"] = "price",
             descending: bool = False, svc=Depends(service)):
    return svc.products(query, category, brand, color, min_price, max_price, in_stock, sort, descending)


@router.get("/movies")
def movies(query: str | None = None, genre: str | None = None, language: str | None = None,
           min_rating: Annotated[float | None, Query(ge=0, le=10)] = None,
           on_date: date | None = None, svc=Depends(service)):
    return svc.movies(query, genre, language, min_rating, on_date)


@router.get("/cinemas")
def cinemas(query: str | None = None, district: str | None = None, svc=Depends(service)):
    return svc.cinemas(query, district)


@router.get("/showtimes")
def showtimes(movie_id: str | None = None, cinema_id: str | None = None,
              on_date: date | None = None, after: time | None = None, svc=Depends(service), state=Depends(store)):
    return [{**row, "showtime": current_record(row["showtime"], "showtimes", state)}
            for row in svc.showtimes(movie_id, cinema_id, on_date, after)]


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=2, max_length=4)


@router.post("/{domain}/compare")
def compare(domain: Literal["flights", "products", "movies"], body: CompareRequest, svc=Depends(service), state=Depends(store)):
    return [current_record(row, domain, state) for row in svc.compare(domain, body.ids)]


@router.get("/{domain}/{record_id}/availability")
def availability(domain: Literal["flights", "showtimes"], record_id: str, state=Depends(store)):
    return state.availability(domain, record_id)


@router.get("/products/{record_id}/stock")
def stock(record_id: str, svc=Depends(service)):
    product = svc.detail("products", record_id)
    return {"product_id": record_id, "stock": product.stock, "in_stock": product.stock > 0}


@router.post("/sessions", status_code=201)
def session(state=Depends(store)):
    return state.create_session()


class CartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quantity: int = Field(ge=0, strict=True)


@router.get("/sessions/{session_id}/cart")
def cart(session_id: str, state=Depends(store)):
    return state.cart(session_id)


@router.put("/sessions/{session_id}/cart/{product_id}")
def set_cart(session_id: str, product_id: str, body: CartRequest, state=Depends(store)):
    return state.set_cart(session_id, product_id, body.quantity)


@router.delete("/sessions/{session_id}/cart/{product_id}")
def remove_cart(session_id: str, product_id: str, state=Depends(store)):
    return state.set_cart(session_id, product_id, 0)


class ReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: Literal["flights", "showtimes"]
    record_id: str = Field(min_length=1)
    quantity: int = Field(gt=0, strict=True)
    request_key: str = Field(min_length=1, max_length=128)


@router.post("/sessions/{session_id}/reservations", status_code=201)
def reserve(session_id: str, body: ReservationRequest, state=Depends(store)):
    return state.reserve(session_id, body.domain, body.record_id, body.quantity, body.request_key)


@router.get("/sessions/{session_id}/reservations")
def reservations(session_id: str, state=Depends(store)):
    return state.reservations(session_id)


@router.get("/{domain}/{record_id}")
def detail(domain: Literal["flights", "products", "movies", "cinemas", "showtimes"],
           record_id: str, svc=Depends(service), state=Depends(store)):
    return current_record(svc.detail(domain, record_id), domain, state)


def current_record(record, domain, state):
    item = record.model_dump(mode="json")
    if domain in ("flights", "showtimes"):
        item.update(state.availability(domain, record.id))
    return item
