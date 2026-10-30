# Trading Agents desktop integration

The dedicated macOS app retains upstream v0.7.1's LangGraph, analyst tools,
research/risk debates, portfolio-aware prompts, memory/reflection,
checkpoint lifecycle, backtest scoring and report writer. It adds a desktop
composition layer; it does not replace the graph with terminal agents.

## Local setup

Use Python 3.11+ in the repository virtual environment:

```sh
pip install -e '.[desktop]'
```

The `desktop` extra installs the Backtrader engine used for a local virtual
account. The desktop bridge runs from this checkout; the app manifest stores
its Python and repository paths. `desktop/build_macos.py` builds the shell.
All private state stays in ignored `config/desktop.json`, `.env`, `work/`
and `outputs/`. Never commit credentials or personal reports.

## Models

The latest user-selected profile balances the two primary providers:
**6 Codex roles, 6 DeepSeek roles, 1 Antigravity role**.

| Roles | Backend | Requested model |
|---|---|---|
| Research Manager, Trader, three Risk roles, Portfolio Manager | Codex CLI | gpt-6.1-sol; high effort for PM, medium/low for other roles |
| Market, News, Fundamentals, Bull, Bear, Memory reflection | DeepSeek API | deepseek-flash (V4.1 Flash alias), max output2048, no SDK retry |
| Sentiment with context readback | agy CLI | gemini-3.8-flash-high |


CLI processes use their own existing sign-ins. Financial tools are requested
through structured messages and executed by Python, with results fed back into
the original analyst subgraphs. Native CLI shell/web tool use is rejected.
The original `chatgpt` subscription provider, login and logout remain available
independently. It is not the same transport as Codex CLI.

All original configuration remains accessible through Advanced JSON. The UI
also exposes role routing and native Codex reasoning effort. Saving a preset
is explicit; providers are not silently swapped on a failure.

## Vietnamese data

When `desktop_vietnam.enabled` is true, `.VN` tickers use an independent,
cached adapter to public Vietcap OHLCV, VNINDEX, published financial statements
and company-news headlines. The source's statement metadata supplies field
names; no vnstock SDK code or mapping is bundled. Standard original tools
remain callable, with per-job transport overrides. Identity and the verified
market snapshot use the same canonical dataset. Outcome scoring routes VN
closes separately and configures `.VN` against `VNINDEX.VN`.

Every role receives the same deterministic quantitative evidence. The saved
`quantitative.json` records formulas, values, units, dates, source URLs, hashes
and missing values. Memory reflections on other tickers do not receive the
current ticker's quantitative capsule. Backtest uses a fresh dated scope per
cell while retaining upstream's grid, resume and scoring behavior.

Coverage limits are visible: bounded domestic community RSS sample, no representative social
feed, incomplete domestic rates/credit/FX coverage, no guaranteed full news articles, and
no historical vintage of revised financial statements. Yahoo, Reddit,
StockTwits, FRED and Polymarket tools are preserved; unavailable sources are
reported, not replaced with invented values. SEC coverage does not turn into
Vietnamese issuer coverage. These limits prevent a claim of complete data
coverage or verified investment accuracy.

## Decision and virtual orders

Portfolio Manager retains upstream's five-tier rating and thesis, and also
returns a typed virtual action with quantity, VND limit price, rationale and
material gaps. Invalid structured output never becomes an order extracted from
prose. `Hold` creates no order. An explicit manual portfolio is not treated as
the virtual account.

The legacy Backtrader/BackBroker daily-replay account started with VND 100,000,000 in virtual cash; it is preserved separately and is no longer the active portfolio account (see the 3M practice account below).
The local simulation policy is board lots of 100, a 20% per-position cap,
0.15% commission, 0.1% sell tax and a conservative two-session settlement lock.
These are disclosed simulation assumptions, not full exchange-rule coverage.
Limit orders match from later real completed daily bars, subject to a 1% volume
cap, and expire after the first eligible session. No fills are invented on the
analysis candle. Changed historical bars block replay instead of rewriting
past fills.

A PM result can only create `PROPOSED`. The renderer cannot call approval via
the general action allowlist. A native dialog displays side, symbol, quantity,
price, fees, available cash and simulation date. Cancel is the default.
Only human confirmation lets Electron main sign the exact preview with a
short-lived, process-local key. Python verifies the signature, account version,
preview hash and constraints again before marking `APPROVED`. Submission is
still not a fill. No real broker endpoint exists in this integration.

