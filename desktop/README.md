# Trading Agents desktop

A dedicated desktop surface for this TradingAgents fork. The UI contains only
TradingAgents research, role reports, backtest, memory/reflection, portfolio
context, model/data configuration, checkpoints and authentication. It replaces
the generic AgentsRoom renderer/main process rather than hiding a project list.

The original Python framework remains the engine. Analysis uses its
`create_run_state`, `checkpoint_scope`, `checkpoint_input`, `stream_run`,
`record_decision`, checkpoint cleanup and report writer, matching the upstream
CLI streaming path. Backtest delegates to `run_backtest` and `summarize`.
Settlement and reflection use the original graph methods. Every upstream
configuration key remains accessible in advanced JSON; local output/cache/memory
paths are managed by the desktop. Optional save/HTML/show flags mirror the CLI.

## Authentication

- `codex_cli`: the installed Codex CLI's sign-in, via the existing CLI adapter.
- `agy_cli`: the installed agy CLI's sign-in; experimental on the checked version.
- **ChatGPT Subscription / `chatgpt`**: the original TradingAgents Plus/Pro OAuth
  provider. Settings supports browser sign-in, loopback callback, pasted callback,
  model discovery and revocation. This is distinct from the CLI adapters.
  It uses the plan's limits; temperature/max_tokens are not applied by the plan.

Credentials continue to be managed by the original provider. The app does not
copy Codex tokens into TradingAgents, request keys in chat or store tokens in its
renderer. OAuth callback mailboxes are temporary private local files.

## Build on this Mac

The checkout and its `.venv` must remain at the path embedded during the build.
The Python dependencies are the same environment used by this fork. The local
AgentsRoom app supplies the Electron runtime; its original app is read-only.
The output contains a new seven-file ASAR with our renderer, preload, main,
stylesheet, logo and package metadata. Generic AgentsRoom UI/services are absent.
Runtime license/notice files are preserved.

```sh
.venv/bin/python desktop/build_macos.py \
  --shell /Applications/AgentsRoom.app \
  --output '/private/tmp/trading-agents-build/Trading Agents.app'
```

The builder refuses to overwrite an existing output or modify the source shell,
renames the executable/helpers consistently, creates a separate bundle ID,
recomputes ASAR integrity and signs/verifies the local bundle. Install the staged
app as `/Applications/Trading Agents.app` after closing an earlier instance.
The default config uses `config/agentsroom.env`/`.env`/the example profile;
settings saved in `config/desktop.json` stay private and are ignored by Git.

## Verification boundaries

Tests cover the original streaming/persistence/report boundaries, path safety,
portfolio, backtest delegation/resume, OAuth state/PKCE/mailbox cleanup,
callback accuracy, optional reports and configuration isolation. A complete
real-Codex original-graph run for FPT.VN completed before this UI was built.
A later real run started from the desktop completed its four analysts, Memory
and Bull Researcher, then the CLI exited with code 1; its checkpoint was retained.
Do not label that second run completed. No live desktop backtest or completed
browser-consent sign-in is claimed.

All original workflow/data/memory/backtest/portfolio/report modules are compared
against the pinned upstream commit by `scripts/audit_upstream_core.py`. Keeping
all source features does not guarantee data coverage for Vietnam or investment
accuracy. In particular, the checked default sources lack FPT.VN ticker news;
StockTwits returned 404, FRED lacked its data key and Polymarket was unreachable.

## Per-role providers and DeepSeek

Settings contains dedicated Codex, Antigravity and DeepSeek cards, private key
entry, connection test and a team/role assignment table. Inherit the original
quick/deep tiers or assign each role its own provider/model. Presets cover two
CLI providers, Codex+DeepSeek and all three. Model IDs come from the original
catalog and remain editable. Actual API/CLI access must be valid for each role.

`model_routing.py` uses LangGraph metadata (including enclosing analyst
namespaces) to choose a provider. Prompt text cannot switch a role. Tool binding
and structured output are delegated to that provider's original client. The
same original graph shape and backtest loop remain in use. A scoped graph
factory in the isolated backtest job applies the model assignments there too.
Empty overrides preserve the existing upstream checkpoint signature.

`credentials.py` writes only the DeepSeek key into the framework's original
ignored dotenv file, with mode 0600, retaining unrelated entries. The renderer
receives a configured boolean, never the stored key. Connection tests are
explicit user actions and send one small model request. No API call is claimed
before a real key is supplied.

## Upstream audit follow-up

