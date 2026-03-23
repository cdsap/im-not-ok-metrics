#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
import math
import os
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ISO_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ")
JFR_PARSE_TIMEOUT_SECONDS = int(os.environ.get("JFR_PARSE_TIMEOUT_SECONDS", "90"))
JFR_MAX_PARSE_BYTES = int(os.environ.get("JFR_MAX_PARSE_BYTES", str(96 * 1024 * 1024)))
PARSE_JFR_SUMMARY = os.environ.get("PARSE_JFR_SUMMARY", "0") == "1"


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ISO_FORMATS:
      try:
        return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
      except ValueError:
        pass
    return None


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def safe_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def safe_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def load_metadata(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_pid_roles(path: Path) -> dict[str, dict]:
    roles: dict[str, dict] = {}
    if not path.exists():
        return roles
    with path.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            pid = row.get("pid")
            if not pid:
                continue
            roles[pid] = {
                "role": row.get("role") or "unknown",
                "command": row.get("command") or "",
                "last_seen": row.get("timestamp"),
            }
    return roles


def summarize_process_metrics(path: Path) -> tuple[dict[str, dict], list[dict]]:
    per_pid: dict[str, dict] = {}
    rows: list[dict] = []
    if not path.exists():
        return per_pid, rows

    buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    peaks: dict[str, dict] = {}

    with path.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(row)
            pid = row["pid"]
            rss = safe_float(row.get("rss_kb"))
            cpu = safe_float(row.get("cpu_pct"))
            role = row.get("role") or "unknown"
            if rss is not None:
                buckets[pid]["rss"].append(rss)
            if cpu is not None:
                buckets[pid]["cpu"].append(cpu)
            buckets[pid]["role"] = [role]

            current_peak = peaks.get(pid)
            if rss is not None and (current_peak is None or rss > current_peak["rss_kb"]):
                peaks[pid] = {"rss_kb": rss, "timestamp": row.get("timestamp"), "role": role}

    for pid, stats in buckets.items():
        rss_values = stats.get("rss", [])
        cpu_values = stats.get("cpu", [])
        per_pid[pid] = {
            "max_rss_kb": max(rss_values) if rss_values else None,
            "avg_rss_kb": statistics.mean(rss_values) if rss_values else None,
            "max_cpu_pct": max(cpu_values) if cpu_values else None,
            "role": stats.get("role", ["unknown"])[0],
            "peak_rss_timestamp": peaks.get(pid, {}).get("timestamp"),
        }
    return per_pid, rows


GC_PAUSE_PATTERNS = [
    re.compile(r"\bgc[^\]]*\]\s+GC\(\d+\)\s+Pause .*? ([0-9]+(?:\.[0-9]+)?)(ms|s)\b"),
    re.compile(r"Total time for which application threads were stopped: ([0-9]+(?:\.[0-9]+)?) seconds"),
]
SAFEPOINT_TOTAL_PATTERN = re.compile(r"\[info \]\[safepoint\s*\].* Total: ([0-9]+(?:\.[0-9]+)?)(ns|ms|s)\b")
GC_NAME_PATTERN = re.compile(r"\[info\s*\]\[gc(?:,init)?\s*\]\s+Using ([A-Za-z0-9 +_-]+)")


def duration_to_ms(value: float, unit: str) -> float:
    if unit == "s":
        return value * 1000.0
    if unit == "ns":
        return value / 1_000_000.0
    return value


def parse_gc_pauses(log_path: Path) -> list[float]:
    pauses_ms: list[float] = []
    if not log_path.exists():
        return pauses_ms
    for line in log_path.read_text(errors="ignore").splitlines():
        matched_pause = False
        for pattern in GC_PAUSE_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            value = float(match.group(1))
            unit = match.group(2) if len(match.groups()) > 1 else "s"
            pauses_ms.append(duration_to_ms(value, unit))
            matched_pause = True
            break
        if matched_pause:
            continue

        # Fallback for logs where only safepoint totals are available.
        match = SAFEPOINT_TOTAL_PATTERN.search(line)
        if match:
            pauses_ms.append(duration_to_ms(float(match.group(1)), match.group(2)))
    return pauses_ms


def parse_observed_gc_name(log_path: Path) -> str | None:
    if not log_path.exists():
        return None
    for line in log_path.read_text(errors="ignore").splitlines():
        match = GC_NAME_PATTERN.search(line)
        if match:
            return match.group(1).strip()
    return None


def declared_profile_to_collector_name(declared_profile: str | None) -> str | None:
    if not declared_profile:
        return None
    normalized = declared_profile.strip().lower()
    if normalized == "imnotokay":
        return "UseImNotOkayGC"
    return declared_profile


def choose_reported_gc_name(declared_profile: str | None, observed_gc_name: str | None) -> str | None:
    declared_name = declared_profile_to_collector_name(declared_profile)
    return declared_name or observed_gc_name


def summarize_gc(gc_dir: Path) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    if not gc_dir.exists():
        return stats
    pauses_by_pid: dict[str, list[float]] = defaultdict(list)
    files_by_pid: dict[str, list[str]] = defaultdict(list)
    observed_gc_by_pid: dict[str, str | None] = {}

    for path in sorted(gc_dir.glob("*.log")):
        pid_match = re.search(r"([0-9]+)", path.name)
        pid = pid_match.group(1) if pid_match else path.stem
        pauses_by_pid[pid].extend(parse_gc_pauses(path))
        files_by_pid[pid].append(str(path))
        observed_name = parse_observed_gc_name(path)
        if observed_name and not observed_gc_by_pid.get(pid):
            observed_gc_by_pid[pid] = observed_name

    for pid, pauses in pauses_by_pid.items():
        stats[pid] = {
            "files": files_by_pid[pid],
            "file": files_by_pid[pid][-1] if files_by_pid[pid] else None,
            "observed_gc_name": observed_gc_by_pid.get(pid),
            "pause_count": len(pauses),
            "p50_ms": percentile(pauses, 0.50),
            "p95_ms": percentile(pauses, 0.95),
            "p99_ms": percentile(pauses, 0.99),
            "max_ms": max(pauses) if pauses else None,
            "total_gc_time_ms": sum(pauses) if pauses else 0.0,
        }
    return stats


def safe_get_nested(mapping: dict, path: list[str]) -> str | int | float | None:
    current = mapping
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def normalize_jfr_class_name(raw_name: str | None) -> str | None:
    if not raw_name:
        return None
    return raw_name.replace("/", ".")


def parse_jfr_allocation_file(jfr_path: Path) -> dict | None:
    if not jfr_path.exists() or jfr_path.stat().st_size == 0:
        return None
    if jfr_path.stat().st_size > JFR_MAX_PARSE_BYTES:
        return None

    try:
        proc = subprocess.run(
            [
                "jfr",
                "print",
                "--json",
                "--events",
                "jdk.ObjectAllocationSample,jdk.ObjectAllocationInNewTLAB,jdk.ObjectAllocationOutsideTLAB",
                str(jfr_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=JFR_PARSE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None

    if proc.returncode != 0 or not proc.stdout.strip():
        return None

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None

    events = payload.get("recording", {}).get("events", [])
    if not events:
        return None

    total_bytes = 0
    top_threads: dict[str, int] = defaultdict(int)
    top_classes: dict[str, int] = defaultdict(int)
    event_counts: dict[str, int] = defaultdict(int)
    mode = None

    for event in events:
        event_type = event.get("type")
        values = event.get("values", {})
        byte_count = safe_get_nested(values, ["allocationSize"])
        if byte_count is None:
            byte_count = safe_get_nested(values, ["weight"])
        if byte_count is None:
            continue
        try:
            byte_count_int = int(byte_count)
        except (TypeError, ValueError):
            continue

        total_bytes += byte_count_int
        event_counts[str(event_type)] += 1

        if event_type == "jdk.ObjectAllocationSample":
            mode = mode or "sampled"
        else:
            mode = "exact"

        thread_name = safe_get_nested(values, ["eventThread", "javaName"]) or safe_get_nested(values, ["eventThread", "osName"])
        class_name = normalize_jfr_class_name(safe_get_nested(values, ["objectClass", "name"]))
        if thread_name:
            top_threads[str(thread_name)] += byte_count_int
        if class_name:
            top_classes[str(class_name)] += byte_count_int

    if total_bytes == 0:
        return None

    def top_items(source: dict[str, int]) -> list[dict]:
        return [
            {"name": name, "bytes": value}
            for name, value in sorted(source.items(), key=lambda item: item[1], reverse=True)[:5]
        ]

    return {
        "file": str(jfr_path),
        "mode": mode or "unknown",
        "total_allocation_bytes": total_bytes,
        "event_counts": dict(event_counts),
        "top_threads": top_items(top_threads),
        "top_classes": top_items(top_classes),
    }


def summarize_jfr(jfr_dir: Path, build_duration_seconds: float | None) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    if not PARSE_JFR_SUMMARY or not jfr_dir.exists():
        return stats

    best_by_pid: dict[str, tuple[Path, int]] = {}
    for path in sorted(jfr_dir.glob("*.jfr")):
        pid_match = re.search(r"([0-9]+)", path.name)
        if not pid_match:
            continue
        pid = pid_match.group(1)
        size = path.stat().st_size if path.exists() else 0
        current = best_by_pid.get(pid)
        if current is None or size > current[1]:
            best_by_pid[pid] = (path, size)

    for pid, (path, _) in best_by_pid.items():
        try:
            parsed = parse_jfr_allocation_file(path)
        except Exception:
            parsed = None
        if not parsed:
            continue
        if build_duration_seconds and build_duration_seconds > 0:
            parsed["allocation_rate_mb_per_s"] = parsed["total_allocation_bytes"] / (1024 * 1024) / build_duration_seconds
        else:
            parsed["allocation_rate_mb_per_s"] = None
        stats[pid] = parsed
    return stats


TASK_PATTERN = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(?P<line>> Task (?P<task>:[^\s]+)(?:\s+(?P<outcome>[A-Z-]+))?.*)$"
)
SCAN_PATTERN = re.compile(r"https://gradle\.com/s/[A-Za-z0-9]+")


def parse_task_timeline(stdout_path: Path, build_end: datetime | None) -> tuple[list[dict], str | None]:
    tasks: list[dict] = []
    scan_url = None
    if not stdout_path.exists():
        return tasks, scan_url

    current: dict | None = None
    for raw_line in stdout_path.read_text(errors="ignore").splitlines():
        scan_match = SCAN_PATTERN.search(raw_line)
        if scan_match:
            scan_url = scan_match.group(0)

        match = TASK_PATTERN.match(raw_line)
        if not match:
            continue
        ts = parse_ts(match.group("ts"))
        task_path = match.group("task")
        outcome = match.group("outcome") or "SUCCESS"
        if current and current["task_path"] != task_path:
            current["end_time"] = match.group("ts")
            tasks.append(current)
            current = None
        if current is None:
            current = {
                "task_path": task_path,
                "start_time": match.group("ts"),
                "end_time": match.group("ts"),
                "outcome": outcome,
            }
        else:
            current["end_time"] = match.group("ts")
            current["outcome"] = outcome

    if current:
        current["end_time"] = (
            build_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if build_end else current["end_time"]
        )
        tasks.append(current)
    return tasks, scan_url


def task_for_timestamp(tasks: list[dict], timestamp: str | None) -> str | None:
    ts = parse_ts(timestamp)
    if ts is None:
        return None
    for task in tasks:
        start = parse_ts(task.get("start_time"))
        end = parse_ts(task.get("end_time"))
        if start and end and start <= ts <= end:
            return task["task_path"]
    return None


def write_task_csv(path: Path, tasks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["task_path", "start_time", "end_time", "outcome"])
        writer.writeheader()
        writer.writerows(tasks)


def role_display(pid: str, pid_roles: dict[str, dict], process_summary: dict[str, dict]) -> str:
    role = pid_roles.get(pid, {}).get("role") or process_summary.get(pid, {}).get("role") or "unknown"
    return f"{role} (pid {pid})"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: summarize.py <artifacts/timestamp-dir>", file=sys.stderr)
        return 1

    artifact_dir = Path(sys.argv[1]).resolve()
    metadata = load_metadata(artifact_dir / "metadata.json")
    run_profile = load_json(artifact_dir / "run_profile.json")
    pid_roles = load_pid_roles(artifact_dir / "logs" / "os" / "discovered_pids.csv")
    process_summary, process_rows = summarize_process_metrics(artifact_dir / "logs" / "os" / "process_metrics.csv")
    gc_summary = summarize_gc(artifact_dir / "logs" / "gc")

    build_started = parse_ts(metadata.get("build_started_at"))
    build_finished = parse_ts(metadata.get("build_finished_at"))
    build_duration = None
    if build_started and build_finished:
        build_duration = (build_finished - build_started).total_seconds()
    jfr_summary = summarize_jfr(artifact_dir / "logs" / "jfr", build_duration)

    tasks, scan_url = parse_task_timeline(artifact_dir / "logs" / "gradle_stdout.log", build_finished)
    write_task_csv(artifact_dir / "gradle" / "task_timeline.csv", tasks)
    if scan_url and "develocity_build_scan" not in metadata:
        metadata["develocity_build_scan"] = scan_url
        (artifact_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")

    per_process = {}
    for pid, proc_stats in process_summary.items():
        gc_stats = gc_summary.get(pid)
        peak_task = task_for_timestamp(tasks, proc_stats.get("peak_rss_timestamp"))
        role = pid_roles.get(pid, {}).get("role") or proc_stats.get("role")
        declared_gc_name = choose_reported_gc_name(
            (run_profile.get("declared_gc_profiles") or {}).get(role or ""),
            (gc_stats or {}).get("observed_gc_name"),
        )
        per_process[pid] = {
            "role": role,
            "command": pid_roles.get(pid, {}).get("command"),
            "max_rss_kb": proc_stats.get("max_rss_kb"),
            "avg_rss_kb": proc_stats.get("avg_rss_kb"),
            "max_cpu_pct": proc_stats.get("max_cpu_pct"),
            "peak_rss_timestamp": proc_stats.get("peak_rss_timestamp"),
            "peak_task": peak_task,
            "jfr": jfr_summary.get(pid),
            "gc": {
                **(
                    gc_stats
                    or {
                        "file": None,
                        "pause_count": 0,
                        "p50_ms": None,
                        "p95_ms": None,
                        "p99_ms": None,
                        "max_ms": None,
                        "total_gc_time_ms": 0.0,
                    }
                ),
                "reported_gc_name": declared_gc_name,
            },
        }

    correlations = []
    observed_gc_profiles = {}
    reported_gc_profiles = {}
    declared_gc_profiles = {
        role: value
        for role, value in (run_profile.get("declared_gc_profiles") or {}).items()
        if value
    }
    for pid, proc in per_process.items():
        role = proc.get("role")
        observed_gc_name = proc.get("gc", {}).get("observed_gc_name")
        reported_gc_name = proc.get("gc", {}).get("reported_gc_name")
        if role and observed_gc_name and role not in observed_gc_profiles:
            observed_gc_profiles[role] = observed_gc_name
        if role and reported_gc_name and role not in reported_gc_profiles:
            reported_gc_profiles[role] = reported_gc_name
        if proc.get("peak_task"):
            correlations.append(
                {
                    "pid": pid,
                    "role": role,
                    "task": proc.get("peak_task"),
                    "peak_rss_kb": proc.get("max_rss_kb"),
                    "peak_rss_timestamp": proc.get("peak_rss_timestamp"),
                }
            )

    summary = {
        "artifact_dir": str(artifact_dir),
        "build_duration_seconds": build_duration,
        "build_exit_code": metadata.get("build_exit_code"),
        "develocity_build_scan": metadata.get("develocity_build_scan"),
        "declared_gc_profiles": declared_gc_profiles,
        "observed_gc_profiles": observed_gc_profiles,
        "reported_gc_profiles": reported_gc_profiles,
        "per_process": per_process,
        "correlated_peaks": correlations,
    }
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Build Profiling Summary",
        "",
        f"- Build duration: {build_duration:.2f}s" if build_duration is not None else "- Build duration: unavailable",
        f"- Build exit code: {metadata.get('build_exit_code', 'unknown')}",
        f"- JDK: {metadata.get('jdk_runtime') or 'unknown'}",
        f"- Gradle: {metadata.get('gradle_version') or 'unknown'}",
    ]
    if metadata.get("develocity_build_scan"):
        lines.append(f"- Build scan: {metadata['develocity_build_scan']}")
    if declared_gc_profiles:
        lines.append(
            "- Declared GC profiles: "
            + ", ".join(f"{role}={name}" for role, name in sorted(declared_gc_profiles.items()))
        )
    if reported_gc_profiles:
        lines.append(
            "- Reported collector labels: "
            + ", ".join(f"{role}={name}" for role, name in sorted(reported_gc_profiles.items()))
        )
    lines.extend(["", "## Per-process highlights", ""])

    if per_process:
        for pid, proc in sorted(per_process.items(), key=lambda item: ((item[1].get("role") or ""), item[0])):
            gc = proc["gc"]
            jfr = proc.get("jfr") or {}
            lines.append(
                (
                    f"- {role_display(pid, pid_roles, process_summary)}: "
                    f"max RSS {proc.get('max_rss_kb') or 'n/a'} kB, "
                    f"avg RSS {proc.get('avg_rss_kb') or 'n/a'} kB, "
                    f"max CPU {proc.get('max_cpu_pct') or 'n/a'}%, "
                    f"collector {gc.get('reported_gc_name') or 'n/a'}, "
                    f"runtime GC {gc.get('observed_gc_name') or 'n/a'}, "
                    f"alloc mode {jfr.get('mode') or 'n/a'}, "
                    f"alloc rate {jfr.get('allocation_rate_mb_per_s') or 'n/a'} MB/s, "
                    f"GC p95 {gc.get('p95_ms') or 'n/a'} ms, "
                    f"GC max {gc.get('max_ms') or 'n/a'} ms, "
                    f"total GC {gc.get('total_gc_time_ms') or 0.0} ms"
                )
            )
    else:
        lines.append("- No process metrics were collected.")

    lines.extend(["", "## Correlated peaks", ""])
    if correlations:
        for correlation in correlations:
            lines.append(
                f"- {correlation['role']} pid {correlation['pid']} peaked near {correlation['task']} at {correlation['peak_rss_timestamp']}"
            )
    else:
        lines.append("- No task correlation was derived.")

    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Metadata: {artifact_dir / 'metadata.json'}",
            f"- GC logs: {artifact_dir / 'logs' / 'gc'}",
            f"- JFR: {artifact_dir / 'logs' / 'jfr'}",
            f"- OS metrics: {artifact_dir / 'logs' / 'os' / 'process_metrics.csv'}",
            f"- Task timeline: {artifact_dir / 'gradle' / 'task_timeline.csv'}",
        ]
    )

    (artifact_dir / "summary.md").write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
