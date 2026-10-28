"""Launch the upstream CLI with a portable AgentsRoom backend profile."""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
# Explicit shell settings win, then local project settings, then the example.
load_dotenv(ROOT / "config" / "agentsroom.env")
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "config" / "agentsroom.env.example")
runtime = ROOT / "work" / "runtime"
for key, value in {
    "TRADINGAGENTS_RESULTS_DIR": ROOT / "outputs",
    "TRADINGAGENTS_CACHE_DIR": runtime / "cache",
    "TRADINGAGENTS_MEMORY_LOG_PATH": runtime / "memory" / "trading_memory.md",
    "TRADINGAGENTS_CLI_RUNTIME_DIR": runtime / "cli-models",
}.items():
    os.environ.setdefault(key, str(value))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

# Only convenience-file locations are changed here; the upstream graph, CLI,
# tool implementations, reporting and persistence code are used directly.
from cli import prefs, updates  # noqa: E402
from cli.main import app  # noqa: E402

prefs._PREFS_PATH = runtime / "cli_prefs.json"
updates.CACHE = runtime / "update_check.json"

if __name__ == "__main__":
    if sys.argv[1:] == ["--doctor"]:
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.diagnostics import diagnose

        try:
            print(json.dumps(diagnose(DEFAULT_CONFIG), ensure_ascii=False, indent=2))
        except Exception as error:
            # Exception text may include endpoints or credentials; print type only.
            print(json.dumps({"status": "fail", "error_type": type(error).__name__,
                              "next_step": "Check CLI executable overrides and local model configuration."}))
            raise SystemExit(1) from None
        raise SystemExit(0)
    app()
