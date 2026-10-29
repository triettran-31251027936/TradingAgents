# Trading Agents desktop and CLI integration

The dedicated local application is **Trading Agents.app**. Its source is in
[`desktop/`](desktop/README.md). It contains only TradingAgents research,
clickable role reports, backtest, memory/reflection, portfolio context, model and
data settings, checkpoints and authentication. Project/folder/computer browsing,
generic assistants and unrelated AgentsRoom services are not included.

The original `/Applications/AgentsRoom.app` is read-only input to the local
builder as an Electron runtime. The new application has its own main process,
renderer, preload, executable/helper names, bundle ID, icon and native menu.
It is a dedicated desktop implementation in this TradingAgents fork, not a
claim that proprietary AgentsRoom TypeScript sources were published.

## Models and accounts

- Codex CLI (`codex_cli`) uses its own existing sign-in.
- Antigravity (`agy_cli`) uses the `agy` terminal command and its own sign-in.
  It remains experimental on the inspected local version.
- DeepSeek uses the original `deepseek` provider. Enter the key in Settings;
  it is stored only in the private `.env` file with mode 0600, never returned
  to the renderer or included in Git, reports or model-routing configuration.
- Subscription (`chatgpt`) uses the original TradingAgents Plus/Pro OAuth flow,
  including loopback, pasted callback, available models and revocation. It is
  a separate option from the two CLI adapters and uses the plan's limits.

Keep the original quick/deep configuration, or assign provider/model per role.
The role router selects from actual LangGraph metadata rather than guessing
from prompts. It rebuilds the same original GraphSetup with different LLM
transports; analyst/tool graphs, debates, memory and managers remain original.
Backtest uses the same routing through a scoped graph factory in its dedicated
job process. Changing model assignments changes the checkpoint signature;
empty assignments preserve the original signature.

Settings offers Codex+Antigravity, Codex+DeepSeek and three-provider presets.
A preset is applied only after saving the assignment. DeepSeek needs the user's
key; CLI providers need their own valid sign-in and available usage quota.
All upstream config keys remain available in advanced JSON. Storage paths are
managed by the desktop. Optional save, HTML and show flags match the CLI.

## Running the original CLI inside AgentsRoom

The earlier terminal integration remains available:

```sh
./scripts/agentsroom.command --doctor
./scripts/agentsroom.command --help
```

The wrapper loads `config/agentsroom.env`, `.env`, then the example profile;
explicit environment values take precedence. The original CLI's analysis,
backtest and subscription login/logout commands are preserved. No API keys
are required for the CLI LLM adapters, but data vendors may have their own keys.

## Verification

```sh
.venv/bin/python scripts/audit_upstream_core.py
.venv/bin/python -m pytest -q
.venv/bin/ruff check desktop tests/test_desktop*
```

The source audit checks 72 original workflow, data, memory, backtest, portfolio,
report, config and CLI files against pinned upstream commit
`c4544913a14ed3df5f1ed7b9e8483270ce20682a`; they are unchanged.
Tests cover a full original graph with every role assigned a distinct model
across three providers, tools/structured output, graph shape, persistence,
resume, OAuth, private keys and error handling using controlled model/data IO.
Those tests do not represent live DeepSeek or agy validation.

A full real-Codex, real-vendor FPT.VN run completed on 2026-10-29 in 610.5s.
A subsequent run started from the dedicated UI completed its four analysts,
Memory and Bull Researcher before the CLI exited with code 1; the checkpoint
was retained. Browser OAuth creation/cancel was checked, but no account sign-in
was completed by the agent. No live DeepSeek call is claimed without a key.
Default data coverage for Vietnam remains incomplete (ticker news, StockTwits,
FRED key and Polymarket connectivity). Source completeness does not establish
investment accuracy or complete Vietnam data coverage.

The subsequent original-mode UI resume completed all13 result sections and wrote
a separate timestamped report with transport provenance. Codex concurrency was
verified with four real simultaneous CLI processes. The default remains the
original two-tier allocation; per-role assignments are optional extensions.
See the original setup/conditional-logic modules for the authoritative role map.
The app now also mirrors clear-checkpoints and show-without-save behavior.

The user separately requested the original AgentsRoom startup-cache patch and
removal of duplicate app bundles. AgentsRoom 1.229.0 now has bounded mail warmup
and asynchronous cold-cache reads, with a compressed pre-patch recovery copy.
Only /Applications/AgentsRoom.app and /Applications/Trading Agents.app remain
registered/indexed from this app family; prototypes were unregistered and removed.
