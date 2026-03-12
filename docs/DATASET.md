# Dataset Layout

The harness produces raw per-run outputs under `artifacts/<run_id>/`. For corpus collection, treat each run directory as immutable and index it rather than rewriting it.

## Per-run files

- `metadata.json`
- `project_profile.json`
- `run_profile.json`
- `summary.json`
- `summary.md`
- `logs/`
- `gradle/`

`summary.json` and `summary.md` now include best-effort JFR allocation summaries for JVMs where non-empty `.jfr` recordings are available. The current rollups are intentionally basic:

- allocation mode (`sampled` or `exact`)
- total observed allocation bytes
- allocation rate in MB/s
- top allocating threads
- top allocated classes

## Why two extra metadata files

- `project_profile.json` describes the workload family: module count, Android module-role counts, Kotlin/Java source volume, and whether the project uses KSP, KAPT, or Compose.
- `run_profile.json` describes the specific run shape: project slug, configuration slug, run kind, iteration number, runner shape, build command, and declared per-role GC profiles.

This separation lets you ask both kinds of questions:

- "How do Kotlin-heavy projects behave on JDK 23?"
- "How do clean `assembleDebug` runs behave on ephemeral 4 vCPU runners?"

## Recommended storage model

For long-term analysis, store uploaded runs grouped by project and configuration:

```text
datasets/
  <project_slug>/
    <configuration_slug>/
      <run_id>/
```

The raw harness output does not need to change for this. You can reconstruct that layout from the three key identifiers already written into each run:

- `project_slug`
- `configuration_slug`
- `run_id`

## Dataset index

Use:

```bash
scripts/index_dataset.sh artifacts
```

This writes `artifacts/dataset_index.jsonl`, one JSON object per run, to make later analysis and clustering easier.

The dataset index now includes both:

- declared GC intent per role, from `run_profile.json`
- observed GC per role, from the collected GC logs
- richer project-shape fields such as Android app/library module counts
