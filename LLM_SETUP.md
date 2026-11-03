# Gemini setup and diagnostics

## Configure

Create `D:\superchat\.env` next to `run.py`, using your own private key:

```dotenv
SUPERCHAT_CHAT_MODE=llm
GEMINI_API_KEY=YOUR_PRIVATE_KEY
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_TIMEOUT_SECONDS=90
```

Keep billing disabled on the selected Free Tier project. The application does
not inspect billing tier; a model name does not guarantee free use. Quotas and
model access are controlled by Google. No alternate provider, automatic retry,
automatic model switch or paid fallback is configured. OpenAI variables are ignored.
Never send your key or `.env` in chat or screenshots. `.env` is ignored by git.

Terminal variables take precedence over `.env`, even when empty. If you previously
set them in PowerShell and now want to use the file, clear the overrides:

```powershell
Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:GEMINI_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:SUPERCHAT_CHAT_MODE -ErrorAction SilentlyContinue
```

Stop the old server with Ctrl+C, then run from `D:\superchat`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe run.py
```

No new package is required. Open http://127.0.0.1:8000/health and check
`chat_mode=llm`, `llm_provider=gemini`, `llm_configured=true`. This is configuration
status, not proof that a live request succeeded. The UI should say Ready · Gemini.

## Request flow

An ordinary Gemini turn uses one provider call containing the question, short
history, per-domain state, saved preference statements and official mock catalog.
The structured response contains allowed backend actions and an optional answer
draft. The backend validates and executes actions. Draft record IDs and numeric
claims are checked against execution evidence; an invalid or absent draft becomes
a deterministic verified summary. No second answer-generation request is made.
These checks do not prove every semantic claim correct.

Explicit durable preferences are stored as verbatim statements, independently
of the short history. Current user constraints should override prior preferences;
interpretation and application still depend on the model. Search/detail/comparison
cards use current seat counts after mock reservations.

Chat cart changes and reservations first create a pending proposal. Confirm/Cancel
for that proposal is local. A different message invalidates the pending proposal.
UI card buttons use the REST endpoints directly. Gemini selects English, Azerbaijani or Russian response language and keeps it for
short follow-ups and local Confirm/Cancel replies. UI labels and official record
names keep their original spelling. Full language parity needs live testing.

## Diagnostics

- Connection refused at 127.0.0.1: start the local server and keep its terminal open.
- Missing key: verify `.env` location, terminal overrides, then restart.
- HTTP 400/401/403/404: read the controlled error; check request/model/key/project access.
- HTTP 429: stop and wait for quota availability; no automatic retry is performed.
- Timeout: investigate network/provider delay. Raising the timeout only waits longer.
- Network diagnostic HTTP 403 without a key: HTTPS was reachable; authentication,
  model access and free quota were not verified.

`check_gemini_connection.py` checks connectivity without a key.
`check_gemini_api.py` sends a minimal generation request with the configured key.
Neither demonstrates full chat correctness. Do not share raw secret-bearing logs.

## Acceptance and timing

With the server running, use another terminal:

```powershell
.\.venv\Scripts\python.exe check_phase1.py
.\.venv\Scripts\python.exe check_phase1.py --run-live
```

The first command checks health only. The second sends 14 messages across the
eight official development scenarios in new sessions. In LLM mode it consumes
provider quota. It does not send reservation confirmations or cart writes.
It saves `phase1_live_results.json` with routing labels, basic behavior checks,
record IDs and measured times. It omits response text and provider error bodies.
Stop-on-error avoids repeatedly hitting a failing provider. Do not automatically
rerun after quota errors. PASS is not full factual or conversational acceptance:
manually review the browser answers using DEMO.md.

Development verification: 99 offline tests passed; provider results were mocked.
The old 41/44 rules-router result is not a Gemini accuracy score. Live latency,
model reliability and browser/mobile behavior require local measurement.

References for account-specific access, quotas and billing:

- https://aistudio.google.com/apikey
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/billing
