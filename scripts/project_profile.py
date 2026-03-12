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

ANDROID_PLUGIN_ROLES = {
    "com.android.application": "android-application",
    "com.android.library": "android-library",
    "com.android.dynamic-feature": "android-dynamic-feature",
    "com.android.test": "android-test",
}


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


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def normalize_alias(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def load_version_catalog_plugins(project_root: Path) -> dict[str, str]:
    alias_to_plugin_id: dict[str, str] = {}
    if tomllib is None:
        return alias_to_plugin_id

    for path in project_root.rglob("libs.versions.toml"):
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        try:
            payload = tomllib.loads(read_text(path))
        except Exception:
            continue
        plugins = payload.get("plugins", {})
        if not isinstance(plugins, dict):
            continue
        for alias, entry in plugins.items():
            plugin_id = None
            if isinstance(entry, str):
                plugin_id = entry
            elif isinstance(entry, dict):
                plugin_id = entry.get("id")
            if plugin_id:
                alias_to_plugin_id[normalize_alias(alias)] = plugin_id
    return alias_to_plugin_id


def read_settings_modules(project_root: Path) -> set[str]:
    module_paths: set[str] = set()
    settings_files = [project_root / "settings.gradle", project_root / "settings.gradle.kts"]
    include_pattern = re.compile(r'include\((.*?)\)|include\s+(.*)')
    for settings_file in settings_files:
        if not settings_file.exists():
            continue
        for raw_line in read_text(settings_file).splitlines():
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


def candidate_build_files(project_root: Path, module_path: str) -> list[Path]:
    module_rel = module_path.lstrip(":").replace(":", "/")
    module_root = project_root / module_rel
    return [
        module_root / "build.gradle.kts",
        module_root / "build.gradle",
    ]


def plugin_ids_from_text(text: str, alias_map: dict[str, str]) -> set[str]:
    plugin_ids: set[str] = set()

    for match in re.finditer(r'com\.android\.(?:application|library|dynamic-feature|test)', text):
        plugin_ids.add(match.group(0))

    for match in re.finditer(r'org\.jetbrains\.kotlin(?:\.[A-Za-z0-9_.-]+)?', text):
        plugin_ids.add(match.group(0))

    for match in re.finditer(r'com\.google\.devtools\.ksp', text):
        plugin_ids.add(match.group(0))

    for match in re.finditer(r'alias\s*\(\s*libs\.plugins\.([A-Za-z0-9_.-]+)\s*\)', text):
        alias = normalize_alias(match.group(1))
        plugin_id = alias_map.get(alias)
        if plugin_id:
            plugin_ids.add(plugin_id)

    for match in re.finditer(r'id\s*\(\s*"([^"]+)"\s*\)|id\s+"([^"]+)"', text):
        plugin_id = match.group(1) or match.group(2)
        if plugin_id:
            plugin_ids.add(plugin_id)

    return plugin_ids


def classify_module(plugin_ids: set[str]) -> str:
    for plugin_id, role in ANDROID_PLUGIN_ROLES.items():
        if plugin_id in plugin_ids:
            return role
    if plugin_ids:
        return "non-android"
    return "unknown"


def collect_module_roles(project_root: Path, modules: set[str], alias_map: dict[str, str]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for module in sorted(modules):
        plugin_ids: set[str] = set()
        for path in candidate_build_files(project_root, module):
            if path.exists():
                plugin_ids.update(plugin_ids_from_text(read_text(path), alias_map))
        roles[module] = classify_module(plugin_ids)
    return roles


def collect_repo_signals(project_root: Path, alias_map: dict[str, str]) -> dict[str, bool]:
    uses_ksp = False
    uses_kapt = False
    uses_compose = False

    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.name not in {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle.properties", "libs.versions.toml"}:
            continue
        text = read_text(path)
        plugin_ids = plugin_ids_from_text(text, alias_map)
        if "com.google.devtools.ksp" in plugin_ids or re.search(r"\bksp\b", text):
            uses_ksp = True
        if "org.jetbrains.kotlin.kapt" in plugin_ids or re.search(r"\bkapt\b", text):
            uses_kapt = True
        if re.search(r"compose\s*=\s*true|buildFeatures\s*\{[^}]*compose|androidx\.compose", text, re.DOTALL):
            uses_compose = True
    return {
        "uses_ksp": uses_ksp,
        "uses_kapt": uses_kapt,
        "uses_compose": uses_compose,
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
    alias_map = load_version_catalog_plugins(project_root)
    module_roles = collect_module_roles(project_root, modules, alias_map)
    repo_signals = collect_repo_signals(project_root, alias_map)

    role_counts: dict[str, int] = {}
    for role in module_roles.values():
        role_counts[role] = role_counts.get(role, 0) + 1

    android_module_count = sum(
        count for role, count in role_counts.items() if role.startswith("android-")
    )

    profile = {
        "project_root": str(project_root),
        "settings_declared_modules": sorted(modules),
        "module_count": len(modules),
        "module_role_counts": role_counts,
        "module_roles": module_roles,
        "android_module_count": android_module_count,
        "non_android_module_count": role_counts.get("non-android", 0),
        "android_application_module_hits": role_counts.get("android-application", 0),
        "android_library_module_hits": role_counts.get("android-library", 0),
        "android_dynamic_feature_module_hits": role_counts.get("android-dynamic-feature", 0),
        "android_test_module_hits": role_counts.get("android-test", 0),
        "unknown_module_count": role_counts.get("unknown", 0),
        "kotlin_source_file_count": len(kotlin_files),
        "java_source_file_count": len(java_files),
        "uses_ksp": repo_signals["uses_ksp"],
        "uses_kapt": repo_signals["uses_kapt"],
        "uses_compose": repo_signals["uses_compose"],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
