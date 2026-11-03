"""Financial score labels must preserve the original evaluator's semantics."""

import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from tradingagents.backtest import BacktestSummary, RatingScore


def render(summary):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    app = Path(__file__).resolve().parents[1] / "desktop/app.js"
    script = """
const fs=require('fs'),vm=require('vm');
const source=fs.readFileSync(process.argv[1],'utf8');
const start=source.indexOf('function formatSummary(result){');
const end=source.indexOf('async function refreshSubscription',start);
const format=vm.runInNewContext('('+source.slice(start,end).trim()+')');
console.log(format(JSON.parse(fs.readFileSync(0,'utf8'))));
"""
    result = subprocess.run(
        [node, "-e", script, str(app)],
        input=json.dumps(
            {
                "run_id": "synthetic_grid",
                "cells_run": 4,
                "skipped": 0,
                "summary": asdict(summary),
            }
        ),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_hold_review_pending_and_window_are_not_hidden_or_scored_as_hits():
    text = render(
        BacktestSummary(
            resolved=2,
            pending=1,
            unscored=1,
            holding="5 trading days",
            by_rating={
                "Hold": RatingScore(count=1, hit_rate=None, mean_alpha=-0.02),
                "Underweight": RatingScore(count=1, hit_rate=1.0, mean_alpha=-0.03),
            },
        )
    )
    assert "Chờ đủ cửa sổ giá: 1" in text
    assert "Không chấm được (REVIEW): 1" in text
    assert "5 phiên giao dịch" in text
    assert "Hold: n=1 · Không dự báo chiều giá · Alpha bình quân: -2.00%" in text
    assert "Underweight: n=1 · Đúng chiều: 100.0% · Alpha bình quân: -3.00%" in text
    assert "không phải lãi/lỗ" in text
    assert "đóng cửa đầu tiên sau ngày phân tích" in text


def test_no_resolved_cell_does_not_invent_a_zero_percent_hit_rate():
    text = render(BacktestSummary(resolved=0, pending=3, by_rating={}, holding=""))
    assert "Đã có kết quả giá: 0" in text and "Chờ đủ cửa sổ giá: 3" in text
    assert "Đúng chiều:" not in text
    assert "Alpha bình quân:" not in text
