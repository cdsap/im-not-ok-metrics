#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def safe_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def slugify(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value).strip("-") or "value"


def collect_corpus_runs(downloads_root: Path) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for github_run_dir in sorted(path for path in downloads_root.iterdir() if path.is_dir()):
        for summary_path in github_run_dir.rglob("summary.json"):
            corpus_run_dir = summary_path.parent
            pairs.append((github_run_dir, corpus_run_dir))
    return sorted(set(pairs))


def summarize_roles(summary: dict) -> tuple[dict[str, dict], Counter]:
    role_totals: dict[str, dict] = defaultdict(
        lambda: {
            "process_count": 0,
            "max_rss_kb_peak": None,
            "avg_rss_kb_peak": None,
            "max_cpu_pct_peak": None,
            "gc_pause_count_sum": 0,
            "gc_total_time_ms_sum": 0.0,
            "gc_p95_ms_peak": None,
            "gc_max_ms_peak": None,
            "jfr_allocation_bytes_sum": 0.0,
            "jfr_allocation_rate_mb_per_s_peak": None,
            "observed_gc_names": Counter(),
            "peak_task_examples": [],
        }
    )
    role_process_counts: Counter = Counter()

    for process in (summary.get("per_process") or {}).values():
        role = process.get("role") or "unknown"
        role_process_counts[role] += 1
        role_total = role_totals[role]
        role_total["process_count"] += 1

        for field, key in (
            ("max_rss_kb_peak", "max_rss_kb"),
            ("avg_rss_kb_peak", "avg_rss_kb"),
            ("max_cpu_pct_peak", "max_cpu_pct"),
        ):
            value = safe_float(process.get(key))
            if value is not None and (role_total[field] is None or value > role_total[field]):
                role_total[field] = value

        gc = process.get("gc") or {}
        role_total["gc_pause_count_sum"] += safe_int(gc.get("pause_count")) or 0
        role_total["gc_total_time_ms_sum"] += safe_float(gc.get("total_gc_time_ms")) or 0.0
        for field, key in (("gc_p95_ms_peak", "p95_ms"), ("gc_max_ms_peak", "max_ms")):
            value = safe_float(gc.get(key))
            if value is not None and (role_total[field] is None or value > role_total[field]):
                role_total[field] = value
        observed_gc_name = gc.get("observed_gc_name")
        if observed_gc_name:
            role_total["observed_gc_names"][observed_gc_name] += 1

        jfr = process.get("jfr") or {}
        role_total["jfr_allocation_bytes_sum"] += safe_float(jfr.get("total_allocation_bytes")) or 0.0
        allocation_rate = safe_float(jfr.get("allocation_rate_mb_per_s"))
        if allocation_rate is not None and (
            role_total["jfr_allocation_rate_mb_per_s_peak"] is None
            or allocation_rate > role_total["jfr_allocation_rate_mb_per_s_peak"]
        ):
            role_total["jfr_allocation_rate_mb_per_s_peak"] = allocation_rate

        peak_task = process.get("peak_task")
        if peak_task and peak_task not in role_total["peak_task_examples"] and len(role_total["peak_task_examples"]) < 3:
            role_total["peak_task_examples"].append(peak_task)

    normalized = {}
    for role, data in role_totals.items():
        normalized[role] = dict(data)
        normalized[role]["observed_gc_names"] = dict(data["observed_gc_names"])
    return normalized, role_process_counts


def detect_workload_signature(entry: dict) -> str:
    gradle_rss = safe_float(entry.get("gradle_daemon_max_rss_kb_peak")) or 0.0
    kotlin_rss = safe_float(entry.get("kotlin_daemon_max_rss_kb_peak")) or 0.0
    gradle_gc = safe_float(entry.get("gradle_daemon_gc_total_time_ms_sum")) or 0.0
    kotlin_gc = safe_float(entry.get("kotlin_daemon_gc_total_time_ms_sum")) or 0.0
    kotlin_processes = safe_int(entry.get("kotlin_daemon_process_count")) or 0

    if kotlin_processes >= 2 and kotlin_rss >= gradle_rss * 0.75:
        return "multi-kotlin-pressure"
    if gradle_rss >= kotlin_rss * 1.25 and gradle_gc >= kotlin_gc * 1.25:
        return "gradle-daemon-dominant"
    if kotlin_rss >= gradle_rss * 1.25 and kotlin_gc >= gradle_gc * 1.25:
        return "kotlin-daemon-dominant"
    if gradle_rss and kotlin_rss:
        return "mixed-pressure"
    return "partial-signal"