## Reports and usage

All original per-role reports and consolidated HTML remain. The desktop adds
an evidence/decision page and a virtual-account page. New saved exports include
a deterministic evidence appendix and sidecar `decision.json`, `usage.json`
and `integration.json` files. The retired final council is removed; existing
council output was backed up locally before restoring its original report.

Exact-request caching includes model, endpoint, schema, settings, complete
model-visible messages and data/account fingerprints. Ephemeral graph IDs are
excluded; evidence and tool-call references remain. Failures are not cached.
Financial statement tools retain every labelled non-null value, including zero,
in a compact CSV representation rather than repeating provider keys/null
fields. In the FPT sample this reduced statement payload characters by 65–81%;
this is not a measurement of total token or monetary savings.

Usage counters record provider-reported tokens and missing counters separately.
Native CLI thinking may consume substantial tokens even with concise reports.
No cost or subscription-limit percentage is inferred from token counts.

## Verification on 2026-10-30

- Full real FPT.VN graph completed all 13 roles on all three backends in 406.4s.
- PM returned Underweight + execution Hold / quantity 0; no user order approved.
- A separate real original News Analyst run verified 4 dated Vietnamese headlines
  after the company-news adapter was added. The complete graph run preceded that
  addition; it did not have these headlines and is not relabelled otherwise.
- Unit checks cover next-bar matching, unfilled/partial expiration, zero-volume
  bars, settlement locks, forged/stale/repeated approvals, reserved exposure,
  revised data, dated backtest transport, cache identity and cross-ticker memory.
- Full suite and lint logs are retained locally under `work/`; the user-facing
  verification report records the final counts and installed bundle integrity.
- Direct macOS UI checks verified evidence, decision and virtual-account pages.
  A real order-approval dialog was not submitted during verification.

The upstream core's preservation and passing tests are evidence of workflow
compatibility, not proof of predictive accuracy or complete Vietnamese data.

## Antigravity context and startup hardening

The default assigns only Sentiment to Antigravity. Bull and Aggressive Risk
now use Codex under the low-API profile, because completing a CLI call does not
demonstrate evidence synthesis quality. Antigravity receives readable role-separated messages and a
source ledger. A grounded Sentiment schema must read back the exact ticker,
date, snapshot, news IDs and availability of social feeds. Claims need valid
source references; sparse inputs require low confidence. Selected unsupported
valuation figures are rejected. The original free-text fallback cannot bypass
this check. Receipts are saved in grounding.json and displayed in the report.
These are bounded grounding checks, not a claim of complete semantic validation.

A second full real graph run completed all 13 roles in 222.9 seconds with the
new assignment, four dated news items and a validated Antigravity receipt.
PM again returned Underweight + virtual Hold. No user order was approved.

Chromium session caches are isolated by build, GPU acceleration is disabled
for this document interface, and inherited Python launcher/path variables are
removed from bridge children. Lightweight metadata no longer imports the data
engine. A private last-successful metadata snapshot is for startup display only;
mutating actions require fresh engine metadata. Optional subscription status
is loaded when Settings is opened, rather than blocking startup. Idle bridge
children are cancelled on quit; quick requests have a bounded timeout.

## Previous small paid-API preference (historical)

The previous plan was 11 Codex roles, 1 Antigravity role and 1 DeepSeek role. It has been superseded by the balanced profile below.
DeepSeek is limited to one Bear call per analysis, with 1024 output tokens and
SDK retries disabled. Exact cache hits do not consume the request limit; failed
attempts do. The runtime rejects further paid attempts before invoking the
backend. Presets and saved configuration are checked against the cap, including
the minimum calls implied by extra debate rounds.

Backtests substitute Codex for DeepSeek by default. Enabling DeepSeek for a
backtest is explicit; its request cap is then per cell, so a large grid can
spend more. Settings expose the request/output limits and this opt-in. These
limits are not a verified USD balance or dollar-cost guarantee. The native
provider-connection test is explicit and makes a paid API call if clicked; it
was not run after the user disclosed the 2 USD budget. No further live DeepSeek
validation was performed after that disclosure.

Earlier real-run measurements in this document used the prior model allocation.
They validate the integration, not the new 11-role Codex allocation. The latest
budget policy is checked with isolated backend doubles, without spending API
credit. config/desktop.example.json contains a sanitized reproducible profile.

## Reconciled financial and account metrics

