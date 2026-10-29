"""Compare original workflow files against a local upstream Git ref."""

import argparse
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="upstream/main")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]

    def git(*parts):
        return subprocess.check_output(["git", *parts], cwd=root)

    paths = git("ls-tree", "-r", "--name-only", args.ref).decode().splitlines()
    prefixes = ("tradingagents/agents/", "tradingagents/graph/", "tradingagents/dataflows/",
                "tradingagents/memory/")
    modules = {"tradingagents/backtest.py", "tradingagents/portfolio.py",
               "tradingagents/reporting.py", "tradingagents/report_html.py",
               "tradingagents/default_config.py", "cli/main.py", "cli/run.py", "cli/selections.py"}
    core = [p for p in paths if p.startswith(prefixes) or p in modules]
    changed = [p for p in core if not (root / p).exists()
               or (root / p).read_bytes() != git("show", f"{args.ref}:{p}")]
    result = {
        "scope": "source preservation; does not verify live data, inference or financial accuracy",
        "upstream_commit": git("rev-parse", args.ref).decode().strip(),
        "files_checked": len(core), "changed_or_missing": changed,
        "all_preserved": bool(core) and not changed,
    }
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["all_preserved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
