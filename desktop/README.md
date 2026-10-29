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