The canonical adapter version3 adds flow reconciliation before TTM arithmetic.
Complete four-quarter totals are checked against up to two recent published FY
statements with a 0.5% tolerance. Discrete flows are summed; YTD flows are first
differenced. Ambiguous, inconsistent, stale or incomplete evidence yields
missing TTM, not a guessed number. This is an empirical representation check,
not an audit of statements or protection against all accounting restatements.
FCF requires aligned periods and signed non-positive asset-purchase cashflow;
the provider definition can include other long-term assets. Quarterly EPS is
labelled as a reported-period value and is never summed to invent EPS TTM.
Current PE/PB and parent-scoped ROE are now derived when their inputs pass the valuation checks below; absent or inconsistent inputs remain explicitly missing.

A fresh public FPT dataset validated 50 available metrics of 53 defined, with
PE/PB/ROE withheld. No LLM or paid API call was needed for that source test.
The original 39-metric reports remain historical records and are not rewritten
as if they had these new inputs.

Virtual-account evidence includes current exposure/weight, a single-stock
historical VaR/CVaR amount and whole-lot capacity at the reference close after
fees and reserved exposure. These are deterministic reference calculations,
not a trade recommendation, portfolio VaR or a forecast. Runtime sidecars
record each role's supplied snapshot, metric coverage and input hash. A role
with a missing canonical capsule cannot be marked complete. The receipt proves
input delivery, not the correctness of every output assertion.

## macOS app runtime location

The app now stages its code, venv dependencies and mutable runtime under
`~/Library/Application Support/Trading Agents/engine`. The checkout remains
the development/source copy. Initial installation copies settings, local .env,
reports, memory and virtual-account data intact; it does not delete the originals.
Later app deployments refresh code only and preserve runtime user settings,
keys, reports and account state. The manifest records both sourceRepo and repo.

The actual Python executable and site-packages path are resolved at build time.
The bridge starts from application Resources with `-S`, then explicitly loads
the staged environment. It does not discover the Documents-resident venv or
use Documents as cwd. This addresses observed macOS stalls in pyvenv.cfg
opening, getcwd and directory scans. The shell still waits for fresh engine
metadata before enabling mutating operations.

The original source .env and config remain private migration backups; changing
keys/settings in the installed app now updates the Application Support runtime.
Source validation scripts run against their own checkout and do not overwrite
this runtime state. Report paths displayed in the installed app refer to the
staged runtime.

## Current balanced profile

The user requested equal Codex/DeepSeek allocation after the earlier 11/1 split.
The installed runtime, example profile and UI presets now use 6 Codex /6 DeepSeek
/1 Antigravity. A lightweight shared preset in desktop/profiles.py is exposed
through bridge metadata, preventing the UI from restoring the retired one-Bear
allocation. Saving role assignments also saves the visible request/token limits.

The paid request cap is24 per analysis, max output2048 tokens/request, with SDK
retries disabled. This accommodates three analysts' tool loops, two research
turns and a reflection at the configured six-tool-round limit. Actual usage
can differ: Memory only calls a model when due, tool rounds vary and multiple
reflections may hit the cap. Equal role counts do not imply equal token counts,
latency or financial cost. No dollar balance is inferred.

The same model distribution is enabled for backtest; the request cap is per
cell, so a grid can spend more in total. Users can uncheck paid backtesting or
choose the Codex+Antigravity preset. This update changes configuration only; no
paid inference validation was run to apply the new split. Prior real-run reports
retain their original requested-model metadata and are not relabelled.

### Live-market paper practice, 3,000,000 VND

The current portfolio uses a **new, separate** `practice.sqlite` account with exactly
3M VND starting cash; the legacy `backtrader.sqlite` daily replay is retained.
The trading page retrieves actual Vietcap minute/day OHLCV and recent matched trades.
Lightweight Charts is vendored, checksum-verified, with LICENSE, NOTICE and attribution.
Polling is approximately 30 seconds while the portfolio page is visible. It is REST
market data and can be delayed; source and retrieval timestamps are both displayed.
Matching advances on portfolio refresh; keep this page open after approving a proposal.

PM execution proposals use the same 3M account snapshot. Historical analysis cannot
create a current-market order. The renderer cannot call `paper_approve` directly;
Electron's native, cancel-by-default dialog signs a short-lived confirmation.
Approval creates a pending order only. Backtrader BackBroker matches from the next
completed real minute after actual approval, with 100-share lots, a 1% minute-volume
cap, continuous trading only, limit prices, fees, expiry and conservative T+2 EOD.
There are no deposits, margin, short sales, fractional shares or fabricated odd-lot fills.
Practice concentration can reach 100% of this small virtual account; this is not a
recommendation to allocate all real capital. Source revisions and material missing
post-approval history block matching, preserving the account for reconciliation.

