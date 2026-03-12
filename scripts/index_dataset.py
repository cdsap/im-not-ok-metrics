#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def collect_run_dirs(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in root.rglob("summary.json"):
        candidates.append(path.parent)
    return sorted(set(candidates))


def normalize_entry(run_dir: Path) -> dict:
    metadata = load_json(run_dir / "metadata.json")
    summary = load_json(run_dir / "summary.json")
    project_profile = load_json(run_dir / "project_profile.json")
    run_profile = load_json(run_dir / "run_profile.json")
    declared_gc_profiles = run_profile.get("declared_gc_profiles") or {}

    entry = {
        "run_dir": str(run_dir),
        "run_id": run_dir.name,
        "repo_name": metadata.get("repo_name"),
        "git_sha": metadata.get("git_sha"),
        "build_command": metadata.get("full_command"),
        "jdk_runtime": metadata.get("jdk_runtime"),
        "jdk_vendor": metadata.get("jdk_vendor"),
        "gradle_version": metadata.get("gradle_version"),
        "agp_version": metadata.get("agp_version"),
        "kotlin_version": metadata.get("kotlin_version"),
        "build_duration_seconds": summary.get("build_duration_seconds"),
        "build_exit_code": metadata.get("build_exit_code"),
        "deep_mode": metadata.get("deep_mode"),
        "project_slug": run_profile.get("project_slug"),
        "configuration_slug": run_profile.get("configuration_slug"),
        "run_kind": run_profile.get("run_kind"),
        "iteration": run_profile.get("iteration"),
        "runner_os": run_profile.get("runner_os"),
        "runner_vcpus": run_profile.get("runner_vcpus"),
        "runner_memory_gb": run_profile.get("runner_memory_gb"),
        "declared_gc_gradle_daemon": declared_gc_profiles.get("gradle-daemon"),
        "declared_gc_kotlin_daemon": declared_gc_profiles.get("kotlin-daemon"),
        "declared_gc_test_jvm": declared_gc_profiles.get("test-jvm"),
        "module_count": project_profile.get("module_count"),
        "kotlin_source_file_count": project_profile.get("kotlin_source_file_count"),
        "java_source_file_count": project_profile.get("java_source_file_count"),
        "uses_ksp": project_profile.get("uses_ksp"),
        "uses_kapt": project_profile.get("uses_kapt"),
        "uses_compose": project_profile.get("uses_compose"),
    }

    per_process = summary.get("per_process", {})
    for pid, proc in per_process.items():
        role = proc.get("role")
        if role == "gradle-daemon":
            entry["gradle_daemon_max_rss_kb"] = proc.get("max_rss_kb")
            entry["gradle_daemon_gc_p95_ms"] = proc.get("gc", {}).get("p95_ms")
            entry["gradle_daemon_gc_max_ms"] = proc.get("gc", {}).get("max_ms")
            entry["observed_gc_gradle_daemon"] = proc.get("gc", {}).get("observed_gc_name")
            entry["gradle_daemon_alloc_mode"] = proc.get("jfr", {}).get("mode")
            entry["gradle_daemon_alloc_rate_mb_per_s"] = proc.get("jfr", {}).get("allocation_rate_mb_per_s")
        if role == "kotlin-daemon":
            entry["kotlin_daemon_max_rss_kb"] = proc.get("max_rss_kb")
            entry["kotlin_daemon_gc_p95_ms"] = proc.get("gc", {}).get("p95_ms")
            entry["kotlin_daemon_gc_max_ms"] = proc.get("gc", {}).get("max_ms")
            entry["observed_gc_kotlin_daemon"] = proc.get("gc", {}).get("observed_gc_name")
            entry["kotlin_daemon_alloc_mode"] = proc.get("jfr", {}).get("mode")
            entry["kotlin_daemon_alloc_rate_mb_per_s"] = proc.get("jfr", {}).get("allocation_rate_mb_per_s")
    return entry


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print("Usage: index_dataset.py <artifacts-root> [output-jsonl]", file=sys.stderr)
        return 1

    root = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else root / "dataset_index.jsonl"

    entries = [normalize_entry(run_dir) for run_dir in collect_run_dirs(root)]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
