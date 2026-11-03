# Phase 2 — real source snapshots and optional flight-price API

## Current behavior

- Birmarket public category and product pages supply real listed AZN prices,
  brand, availability flag and collection timestamps. Product JSON-LD and the
  `product-features-row` HTML table are both read. Published color, RAM, SSD,
  processor and other table attributes are retained under `specifications`.
  Keyboard color is kept separate from body color. Missing values remain unknown.
- CinemaPlus/CineMastercard public venue tables supply actual dated showtimes,
  venues, halls and listed prices. Exact available-seat counts are not published
  by this collector. Genre/language/district data are not inferred.
- Optional Travelpayouts / Aviasales Data API supplies cached prices from actual
  user searches. It is NOT a live offer/seat confirmation API. Requests are made
  on demand with a locally configured token. No token means no flight prices;
  no Phase 1 fares are substituted. Website-side booking completes externally.
- Gemini routes semantic/mixed requests and is separate from the data sources.
  It never supplies prices, inventory or missing specifications itself.

All features are read-only: no real orders, payments or reservations are made.
Photo search returns candidates from the scraped product snapshot. Voice input
prepares a draft and still waits for Send. Phase 1 remains isolated on port 8000
with its original mock data; Phase 2 runs on 8001 and uses a separate SQLite file.

## Install changed files

| File | Destination |
| --- | --- |
| live_sources.py | app/live_sources.py |
| live_chat.py | app/live_chat.py |
| flight_source.py | app/flight_source.py |
| flight_providers.py (new) | app/flight_providers.py |
| ui.py | app/ui.py |
| test_flight_providers.py (new) | tests/test_flight_providers.py |
| conftest.py | tests/conftest.py |
| PHASE2_SETUP.md | docs/PHASE2_SETUP.md |

No new Python dependency is required. Keep the existing Gemini settings and
other .env entries. Do not replace the .env file or share any token in a chat.

## Refresh actual product and cinema data

From D:\superchat:

```powershell
.\.venv\Scripts\python.exe scrape_sources.py --per-category 15 --pages 3 --cinemas 5
.\.venv\Scripts\python.exe run_live.py
```

Open http://127.0.0.1:8001 and reload with Ctrl+F5. Alternatively, after the first
snapshot exists, use the header's Refresh data button. Its updated limits are
15 products per category, up to 3 published category pages and 5 discovered
cinema venues. Actual counts can be lower when pages fail or contain fewer links.
Collection is sequential and rate-limited; a larger refresh can take several
minutes. Chat can use the previous snapshot while collection runs.

These are bounded samples, not a full marketplace or all cinemas. Increasing a
limit does not itself guarantee coverage. Only published next-page/venue links
are followed. No authentication, challenge bypass or disallowed Aviasales search
page scraping is performed. Older successful records from entirely failed sources
are retained as stale with original timestamps; a new fetch time is not assigned
to retained records. Partial venue/category results are reported as partial.

## Enable the optional flight-price source

1. Create a Travelpayouts account and follow its Aviasales/API access requirements.
2. Open Profile → API token and copy your own token locally.
3. Append this entry to the existing D:\superchat\.env (replace the placeholder):

```dotenv
TRAVELPAYOUTS_TOKEN=YOUR_OWN_TOKEN
```

4. Restart the server. Configuration alone does not prove access or route coverage.
5. Test one request first, choosing a future departure date:

```powershell
.\.venv\Scripts\python.exe -m app.flight_source --origin BAK --destination IST --date 2026-11-20
```

Replace the example date if it has passed. The command uses the token only in an
HTTPS header, never prints it, and makes no Gemini calls. A successful empty list
means the provider cache has no accepted price for that request. It does not
prove that no airline flies that route.

A nonempty successful response should contain `TF_` IDs and
`price_kind: cached_search_price`. Then ask the chat, for example:
`Bakıdan İstanbula 20 noyabr 2026 üçün uçuşları göstər`.
Origin, destination and date must be specified. Common city aliases are supported;
otherwise use three-letter IATA codes. Airline filters require IATA codes (J2,
TK, etc.). This integration requests one-way cached prices, with one bounded request of
up to 30 entries for an exact date or 31 for a month; provider grouping may return fewer.
It is not a full inventory of flights or a multi-passenger quotation.