def normalize_entry(github_run_dir: Path, corpus_run_dir: Path) -> dict:
    metadata = load_json(corpus_run_dir / "metadata.json")
    summary = load_json(corpus_run_dir / "summary.json")
    project_profile = load_json(corpus_run_dir / "project_profile.json")
    run_profile = load_json(corpus_run_dir / "run_profile.json")
    github_run = load_json(github_run_dir / "run.json")

    artifact_bundle_dir = corpus_run_dir.parent
    role_totals, role_process_counts = summarize_roles(summary)
    declared_gc_profiles = run_profile.get("declared_gc_profiles") or {}
    observed_gc_profiles = summary.get("observed_gc_profiles") or {}
    reported_gc_profiles = summary.get("reported_gc_profiles") or {}

    entry = {
        "github_run_dir": str(github_run_dir),
        "github_run_id": github_run.get("run_id"),
        "github_workflow_name": github_run.get("workflow_name"),
        "github_run_created_at": github_run.get("created_at"),
        "github_display_title": github_run.get("display_title"),
        "artifact_bundle_dir": str(artifact_bundle_dir),
        "artifact_bundle_name": artifact_bundle_dir.name,
        "run_dir": str(corpus_run_dir),
        "captured_run_id": corpus_run_dir.name,
        "repo_name": metadata.get("repo_name"),
        "git_sha": metadata.get("git_sha"),
        "build_command": run_profile.get("full_command") or metadata.get("full_command"),
        "jdk_runtime": metadata.get("jdk_runtime"),
        "jdk_vendor": metadata.get("jdk_vendor"),
        "gradle_version": metadata.get("gradle_version"),
        "agp_version": metadata.get("agp_version"),
        "kotlin_version": metadata.get("kotlin_version"),
        "build_duration_seconds": safe_float(summary.get("build_duration_seconds")),
        "build_exit_code": safe_int(summary.get("build_exit_code", metadata.get("build_exit_code"))),
        "deep_mode": bool(run_profile.get("deep_mode") if run_profile else metadata.get("deep_mode")),
        "project_slug": run_profile.get("project_slug"),
        "configuration_slug": run_profile.get("configuration_slug"),
        "run_kind": run_profile.get("run_kind"),
        "iteration": safe_int(run_profile.get("iteration")),
        "runner_os": run_profile.get("runner_os"),
        "runner_vcpus": safe_int(run_profile.get("runner_vcpus")),
        "runner_memory_gb": safe_float(run_profile.get("runner_memory_gb")),
        "declared_gc_gradle_daemon": declared_gc_profiles.get("gradle-daemon"),
        "declared_gc_kotlin_daemon": declared_gc_profiles.get("kotlin-daemon"),
        "declared_gc_test_jvm": declared_gc_profiles.get("test-jvm"),
        "observed_gc_gradle_daemon": observed_gc_profiles.get("gradle-daemon"),
        "observed_gc_kotlin_daemon": observed_gc_profiles.get("kotlin-daemon"),
        "reported_gc_gradle_daemon": reported_gc_profiles.get("gradle-daemon"),
        "reported_gc_kotlin_daemon": reported_gc_profiles.get("kotlin-daemon"),
        "reported_gc_test_jvm": reported_gc_profiles.get("test-jvm"),
        "module_count": safe_int(project_profile.get("module_count")),
        "android_module_count": safe_int(project_profile.get("android_module_count")),
        "non_android_module_count": safe_int(project_profile.get("non_android_module_count")),
        "android_application_module_hits": safe_int(project_profile.get("android_application_module_hits")),
        "android_library_module_hits": safe_int(project_profile.get("android_library_module_hits")),
        "android_dynamic_feature_module_hits": safe_int(project_profile.get("android_dynamic_feature_module_hits")),
        "android_test_module_hits": safe_int(project_profile.get("android_test_module_hits")),
        "unknown_module_count": safe_int(project_profile.get("unknown_module_count")),
        "kotlin_source_file_count": safe_int(project_profile.get("kotlin_source_file_count")),
        "java_source_file_count": safe_int(project_profile.get("java_source_file_count")),
        "uses_ksp": bool(project_profile.get("uses_ksp")),
        "uses_kapt": bool(project_profile.get("uses_kapt")),
        "uses_compose": bool(project_profile.get("uses_compose")),
        "correlated_peak_count": len(summary.get("correlated_peaks") or []),
        "correlated_peaks": summary.get("correlated_peaks") or [],
        "role_totals": role_totals,
        "role_process_counts": dict(role_process_counts),
    }

    for role_key, role_name in (
        ("gradle_daemon", "gradle-daemon"),
        ("kotlin_daemon", "kotlin-daemon"),
        ("test_jvm", "test-jvm"),
    ):
        role_data = role_totals.get(role_name) or {}
        entry[f"{role_key}_process_count"] = safe_int(role_data.get("process_count"))
        entry[f"{role_key}_max_rss_kb_peak"] = safe_float(role_data.get("max_rss_kb_peak"))
        entry[f"{role_key}_avg_rss_kb_peak"] = safe_float(role_data.get("avg_rss_kb_peak"))
        entry[f"{role_key}_max_cpu_pct_peak"] = safe_float(role_data.get("max_cpu_pct_peak"))
        entry[f"{role_key}_gc_pause_count_sum"] = safe_int(role_data.get("gc_pause_count_sum"))
        entry[f"{role_key}_gc_total_time_ms_sum"] = safe_float(role_data.get("gc_total_time_ms_sum"))
        entry[f"{role_key}_gc_p95_ms_peak"] = safe_float(role_data.get("gc_p95_ms_peak"))
        entry[f"{role_key}_gc_max_ms_peak"] = safe_float(role_data.get("gc_max_ms_peak"))
        entry[f"{role_key}_jfr_allocation_bytes_sum"] = safe_float(role_data.get("jfr_allocation_bytes_sum"))
        entry[f"{role_key}_jfr_allocation_rate_mb_per_s_peak"] = safe_float(
            role_data.get("jfr_allocation_rate_mb_per_s_peak")
        )
        entry[f"{role_key}_peak_task_examples"] = role_data.get("peak_task_examples") or []
        observed_names = role_data.get("observed_gc_names") or {}
        entry[f"{role_key}_observed_gc_names"] = observed_names

    entry["workload_key"] = "|".join(
        [
            entry.get("project_slug") or "unknown-project",
            entry.get("configuration_slug") or "unknown-config",
            entry.get("run_kind") or "unknown-kind",
        ]
    )
    entry["workload_signature"] = detect_workload_signature(entry)
    return entry


