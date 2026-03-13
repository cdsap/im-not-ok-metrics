# Corpus Collection

The repository now has two GitHub Actions entry points:

- `.github/workflows/profile-build.yml`
- `.github/workflows/collect-corpus.yml`

## Intent

- `profile-build.yml` is the single-run path for validating a project or debugging one workload shape.
- `collect-corpus.yml` is the dataset path for repeated executions across the same project configuration.

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

## Why this matters for "I'm not Ok"

The goal is not just to gather logs. The goal is to build a workload corpus for JDK 23 collector design.

That means every run should be labeled with:

- which project it came from
- which build shape it represents
- whether it was clean, warm, incremental, lint-heavy, or test-heavy
- which iteration it is inside a repeated series
- which collector family we intended each JVM role to use

Without those labels, later GC analysis becomes anecdotal instead of systematic.
