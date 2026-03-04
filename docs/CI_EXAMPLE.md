# GitHub Actions Example

```yaml
name: android-build-gc-harness

on:
  workflow_dispatch:
  pull_request:

jobs:
  profile-build:
    runs-on: ubuntu-latest
    timeout-minutes: 60

    steps:
      - uses: actions/checkout@v4

      - name: Set up JDK 21
        uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: '21'

      - name: Run build with baseline profiling
        run: |
          chmod +x scripts/*.sh
          scripts/run_build.sh "./gradlew assembleDebug --stacktrace"

      - name: Upload profiling artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: gradle-jvm-gc-artifacts
          path: artifacts/
```

Deep mode example:

```yaml
      - name: Run build with deep profiling
        run: |
          chmod +x scripts/*.sh
          DEEP=1 scripts/run_build.sh "./gradlew assembleDebug --stacktrace"
```
