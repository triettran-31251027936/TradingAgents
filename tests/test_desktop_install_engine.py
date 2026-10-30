"""Refresh app code without overwriting the user's runtime state."""

import json
from pathlib import Path

import pytest

from desktop.install_engine import install


def fixture_source(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    for name in ("desktop", "cli", "tradingagents"):
        (root / name).mkdir()
        (root / name / "__init__.py").write_text("VERSION=1\n")
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").write_text("isolated test interpreter")
    (root / ".venv/lib/python3.12/site-packages").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config/desktop.json").write_text('{"policy":"original"}')
    (root / "outputs").mkdir()
    (root / "outputs/report.md").write_text("Original report")
    (root / "work/runtime").mkdir(parents=True)
    (root / "work/runtime/memory.md").write_text("Original memory")
    (root / ".env").write_text("TEST_VALUE=isolated_fixture\n")
    return root


def test_initial_migration_copies_state_and_returns_runtime_manifest(tmp_path):
    source = fixture_source(tmp_path)
    target = tmp_path / "Application Support/engine"
    manifest = install(source, target)
    assert Path(manifest["repo"]) == target
    assert Path(manifest["sourceRepo"]) == source
    assert Path(manifest["pythonSitePackages"]).is_dir()
    assert (target / "outputs/report.md").read_text() == "Original report"
    assert (source / "outputs/report.md").exists()
    assert (target / ".env").stat().st_mode & 0o777 == 0o600
    marker = json.loads((target / ".tradingagents-engine.json").read_text())
    assert "desktop/__init__.py" in marker["code_sha256"]


def test_later_install_refreshes_code_but_never_overwrites_private_state(tmp_path):
    source = fixture_source(tmp_path)
    target = tmp_path / "engine"
    install(source, target)
    (target / "config/desktop.json").write_text('{"policy":"user_changed"}')
    (target / "work/runtime/memory.md").write_text("User memory after migration")
    (target / ".env").write_text("TEST_VALUE=user_changed\n")
    (source / "desktop/__init__.py").write_text("VERSION=2\n")
    (target / "desktop/retired_council.py").write_text("retired feature")
    install(source, target)
    assert "VERSION=2" in (target / "desktop/__init__.py").read_text()
    assert not (target / "desktop/retired_council.py").exists()
    assert "user_changed" in (target / "config/desktop.json").read_text()
    assert (target / "work/runtime/memory.md").read_text() == "User memory after migration"
    assert "user_changed" in (target / ".env").read_text()


def test_rejects_other_checkout_and_links(tmp_path):
    source = fixture_source(tmp_path)
    target = tmp_path / "engine"
    install(source, target)
    other = tmp_path / "another"
    other.mkdir()
    with pytest.raises(ValueError, match="another"):
        install(other, target)
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        install(source, link)