The adapter requests AZN and requires the response to confirm AZN before showing
prices. It never relabels USD/RUB as AZN or silently converts currencies. If AZN
is unsupported or unconfirmed, FLIGHT_CURRENCY is shown and no price is displayed.
Expired, wrong-date, wrong-route, malformed, nonpositive and unsafe-link rows are
excluded. Provider retrieval time is shown separately from any provider-supplied
found_at timestamp. Retrieval time is not a claim that the fare was freshly found.

- FLIGHT_NOT_CONFIGURED: token is missing locally.
- FLIGHT_TOKEN_FORMAT: invalid local token format.
- FLIGHT_HTTP_401 / 403: inspect account access and token locally.
- FLIGHT_HTTP_429: provider quota/rate limit; there is no paid fallback.
- FLIGHT_CURRENCY: response did not confirm AZN; do not treat it as manat.
- FLIGHT_NETWORK: connection failure; no mock fallback is used.
- FLIGHT_LOCATION / DATE / AIRLINE: provide valid search parameters.

There are no automatic retries or fallback providers. Provider redirects are
blocked so credentials cannot be forwarded. Keys/provider error bodies are not
stored in conversation history or shown to the browser. Tests explicitly clear
the token so running pytest cannot spend a user's provider quota.

## Conversation behavior

Basic AZ/EN/RU product-type searches, price refinements, sorting, ordinal details,
cheapest/most-expensive details and comparison of the first two use narrow local
read plans. Broader and mixed requests still use Gemini. The local grammar is
bounded; support for a few phrases is not a guarantee for all paraphrases.

- `Noutbukları göstər` / `Show me laptops` / `Покажи ноутбуки`: same sample IDs.
- `qara rəngdə qiymət 1500 və 1800 aralığında olan kompüter`: fresh color/range search.
- `qiymət fərqi yoxdur`: remove only price bounds, retain color/type/brand.
- `rəng fərqi yoxdur`: remove only color, retain other filters.
- `Filtrləri sıfırla`: remove constraints but keep the requested product type.
- `Bahadan ucuza sırala`: reorder the current filtered list.
- `Ən ucuzunun detalları` / `Ən bahasının detalları`: select within the saved shortlist.
- `İkincinin detalları`: second search result, even after viewing another detail card.
- `İlk ikisini müqayisə et`: source prices and their difference; no invented specs.
- `Sabah hansı filmlər var?` → `18:00-dan sonra`: retain the cinema date.
- `Əvvəlki məhsulları göstər`: restore the saved shopping search after a cinema turn.
- `kaqdela`: Russian greeting without replaying prior flight results.

A complete new type search clears old shopping filters. A short price refinement
retains them. Under/cheaper than is strict; up to is inclusive, at AZN cent
precision. Conflicting bounds are clarified without changing the saved filters.
Missing color coverage is explained and never silently treated as black. Product
detail text shows the collected specifications. The first six attributes are
also visible on cards; All details contains the full record. Arbitrary numeric
RAM/SSD filters are not yet added to the tool schema.

An empty list does not replay old successful results. Comparison requires two
current distinct products. A single remaining product can be both cheapest and
most expensive. Cinema no-match messages list collected dates when available.
Today's relative dates use current Baku time (UTC+4 fallback on Windows).

## Verification and remaining limits

The provider-boundary revision passes 317 Python tests, 12 voice-controller checks
and JavaScript syntax validation. The real HP 178229 product page was fetched through the
robots-aware public client: its feature table yielded body color Gümüşü, RAM 8,
SSD 512 and 30 named specifications. This validates that page, not every listing.
Earlier multi-venue collection yielded 93 showtimes across three attempted venue
pages; one unavailable/empty venue made its status partial. Expanded refresh
limits are tested with controlled collectors; no full-coverage claim is made.

Flight tests mock the provider and verify header-only credentials, AZN checks,
route/date filtering, malformed/expired records, source links, error redaction,
read-only details/bookings and persisted session records. The user has confirmed a successful local CLI and chat request for BAK–IST on
2026-11-20, returning a 153 AZN cached price at that time. This is an observation,
not a current fare guarantee. No account token is available in this development
environment; new month queries still need local live verification. Live Gemini
routing and browser/microphone behavior also need verification on Windows.

