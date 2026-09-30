# AI Tester

A local AI agent that tests a website in a real, visible browser and writes a bug report you can trust.

**Status: Phase 0 (seeded benchmark) done.** Next up is Phase 1, the browser core.

| Phase | Scope | Status |
|---|---|---|
| 0 | Seeded benchmark app, ground truth, evaluator | done |
| 1 | BrowserSession, observation, domain scope, tracing | next |
| 2 | Model provider, planner, executor, assertions | |
| 3 | Detectors, baseline, bug analyzer, dedup | |
| 4 | FastAPI + SQLite | |
| 5 | React UI | |
| 6 | Tauri packaging | |

## Setup

```bash
uv sync                                  # Python env (evaluator, tests)
cd benchmark/seeded-app && npm install   # benchmark app
```

## Layout

```text
benchmark/
  seeded-app/              React app with switchable seeded bugs (localhost:3000)
  expected-results.yaml    ground truth: bugs, noise, scenarios
  evaluator/               scores session folders against the ground truth
  tests/                   evaluator unit tests + seeded-app ground-truth checks
data/                      local runtime data (git-ignored)
```

See [benchmark/README.md](benchmark/README.md) for how to run the benchmark.
