# Stage 2: Domain operations

Implemented domain services and a REST API. The unified conversational interface,
intent routing, relative dates and conversation history are scheduled for Stage 3/4.
No LLM, real service API, payment or personal information is used.

## Files

- `app/services/catalog_service.py`: repository-backed search, filters, sorting,
  detail retrieval, comparisons and movie/cinema/showtime joins.
- `app/services/state.py`: SQLite sessions, carts, atomic mock reservations,
  availability and session-scoped idempotency keys.
- `app/api.py`: validated HTTP endpoints and request models.
- `app/main.py`: service initialization, route registration and domain errors.
- `app/config.py`: database location configuration.
- `tests/test_domains.py`: domain, API, persistence and concurrency tests.

## Running

No new third-party dependency is required. SQLite is included in Python.
Install requirements-dev.txt, run `python -m pytest -q`, then `python run.py`.
Open http://127.0.0.1:8000/docs for the interactive API.
Health reports stage 2. No chat UI is included yet.

## API walkthrough

1. POST `/api/sessions` and copy the returned session_id.
2. GET `/api/flights?origin=GYD&sort=price`.
3. POST `/api/flights/compare` with `{"ids":["F001","F002"]}`.
4. GET `/api/products?in_stock=true&max_price=100`.
5. PUT `/api/sessions/{session_id}/cart/P001` with `{"quantity":2}`.
6. GET `/api/sessions/{session_id}/cart`.
7. GET `/api/showtimes?on_date=2026-11-02`.
8. POST `/api/sessions/{session_id}/reservations` with
   `{"domain":"showtimes","record_id":"S001","quantity":1,"request_key":"demo-1"}`.
9. GET `/api/showtimes/S001/availability` and
   `/api/sessions/{session_id}/reservations`.

Use `domain: "flights"` for flight reservations. Keep the same request_key when
retrying an identical reservation. Reusing it with changed input returns 409.
Cart PUT sets the absolute quantity; quantity zero or DELETE removes the item.
Carts check stock but do not reserve inventory. There is no checkout in this stage.
All prices use AZN; calculated totals are decimal strings to preserve precision.
Details/search show immutable source availability; the availability endpoints
return remaining seats after reservations. Reservations are global inventory,
while carts and booking lists are scoped to a session.

## Storage and limits

Runtime state is stored at `var/superchat.sqlite3`. Stop the server and remove that
file to reset the mock demo. Do not remove it if you need existing sessions.
JSON source files are never updated. SQLite write transactions prevent overselling
across concurrent requests. No seat numbers are invented. Sessions are opaque IDs,
not production authentication. Run locally for the competition demonstration.
SQLite uses local disk; multi-host deployment is outside this stage.

## Validation

Tests cover source integrity, filters, comparisons, joins, totals, unavailable
stock, sold-out screenings, invalid inputs, session isolation, repeated booking
requests, capacity conflicts, concurrent attempts and restart persistence.
Routing evaluation will be supplied after the router exists.
