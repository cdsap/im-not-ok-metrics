#!/usr/bin/env bash

set -euo pipefail

ARTIFACT_ROOT="${1:-}"
OUTPUT_PATH="${2:-}"

if [[ -z "$ARTIFACT_ROOT" ]]; then
  echo "Usage: index_dataset.sh <artifacts-root> [output-jsonl]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/index_dataset.py" "$ARTIFACT_ROOT" ${OUTPUT_PATH:+"$OUTPUT_PATH"}
