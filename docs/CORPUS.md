# Corpus Collection

The repository now has two GitHub Actions entry points:

- `.github/workflows/profile-build.yml`
- `.github/workflows/collect-corpus.yml`
- `.github/workflows/collect-corpus-warm-guh.yml`

## Intent

- `profile-build.yml` is the single-run path for validating a project or debugging one workload shape.
- `collect-corpus.yml` is the dataset path for repeated executions across the same project configuration.
- `collect-corpus-warm-guh.yml` is the repeated-run path that first prewarms a shared `GRADLE_USER_HOME` and then reuses it across iteration jobs.

This follows the useful part of Telltale's model: treat execution as parameterized orchestration, not as a one-off shell command.

## Recommended usage

### Single run

Use `profile-build.yml` when you want one artifact set for a project and build target.

Important inputs:

- `target_repository`
- `target_ref`
- `gradle_command`
- `gradle_task`
- `configuration_slug`
- `run_kind`
- `gradle_gc_profile`
- `kotlin_gc_profile`
- `test_jvm_gc_profile`
- `deep_mode`

### Corpus run

Use `collect-corpus.yml` when you want repeated runs for the same workload shape.

Important inputs:

- `target_repository`
- `target_ref`
- `gradle_command`
- `gradle_task`
- `project_slug`
- `configuration_slug`
- `run_kind`
- `iterations_json`
- `gradle_gc_profile`
- `kotlin_gc_profile`
- `test_jvm_gc_profile`
- `deep_mode`

Example:

```json
[1,2,3,4,5,6,7,8,9,10]
```

This creates ten repeated runs of the same build shape, each with the same project/configuration labels and a different `iteration` value in `run_profile.json`.

If you only want to choose a task such as `assembleRelease`, you can now use `gradle_task` instead of typing the full command. The workflow expands it to:

```bash
./gradlew <task> --stacktrace
```

### Warm GUH corpus run

Use `collect-corpus-warm-guh.yml` when you want each iteration to run in a clean workspace but reuse the same prewarmed Gradle user home.

Important inputs:

- `target_repository`
- `target_ref`
- `gradle_command`
- `gradle_task`
- `prewarm_command`
- `project_slug`
- `configuration_slug`
- `run_kind`
- `iterations_json`

This workflow:

1. creates a dedicated `GRADLE_USER_HOME`
2. runs one prewarm command to populate wrapper and dependency caches
3. removes the local Gradle build cache from `GRADLE_USER_HOME/caches/build-cache-*`
4. saves that Gradle user home under a workflow-scoped cache key
5. restores the same Gradle user home for each iteration job

This preserves dependency and wrapper reuse while avoiding `FROM-CACHE` results caused by the prewarm job's local build cache.

## Why this matters for "I'm not Ok"

The goal is not just to gather logs. The goal is to build a workload corpus for JDK 23 collector design.

That means every run should be labeled with:

- which project it came from
- which build shape it represents
- whether it was clean, warm, incremental, lint-heavy, or test-heavy
- which iteration it is inside a repeated series
- which collector family we intended each JVM role to use

Without those labels, later GC analysis becomes anecdotal instead of systematic.
