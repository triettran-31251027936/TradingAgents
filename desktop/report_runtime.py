"""Add truthful transport provenance without changing upstream report/agent code."""

import json
import os
import tempfile
from pathlib import Path

from desktop.model_routing import ROLE_NAMES


def execution_details(cfg):
    routes = cfg.get("desktop_role_models", {})
    models = {}
    for role in ROLE_NAMES:
        tier = "deep" if role in ("Research Manager", "Portfolio Manager") else "quick"
        spec = routes.get(role) or {
            "provider": cfg.get(f"{tier}_think_provider") or cfg["llm_provider"],
            "model": cfg[f"{tier}_think_llm"],
        }
        models[role] = {"provider": spec["provider"], "requested_model": spec["model"]}
    cli = {m["provider"] for m in models.values()} & {"codex_cli", "agy_cli"}
    ignored = [
        k
        for k in (
            "temperature",
            "max_tokens",
            "llm_max_retries",
            "openai_reasoning_effort",
            "google_thinking_level",
            "anthropic_effort",
        )
        if cfg.get(k) not in (None, "")
    ]
    return {
        "mode": "per_role_extension" if routes else "original_quick_deep",
        "models": models,
        "memory_reflection_only_when_due": True,
        "actual_cli_model_id_verified": False if cli else None,
        "cli_api_controls_not_forwarded": ignored if cli else [],
        "cli_concurrency_limits": {
            p: int(
                os.environ.get("TRADINGAGENTS_CODEX_MAX_CONCURRENCY", "4")
                if p == "codex_cli"
                else os.environ.get("TRADINGAGENTS_AGY_MAX_CONCURRENCY", "1")
            )
            for p in sorted(cli)
        },
    }


def provenance_markdown(details):
    text = "## Model selection and transport\n\n"
    text += (
        "Mode: per-role extension; the quick/deep labels above are only defaults.\n\n"
        if details["mode"] == "per_role_extension"
        else "Mode: original TradingAgents quick/deep allocation.\n\n"
    )
    text += "| Role | Provider | Requested model |\n| --- | --- | --- |\n"
    for role, model in details["models"].items():
        values = [role, model["provider"], model["requested_model"]]
        text += (
            "| "
            + " | ".join(str(x).replace("|", "\\|").replace("\n", " ") for x in values)
            + " |\n"
        )
    text += "\nMemory uses Quick only when settlement needs a reflection. "
    text += "A CLI model named `default` is chosen by that CLI; Quick and Deep can resolve to the same model.\n"
    if details["cli_api_controls_not_forwarded"]:
        text += (
            "\nAPI controls not forwarded by the CLI adapter: "
            + ", ".join(details["cli_api_controls_not_forwarded"])
            + ".\n"
        )
    if details["cli_concurrency_limits"]:
        text += (
            "\nCLI concurrency limits: "
            + ", ".join(f"{k}={v}" for k, v in details["cli_concurrency_limits"].items())
            + ".\n"
        )
    return text


def enrich_report(path, ticker, state, settings, cfg, html=True):
    from tradingagents.report_html import render_report
    from tradingagents.reporting import _report_parts

    details = execution_details(cfg)
    appendix = provenance_markdown(details)
    (path.parent / "execution.json").write_text(json.dumps(details, indent=2), encoding="utf-8")
    body = path.read_text(encoding="utf-8")
    if details["mode"] == "per_role_extension":
        title, rest = body.split("\n", 1)
        body = (
            title
            + "\n\n> This run uses per-role model assignments. See the model-selection appendix.\n"
            + rest
        )
    path.write_text(body + "\n\n" + appendix, encoding="utf-8")
    if html:
        parts = _report_parts(state) + [
            (
                "VI. Model Selection and Transport",
                "execution",
                [("Execution metadata", "runtime.md", appendix)],
            )
        ]
        path.with_suffix(".html").write_text(
            render_report(ticker, state, settings, parts), encoding="utf-8"
        )
    return details


def transient_report(ticker, state, settings, cfg, render):
    from tradingagents.reporting import write_report_tree

    Path(cfg["data_cache_dir"]).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cfg["data_cache_dir"], prefix="preview-") as folder:
        path = write_report_tree(state, ticker, folder, settings=settings, html=False)
        enrich_report(path, ticker, state, settings, cfg, html=False)
        text = path.read_text(encoding="utf-8")
        sections = [
            {
                "name": p.stem,
                "group": p.parent.name,
                "text": p.read_text(encoding="utf-8"),
                "html": render(p.read_text(encoding="utf-8")),
            }
            for p in sorted(Path(folder).glob("*/*.md"))
        ]
    return {"markdown": text, "rendered": render(text), "sections": sections, "html": ""}
