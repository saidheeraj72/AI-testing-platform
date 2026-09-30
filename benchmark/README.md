# Benchmark

A small app with known bugs plus an evaluator. It tells us whether a change to the agent or model made things better or worse.

## Seeded app

```bash
cd benchmark/seeded-app
npm run dev          # all seeded bugs on
npm run dev:clean    # no bugs (false-positive baseline)
SEEDED_BUGS=BUG-001,BUG-003 npx vite
```

Runs on http://localhost:3000. Demo login: `demo@example.com` / `password123`. State is in memory and resets on restart.

| Id | Bug | Workflow | Detected by |
|---|---|---|---|
| BUG-001 | `POST /api/customers` returns 500 | Customers → New customer | HTTP detector |
| BUG-002 | Cart total ignores quantity | Shop → Cart | assertion (sum of line totals) |
| BUG-003 | Settings accepts an invalid email | Settings | assertion (expected validation error) |
| BUG-004 | Logout does not redirect to login | Log out | assertion (URL) |

Noise that is always on. Reporting any of these counts as a false positive:

| Id | What | Should be removed by |
|---|---|---|
| NOISE-001 | `analytics.seeded-app.invalid/tracker.js` fails to load | first-party filter |
| NOISE-002 | `[legacy-widget]` console error on every load | baseline filter |
| NOISE-003 | `GET /api/me` returns 401 when logged out | expected-4xx handling |

`GET /api/customers` has a deliberate 600 ms delay to exercise waiting.

### Harness-only endpoints

- `GET /__bench/config` returns the active bugs
- `POST /__bench/reset` resets all data. Call it before every scenario run, because the cart and customers persist across runs.

The agent must never be told about `/__bench/*` or `expected-results.yaml`.

## Scenarios

```bash
uv run python -m benchmark.evaluator scenarios
```

Each scenario's `objective` is passed to the agent verbatim. `checkout-single-item` and `browse-customers` are controls: no bug is reachable, so the correct outcome is `PASS`.

## Scoring a run

```bash
uv run python -m benchmark.evaluator score data/sessions/<id> [more sessions...] \
    [--scenario customer-create] [--active all|none|BUG-001,...] [--json]
```

`--scenario` and `--active` can be omitted when the session's `manifest.json` contains `"benchmark": {"scenario": ..., "active_bugs": [...]}`.

Each reported bug is labelled one of the following:

- `expected:<id>`: true positive
- `duplicate:<id>`: same bug reported again (a deduplication failure, not counted as a false positive)
- `incidental:<id>`: a real active bug found outside the scenario (not a false positive)
- `noise:<id>` / `inactive:<id>` / `unmatched`: false positive

The summary reports outcome accuracy, recall, precision, duplicates, model calls with invalid-call rate, and mean duration.

### Session contract the evaluator reads

```text
report.json         outcome, steps[].status, bugs[] {title, summary, expected, actual, url,
                    evidence {network[] {method, url, status}, console[]}}
manifest.json       duration_ms, benchmark {scenario, active_bugs}        (optional)
model_calls.jsonl   one object per call; validation_error, latency_ms     (optional)
```

The agent in Phases 1–3 must write these fields.

## Tests

```bash
uv run pytest                                                   # evaluator unit tests
SEEDED_APP_URL=http://localhost:3000 uv run pytest -m seeded_app # ground truth, needs app + Chrome
```

The `seeded_app` tests read `/__bench/config` and check the buggy or the correct behaviour in a real browser. Run them against both `npm run dev` and `npm run dev:clean` whenever the app changes.