This is an open-source local paper account, **not** Paave/SSI exchange UAT integration.
Paave offers Vietnamese virtual trading but its documented MCP is read-only:
https://www.paave.ai/mcp . SSI's developer FAQ does not establish an available UAT
order endpoint: https://developers.ssi.com.vn/docs/faq/service-overview . Backtrader's
broker simulation is the selected open-source alternative:
https://github.com/mementum/backtrader . The simulator does not reproduce exchange
queue priority, auctions, corporate actions or a full authoritative holiday calendar.

### Validated VN valuation inputs (schema v4)

The adapter now calls Vietcap `statistics-financial` in addition to statement
metadata, income, balance and cash-flow sources. Provider comparison ratios are
separate metrics. They never silently replace Python calculations. Current PE is
observed market capitalization divided by positive reconciled parent profit TTM;
PB uses parent-attributable equity, subtracting explicit non-controlling interests.
ROE uses the same parent profit scope and the arithmetic average of equity at
TTM end and exactly one year earlier. This is a two-point average, not a daily
weighted figure. Both balance sheets must satisfy assets - liabilities = equity.

Market-cap facts are accepted only for the retrieval's VN calendar date, from
current company/details (price and issued-share checks) or a statistics period
matching the published statement window. Cap/shares must match the canonical
completed close within 0.001%; shares must be positive integers. PE/PB additionally
require explicitly zero preferred equity capital. The observed cap/share facts
remain available even when preferred-capital scope blocks PE/PB. Preferred shares in debt
and in paid-in capital are distinguished through provider metadata hierarchy.
Historical/current data must not be mixed: historical PE/PB remain withheld if a
same-date observation was not supplied. Statement ROE remains usable with dated
inputs. No weighted-average EPS is inferred from current outstanding shares.

The snapshot retains metadata, raw source hashes, valuation_reconciliation and
separate provider PE/PB/ROE observations. All metric values, formulas and gaps flow
through the existing canonical capsule to the original graph roles and reports.
A live FPT test on 2026-10-30 returned 60/60 metrics, including computed PE
9.659960943, PB 2.317926882 and ROE 0.261556570. The provider's PE 9.618355198 and
ROE 0.260182172 differ; both are retained as comparison observations, not forced
to agree. This is one issuer/source-time verification, not universal coverage or
an independently audited statement validation.

Current issuer profiles are obtained from company/details, matched to the ticker,
converted to plain text and delivered through the canonical capsule and exported
evidence report. Broker ratings, target prices and projections are not copied into
issuer facts. Current profile content is withheld in historical analysis.

A three-issuer check found: FPT computed PE/PB/ROE available; HPG computed PE/PB/ROE
available after using current company-details cap rather than stale Q2 statistics;
VCB parent-scoped ROE and current cap/shares available, while PE/PB remain withheld
because its statement metadata does not establish an explicit preferred-capital
split. Bank-specific aliases distinguish TOTAL LIABILITIES and Minority interest.
Industrial revenue/current-assets/CFO definitions do not automatically describe a
bank; the remaining VCB gaps are recorded, not filled with industrial substitutes.

### Official domestic macro transport (schema v5)

The original get_macro_indicators tool now supports a Vietnam transport during
VN jobs. Explicit vn_cpi/vn_cpi_yoy/vn_cpi_mom, vn_labor_force, vn_employment,
vn_unemployment, vn_youth_unemployment and vn_underemployment aliases use NSDP
SDMX files published by NSO. vn_nsdp:<INDICATOR> exposes every retrieved SDMX
series with raw dimensions; the live sample contains 48 series. Existing cpi,
unemployment, fed_funds_rate and raw FRED aliases keep their original semantics
and still use the original FRED implementation. They are not renamed VN data.

Raw file versions are SHA-256 checked, cached for six hours and archived by
content hash with first-observed timestamps. A historical query can only reuse
a local vintage first observed by its analysis date; otherwise it is withheld.
No quarterly interpolation into monthly rows or forward filling is performed.
CPI changes use same-base observations at exactly 1/12-month offsets. NSDP count
scales are applied explicitly. NSDP's percent-rate UNIT_MULT=3 is inconsistent
with the official release's percentage definition: rates retain raw percentages
without a 1000x multiplier, with an explicit warning and official cross-check
link. Canonical rate metrics use fractions; tool tables use declared percentages.

