# Provenance (v0)

An AI copilot for a multi-manager fund platform where every number is computed deterministically and every claim cites its source. The LLM explains; a deterministic engine does every calculation.

This is a personal side project. It is not connected to any employer system and must not be pushed or shared anywhere.

## Run it

```bash
./provenance/run.sh all      # venv, synthetic data, pipeline, tests, evals
./provenance/run.sh serve    # http://127.0.0.1:8000
```

Sign in from the header dropdown: a PM for Manager A to D, the risk team, or the platform engineer. Tokens are seeded in `data/synthetic/generated/users.csv`.

Individual steps: `run.sh generate | pipeline | test | evals | serve`.

## What v0 does

| Layer | Status | Notes |
|---|---|---|
| Synthetic data (managers A to D, 60 trading days, 4 file formats) | done | seeded, byte-identical reruns; five planted problems recorded in `planted.json` |
| Onboarding adapters | done | one class per manager, registered in `ingestion/adapters/registry.py`; adapters never touch the DB |
| Quality rules and quarantine | done | 8 rules; every planted problem lands in quarantine with rule and reason |
| Idempotent pipeline with audit log | done | file content hashes, unique trade keys, natural-key upserts; rerun changes nothing; a missing source ends `partial` and is named |
| Risk and P&L engine | done | positions (average cost), P&L, attribution, exposure, Black-Scholes Greeks, concentration; Decimal throughout; every result carries the row ids it used |
| API gateway with roles | done | token auth, permission filter applied before any tool or retrieval call |
| Document ingestion and retrieval | done, offline | heading-aware chunking; manager_id on every chunk; retrieval filters in SQL |
| LLM copilot | done, two narrators | deterministic planner (offline) and Anthropic tool loop (needs `ANTHROPIC_API_KEY`); same tools, guardrails, and logging |
| Number-check guardrail | done | any number in the answer not backed by a figure with source rows rejects the answer; one retry, then refusal |
| Dashboard | done | firm view, pod view, pipeline view, chat with clickable figures and citations |
| Eval harness | done | 30 questions, scorecard in one command |

Current scorecard (planner mode, offline):

```
numeric_accuracy_pct                100.0%
citation_correctness_pct            100.0%
required_warning_rate_pct           100.0%
refusal_correctness_pct             100.0%
hallucinated_number_rate_pct          0.0%
overall                            30/30
```

## v0 substitutions, and how to swap them

These are deliberate so the whole system runs with one command and no network or keys.

| Spec | v0 | Swap |
|---|---|---|
| Postgres + pgvector | SQLite, `NUMERIC` as Decimal strings, embeddings as JSON | schema in `infra/migrations/001_init.sql` is written in portable SQL; replace `db.connect` and the cosine in `copilot/retrieval.py` with pgvector `<=>` |
| yfinance + second vendor, FRED, EDGAR | two synthetic price sources, a synthetic rate curve, synthetic filing excerpts labelled as such | `ingestion/prices.py` fetchers already carry timeout, retry, and `SourceError`; add real fetchers behind the same functions |
| OpenAI/Anthropic embeddings | hashed bag-of-words vector, dimension 512 | replace `ingestion/documents.embed` |
| React + Recharts | one static HTML page served by FastAPI, inline SVG-free bars | `web/` is self-contained; port view by view |
| LLM-written memos | template memos, labelled SYNTHETIC | run a one-off generation script into `data/synthetic/generated/memos/` |
| cron / APScheduler | `POST /api/runs` (engineer role) and `run.sh pipeline` | wire either to a scheduler |

The LLM narrator in `copilot/agent.py` uses the Anthropic SDK with a manual tool loop against `claude-opus-5-5`. It has not been exercised in this environment because no key was available. Set `ANTHROPIC_API_KEY` to enable it; set `PROVENANCE_LLM=0` to force the planner. The guardrails run after either narrator, so a wrong number from the model is rejected the same way.

