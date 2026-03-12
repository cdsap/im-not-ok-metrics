#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: render_corpus_matrix.py <matrix-json> <iterations-json>", file=sys.stderr)
        return 1

    matrix_path = Path(sys.argv[1]).resolve()
    iterations = json.loads(sys.argv[2])
    payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    entries = payload.get("entries", [])

    include = []
    for entry in entries:
        for iteration in iterations:
            include.append(
                {
                    "target_repository": entry["target_repository"],
                    "target_ref": entry.get("target_ref", ""),
                    "project_slug": entry["project_slug"],
                    "configuration_slug": entry["configuration_slug"],
                    "run_kind": entry["run_kind"],
                    "gradle_command": entry["gradle_command"],
                    "iteration": iteration,
                }
            )

    print(json.dumps({"include": include}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
