# SuperChat

One conversation for flights, products and cinema. FastAPI serves a self-contained
web UI; Gemini handles semantic routing, while narrow local read plans handle
common searches and follow-ups. Prices and records come from backend sources,
not model knowledge. Python 3.11+ is required; there is no frontend build step.

## Two separate modes

| Mode | Start | Browser | Data / transactions |
| --- | --- | --- | --- |
| Phase 1 | `python run.py` | http://127.0.0.1:8000 | Official mock JSON; simulated cart and reservations |
| Phase 2 | `python run_live.py` | http://127.0.0.1:8001 | Birmarket and cinema snapshots; optional cached flight-price API; read-only |

For the real-source demonstration, use Phase 2. Do not mix Phase 1 mock results
with Phase 2 records or describe cached flight prices as guaranteed live fares.

## Install

Run in Windows PowerShell from the project directory, for example D:\superchat:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Use .venv/bin/python on macOS/Linux. Runtime dependencies are FastAPI, Pydantic,
Uvicorn and Pillow; pytest/HTTPX are development dependencies. SQLite and provider
HTTPS clients use the Python standard library. See requirements*.txt for ranges.

## Local settings

Create or edit .env next to run.py. Preserve existing working keys:

```dotenv
SUPERCHAT_CHAT_MODE=llm
GEMINI_API_KEY=YOUR_PRIVATE_GEMINI_KEY
GEMINI_MODEL=gemini-3.5-flash-lite
TRAVELPAYOUTS_TOKEN=YOUR_PRIVATE_TRAVELPAYOUTS_TOKEN
SUPERCHAT_FLIGHT_PROVIDER=travelpayouts
```

Never commit/share .env or display it during a demo. Existing process environment
variables override .env, including empty values. Restart the server after changes.
The code has no automatic paid-provider/model fallback; billing/free quotas remain
properties of the user's provider accounts, not a guarantee implied by a model name.
A configured flag confirms local settings, not authentication or remaining quota.

Phase 1 may use SUPERCHAT_CHAT_MODE=rules for offline English-oriented rules.
Phase 2 uses its Gemini-capable live engine regardless of that Phase 1 setting;
common local reads can avoid model calls, but general semantic requests need Gemini.

## Run Phase 2

Collect the first real-source snapshot (internet required):

```powershell
.\.venv\Scripts\python.exe scrape_sources.py --per-category 15 --pages 3 --cinemas 5
.\.venv\Scripts\python.exe run_live.py
```

Open http://127.0.0.1:8001. On later starts only run_live.py is needed if a readable
snapshot exists. Refresh data starts the same bounded background collection;
chat can use the previous snapshot while it runs. It is not a recurring scheduler.
Refresh can take several minutes and can return fewer records than the limits.

The collector follows published pages/links, checks robots policies and known
HTTPS hosts, limits response size and rate, and saves var/live_sources.json
atomically. Failed sources may retain older successful records with original
timestamps and a stale label. Partial failures remain visible; mock fallback is
never used. Unknown color, stock, seats or baggage is not treated as zero.

Flight token check (choose a future date):

```powershell
.\.venv\Scripts\python.exe -m app.flight_source --origin BAK --destination IST --date 2026-11-20
```

Month comparison, explicitly requested:

```powershell
.\.venv\Scripts\python.exe -m app.flight_source --origin BAK --destination IST --month 2026-11
```

The Travelpayouts Data API returns prices cached from real user searches. Its
one-way endpoint groups by date and may return one cheapest price per day;
it does not enumerate all airlines for an exact date. Every month result keeps
its actual departure date. AZN must be confirmed; no silent currency conversion
is performed. Baggage and exact available seats are unknown for this source.

For detailed setup/error codes and future adapter integration, see
[Phase 2 setup](docs/PHASE2_SETUP.md). A new reviewed flight adapter can be selected
without rewriting the chat/UI, but a source, credentials and implementation are
still required; no second real flight provider is currently connected.

## Conversation and interface

- Three domains and seven categories: Flights, Shopping, Cinema, Mixed,
  Follow-up, Ambiguous, Out of scope.
- AZ/EN/RU response-language handling, saved per-domain filters, ordered search
  IDs, selected records and conversation history. Known local references use
  the full shortlist even after a detail card; broader interpretation uses Gemini.
- Product category/brand/color/price filters, sorting, details and price comparison.
  Source feature tables retain published specifications; not every specification
  has a numeric filter in the tool schema.
- Cinema date/time/venue searches from collected rows. Film genre/language/rating
  and district filters are explicitly unsupported when the source has no evidence.
