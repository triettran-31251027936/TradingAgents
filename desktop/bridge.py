"""JSON process bridge for the dedicated desktop; uses the original framework."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import sys
import threading
from dataclasses import asdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

from tradingagents.default_config import DEFAULT_CONFIG, build_default_config  # noqa: E402

ROLES = [
    "Market Analyst",
    "Sentiment Analyst",
    "News Analyst",
    "Fundamentals Analyst",
    "Memory Log",
    "Bull Researcher",
    "Bear Researcher",
    "Research Manager",
    "Trader",
    "Aggressive Analyst",
    "Conservative Analyst",
    "Neutral Analyst",
    "Portfolio Manager",
]
MANAGED = {
    "results_dir": str(ROOT / "outputs"),
    "data_cache_dir": str(ROOT / "work/runtime/cache"),
    "memory_log_path": str(ROOT / "work/runtime/memory/trading_memory.md"),
}
CONFIG_FILE = ROOT / "config/desktop.json"
WRITE_LOCK = threading.Lock()


def emit(kind, payload):
    with WRITE_LOCK:
        print(
            json.dumps({"type": kind, "payload": payload}, ensure_ascii=False, default=str),
            flush=True,
        )


def clean_error(message):
    text = str(message)
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        secret = os.environ.get(name)
        if secret and len(secret) >= 8:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"https?://\S+", "[data source]", text)
    return re.sub(r"(?i)(api[ _-]?key|token|authorization)([=:]\s*)\S+", r"\1\2[redacted]", text)[
        :1600
    ]


class Warnings(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.WARNING:
            emit("warning", {"message": clean_error(record.getMessage())})


def load_environment():
    # The bridge is also imported by tests. Profile loading belongs to its
    # process entry point so importing it never changes the caller's environment.
    global DEFAULT_CONFIG
    load_dotenv(ROOT / "config/agentsroom.env")
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "config/agentsroom.env.example")
    os.environ.setdefault("TRADINGAGENTS_CLI_RUNTIME_DIR", str(ROOT / "work/runtime/cli-models"))
    DEFAULT_CONFIG = build_default_config()


def config():
    value = copy.deepcopy(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        value.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    if not value.get("desktop_role_models"):
        value.pop("desktop_role_models", None)
    value.pop("desktop_council", None)
    value.update(MANAGED)
    if value.get("desktop_vietnam", {}).get("enabled"):
        value["benchmark_map"] = {**value.get("benchmark_map", {}), ".VN": "VNINDEX.VN"}
    return value


def save_config(value):
    if not isinstance(value, dict):
        raise ValueError("Cấu hình phải là JSON object.")
    unknown = set(value) - (
        set(DEFAULT_CONFIG)
        | {"desktop_role_models", "desktop_usage", "desktop_vietnam", "desktop_budget"}
    )
    if unknown:
        raise ValueError("Không có tham số: " + ", ".join(sorted(unknown)))
    value = {k: v for k, v in value.items() if k not in MANAGED}
    for k, v in value.items():
        original = DEFAULT_CONFIG.get(k, {})
        if original is not None and type(v) is not type(original):
            raise ValueError(f"Sai kiểu dữ liệu: {k}")
    for k in ["max_debate_rounds", "max_risk_discuss_rounds", "max_tool_rounds", "max_recur_limit"]:
        if k in value and value[k] < 1:
            raise ValueError(f"{k} phải lớn hơn 0.")
    routes = value.get("desktop_role_models", {})
    from desktop.model_routing import ROLE_NAMES

    for role, spec in routes.items():
        if (
            role not in ROLE_NAMES
            or not isinstance(spec, dict)
            or set(spec) - {"provider", "model", "backend_url", "effort", "max_tokens"}
        ):
            raise ValueError("Phân công mô hình theo vai trò không hợp lệ.")
        if not all(
            isinstance(spec.get(key), str) and spec[key].strip() for key in ("provider", "model")
        ):
            raise ValueError("Mỗi vai trò cần có provider và model.")
        if spec.get("backend_url") is not None and not isinstance(spec["backend_url"], str):
            raise ValueError("Endpoint của vai trò không hợp lệ.")
    for spec in routes.values():
        if spec.get("effort") not in (None, "low", "medium", "high", "xhigh", "max"):
            raise ValueError("Reasoning effort không hợp lệ.")
        if spec.get("max_tokens") is not None and (
            type(spec["max_tokens"]) is not int or spec["max_tokens"] < 1
        ):
            raise ValueError("max_tokens của role phải là số nguyên dương.")
    for name in ("desktop_usage", "desktop_vietnam"):
        if name in value and any(type(v) is not bool for v in value[name].values()):
            raise ValueError(name + " chỉ nhận cờ boolean.")
    candidate = {**DEFAULT_CONFIG, **value}
    from desktop.budget import validate_config

    validate_config(candidate)
    if 2 * candidate["max_tool_rounds"] + 2 >= candidate["max_recur_limit"]:
        raise ValueError("max_recur_limit phải lớn hơn 2 × max_tool_rounds + 2.")
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(CONFIG_FILE)
    return config()


def report_path(identifier, cfg):
    root = Path(cfg["results_dir"]).resolve()
    path = (root / identifier).resolve()
    if not path.is_relative_to(root) or path.name != "complete_report.md" or not path.is_file():
        raise ValueError("Báo cáo không hợp lệ.")
    return path


def history(cfg):
    root = Path(cfg["results_dir"])
    reports = sorted(
        root.rglob("complete_report.md"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    items = []
    for path in reports[:200]:
        if not path.resolve().is_relative_to(root.resolve()):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if not text.strip():
            continue

        def field(label, text=text):
            match = re.search(r"^- " + re.escape(label) + r": (.+)$", text, re.M)
            return match.group(1) if match else ""

        title = text.splitlines()[0].removeprefix("# Trading Analysis Report: ")
        items.append(
            {
                "id": str(path.relative_to(root)),
                "ticker": title,
                "date": field("Analysis date"),
                "rating": field("Rating"),
                "modified": path.stat().st_mtime,
                "backtest": "backtest" in path.parts,
            }
        )
    return items


def inputs(payload):
    from tradingagents.portfolio import PortfolioContext

    ticker = str(payload.get("ticker", "")).strip().upper()
    from tradingagents.dataflows.symbols import safe_ticker_component

    safe_ticker_component(ticker)
    chosen = payload.get("analysts")
    if chosen is None:
        chosen = ["market", "social", "news", "fundamentals"]
    if not isinstance(chosen, list) or not set(chosen) <= {
        "market",
        "social",
        "news",
        "fundamentals",
    }:
        raise ValueError("Lựa chọn analyst không hợp lệ.")
    chosen = [key for key in ("market", "social", "news", "fundamentals") if key in chosen]
    asset = payload.get("asset_type", "stock")
    if asset not in ("stock", "crypto"):
        raise ValueError("Loại tài sản không hợp lệ.")
    if asset == "crypto" and "fundamentals" in chosen:
        chosen = [x for x in chosen if x != "fundamentals"]
    if not chosen:
        raise ValueError("Cần chọn ít nhất một analyst.")
    book = payload.get("portfolio")
    portfolio = PortfolioContext.model_validate(book) if book is not None else None
    return ticker, chosen, asset, portfolio


def markdown(text):
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark", {"html": False}).enable("table").render(text)


def role_reports(state, *, include_empty_memory=True):
    found = {}
    for name, key in {
        "Market Analyst": "market_report",
        "Sentiment Analyst": "sentiment_report",
        "News Analyst": "news_report",
        "Fundamentals Analyst": "fundamentals_report",
        "Research Manager": "investment_plan",
        "Trader": "trader_investment_plan",
        "Portfolio Manager": "final_trade_decision",
    }.items():
        if state.get(key):
            found[name] = state[key]
    for state_key, pairs in [
        (
            "investment_debate_state",
            [("Bull Researcher", "bull_history"), ("Bear Researcher", "bear_history")],
        ),
        (
            "risk_debate_state",
            [
                ("Aggressive Analyst", "aggressive_history"),
                ("Conservative Analyst", "conservative_history"),
                ("Neutral Analyst", "neutral_history"),
            ],
        ),
    ]:
        for name, key in pairs:
            if state.get(state_key, {}).get(key):
                found[name] = state[state_key][key]
    if "past_context" in state or "memory_note" in state:
        lessons = state.get("past_context") or ""
        note = state.get("memory_note") or ""
        if lessons or note or include_empty_memory:
            text = "# Memory & Reflection\n\n## Bài học được cung cấp\n\n"
            text += lessons or "Chưa có bài học được cung cấp cho lượt phân tích này."
            if note:
                text += "\n\n## Trạng thái settlement\n\n" + note
            found["Memory Log"] = text
    return found


def callback():
    from langchain_core.callbacks import BaseCallbackHandler

    class Progress(BaseCallbackHandler):
        def __init__(self):
            self.nodes = {}
            self.completed = set()

        def on_chain_start(self, serialized, inputs, **kwargs):
            # langgraph_node metadata propagates into prompts and parsers.
            # Their completion is not completion of the containing role.
            name = kwargs.get("name")
            if name is None and isinstance(serialized, dict):
                name = serialized.get("name")
            if name in ROLES:
                self.nodes[str(kwargs.get("run_id"))] = name
                emit("node", {"name": name, "status": "running"})

        def on_chain_end(self, outputs, **kwargs):
            name = self.nodes.pop(str(kwargs.get("run_id")), None)
            if name:
                self.completed.add(name)
                emit("node", {"name": name, "status": "completed"})

        def on_llm_start(self, serialized, prompts, **kwargs):
            emit("inference", {"status": "running"})

        def on_chat_model_start(self, serialized, messages, **kwargs):
            emit("inference", {"status": "running"})

        def on_tool_start(self, serialized, input_str, **kwargs):
            emit("tool", {"name": (serialized or {}).get("name", "data"), "status": "running"})

        def on_tool_end(self, output, **kwargs):
            emit("tool", {"status": "completed"})

    return Progress()


def analyze(payload, cfg):
    from tradingagents.graph.trading_graph import TradingAgentsGraph, _validate_trade_date

    options = {
        name: payload.get(name, default)
        for name, default in [("save", True), ("html", True), ("show", False)]
    }
    for name, value in options.items():
        if type(value) is not bool:
            raise ValueError(f"{name} phải là boolean.")
    ticker, chosen, asset, portfolio = inputs(payload)
    trade_date = _validate_trade_date(payload.get("date", date.today().isoformat()))
    emit("started", {"ticker": ticker, "date": trade_date, "analysts": chosen})
    handler = callback()
    from desktop.runtime_models import TokenUsage

    usage = TokenUsage()
    callbacks = [handler]
    if (
        cfg.get("desktop_usage", {}).get("enabled")
        or cfg.get("desktop_vietnam", {}).get("enabled")
        or cfg.get("desktop_budget", {}).get("enabled")
    ):
        callbacks.append(usage)
    graph = TradingAgentsGraph(chosen, config=cfg, callbacks=callbacks)
    from desktop.integration import append_evidence, artifacts, prepare
    from desktop.vietnam import settlement_scope, vendor_scope

    quant, runtime, portfolio, paper = prepare(
        graph, ticker, trade_date, portfolio, cfg, callbacks, emit, allow_paper=True
    )
    from tradingagents.agents.rating import run_rating
    from tradingagents.dataflows.config import run_config

    state, published = {}, {}
    memory_finished = False
    with (
        vendor_scope(quant),
        settlement_scope(cfg),
        run_config(graph.config),
        graph.checkpoint_scope(ticker, trade_date, asset, portfolio) as tid,
    ):
        initial = graph.create_run_state(ticker, trade_date, asset, portfolio)
        args = graph.propagator.get_graph_args(callbacks=callbacks)
        if tid:
            args["config"]["configurable"] = {"thread_id": tid}
        graph_input = graph.checkpoint_input(initial)
        for _messages, chunk in graph.stream_run(graph_input, **args):
            if chunk:
                state.update(chunk)
                # Initial state already has empty memory fields. Those fields
                # alone cannot prove the parallel Memory Log node finished.
                # Research waits for the analysts AND Memory Log, so its real
                # output is also a valid completion signal if callbacks vary.
                research = chunk.get("investment_debate_state") or {}
                memory_finished = memory_finished or (
                    "Memory Log" in handler.completed
                    or bool(chunk.get("past_context") or chunk.get("memory_note"))
                    or bool(research.get("bull_history") or research.get("bear_history"))
                    or bool(
                        chunk.get("investment_plan")
                        or chunk.get("trader_investment_plan")
                        or chunk.get("final_trade_decision")
                    )
                )
                for name, text in role_reports(chunk, include_empty_memory=memory_finished).items():
                    if published.get(name) != text:
                        published[name] = text
                        emit("role_report", {"name": name, "text": text, "html": markdown(text)})
                        emit("node", {"name": name, "status": "completed"})
        # The stream ended successfully: publish even an explicitly empty
        # memory report if an upstream callback omitted its node label.
        memory = role_reports(state).get("Memory Log")
        if memory and published.get("Memory Log") != memory:
            emit("role_report", {"name": "Memory Log", "text": memory, "html": markdown(memory)})
            emit("node", {"name": "Memory Log", "status": "completed"})
        graph.record_decision(ticker, trade_date, state)
        graph.clear_checkpoint_on_success(ticker, trade_date, asset, portfolio)
    rating = run_rating(state)
    path = None
    extra = {}
    if options["save"]:
        base = graph.default_report_path(ticker)
        for suffix in range(10000):
            target = base if suffix == 0 else base.with_name(base.name + f"_{suffix}")
            try:
                target.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("Không tạo được thư mục báo cáo mới.")
        path = graph.save_reports(state, ticker, target, html=options["html"])
        from desktop.report_runtime import enrich_report

        enrich_report(path, ticker, state, graph.run_settings(), cfg, html=options["html"])
        if runtime:
            extra = artifacts(
                path.parent,
                quant,
                runtime,
                usage,
                paper,
                str(path.relative_to(Path(cfg["results_dir"]))),
            )
            append_evidence(path, ticker, state, graph.run_settings(), extra, html=options["html"])
        memory = role_reports(state).get("Memory Log")
        if memory:
            folder = path.parent / "6_memory"
            if not folder.resolve().is_relative_to(path.parent.resolve()):
                raise ValueError("Thư mục báo cáo Memory không hợp lệ.")
            folder.mkdir(exist_ok=True)
            # Atomic replacement also avoids following a pre-existing file symlink.
            temporary = folder / "memory.md.tmp"
            if temporary.is_symlink():
                raise ValueError("File báo cáo Memory không hợp lệ.")
            temporary.write_text(memory, encoding="utf-8")
            temporary.replace(folder / "memory.md")
    if runtime and not options["save"]:
        import tempfile

        with tempfile.TemporaryDirectory(
            dir=cfg["data_cache_dir"], prefix="evidence-preview-"
        ) as transient:
            extra = artifacts(Path(transient), quant, runtime, usage, None, "unsaved")
    # Roles in the final persisted state are stronger completion evidence than
    # callback labels, which can vary between LangGraph versions.
    keys = {
        "Market Analyst": "market_report",
        "Sentiment Analyst": "sentiment_report",
        "News Analyst": "news_report",
        "Fundamentals Analyst": "fundamentals_report",
        "Research Manager": "investment_plan",
        "Trader": "trader_investment_plan",
        "Portfolio Manager": "final_trade_decision",
    }
    for name, key in keys.items():
        if state.get(key):
            emit("node", {"name": name, "status": "completed"})
    for state_key, pairs in [
        (
            "investment_debate_state",
            [("Bull Researcher", "bull_history"), ("Bear Researcher", "bear_history")],
        ),
        (
            "risk_debate_state",
            [
                ("Aggressive Analyst", "aggressive_history"),
                ("Conservative Analyst", "conservative_history"),
                ("Neutral Analyst", "neutral_history"),
            ],
        ),
    ]:
        for name, key in pairs:
            if state.get(state_key, {}).get(key):
                emit("node", {"name": name, "status": "completed"})
    result = {
        "ticker": ticker,
        "date": trade_date,
        "rating": rating,
        **options,
        "report_id": str(path.relative_to(Path(cfg["results_dir"]))) if path else None,
    }
    if options["show"] and not options["save"]:
        from desktop.report_runtime import transient_report

        result["preview"] = transient_report(ticker, state, graph.run_settings(), cfg, markdown)
    result.update(extra)
    return result


def dispatch(action, payload):
    cfg = config()
    if action.startswith("paper_"):
        if not cfg.get("desktop_vietnam", {}).get("paper_enabled"):
            raise ValueError("Tài khoản tiền ảo chưa bật trong cấu hình.")
        from desktop.paper import Store
        from desktop.vietnam import load

        store = Store(cfg["data_cache_dir"])
        if action == "paper_snapshot":
            return store.snapshot()
        if action == "paper_preview":
            return store.preview(payload.get("id", ""))
        if action == "paper_approve":
            return store.approve(
                payload.get("id", ""), payload.get("preview_hash", ""), payload.get("approval")
            )
        if action == "paper_reject":
            return store.reject(payload.get("id", ""))
        if action == "paper_advance":
            target = payload.get("date", date.today().isoformat())
            if date.fromisoformat(target) > date.today():
                raise ValueError("Không tiến replay vào tương lai.")
            book = store.snapshot()
            names = {o["ticker"] for o in book["orders"] if o["approved_at"]}
            names.add(str(payload.get("ticker", "FPT.VN")).upper())
            frames = {
                t: load(t, target, cfg["data_cache_dir"], refresh=True, prices_only=True)["prices"]
                for t in sorted(names)
            }
            return store.advance(target, frames)
        raise ValueError("Thao tác tiền ảo không hợp lệ.")
    if action in ("api_key_status", "save_api_key", "remove_api_key"):
        from desktop import credentials

        provider = payload.get("provider", "deepseek")
        if action == "save_api_key":
            return credentials.save(provider, payload.get("key"))
        if action == "remove_api_key":
            return credentials.remove(provider)
        return credentials.status(provider)
    if action == "test_provider":
        from tradingagents.llm_clients import create_llm_client

        provider = payload.get("provider")
        if provider not in ("deepseek", "codex_cli", "agy_cli", "chatgpt"):
            raise ValueError("Chọn một nhà cung cấp đã hỗ trợ.")
        try:
            controls = {"max_retries": 0, "max_tokens": 256} if provider == "deepseek" else {}
            model = create_llm_client(
                provider, payload.get("model") or "default", **controls
            ).get_llm()
            model.invoke("Reply with OK. Do not use any tools.")
        except Exception as exc:
            raise RuntimeError(
                f"Chưa kết nối được {provider} ({type(exc).__name__}). Kiểm tra đăng nhập, khóa và hạn mức."
            ) from None
        return {"provider": provider, "connected": True}
    if action.startswith("subscription_"):
        from desktop import subscription

        if action == "subscription_status":
            return subscription.status()
        if action == "subscription_login":
            return subscription.login(emit)
        if action == "subscription_redirect":
            return subscription.submit_redirect(
                payload.get("attempt_id", ""), payload.get("url", "")
            )
        if action == "subscription_logout":
            return subscription.logout()
        if action == "subscription_models":
            return subscription.list_models()
        raise ValueError("Thao tác Subscription không hợp lệ.")
    if action == "meta":
        import tradingagents
        from desktop.profiles import BALANCED_BUDGET, balanced_routes
        from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS

        return {
            "version": tradingagents.__version__,
            "config": cfg,
            "roles": ROLES,
            "routing_presets": {
                "balanced_three": {
                    "routes": balanced_routes(True, ROLES),
                    "budget": BALANCED_BUDGET,
                },
                "balanced_two": {
                    "routes": balanced_routes(False, ROLES),
                    "budget": BALANCED_BUDGET,
                },
            },
            "providers": sorted(set(MODEL_OPTIONS) | {"codex_cli", "agy_cli"}),
            "models": {
                name: MODEL_OPTIONS.get(name, {})
                for name in ("deepseek", "codex_cli", "agy_cli", "chatgpt")
            },
            "today": date.today().isoformat(),
            "history": history(cfg),
        }
    if action == "save_config":
        return save_config(payload.get("config"))
    if action == "doctor":
        from tradingagents.diagnostics import diagnose

        result = diagnose(cfg)
        if cfg.get("desktop_role_models"):
            from desktop.model_routing import apply_role_models
            from tradingagents.graph.trading_graph import TradingAgentsGraph

            apply_role_models(TradingAgentsGraph(config=cfg), cfg["desktop_role_models"])
            result["role_models"] = {
                role: {k: spec[k] for k in ("provider", "model")}
                for role, spec in cfg["desktop_role_models"].items()
            }
        return result
    if action == "clear_checkpoints":
        from tradingagents.graph.checkpointer import clear_all_checkpoints

        return {"cleared": clear_all_checkpoints(cfg["data_cache_dir"])}
    if action == "history":
        return history(cfg)
    if action == "report":
        path = report_path(payload.get("id", ""), cfg)
        html = path.with_suffix(".html")
        if html.exists() and not html.resolve().is_relative_to(path.parent.resolve()):
            raise ValueError("HTML report escapes its report directory.")
        sections = []
        for part in sorted(path.parent.glob("*/*.md")):
            if part.resolve().is_relative_to(path.parent.resolve()):
                sections.append(
                    {
                        "name": part.stem,
                        "group": part.parent.name,
                        "text": part.read_text(encoding="utf-8"),
                        "html": markdown(part.read_text(encoding="utf-8")),
                    }
                )
        metadata = path.parent / "execution.json"
        execution = (
            json.loads(metadata.read_text(encoding="utf-8"))
            if metadata.is_file() and not metadata.is_symlink()
            else {}
        )
        return {
            "markdown": path.read_text(encoding="utf-8"),
            "rendered": markdown(path.read_text(encoding="utf-8")),
            "html": html.read_text(encoding="utf-8") if html.exists() else "",
            "sections": sections,
            "path": str(html if html.exists() else path),
            "execution": execution,
            "integration": json.loads((path.parent / "integration.json").read_text())
            if (path.parent / "integration.json").is_file()
            and not (path.parent / "integration.json").is_symlink()
            else {},
        }
    if action == "memory":
        from tradingagents.memory import TradingMemoryLog

        return {"entries": TradingMemoryLog(cfg).load_entries()}
    if action == "analyze":
        return analyze(payload, cfg)
    if action == "backtest":
        from tradingagents.backtest import iter_grid, run_backtest, summarize

        names = [x.strip().upper() for x in str(payload.get("tickers", "")).split(",") if x.strip()]
        if not names:
            raise ValueError("Cần nhập mã cổ phiếu.")
        _, chosen, asset, portfolio = inputs({**payload, "ticker": names[0]})
        dates = iter_grid(payload["start"], payload["end"], int(payload.get("every", 7)))

        def progress(done, total, ticker, day):
            emit("cell", {"done": done, "total": total, "ticker": ticker, "date": day})

        from desktop.integration import backtest_scope

        with backtest_scope(cfg, emit):
            result = run_backtest(
                names,
                dates,
                cfg,
                asset_type=asset,
                portfolio=portfolio,
                selected_analysts=chosen,
                run_id=payload.get("run_id") or None,
                progress=progress,
            )
        return {
            "run_id": result.run_id,
            "cells_run": result.cells_run,
            "skipped": result.skipped,
            "failures": result.failures,
            "settlement_failures": result.settlement_failures,
            "summary": asdict(summarize(result)),
        }
    if action == "settle":
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        all_tickers = payload.get("all", False)
        if type(all_tickers) is not bool:
            raise ValueError("all phải là boolean.")
        ticker = None if all_tickers else inputs(payload)[0]
        handler = callback()
        graph = TradingAgentsGraph(config=cfg, callbacks=[handler])
        from desktop.model_routing import apply_role_models
        from desktop.runtime_models import Runtime

        runtime = Runtime(cfg) if cfg.get("desktop_budget", {}).get("enabled") else None
        apply_role_models(graph, cfg.get("desktop_role_models", {}), [handler], runtime=runtime)
        from desktop.vietnam import settlement_scope

        with settlement_scope(cfg):
            result = graph.settle_all_pending() if all_tickers else graph.settle_pending(ticker)
        return asdict(result)
    raise ValueError("Thao tác không được hỗ trợ.")


def main():
    load_environment()
    logging.getLogger().addHandler(Warnings())
    try:
        request = json.loads(sys.stdin.read())
        result = dispatch(request["action"], request.get("payload") or {})
        emit("result", result)
    except Exception as exc:
        emit("error", {"type": type(exc).__name__, "message": clean_error(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
