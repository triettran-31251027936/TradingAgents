"""Persist RSS retrieval receipts independently of normalized content versions."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from desktop.vietnam import digest, write_json

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def stamp(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("Observation requires timezone")
    return dt.astimezone(TZ)


def read(path):
    if not path.is_file() or path.is_symlink():
        return None
    try:
        row = json.loads(path.read_text())
        if (
            stamp(row["first_observed_at"]) > stamp(row["retrieved_at"])
            or not isinstance(row["records"], list)
            or not isinstance(row["issues"], list)
            or not isinstance(row["source_sha256"], str)
            or type(row["source_item_count"]) is not int
        ):
            return None
        return row
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _order(time):
    return time.strftime("%Y%m%dT%H%M%S%f")


def _archive(folder, row):
    if row is None:
        return
    name = f"{_order(stamp(row['retrieved_at']))}-{digest(row)}.json"
    path = folder / "observations" / name
    if not path.is_file():
        write_json(path, row)


def save(folder, version_path, first, previous, observed):
    if not first:
        write_json(version_path, observed)
    # Preserve the last legacy observation before replacing it during upgrade.
    _archive(folder, previous)
    _archive(folder, observed)
    write_json(folder / "latest.json", observed)


def replay(folder, end, latest):
    # Read legacy content-addressed archives, including the last pre-upgrade
    # observation. A content version's first-seen date alone cannot date its
    # raw source hash when a legacy writer overwrote its retrieval receipt.
    rows = [latest, *(read(path) for path in folder.glob("*.snapshot.json"))]
    cutoff = _order(end)
    for path in sorted((folder / "observations").glob("*.json"), reverse=True):
        if path.name.split("-", 1)[0] <= cutoff:
            row = read(path)
            if row and stamp(row["retrieved_at"]) <= end:
                rows.append(row)
                break
    return max(
        (row for row in rows if row and stamp(row["retrieved_at"]) <= end),
        key=lambda row: stamp(row["retrieved_at"]),
        default=None,
    )