- Exact-date and explicit-month flight searches; source links and cache labels.
  No live inventory guarantee or real booking is made in Phase 2.
- Voice: start/stop recognition, review/edit the transcript, then press Send.
  Browser/OS language support varies; recognition may use an online browser service.
- Optional read-aloud uses installed browser voices; missing voices do not hide text.
- Photo search: JPEG/PNG/WebP, <=5 MB and <=12 MP; Gemini estimates product type,
  then deterministic search returns source candidates. No exact brand/model match
  is guaranteed. Confidence is a model estimate, not calibrated probability.
  Images are validated/re-encoded before sending; raw image bytes are not saved
  in conversation history. Search explicitly sends the image to Gemini.

The page uses textContent for message content. Its English UI labels and original
source titles are not a promise of a fully translated interface.

## Phase 1 behavior

The original five JSON datasets remain unchanged: 17 flights, 12 products,
8 movies, 3 cinemas, 16 showtimes. Relative dates use 2026-11-02. Phase 1 includes
simulated cart totals, seat availability and quantity reservations; no payment or
real reservation occurs. Gemini writes require a pending proposal and confirmation;
REST request keys support idempotency. SQLite transactions guard mock inventory.

Phase 2 blocks Phase 1 catalog/write REST routes and uses a separate database.
Phase 1 runtime state is var/superchat.sqlite3; Phase 2 uses
var/superchat-live.sqlite3. New conversation creates a fresh session, not a reset
of global Phase 1 seat usage. Do not delete databases or snapshots just to update
source files. Durable preference extraction is a Phase 1 LLM feature; its full
behavior is not asserted for the Phase 2 routing-only planner.

## Validation

Offline development suite: 342 Python tests passed; 12 voice-controller checks
passed. Provider responses are mocked in automated tests. One non-failing
Starlette/HTTPX deprecation warning remains.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node tests/voice_ui.test.cjs
```

With the latest Phase 2 server running, open a second terminal:

```powershell
.\.venv\Scripts\python.exe check_phase2.py --run-live --date 2026-11-20
```

This sends up to 14 test messages, can use Gemini/provider quotas, and saves
phase2_live_results.json. Without --run-live it checks health only. PASS checks
minimal structure/routing/context; WARN means missing coverage and FAIL means a
failed check. Backend errors stop early. The script is not a factual benchmark.

The user reported a complete local Phase 2 run on 3 November 2026: 14/14 passed,
0 warnings, median 0.23 s, max 2.78 s. Mixed read latency was 0.93 s after a previous
46.69 s observation. These are user-supplied terminal results, not independently
inspected JSON or a production latency guarantee. Many checks use narrow local
handlers; they do not measure general Gemini inference quality or speed.

Historical rules-router development evaluation: 41/44 (93.18%), not a Gemini score.
Phase 1 has its own check_phase1.py; never run the Phase 2 script against port 8000.
Follow [the demo and manual checklist](docs/DEMO.md) before final submission.

## Files and remaining limits

| Path | Responsibility |
| --- | --- |
| app/main.py / app/ui.py | FastAPI composition and self-contained UI |
| app/llm_chat.py | Gemini planning and Phase 1 draft validation |
| app/live_chat.py | Phase 2 local/semantic routing and verified result templates |
| app/live_sources.py | Product/cinema collectors, snapshots and background refresh |
| app/flight_source.py | Current Travelpayouts adapter and diagnostic CLI |
| app/flight_providers.py | Provider contract, capabilities and offer validation |
| app/visual_search.py | Image validation, type classification and catalog candidates |
| app/repositories/, app/services/ | Official catalog and SQLite state services |
| check_phase2.py / tests/ | Live smoke checks and offline regression tests |
| docs/ | Setup, demonstration and current technical report |

Use one Uvicorn worker: locks and refresh cooldowns are process-local. Session IDs
are demo identifiers, not authentication. This is not a production booking or
commerce platform. Snapshot coverage, provider availability/quota, free-form
interpretation and actual browser/voice behavior remain practical limits.

Current reports: [Markdown](docs/TECHNICAL_REPORT.md) / [PDF](docs/TECHNICAL_REPORT.pdf).
Older STAGE/acceptance/evaluation documents are historical, not current sign-off.

Dependency sources: [FastAPI](https://github.com/fastapi/fastapi),
[Pydantic](https://github.com/pydantic/pydantic),
[Uvicorn](https://github.com/Kludex/uvicorn),
[Pillow](https://github.com/python-pillow/Pillow),
[pytest](https://github.com/pytest-dev/pytest),
[HTTPX](https://github.com/encode/httpx).
