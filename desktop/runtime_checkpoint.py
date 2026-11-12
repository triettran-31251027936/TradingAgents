"""Desktop receipts belong to the same run as the upstream graph checkpoint."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from pathlib import Path

from desktop.vietnam import write_json


class RuntimeCheckpoint:
    def __init__(self, runtime, usage, thread_id, *, resuming):
        self.runtime, self.usage = runtime, usage
        self.lock = threading.Lock()
        self.signature = hashlib.sha256(thread_id.encode()).hexdigest()
        self.path = (
            Path(runtime.cfg["data_cache_dir"]) / "desktop-checkpoints" / (self.signature + ".json")
        )
        self.fingerprint = (runtime.quant or {}).get("fingerprint")
        if resuming:
            self.restore()
        runtime._checkpoint = self
        usage.on_change = self.save
        self.save()

    def restore(self):
        try:
            if self.path.is_symlink():
                raise ValueError("Symlink")
            value = json.loads(self.path.read_text())
            if (
                value["version"] != 1
                or value["signature"] != self.signature
                or value["quant_fingerprint"] != self.fingerprint
            ):
                raise ValueError("Checkpoint identity mismatch")
            calls = value["paid_attempts"]
            if type(calls) is not int or calls < 0:
                raise ValueError("Invalid paid attempts")
            for field in ("stats", "handoffs", "grounding", "incomplete_roles"):
                if not isinstance(value[field], dict):
                    raise ValueError("Invalid receipt field")
            if not isinstance(value["usage_items"], list):
                raise ValueError("Invalid usage")
            if not isinstance(value["usage_pending"], list):
                raise ValueError("Invalid pending usage")
            for row in value["usage_items"]:
                if not isinstance(row, dict) or "tokens" not in row:
                    raise ValueError("Invalid usage row")
            decision = value["decision"]
            if decision is not None:
                from desktop.runtime_models import ExecutablePortfolioDecision

                decision = ExecutablePortfolioDecision.model_validate(decision).model_dump(
                    mode="json"
                )
            for receipt in value["handoffs"].values():
                if (
                    not isinstance(receipt, dict)
                    or receipt.get("snapshot_fingerprint") != self.fingerprint
                ):
                    raise ValueError("Handoff snapshot mismatch")
            for field in ("stats", "handoffs", "grounding", "incomplete_roles"):
                setattr(self.runtime, field, value[field])
            news_tool_receipts = value.get("news_tool_receipts", {})
            if not isinstance(news_tool_receipts, dict):
                raise ValueError("Invalid news tool receipts")
            self.runtime.news_tool_receipts = news_tool_receipts
            market_tool_receipts = value.get("market_tool_receipts", {})
            if not isinstance(market_tool_receipts, dict) or any(
                not isinstance(row, dict) or row.get("snapshot_fingerprint") != self.fingerprint
                for row in market_tool_receipts.values()
            ):
                raise ValueError("Invalid market tool receipts")
            self.runtime.market_tool_receipts = market_tool_receipts
            self.runtime.decision = decision
            self.runtime.paid_budget.calls = calls
            self.usage.items = value["usage_items"]
            for row in value["usage_pending"]:
                if not isinstance(row, dict) or "role" not in row:
                    raise ValueError("Invalid pending receipt")
                self.usage.items.append(
                    {
                        **{k: row.get(k) for k in ("role", "provider", "requested_model")},
                        "tokens": None,
                        "status": "interrupted_before_receipt",
                    }
                )
            # A crash can also occur after reserving an attempt but before the
            # start callback. Preserve it as unknown, never zero or billed usage.
            for role, stats in self.runtime.stats.items():
                calls = stats.get("calls") if isinstance(stats, dict) else None
                if type(calls) is not int or calls < 0:
                    raise ValueError("Invalid role calls")
                observed = sum(item.get("role") == role for item in self.usage.items)
                spec = self.runtime.cfg.get("desktop_role_models", {}).get(role, {})
                for _ in range(max(0, calls - observed)):
                    self.usage.items.append(
                        {
                            "role": role,
                            "provider": spec.get("provider"),
                            "requested_model": spec.get("model"),
                            "tokens": None,
                            "status": "reserved_attempt_without_receipt",
                        }
                    )
            # A process can stop after a completion callback was persisted but
            # before Runtime._capture. Retain that terminal length signal too.
            for item in self.usage.items:
                role, finish = item.get("role"), item.get("finish_reason")
                if not role or item.get("has_tool_calls"):
                    continue
                if finish == "length":
                    self.runtime.incomplete_roles[role] = "output_token_limit"
                elif finish == "stop":
                    self.runtime.incomplete_roles.pop(role, None)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ValueError(
                "Checkpoint thiếu hoặc sai metadata desktop; không tự đặt lại ngân sách hay coi báo cáo là đầy đủ. "
                "Giữ checkpoint để kiểm tra, hoặc dùng Xóa checkpoint để chủ động bắt đầu lượt mới."
            ) from exc

    def save(self):
        # All mutation hooks release their own lock before entering this writer.
        # Serialize writers as well as replacement, so an older concurrent
        # snapshot cannot overwrite the latest completed role or paid attempt.
        with self.lock:
            with self.runtime.lock:
                value = copy.deepcopy(
                    {
                        field: getattr(self.runtime, field)
                        for field in (
                            "stats",
                            "handoffs",
                            "grounding",
                            "incomplete_roles",
                            "decision",
                            "news_tool_receipts",
                            "market_tool_receipts",
                        )
                    }
                )
            with self.runtime.paid_budget.lock:
                value["paid_attempts"] = self.runtime.paid_budget.calls
            with self.usage.lock:
                value["usage_items"] = copy.deepcopy(self.usage.items)
                value["usage_pending"] = [
                    {
                        "role": meta.get("ta_role", meta.get("langgraph_node")),
                        "provider": meta.get("ta_provider"),
                        "requested_model": meta.get("ta_requested_model"),
                    }
                    for meta in self.usage.roles.values()
                ]
            value.update(version=1, signature=self.signature, quant_fingerprint=self.fingerprint)
            write_json(self.path, value)

    def clear(self):
        self.path.unlink(missing_ok=True)
        self.runtime._checkpoint = None
        self.usage.on_change = lambda: None
