#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


IGNORE_DIRS = {
    ".git",
    ".gradle",
    ".idea",
    ".kotlin",
    "build",
    "out",
    "node_modules",
}

ANDROID_PLUGIN_IDS = {
    "com.android.application",
    "com.android.library",
    "com.android.dynamic-feature",
    "com.android.test",
}


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def iter_candidate_files(project_root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.name in {
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
            "gradle.properties",
            "libs.versions.toml",
        }:
            paths.append(path)
    return paths


def version_from_catalogs(project_root: Path) -> tuple[str | None, str | None]:
    agp_version = None
    kotlin_version = None
    if tomllib is None:
        return agp_version, kotlin_version

    for path in project_root.rglob("libs.versions.toml"):
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        try:
            payload = tomllib.loads(read_text(path))
        except Exception:
            continue

        versions = payload.get("versions", {})
        plugins = payload.get("plugins", {})
        if not isinstance(versions, dict) or not isinstance(plugins, dict):
            continue

        for plugin_id in ANDROID_PLUGIN_IDS:
            if agp_version:
                break
            for entry in plugins.values():
                if not isinstance(entry, dict) or entry.get("id") != plugin_id:
                    continue
                version = entry.get("version")
                if isinstance(version, str):
                    agp_version = version
                elif isinstance(version, dict):
                    ref = version.get("ref")
                    if ref and isinstance(versions.get(ref), str):
                        agp_version = versions[ref]

        for entry in plugins.values():
            if kotlin_version:
                break
            if not isinstance(entry, dict):
                continue
            plugin_id = entry.get("id")
            if not isinstance(plugin_id, str) or not plugin_id.startswith("org.jetbrains.kotlin"):
                continue
            version = entry.get("version")
            if isinstance(version, str):
                kotlin_version = version
            elif isinstance(version, dict):
                ref = version.get("ref")
                if ref and isinstance(versions.get(ref), str):
                    kotlin_version = versions[ref]

    return agp_version, kotlin_version


def version_from_scripts(project_root: Path) -> tuple[str | None, str | None]:
    agp_version = None
    kotlin_version = None
    files = iter_candidate_files(project_root)

    agp_patterns = [
        re.compile(r'com\.android\.tools\.build:gradle:([^"\s]+)'),
        re.compile(r'id\s*\(\s*"com\.android\.(?:application|library|dynamic-feature|test)"\s*\)\s*version\s*"([^"]+)"'),
        re.compile(r'id\s+"com\.android\.(?:application|library|dynamic-feature|test)"\s+version\s+"([^"]+)"'),
    ]
    kotlin_patterns = [
        re.compile(r'org\.jetbrains\.kotlin:[^:"\s]+:([^"\s]+)'),
        re.compile(r'id\s*\(\s*"org\.jetbrains\.kotlin(?:\.[^"]+)?"\s*\)\s*version\s*"([^"]+)"'),
        re.compile(r'id\s+"org\.jetbrains\.kotlin(?:\.[^"]+)?"\s+version\s+"([^"]+)"'),
    ]

    for path in files:
        text = read_text(path)
        if not agp_version:
            for pattern in agp_patterns:
                match = pattern.search(text)
                if match:
                    agp_version = match.group(1)
                    break
        if not kotlin_version:
            for pattern in kotlin_patterns:
                match = pattern.search(text)
                if match:
                    kotlin_version = match.group(1)
                    break
        if agp_version and kotlin_version:
            break

    return agp_version, kotlin_version


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: detect_build_versions.py <project-root>", file=sys.stderr)
        return 1

    project_root = Path(sys.argv[1]).resolve()
    agp_version, kotlin_version = version_from_catalogs(project_root)
    script_agp, script_kotlin = version_from_scripts(project_root)

    payload = {
        "agp_version": agp_version or script_agp,
        "kotlin_version": kotlin_version or script_kotlin,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
