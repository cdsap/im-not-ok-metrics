# Rectangle Results Table

Baseline reference:

- Debug build: `1164.5s`
- Debug Gradle GC p95: `1430.8ms`
- Release build: `1957.0s`
- Release Gradle GC p95: `3206.8ms`

| Attempt | Variant | Commit | Debug build (s) | Debug vs base | Debug GC p95 (ms) | Debug GC vs base | Release build (s) | Release vs base | Release GC p95 (ms) | Release GC vs base |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 01 | `rectangle-minimal-tightening` | `dec3d206b788` | 1303.0 | +11.9% | 191.1 | -86.6% | 2106.5 | +7.6% | 172.8 | -94.6% |
| 02 | `rectangle-throughput-75` | `ace955d6c5eb` | 1321.0 | +13.4% | 187.5 | -86.9% | 2097.5 | +7.2% | 171.4 | -94.7% |
| 03 | `rectangle-no-clamp` | `60792519452c` | 1336.5 | +14.8% | 189.7 | -86.7% | 2075.0 | +6.0% | 174.2 | -94.6% |
| 04 | `rectangle-baseline-shadow` | `bcf01c5df40b` | 1321.5 | +13.5% | 192.1 | -86.6% | 2084.0 | +6.5% | 178.0 | -94.4% |
| 05 | `rectangle-gradle-openjdk-default` | `82af578da3f6` | 1322.5 | +13.6% | 198.2 | -86.2% | 2109.0 | +7.8% | 177.0 | -94.5% |
| 06 | `rectangle-gradle-openjdk-kotlin-imnotokay` | `82af578da3f6` | 1320.5 | +13.4% | 195.1 | -86.4% | 2108.5 | +7.7% | 169.4 | -94.7% |
| 07 | `rectangle-baseline-shadow-98` | `82af578da3f6` | 1316.0 | +13.0% | 201.1 | -85.9% | 2125.0 | +8.6% | 180.0 | -94.4% |
| 08 | `rectangle-big-young-90` | `3c1b82582728` | 1341.0 | +15.2% | 200.8 | -86.0% | 2039.0 | +4.2% | 171.8 | -94.6% |
| 09 | `rectangle-phase-aware-activation` | `0333314b803b` | 1353.0 | +16.2% | 200.7 | -86.0% | 2106.5 | +7.6% | 175.1 | -94.5% |

Current read:

- Best debug build time so far: attempt `01`
- Best release build time so far: attempt `08`
- Best debug GC p95 so far: attempt `02`
- Best release GC p95 so far: attempt `06`
- No attempt has met the combined goal yet