Status: http://127.0.0.1:8001/api/live/status includes a dynamic flight_api.configured
flag. The snapshot's sources.flights describes the scrape snapshot, which itself
contains no flight offers; optional API flight records are fetched on demand.
A configured flag is not proof of successful authentication.

Transient Gemini HTTP 503 behavior is unchanged. Scheduled refresh, full source
coverage, exact seat/baggage confirmation and guaranteed live fares remain open.
Do not present this revision as a complete live booking platform.

Official references:
- https://support.travelpayouts.com/hc/en-us/articles/203956083-Requirements-for-Aviasales-data-API-access
- https://support.travelpayouts.com/hc/en-us/articles/203956163-Aviasales-Data-API
- https://birmarket.az/robots.txt
- https://cinemastercard.az/robots.txt


## Replaceable flight adapters

The chat now uses app/flight_providers.py as a provider boundary. The default
remains Travelpayouts with the existing TRAVELPAYOUTS_TOKEN. No new account,
secret or dependency is needed for the current source. Optionally make the choice
explicit in .env:

```dotenv
SUPERCHAT_FLIGHT_PROVIDER=travelpayouts
```

For a future source, implement a reviewed Python adapter with:

- `capabilities`: FlightCapabilities(provider_id, name, price_kind, source_hosts,
  supports_calendar, supports_baggage, supports_seats).
- `configured()`: check local configuration without printing or calling a network.
- `search(args)`: call that provider and map its data into FlightOffer fields.

Register its factory in the local PROVIDERS dictionary and select its name with
SUPERCHAT_FLIGHT_PROVIDER. Registration is a code integration, not an automatic
connection to any URL. Do not put a new provider's credentials into the Travelpayouts
field. Each adapter must handle its own HTTPS authentication, timeout, response
limits, API-specific filters, currency semantics and documented access terms.
Only one provider is selected; there is no paid or silent fallback. Unknown names
produce FLIGHT_PROVIDER. Shopping/cinema continue using their existing collectors.

FlightGateway validates shared fields, timezone-aware timestamps, positive AZN
prices, route/date/month, distinct IDs, optional expiry, and HTTPS source links
against the adapter's reviewed source_hosts. It reapplies price/stops/time/airline
filters and sorting. It strips unrecognized fields (including raw API credentials)
and keeps unsupported baggage/seat fields null. There is no currency conversion.
An invalid provider response produces FLIGHT_CONTRACT, with no raw error payload.
A reviewed adapter must normalize airport/city codes consistently with its request.

The normalized card contains provider_id, source name, retrieval time, source URL,
price_kind and data_mode. Current cache rows stay cached_search_price/api_cache.
A future adapter may use live_offer/api_live only when its actual API semantics
justify that label. Even a live offer requires reconfirmation before purchase.
No adapter enables real booking here; can_reserve remains false. UI source links
for a new provider appear only after the backend verifies the reviewed host.

The local test adapter demonstrates two simultaneous offers through the same
chat engine, without rewriting the chat or renderer. It is a test double and is
NOT registered as a real selectable data source. Tests also check invalid links,
wrong currencies/dates/routes, stripped secret fields, source switching and
unsupported calendar capability. Switching providers clears the saved flight
shortlist; old cards remain historical and are not relabelled as new results.

## Explicit month searches

Only when requested, use departure_month (YYYY-MM) instead of departure_date:

```powershell
.\.venv\Scripts\python.exe -m app.flight_source --origin BAK --destination IST --month 2026-11
```

Chat examples:

- `Bakıdan İstanbula noyabr 2026 ayında uçuşları göstər`
- `Show flights from Baku to Istanbul in November 2026`

Each row keeps its actual departure date. Different dates are clearly labelled;
there is no claim that these are multiple alternatives for a single day. The
current API groups one-way prices by date, so a month search can return prices
for several days, but neither a full month's coverage nor a minimum number of
results is guaranteed. Past/out-of-month rows are not displayed.

