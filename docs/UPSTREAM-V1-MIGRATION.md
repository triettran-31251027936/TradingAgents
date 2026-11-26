# TradingAgents 1.0.1 update review

This branch stages upstream v1 beside the existing Python desktop engine and adds
Vietnamese daily price history to the v1 CLI. Desktop integration is pending.

## Reviewed commits

| Commit | Change |
| --- | --- |
| `da95d50` | v1.0.0: replaces the Python/LangGraph workflow with a TypeScript research agent, specialist delegation, skills, compute/data tools and sessions |
| `81501e0` | Enables npm publishing of the release executables |
| `121265d` | v1.0.1: fixes report question/answer handling, long price-history windows, leap years, key messages and undersampled return correlations |

Upstream's [README](upstream-v1-README.md), [usage](usage.md),
[models](models.md) and [configuration](configuration.md) describe the new CLI.
Those documents describe upstream v1, not the current desktop controls.

## Architecture and migration requirements

| Feature | Upstream v1 | Desktop status / required work |
| --- | --- | --- |
| Orchestration | Main agent delegates Equity Research, Macro Strategy, Quant Research and Risk Analysis | Desktop continues the original analyst, Bull/Bear, Trader, Risk and Portfolio Manager graph; implement a separate v1 bridge before switching |
| Research methods | 16 built-in skills, plus user Markdown agents and skills | Sources imported intact; desktop discovery, selection and invocation remain to be connected |
| Planning | Review and revise a research plan | No native v1 planning controls yet |
| Evidence | Source IDs shared across tools and specialist contexts | Adapt Vietnamese source receipts, units and publication dates to v1 contracts; preserve missing-data and point-in-time checks |
| Data | Tauric data API plus other supported sources | Vietcap daily `price_history` and same-source pairs are wired into v1. Intraday, quotes, Vietnamese filings, fundamentals, news, ownership and other local data adapters remain pending. A model subscription does not supply a Tauric data key. |
| Models | Provider/model selection including DeepSeek and ChatGPT subscription | Codex CLI transport and per-role routing need a new v1 adapter; Antigravity is excluded from the active user configuration |
| Sessions | Resume, optional Tauric sync, PDF/Markdown export | Keep local desktop history and credentials separate; do not enable cloud sync implicitly |
| Limits | Time, tokens and steps | Map the existing paid-request budget and cache to v1 accounting |
| Trading | Upstream explicitly does not place trades | Retain the local paper execution extension, native user approval, 3M VND capital, fees, odd lots and settlement; a research answer must not become an executable order without validation |

The original engine, CLI, desktop and Python tests are retained byte for byte
against `af6dae7`. The v1 upstream files match
`121265d18d467ac717f7af99cb0d669c15d2e73c` outside the four adapter touchpoints
listed by the audit. Release automation is restricted to
the upstream repository. No updater, installer, account migration or data sync
runs as part of this merge.

## Checks

```sh
python3 scripts/audit_v1_staging.py
npm ci --ignore-scripts
npm run typecheck
npm run build
npm run lint
node --test scripts/test-vietcap.mjs
```

Run Python regression tests with the desktop engine's Python 3.12 environment.
Type checking and a successful build establish source compatibility only. Before
activating v1, require a live VHM or VIC run with Vietnamese evidence, provider
usage receipts, complete specialist reports and a validated paper decision.
Buy/sell execution must still require the user's approval. These acceptance
steps are pending; this PR remains a draft.

### Local validation on 2026-11-26

- Source audit: 193 v1 paths checked (four changed for Vietcap), 299 legacy
  engine/desktop/test files preserved.
- `npm run typecheck`, `npm run build` and scoped `npm run lint`: passed.
- Python regression: 124 passed across practice trading, role routing, desktop
  bridge and Vietnamese integration. Run using the engine venv interpreter so
  subprocess tests inherit installed dependencies.
- Compiled-JavaScript check: return correlation rejects three prices/two returns,
  covering the v1.0.1 insufficient-observations fix.
- Local standalone compilation ran out of disk space. GitHub Actions passed
  the executable build and upstream smoke test on the source-staging commit.
- `git diff --check` passes excluding unchanged upstream font-license files,
  whose original CRLF/trailing spaces are preserved.
- Full-repository Python Ruff reports 18 existing diagnostics in 13 legacy
  files. Every affected file is byte-identical to `af6dae7`; the staging audit
  passes. This existing CI failure is not waived by the successful scoped v1
  lint, and must be resolved before this draft is considered merge-ready.

### Vietnamese daily price tool

Use `VHM.VN` or `VIC.VN` to address the Vietnamese listing and `VNINDEX` for
the benchmark. The v1 engine queries the Vietcap daily chart without a data key.
It records Vietcap as the source and labels the date of the latest included
session. Equity prices are VND per share. The index is measured in points.
The adapter uses Vietcap's current adjustment basis, which can differ from
historically traded prices. It excludes unfinished daily bars and treats
revisions and full session coverage as unverified. That basis is unsuitable
for paper fills or point-in-time backtests without independent verification.

A live five-year `price_history` check on 2026-11-26 before the session opened
returned 1,243 daily candles per symbol, spanning 2021-11-29 through 2026-11-25.
The last closes reported by the provider were 61,100 VND for VHM, 245,400 VND for
VIC and 1,687.45 points for VNINDEX. Tests also exercise malformed responses,
market routing, mixed-market pairs and incomplete-session filtering.
