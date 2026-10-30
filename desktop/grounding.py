"""Source readback checks for the bounded Antigravity sentiment role.

These checks verify input identity, source references and selected unsupported
numeric claims. They do not certify the semantic truth of an LLM inference.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from tradingagents.agents.schemas import SentimentReport


class ContextReadback(BaseModel):
    ticker: str
    analysis_date: str
    snapshot_fingerprint: str
    headline_ids: list[str] = Field(
        description="Every news:<id> supplied in the context ledger, exactly once. No invented IDs."
    )
    news_count: int = Field(ge=0, strict=True)
    stocktwits_status: str
    reddit_status: str
    domestic_social_status: str = "not_supplied"
    domestic_topic_ids: list[str] = Field(default_factory=list)


class EvidenceClaim(BaseModel):
    claim: str = Field(
        description="One concrete observation or explicitly labelled inference; separate observation from interpretation."
    )
    source_ids: list[str] = Field(
        min_length=1,
        description="Supporting IDs from the supplied ledger only. Never cite a source that was not retrieved.",
    )


class GroundedSentimentReport(SentimentReport):
    context_readback: ContextReadback
    evidence_claims: list[EvidenceClaim] = Field(
        description="Evidence-linked observations and interpretations. No unsupported financial valuation claims."
    )


def _block_status(messages, kind):
    text = "\n".join(str(m.content) for m in messages)
    match = re.search(rf"<start_of_{kind}>(.*?)<end_of_{kind}>", text, re.S)
    if not match:
        return "unknown"
    block = match.group(1).strip().lower()
    if "unavailable" in block or "fetch failed" in block:
        return "unavailable"
    if re.search(r"<no |<none of ", block):
        return "empty"
    return "present" if block else "empty"


def ledger(bundle, messages):
    news = [
        {
            "id": "news:" + str(r["id"]),
            "published": r["published"],
            "headline": r["headline"],
            "url": r["url"],
            "coverage": r["coverage"],
        }
        for r in bundle.get("news", [])
    ]
    social = bundle.get("domestic_social") or {}
    topics = social.get("context_records", [])
    social_status = social.get("status", "not_supplied")
    if social_status == "available":
        social_status = "present" if topics else "empty"
    return {
        "ticker": bundle["ticker"],
        "analysis_date": bundle["analysis_date"],
        "snapshot_fingerprint": bundle["fingerprint"],
        "news_count": len(news),
        "domestic_social_status": social_status,
        "domestic_topic_ids": [r["id"] for r in topics],
        "domestic_social_source": social.get("source_url"),
        "domestic_social_scope": social.get("scope"),
        "headline_ids": [r["id"] for r in news],
        "stocktwits_status": _block_status(messages, "stocktwits"),
        "reddit_status": _block_status(messages, "reddit"),
        "news": news,
        "other_source_ids": ["input:stocktwits", "input:reddit", "input:limitations"],
        "quantitative_source_ids": [
            "metric:" + k for k, m in bundle["metrics"].items() if m["value"] is not None
        ],
        "instructions": "Read back identity, date, fingerprint, all headline IDs, domestic_topic_ids and source statuses exactly. Domestic f247:<id> evidence is supplied in canonical domestic_social.context_records; tags alone may be irrelevant. Community posts are unverified opinions, not financial facts. Anchor each evidence_claim to IDs. An unavailable source says nothing about market sentiment. Headlines are not full article text. Financial ratios absent from canonical metrics must not be invented. Confidence must be low when sources are missing or news count is below5.",
    }


def validate(report, bundle, messages):
    expected = ledger(bundle, messages)
    read = report.context_readback.model_dump()
    for key in (
        "ticker",
        "analysis_date",
        "snapshot_fingerprint",
        "news_count",
        "stocktwits_status",
        "reddit_status",
        "domestic_social_status",
    ):
        if read[key] != expected[key]:
            raise ValueError(
                f"Antigravity context check failed: {key}; do not complete the role with an unverified report."
            )
    if len(read["headline_ids"]) != len(set(read["headline_ids"])) or set(
        read["headline_ids"]
    ) != set(expected["headline_ids"]):
        raise ValueError("Antigravity did not read back the exact provided news set.")
    if len(read["domestic_topic_ids"]) != len(set(read["domestic_topic_ids"])) or set(
        read["domestic_topic_ids"]
    ) != set(expected["domestic_topic_ids"]):
        raise ValueError("Antigravity did not read back the exact domestic community topic set.")
    allowed = set(
        expected["headline_ids"]
        + expected["other_source_ids"]
        + expected["quantitative_source_ids"]
        + expected["domestic_topic_ids"]
    )
    if (expected["news_count"] or expected["domestic_topic_ids"]) and not report.evidence_claims:
        raise ValueError("Antigravity omitted evidence-linked observations.")
    for claim in report.evidence_claims:
        if not set(claim.source_ids) <= allowed:
            raise ValueError("Antigravity cited a source outside the provided evidence ledger.")
    sparse = expected["news_count"] < 5 or any(
        expected[k] != "present" for k in ("stocktwits_status", "reddit_status")
    )
    if bundle.get("domestic_social"):
        sparse = (
            sparse
            or expected["domestic_social_status"] != "present"
            or len(expected["domestic_topic_ids"]) < 5
        )
    if sparse and report.confidence != "low":
        raise ValueError("Antigravity overstated confidence for sparse/unavailable sources.")
    text = report.narrative + "\n" + "\n".join(c.claim for c in report.evidence_claims)
    # This role has no independently verified PE/PB/ROE/EPS valuation series.
    unsupported = re.search(
        r"\b(?:P\s*/\s*[EB]|ROE|EPS\s*TTM)\s*(?:(?:là|khoảng|=|:)\s*)?[−-]?\d", text, re.I
    )
    if unsupported:
        raise ValueError(
            "Antigravity inserted an unsupported valuation figure; remove it or explicitly report missing data."
        )
    marker = "### Kiểm tra nguồn đầu vào"
    if marker not in report.narrative:
        appendix = (
            "\n\n"
            + marker
            + f"\n\nMã: {expected['ticker']} · ngày: {expected['analysis_date']} · {expected['news_count']} tiêu đề tin. StockTwits: {expected['stocktwits_status']}; Reddit: {expected['reddit_status']}.\n\n"
        )
        if bundle.get("domestic_social"):
            appendix += f"F247 RSS: {expected['domestic_social_status']} · {len(expected['domestic_topic_ids'])} chủ đề đã cấp; mẫu trích bài đầu, không đại diện thị trường.\n\n"
        for claim in report.evidence_claims:
            appendix += "- " + claim.claim + " [" + ", ".join(claim.source_ids) + "]\n"
        report = report.model_copy(update={"narrative": report.narrative + appendix})
    receipt = {
        "validated": True,
        "readback": read,
        "claims": [c.model_dump() for c in report.evidence_claims],
        "scope": "Identity/source readback and bounded numeric checks; not proof that every interpretation is correct.",
    }
    return report, receipt
