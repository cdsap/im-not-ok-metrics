# Corpus Matrix

The first corpus matrix is checked in at [`/Users/inakivillar/experiments/im-not-ok/configs/corpus_matrix.phase1.json`](/Users/inakivillar/experiments/im-not-ok/configs/corpus_matrix.phase1.json).

## Phase 1 intent

Phase 1 is meant to establish the first workload family for "I'm not Ok", not to generalize across all Android builds yet.

The checked-in matrix currently uses `cdsap/simple-app-for-profile` and covers four build shapes:

- `clean-assemble-debug`
- `warm-assemble-debug`
- `lint-heavy`
- `compile-heavy`

The default workflow repeats each entry for ten iterations through the `iterations_json` input in [`/Users/inakivillar/experiments/im-not-ok/.github/workflows/collect-corpus.yml`](/Users/inakivillar/experiments/im-not-ok/.github/workflows/collect-corpus.yml).

## How to extend the matrix

Add new entries to the JSON file with these fields:

- `target_repository`
- `target_ref` (optional)
- `project_slug`
- `configuration_slug`
- `run_kind`
- `gradle_command`

This keeps the corpus definition machine-readable and reviewable in git.

## Recommended next additions

After `cdsap/simple-app-for-profile`, add:

1. A medium multi-module Compose app.
2. A large modular app using KSP or KAPT.
3. A library-heavy Android repository with significant lint and resource work.
