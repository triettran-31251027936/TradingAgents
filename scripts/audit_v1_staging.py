"""Verify v1 source staging and preservation of the installed Python engine.

This checks source bytes, not desktop integration or live model/data coverage.
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "121265d18d467ac717f7af99cb0d669c15d2e73c"
DESKTOP = "af6dae7"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def check(ref, prefixes):
    entries = git("ls-tree", "-r", ref).decode().splitlines()
    checked, differences = 0, []
    for entry in entries:
        info, name = entry.split("\t", 1)
        if not name.startswith(prefixes):
            continue
        checked += 1
        path = ROOT / name
        if not path.is_file() or git("hash-object", "--", name).decode().strip() != info.split()[2]:
            differences.append(name)
    return {"files_checked": checked, "changed_or_missing": differences,
            "passed": checked > 0 and not differences}


def main():
    result = {
        "scope": "source preservation only; v1 is not wired into the desktop",
        "upstream_commit": UPSTREAM,
        "desktop_baseline": DESKTOP,
        "v1": check(UPSTREAM, ("src/", "specs/")),
        "desktop": check(DESKTOP, ("tradingagents/", "cli/", "desktop/", "tests/")),
    }
    print(json.dumps(result, indent=2))
    return 0 if all(result[key]["passed"] for key in ("v1", "desktop")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