The default remains the original Quick/Deep allocation. Quick serves the four
analysts, Bull/Bear, Trader and the three risk analysts; Deep serves Research
Manager and Portfolio Manager. Memory calls Quick only for due reflections.
Role overrides are explicitly optional and are not enabled in the original-mode
live verification. Codex `default` omits --model and ignores user config, so it
must not be advertised as automatically choosing a distinct stronger Deep model.

The original four-analyst fan-out can issue four Codex CLI requests concurrently;
the default per-provider limits are Codex4 and agy1 (experimental), adjustable via
TRADINGAGENTS_CODEX_MAX_CONCURRENCY / TRADINGAGENTS_AGY_MAX_CONCURRENCY (1–8).
Four real Codex requests succeeded concurrently with four isolated CLI processes.
API-specific generation controls unavailable in the adapter are reported, not
claimed to be forwarded. CLI prompt/schema transport is not identical to SDK
wire messages and does not guarantee identical model behavior.

Each saved report uses a unique timestamped folder and includes execution.json
plus a model/transport appendix. `show=True, save=False` displays an ephemeral
complete report without adding it to saved history. The clear-checkpoint control
calls the original utility, requires native confirmation and is blocked during
a job. A real original-mode UI run resumed its retained checkpoint and completed
all13 report sections; its earlier provider exit was not replaced with fake output.

### Dedicated Trading Agents integration

The final advisory council was removed at the user's request. Original graph
roles can be distributed across Codex CLI, DeepSeek API and Antigravity/agy;
there is no extra final council or synthesis phase. See DESKTOP-INTEGRATION.md
for the current role routing, canonical VN data, reports, execution schema and
consent-gated 3M VND paper account. Older saved reports describe their original
run settings and do not redefine the current account or next-run model routing.

### Source credentials and report capacity

Settings exposes separate masked FRED and Alpha Vantage keys below the model
providers. Keys use the original FRED_API_KEY/ALPHA_VANTAGE_API_KEY dotenv names,
remain local, and are never returned to the renderer or exported into reports.
Saving a key does not change vendor selection or imply coverage of Vietnamese
tickers. FRED still requires the user's own key; no key has been provisioned by
the integration. Alpha Vantage remains optional.

The balanced profile retains6 Codex/6 DeepSeek/1 agy. DeepSeek analysis gets a
4096-token output ceiling after real2048-token reports were truncated even with
low effort; Memory is capped at2048. The18-request cap, zero SDK retries and
incomplete-report execution gate remain. This increases permitted capacity,
not the number of requests or a guarantee of complete output.

The2026-11-03 installed-engine run finished News without a length stop, but
failed at Market when the OpenAI-compatible endpoint returned an unexpected
None response. Four roles completed; no full report or execution acceptance
is claimed. The original failure proof and source receipts are retained.

### Desktop checkpoint receipts

An enabled graph checkpoint also keeps private desktop metadata under
data_cache_dir/desktop-checkpoints. Its identity is the hash of the original
graph thread ID, which already includes model settings, evidence and portfolio.
This retains typed PM decisions, incomplete-output flags, quantitative handoffs,
grounding, usage receipts and paid attempt counts across processes. Paid attempts
are persisted before the model is called, including requests that later fail.
The metadata contains no provider keys or raw endpoint configuration.

Analysis and backtest retain their completed graph checkpoint until report and
evidence export succeeds. A retry can export the saved state and decision without
calling completed nodes again. Fresh runs do not inherit a completed run's
quality flags. Legacy checkpoints lacking desktop metadata fail before inference,
instead of silently resetting the budget or treating partial reports as complete;
keep them for inspection or explicitly use the existing clear-checkpoint action
to start a new analysis. Existing saved reports are unchanged.

### Registry quantities after equitization

VSDC histories can contain an IPO auction tranche followed by registration of
the whole company. Those two quantities are not additive. The parser supports
this only for the explicit auction/post-equitization descriptions in the first
two chronologically ordered rows, with the entire subsequent history reconciling
to the identical header/footer current total. All reported quantities and
certificates remain retained; the two initial entries have no invented issuance
delta. Other unexplained arithmetic mismatches still fail validation.

This resolves the BSR history at https://vsdc.vn/vi/s-detail/6571. Current shares
are still not automatically outstanding shares: broker counts, reported charter
capital, zero treasury/preferred capital and statement/registration dates must
independently pass the existing valuation checks. The reconciliation method is
included in the canonical context. Snapshot version16 prevents reuse of an
older composite snapshot that withheld these inputs.

### Empty provider responses

