# Stage 1 - Foundation

## Scope and decisions

The backend uses Python, FastAPI and Pydantic. Domain-specific repository Protocols
separate future services from JSON implementation. The catalog validates all five
sources together before exposing a ready status. Missing or corrupt sources yield
a controlled HTTP 503 without exception details in the response.

Models reject unexpected fields and invalid capacities. Prices use Decimal.
Repositories expose immutable records and tuples. The repository layer implements
only source access; search, filtering, sorting and booking belong in upcoming
services. Official JSON bytes are copied unchanged from the supplied ZIP.

The reference date is 2026-11-02. Future relative-date handling will use that value,
not the host clock. No real external integrations or personal data are required.

## Acceptance criteria

- Load 17 flights, 12 products, 8 movies, 3 cinemas, and 16 showtimes.
- Validate unique IDs, model constraints and cinema/movie references.
- Preserve original dataset bytes.
- Expose readiness with HTTP 200 and controlled unavailability with HTTP 503.
- Run the backend with one command after dependency installation.

## Known limitations

This stage has no chat UI, intent router, domain searches, cart, persistent session,
or reservation operations. It must not be presented as the final submission.
The final technical report (maximum four pages), demo script and routing accuracy
report will describe the completed system, not this intermediate foundation.

## Next stage

Implement domain search/filter/sort/details/compare services, then SQLite-backed
cart and reservation operations with transaction-safe capacity checks. Service
functions will depend on repository contracts rather than JSON file paths.
