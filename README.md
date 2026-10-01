# AI Tester

A local AI agent that tests a website in a real, visible browser and writes a bug report you can trust.

**Status: all phases (0–9) done.**

| Phase | Scope | Status |
|---|---|---|
| 0 | Seeded benchmark app, ground truth, evaluator | done |
| 1 | BrowserSession, observation, domain scope, tracing | done |
| 2 | Model provider, planner, executor, assertions | done |
| 3 | Detectors, baseline, bug analyzer, dedup | done |
| 4 | FastAPI + SQLite | done |
| 5 | React UI | done |
| 6 | Human takeover and auth polish | done |
| 7 | Tauri packaging | done |
| 8 | Chrome extension | done |
| 9 | Autonomous exploration | done |

## Setup

```bash
uv sync                                  # Python env (evaluator, tests)
cd benchmark/seeded-app && npm install   # benchmark app
```

## Quick start

```bash
uv sync && (cd frontend && npm install)
ollama signin                        # once, for the default cloud model
uv run python scripts/dev.py         # API + UI; open http://127.0.0.1:5173
```

`scripts/dev.py` generates a fresh API token and starts the API server and the UI with it. Ctrl+C stops both. The UI has three screens:

- **New test**: enter a website and an objective, then Start testing. A project (the target site plus its browser profile) is reused when the URL matches.
- **Session**:
  - While running: the live plan with each step's checks, the activity feed (the agent's reasoning and every action), and the controls.
    - **Take control** pauses the agent so you can use the Chrome window yourself, for example to log in. **Resume AI** continues.
    - **Stop** ends the test and keeps the report and trace.
    - A banner appears when the agent wants to do something risky (delete, pay, place an order…): **Allow once** or **Refuse**.
  - When finished: the summary, the bugs, what could not be verified or was not tested, the plan with its check results, the timeline, and the evidence files (including the Playwright trace).
- **Bug**: expected and actual, steps to reproduce, screenshots, the failing requests with response bodies, console errors, and occurrences.

## Desktop app

```bash
uv run python scripts/build_desktop.py      # needs Node.js and Rust (https://rustup.rs)
open "desktop/src-tauri/target/release/bundle/macos/AI Tester.app"
```

The build produces `AI Tester.app` and a `.dmg`. It's a Tauri window around the same React UI, with the Python engine frozen by PyInstaller ([desktop/sidecar.spec](desktop/sidecar.spec)) and shipped inside the app. When the app opens, it:

- picks a free local port and a new token;
- starts the engine with them, and stops it on quit (gracefully, so a running test saves its trace and report);
- hands the address and token to the UI, which never sees them in a URL.

Settings, the database, sessions and browser profiles live in `~/Library/Application Support/dev.aitester.desktop/`. Edit `ai-tester.toml` there to change the model; the engine's log is `engine.log` in the same folder. The app uses your installed Google Chrome, and the model comes from Ollama as before (`ollama signin` for cloud models).

To install it, drag `AI Tester.app` (or open the `.dmg` and drag it) into **Applications**, then open it from Launchpad or Spotlight. The app is signed ad hoc, not with an Apple developer certificate, so the first time macOS may ask you to right-click it, choose **Open**, and confirm.

## Model setup (Ollama)

The default is the Ollama **cloud** model `gemma4:cloud`, reached through the local Ollama server:

```bash
ollama serve                 # if it is not already running
ollama signin                # once, connects this machine to your ollama.com account
```

To run fully local instead, set `name = "qwen3:4b"` (after `ollama pull qwen3:4b`) and `observation_max_chars = 6000` in [ai-tester.toml](ai-tester.toml). To skip the local server, use `base_url = "https://ollama.com"`, the model name without `:cloud`, and an API key in `AI_TESTER_MODEL_API_KEY`.

[ai-tester.toml](ai-tester.toml) configures:
- the provider, URL, model name and context window
- thinking (`false`, `true`, or `"low"`/`"medium"`/`"high"`)
- timeouts, agent budgets and the safety policy

`[model.planner]`, `[model.executor]` and `[model.analyzer]` can override any model setting per role. Set `AI_TESTER_CONFIG` to use another file.

Implementation notes:
- The Ollama provider uses the native `/api/chat`. It is the only Ollama API that can set the context window per request, and Ollama's default of 4096 tokens silently truncates page observations for local models.
- Some cloud models ignore the API's JSON-schema parameter. So the schema is also stated in the prompt, and replies are unwrapped from code fences before validation.
- Any OpenAI-compatible server (LM Studio, llama.cpp, vLLM, hosted APIs) works with `provider = "openai_compatible"`.