DeepSeek adapters reject null completions, empty/invalid JSON and missing
completion choices with a typed DeepSeekResponseError. Error messages do not
echo provider response bodies. Both synchronous and asynchronous calls retain
their failed usage receipts; the desktop checkpoint retains the paid attempt.
No report, decision or automatic retry is fabricated. This improves failure
handling, not provider availability. The cause of an earlier real endpoint
returning None was not established by the response shape alone.

Read Codex CLI availability using the same installed CLI's app-server
account/rateLimits/read method when checking a live mixed-provider run. A rate
snapshot from another desktop session is not sufficient evidence that this CLI
has the same availability. This read-only probe initializes the protocol and
reads limits only; it creates no thread/turn and consumes no inference tokens.
It is an acceptance tool, not an automatically installed quota preflight.

### Backtest export commit and score display

The desktop sweep commits its final decision to the original memory log only
after desktop evidence export succeeds. Otherwise the original grid's skip
logic would treat an export failure as a finished cell. A resumed grid now
exports the completed checkpoint without repeating model calls, then commits
one memory entry and clears the checkpoints. The original scoring logic and
standalone upstream backtest remain unchanged.

The results view shows resolved, pending and unscored REVIEW cells and the
actual logged scoring window. Hold has no directional hit rate. Alpha is a
stock-minus-benchmark outcome across independent cells, not paper-account P&L.
Provider failure messages returned by backtest/settlement pass through the same
key-redaction function as top-level errors. Renderer QA uses explicitly labelled
synthetic scores and must not be presented as a paid live backtest performance.

### Complete memory reflections

Memory output that ends with finish_reason=length is rejected before the
original settlement writer can resolve an entry. The decision stays pending,
no incomplete lesson is injected into future context, and the attempt still
counts toward the paid budget. This also applies to a legacy partial response
in the exact-request cache; a failed completeness check does not trigger a
paid fallback. Memory's balanced output ceiling is2048 after a real1024-token
reflection exhausted its budget (including reasoning); analysis stays4096.
The request cap and provider allocation remain unchanged.

The Vietnam settlement price adapter returns an Asia/Ho_Chi_Minh date index so
the unchanged upstream evaluator excludes the current Vietnamese session, not
the UTC day. Query end dates include holiday slack and may be in the future;
the snapshot cutoff/cache date is clamped to the current Vietnamese date.
This does not invent future prices or change the original holding-window rule.

### Report publication and paper proposals

After inference finishes, the desktop holds a short SQLite transaction while
writing the report, evidence and Memory artifact. A proposal remains invisible
to other connections until these exports succeed. An export exception rolls
back the proposal, audit and account changes; rejected proposals use a savepoint
so a caught validation error cannot commit intermediate account changes.
No approval or execution method is exposed to the report writer.

A `.publishing` marker keeps interrupted or failed report directories out of
history and prevents opening them as completed reports. Their files remain on
disk for diagnosis. Tests cover a real process exit during this transaction and
an export exception after creating a proposal, then resume the original graph
checkpoint without another model invocation. These are isolated synthetic
fixtures, not market-price tests or executed paper trades.

The filesystem, memory log and graph checkpoint database do not share one
atomic commit. A committed analysis proposal now has a receipt in the same
SQLite transaction: startup/retry verifies the saved artifact hashes, invokes
the original decision recorder without constructing model clients, clears the
original checkpoint and desktop metadata, then finishes the receipt. The
original memory writer remains idempotent for a ticker/date.

Tests cover process exit after commit/before marker removal and exceptions
after recording, checkpoint deletion and metadata deletion. An advisory request
lock prevents recovery of a still-running request across processes. Corrupted
receipts/files remain pending with no paid fallback. Startup reports recovery
failures without resetting the paper account. This does not guarantee recovery
from arbitrary disk loss or a partially written native memory-log append.

A proposal whose receipt is unfinished cannot be previewed for native consent
or approved, even with a valid signature. Finishing recovery changes the live
preview hash, requiring a fresh review. Recovery never approves, rejects or
executes an order. Clearing graph checkpoints does not discard a committed
proposal's receipt.

### Backtest role reports in the library

Each completed grid cell now exports the original full Markdown/HTML report
and individual role files beside its quantitative evidence. These reports use
the existing history/reader interface and are labelled as retrospective,
independent evaluations rather than account P&L. No paper proposal is created.

The cell's Memory report shows only context supplied at analysis time. A legacy
state log that omitted Memory fields gets an explicit unavailable-context note;
its later outcome/reflection is not backfilled into that report. The original
state and completed model receipts remain unchanged when exporting an old run.

