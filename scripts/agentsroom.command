#!/bin/sh
set -eu
task_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$task_root/.venv/bin/python" "$task_root/scripts/agentsroom.py" "$@"