## Run the local API

```bash
uv run python -m app            # http://127.0.0.1:8765, token written to data/.api-token
```

Every request needs the header `X-AI-Tester-Token: <token>`. WebSockets pass it as `?token=`.

| Method | Path | |
|---|---|---|
| POST / GET | `/api/projects`, `/api/projects/{id}` | target URL, extra allowed domains, persistent login profile |
| POST / GET | `/api/sessions`, `/api/sessions/{id}` | create (and start) a session; details include steps, bugs and live state |
| POST | `/api/sessions/{id}/start` `/pause` `/resume` `/stop` | lifecycle. Pausing is how you take control of the visible browser |
| POST | `/api/sessions/{id}/confirm` | answer a risky-action confirmation (`confirmation_id`, `allow`) |
| GET | `/api/sessions/{id}/bugs` `/coverage` `/report` `/events` | results |
| GET | `/api/sessions/{id}/evidence`, `/files/{path}` | list and download screenshots, trace and logs |
| WS | `/api/sessions/{id}/events` | live events; replays earlier ones first, ends with `stream_end` |

Session status is one of `CREATED`, `RUNNING`, `PAUSED`, `WAITING_FOR_USER`, `COMPLETED`, `CANCELLED`, `FAILED` or `INTERRUPTED` (the server stopped mid-run). The outcome is `PASS`, `BUGS_FOUND`, `COULD_NOT_VERIFY` or `BLOCKED`.

Storage:
- **SQLite** at `data/app.db` holds projects, sessions, steps, actions, observations, bugs, bug occurrences and evidence. Migrations (Alembic) run automatically at startup.
- **Session folders** in `data/sessions/<id>/` keep the full artifacts; database rows point to them.

**Security.** The API drives a browser that may be logged in to real systems, so being on localhost isn't enough protection:
- It binds to loopback only.
- It rejects any Host header that isn't a loopback name, which blocks DNS rebinding.
- It rejects browser Origins that aren't listed in `[server] allowed_origins`.
- It requires the token, which web pages can't read.

One session can run per project at a time, since a browser profile can only be opened once. `max_concurrent_sessions` sets the limit overall.

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

### Latest results

The benchmark ran with `gemma4:cloud`, each scenario twice, in both app modes:

| App mode | Runs | Outcome correct | Bugs found | False positives | Mean time per run |
|---|---|---|---|---|---|
| All 4 seeded bugs on | 12 | 12 / 12 | 8 / 8 | 0 | 18 s |
| No bugs (`dev:clean`) | 12 | 11 / 12 | – | 0 | 17 s |

The one miss ended as `COULD_NOT_VERIFY`, because a guessed check didn't match the page; it did not report a false bug. With the local `qwen3:4b` on an 8 GB Mac, runs took 1–5 minutes each, and the planner often wrote weak checks.

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
  main.py, __main__.py     FastAPI app and server entry point (python -m app)
  api/                     routes, local-only security middleware, WebSocket
  services/                session manager (lifecycle, pause, confirmation), event hub
  db/                      SQLAlchemy models, repositories, Alembic migrations
backend/tests/             unit tests + browser tests against a local fixture site
frontend/                  React + TypeScript UI (Vite, react-query); npm test runs the reducer tests
scripts/dev.py             starts API + UI with a shared token
extension/                 Chrome extension (Manifest V3) that relays CDP from your tab
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

## Chrome extension: test your own tab

The extension in [`extension/`](extension/) tests the tab you already have open, using your own logins and cookies:

1. Open `chrome://extensions`, turn on Developer mode, click **Load unpacked**, and choose `extension/`.
2. In AI Tester, open **Chrome extension** and copy the API token into the extension's settings. The token changes each time AI Tester starts.
3. On any page, click the AI Tester icon, describe the test (or choose Explore), and click **Test this tab**.

**How it works.** The extension attaches `chrome.debugger` to that one tab and relays the Chrome DevTools Protocol to the local API (`/api/relay/{id}/extension`). The API presents the relay to Playwright as a browser (`/api/relay/{id}/cdp`), so the agent, the checks and the evidence capture all run unchanged in your tab.

- The relay never forwards commands that would close your browser or tab.
- Chrome shows its "is being debugged" bar while a test runs; closing it stops the test.
- The test stays within the tab's site.
- The API accepts the extension's `chrome-extension://` origin, but every request still needs the token.