The currently retrieved CPI file ends at 2025-10 and labour at 2026-Q2. Both are
marked stale under the disclosed 62-day monthly / 110-day quarterly heuristic;
this is not an authoritative publication calendar. A successful download is not
evidence of current-period coverage. A cached network failure keeps the original
retrieval time and source error status. Missing sources do not become zero data.

Newer official releases come from NSO's public WordPress REST API. Their published
and modified GMT timestamps are checked against the analysis cutoff. Only exact,
unambiguous period-specific statements produce vn_release_* numeric facts.
The live sample extracts September 2026 CPI MoM 0.62% and YoY 5.08%; Q3/2026
working-age unemployment 2.22%, underemployment 1.31%, youth unemployment 9.07%.
The nine-month 1.53% underemployment and 8.87% youth figures in the same release
are not substituted for quarterly rates. Conflicting statements or changed prose
produce missing values. Provider revisions can be unmarked; this is not a full
historical vintage archive. SDMX and release observations retain separate names,
periods, source URLs/hashes and status rather than overwriting one another.

The canonical capsule delivers compact macro summaries to all decision roles;
full source observations remain in quantitative.json and available through the
original macro tool. Both UI and exported evidence show observation/publication
dates; stale figures have a visible warning. Sources are cached once across stock
jobs. No paid inference or personal credentials are needed for these public data.
Domestic policy rates, credit and FX still require further integration/validation.

### GDP, industrial production and reference FX (schema v6)

GDPVNM and IIPVNM add 221 and 5 source series respectively to the earlier 48.
The parser retains distinct frequency/base dimensions instead of rejecting all
GDP data or taking the first/last series. An unqualified ambiguous code returns
explicit selector choices; `vn_nsdp:<qualified-selector>` reads the chosen raw
series. Canonical quarterly GDP and monthly IIP use explicit dimensions.

The live GDP source contains 28 series with materially conflicting values under
the same observation period and base label. Those conflicts are retained in the
raw evidence and the affected canonical series are withheld. Conflicting periods
are excluded from the normal observation list. Tiny duplicate numeric differences
within relative 1e-12 are recorded and deduplicated; all originals remain in the
raw XML. This is a declared serialization tolerance, not an audit/revision policy.
Do not select the last row, silently relabel a price base or reconstruct real GDP
growth from this ambiguous source.

Quarterly nominal GDP is available after unit conversion from billions to VND.
The real GDP level and derived growth remain missing while this conflict persists.
A separate exact period-specific NSO release fact provides `vn_release_gdp_yoy`:
Q3/2026 growth 9.95%, with publication/modification timestamps. The matching numerical
result from selecting one duplicate is not treated as validation of that selection.
IIP uses the 2015-base monthly index, exact 12-month/1-month comparisons and no
seasonal adjustment. The live sample has September2026 index 223.99; derived YoY
18.922219% and MoM 3.259266%. Observation periods and formulas are explicit.

The official XML linked from Vietcombank's exchange-rate page supplies the three
distinct USD cash-buy, transfer-buy and sell reference quotes and other currencies.
These are not SBV central rates, interbank midpoint prices or executable quotations.
Their source-local clock is interpreted in Asia/Ho_Chi_Minh. Parsing checks the
numeric format, positive bid/ask values, future timestamps and duplicate currencies.
A missing cash bid stays missing. Quotes cache for 30 minutes; raw versions and
first-observed times are preserved for dated replay. An unobserved historical
quote is withheld. Network errors keep the original quote/retrieval times.
Aliases are `vn_fx_usd_cash_buy`, `vn_fx_usd_transfer_buy`, `vn_fx_usd_sell` and
`vn_fx:<CURRENCY>`. Public SBV access was rejected and its older NSDP document links
returned 404; no rate or central FX series is silently invented/substituted.


