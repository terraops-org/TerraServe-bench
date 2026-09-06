---
concepts: [throughput-benchmark, mapserver, terraserve, memory-measurement]
status: active
confidence: measured
updated: 2026-09-06
---

# Throughput results

One run, 2026-09-04, one developer box. `N=300 CONC=4`, 256x256 WMS GetMaps at 600
distinct bboxes (panning), Cascais RGB COG, EPSG:3763. Memory is cgroup v2 `anon`.

**Which TerraServe:** this table used a private host build that self-reports 0.1.0. The
repo now runs the pinned public release `ghcr.io/terraops-org/terraserve:0.2.0`; a smoke
run and the full default run (`N=800`) with it are at the end of this file.

| engine | req/s | baseline | peak | settle | anon under load |
|---|---|---|---|---|---|
| MapServer 8.6.5 | 156.5 | 30.5 MB | 772.1 MB | **772.1 MB** | `▁▁▂▂▂▂▄▆▇███████████` |
| TerraServe (no cache) | 279.5 | 280.0 MB | 356.7 MB | 238.2 MB | `▃▄▄▄▄▄▄▄▄▄▄▃▃▃▃▃` |
| TerraServe (LRU 256) | 367.0 | 281.1 MB | 438.2 MB | 355.7 MB | `▃▄▅▅▅▅▅▅▄▄▄▄▄▄▄` |

## What the shape says

MapServer starts 9x lighter and ends 2x heavier. It climbs monotonically under panning
and **settles at its peak**, giving nothing back: that is GDAL's process-global block
cache filling as the request stream touches new parts of the raster. TerraServe settles
below its peak in both modes, which is the behaviour its design claims.

The LRU is worth 1.3x throughput for ~120 MB more held.

## Caveats

**Not a like-for-like process comparison.** MapServer runs Apache with a pool of
mod_fcgid `mapserv` workers; TerraServe is one process. The 772 MB is across that pool. This
is the realistic deployment for each, not an identical one.

**MapServer's growth is configurable.** `GDAL_CACHEMAX` bounds that block cache and is
left at the image default here. The vector benchmark pins it to 64 MB per worker for
exactly this reason; this one does not, so treat 772 MB as "the default deployment"
rather than "the best MapServer can do".

**Short run.** 300 requests. The curve is still rising for MapServer at the end, so the
772 MB is a floor on where it would settle, not a ceiling.

## Reproduce

```bash
cd benchmarks/throughput
N=300 CONC=4 ./run.sh
```

`sustained.py` starts and stops its own containers and writes `sustained.png` into the
results directory of the run.

MapServer runs its **native** stack (Apache + mod_fcgid), not a python mapscript
wrapper: the public 8.6 image ships no mapscript, and the native path is the realistic
deployment anyway. Two things that cost time to find: the image EXPOSEs 8080 but Apache
listens on 80, and the mapfile must sit under `/etc/mapserver/` or the image's
`MS_MAP_PATTERN` rejects it.

## Smoke run 2026-09-06, pinned TerraServe 0.2.0

`N=100 CONC=4`, same box. Too short to compare with the table above (MapServer's block
cache was still filling at 100 requests: 201 MB, against 772 MB at 300). It shows the
pinned release runs through the harness, nothing more.

| engine | req/s | baseline | peak | settle |
|---|---|---|---|---|
| MapServer 8.6.5 | 93.9 | 30.9 MB | 201.6 MB | 201.6 MB |
| TerraServe 0.2.0 (no cache) | 325.4 | 280.4 MB | 338.7 MB | 231.6 MB |
| TerraServe 0.2.0 (LRU 256) | 373.8 | 276.8 MB | 372.0 MB | 291.2 MB |

## Full default run 2026-09-06, pinned TerraServe 0.2.0

`N=800 CONC=4`, the defaults, same box, `results/20260906-084613/` (on the owner's box;
`results/` is not in the repo). Every engine 800/800 PNG responses (the harness now checks the PNG signature, not just a non-empty
body). MapServer's 1.9 GB is its FastCGI workers each filling a default-sized GDAL block
cache; the vector benchmark caps that at 64 MB per worker, this one does not.

| engine | req/s | baseline | peak | settle |
|---|---|---|---|---|
| MapServer 8.6.5 | 197.1 | 30.4 MB | 1910.2 MB | 1910.2 MB |
| TerraServe 0.2.0 (no cache) | 343.7 | 276.6 MB | 368.0 MB | 284.1 MB |
| TerraServe 0.2.0 (LRU 256) | 479.8 | 278.2 MB | 533.0 MB | 479.6 MB |

## GeoServer added, warm-up added, 2026-09-06

The benchmark now runs four engines and discards `WARMUP=100` requests per engine before
the measured `N` (all engines, same treatment). Two consequences for reading the earlier
tables: MapServer's baseline is no longer a cold pool (198 MB here against 30 MB above,
its FastCGI workers already spawned), and the LRU row is higher because the warm-up
fills part of its cache. GeoServer: own container, `-Xms512m -Xmx4096m`, GWC off, the
layer provisioned over REST by the render benchmark's script. `N=800 CONC=4`, same box,
run from a fresh checkout of the same code (`results/20260906-091207/` on the owner's box;
not in the repo).
Every engine 800/800 PNG.

| engine | req/s | baseline | peak | settle |
|---|---|---|---|---|
| MapServer 8.6.5 | 216.6 | 197.6 MB | 1908.9 MB | 1908.9 MB |
| TerraServe 0.2.0 (no cache) | 289.6 | 291.1 MB | 400.2 MB | 286.9 MB |
| TerraServe 0.2.0 (LRU 256) | 644.3 | 328.8 MB | 538.8 MB | 473.0 MB |
| GeoServer 2.26.1 | 134.4 | 1203.3 MB | 1228.5 MB | 1213.2 MB |

GeoServer's line is flat at about 1.2 GB: the JVM has committed its heap and the requests
move it by a few tens of MB. On raster panning at 4 concurrent it renders at 0.62x
MapServer and 0.46x TerraServe without cache.
