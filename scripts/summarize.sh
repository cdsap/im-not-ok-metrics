#!/usr/bin/env bash

set -euo pipefail

ARTIFACT_DIR="${1:-}"

if [[ -z "$ARTIFACT_DIR" ]]; then
  echo "Usage: summarize.sh <artifacts/timestamp-dir>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PARSE_JFR_SUMMARY="${PARSE_JFR_SUMMARY:-0}"
python3 "$SCRIPT_DIR/summarize.py" "$ARTIFACT_DIR"
