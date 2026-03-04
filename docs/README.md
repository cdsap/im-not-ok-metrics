# JVM GC Build Harness

This harness collects low-overhead JVM and OS telemetry for Gradle Android builds on ephemeral CI agents. It is Linux-first, works on JDK 17 and JDK 21, and keeps all outputs in a single `artifacts/<timestamp>/` directory for CI upload.

## What it captures

- GC logs for JVMs started during the build via `JAVA_TOOL_OPTIONS`, plus best-effort dynamic attachment for already-running daemons through `jcmd`.
- JFR in deep mode (`DEEP=1`) by attaching to discovered JVMs with `jcmd`.
- Per-process RSS, VSZ, CPU, threads, faults, and I/O counters.
- System-level load and memory snapshots.
- Best-effort Gradle task timing derived from timestamped console output.
- Metadata for the repo, OS, JDK, and build command.

## Repository layout

- `scripts/run_build.sh`
- `scripts/find_pids.sh`
- `scripts/collect_metrics.sh`
- `scripts/summarize.sh`
- `scripts/summarize.py`
- `configs/jvm_args_gc_logging.txt`
- `configs/jvm_args_jfr.txt`
- `configs/sampling_config.env`
- `docs/CI_EXAMPLE.md`

## Prerequisites

- `bash`
- `python3`
- `java`
- `jcmd` is optional but strongly recommended
- `rg` is optional for best-effort AGP and Kotlin version discovery

## Local usage

Run the baseline mode:

```bash
scripts/run_build.sh "./gradlew assembleDebug"
```

Run deep mode with JFR:

```bash
DEEP=1 scripts/run_build.sh "./gradlew assembleDebug"
```

Optional environment overrides:

```bash
SAMPLING_INTERVAL_SECONDS=1
PID_DISCOVERY_INTERVAL_SECONDS=2
ENABLE_JCMD_ATTACH=1
ENABLE_JCMD_DYNAMIC_GC_LOGS=1
```

The command exits with the same exit code as the build. Profiling failures are recorded in `warnings.log` and do not fail the run.

## Artifact structure

Each run writes to `artifacts/<timestamp>/`.

- `metadata.json`
- `warnings.log`
- `logs/gradle_stdout.log`
- `logs/gradle_stderr.log`
- `logs/gc/*.log`
- `logs/jfr/*.jfr`
- `logs/os/discovered_pids.csv`
- `logs/os/process_metrics.csv`
- `logs/os/system_metrics.csv`
- `gradle/task_timeline.csv`
- `summary.json`
- `summary.md`

The GC and JFR directories contain raw per-pid files and best-effort role-named copies such as `gradle-daemon-gc.log`, `kotlin-daemon-12345-gc.log`, and `gradle-daemon.jfr`.

## Notes on process discovery

- Preferred path: `jcmd -l`, which is reliable for daemon-style JVMs.
- Fallback path: `ps` matching Java commands that contain `GradleDaemon`, `KotlinCompileDaemon`, Gradle test executors, or Gradle worker classes.
- If the Gradle daemon is already running, the harness still samples it and attempts to attach GC logging and JFR dynamically through `jcmd`.

## OS support

### Linux

Linux uses `/proc` for process RSS, VSZ, thread count, faults, and I/O. System metrics come from `/proc/loadavg`, `/proc/meminfo`, and `/proc/diskstats`.

### macOS

macOS uses `ps` for RSS, VSZ, CPU, and thread count. `vmmap` is intentionally not used in the default loop because it is too expensive for always-on sampling. Fault and I/O counters are left blank in `process_metrics.csv`, and system metrics are best effort from `sysctl` and `vm_stat`.

## Interpreting the summary

- `summary.json` is the machine-readable aggregate.
- `summary.md` is the quick report for CI artifacts.
- GC pause stats are derived from unified GC log pause lines and safepoint stop lines when available.
- Task correlation is approximate and based on timestamped console lines like `> Task :app:compileDebugKotlin`.

## Limitations

- Dynamic GC log enablement for already-running JVMs depends on `jcmd VM.log` support in the target JDK.
- Task timing is best effort and may be coarse for parallel task execution.
- Non-JVM tools are outside the default capture scope.
