# SuperChat - Phase 2 demo and final manual checks

Use the real-source application at http://127.0.0.1:8001. Phase 1 on 8000 is a
separate mock demo and must never be presented as real inventory. This script is
a five-minute target, not a measured rehearsal. Rehearse once before recording.

## Prepare before presenting

- Keep keys and .env off screen. Stop sharing the terminal before any configuration.
- Start the latest run_live.py and confirm Phase 2 / Sources on the page.
- Refresh data beforehand and wait for completion. Inspect source status; note
  partial/stale results honestly. Do not refresh while recording or running parity checks.
- Open one product and one cinema source link to establish what is actually available.
- Use a fresh conversation. Choose a future flight date/month with real cache data.
  The commands below use November 2026; replace them if they have passed.
- Keep phase2_live_results.json from the latest successful run for evidence.
  The supplied run was 14/14, median 0.23s, max 2.78s; it is not a full benchmark.

## Core walkthrough - target 4:40

| Time | Action / message | What to show |
| --- | --- | --- |
| 0:00-0:30 | Introduce the three-domain chat and read-only real-source mode | Source provenance; Gemini interprets requests, backend supplies facts |
| 0:30-1:10 | `Noutbukları göstər` | Published prices, source timestamps and links; only a collected sample |
| 1:10-1:40 | `İlk ikisini müqayisə et` then `Ən ucuzunun detalları` | Correct shortlist IDs, price difference and collected specifications |
| 1:40-2:20 | `Sabah hansı filmlər var?` | First switch: Shopping -> Cinema; actual dated source rows |
| 2:20-3:15 | `Bakıdan İstanbula 20 noyabr 2026 üçün uçuşları göstər` | Second switch: Cinema -> Flights; cached price and exact departure date |
| 3:15-3:50 | `Bakıdan İstanbula noyabr 2026 ayında uçuşları göstər` | Compare different days; never describe them as same-day alternatives |
| 3:50-4:20 | `Əvvəlki məhsulları göstər` | Third switch: Flights -> Shopping; saved product search restored |
| 4:20-4:40 | State the limitations and point to the current report | No real purchase/reservation; sampled records; cached fare caveat |

If fewer than two products match, comparison should clarify. Broaden the filter
or choose a verified populated category; do not fabricate a second result. If a
cinema date is absent, show the collected-date explanation and use an actual
published date. A month query is allowed to have few/zero cached prices. Do not
replace missing real results with F001/P001 mock records or fixed demo prices.

## Optional bonus segment - rehearse separately

Voice: start microphone, speak a short search, stop, edit the transcript, then
press Send. Show that Stop never sends automatically. If browser speech support
or permission is unavailable, show the text fallback rather than claiming success.

Photo: choose a clear product photo and click Search image. Explain that Gemini
estimates product type and the app returns catalog candidates. It does not certify
the same brand/model. The image is sent to Gemini only for the requested search.
Read-aloud depends on an installed language voice and is not guaranteed in AZ.

Language: `Show me laptops` then `Покажи ноутбуки`; compare source IDs. Source
names/UI labels may retain their original language. For a clear mixed example,
use `Find laptops and flights from Baku to Istanbul on 2026-11-20`. Narrow local
cases avoid a model request; broader mixed interpretation still depends on Gemini.

## Final manual acceptance checklist - not yet signed off

- [ ] Compare at least three product prices and body-color/spec fields with their
  source pages. Do not confuse keyboard color with case color.
- [ ] Compare at least two cinema rows: title, venue, date, time, price and link.
- [ ] Open a flight link. Note cache/current price differences instead of treating
  them as a guaranteed-price failure or silently overwriting the recorded result.
- [ ] Ask `Ən ucuzunun baqajı neçə kilodur?`; the current flight source must not
  invent baggage kilograms or available seats.
- [ ] Apply a price/color filter; remove price only, then color only, and verify
  that unrelated constraints stay intact. Unknown colors must remain unknown.
- [ ] Refresh the browser and verify conversation restoration; New conversation
  should start a separate session. Do not delete databases to demonstrate this.
- [ ] Verify source links open safely and there are no Phase 2 Add to cart/Reserve
  actions that imply real transactions. Website-side orders are not queried.
- [ ] Verify microphone permission, interim/final transcript, Stop, edit and Send.
- [ ] Verify image preview, Clear and Search; include an ambiguous/non-product photo.
- [ ] Inspect narrow-screen layout and optional read-aloud with installed voices.
- [ ] Rehearse the walkthrough and record its actual duration and any caveats.

Record real findings here with date, input, expected behavior, observed behavior
and source timestamp. Leave unchecked items pending; passing code tests does not
prove microphone, image recognition or visual layout works on every browser.

## Automated evidence

With run_live.py running, open a second terminal in D:\superchat:

```powershell
.\.venv\Scripts\python.exe check_phase2.py --run-live --date 2026-11-20
```

This can use free-provider quotas. Keep the server and source snapshot stable
throughout the check. PASS verifies bounded structural/routing/context conditions;
WARN flags source coverage; FAIL includes a response message. Manually review
facts even when all 14 checks pass. No held-out accuracy score is implied.

## Suggested spoken disclosure

“SuperChat combines product, cinema and flight searches in one conversation.
Products and cinema showtimes come from collected public source pages. Flight
prices come from cached Aviasales searches and must be reconfirmed at the source.
Gemini handles requests that need semantic interpretation; verified backend data
supplies the cards. Purchases and reservations finish on the source websites.”

## Phase 1 only, if requested by the evaluator

Start run.py on port 8000 and explicitly label it organizer-provided mock data.
Only there demonstrate cart totals, reservation proposals, Cancel/Confirm and
adjusted mock seat availability. Do not combine its records with the Phase 2
real-source walkthrough. The phase1_live_results.json and rules-router score
are separate evidence from the Phase 2 acceptance run.