Exact-date requests remain exact. No silent ±day expansion is performed. Date and
month cannot both be supplied. An adapter without month-search support returns
FLIGHT_CAPABILITY rather than inventing results or changing the requested date.
Calendar comparisons continue to use cached prices, with baggage and seat counts
unknown for Travelpayouts. The future-adapter boundary does not remove limitations
of the currently selected source.

After copying this batch, restart run_live.py and Ctrl+F5. Existing tokens remain
in place. No new product/cinema refresh is required for the adapter change alone.
Run the month CLI check locally and then the chat example. Send only the result,
never the token, if a provider access/format problem occurs.


## Final local conversation check

The combined acceptance runner creates a fresh Phase 2 session and sends up to
14 messages. It checks AZ/EN/RU product parity, cheapest/ordinal references,
comparison, cinema/topic return, exact/month flight dates, baggage unknowns,
small talk, mixed domains and unrelated/out-of-scope input. Source/currency/mode
checks detect Phase 1 mock IDs and malformed source records.

Files in this acceptance-only batch:

| File | Destination |
| --- | --- |
| check_phase2.py (new) | D:\superchat\check_phase2.py |
| test_phase2_check.py (new) | D:\superchat\tests\test_phase2_check.py |
| PHASE2_SETUP.md | D:\superchat\docs\PHASE2_SETUP.md |

With the latest run_live.py server already running, use a second terminal:

```powershell
.\.venv\Scripts\python.exe check_phase2.py --run-live --date 2026-11-20
```

Choose a future date if necessary. Without --run-live it only checks health.
The explicit --run-live flag can use Gemini and flight-provider quotas through
the local server. The runner reads no API keys and performs no refresh, real
booking or payment. Do not refresh data concurrently: changing snapshots can
cause parity comparisons to fail even if language handling is unchanged.

The script refuses Phase 1 and missing Gemini configuration before sending chat
messages. It stops early on backend/transport errors. A missing source result is
WARN, not PASS; malformed/incorrect responses are FAIL. Exit 0 requires no FAIL,
no coverage WARN and a configured flight API. Exit 1 still needs review, and exit
2 indicates setup/transport failure. Unsupported provider calendars may fail the
month check; this is a reported capability gap, not a reason to fabricate data.

The result is saved as phase2_live_results.json in the working directory. Send
its final summary and any FAIL/WARN rows for diagnosis; never send .env or keys.
PASS is a development smoke result, not a completion certificate: free-form fact
accuracy, source coverage, sensible clarifications and browser/voice behavior
still require manual review. This runner does not fix upstream missing records.
The acceptance-only tests pass against mocked providers and a local TestClient.
The complete suite now passes 327 Python tests; no new live provider requests
were made in the development environment for this batch.


## Latency and unrelated-input correction

The user's local run passed the first 12 checks, but Mixed domains took 46.69s
and Unrelated input returned backend_error. That summary did not include the
underlying error message; it cannot establish whether the cause was a provider
HTTP failure, response formatting or another issue.

This batch reduces duplicated planner input: all searchable record IDs, filter
fields and shortlist order remain, but full specification tables, source URL
queries and recent_results are not copied repeatedly into model context. Backend
records and detail responses retain their full source evidence. Phase 2 Gemini
planning now requests only category, clarification and actions, instead of drafting
an unused answer. Phase 1 retains its existing draft-answer behavior.

Two unambiguous local search clauses across domains (such as `Find laptops and
flights from Baku to Istanbul on 2026-11-20`) execute as a Mixed read plan without
Gemini. Unknown constraints, same-domain comparisons and semantic mixed requests
still go to Gemini. This is a bounded optimization, not a replacement for semantic
understanding or a guarantee of model latency.

Conservative keyboard-row runs such as asdfgh/qwerty and punctuation-only input
return an Ambiguous clarification without a provider call or old result cards.
Ordinary words, product/record IDs and questions containing qwerty remain eligible
for semantic handling. This is not a general gibberish classifier.

Planner failures without an existing provider code now retain GEMINI_RESPONSE in
AZ/RU and log only the safe error code. The smoke runner prints failed response
messages directly, instead of only backend_error. Successful chat JSON/report
also includes planner/execution timings and used_planner to help distinguish
routing delay from provider search delay. Real Gemini timing still needs a new
local run; no new API key is available in the development environment.

