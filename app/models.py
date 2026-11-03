from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)


class Airport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str = Field(pattern=r"^[A-Z]{3}$")
    city: str = Field(min_length=1)


class Flight(Record):
    airline: str
    flight_number: str
    origin: Airport
    destination: Airport
    departure_local: datetime
    arrival_local: datetime
    duration_min: PositiveInt
    stops: NonNegativeInt
    via: str | None
    price_azn: Money
    cabin: Literal["economy", "business"]
    baggage_kg: NonNegativeInt
    seats_available: NonNegativeInt


class ProductName(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    en: str
    az: str
    ru: str


class Product(Record):
    name: ProductName
    category: str
    brand: str
    price_azn: Money
    rating: float = Field(ge=0, le=5)
    stock: NonNegativeInt
    color: str
    description: str


class Movie(Record):
    title: str
    genre: tuple[str, ...] = Field(min_length=1)
    language: str
    duration_min: PositiveInt
    age_rating: str
    rating: float = Field(ge=0, le=10)
    synopsis: str


class Cinema(Record):
    name: str
    district: str
    address: str


class Showtime(Record):
    movie_id: str
    cinema_id: str
    date: date
    time: time
    hall: str
    price_azn: Money
    seats_total: PositiveInt
    seats_available: NonNegativeInt

    @model_validator(mode="after")
    def validate_capacity(self) -> "Showtime":
        if self.seats_available > self.seats_total:
            raise ValueError("Available seats exceed hall capacity")
        return self
