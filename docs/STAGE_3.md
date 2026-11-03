# Stage 3: Offline conversation engine

New files: app/chat.py and tests/test_chat.py. Replace app/main.py.
No additional dependency is needed.

Run `python -m pytest -q` and `python run.py`. Health now reports stage 3.
The chat UI is scheduled for Stage 4; the root URL still returns 404.

## API demonstration

1. POST /api/sessions; retain session_id.
2. POST /api/sessions/{session_id}/chat with JSON:
   {"message":"Find me a flight from Baku to Istanbul on Friday."}
3. Continue with {"message":"The cheaper one, is it direct?"}.
4. Continue with {"message":"What is playing tonight?"}.
5. Continue with {"message":"Show me laptops."}.
6. Continue with {"message":"The second one, is it in stock?"}.
7. GET /api/sessions/{session_id}/history to inspect all turns.

The response contains category, message and domain result items. IDs in those
items can be used explicitly, e.g. "Book F001 two seats" or "Add P001 to cart".
A booking is committed immediately when explicitly requested. A second separate
chat message is a separate request; transport-level retry deduplication remains
available through the Stage 2 reservation endpoint with request_key.

## Behavior and limits

Routing uses English keywords and official catalog names, not a language model.
The seven categories are Flights, Shopping, Cinema, Mixed, Follow-up, Ambiguous
and Out of scope. Domain filters and last-result order survive switches/restarts.
Relative dates use 2026-11-02. Weekday names mean the next occurrence, with today
represented explicitly by "today". Movie ambiguity returns candidate showtimes
and asks for a specific title or ID. No movie is booked merely from its genre.

Supported chat filters are a subset of the full REST API: route/date/directness,
product keyword/brand/category/color/maximum price and movie genre/date/title.
The REST API retains all Stage 2 filters. Complex paraphrases, multiple separate
requests in the same domain and independent budgets/dates inside a mixed message
are not fully supported. Mixed requests currently share extracted constraints.
The later reliability pass must address these cases before final acceptance.
The engine serializes turns within a single server process; use one worker.
No external services, invented records or real payments are used.

## Validation

32 tests pass: 21 foundation/domain checks and 11 conversation checks including
all eight brief scenarios, persistence, cart, booking, invalid dates and isolation.
This is scenario coverage, not an independently measured routing accuracy score.
The test environment emits one Starlette/httpx deprecation warning.