### Domestic shareholder dealings (v7)
The native get_insider_transactions tool now reads all CafeF shareholder-dealing
pages for the canonical Vietnamese ticker. Quantitative artifacts and the evidence
page/export preserve every retained professional record; shared context contains
summary metrics and five examples, with the original tool available for all rows.
This aggregator feed includes insiders, related parties and other shareholders;
it is not exchange-confirmed completeness. Registrations, dated reported results,
ESOP/rights and unspecified mechanisms are distinct. No execution prices or VND
notionals are invented. Undated results remain visible but outside dated subtotals;
incomplete pagination or missing quantities withhold affected totals. Reversed
registration windows are retained with warnings and excluded from active plans.
The source can update results under an earlier announcement date. Historical
queries therefore require an exact locally observed vintage no later than the
analysis date, rather than treating registration dates as result-publication dates.


### Vietnamese community sample (v8)
F247's public per-ticker tag RSS supplies dated first-post excerpts. The source
contains up to the feed's current items, filtered to the original seven-day window;
it is not thread/reply coverage or a census. All retained excerpt records are saved
and shown in evidence/export. The shared capsule supplies the eight newest sample
records that explicitly mention the ticker in their supplied title/excerpt (100 excerpt words each), with an explicit omitted count. Author/profile,
avatar, contact and tracking fields are not retained. Tags alone do not establish
company relevance; tag-only records stay inspectable but outside model context. No sentiment score, investor count or bullish/bearish fraction
is computed from this source. First-observed snapshot guards prevent current edits
being passed off as historical knowledge. Failed refresh preserves source timestamps.
The original sentiment node, StockTwits and Reddit remain separate. Antigravity
must read back every supplied F247 ID and source status; claims cannot reference
unseen IDs. Sparse/missing data requires low confidence. These checks verify source
identity and transport, not semantic truth or representative investor sentiment.


### Bank-specific arithmetic (v9)
Bank identification requires populated net-interest-income and customer-deposit
statement fields, rather than applying a current sector label to historical accounts.
26 additional banking metrics include gross/net customer loans, signed allowance,
customer deposits, interbank placements, SBV balances, issued papers, reconciled
TTM income/expense/provision flows, accounting loan/deposit, loan-allowance ratio,
TTM CIR and noninterest income. Gross plus non-positive allowance must equal net
loans within max(1 VND, 1e-9 gross). Duplicate flow periods or ambiguous quarter/FY
representation withhold TTM instead of choosing/summing rows. Accounting LDR is
not the SBV regulatory definition. Seven provider ratios (NIM, CASA, NPL and
comparison ratios) remain separately labelled provider_observed, signed and
methodologically unverified. They require a unique current provider row matching
published income/balance periods; current values are withheld for historical dates.
No independently reconstructed NIM, regulatory CAR, CASA breakdown or NPL-group
schedule is claimed without the necessary note denominators and scope.


### Bank presentation and capital-scope corrections (v10)
The CFO caption `Net cash from operating activities` is accepted alongside the
industrial caption, with the same quarter/FY reconciliation. Bank CFO is not used
as industrial FCF/cash conversion. Seventeen industrial presentation definitions
are explicitly `not_applicable` for a bank identified from populated published
statement fields. They remain visible with reasons and null values, outside the
missing-data list/applicable denominator; real missing bank/valuation inputs stay
missing. Preferred-share capital can be found under the banking `Capital` caption
only when metadata places it beneath shareholder equity. Duplicate balance periods
cannot establish the preferred class. Every failed price/capital check is retained;
recognizing zero preferred capital never overrides marketcap/quote disagreement.


### Quote comparison (v11)
Valuation proof, shared context and evidence/export now expose canonical quote
date/close, observed profile price, cap/shares-implied price, observation time and
prior canonical dates matching that price. Provider quote time remains unknown
unless actually supplied; retrieval time does not establish it. A prior-close match
is diagnostic, not proof of lag or corporate adjustment. The valuation alignment
gate stays intact; no silent rebasing unlocks P/E/P/B. Existing saved snapshots
without comparison remain readable. New snapshot version forces fresh assembly.


### Paper-account identity on checkpoint recovery
Desktop preparation adds a run-local `desktop_paper_context_fingerprint` computed
from the complete model-visible practice snapshot, including pending/unapproved
orders and settlement availability. Original checkpoint settings signatures include
this field, so a changed book cannot resume reports from a different account
context even when available cash and total holdings are unchanged. Identical
books still resume. The field is removed before preparing another context; explicit
portfolios/backtests do not inherit an earlier virtual-account identity. This does
not change the approval fingerprint, HMAC, native dialog or execution rules.
Tests exercise actual SQLite crash/recovery with deterministic stand-in nodes;
they verify lifecycle/context isolation, not real LLM reasoning or all graph nodes.