def pct(values: list[float], fraction: float):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize_entries(entries: list[dict]) -> dict:
    successful = [entry for entry in entries if entry.get("build_exit_code") == 0]
    by_workload: dict[str, list[dict]] = defaultdict(list)
    by_project: dict[str, list[dict]] = defaultdict(list)
    signature_counts: Counter = Counter()
    observed_gc_by_role: dict[str, Counter] = defaultdict(Counter)
    workflow_counts: Counter = Counter()

    for entry in entries:
        by_workload[entry["workload_key"]].append(entry)
        by_project[entry.get("project_slug") or "unknown-project"].append(entry)
        signature_counts[entry.get("workload_signature") or "unknown"] += 1
        workflow_counts[entry.get("github_workflow_name") or "unknown"] += 1
        for role in ("gradle_daemon", "kotlin_daemon", "test_jvm"):
            for gc_name, count in (entry.get(f"{role}_observed_gc_names") or {}).items():
                observed_gc_by_role[role][gc_name] += count

    workload_summaries = []
    for workload_key, workload_entries in sorted(by_workload.items()):
        durations = [entry["build_duration_seconds"] for entry in workload_entries if entry.get("build_duration_seconds") is not None]
        gradle_rss = [entry["gradle_daemon_max_rss_kb_peak"] for entry in workload_entries if entry.get("gradle_daemon_max_rss_kb_peak") is not None]
        kotlin_rss = [entry["kotlin_daemon_max_rss_kb_peak"] for entry in workload_entries if entry.get("kotlin_daemon_max_rss_kb_peak") is not None]
        gradle_gc_p95 = [entry["gradle_daemon_gc_p95_ms_peak"] for entry in workload_entries if entry.get("gradle_daemon_gc_p95_ms_peak") is not None]
        kotlin_gc_p95 = [entry["kotlin_daemon_gc_p95_ms_peak"] for entry in workload_entries if entry.get("kotlin_daemon_gc_p95_ms_peak") is not None]
        gradle_gc_total = [entry["gradle_daemon_gc_total_time_ms_sum"] for entry in workload_entries if entry.get("gradle_daemon_gc_total_time_ms_sum") is not None]
        kotlin_gc_total = [entry["kotlin_daemon_gc_total_time_ms_sum"] for entry in workload_entries if entry.get("kotlin_daemon_gc_total_time_ms_sum") is not None]
        signatures = Counter(entry.get("workload_signature") or "unknown" for entry in workload_entries)
        first = workload_entries[0]
        workload_summaries.append(
            {
                "workload_key": workload_key,
                "project_slug": first.get("project_slug"),
                "configuration_slug": first.get("configuration_slug"),
                "run_kind": first.get("run_kind"),
                "sample_count": len(workload_entries),
                "successful_runs": sum(1 for entry in workload_entries if entry.get("build_exit_code") == 0),
                "deep_mode": first.get("deep_mode"),
                "median_build_duration_seconds": median(durations) if durations else None,
                "p95_build_duration_seconds": pct(durations, 0.95),
                "median_gradle_daemon_max_rss_kb": median(gradle_rss) if gradle_rss else None,
                "p95_gradle_daemon_max_rss_kb": pct(gradle_rss, 0.95),
                "median_kotlin_daemon_max_rss_kb": median(kotlin_rss) if kotlin_rss else None,
                "p95_kotlin_daemon_max_rss_kb": pct(kotlin_rss, 0.95),
                "median_gradle_daemon_gc_p95_ms": median(gradle_gc_p95) if gradle_gc_p95 else None,
                "p95_gradle_daemon_gc_p95_ms": pct(gradle_gc_p95, 0.95),
                "median_kotlin_daemon_gc_p95_ms": median(kotlin_gc_p95) if kotlin_gc_p95 else None,
                "p95_kotlin_daemon_gc_p95_ms": pct(kotlin_gc_p95, 0.95),
                "median_gradle_daemon_gc_total_time_ms": median(gradle_gc_total) if gradle_gc_total else None,
                "median_kotlin_daemon_gc_total_time_ms": median(kotlin_gc_total) if kotlin_gc_total else None,
                "dominant_signature": signatures.most_common(1)[0][0] if signatures else None,
                "signatures": dict(signatures),
                "observed_gc_gradle_daemon": dict(
                    Counter(entry.get("observed_gc_gradle_daemon") for entry in workload_entries if entry.get("observed_gc_gradle_daemon"))
                ),
                "observed_gc_kotlin_daemon": dict(
                    Counter(entry.get("observed_gc_kotlin_daemon") for entry in workload_entries if entry.get("observed_gc_kotlin_daemon"))
                ),
            }
        )

    workload_summaries.sort(
        key=lambda item: (
            -(item.get("median_gradle_daemon_max_rss_kb") or 0),
            -(item.get("median_kotlin_daemon_max_rss_kb") or 0),
            item["workload_key"],
        )
    )

    project_summaries = []
    for project_slug, project_entries in sorted(by_project.items()):
        durations = [entry["build_duration_seconds"] for entry in project_entries if entry.get("build_duration_seconds") is not None]
        project_summaries.append(
            {
                "project_slug": project_slug,
                "sample_count": len(project_entries),
                "configuration_count": len({entry.get("configuration_slug") for entry in project_entries}),
                "run_kind_count": len({entry.get("run_kind") for entry in project_entries}),
                "median_build_duration_seconds": median(durations) if durations else None,
                "max_module_count": max((entry.get("module_count") or 0) for entry in project_entries),
                "max_kotlin_source_file_count": max((entry.get("kotlin_source_file_count") or 0) for entry in project_entries),
                "uses_ksp": any(entry.get("uses_ksp") for entry in project_entries),
                "uses_kapt": any(entry.get("uses_kapt") for entry in project_entries),
                "uses_compose": any(entry.get("uses_compose") for entry in project_entries),
            }
        )

    hottest_gradle = sorted(
        (entry for entry in entries if entry.get("gradle_daemon_max_rss_kb_peak") is not None),
        key=lambda entry: entry["gradle_daemon_max_rss_kb_peak"],
        reverse=True,
    )[:10]
    hottest_kotlin = sorted(
        (entry for entry in entries if entry.get("kotlin_daemon_max_rss_kb_peak") is not None),
        key=lambda entry: entry["kotlin_daemon_max_rss_kb_peak"],
        reverse=True,
    )[:10]

    return {
        "downloads_root": None,
        "github_run_count": len({entry.get("github_run_id") for entry in entries}),
        "captured_run_count": len(entries),
        "successful_run_count": len(successful),
        "project_count": len(by_project),
        "workload_count": len(by_workload),
        "workflow_counts": dict(workflow_counts),
        "workload_signature_counts": dict(signature_counts),
        "observed_gc_by_role": {role: dict(counter) for role, counter in observed_gc_by_role.items()},
        "project_summaries": project_summaries,
        "workload_summaries": workload_summaries,
        "top_gradle_daemon_rss_runs": hottest_gradle,
        "top_kotlin_daemon_rss_runs": hottest_kotlin,
    }


