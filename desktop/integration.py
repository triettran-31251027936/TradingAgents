"""Desktop composition of the unchanged upstream workflow and VN transports."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from pathlib import Path

from desktop import vietnam
from desktop.model_routing import apply_role_models
from desktop.runtime_models import Runtime, TokenUsage

VN_METHODS = (
    "get_stock_data",
    "get_indicators",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
    "get_macro_indicators",
    "get_insider_transactions",
)


def configure(cfg):
    cfg = {**cfg, "benchmark_map": dict(cfg.get("benchmark_map", {}))}
    if cfg.get("desktop_vietnam", {}).get("enabled"):
        cfg["benchmark_map"][".VN"] = "VNINDEX.VN"
    return cfg


def prepare(graph, ticker, asof, portfolio, cfg, callbacks, emit, *, allow_paper=False):
    quant = None
    store = None
    book = None
    if cfg.get("desktop_vietnam", {}).get("enabled") and vietnam.is_vietnam(ticker):
        emit(
            "progress",
            {"message": "Đang thu thập giá, VNINDEX và BCTC đã công bố từ nguồn Việt Nam…"},
        )
        quant = vietnam.load(ticker, asof, cfg["data_cache_dir"])
        graph.config["tool_vendors"] = {
            **graph.config.get("tool_vendors", {}),
            **dict.fromkeys(VN_METHODS, "vietnam"),
        }
        graph.config["desktop_quant_fingerprint"] = quant["fingerprint"]
        # Resolve once from the same verified instrument, without a Yahoo request.
        from tradingagents.agents.context import build_instrument_context

        graph.resolve_instrument_context = lambda name, asset_type="stock", trade_date=None: (
            build_instrument_context(
                name,
                asset_type,
                {"company_name": quant["company_name"], "exchange": quant["exchange"]},
                trade_date,
            )
        )
        if (
            allow_paper
            and cfg.get("desktop_vietnam", {}).get("paper_enabled")
            and portfolio is None
        ):
            from desktop.practice import PracticeStore as Store
            from tradingagents.portfolio import PortfolioContext

            store = Store(cfg["data_cache_dir"])
            book = store.snapshot()
            portfolio = PortfolioContext(
                cash=book["available_cash"],
                currency="VND",
                positions=[
                    {"ticker": t, "quantity": p["quantity"], "average_price": p["average_price"]}
                    for t, p in book["positions"].items()
                ],
            )
        emit(
            "quantitative",
            {
                "ticker": quant["ticker"],
                "quote_date": quant["prices"][-1]["date"],
                "available": sum(v["value"] is not None for v in quant["metrics"].values()),
                "total": len(quant["metrics"]),
                "applicable_total": sum(
                    m.get("status") != "not_applicable" for m in quant["metrics"].values()
                ),
                "not_applicable": sum(
                    m.get("status") == "not_applicable" for m in quant["metrics"].values()
                ),
                "missing": quant["missing"],
            },
        )
    runtime = (
        Runtime(cfg, quant=quant, book=book, emit=emit)
        if quant
        or cfg.get("desktop_usage", {}).get("enabled")
        or cfg.get("desktop_budget", {}).get("enabled")
        else None
    )
    apply_role_models(graph, cfg.get("desktop_role_models", {}), callbacks, runtime=runtime)
    return quant, runtime, portfolio, store


def artifacts(folder, quant, runtime, usage, store, report_id):
    result = {}
    if quant:
        vietnam.write_json(folder / "quantitative.json", quant)
        result["quantitative"] = {
            "ticker": quant["ticker"],
            "company_profile": quant.get("company_profile", {}),
            "domestic_macro": quant.get("domestic_macro", {}),
            "insider_dealings": quant.get("insider_dealings", {}),
            "domestic_social": quant.get("domestic_social", {}),
            "quote_date": quant["prices"][-1]["date"],
            "metrics": quant["metrics"],
            "missing": quant["missing"],
            "warnings": quant["warnings"],
            "basis": quant["basis"],
            "sources": quant["sources"],
            "financial_reconciliation": quant.get("financial_reconciliation", {}),
            "valuation_reconciliation": quant.get("valuation_reconciliation", {}),
            "bank_reconciliation": quant.get("bank_reconciliation", {}),
        }
    if runtime:
        result["grounding"] = runtime.grounding
        result["handoffs"] = runtime.handoffs
        result["account_metrics"] = runtime.account_metrics
        vietnam.write_json(folder / "handoffs.json", runtime.handoffs)
        result["paid_budget"] = runtime.paid_budget.summary()
        vietnam.write_json(folder / "grounding.json", runtime.grounding)
        result["usage"] = {**usage.summary(), "roles": runtime.stats}
        vietnam.write_json(folder / "usage.json", result["usage"])
        if runtime.decision:
            result["decision"] = runtime.decision
            vietnam.write_json(folder / "decision.json", runtime.decision)
            if store:
                try:
                    result["paper_proposal"] = store.proposal(
                        report_id, runtime.decision, quant, quant["exchange"]
                    )
                except ValueError as exc:
                    result["paper_proposal"] = {"status": "BLOCKED", "reason": str(exc)}
            else:
                result["paper_proposal"] = {
                    "status": "DISABLED",
                    "reason": "Không dùng tài khoản tiền ảo cho lượt này; chỉ lưu decision.",
                }
        elif quant:
            result["paper_proposal"] = {
                "status": "BLOCKED",
                "reason": "Portfolio Manager chưa trả về decision có schema thực thi hợp lệ; không suy lệnh từ văn bản.",
            }
    vietnam.write_json(folder / "integration.json", result)
    return result


def append_evidence(path, ticker, state, settings, result, *, html=True):
    """Deterministic appendix; exported reports contain the same evidence as UI."""
    import json

    from desktop.report_runtime import provenance_markdown
    from tradingagents.report_html import render_report
    from tradingagents.reporting import _report_parts

    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    q = result.get("quantitative")
    text = "## Canonical evidence and virtual execution\n\n"
    if q:
        text += f"Ticker: {q['ticker']} · latest completed quote: {q['quote_date']}\n\n"
        profile = q.get("company_profile") or {}
        if profile.get("status") == "available":
            text += "### Issuer profile observed " + cell(profile.get("observed_at")) + "\n\n"
            text += (
                cell(profile.get("description_vi") or profile.get("description_en") or "") + "\n\n"
            )
            text += (
                "Sector: "
                + cell(profile.get("sector_vi") or profile.get("sector") or "unavailable")
                + ". Source: Vietcap company/details. Current observation, not a historical profile archive.\n\n"
            )

        if q.get("bank_reconciliation"):
            text += "\n### Chỉ tiêu ngân hàng\n\nTỷ số kế toán dư nợ/tiền gửi không phải LDR theo quy định NHNN. Tỷ số provider được giữ riêng, có thể mang dấu chi phí/dự phòng âm; phương pháp và cửa sổ tính chưa được đối soát từ thuyết minh. TTM tự tính chỉ dùng các quý liên tiếp đã đối chiếu với báo cáo năm.\n\n"
        text += "| Metric | Value | Unit | As of | Formula / source |\n|---|---:|---|---|---|\n"
        for name, m in q["metrics"].items():
            values = (
                name,
                m["value"]
                if m["value"] is not None
                else "NOT APPLICABLE"
                if m.get("status") == "not_applicable"
                else "MISSING",
                m["unit"],
                m["asof"] + (" published:" + m["published"] if m.get("published") else ""),
                m["formula"] + " / " + m["source"],
            )
            text += "| " + " | ".join(cell(v) for v in values) + " |\n"
        text += "\n" + "\n".join("- " + cell(v) for v in q["basis"] + q["warnings"]) + "\n"
        text += "\nCoverage is limited to the recorded sources; domestic shareholder-dealing coverage is aggregator-reported and distinct from exchange-confirmed results; community RSS is a bounded unverified opinion sample, and missing social coverage is not zero sentiment. NSDP macro files may lag newer press releases; read each observation period and freshness warning.\n"
        insider = q.get("insider_dealings") or {}
        if insider:
            text += "\n### Giao dịch cổ đông và người nội bộ — toàn bộ dòng nguồn đã lưu\n\n"
            text += "Nguồn tổng hợp CafeF, chưa đối chiếu từng công bố với sở giao dịch. Đăng ký không phải kết quả; ESOP không phải mua khớp lệnh. Ngày công bố ban đầu không chứng minh ngày thị trường biết kết quả. Không có giá thực hiện.\n\n"
            rows = insider.get("records", [])
            text += f"Trạng thái: {cell(insider.get('status'))}. Số dòng lưu: {len(rows)}.\n\n"
            text += "| Cá nhân / tổ chức | Vai trò | Phương thức / ghi chú | Công bố ban đầu | Kỳ đăng ký | Mua / bán đăng ký | Ngày kết thúc kết quả | Mua / bán báo cáo | Cảnh báo |\n|---|---|---|---|---|---:|---|---:|---|\n"
            for row in rows:
                values = (
                    row.get("party"),
                    row.get("role"),
                    str(row.get("mechanism")) + " / " + str(row.get("note") or ""),
                    row.get("source_announcement_date"),
                    str(row.get("plan_begin")) + " → " + str(row.get("plan_end")),
                    str(row.get("planned_buy")) + " / " + str(row.get("planned_sell")),
                    row.get("reported_result_end"),
                    str(row.get("reported_buy")) + " / " + str(row.get("reported_sell")),
                    "; ".join(row.get("quality_notes", [])),
                )
                text += "| " + " | ".join(cell(v) for v in values) + " |\n"
        social = q.get("domestic_social") or {}
        if social:
            text += "\n### Mẫu thảo luận cộng đồng Việt Nam — F247 RSS\n\n"
            text += "Mẫu RSS theo tag, trích bài đầu; không đọc bình luận/hình ảnh, không đại diện toàn thị trường. Ngày RSS không chứng minh thời điểm sửa nội dung. Chủ đề gắn tag có thể nói về doanh nghiệp khác. Không suy ra tỷ lệ lạc quan, số người hay sức mua từ mẫu này.\n\n"
            text += f"Trạng thái: {cell(social.get('status'))}; giữ {len(social.get('records', []))} chủ đề trong kỳ; context dùng {len(social.get('context_records', []))} chủ đề có nhắc mã.\n\n"
            for row in social.get("records", []):
                text += (
                    f"- [{cell(row['title'])}]({row['url']}) · {cell(row['published'])} · {cell(row['relevance'])} · {cell(row['excerpt'])}"
                    + (" [trích đoạn đã cắt]" if row["excerpt_truncated"] else " [phần mô tả RSS]")
                    + "\n"
                )
        text += "\n### Quantitative context handoffs\n\n"
        for role, receipt in result.get("handoffs", {}).items():
            text += f"- {role}: {receipt['metric_count']} metrics supplied, {receipt['available_metrics']} available; snapshot {receipt['snapshot_fingerprint']}. Input verified, not a guarantee of correct interpretation.\n"
        for source in q["sources"]:
            text += f"\n- {source['name']}: {source['url']} (retrieved {source['retrieved_at']})\n"
    d = result.get("decision")
    account = result.get("account_metrics") or {}
    if account.get("status") == "available":
        text += "\n### Virtual-account risk and reference sizing\n\n| Metric | Value | Unit | Formula |\n|---|---:|---|---|\n"
        for name, m in account["metrics"].items():
            text += (
                "| "
                + " | ".join(
                    cell(v)
                    for v in (
                        name,
                        m["value"]
                        if m["value"] is not None
                        else "NOT APPLICABLE"
                        if m.get("status") == "not_applicable"
                        else "MISSING",
                        m["unit"],
                        m["formula"],
                    )
                )
                + " |\n"
            )
        text += "\n" + account["limitations"] + "\n"
    if d:
        e = d["execution"]
        text += f"\n### Proposed virtual action\n\nAction: {e['action']} · quantity: {e['quantity']} · limit: {e['limit_price']} VND/share\n\n{e['rationale']}\n\n"
        text += "\n".join("- Data gap: " + str(g) for g in e["data_gaps"]) + "\n"
    proposal = result.get("paper_proposal", {})
    text += (
        "\nPaper status: "
        + str(proposal.get("status", "DISABLED"))
        + ". A proposal is not an executed order; separate human approval is required.\n"
    )
    u = result.get("usage", {})
    text += f"\nReported token usage: input {u.get('reported_input_tokens', 'unavailable')}, output {u.get('reported_output_tokens', 'unavailable')}; requests without counters {u.get('unreported_requests', 'unavailable')}. Not a monetary or subscription-limit estimate.\n"
    part = path.parent / "7_evidence"
    part.mkdir(exist_ok=True)
    (part / "evidence.md").write_text(text, encoding="utf-8")
    path.write_text(path.read_text(encoding="utf-8") + "\n\n" + text, encoding="utf-8")
    if html:
        details = json.loads((path.parent / "execution.json").read_text())
        parts = _report_parts(state) + [
            (
                "VI. Model selection",
                "execution",
                [("Model transport", "runtime.md", provenance_markdown(details))],
            ),
            (
                "VII. Evidence and virtual execution",
                "evidence",
                [("Canonical evidence", "evidence.md", text)],
            ),
        ]
        path.with_suffix(".html").write_text(
            render_report(ticker, state, settings, parts), encoding="utf-8"
        )


@contextmanager
def backtest_scope(cfg, emit):
    """Keep upstream grid/resume/settlement; provide a dated transport per cell."""
    from tradingagents import backtest

    original = backtest.TradingAgentsGraph

    class Graph(original):
        def __init__(self, *args, **kwargs):
            from desktop.budget import backtest_config

            if "config" in kwargs:
                kwargs["config"] = backtest_config(kwargs["config"])
            super().__init__(*args, **kwargs)
            self._desktop_base_config = copy.deepcopy(self.config)
            self._desktop_identity = self.resolve_instrument_context

        def propagate(self, ticker, asof, asset_type="stock", portfolio=None):
            self.config = copy.deepcopy(self._desktop_base_config)
            self.resolve_instrument_context = self._desktop_identity
            counter = TokenUsage()
            callbacks = [counter]
            self.callbacks = callbacks
            quant, runtime, portfolio, _ = prepare(
                self, ticker, asof, portfolio, self.config, callbacks, emit
            )
            from tradingagents.dataflows.config import run_config

            with vietnam.vendor_scope(quant), run_config(self.config):
                result = super().propagate(ticker, asof, asset_type, portfolio=portfolio)
            # Original reports/scoring remain; these are supporting audit files.
            from tradingagents.dataflows.symbols import safe_ticker_component

            folder = (
                Path(self.config["results_dir"]) / "evidence" / safe_ticker_component(ticker) / asof
            )
            folder.mkdir(parents=True, exist_ok=True)
            artifacts(folder, quant, runtime, counter, None, "backtest/" + ticker + "/" + asof)
            return result

    backtest.TradingAgentsGraph = Graph
    try:
        with vietnam.settlement_scope(cfg):
            yield
    finally:
        backtest.TradingAgentsGraph = original