## Exploration mode

Choose **Explore and find bugs** in the UI, or run `uv run python -m app.run --explore --url … [--objective "Log in with …"]`, to test a site without a specific objective:

1. **Log in.** If the start page is a login form, the agent logs in using the credentials from your notes. Without credentials, it asks you to log in.
2. **Crawl.** In code, it follows the site's own links breadth-first, up to `explore_max_pages`. It never follows "Log out", "Delete" and similar links.
3. **Workflows.** The model proposes `explore_workflows` workflows from the site map (creating records, forms, cart, settings…), and each one runs as an ordinary test.

Exploration reports **evidence**: server errors, broken links (a link the site shows that leads to an error page), uncaught exceptions and failed requests. Workflow objectives are written by the model, not by you, so a failed check inside one is *could not verify*, never a bug. To check what the app *should* do, use objective mode with stated expectations. The report adds pages discovered and visited, and workflows attempted and completed.

On the seeded app (`gemma4:cloud`):

| App | Runs | Result | False positives |
|---|---|---|---|
| clean | 3 | PASS 3 / 3 | 0 |
| buggy | 2 | found the HTTP 500 in 2 / 2 | 0 |

## When the agent needs you

These are detected in code, on every page the agent reads, so the model never gets to try them itself:

- **CAPTCHA:** the agent never tries to solve it.
- **MFA or one-time code prompt.**
- **Login page when the objective gives no credentials.**
- **Something only a person can do,** like opening an e-mail link; the agent can ask for this itself.

The test pauses, its status becomes `WAITING_FOR_USER`, and the UI tells you what to do. **Continue** makes the agent read the page again; **Skip this step** ends the step as `BLOCKED`. Waiting time doesn't count against the session budget. In the CLI, the same prompts appear in the terminal.

For projects that keep their login, **Set up login** opens Chrome with the project's profile at the target URL. Log in once, click **Done**, and later tests start logged in.

If a navigation is blocked because it's part of the login (single sign-on), add that domain under the project's **Extra allowed domains**.

## Writing objectives that can find bugs

A mismatch is reported as a bug only when the expectation is something you stated exactly. Everything else the agent assumes about the UI is a guess, and a failed guess is reported as *could not verify*, never as a bug. So be precise about what you expect:

| Instead of | Write |
|---|---|
| "verify the user returns to the login page" | "... returns to the login page at /login" |
| "verify an error is shown" | "... shows a validation error" (any `alert` counts) or quote the message: `"Enter a valid email"` |
| "verify the customer is listed" | "... is listed" (the agent uses its generated test data, which counts as stated) |

Quoted text, URL paths, e-mail addresses and the generated test data count as stated values. HTTP 5xx responses and uncaught JavaScript errors are reported whatever the objective says.

## How bugs are found

After the run, `app/detection` goes through everything recorded:

1. **Detectors.**
   - First-party HTTP errors: 5xx and failed requests are strong signals. Unexpected 404/405/410 are weak. 400, 401, 403, 409, 422 and 429 are treated as intended behaviour.
   - Uncaught exceptions are strong; console errors are weak.
   - Failed grounded checks are strong.
   - Third-party requests and scripts are ignored.
   - A 404 page the agent reached by typing a made-up URL is ignored.
2. **Baseline.** Errors present when the target first loaded (for example a console error on every page load, or a 401 from a session check) are ignored, including when they recur later.
3. **Incidents.** Signals from the same action are one candidate: a click that causes a 500, the app's console error about it, and the failed check.
4. **Analysis** (`[model.analyzer]`).
   - Strong candidates are always bugs: the model only writes the title, summary, expected, actual, severity and category.
   - Weak candidates become bugs only if the model confirms them. Otherwise they appear under `unconfirmed` in the report.
   - Without a model, strong candidates are described by rules.
5. **Deduplication.** The same method, path pattern and status, or the same normalized error text, or the same check, becomes one bug with several occurrences.

Evidence captured per bug:
- the requests with status and response body
- the app's console errors
- a screenshot, taken automatically at the moment an action caused a 5xx, network failure or uncaught exception
- `trace.zip`

`report.json` also lists what could not be verified and what was not tested.

## Hardware note (local models)

On an 8 GB machine, `qwen3:4b` is the practical local limit. Ollama keeps a local model in memory for `keep_alive` (15 minutes by default), and with Chrome and an editor also running, memory runs short: browser launches stall. Run `ollama stop qwen3:4b` before running the test suite. Cloud models don't have this problem.

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
