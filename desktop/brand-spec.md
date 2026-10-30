# Trading Agents desktop

Mode: Redesign · Overhaul. Replace the project-manager information architecture
with a single TradingAgents research workspace. Preserve the original Python
agents, graph, vendors, portfolio, memory/reflection, checkpoints, backtest and
report writer; expose their controls directly. No project chooser, folder list,
computer browser, generic assistant, community, unrelated terminals or services.

Design read: desktop financial research workstation for a personal Vietnamese
investor. Variance 3/10, motion 2/10, density 7/10, asset dependence 3/10,
brand fidelity 9/10. Laptop viewing distance; quiet, precise, data-first.
The central story is the research workflow and its evidence, with controls on
one side and run/report content in the main area. Secondary functions have
dedicated pages, not a growing collection of project panels.

Logo: assets/logo.png, the locally created TA candle-chart icon used by the
installed application. Existing UI reference: outputs/Trading-Agents-role-report-preview.png
in the enclosing workspace; retain the useful dark working environment, replace
its unrelated project-management surfaces.

Tokens: navy canvas #09121e; panel #101d2b; raised #162638; line #24394a;
primary text #e7f0f5; muted #91a6b8; mint #13d5b4; warning #e4ba69;
error #ed8d83. Fonts: Avenir Next for interface, SF Mono/Menlo for numbers.
Spacing 4/8/12/16/24/32px. Radius 8px controls, 12px panels; no decorative
shadows. Focus ring mint. 140ms opacity/color transitions, disabled when reduced
motion is requested. Local assets only; no CDN or external telemetry.

Capacity: 216px navigation, flexible work area, 340px settings column on wide
screens; stack controls below 1100px. Long research text scrolls in the report
viewer. No fabricated charts or invented prices. Empty/error/running/completed
states come from the actual Python process and saved report tree.

## Provider and team configuration refinement

The Settings page uses three provider cards for Codex CLI, Antigravity/agy and
DeepSeek API, followed by a role assignment table in the original team order.
Primary controls have Vietnamese labels; provider/model IDs remain editable in
advanced fields. Secrets are masked, saved locally and never repopulated.
Presets support Codex+DeepSeek and Codex+agy+DeepSeek; inheritance retains the
original quick/deep configuration. Navigation anchors reduce settings hunting.
Each routed role displays its provider in the workflow. Readable report
typography and tables remain the primary result surface.

## Evidence and virtual execution extension

Mode: Extension. Retain navigation, existing logo, navy/mint tokens, Avenir Next,
role selection and report reader. Add two trading-only pages: Dữ liệu & quyết định
and Danh mục tiền ảo. Use source-backed numeric tables, no decorative charts.
Show empty/missing/blocked/proposed/approved/filled states. The primary order
button opens a native consent dialog; cancellation is the default. No final
council group. Retain all original graph roles and advanced configuration.
