#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


IGNORE_DIRS = {
    ".git",
    ".gradle",
    ".idea",
    ".kotlin",
    "build",
    "out",
    "node_modules",
}


def run_rg(pattern: str, root: Path, globs: list[str]) -> list[str]:
    cmd = ["rg", "-n", "--no-heading"]
    for glob in globs:
        cmd.extend(["--glob", glob])
    cmd.extend([pattern, str(root)])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return []
    if proc.returncode not in (0, 1):
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]


def iter_source_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    results: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.suffix in suffixes:
            results.append(path)
    return results


def read_settings_modules(project_root: Path) -> set[str]:
    module_paths: set[str] = set()
    settings_files = [project_root / "settings.gradle", project_root / "settings.gradle.kts"]
    include_pattern = re.compile(r'include\((.*?)\)|include\s+(.*)')
    for settings_file in settings_files:
        if not settings_file.exists():
            continue
        for raw_line in settings_file.read_text(errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue
            match = include_pattern.search(line)
            if not match:
                continue
            payload = match.group(1) or match.group(2) or ""
            for token in re.findall(r'["\'](:[^"\']+)["\']', payload):
                module_paths.add(token)
    return module_paths


def collect_plugin_stats(project_root: Path) -> dict[str, int | bool]:
    gradle_globs = ["*.gradle", "*.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle.properties"]
    android_app_matches = run_rg(r'com\.android\.application', project_root, gradle_globs)
    android_lib_matches = run_rg(r'com\.android\.library', project_root, gradle_globs)
    ksp_matches = run_rg(r'\bksp\b|com\.google\.devtools\.ksp', project_root, gradle_globs)
    kapt_matches = run_rg(r'\bkapt\b|org\.jetbrains\.kotlin\.kapt', project_root, gradle_globs)
    compose_matches = run_rg(r'compose\s*=\s*true|buildFeatures\s*\{[^}]*compose|androidx\.compose', project_root, gradle_globs)
    return {
        "android_application_module_hits": len(android_app_matches),
        "android_library_module_hits": len(android_lib_matches),
        "uses_ksp": bool(ksp_matches),
        "uses_kapt": bool(kapt_matches),
        "uses_compose": bool(compose_matches),
    }


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: project_profile.py <project-root> <output-json>", file=sys.stderr)
        return 1

    project_root = Path(sys.argv[1]).resolve()
    output_path = Path(sys.argv[2]).resolve()

    kotlin_files = iter_source_files(project_root, (".kt", ".kts"))
    java_files = iter_source_files(project_root, (".java",))
    modules = read_settings_modules(project_root)
    plugin_stats = collect_plugin_stats(project_root)

    profile = {
        "project_root": str(project_root),
        "settings_declared_modules": sorted(modules),
        "module_count": len(modules),
        "android_application_module_hits": plugin_stats["android_application_module_hits"],
        "android_library_module_hits": plugin_stats["android_library_module_hits"],
        "kotlin_source_file_count": len(kotlin_files),
        "java_source_file_count": len(java_files),
        "uses_ksp": plugin_stats["uses_ksp"],
        "uses_kapt": plugin_stats["uses_kapt"],
        "uses_compose": plugin_stats["uses_compose"],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
