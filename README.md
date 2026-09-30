# AI Tester

A local AI agent that tests a website in a real, visible browser and writes a bug report you can trust.

**Status: Phase 1 (browser core) done.** Next up is Phase 2, the model and agent.

| Phase | Scope | Status |
|---|---|---|
| 0 | Seeded benchmark app, ground truth, evaluator | done |
| 1 | BrowserSession, observation, domain scope, tracing | done |
| 2 | Model provider, planner, executor, assertions | next |
| 3 | Detectors, baseline, bug analyzer, dedup | |
| 4 | FastAPI + SQLite | |
| 5 | React UI | |
| 6 | Tauri packaging | |

## Setup

```bash
uv sync                                  # Python env (evaluator, tests)
cd benchmark/seeded-app && npm install   # benchmark app
```

## Try the browser layer by hand

```bash
uv run python -m app.probe http://localhost:3000                  # fresh profile
uv run python -m app.probe https://staging.example.com --project crm   # persistent login
```

A visible Chrome opens. You then drive it with the same actions the agent will use (`click e12`, `type e7 hello`, `goto /cart`, `net`, `console`, `help`). Every run is recorded to `data/sessions/<id>/`.

## Layout

```text
backend/app/
  browser/                 BrowserSession, snapshot parsing, observation, element resolution,
                           network + console capture, persistent profiles
  safety/                  domain scope, secret-field masking
  storage/                 session folder layout and writers
  schemas/                 Observation, Element, ActionResult
  probe.py                 manual driver (python -m app.probe)
backend/tests/             unit tests + browser tests against a local fixture site
benchmark/
  seeded-app/              React app with switchable seeded bugs (localhost:3000)
  expected-results.yaml    ground truth: bugs, noise, scenarios
  evaluator/               scores session folders against the ground truth
  tests/                   evaluator unit tests + seeded-app ground-truth checks
data/                      local runtime data (git-ignored)
```

See [benchmark/README.md](benchmark/README.md) for how to run the benchmark.

## Tests

```bash
uv run pytest                                              # unit + headless-Chrome tests
SEEDED_APP_URL=http://localhost:3000 uv run pytest         # also the seeded-app checks
```

## How the browser layer works

- **Observation.** Built from Playwright's `aria_snapshot(mode="ai")`. The model gets a compact outline: landmarks, headings, lists and dialogs for context, `[ref]` for every actionable element, and table rows on one line. Large pages keep what is in or near the viewport and mark the rest as omitted. Iframes and open shadow DOM are included.
- **Refs are ephemeral.** Before every action the page is snapshotted again and the ref is re-resolved by role, name and container context (`app/browser/elements.py`). If the page changed so much that the match is ambiguous, the action fails with `AMBIGUOUS_ELEMENT` instead of guessing. This avoids clicking the wrong row of a re-rendered list.
- **Domain scope is enforced in code.** `navigate` checks the URL before sending any request. A context route blocks top-level navigations outside the scope, whether they come from clicks or popups. A `framenavigated` watchdog catches server-side redirects, which Playwright does not route, and returns to the last in-scope page. For localhost targets the port must match.
- **Evidence.** Every session folder contains:
  - `actions.jsonl`
  - `observations/`, with masked raw snapshots
  - `network/events.jsonl`: first-party API bodies, secrets redacted, error response bodies
  - `console/events.jsonl`: errors, warnings and uncaught exceptions, each tagged with the action that preceded it
  - `browser/events.jsonl`: dialogs, tabs, blocked navigations
  - `screenshots/`
  - `trace.zip`
- **Secrets.** Values of password, OTP and card fields are masked in observations. They are redacted in action logs and in captured request bodies.
- **Dialogs.** `alert` and `confirm` dialogs are dismissed, never confirmed, and reported to the agent as a notice.

Known limits:
- When a server redirect leaves the scope, the redirected GET request is sent before the page is pulled back.
- `trace.zip` contains DOM snapshots, which can include typed field values. Keep session folders local.
