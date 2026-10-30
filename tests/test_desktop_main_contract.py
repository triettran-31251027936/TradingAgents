"""Exercise the real Electron main module with isolated process/dialog doubles."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_native_approval_and_startup_contract():
    node = shutil.which("node")
    if not node:
        node_path = (
            Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
        )
        node = str(node_path) if node_path.is_file() else None
    if not node:
        pytest.skip("Node.js is needed for the isolated Electron-main contract test")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("desktop_main_contract.cjs"))],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cancel/confirm" in result.stdout
