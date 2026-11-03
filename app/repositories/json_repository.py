import json
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import ValidationError

from app.models import Record

T = TypeVar("T", bound=Record)


class DataSourceError(Exception):
    """A mock data source is unavailable or invalid."""


class JsonRepository(Generic[T]):
    """Load and validate a read-only snapshot without changing the source file."""

    def __init__(self, path: Path, model: type[T]):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("Expected a JSON array")
            records = tuple(model.model_validate(item) for item in raw)
            index = {record.id: record for record in records}
            if len(index) != len(records):
                raise ValueError("Duplicate record IDs")
        except (OSError, ValueError, TypeError, ValidationError) as exc:
            raise DataSourceError(f"Unable to load dataset: {path.name}") from exc
        self._records = records
        self._index = index

    def all(self) -> tuple[T, ...]:
        return self._records

    def get(self, record_id: str) -> T | None:
        return self._index.get(record_id)
