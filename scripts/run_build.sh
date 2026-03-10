#!/usr/bin/env bash

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$HARNESS_ROOT_DIR}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"

BUILD_CMD="${*:-./gradlew assembleDebug}"
TIMESTAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
ARTIFACT_DIR="${ARTIFACT_DIR:-$PROJECT_ROOT/artifacts/$TIMESTAMP}"
LOG_DIR="$ARTIFACT_DIR/logs"
GC_DIR="$LOG_DIR/gc"
JFR_DIR="$LOG_DIR/jfr"
OS_DIR="$LOG_DIR/os"
GRADLE_DIR="$ARTIFACT_DIR/gradle"
WARNINGS_FILE="$ARTIFACT_DIR/warnings.log"
METADATA_FILE="$ARTIFACT_DIR/metadata.json"
DISCOVERED_PIDS_FILE="$OS_DIR/discovered_pids.csv"
PROCESS_METRICS_FILE="$OS_DIR/process_metrics.csv"
SYSTEM_METRICS_FILE="$OS_DIR/system_metrics.csv"
STDOUT_LOG="$LOG_DIR/gradle_stdout.log"
STDERR_LOG="$LOG_DIR/gradle_stderr.log"
GC_ARGS_TEMPLATE_FILE="$HARNESS_ROOT_DIR/configs/jvm_args_gc_logging.txt"
JFR_ARGS_TEMPLATE_FILE="$HARNESS_ROOT_DIR/configs/jvm_args_jfr.txt"
SAMPLING_CONFIG_FILE="$HARNESS_ROOT_DIR/configs/sampling_config.env"
DEEP="${DEEP:-0}"
ENABLE_JCMD_ATTACH="${ENABLE_JCMD_ATTACH:-1}"
ENABLE_JCMD_DYNAMIC_GC_LOGS="${ENABLE_JCMD_DYNAMIC_GC_LOGS:-1}"

mkdir -p "$GC_DIR" "$JFR_DIR" "$OS_DIR" "$GRADLE_DIR"
: >"$WARNINGS_FILE"
: >"$STDOUT_LOG"
: >"$STDERR_LOG"

if [[ -f "$SAMPLING_CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$SAMPLING_CONFIG_FILE"
fi

PID_DISCOVERY_INTERVAL_SECONDS="${PID_DISCOVERY_INTERVAL_SECONDS:-2}"
SAMPLING_INTERVAL_SECONDS="${SAMPLING_INTERVAL_SECONDS:-1}"

warn() {
  printf '%s %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*" | tee -a "$WARNINGS_FILE" >&2
}

iso_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

collect_java_metadata() {
  local vendor version runtime
  vendor="$(java -XshowSettings:properties -version 2>&1 | awk -F'= ' '/^\s*java.vendor = / {print $2; exit}' || true)"
  version="$(java -XshowSettings:properties -version 2>&1 | awk -F'= ' '/^\s*java.version = / {print $2; exit}' || true)"
  runtime="$(java -XshowSettings:properties -version 2>&1 | awk -F'= ' '/^\s*java.runtime.version = / {print $2; exit}' || true)"
  printf '%s|%s|%s' "${vendor:-unknown}" "${version:-unknown}" "${runtime:-unknown}"
}

detect_total_mem_kb() {
  case "$(uname -s)" in
    Linux)
      awk '/MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || true
      ;;
    Darwin)
      local bytes
      bytes="$(sysctl -n hw.memsize 2>/dev/null || true)"
      if [[ -n "$bytes" ]]; then
        awk -v bytes="$bytes" 'BEGIN {printf "%.0f\n", bytes / 1024}'
      fi
      ;;
  esac
}

detect_cgroup_limit() {
  if [[ -f /sys/fs/cgroup/memory.max ]]; then
    cat /sys/fs/cgroup/memory.max 2>/dev/null || true
  elif [[ -f /sys/fs/cgroup/memory/memory.limit_in_bytes ]]; then
    cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null || true
  fi
}

