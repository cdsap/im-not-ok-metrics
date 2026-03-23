#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


GC_FLAG_PATTERN = re.compile(
    r"(?:^|\s)-XX:(?:\+|-)?Use(?:ParallelGC|G1GC|SerialGC|ZGC|ShenandoahGC|EpsilonGC|ImNotOkGC|ImNotOkayGC)(?=\s|$)"
)

IMNOTOKAY_FLAGS = "-XX:+UnlockExperimentalVMOptions -XX:+UseImNotOkayGC"
TARGET_PROPERTIES = ("org.gradle.jvmargs", "kotlin.daemon.jvmargs")


def normalize_value(value: str) -> str:
    cleaned = GC_FLAG_PATTERN.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if IMNOTOKAY_FLAGS not in cleaned:
      cleaned = f"{cleaned} {IMNOTOKAY_FLAGS}".strip()
    return cleaned


def update_property_lines(text: str) -> tuple[str, dict[str, str], bool]:
    lines = text.splitlines()
    found: dict[str, str] = {}
    changed = False

    for idx, line in enumerate(lines):
        stripped = line.strip()
        for prop in TARGET_PROPERTIES:
            prefix = f"{prop}="
            if stripped.startswith(prefix):
                current = stripped[len(prefix):].strip()
                updated = normalize_value(current)
                found[prop] = updated
                new_line = f"{prop}={updated}"
                if lines[idx] != new_line:
                    lines[idx] = new_line
                    changed = True

    for prop in TARGET_PROPERTIES:
        if prop not in found:
            lines.append(f"{prop}={IMNOTOKAY_FLAGS}")
            found[prop] = IMNOTOKAY_FLAGS
            changed = True

    updated_text = "\n".join(lines)
    if text.endswith("\n"):
        updated_text += "\n"
    else:
        updated_text += "\n"
    return updated_text, found, changed


def process_file(path: Path) -> dict | None:
    original = path.read_text(encoding="utf-8")
    updated_text, props, changed = update_property_lines(original)
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
    parser.add_argument(
        "--report-file",
        default="",
        help="Optional JSON file to write the applied override report.",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    if args.gc_variant != "imnotokay":
        report = {"gc_variant": args.gc_variant, "applied": False, "files": []}
        if args.report_file:
            Path(args.report_file).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0

    files = sorted(project_root.rglob("gradle.properties"))
    report_files = []
    for path in files:
        report_files.append(process_file(path))

    if not files:
        root_props = project_root / "gradle.properties"
        root_props.write_text(
            "\n".join(
                [
                    f"org.gradle.jvmargs={IMNOTOKAY_FLAGS}",
                    f"kotlin.daemon.jvmargs={IMNOTOKAY_FLAGS}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        report_files.append(process_file(root_props))

    report = {
        "gc_variant": args.gc_variant,
        "applied": True,
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
