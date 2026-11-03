# SuperChat - Technical report

## 1. Scope, architecture and execution

### Delivery scope

SuperChat is a unified web conversation for flights, shopping and cinema. Phase 1 preserves the organizer's mock catalog and simulated transactions. Phase 2 uses public retail/cinema snapshots and an optional flight-price API. The real-source demonstration uses port 8001; the original mock application remains isolated on port 8000. No real payment, product order or ticket reservation is created in either mode.

### Components and data paths

FastAPI serves the REST/chat routes and a self-contained HTML/CSS/JavaScript UI. CatalogService and repository contracts load the immutable official JSON. StateStore persists sessions and conversation context in SQLite. Phase 2 LiveConversation executes local read plans or Gemini-generated structured plans, then renders source-backed templates and cards. The model does not supply inventory or prices from its own knowledge.

The organizer datasets contain 17 flights, 12 products, 8 movies, 3 cinemas and 16 showtimes. Phase 1 uses the fixed reference date 2026-11-02. Phase 2 uses current Baku dates; Windows installations without an IANA timezone database fall back to UTC+4. Mock records never fill missing real-source results. Phase 2 blocks the Phase 1 catalog and transaction REST routes.

### Real-source collection

The Birmarket collector reads published product links, JSON-LD offers and product-features HTML tables. It retains listed AZN price, brand, published availability, explicit body color and named specifications such as RAM/SSD. Keyboard color is distinct from body color. Unpublished quantities remain null. CinemaPlus/CineMastercard venue tables supply dated movie, cinema, hall, time and price rows; exact available seats are unknown.

Refresh data starts a bounded background job: up to 15 products per category, three category pages and five discovered venue pages. Actual coverage can be lower. The client checks robots policy and approved HTTPS hosts, rate-limits requests and bounds time/response size. Snapshots are replaced atomically. Entirely failed sources can retain older successful rows with original timestamps and stale labels; partial failures remain visible. There is no recurring scheduler.

### Flight provider boundary

Travelpayouts/Aviasales Data API is the current adapter. It returns prices cached from real user searches, not guaranteed live fares. One-way queries group by date and may yield one cheapest price per day. Explicit month searches compare different departure dates; exact-day requests are not silently broadened. API output must confirm AZN. Baggage and available-seat counts are unknown for this adapter.

FlightGateway separates the chat from API-specific authentication/parsing. Reviewed adapters declare cache/live semantics, calendar support, optional inventory fields and approved source-link hosts. A shared validated format checks route/date, money, timezones, IDs and links; strips unknown fields; and reapplies filters/sorting. Unknown adapters fail explicitly, without a fallback. A future adapter can return multiple offers without changing the chat/UI, but no second real provider is connected. Provider switching invalidates the saved flight shortlist, while previous cards remain historical.

## 2. Routing, grounding and user interaction

### Semantic planning and local reads

The seven routing categories are Flights, Shopping, Cinema, Mixed, Follow-up, Ambiguous and Out of scope. Gemini receives the current message, response-language hint, compact history, domain filters and a compact source catalog. Current provider capabilities override historical refusals and scrape-snapshot flight statuses. Whitelisted tools and validated arguments constrain execution to at most six operations per turn.

Phase 2 requests only a plan: category, clarification and actions. It does not generate an unused narrative draft. Full specification tables, source URL queries and duplicated recent-result payloads are omitted from planning, while searchable IDs, filter fields, shortlist order and backend evidence remain. Phase 1 keeps its single-call plan plus optional answer draft, checked against executed record IDs and numeric evidence; those checks are partial, not proof of every statement.

Narrow local plans cover common AZ/EN/RU product searches, price refinements, sorting, ordinal details, cheapest/most-expensive references, cinema date/time searches and explicit flight routes/dates/months. Two clear independent search clauses can become a Mixed read plan without a model call. Conservative keyboard-row/punctuation input gets a clarification. Other semantic requests still use Gemini; local grammar does not imply universal paraphrase coverage.

### State and transactional separation

SQLite stores history, active domain, per-domain filters, ordered search IDs and selected records. A detail response does not shrink the search shortlist. Short refinements retain context; complete new type searches reset old filters. Removing price keeps color/brand; removing color keeps price. Empty results do not revive an earlier successful list. Explicit topic-return requests restore the saved domain search.

Phase 1 mock reservations use capacity checks and SQLite transactions; REST request keys provide scoped idempotency. Chat writes require a pending proposal and confirmation. Carts set absolute quantities and do not reserve stock. Phase 2 is read-only and directs purchases to source websites. It never implies that website accounts or orders were queried. Durable verbatim preference extraction belongs to the Phase 1 LLM path; equivalent Phase 2 preference behavior is not asserted.

### Language, voice and images

The current-message detector and stored language support Azerbaijani, English and Russian replies. UI labels and source titles retain original spelling. Browser SpeechRecognition prepares an editable transcript and always waits for Send. Recognition support and online audio processing depend on the browser/OS. Optional speech synthesis uses installed voices; unavailable voices leave text intact.

Photo search validates JPEG/PNG/WebP up to 5 MB and 12 MP, applies orientation and re-encodes a bounded image before sending it to Gemini. Classification selects a product type; backend search supplies candidates. Low-confidence or ambiguous input asks for clarification. An apparent 100% estimate is not calibrated confidence. Exact brand/model identity is not guaranteed, and raw image bytes are not stored in conversation history.

### Failure handling

Controlled codes distinguish source, contract, currency, request, network and Gemini errors. Exposed model-provider messages redact configured keys; flight-provider error bodies are not exposed. Tokens are sent in HTTPS headers and provider redirects are blocked. No automatic retry, model switch or paid fallback is configured. Real provider accounts determine billing/quota; configuration alone does not prove access. Failures never substitute fabricated prices or a mock catalog.