def markdown_summary(summary: dict) -> str:
    def fmt_float(value, digits: int = 1) -> str:
      if value is None:
          return "n/a"
      return f"{value:.{digits}f}"

    def fmt_int(value) -> str:
      if value is None:
          return "n/a"
      return str(int(value))

    lines = [
        "# Downloaded Corpus Summary",
        "",
        "## Scope",
        f"- GitHub workflow runs: {summary['github_run_count']}",
        f"- Captured workload runs: {summary['captured_run_count']}",
        f"- Successful runs: {summary['successful_run_count']}",
        f"- Projects: {summary['project_count']}",
        f"- Workload shapes: {summary['workload_count']}",
        "",
        "## Workflow Mix",
    ]
    for workflow_name, count in sorted(summary["workflow_counts"].items()):
        lines.append(f"- {workflow_name}: {count}")

    lines.extend(["", "## Workload Signatures"])
    for signature, count in sorted(summary["workload_signature_counts"].items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {signature}: {count}")

    lines.extend(["", "## Observed GC By Role"])
    for role, gc_counts in sorted(summary["observed_gc_by_role"].items()):
        rendered = ", ".join(f"{name}={count}" for name, count in sorted(gc_counts.items())) or "none"
        lines.append(f"- {role}: {rendered}")

    lines.extend(["", "## Highest-Pressure Workloads"])
    for item in summary["workload_summaries"][:10]:
        lines.append(
            "- "
            f"{item['project_slug']} | {item['configuration_slug']} | {item['run_kind']} | "
            f"samples={item['sample_count']} | "
            f"median_build_s={fmt_float(item['median_build_duration_seconds'])}"
        )
        lines.append(
            f"  median_gradle_rss_kb={fmt_int(item['median_gradle_daemon_max_rss_kb'])} | "
            f"median_kotlin_rss_kb={fmt_int(item['median_kotlin_daemon_max_rss_kb'])} | "
            f"signature={item['dominant_signature']}"
        )

    lines.extend(["", "## Top Gradle Daemon RSS Runs"])
    for entry in summary["top_gradle_daemon_rss_runs"][:10]:
        lines.append(
            "- "
            f"{entry['project_slug']} | {entry['configuration_slug']} | iter={entry['iteration']} | "
            f"rss_kb={fmt_int(entry['gradle_daemon_max_rss_kb_peak'])} | "
            f"gc_total_ms={fmt_float(entry['gradle_daemon_gc_total_time_ms_sum'])} | "
            f"run={entry['captured_run_id']}"
        )

    lines.extend(["", "## Top Kotlin Daemon RSS Runs"])
    for entry in summary["top_kotlin_daemon_rss_runs"][:10]:
        lines.append(
            "- "
            f"{entry['project_slug']} | {entry['configuration_slug']} | iter={entry['iteration']} | "
            f"rss_kb={fmt_int(entry['kotlin_daemon_max_rss_kb_peak'])} | "
            f"gc_total_ms={fmt_float(entry['kotlin_daemon_gc_total_time_ms_sum'])} | "
            f"run={entry['captured_run_id']}"
        )

    return "\n".join(lines) + "\n"


def write_jsonl(entries: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print("Usage: index_downloaded_corpus.py <downloads-root> [output-prefix]", file=sys.stderr)
        return 1

    downloads_root = Path(sys.argv[1]).resolve()
    output_prefix = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else downloads_root / "corpus"

    entries = []
    for github_run_dir, corpus_run_dir in collect_corpus_runs(downloads_root):
        try:
            entries.append(normalize_entry(github_run_dir, corpus_run_dir))
        except Exception as exc:
            print(f"warning: skipping {corpus_run_dir}: {exc}", file=sys.stderr)

    summary = summarize_entries(entries)
    summary["downloads_root"] = str(downloads_root)

    write_jsonl(entries, output_prefix.with_suffix(".jsonl"))
    output_prefix.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_prefix.with_suffix(".summary.md").write_text(markdown_summary(summary), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
