# TradingAgents in AgentsRoom — Codex and agy CLI backends

This fork keeps the upstream LangGraph workflow. `codex_cli` and `agy_cli`
replace only the LLM transport. Four analysts, bull/bear research, the research
manager, trader, three risk perspectives, portfolio manager, reports, memory,
reflection, checkpoints and decision backtests remain upstream components.

Verification on 2026-10-28: **Codex CLI 0.164.0 passed a real tool-request →
tool-result → structured-output smoke test. agy CLI 1.4.7 is experimental:**
a direct minimal schema prompt succeeded, but the adapter's tool request timed
out at 60 seconds. Use `codex_cli` as the verified starting backend. A higher
timeout is configurable but has not been established as the fix for agy.

AgentsRoom is the outer terminal/agent workspace. Adding this project does not
turn each graph node into a separate native AgentsRoom agent tile. That would
require a different UI integration, not just an LLM provider adapter.

## Setup

Requires Python 3.11+, and at least one signed-in CLI (`codex` or `agy`).

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp config/agentsroom.env.example config/agentsroom.env
```

Sign in using the CLI's own terminal interface. The adapter never reads or
copies its token files. Existing subscription limits still apply. Set the
provider to `codex_cli` or `agy_cli` in `config/agentsroom.env`. Model `default`
omits `--model`; an explicit model ID is forwarded unchanged. Codex starts
with `--ignore-user-config`, so `default` means the CLI built-in default, not a
custom profile from your config file. Set both model IDs if you need pinning.

## Open in AgentsRoom

1. Add this repository folder as a project.
2. Add a Codex or Antigravity agent for operating the project, or use its terminal.
3. Run `./scripts/agentsroom.command --help` to verify the launcher.
4. Start the upstream analysis interactively with `./scripts/agentsroom.command`.

Example for a **US connectivity check**, not a Vietnam stock mapping:

```sh
./scripts/agentsroom.command --ticker AAPL --date 2026-10-27 \
  --analysts market,sentiment,news,fundamentals --save --no-show --html
```

Pass the analysis date you actually want; this example is deliberately fixed.
To include your holdings, add `--portfolio path/to/portfolio.json` using the
upstream cash/currency/positions JSON schema. Do not put account credentials in
that file. Backtest uses the same native subcommand and backend settings:

```sh
./scripts/agentsroom.command backtest AAPL --start 2026-09-01 --end 2026-09-15 \
  --every 7 --analysts market,sentiment,news,fundamentals
```

Reports and state logs are under `outputs/`; private runtime memory and caches
under `work/runtime/`. Each model call logs its start and completion to the
terminal. The upstream CLI also shows graph progress. Backtest evaluates
independent decisions; it does not simulate a changing cash/position ledger.

Suggested initial prompt for an AgentsRoom operator:

> Use scripts/agentsroom.command to operate the original TradingAgents graph.
> Keep all four stock analysts and both debates enabled. Report missing data
> and errors. Do not substitute your own one-shot analysis for a failed graph
> run. Read AGENTSROOM.md before running. Do not place brokerage orders.

## Transport contract

- Every turn includes the complete role-labelled history; sessions are not
  shared across analysts or runs.
- Tool schemas are supplied to the CLI as data. The answer is a JSON envelope
  with `content` and requested `tool_calls`. LangGraph's original ToolNode
  executes the tools and feeds the real results into the next turn.
- Structured decision agents use their original Pydantic schemas.
- One call per CLI provider runs at a time, including across quick/deep tiers
  in the same Python process. Separate OS processes do not share this lock.
- Fresh request directories, no shell interpolation, no
  automatic permission bypass, timeouts terminate the process group on POSIX.
- Codex receives prompts through stdin. The verified agy 1.4.7 stdin streaming
  path hangs locally, so agy receives `--print=<prompt>` as a subprocess argument.
  That argument can appear in local process listings. Requests above 120 KB
  are refused without truncation; use Codex for longer histories. Do not use
  the agy backend for sensitive portfolio data on a shared machine.
- Codex uses a read-only sandbox without the user's MCP/provider config. agy
  uses its sandbox and plan mode. Both are instructed not to use native tools.
  The schema-completion `finish` event in agy is accepted. The response is
  rejected if event logs show other native tool usage; this check is
  detection after the event, not a guarantee of tool unavailability in agy.
- CLI failures, missing completion and malformed tool output raise errors.
  Upstream structured-output fallback may retry as free text and logs that fact.
- No streaming token adapter, automatic model switching, or quota retry is
  implemented. CLI `--json-schema` capabilities/version must match the local
  installations; CLI upgrades may need transport changes.

## Vietnam stocks: separate data work still required

This fork does not add a Vietnam market data vendor. Before using FPT, VCB,
HPG or other local tickers, integrate and validate price/volume, financials,
news, sentiment and a suitable benchmark. Do not map a Vietnam ticker to an
unrelated US listing to make a run succeed. Historical fundamentals must use
publication dates. Missing or unavailable data must remain explicit.

## Verification

```sh
.venv/bin/python -m pytest tests/test_cli_backend.py tests/test_graph_end_to_end.py -q
```

Adapter transport tests use scripted subprocesses. The full-graph adapter test
uses scripted model/vendor responses and checks every original role, tools,
memory and HTML output. These checks prove wiring, not financial performance.
Real CLI smoke results and remaining limitations are recorded separately in
the delivery report.

## Sources

- https://learn.chatgpt.com/docs/non-interactive-mode
- https://antigravity.google/docs/cli/headless/
- https://agentsroom.dev/guides/getting-started