## 3. Verification and observed results

### Offline evidence

The current development tree passes 342 Python tests and 12 voice-controller checks. JavaScript syntax was checked. One non-failing Starlette/HTTPX deprecation warning remains. Tests cover catalog integrity, schemas, filters, mock inventory/transactions, session isolation, persistence, source parsing/retention, read-only isolation, language/context, provider contracts and safe diagnostics. Model/API responses are mocked in automated tests; test counts are not a live-answer accuracy score.

A real Birmarket HP 178229 page was fetched through the public client. The feature table yielded 30 named attributes, including explicit body color Gümüşü, RAM 8 and SSD 512. This validates one page layout, not every listing. An earlier expanded collection returned 93 dated showtimes across three attempted venue pages; one empty/unavailable venue made cinema status partial. Larger refresh limits do not establish full source coverage.

### User-reported local Phase 2 run

On 3 November 2026, the user supplied terminal output for all 14 live smoke checks: 14 passed, zero warnings, zero failures, no early stop, flight configuration true. Median elapsed time was 0.23 seconds and maximum 2.78 seconds. Covered flows included three-language product parity, cheapest detail, comparison, cinema switching, product-context return, exact/month flights, baggage reference, small talk, mixed intent, unrelated input and out-of-scope handling.

The mixed request took 0.93 seconds after a previous 46.69-second observation. The optimized simple mixed case uses local plans rather than Gemini, so this is not evidence that all Gemini requests became faster. Many smoke turns use narrow deterministic handlers. The user also confirmed a local API and chat response for BAK-IST on 20 November 2026 at 153 AZN; this was a cached observation at that time, not a current fare promise.

Evidence is pasted user terminal output and chat examples; the saved phase2_live_results.json has not been independently inspected. The runner checks minimum routing, result structure, selected references, provenance and currency/date consistency. It does not prove arbitrary answer correctness, complete source coverage or full multilingual equivalence. WARN would flag missing coverage; backend/transport errors stop early. A passing run is not a completion certificate.

### Historical evidence and practical limits

The earlier rules-router development set scored 41/44 (93.18%). Its misses were computer, weekend-screening and plane-travel paraphrases. That score is neither a Gemini result nor a held-out benchmark. Phase 1 had a user-reported 14-turn smoke pass with median 1.71 seconds and maximum 2.00 seconds; it is separate from the real-source Phase 2 run.

Hosted-model interpretation, transient provider failures, free quotas and network latency remain variable. One Uvicorn worker is required because conversation locks and refresh cooldowns are process-local. Locks can queue concurrent conversations. Session IDs are demo identifiers, not authentication. No production authorization, payment, seat-map or live inventory guarantee is claimed. Specified but unpublished attributes cannot be confirmed by model inference.

## 4. Reproduction, demonstration and remaining acceptance

### Run and configure

Use Python 3.11+ and install requirements-dev.txt for runtime plus tests. The runtime uses FastAPI, Pydantic, Uvicorn, Pillow and standard-library SQLite/HTTPS; no frontend build or provider SDK is required. Local .env supplies the Gemini key/model and optional Travelpayouts token. Process environment overrides file values. Keep the user's keys private and preserve the existing settings when updating files.

For Phase 2, first run scrape_sources.py --per-category 15 --pages 3 --cinemas 5, then python run_live.py and open port 8001. Later starts reuse the saved snapshot. Phase 1 remains python run.py on 8000. Data stays in var/live_sources.json; Phase 1 and Phase 2 SQLite files are separate. Refreshing source data does not require resetting conversation databases.

Run python -m pytest -q and node tests/voice_ui.test.cjs for development checks. With Phase 2 running, check_phase2.py --run-live --date 2026-11-20 sends up to 14 messages and saves phase2_live_results.json. Select a future date if needed. The runner uses the server's configured providers and can consume their quotas; it reads no keys itself. Its failure output and planner/execution timings assist diagnosis.

### Five-minute demonstration target

Use one Phase 2 conversation: product search and comparison; switch to tomorrow's cinema rows; switch to an explicit flight date/month; return to saved products. This gives at least three domain transitions. Show source links, actual departure dates and retrieval timestamps. Explain that cache prices are not live offers and that availability can be unknown. The voice transcript must wait for Send; photo results must be described as type-based candidates. Rehearse timing rather than claiming a measured five-minute run.

### Final manual checks still open

Compare several product prices/specifications and cinema dates/prices with their source pages. Check current flight links without expecting cached prices to remain unchanged. In a supported browser, verify microphone permission, stop/review/send behavior, installed read-aloud voices, image preview/clear/search, session restoration and narrow-screen layout. Do not mark these complete solely from smoke tests. The demo script includes a checklist and labels these items as pending.

### Delivery boundaries and sources

The implementation is a working real-source prototype with a successful user-reported smoke run, not a full booking platform. Retail/cinema data is sampled, flight results are cached and grouped, exact inventory is often unknown, and broader source coverage/scheduled refresh remain open. Future API integration requires a reviewed adapter, credentials and access verification; a plug-in boundary does not itself connect a new service.

Source references: organizer SuperChat project brief and immutable official JSON; https://birmarket.az/robots.txt; https://cinemastercard.az/robots.txt; https://support.travelpayouts.com/hc/en-us/articles/203956163-Aviasales-Data-API; https://ai.google.dev/gemini-api/docs. README links the dependency repositories. Snapshot retrieval timestamps, provider labels and user-run evidence should accompany any demonstration claims.
