from typing import Protocol, TypeVar

from app.models import Cinema, Flight, Movie, Product, Record, Showtime

T = TypeVar("T", bound=Record, covariant=True)


class ReadRepository(Protocol[T]):
    def all(self) -> tuple[T, ...]: ...
    def get(self, record_id: str) -> T | None: ...


class FlightRepository(ReadRepository[Flight], Protocol):
    pass


class ProductRepository(ReadRepository[Product], Protocol):
    pass


class MovieRepository(ReadRepository[Movie], Protocol):
    pass


class CinemaRepository(ReadRepository[Cinema], Protocol):
    pass


class ShowtimeRepository(ReadRepository[Showtime], Protocol):
    pass
