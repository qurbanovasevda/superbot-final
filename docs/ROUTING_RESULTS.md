# Routing scenario results

Reproducible checks: tests/test_chat.py. This is an in-development scenario suite, not a held-out accuracy benchmark.

| Brief scenario | Expected sequence | Observed sequence | Result |
|---|---|---|---|
| 1 | Flights → Cinema → Shopping | Flights → Cinema → Shopping | PASS |
| 2 | Cinema → Shopping → Cinema | Cinema → Shopping → Cinema | PASS |
| 3 | Shopping → Follow-up | Shopping → Follow-up | PASS |
| 4 | Flights → Follow-up | Flights → Follow-up | PASS |
| 5 | Mixed | Mixed | PASS |
| 6 | Ambiguous | Ambiguous | PASS |
| 7 | Out of scope | Out of scope | PASS |
| 8 | Ambiguous | Ambiguous | PASS |

8/8 scenario category sequences pass. Functional assertions also verify returned records, date resolution, references, isolation and constraints. See the full pytest log for 40 checks.
