# Supported chat commands

Use English. Dates are interpreted relative to 2026-11-02. Prices are AZN.
The parser is deterministic; the examples below describe supported phrasing.

| Task | Example |
|---|---|
| Route and date | Flights from Baku to Istanbul on Friday |
| Flight time window | Flights to Istanbul on Friday after 08:00 before 23:00 |
| Stops and duration | Flights to Istanbul on Friday max 1 stop fastest |
| Direct flights | Direct flights to Istanbul on Friday |
| Flight comparison | Compare F001 and F002 |
| Flight detail | Details F001 |
| Context detail | The cheaper one, is it direct? |
| Product search | Headphones under 100 AZN |
| Brand/color/category | Black Sonivo audio products |
| Stock filter | Show me laptops in stock |
| Product sorting | Sort by rating |
| Product comparison | Compare P003 and P004 |
| Cart | Add P001 to cart |
| Cart quantity | Add P001 two items to cart |
| Cart removal | Remove P001 from cart |
| Cart total | Show my cart |
| Movie filters | Movies in English rating at least 8 |
| Genre/date | Sci-fi movies on Saturday |
| Showtime venue/time/price | Showtimes at Ganjlik on Friday after 18:00 under 20 AZN |
| Cinema listing | Show cinemas |
| Specific movie | Showtimes for Orbit Zero on Friday |
| Availability | Seats available for S001 |
| Mock booking | Book S001 two seats |
| Mixed constraints | Flights to Istanbul on Friday under 500 AZN and headphones under 100 AZN |
| Reset domain filters | Reset filters |

Filters are remembered separately in each domain. A product-type change resets
shopping filters. Selecting a named movie clears the previous genre filter;
selecting a genre clears the previous movie title filter. Use Reset filters
before an unrelated search if remembered filters are no longer appropriate.

Cart commands set absolute quantities, rather than incrementing them.
Reservations use inventory shared across sessions. Text booking is immediate;
card booking asks for confirmation. No real booking or payment occurs.

## Installing this update

Replace app/chat.py and tests/test_chat.py. No other Python file changes.
No additional package installation is needed. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe run.py
```

Then open http://127.0.0.1:8000. Health still reports UI stage 4; this update
extends that existing application rather than changing the startup interface.

## Verification

45 automated tests pass, including explicit domain precedence, time windows,
stop limits, duration/rating sorting, filter reset, venue/time/price and movie
language/rating filters. One non-failing dependency deprecation warning remains.
Browser visual verification is still outstanding in the development environment.
This update does not claim complete unrestricted natural language coverage.