extract_wrapper_gradle_version() {
  if [[ -f "$PROJECT_ROOT/gradle/wrapper/gradle-wrapper.properties" ]]; then
    sed -nE 's#.*gradle-([0-9.]+)-.*#\1#p' "$PROJECT_ROOT/gradle/wrapper/gradle-wrapper.properties" | head -n1
  fi
}

grep_build_version() {
  local pattern="$1"
  rg --no-heading --glob '*.gradle' --glob '*.gradle.kts' -n "$pattern" "$PROJECT_ROOT" 2>/dev/null | head -n1 | sed 's/"/\\"/g' || true
}

write_metadata() {
  python3 - "$METADATA_FILE" <<'PY'
import json
import os
import sys

path = sys.argv[1]
data = {
    "repo_name": os.environ.get("META_REPO_NAME"),
    "git_sha": os.environ.get("META_GIT_SHA") or None,
    "captured_at": os.environ.get("META_CAPTURED_AT"),
    "os": os.environ.get("META_OS"),
    "kernel": os.environ.get("META_KERNEL"),
    "cpu_cores": int(os.environ["META_CPU_CORES"]) if os.environ.get("META_CPU_CORES") else None,
    "total_mem_kb": int(os.environ["META_TOTAL_MEM_KB"]) if os.environ.get("META_TOTAL_MEM_KB") else None,
    "cgroup_memory_limit": os.environ.get("META_CGROUP_LIMIT") or None,
    "gradle_version": os.environ.get("META_GRADLE_VERSION") or None,
    "agp_version": os.environ.get("META_AGP_VERSION") or None,
    "kotlin_version": os.environ.get("META_KOTLIN_VERSION") or None,
    "jdk_vendor": os.environ.get("META_JDK_VENDOR"),
    "jdk_version": os.environ.get("META_JDK_VERSION"),
    "jdk_runtime": os.environ.get("META_JDK_RUNTIME"),
    "full_command": os.environ.get("META_FULL_COMMAND"),
    "deep_mode": os.environ.get("META_DEEP_MODE") == "1",
    "build_started_at": os.environ.get("META_BUILD_STARTED_AT"),
    "build_finished_at": None,
    "build_exit_code": None,
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
}

update_metadata_after_build() {
  python3 - "$METADATA_FILE" "$1" "$2" <<'PY'
import json
import sys

path, finished_at, exit_code = sys.argv[1:]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
data["build_finished_at"] = finished_at
data["build_exit_code"] = int(exit_code)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
}

timestamp_stream() {
  local log_file="$1"
  while IFS= read -r line || [[ -n "$line" ]]; do
    printf '%s %s\n' "$(iso_now)" "$line" >>"$log_file"
    printf '%s\n' "$line"
  done
}

enable_dynamic_gc_logging() {
  local pid="$1"
  local log_path="$GC_DIR/attached-$pid.gc.log"
  if [[ "$ENABLE_JCMD_DYNAMIC_GC_LOGS" != "1" ]] || ! command -v jcmd >/dev/null 2>&1; then
    return 0
  fi
  if [[ -f "$log_path" ]]; then
    return 0
  fi
  jcmd "$pid" VM.log "output=$log_path" "what=gc*=debug,gc+heap=debug,gc+age=trace,safepoint*=info" >/dev/null 2>&1 || return 1
}

enable_jfr_attach() {
  local pid="$1"
  local jfr_path="$JFR_DIR/jvm-$pid.jfr"
  [[ "$DEEP" == "1" ]] || return 0
  command -v jcmd >/dev/null 2>&1 || return 1
  [[ -f "$jfr_path" ]] && return 0
  jcmd "$pid" JFR.start "name=ci-harness-$pid" settings=profile "filename=$jfr_path" dumponexit=true >/dev/null 2>&1 || return 1
}

attach_loop() {
  local seen_file="$ARTIFACT_DIR/.attached_pids"
  : >"$seen_file"
  trap 'exit 0' INT TERM
  while true; do
    if [[ -f "$DISCOVERED_PIDS_FILE" ]]; then
      awk -F, 'NR > 1 {print $2}' "$DISCOVERED_PIDS_FILE" | sort -u | while read -r pid; do
        [[ -n "$pid" ]] || continue
        grep -qx "$pid" "$seen_file" 2>/dev/null && continue
        kill -0 "$pid" 2>/dev/null || continue
        if [[ "$ENABLE_JCMD_ATTACH" == "1" ]]; then
          enable_dynamic_gc_logging "$pid" || warn "Could not enable dynamic GC logging for pid $pid"
          enable_jfr_attach "$pid" || {
            if [[ "$DEEP" == "1" ]]; then
              warn "Could not enable JFR for pid $pid"
            fi
          }
        fi
        printf '%s\n' "$pid" >>"$seen_file"
      done
    fi
    sleep 2
  done
}

finalize_gc_files() {
  python3 - "$DISCOVERED_PIDS_FILE" "$GC_DIR" "$JFR_DIR" <<'PY'
import csv
import os
import shutil
import sys
from pathlib import Path

pid_file = Path(sys.argv[1])
gc_dir = Path(sys.argv[2])
jfr_dir = Path(sys.argv[3])

roles = {}
if pid_file.exists():
    with pid_file.open() as fh:
        for row in csv.DictReader(fh):
            roles[row["pid"]] = row.get("role") or "jvm"

def materialize(kind_dir: Path, suffix: str):
    counts = {}
    for path in sorted(kind_dir.glob("*")):
        if not path.is_file():
            continue
        pid = None
        for token in path.stem.split("-"):
            if token.isdigit():
                pid = token
                break
        if not pid:
            continue
        role = roles.get(pid, "jvm")
        counts[role] = counts.get(role, 0) + 1
        target_name = f"{role}{suffix}" if counts[role] == 1 else f"{role}-{pid}{suffix}"
        target = kind_dir / target_name
        if target.exists():
            continue
        shutil.copy2(path, target)

materialize(gc_dir, "-gc.log")
materialize(jfr_dir, ".jfr")
PY
}

GC_ARGS=""
if [[ -f "$GC_ARGS_TEMPLATE_FILE" ]]; then
  GC_ARGS="$(sed "s#{gc_log_path}#$GC_DIR/jvm-%p.gc.log#g" "$GC_ARGS_TEMPLATE_FILE")"
fi
JFR_ARGS=""

JVM_TOOL_OPTS="${JAVA_TOOL_OPTIONS:-}"
if [[ -n "$GC_ARGS" ]]; then
  JVM_TOOL_OPTS="${JVM_TOOL_OPTS:+$JVM_TOOL_OPTS }$GC_ARGS"
fi

export META_REPO_NAME="$(basename "$PROJECT_ROOT")"
export META_GIT_SHA="$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || true)"
export META_CAPTURED_AT="$(iso_now)"
export META_OS="$(uname -s)"
export META_KERNEL="$(uname -r)"
export META_CPU_CORES="$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || true)"
export META_TOTAL_MEM_KB="$(detect_total_mem_kb)"
export META_CGROUP_LIMIT="$(detect_cgroup_limit)"
export META_GRADLE_VERSION="$(extract_wrapper_gradle_version)"
export META_AGP_VERSION="$(grep_build_version 'com\.android\.tools\.build:gradle|id\([[:space:]]*"com\.android\.[^"]+"' | head -n1)"
export META_KOTLIN_VERSION="$(grep_build_version 'org\.jetbrains\.kotlin|kotlin\("android"\)|kotlin\("jvm"\)' | head -n1)"
IFS='|' read -r META_JDK_VENDOR META_JDK_VERSION META_JDK_RUNTIME <<<"$(collect_java_metadata)"
export META_JDK_VENDOR META_JDK_VERSION META_JDK_RUNTIME
export META_FULL_COMMAND="$BUILD_CMD"
export META_DEEP_MODE="$DEEP"
export META_BUILD_STARTED_AT="$(iso_now)"
export META_PROJECT_ROOT="$PROJECT_ROOT"
write_metadata

if [[ "$DEEP" == "1" && ! -x "$(command -v jcmd 2>/dev/null)" ]]; then
  warn "DEEP=1 requested but jcmd is not available; JFR collection will be skipped"
fi

PIDS_WATCH_PID=""
METRICS_PID=""
ATTACH_PID=""
STDOUT_CAPTURE_PID=""
STDERR_CAPTURE_PID=""
STDOUT_PIPE=""
STDERR_PIPE=""

cleanup() {
  [[ -n "$PIDS_WATCH_PID" ]] && kill "$PIDS_WATCH_PID" 2>/dev/null || true
  [[ -n "$METRICS_PID" ]] && kill "$METRICS_PID" 2>/dev/null || true
  [[ -n "$ATTACH_PID" ]] && kill "$ATTACH_PID" 2>/dev/null || true
  [[ -n "$STDOUT_CAPTURE_PID" ]] && kill "$STDOUT_CAPTURE_PID" 2>/dev/null || true
  [[ -n "$STDERR_CAPTURE_PID" ]] && kill "$STDERR_CAPTURE_PID" 2>/dev/null || true
  [[ -n "$STDOUT_PIPE" ]] && rm -f "$STDOUT_PIPE" 2>/dev/null || true
  [[ -n "$STDERR_PIPE" ]] && rm -f "$STDERR_PIPE" 2>/dev/null || true
  rm -f "$ARTIFACT_DIR/.attached_pids" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"$SCRIPT_DIR/find_pids.sh" --output "$DISCOVERED_PIDS_FILE" --watch --interval "$PID_DISCOVERY_INTERVAL_SECONDS" &
PIDS_WATCH_PID=$!

"$SCRIPT_DIR/collect_metrics.sh" \
  --pids-file "$DISCOVERED_PIDS_FILE" \
  --process-output "$PROCESS_METRICS_FILE" \
  --system-output "$SYSTEM_METRICS_FILE" \
  --interval "$SAMPLING_INTERVAL_SECONDS" &
METRICS_PID=$!

attach_loop &
ATTACH_PID=$!

export JAVA_TOOL_OPTIONS="$JVM_TOOL_OPTS"

STDOUT_PIPE="$ARTIFACT_DIR/.stdout.pipe"
STDERR_PIPE="$ARTIFACT_DIR/.stderr.pipe"
rm -f "$STDOUT_PIPE" "$STDERR_PIPE"
mkfifo "$STDOUT_PIPE" "$STDERR_PIPE"

timestamp_stream "$STDOUT_LOG" <"$STDOUT_PIPE" &
STDOUT_CAPTURE_PID=$!
timestamp_stream "$STDERR_LOG" <"$STDERR_PIPE" >&2 &
STDERR_CAPTURE_PID=$!

(cd "$PROJECT_ROOT" && /bin/bash -lc "$BUILD_CMD") >"$STDOUT_PIPE" 2>"$STDERR_PIPE"
BUILD_EXIT_CODE=$?

wait "$STDOUT_CAPTURE_PID" "$STDERR_CAPTURE_PID" 2>/dev/null || true
rm -f "$STDOUT_PIPE" "$STDERR_PIPE"

export META_BUILD_FINISHED_AT="$(iso_now)"
update_metadata_after_build "$META_BUILD_FINISHED_AT" "$BUILD_EXIT_CODE"

sleep 1
cleanup
finalize_gc_files || warn "Could not materialize role-named GC/JFR artifacts"
"$SCRIPT_DIR/summarize.sh" "$ARTIFACT_DIR" || warn "Summarization failed"

printf 'Artifacts: %s\n' "$ARTIFACT_DIR"
exit "$BUILD_EXIT_CODE"
