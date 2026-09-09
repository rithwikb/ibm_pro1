# Calibration Notes — Person E

## Day 0 Calibration Run

**Date:** _fill in today's date_
**Bob session used for calibration:** Run one Bob Agent session and ask it to summarise
`schemas/task_schema.json` — short context, structured task, good baseline.

**Approximate coins consumed:** _fill in after running — check the usage gauge in Bob_

---

## Spending Strategy

### Estimated cost breakdown per pipeline run (stubs are FREE — only real Bob calls cost coins):

| Stage | Coin estimate | Notes |
|-------|--------------|-------|
| PM Agent call | ~2–3 | Short context: feature_request → acceptance_criteria |
| Architect Agent call | ~3–5 | Reads file tree + feature request |
| Coding Agent call | ~5–8 | Reads scoped files (3–5 files) + plan |
| Testing Agent call | ~4–6 | Reads code diff + acceptance criteria |
| Review Agent call | ~4–6 | Reads code diff + security checklist |
| Reflection Agent call | ~3–5 | Reads current prompt (~1KB) + failure report |
| **Total per full run (no retries)** | **~21–33** | Happy path |
| **With 1 retry (coding rerun)** | **~27–41** | One rejection + one retry |
| **With 2 retries (worst case)** | **~33–49** | Two rejections + two retries |
| **Manager Agent** | 0 | Pure Python — no LLM, no coins |

_Update with real numbers after your first calibration run._

### Key cost levers (in order of impact):

1. **File scoping** — the Architect Agent's `scoped_files` list determines how much
   context the Coding, Testing, and Review agents receive. Keeping it to 3–5 files
   instead of the whole repo is the single biggest cost lever.
2. **Retry cap** — hard cap at 2 retries means worst-case multiplier is 3×, not
   unbounded.
3. **Rolling window = 5** — Reflection Agent only triggers after 3+ runs below
   threshold, meaning it fires at most once per 3–5 full-chain runs (not every run).
4. **Stubs during development** — Week 1–2 stub agents cost zero coins. Only replace
   stubs with real Bob calls when needed, and only run full-chain real calls in Week 3.

### Personal budget rules:

1. **Week 1** (standalone testing only): 0 coins — all agents tested with stubs/mocks,
   no real Bob calls needed from Person E
2. **Week 2** (integration, stubs active): ~5–10 coins — calibration run + 2–3 pipeline
   runs with at least Reflection Agent real (others still stubbed)
3. **Week 3** (full real runs + rehearsal): budget ~25–35 coins — 3 real end-to-end runs
   for rehearsal + 1 live demo run
4. **Hard rules:**
   - Never run more than 2 retries in any single session
   - Always scope files narrowly (3–5 files max per Architect Agent call)
   - Spread expensive full-chain runs across teammates' accounts in Week 3
   - Always check the usage gauge after each run and log it in the Integration Run Log

### Total personal budget: 40 coins

**Estimated safe number of full end-to-end runs:** ~4–6 runs (at ~7–10 coins per run
when other agents are stubbed; ~1–2 at full real cost ~21–33 coins)

_Calculate exactly after your first calibration run._

---

## Actual Run Log

| Date | Run type | Coins consumed | Notes |
|------|----------|---------------|-------|
| — | calibration (Bob explain task schema) | — | fill in |
| — | — | — | — |
