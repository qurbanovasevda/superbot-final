from dataclasses import dataclass
from pathlib import Path

from app.models import Cinema, Flight, Movie, Product, Showtime
from app.repositories.interfaces import (
    CinemaRepository, FlightRepository, MovieRepository,
    ProductRepository, ShowtimeRepository,
)
from app.repositories.json_repository import DataSourceError, JsonRepository


@dataclass(frozen=True)
class Catalog:
    flights: FlightRepository
    products: ProductRepository
    movies: MovieRepository
    cinemas: CinemaRepository
    showtimes: ShowtimeRepository

    @classmethod
    def load(cls, directory: Path) -> "Catalog":
        catalog = cls(
            flights=JsonRepository(directory / "flights.json", Flight),
            products=JsonRepository(directory / "products.json", Product),
            movies=JsonRepository(directory / "movies.json", Movie),
            cinemas=JsonRepository(directory / "cinemas.json", Cinema),
            showtimes=JsonRepository(directory / "showtimes.json", Showtime),
        )
        for showtime in catalog.showtimes.all():
            if catalog.movies.get(showtime.movie_id) is None:
                raise DataSourceError("Showtime references an unknown movie")
            if catalog.cinemas.get(showtime.cinema_id) is None:
                raise DataSourceError("Showtime references an unknown cinema")
        return catalog

    def counts(self) -> dict[str, int]:
        return {name: len(getattr(self, name).all()) for name in
                ("flights", "products", "movies", "cinemas", "showtimes")}
