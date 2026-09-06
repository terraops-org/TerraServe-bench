---
concepts: [vector-benchmark, mapserver, geoserver, terraserve, correctness-canary]
status: active
confidence: measured
updated: 2026-09-06
---

# Vector results (COS2023 land cover)

One run, 2026-09-04, one developer box. COS2023v1-S2.gpkg, 842,413 MultiPolygons,
EPSG:3763. `N=200 WARMUP=100 CONC=8`, 256x256 WMS 1.3.0 GetMaps, 81 distinct bboxes.
All engines read the same GeoPackage and the same classification: TerraServe and
GeoServer read `cos2023.sld` directly, MapServer reads a mapfile generated from it.
Memory is cgroup v2 `anon`, sampled identically.

**Which TerraServe:** this table used a local `terraserve:latest` image built from the
private repo. The repo now runs the pinned public release
`ghcr.io/terraops-org/terraserve:0.2.0`; a smoke run and the full four-engine default run
with it are at the end of this file.

| engine | req/s | p50 | p95 | base | peak | settle |
|---|---|---|---|---|---|---|
| TerraServe (no cache) | 162.4 | 45 ms | 83 ms | 123 MB | 224 MB | 94 MB |
| TerraServe (WMS cache) | 3772.7 | 2 ms | 3 ms | 160 MB | 160 MB | 106 MB |
| MapServer 8.6.5 FastCGI | 280.3 | 27 ms | 42 ms | 218 MB | 231 MB | 231 MB |
| GeoServer 2.26 | 98.9 | 76 ms | 131 ms | 979 MB | 1046 MB | 1046 MB |

## Read the WMS-cache row separately

**TerraServe-wmscache is not comparable to the other three and must not be quoted
next to them.** GeoServer runs with GWC off and MapServer has no response cache, so
those two render every request. TerraServe-wmscache is serving cache hits: the run
uses 200 requests over 81 distinct bboxes, so most requests repeat one already
answered. 3772 req/s is a cache-hit rate, not a render rate.

The dynamic-render comparison is the other three:

| engine | req/s | relative |
|---|---|---|
| MapServer 8.6.5 | 280.3 | 1.7x |
| TerraServe (no cache) | 162.4 | 1.0x |
| GeoServer 2.26 | 98.9 | 0.6x |

**MapServer is the fastest dynamic vector renderer here**, by 1.7x over TerraServe.

## Memory

TerraServe settles below its peak in both modes (224 to 94 MB, 160 to 106 MB).
MapServer and GeoServer both settle exactly at their peak, releasing nothing.
GeoServer's ~1 GB is the JVM committing toward `-Xmx4096m` regardless of per-request
use.

MapServer here is pinned to `GDAL_CACHEMAX=64` per worker, which is why it stays near
230 MB rather than climbing the way it does in the raster throughput benchmark.

## Correctness

All three engines render the same classification. Comparing the sample tiles on
pixels both engines drew (2026-09-06, pinned 0.2.0, the `sample_cos_<engine>.png` of
`results/20260906-084613/` on the owner's box; MapServer's background is transparent
where the other two paint white, so only its opaque pixels are compared):

| pair | mean abs difference |
|---|---|
| TerraServe vs GeoServer | 0.93 |
| TerraServe vs MapServer | 6.39 |
| GeoServer vs MapServer | 6.84 |

The raw whole-image difference looks like 136/255, which is misleading: the request
does not set `TRANSPARENT`, so MapServer leaves 52% of the tile transparent while the
other two fill it opaque white. Dropping alpha compares black against white over half
the image. Compare opaque pixels only, or set `TRANSPARENT=TRUE` for all engines.

## Reproduce

```bash
cd benchmarks/vector
N=200 WARMUP=100 CONC=8 ./run.sh

# The GeoPackage is mounted as a resolved file, so a ./data made of symlinks (dev
# checkout) works as-is. GPKG_DIR only matters when the file lives elsewhere:
GPKG_DIR=/path/to/gpkg ./run.sh
```

Engine keys are `ts-nocache`, `ts-wmscache`, `mapserver`, `geoserver`. An unknown key
is now a hard error: `ENGINES=ts` once ran the entire comparison with no TerraServe in
it and the output looked completely normal.

## Smoke run 2026-09-06, pinned TerraServe 0.2.0

`ENGINES=ts-nocache,mapserver N=100 WARMUP=50 CONC=8`, same box. Too short to compare
with the table above: half the warm-up, a third of the requests, and MapServer reads
97 req/s here against 280 above, most likely because its FastCGI pool (`MIN_PROCESSES=2`)
had not grown yet. It shows the pinned release serves the GeoPackage with the SLD through
this harness, nothing more.

| engine | req/s | p50 | p95 | base | peak | settle |
|---|---|---|---|---|---|---|
| TerraServe 0.2.0 (no cache) | 154.3 | 50 ms | 82 ms | 199 MB | 298 MB | 153 MB |
| MapServer 8.6.5 FastCGI | 96.9 | 26 ms | 43 ms | 178 MB | 198 MB | 198 MB |

## Full default run 2026-09-06, pinned TerraServe 0.2.0, all four engines

`N=600 WARMUP=300 CONC=16`, the defaults, same box, `results/20260906-084613/` on the
owner's box, not in the repo (the first complete default-settings run on 0.2.0, on the harness fixed the same day: a
response only counts when it is a PNG, see the wiki log). Every engine 600/600.
GeoServer in its own container, `-Xmx4096m`, GWC off; MapServer with up to 16 FastCGI
workers and `GDAL_CACHEMAX=64` each.

| engine | req/s | p50 | p95 | base | peak | settle |
|---|---|---|---|---|---|---|
| TerraServe 0.2.0 (no cache) | 200.8 | 76 ms | 128 ms | 218 MB | 628 MB | 178 MB |
| TerraServe 0.2.0 (WMS cache 256 MiB) | 3764.1 | 4 ms | 7 ms | 286 MB | 286 MB | 178 MB |
| MapServer 8.6.5 FastCGI | 294.8 | 33 ms | 55 ms | 316 MB | 415 MB | 415 MB |
| GeoServer 2.26.1 | 130.3 | 110 ms | 231 ms | 1777 MB | 1815 MB | 1810 MB |

The cache row is a hit rate, not a render rate. At 16 concurrent requests MapServer's
worker pool renders more tiles per second than TerraServe's single process; TerraServe
holds the least memory after load. Visual parity: the three `sample_cos_<engine>.png`
of this run are the same tile; TerraServe and GeoServer differ by 0.93 mean RGB over the
frame; MapServer differs from each by about 6.5 mean RGB over its opaque pixels, with a
fifth of them differing (its background is transparent where the other two paint white,
so a full-frame comparison shows the 136/255 trap noted in the wiki's measurement page).
The three tiles look the same to the eye.
