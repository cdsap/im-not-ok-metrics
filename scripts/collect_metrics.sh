#!/usr/bin/env bash

set -euo pipefail

PIDS_FILE=""
PROCESS_OUTPUT=""
SYSTEM_OUTPUT=""
INTERVAL_SECONDS="${SAMPLING_INTERVAL_SECONDS:-1}"

usage() {
  cat <<'EOF'
Usage: collect_metrics.sh --pids-file <file> --process-output <file> --system-output <file> [--interval <seconds>]
EOF
}

read_roles() {
  local pids_file="$1"
  awk -F, 'NR > 1 { gsub(/"/, "", $4); latest[$2] = $3 } END { for (pid in latest) print pid "," latest[pid] }' "$pids_file" 2>/dev/null || true
}

linux_process_metrics() {
  local pid="$1"
  local rss_kb="" vsize_kb="" threads="" io_read_kb="" io_write_kb="" majflt="" minflt="" cpu_pct=""
  [[ -r "/proc/$pid/status" ]] || return 1

  rss_kb="$(awk '/VmRSS:/ {print $2}' "/proc/$pid/status" 2>/dev/null || true)"
  vsize_kb="$(awk '/VmSize:/ {print $2}' "/proc/$pid/status" 2>/dev/null || true)"
  threads="$(awk '/Threads:/ {print $2}' "/proc/$pid/status" 2>/dev/null || true)"
  cpu_pct="$(ps -p "$pid" -o %cpu= 2>/dev/null | awk '{print $1}' || true)"

  if [[ -r "/proc/$pid/io" ]]; then
    io_read_kb="$(awk '/read_bytes:/ {printf "%.0f", $2 / 1024}' "/proc/$pid/io" 2>/dev/null || true)"
    io_write_kb="$(awk '/write_bytes:/ {printf "%.0f", $2 / 1024}' "/proc/$pid/io" 2>/dev/null || true)"
  fi

  if [[ -r "/proc/$pid/stat" ]]; then
    local trimmed
    trimmed="$(sed -E 's/^[0-9]+ \(.+\) //' "/proc/$pid/stat" 2>/dev/null || true)"
    minflt="$(awk '{print $8}' <<<"$trimmed" 2>/dev/null || true)"
    majflt="$(awk '{print $10}' <<<"$trimmed" 2>/dev/null || true)"
  fi

  printf '%s,%s,%s,%s,%s,%s,%s,%s' \
    "${rss_kb:-}" "${vsize_kb:-}" "${cpu_pct:-}" "${threads:-}" "${io_read_kb:-}" "${io_write_kb:-}" "${majflt:-}" "${minflt:-}"
}

mac_process_metrics() {
  local pid="$1"
  local values
  values="$(ps -p "$pid" -o rss=,vsz=,%cpu=,thcount= 2>/dev/null | awk '{$1=$1; print}' || true)"
  [[ -n "$values" ]] || return 1
  local rss_kb vsize_kb cpu_pct threads
  rss_kb="$(awk '{print $1}' <<<"$values")"
  vsize_kb="$(awk '{print $2}' <<<"$values")"
  cpu_pct="$(awk '{print $3}' <<<"$values")"
  threads="$(awk '{print $4}' <<<"$values")"
  printf '%s,%s,%s,%s,,,,' "${rss_kb:-}" "${vsize_kb:-}" "${cpu_pct:-}" "${threads:-}"
}

sample_processes() {
  local ts="$1"
  read_roles "$PIDS_FILE" | while IFS=, read -r pid role; do
    [[ -n "${pid:-}" ]] || continue
    kill -0 "$pid" 2>/dev/null || continue
    local metrics=""
    case "$(uname -s)" in
      Linux)
        metrics="$(linux_process_metrics "$pid" || true)"
        ;;
      Darwin)
        metrics="$(mac_process_metrics "$pid" || true)"
        ;;
    esac
    [[ -n "$metrics" ]] || continue
    printf '%s,%s,%s,%s\n' "$ts" "$pid" "$metrics" "$role" >>"$PROCESS_OUTPUT"
  done
}

