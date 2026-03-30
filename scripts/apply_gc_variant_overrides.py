#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


GC_FLAG_PATTERN = re.compile(
    r"(?:^|\s)-XX:(?:\+|-)?Use(?:ParallelGC|G1GC|SerialGC|ZGC|ShenandoahGC|EpsilonGC|ImNotOkGC|ImNotOkayGC)(?=\s|$)"
)
EXPERIMENTAL_FLAG_PATTERN = re.compile(r"(?:^|\s)-XX:\+UnlockExperimentalVMOptions(?=\s|$)")
GC_FLAG_TOKEN_PATTERN = re.compile(
    r"-?XX:(?:\+|-)?Use(?:ParallelGC|G1GC|SerialGC|ZGC|ShenandoahGC|EpsilonGC|ImNotOkGC|ImNotOkayGC)$"
)
EXPERIMENTAL_FLAG_TOKEN_PATTERN = re.compile(r"-?XX:\+UnlockExperimentalVMOptions$")

IMNOTOKAY_FLAGS = "-XX:+UnlockExperimentalVMOptions -XX:+UseImNotOkayGC"
G1_FLAGS = "-XX:+UseG1GC"
TARGET_PROPERTIES = ("org.gradle.jvmargs", "kotlin.daemon.jvmargs")
KOTLIN_DAEMON_OPTIONS_PREFIX = "-Dkotlin.daemon.jvm.options="
EMBEDDED_IMNOTOKAY_FLAGS = "XX:+UnlockExperimentalVMOptions,XX:+UseImNotOkayGC"
EMBEDDED_G1_FLAGS = "XX:+UseG1GC"