## Architecture

Data flows top to bottom: sources, ingestion, storage, compute, API, app. The deterministic path produces every number; the AI path only retrieves and explains. The one horizontal arrow is the copilot calling the engine as a tool.

Request flow for "Why is Manager C down this week?":

1. The app posts the question with the user's token.
2. The gateway resolves role and manager from `users`; a PM may only ask about their own pod.
3. The copilot calls `get_pnl_attribution("C", week_start, as_of)`; the engine returns exact figures plus the trade and price row ids used.
4. The copilot calls `search_documents` filtered to Manager C and firm-wide documents.
5. Any price row for the period that was quarantined attaches a warning to the mark, which the answer must carry.
6. The number check confirms every figure in the text came from a tool result.
7. The app shows figures that open their source rows and citations that open the passage. The exchange is written to `copilot_logs`.

## Design decisions

| Decision | Chosen | Why |
|---|---|---|
| Who does math | the engine | reproducible and unit-tested; the LLM only narrates |
| P&L formula | change in market value minus net cash invested, per instrument | exact from rows alone; realized from average cost; unrealized is the remainder |
| Marks | primary source, then secondary, then most recent prior close within 7 days | a quarantined primary price never silently becomes a mark; the fallback is visible as a warning |
| price_jump scope | stocks only | option premiums near the money legitimately move more than any sane stock threshold; the synthetic run showed 344 false positives before this change |
| Idempotency | file content hash + unique trade key + natural-key upserts | a resent file cannot double count; a rerun is a no-op |
| Permissions | in the SQL `WHERE` and the retrieval query, before the LLM sees text | the model cannot leak what it never saw |
| Money | Decimal everywhere | floats cannot represent most decimals exactly; see the 10,000-trade precision test |

## Layout

```
provenance/
  config.py            env-driven settings
  db.py                SQLite connection, migrations, Decimal helpers
  models.py            RawTrade, Trade, PriceRow, QualityFailure
  ingestion/
    adapters/          manager_a..d.py, registry.py, base.py
    quality.py         rules + quarantine context
    prices.py          source fetchers with timeout/retry
    documents.py       chunking + embedding
    pipeline.py        one run, idempotent, writes pipeline_runs
    reliability.py     with_retry, TransientError/PermanentError
    observability.py   JSON logs with run_id, alerts
    tests/             quality-rule fixtures
  engine/
    types.py, positions.py, pnl.py, attribution.py, exposure.py, greeks.py, loader.py
    tests/             hand-calculated P&L, textbook Greeks, Decimal precision
  copilot/
    tools.py           engine wrapped as tools, permissions enforced here
    retrieval.py       permission-filtered search
    answer.py          schema, number check, refusal detection
    agent.py           planner narrator, LLM narrator, orchestration, logging
    evals/             questions.yaml, run_evals.py, last_scorecard.json
    tests/             API permission integration tests
  api/                 main.py (FastAPI), auth.py
  web/                 index.html dashboard
  data/synthetic/      generator.py and generated files
  infra/               migrations/, provenance.db, last_run.log.jsonl
  run.sh
```

## Planted problems and where they land

| Planted | Rule | Where to see it |
|---|---|---|
| ORCL stale price on two days | `stale_price` | Pipeline view; ask "Were any of today's prices stale?" |
| GILD close 100x too high in the primary source | `price_jump` | Pipeline view; GILD is marked from the secondary source that day with a warning |
| Manager A file resent with 12 duplicate trades | `duplicate_trade` | Pipeline view; trade count unchanged |
| Manager B option with a past expiry | `option_sanity` | Pipeline view |
| Manager D trade in unknown symbol ZZZQ | `unknown_instrument` | Pipeline view |

## Next

Phase 5 items not in v0: Slack or email alert sink (the hook exists in `observability.alert`), real data fetchers, a React port of the dashboard, and the demo video.