linux_system_metrics() {
  local loadavg mem_available swap_used disk_read_kb disk_write_kb
  loadavg="$(awk '{print $1}' /proc/loadavg 2>/dev/null || true)"
  mem_available="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null || true)"
  local swap_total swap_free
  swap_total="$(awk '/SwapTotal:/ {print $2}' /proc/meminfo 2>/dev/null || true)"
  swap_free="$(awk '/SwapFree:/ {print $2}' /proc/meminfo 2>/dev/null || true)"
  if [[ -n "$swap_total" && -n "$swap_free" ]]; then
    swap_used="$((swap_total - swap_free))"
  fi
  if [[ -r /proc/diskstats ]]; then
    local disk_totals
    disk_totals="$(awk '{read += $6 / 2; write += $10 / 2} END {printf "%.0f %.0f\n", read, write}' /proc/diskstats 2>/dev/null || true)"
    disk_read_kb="$(awk '{print $1}' <<<"$disk_totals")"
    disk_write_kb="$(awk '{print $2}' <<<"$disk_totals")"
  fi
  printf '%s,%s,%s,%s,%s' "${loadavg:-}" "${mem_available:-}" "${swap_used:-}" "${disk_read_kb:-}" "${disk_write_kb:-}"
}

mac_system_metrics() {
  local loadavg pages_free pages_inactive page_size mem_available swap_used
  loadavg="$(sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}' || true)"
  page_size="$(sysctl -n hw.pagesize 2>/dev/null || echo 4096)"
  pages_free="$(vm_stat 2>/dev/null | awk '/Pages free:/ {gsub("\\.","",$3); print $3}' || true)"
  pages_inactive="$(vm_stat 2>/dev/null | awk '/Pages inactive:/ {gsub("\\.","",$3); print $3}' || true)"
  if [[ -n "$pages_free" && -n "$pages_inactive" ]]; then
    mem_available="$(( (pages_free + pages_inactive) * page_size / 1024 ))"
  fi
  swap_used="$(sysctl -n vm.swapusage 2>/dev/null | awk -F'[, =]+' '{for (i=1; i<=NF; i++) if ($i == "used") print $(i+1)}' | sed 's/M$//' || true)"
  printf '%s,%s,%s,,' "${loadavg:-}" "${mem_available:-}" "${swap_used:-}"
}

sample_system() {
  local ts="$1"
  local metrics=""
  case "$(uname -s)" in
    Linux)
      metrics="$(linux_system_metrics)"
      ;;
    Darwin)
      metrics="$(mac_system_metrics)"
      ;;
  esac
  printf '%s,%s\n' "$ts" "$metrics" >>"$SYSTEM_OUTPUT"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pids-file)
      PIDS_FILE="$2"
      shift 2
      ;;
    --process-output)
      PROCESS_OUTPUT="$2"
      shift 2
      ;;
    --system-output)
      SYSTEM_OUTPUT="$2"
      shift 2
      ;;
    --interval)
      INTERVAL_SECONDS="$2"
      shift 2
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

if [[ -z "$PIDS_FILE" || -z "$PROCESS_OUTPUT" || -z "$SYSTEM_OUTPUT" ]]; then
  usage >&2
  exit 1
fi

mkdir -p "$(dirname "$PROCESS_OUTPUT")" "$(dirname "$SYSTEM_OUTPUT")"
printf 'timestamp,pid,rss_kb,vsize_kb,cpu_pct,threads,io_read_kb,io_write_kb,majflt,minflt,role\n' >"$PROCESS_OUTPUT"
printf 'timestamp,loadavg,mem_available_kb,swap_used_kb,disk_read_kb,disk_write_kb\n' >"$SYSTEM_OUTPUT"

trap 'exit 0' INT TERM
while true; do
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  sample_processes "$ts" || true
  sample_system "$ts" || true
  sleep "$INTERVAL_SECONDS"
done