def strip_gc_flags(value: str) -> str:
    cleaned = GC_FLAG_PATTERN.sub(" ", value)
    cleaned = EXPERIMENTAL_FLAG_PATTERN.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_value(value: str, profile: str) -> str:
    cleaned = strip_gc_flags(value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if profile == "imnotokay":
        if IMNOTOKAY_FLAGS not in cleaned:
            cleaned = f"{cleaned} {IMNOTOKAY_FLAGS}".strip()
    elif profile == "openjdk-default":
        if G1_FLAGS not in cleaned:
            cleaned = f"{cleaned} {G1_FLAGS}".strip()
    return cleaned


def is_gc_flag_token(token: str) -> bool:
    return GC_FLAG_TOKEN_PATTERN.fullmatch(token) is not None or EXPERIMENTAL_FLAG_TOKEN_PATTERN.fullmatch(token) is not None


def profile_flags(profile: str, embedded: bool = False) -> list[str]:
    if profile == "imnotokay":
        return EMBEDDED_IMNOTOKAY_FLAGS.split(",") if embedded else IMNOTOKAY_FLAGS.split()
    if profile == "openjdk-default":
        return EMBEDDED_G1_FLAGS.split(",") if embedded else G1_FLAGS.split()
    return []


def normalize_embedded_kotlin_daemon_options_value(value: str, profile: str) -> str:
    if not profile or profile == "repo-default":
        return value

    tokens = [token.strip() for token in value.split(",") if token.strip()]
    tokens = [token for token in tokens if not is_gc_flag_token(token)]
    for token in profile_flags(profile, embedded=True):
        if token not in tokens:
            tokens.append(token)
    return ",".join(tokens)


def normalize_org_gradle_jvmargs(value: str, gradle_profile: str, kotlin_profile: str) -> tuple[str, str | None]:
    tokens = value.split()
    updated_tokens: list[str] = []
    embedded_kotlin_value: str | None = None

    for token in tokens:
        if token.startswith(KOTLIN_DAEMON_OPTIONS_PREFIX):
            raw_value = token[len(KOTLIN_DAEMON_OPTIONS_PREFIX) :]
            normalized_value = normalize_embedded_kotlin_daemon_options_value(raw_value, kotlin_profile)
            updated_tokens.append(f"{KOTLIN_DAEMON_OPTIONS_PREFIX}{normalized_value}")
            embedded_kotlin_value = normalized_value
            continue

        if gradle_profile and gradle_profile != "repo-default" and is_gc_flag_token(token):
            continue

        updated_tokens.append(token)

    for token in profile_flags(gradle_profile):
        if token not in updated_tokens:
            updated_tokens.append(token)

    return " ".join(updated_tokens).strip(), embedded_kotlin_value


def update_property_lines(text: str, property_profiles: dict[str, str]) -> tuple[str, dict[str, str], bool]:
    lines = text.splitlines()
    found: dict[str, str] = {}
    changed = False
    embedded_kotlin_options_present = False
    kotlin_profile = property_profiles.get("kotlin.daemon.jvmargs", "")

    for idx, line in enumerate(lines):
        stripped = line.strip()
        for prop in TARGET_PROPERTIES:
            profile = property_profiles.get(prop, "")
            if not profile or profile == "repo-default":
                continue
            prefix = f"{prop}="
            if stripped.startswith(prefix):
                current = stripped[len(prefix):].strip()
                if prop == "org.gradle.jvmargs":
                    updated, embedded_kotlin_value = normalize_org_gradle_jvmargs(current, profile, kotlin_profile)
                    if embedded_kotlin_value is not None:
                        embedded_kotlin_options_present = True
                        found["kotlin.daemon.jvmargs"] = embedded_kotlin_value
                else:
                    updated = normalize_value(current, profile)
                found[prop] = updated
                new_line = f"{prop}={updated}"
                if lines[idx] != new_line:
                    lines[idx] = new_line
                    changed = True

    for prop in TARGET_PROPERTIES:
        profile = property_profiles.get(prop, "")
        if not profile or profile == "repo-default":
            continue
        if prop == "kotlin.daemon.jvmargs" and embedded_kotlin_options_present:
            continue
        if prop not in found:
            if profile == "imnotokay":
                initial_value = IMNOTOKAY_FLAGS
            elif profile == "openjdk-default":
                initial_value = G1_FLAGS
            else:
                initial_value = ""
            lines.append(f"{prop}={initial_value}".rstrip())
            found[prop] = initial_value
            changed = True

    updated_text = "\n".join(lines)
    if text.endswith("\n"):
        updated_text += "\n"
    else:
        updated_text += "\n"
    return updated_text, found, changed


def process_file(path: Path) -> dict | None:
    original = path.read_text(encoding="utf-8")
    updated_text, props, changed = update_property_lines(original, {})
    if changed:
        path.write_text(updated_text, encoding="utf-8")
    return {
        "path": str(path),
        "changed": changed,
        "properties": props,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply GC-specific Gradle property overrides to a project checkout.")
    parser.add_argument("project_root", help="Project root to scan for gradle.properties files.")
    parser.add_argument("--gc-variant", default="", help="GC variant. Only imnotokay causes changes.")
    parser.add_argument("--gradle-gc-profile", default="", help="Explicit profile for org.gradle.jvmargs.")
    parser.add_argument("--kotlin-gc-profile", default="", help="Explicit profile for kotlin.daemon.jvmargs.")
    parser.add_argument(
        "--report-file",
        default="",
        help="Optional JSON file to write the applied override report.",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    property_profiles = {
        "org.gradle.jvmargs": args.gradle_gc_profile or ("imnotokay" if args.gc_variant == "imnotokay" else ""),
        "kotlin.daemon.jvmargs": args.kotlin_gc_profile or ("imnotokay" if args.gc_variant == "imnotokay" else ""),
    }
    if not any(profile and profile != "repo-default" for profile in property_profiles.values()):
        report = {"gc_variant": args.gc_variant, "applied": False, "files": []}
        if args.report_file:
            report_path = Path(args.report_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0

    files = sorted(project_root.rglob("gradle.properties"))
    report_files = []
    for path in files:
        original = path.read_text(encoding="utf-8")
        updated_text, props, changed = update_property_lines(original, property_profiles)
        if changed:
            path.write_text(updated_text, encoding="utf-8")
        report_files.append(
            {
                "path": str(path),
                "changed": changed,
                "properties": props,
            }
        )

    if not files:
        root_props = project_root / "gradle.properties"
        root_props.write_text("", encoding="utf-8")
        original = root_props.read_text(encoding="utf-8")
        updated_text, props, changed = update_property_lines(original, property_profiles)
        if changed:
            root_props.write_text(updated_text, encoding="utf-8")
        report_files.append({"path": str(root_props), "changed": changed, "properties": props})

    report = {
        "gc_variant": args.gc_variant,
        "applied": True,
        "property_profiles": property_profiles,
        "files": report_files,
    }
    if args.report_file:
        report_path = Path(args.report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