The publication marker also covers grid reports. Decision recording and
checkpoint cleanup happen after report/evidence export; an export failure can
resume the completed graph without another model invocation. The standalone
upstream evaluator and its scoring remain unchanged. Committed paper-proposal recovery follows the receipt protocol above.

### Outcome price evidence

Vietnam settlement now freezes the exact completed close series passed into the
original evaluator, source snapshot fingerprints/retrieval receipts, selected
stock/benchmark endpoint dates and closes, and the resulting raw/alpha returns.
Each attempt writes a separate hash-addressed-content receipt under the managed
cache's `settlement-evidence` directory (random unique filenames, content hash
inside); repeat observations do not overwrite older receipts. Direct settlement
results and backtest settlement audit files reference these receipts.

A resolved price receipt means only that the outcome window was priced; it does
not prove a later model reflection succeeded. Pending/failed price windows are
explicit. Provider exception bodies are excluded. If receipt writing fails,
the native settlement caller keeps the decision pending rather than claiming
an audited outcome without its evidence. The original evaluator's formulas,
calendar alignment and Memory scoring are unchanged.

The adapter consumes Vietcap's observed close series. Adjustment factors,
cash-dividend reinvestment and total-return semantics have not been independently
verified. Receipts expose this limitation rather than certifying adjusted total
return or account P&L. A source can revise historical rows; the saved receipt
preserves the actual values used for that observation.

### Live FRED data without an API key

For a Vietnam analysis dated today, the existing macro tool can retrieve the
original US/euro FRED series through FRED's official public CSV and series page
when no `FRED_API_KEY` is configured. It validates series identity, units and
frequency, retains source update/observation times, raw bounded CSV and source
hashes, and caches the snapshot for15 minutes. Returned tables remain bounded
to the original40-row limit. CPI index levels are not labelled inflation rates;
US/euro aliases never become Vietnamese series.

This is latest-revision data observed now, not an ALFRED historical vintage.
Historical requests without a key remain explicitly unavailable without a CSV
request; current data are never backdated. With a key configured, the original
FRED API provider is used unchanged, including its vintage pin. Network/schema
failure remains unavailable, not invented data. This extension applies only to
the VN job transport; the standalone upstream FRED vendor is unchanged.

Primary download guidance: https://fred.stlouisfed.org/help/data/downloading/using-the-download-data-link/
FRED also offers an official MCP connector without an API key, but it requires
its own account and authorization; no account was created or connected by this
change: https://fred.stlouisfed.org/help/data/connecting-fred-to-ai-services/FRED-MCP-Connector

### Reading backtest outcomes after reopening a report

The report reader adds a separate “Đánh giá sau phân tích” section for the
standard grid report layout. It reads only that run's own outcome log, requires
an exact ticker/date and decision-text match, and shows pending/resolved state,
logged raw/alpha return, resolution date, later reflection and the original
sweep scorer summary. Missing logs and mismatched decisions stay explicit.

This is a current reader section, not a rewrite of the saved complete report,
HTML export or the role Memory context. Later lessons are never inserted into
analysis-time context. The original log rounds returns; its alpha and hit rates
must not be presented as higher-precision source quotes or account P&L. The log
does not retain a benchmark-name field, so the reader does not invent one from
today's settings. Existing standalone HTML remains the original analysis report;
select the outcome section inside the app to read later evaluation.

### Selecting ChatGPT Subscription for the whole team

“Sử dụng Subscription” validates sign-in and obtains the plan's model catalogue
before saving. It clears previous per-role model overrides and old API endpoint
overrides so both inherited tiers actually use the selected subscription model.
Unsupported temperature/output-token settings are cleared too. Vietnam data,
paper approval, checkpoint and budget settings are preserved. Selecting it does
not start analysis or inference. Failed sign-in, an empty catalogue or a failed
save leaves the current provider assignment in place. Nothing changes merely
by installing the update; the current balanced 6/6/1 assignment stays active.

### Rounded quarterly labour counts from official releases

VN snapshots v17 add `vn_release_labor_force` and `vn_release_employment` from
the dated NSO quarterly labour release. Counts in million persons are converted
to persons with an explicit rounded/estimated precision label, source excerpt,
content hash, publication and modification dates. The quarter must match the
title; cumulative averages, sector counts, another quarter and conflicting
same-quarter values are not substituted. Future revisions stay withheld by the
existing release filter. Original NSDP series and stale warnings remain visible
separately; this does not claim that the SDMX source was refreshed or that a
historical unrevised vintage was obtained. The v17 cache identity prevents reuse
of an older aggregate snapshot that lacks these fields.

