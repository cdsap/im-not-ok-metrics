#!/usr/bin/env bash

set -euo pipefail

INTERVAL_SECONDS="${PID_DISCOVERY_INTERVAL_SECONDS:-2}"
OUTPUT_FILE=""
MODE="once"
INCLUDE_OTHER_JVMS=0

usage() {
  cat <<'EOF'
Usage: find_pids.sh --output <file> [--watch] [--interval <seconds>] [--include-other-jvms]

Discovers JVM PIDs that look relevant to Gradle Android builds and writes CSV rows:
timestamp,pid,role,command
EOF
}

csv_escape() {
  local value="${1:-}"
  value="${value//\"/\"\"}"
  printf '"%s"' "$value"
}

classify_role() {
  local command="${1:-}"
  case "$command" in
    *GradleDaemon*)
      printf 'gradle-daemon'
      ;;
    *KotlinCompileDaemon*|*kotlin-daemon*|*org.jetbrains.kotlin.daemon*)
      printf 'kotlin-daemon'
      ;;
    *'Gradle Test Executor '*|*JUnit*|*Surefire*|*TestWorker*)
      printf 'test-jvm'
      ;;
    *GradleWorkerMain*|*worker.org.gradle.process.internal.worker.GradleWorkerMain*)
      printf 'gradle-worker'
      ;;
    *Lint*|*D8*|*R8*)
      printf 'jvm-tool'
      ;;
    *)
      if [[ "$INCLUDE_OTHER_JVMS" -eq 1 ]]; then
        printf 'jvm'
      fi
      ;;
  esac
}

discover_with_jcmd() {
  jcmd -l 2>/dev/null | while read -r pid rest; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    local role
    role="$(classify_role "$rest")"
    [[ -n "$role" ]] || continue
    printf '%s,%s,%s,' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$pid" "$role"
    csv_escape "$rest"
    printf '\n'
  done
}

discover_with_ps() {
  ps -axo pid=,command= | while read -r pid rest; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    case "$rest" in
      java*|*/java*)
        ;;
      *)
        continue
        ;;
    esac
    local role
    role="$(classify_role "$rest")"
    [[ -n "$role" ]] || continue
    printf '%s,%s,%s,' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$pid" "$role"
    csv_escape "$rest"
    printf '\n'
  done
}

discover_once() {
  if command -v jcmd >/dev/null 2>&1; then
    discover_with_jcmd
  else
    discover_with_ps
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      OUTPUT_FILE="$2"
      shift 2
      ;;
    --watch)
      MODE="watch"
      shift
      ;;
    --once)
      MODE="once"
      shift
      ;;
    --interval)
      INTERVAL_SECONDS="$2"
      shift 2
      ;;
    --include-other-jvms)
      INCLUDE_OTHER_JVMS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$OUTPUT_FILE" ]]; then
  echo "--output is required" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"
if [[ ! -f "$OUTPUT_FILE" ]]; then
  printf 'timestamp,pid,role,command\n' >"$OUTPUT_FILE"
fi

if [[ "$MODE" == "once" ]]; then
  discover_once >>"$OUTPUT_FILE"
  exit 0
fi

trap 'exit 0' INT TERM
while true; do
  discover_once >>"$OUTPUT_FILE" || true
  sleep "$INTERVAL_SECONDS"
done