Copy these files together: app/live_chat.py, app/llm_chat.py, check_phase2.py,
tests/test_live_efficiency.py (new), and docs/PHASE2_SETUP.md. Restart run_live.py,
then rerun check_phase2.py --run-live --date 2026-11-20 from a second terminal.
The full offline suite passes 342 tests. If a real backend error recurs, send the
printed Response line as well as the summary; do not send any API key or .env.


## Expanded real-data collection

The previous 15-products-per-category refresh limit was a small sample. Expanded
limits now allow 1-200 products per category, 1-12 category pages and 1-20 venues.
The CLI and Refresh data defaults request 150 per category, 7 pages and all
published venue links up to 20. No new Gemini/flight API call is made by scraping.

```powershell
.\.venv\Scripts\python.exe scrape_sources.py --per-category 150 --pages 7 --cinemas 20
```

The existing three seeds remain computers, audio and phones. Category navigation
was inspected, but no guessed category URLs or unverified product types were
added. Only published next-page and venue links are followed; a source can still
return fewer records. The source status and original per-record retrieval times
must be retained. An expanded snapshot does not prove full site coverage.

Three bounded product workers overlap network waits using the shared rate-limited
PublicClient; request starts remain spaced per host. Results are consumed in URL
order. A failed detail request does not discard successful product rows. Null
availability stays unknown. The expanded limits and partial-failure behavior pass
348 offline tests in the complete suite. The exact live collection counts are
recorded below after the collection finishes.

For an already prepared live_sources.json, stop the local server before replacing
D:\superchat\var\live_sources.json, then restart run_live.py. Keep .env and SQLite
files intact. Also update app/live_sources.py and scrape_sources.py before using
Refresh data, so the old small cap does not shrink the new sample on the next run.
Do not put source snapshots under the official Phase 1 data directory.

### Additional shopping source: CompZone

The collector now follows published /mehsul/ links from https://compzone.az/noutbuklar. It checks robots policy, reads each product's AZN JSON-LD price and specifications, and never calls the disallowed /api/ paths. The BM_CZ_ ID namespace remains compatible with product references. Published availability is not an exact stock count. Listing and detail prices may differ; the detail price is used. Source failures are isolated from Birmarket.

Aviasales requests require the existing local TRAVELPAYOUTS_TOKEN; no token was available in the collection environment. No flight records were invented or included in this scraping snapshot. To search more dates on your own machine, run:

```powershell
.\.venv\Scripts\python.exe -m app.flight_source --origin BAK --destination IST --month 2026-11
.\.venv\Scripts\python.exe -m app.flight_source --origin BAK --destination DXB --month 2026-11
```

The chat also accepts month and route searches on demand. These are cached source prices, not confirmed live fares. A month query returns different departure dates; it does not increase alternatives for one fixed day. Never send the token or .env file.

### Verified snapshot collected on 3 November 2026

The delivered var/live_sources.json contains 464 distinct product IDs: 171 laptops (147 Birmarket + 24 CompZone), 149 audio products, and 144 phones. Body color is published for 445 records. There are 488 distinct dated showtimes from 8 cinemas, covering 2026-11-03 and 2026-11-04. All record IDs are unique and all displayed prices are positive AZN amounts with approved HTTPS source URLs. These checks validate the extracted format, not current stock or continued price validity.

Birmarket collected 440 products; four detail requests and one category-page request failed. Cinema discovery attempted 11 venues; Shamakhi, Khamsa Park and Sea Breeze did not expose supported dated rows. Their absence is recorded as partial coverage, not as proof of no screenings. CompZone yielded 24 product links and all 24 details succeeded. The snapshot contains no flights; the configured Aviasales adapter remains a separate on-demand API source.

Replace app/live_sources.py, app/live_chat.py, app/ui.py and scrape_sources.py before refreshing. Replace tests/test_live_sources.py if keeping the test suite. This revision passes 348 offline Python tests. Stop the server, place the delivered live_sources.json at D:\superchat\var\live_sources.json, then restart python run_live.py. No new dependencies or API keys are required for these scraping changes.