### Technical-indicator contract on Vietnam data

v18 routes all 13 upstream indicator names to stockstats on the same canonical
completed VN candles. This includes the original VWMA formula/default window,
instead of the former close-price 20-session substitution. `look_back_days` is
an inclusive calendar-day range, matching upstream; absent candles are marked
unavailable rather than extending the window to more trading sessions. RSI and
MFI are labelled with the installed stockstats scales: RSI is 0–100, while this
stockstats version returns raw MFI as 0–1 (multiply by 100 before conventional
80/20 comparisons). Other supported indicators use VND/share. Future
dates after the snapshot, unsupported names, empty historical series and invalid
lookback types are rejected. Indicator values can still reflect the provider's
historical price adjustments; formula parity is not predictive validation.

### No-data and stale OHLCV handling

v19 applies the original upstream OHLCV freshness guard to the canonical VN
stock and benchmark series and to dated price/indicator tool requests. The
upstream ten-calendar-day tolerance is reused without inventing a Vietnam
holiday calendar; exceeding it is a no-data signal, not proof of delisting.
An empty requested price range raises the original typed `NoMarketDataError`,
allowing the existing router to return `NO_DATA_AVAILABLE` rather than an empty
CSV. Dates are validated, end dates remain inclusive, and requests beyond the
analysis snapshot are rejected. A stale benchmark is withheld with its warning;
it is never used to compute an apparently current beta or return. v19 snapshots
use a new cache identity, retaining earlier report evidence unchanged.

### Five-year analysis price history

v20 requests up to 1,500 daily stock/index candles and retains the five calendar
years through the analysis date, matching the upstream indicator-history target
instead of the previous 600-row analysis cap. Execution/settlement prices-only
requests keep their compact 600-row request. Listing age, provider omissions and
revisions can still limit coverage; requested start, first/last dates and actual
completed-row counts are included in the shared evidence capsule. Long OHLCV
tables are returned only when the original price tool requests them, rather
than being repeated automatically in every model prompt. This does not obtain
an unrevised historical data vintage or change the original graph/tool loop.

### Full available statement history through the original tools

v21 removes the VN statement tool's silent four-period slice. Income, balance
sheet and cash-flow tools expose every provider record retained in the dated
snapshot, using one period/publication record per CSV row, as upstream does for
long SEC histories. All labelled fields having any non-null value are retained;
zero stays zero, missing cells stay empty and unrelated all-null fields are
omitted. Annual/quarterly and publication cutoffs still apply. Duplicate-period
records remain visible with a warning against summing them. This is available
provider history, not an exhaustive or unrevised filing archive. Tables enter
the model context only when its original tool is called; they are not copied
automatically into every role's evidence capsule. Longer requests can consume
more model tokens, and existing request/output caps remain in force.

### Vietcombank reference-feed recovery

The official XML feed retries a timeout or connection failure once (two attempts
maximum). HTTP errors, including 403/429, invalid XML/quotes and TLS errors are
not retried. Attempt receipts accompany the result; failed refreshes retain the
original quote/retrieval timestamps and remain marked `cached_source_error`.
This is retail reference FX, not an SBV central rate or an executable price.

### Keyless FRED historical replay

A historical VN macro request can replay an exact-lookback public FRED snapshot
that this app observed on that analysis date. Identity, checksum, CSV source
hash/values, date bounds and source update time are checked before use. The
original observation timestamp remains visible. This is a local observed
snapshot, not an ALFRED revision archive or evidence of earlier intraday
availability. Missing, modified, mismatched, symlinked or later-observed snapshots
are withheld without downloading today's revisions. An API key still selects
the original keyed FRED implementation.

### Codex CLI quota preflight

Before the first uncached model request in a desktop analysis, backtest cell or
reflection runtime, the app reads Codex CLI quota if that provider is selected.
It sends only `initialize`, `initialized` and `account/rateLimits/read`. It does
not start an inference turn, sign in/out, buy credits or redeem a reset. Parallel
roles share one probe; exact request-cache hits do not trigger a new probe.

An explicit exhausted state stops before a new model call and before reserving
a DeepSeek request. A missing/failed/unsupported response remains unknown with
a warning, rather than being displayed as zero usage. Quota windows are a fresh
account snapshot, not a capacity reservation or confirmation of model access.
No role assignment, model or paid fallback changes automatically. A sanitized
receipt is stored with the report, without raw account/authentication data.
