# AI Tester

A local AI agent that tests a website in a real, visible browser and writes a bug report you can trust.

**Status: Phase 2 (local model + agent) done.** Next up is Phase 3, the bug engine.

| Phase | Scope | Status |
|---|---|---|
| 0 | Seeded benchmark app, ground truth, evaluator | done |
| 1 | BrowserSession, observation, domain scope, tracing | done |
| 2 | Model provider, planner, executor, assertions | done |
| 3 | Detectors, baseline, bug analyzer, dedup | next |
| 4 | FastAPI + SQLite | |
| 5 | React UI | |
| 6 | Tauri packaging | |

## Setup

```bash
uv sync                                  # Python env (evaluator, tests)
cd benchmark/seeded-app && npm install   # benchmark app
```

## Model setup (Ollama)

```bash
ollama serve                 # if it is not already running
ollama pull qwen3:4b         # the default model
```

The model is configured in [ai-tester.toml](ai-tester.toml): provider, URL, model name, context window, thinking mode, timeouts, plus agent budgets and the safety policy. `[model.planner]` and `[model.executor]` can override any model setting per role, for example a larger model for planning. Set `AI_TESTER_CONFIG` to use another file.

The Ollama provider uses Ollama's native `/api/chat`, not its OpenAI-compatible endpoint. Only the native API can set the context window per request, and Ollama's default of 4096 tokens silently truncates page observations. Any other OpenAI-compatible server (LM Studio, llama.cpp, vLLM, hosted) works with `provider = "openai_compatible"`.

## Run a test

```bash
uv run python -m app.run --url http://localhost:3000 \
  --objective "Log in with demo@example.com / password123. Create a new customer and verify it appears in the customer list."
```

Options: `--headless`, `--project <id>` (persistent login), `--allow-risky` (skip confirmation for delete/pay/order/send), `--allow-domain` (e.g. SSO).

## Run the benchmark

```bash
cd benchmark/seeded-app && npm run dev     # in another terminal
uv run python -m benchmark.run             # all scenarios, scored against expected-results.yaml
uv run python -m benchmark.run cart-total --repeat 3
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

## How the agent decides that a step passed

- **The planner writes the checks up front.** Each step gets 0–3 success criteria: `url_contains`, `text_visible` (optionally inside a named table or list), `element_present`, `field_value`, `request_succeeded`, `sum_equals`. The executor can only ask for them to be evaluated; it cannot change or weaken them.
- **Code evaluates every check** (`app/agent/assertions.py`) on a fresh snapshot and the step's network log. The model never declares a pass.
  - Text typed into input fields does not count as "visible text".
  - `sum_equals` only accepts amounts that are actually on the page, then does the arithmetic in Python.
- **Grounded vs guessed.** A check is *grounded* when its expected value comes from the objective or the generated test data, and *guessed* when it's about UI details. Code decides which by comparing tokens. Only a grounded check that keeps failing, or an HTTP error on the step's own request, makes a step `FAILED`, which is a bug. A failed guess means `COULD_NOT_VERIFY`, not a bug.
- **Checks have to prove something.** A check that was already true before the step started doesn't complete it automatically. A grounded URL check doesn't count if the agent opened that URL itself; the application has to get there.
- **Unchecked steps.** Intermediate steps the planner couldn't write a usable check for complete on the executor's word, after at least one action, and the report marks them. The last step must always have code-evaluated checks.
- **Recovery.** A step that stalls (the action limit, the same action repeated three times, or the page not changing) is replanned from the current page. Grounded checks are carried into the new plan. After a step that did not pass, the rest are `SKIPPED`.
- **Session outcome:** `PASS`, `BUGS_FOUND`, `COULD_NOT_VERIFY`, `BLOCKED`, `FAILED` or `CANCELLED`, written to `report.json`. Every model call, with its prompt, response, latency and validation errors, goes to `model_calls.jsonl`.

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
