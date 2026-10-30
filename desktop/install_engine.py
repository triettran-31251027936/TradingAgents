"""Stage the dedicated app runtime outside macOS-protected Documents folders.

Code is refreshed from the checkout. Mutable state is migrated once, never
replaced by a later deployment. The checkout and its private files are retained.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


def install(source, destination=None):
    source = Path(source).resolve()
    destination = Path(
        destination or (Path.home() / "Library/Application Support/Trading Agents/engine")
    )
    if (
        destination.is_symlink()
        or destination.resolve() == source
        or destination.resolve().is_relative_to(source)
    ):
        raise ValueError("Engine runtime must be a separate real directory.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = destination / ".tradingagents-engine.json"
    first = not marker.exists()
    if not first:
        previous = json.loads(marker.read_text())
        if previous.get("source") != str(source):
            raise ValueError(
                "Runtime belongs to another source checkout; inspect before replacing code."
            )
    if first:
        # Preserve raw personal files exactly; do not print their contents.
        for name in ("config", "outputs", "work/runtime"):
            src = source / name
            dst = destination / name
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, dst, symlinks=False)
        if (source / ".env").is_file() and not (destination / ".env").exists():
            shutil.copy2(source / ".env", destination / ".env")
            (destination / ".env").chmod(0o600)
    venv = destination / ".venv"
    if not venv.exists():
        shutil.copytree(source / ".venv", venv, symlinks=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    for name in ("tradingagents", "cli", "desktop"):
        dst = destination / name
        if dst.is_symlink():
            raise ValueError("Engine code directory cannot be a link.")
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(source / name, dst, ignore=ignore)
    for name in ("pyproject.toml", "README.md", "LICENSE", "DESKTOP-INTEGRATION.md"):
        if (source / name).is_file():
            shutil.copy2(source / name, destination / name)
    hashes = {
        str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest()
        for name in ("tradingagents", "cli", "desktop")
        for p in (destination / name).rglob("*.py")
    }
    marker.write_text(
        json.dumps(
            {"source": str(source), "runtime": str(destination), "code_sha256": hashes}, indent=2
        )
        + "\n"
    )
    marker.chmod(0o600)
    python = venv / "bin/python"
    site = next((venv / "lib").glob("python*/site-packages"))
    return {
        "repo": str(destination),
        "sourceRepo": str(source),
        "python": str(python),
        "pythonExecutable": str(python.resolve()),
        "pythonSitePackages": str(site),
    }
