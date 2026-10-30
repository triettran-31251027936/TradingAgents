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

The latest installed profile prioritizes Codex to protect the user's small DeepSeek API balance. Requested IDs:

| Roles | Backend | Requested model |
|---|---|---|
| Market, News, Fundamentals, Memory reflection | Codex CLI | gpt-6-luna, medium effort |
| Bull, Trader, three Risk roles | Codex CLI | gpt-6.1-sol, low/medium effort |
| Research Manager | Codex CLI | gpt-6.1-sol, medium effort |
| Portfolio Manager | Codex CLI | gpt-6.1-sol, high effort |
| Sentiment with context readback | agy CLI | gemini-3.8-flash-high |
| Bear only | DeepSeek API | deepseek-flash, max output 1024 tokens, SDK retries disabled |


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

Coverage limits are visible: no dedicated domestic insider or community/social
feed, no domestic macro series provider, no guaranteed full news articles, and
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

The Backtrader/BackBroker account starts with VND 100,000,000 in virtual cash.
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

## Small paid-API budget (latest user preference)

The active plan is 11 Codex roles, 1 Antigravity role and 1 DeepSeek role.
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
