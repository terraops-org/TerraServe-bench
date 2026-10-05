---
concepts: [vector-benchmark, mapserver, geoserver, terraserve, correctness-canary]
status: active
confidence: measured
updated: 2026-10-04
corrections:
  - section: "Two full runs on a second machine, 2026-10-03 (new method, new pins)"
    reason: "the GeoPackage was on a ZFS dataset (compression on, 1 MB records) for these two runs, which cost MapServer about half its req/s and GeoServer over 40%. The control of the same day, served from RAM, is the better picture."
  - section: "Vector results (COS2023 land cover)"
    reason: "every table before the 2026-10-03 section was measured with the method PR 1 replaced. A threaded client with 8 or 16 connections, a few seconds of load, every measured request a repeat of one sent in the warm-up, no TRANSPARENT parameter, GeoServer on the 108-rule style. They show the harness ran; they are not an engine comparison."
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
response only counts when it is a PNG). Every engine 600/600.
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
so a full-frame comparison shows a meaningless 136/255 difference).
The three tiles look the same to the eye.

## Two full runs on a second machine, 2026-10-03 (new method, new pins)

**Read this section, not the tables above, for how the engines compare.** Everything above
was measured with the method that
[PR #1](https://github.com/terraops-org/TerraServe-bench/pull/1) replaced.

The same two runs and the same machine as in `../throughput/RESULTS.md`: a desktop-class
server (Ryzen 9 7950X, 16 cores and 32 threads, 124 GB, Ubuntu 24.04, kernel 6.8.0, Docker
29.8.1, transparent huge pages `madvise`), its other workloads stopped, MapServer 8.6.6,
TerraServe 0.3.7, GeoServer 3.0.1. 32 client processes (one per host thread), 30 s of warm-up
and 120 s measured per engine, 256x256 GetMaps over 81 grid cells, no repeated request,
`TRANSPARENT=true` for every engine. Same GeoPackage and same classes for all, in the form
each engine evaluates best: TerraServe reads `cos2023.sld` (108 rules), MapServer the mapfile
generated from it (the first matching class wins), GeoServer `cos2023-recode.sld` (the same
classes as one `Recode`). MapServer with 32 FastCGI workers and `GDAL_CACHEMAX=16`; GeoServer
`-Xms256m -Xmx4096m` with a periodic G1 collection, GWC off.
`results/20261003-143704-moura001-run1/` and `results/20261003-150533-moura001-run2/`, not in
the repo. Every engine answered every request with a PNG. Every cell is run 1 / run 2.

| engine | req/s | p50 | p95 | avg PNG | base | peak | settle 2 s | settle 10 s | settle 30 s |
|---|---|---|---|---|---|---|---|---|---|
| TerraServe 0.3.7 (no cache) | 232.0 / 237.7 | 132 / 129 ms | 212 / 207 ms | 127.0 KB | 124 / 137 MB | 535 / 550 MB | 70 / 93 MB | 66 / 70 MB | 31 / 32 MB |
| TerraServe 0.3.7 (WMS cache 256 MiB) | 235.5 / 241.8 | 130 / 127 ms | 210 / 204 ms | 127.0 KB | 519 / 472 MB | 797 / 802 MB | 400 / 402 MB | 376 / 372 MB | 344 / 339 MB |
| MapServer 8.6.6 FastCGI | 534.9 / 599.0 | 57 / 51 ms | 95 / 84 ms | 73.7 KB | 972 / 971 MB | 1025 / 1025 MB | 1025 / 1025 MB | 1025 / 1025 MB | 1025 / 1025 MB |
| GeoServer 3.0.1 | 491.3 / 524.9 | 64 / 59 ms | 101 / 94 ms | 98.1 KB | 1484 / 1391 MB | 1649 / 1554 MB | 1649 / 1554 MB | 941 / 844 MB | 942 / 844 MB |

**Corrected the same day: the GeoPackage was on ZFS for these two runs, and that cost MapServer
about half its speed and GeoServer over 40%.** The fixtures sat on a ZFS dataset with compression on
and 1 MB records, where every small read SQLite makes costs the kernel a decompression. With
the same file served from RAM, MapServer did 1090 req/s and GeoServer 865, TerraServe 267 (the
control is in the next section). Read the table above as these engines on that storage, not
as their speed.

- **Ranking:** MapServer, GeoServer, TerraServe, in both runs. The distances between them are
  not to be read from this table, because of the storage.
- **Noise between the two runs:** 2.5% for TerraServe, 6.8% for GeoServer, 12.0% for
  MapServer. A difference under about 12% between two rows of this table is not a difference.
- **The cache row has no hits**, because no request repeats. It runs at the speed of the
  no-cache row and holds about 310 MB more 30 s after the load. It shows what the response
  cache costs when it cannot help.
- **Bytes per tile:** TerraServe's PNGs are the largest, 127 KB against 74 KB for MapServer
  and 98 KB for GeoServer.
- **Memory after the load:** TerraServe holds 31 MB, MapServer about 1 GB across its 32
  workers, GeoServer 0.8 to 0.9 GB once the JVM has collected.

### The storage under the GeoPackage: a control

Same machine and day. Single runs, 15 s of warm-up and 60 s measured, 32 clients, with the
machine's other workloads running this time (they take about 2 of its 32 threads; on the raster
benchmark that cost 2 to 8%, and here MapServer's ZFS run is 12% under its run with them
stopped, which is also the size of its run-to-run noise). Each cell is req/s and, after the comma, how many cores the
engine spent in the kernel (each container's cgroup `cpu.stat`, read every 2 s by a side script).
`results/20261003-17*-moura001-vmsup-{layer-*,tmpfs-vector-*}/`.

| engine | full layer on ZFS | layer cut to the benchmark area, on ZFS | full layer served from RAM (tmpfs) |
|---|---|---|---|
| MapServer 8.6.6 | 517.8, 14.5 | 684.5, 14.1 | 1090.4, 3.5 |
| GeoServer 3.0.1 | 490.6, 13.9 | 587.9, 14.6 | 865.4, 3.4 |
| TerraServe 0.3.7 | 236.7, 9.3 | 310.3, 5.3 | 267.0, 6.6 |

The cut layer holds 88,233 of the 842,413 features: every one the benchmark can ask for, so
each request draws the same thing in all three columns.

- **ZFS was the largest single factor in the vector numbers of this machine.** From RAM,
  MapServer is 2.1 times as fast and GeoServer 1.8 times; their kernel time falls from 14
  cores to 3.5. TerraServe gains 13%.
- **Without the storage penalty the gap is wider, not narrower:** MapServer renders 4.1 times
  as many tiles per second as TerraServe, GeoServer 3.2 times. From RAM the p95 is 44 ms for
  MapServer, 61 ms for GeoServer and 181 ms for TerraServe.
- **A smaller layer helps all three:** 32%, 20% and 31%. TerraServe gains what MapServer
  gains, so its cost does not follow the size of the layer any more than theirs does.
- **This is a control, not the final table:** single runs, with other workloads on the
  machine. Two full runs on storage that does not penalise small reads have not been done.

### Why TerraServe is behind: measured, then profiled

**How it behaves.** GeoPackage served from RAM, other workloads running, 15 s of warm-up and
60 s measured. "Cores" is the median CPU use of the container while it was under load, "CPU
per tile" is cores divided by req/s.
`results/20261003-17*-moura001-vmsup-tmpfs-vector-*/`.

| engine | clients | req/s | p50 | cores used, of 32 | of which kernel | CPU per tile |
|---|---|---|---|---|---|---|
| MapServer 8.6.6 | 32 | 1090.4 | 28 ms | 28.2 | 3.5 | 25.9 ms |
| GeoServer 3.0.1 | 32 | 865.4 | 35 ms | 26.8 | 3.4 | 31.0 ms |
| TerraServe 0.3.7 | 32 | 267.0 | 115 ms | 14.8 | 6.6 | 55.3 ms |
| TerraServe 0.3.7 | 16 | 288.3 | 52 ms | 12.6 | 4.4 | 43.7 ms |
| TerraServe 0.3.7 | 8 | 301.6 | 25 ms | 7.5 | 1.2 | 24.9 ms |

- **TerraServe does not get faster with more clients:** 302, 288 and 267 req/s with 8, 16 and
  32. More clients only add waiting (p50 from 25 to 115 ms) and kernel time. The same shape was measured first on ZFS with the other
  workloads stopped: 262, 274 and 239 req/s for 8, 16 and 32 clients
  (`results/20261003-1*-moura001-{cpu-vector,vector-conc16,vector-conc8}/`).
- **Its work per tile is not the problem.** With 8 clients a tile costs TerraServe 24.9 ms of
  CPU, about what MapServer spends at 32 clients (25.9 ms). MapServer's and GeoServer's cost
  with fewer clients was not measured.
- **Its cost per tile doubles with the load and it leaves half the machine idle.** 55 ms per
  tile at 32 clients, on 15 of 32 threads, where the other two keep 27 to 28 busy.

**What the profile points at: a SQLite lock shared by all requests** (the scratch builds at
the end of this file found two such locks). `perf`
(20 s at 499 Hz, then a shorter recording with call graphs) and `strace -c` were attached to
the server during the load from a helper container sharing its process namespace. GeoPackage
on ZFS for these. The shares are of the server's own CPU, not of the machine's.
`results/20261003-17*-moura001-profile-*/`.

| | 0.3.7, 32 clients | 0.3.7, 8 clients | 0.2.0, 32 clients |
|---|---|---|---|
| CPU in the kernel | 49.4% | 22.5% | 46.6% |
| CPU in TerraServe's own code | 30.9% | 51.6% | 34.7% |
| CPU in ZFS | 12.1% | 16.4% | 11.0% |
| top symbol | `native_queued_spin_lock_slowpath`, 17.2% | `Filter::eval`, 8.9% | `native_queued_spin_lock_slowpath`, 15.6% |
| `pread64` calls per request | about 1,300 | about 1,350 | about 1,300 |

Measured:

- **With 32 clients, 17% of TerraServe's CPU goes to one kernel spin lock.** The call graph
  puts about 15 of those 17 points under SQLite's b-tree code in `sqlite3_step`, called from
  `GpkgWindowedSource::query`, and 12 of them explicitly in SQLite's page cache functions
  `pcache1Fetch` and `pcache1Unpin`, reached through `futex` calls. With 8 clients the same
  spin lock is 0.5%.
- **A request makes about 1,300 page reads** (`pread64`, counted under `strace`, which slows the
  server but does not change what a query reads). Under `strace`, `futex` was 85% of all time
  spent in system calls.
- **SQLite itself does little work:** its own functions are about 2% of the CPU. The time is
  in threads queuing for the lock, not in the database engine.
- **The reads are not the ceiling.** Served from RAM, TerraServe gained 13% and still did
  not grow with the clients (table above).

Read from the source of `v0.3.7`, not measured:

- The SQLite that `rusqlite` bundles is compiled with `SQLITE_ENABLE_MEMORY_MANAGEMENT`
  (`build.rs` of `libsqlite3-sys` 0.28), which makes every connection of the process share one
  page cache group and its lock. That lock is taken for every page a query touches.
- `query_capped` in `src/vector/gpkg.rs` opens a new connection for every request. What that
  costs does not show in the profile, and whether keeping connections open would help is not
  known: the default page cache is about 2 MB, a request touches about 1,300 pages of 4 KB,
  and no request repeats, so most of the reads would remain and the lock is taken per page
  either way.

- **It is not the rendering library.** 0.2.0, built with tiny-skia, shows the same profile and
  the same ceiling as 0.3.7, built with vello_cpu. Back to back on this machine (GeoPackage on
  ZFS, other workloads running, `results/20261003-1*-moura001-vmsup-vector-ts0*/`):

| clients | 0.2.0, tiny-skia | 0.3.7, vello_cpu |
|---|---|---|
| 8 | 218.5 req/s, 35.5 ms CPU per tile | 249.0 req/s, 30.7 ms |
| 16 | 254.5 req/s, 55.5 ms | 253.9 req/s, 53.6 ms |
| 32 | 232.3 req/s, 80.5 ms | 232.2 req/s, 74.9 ms |

- **The largest cost in TerraServe's own code is rule evaluation.** `Filter::eval` and the
  number parsing next to it in the profile are 8% of all CPU at 32 clients and 15% at 8. In
  the source, a comparison parses the rule's literal as a number every time it runs
  (`cmp_eval` in `src/vector/style.rs`), and the 108 rules are evaluated twice per feature in
  view: once to draw, once in a marker and label pass that this style has no use for
  (`src/vector/render.rs`).
- **Ruled out as the ceiling**, each with a run at 32 clients on ZFS with the other workloads
  stopped (`results/20261003-16*-moura001-vector-{onerule,jemalloc-thp-*}/`):

| what was changed for TerraServe | clients | req/s | unchanged run |
|---|---|---|---|
| a style with one rule and no filter instead of 108 rules (PNGs shrink from 127 to 73 KB) | 32 | 243.2 | 239.1 |
| its allocator using huge pages | 32 | 237.2 | 239.1 |
| the same | 16 | 276.5 | 273.8 |

  One run covers the rules and the PNG size: nothing changes although the PNGs come out 42%
  smaller. That run was at 32 clients, where the lock is the limit; what the rules cost at 8
  clients was not measured. The huge-pages rows ran the pinned image with one line added,
  `ENV MALLOC_CONF=thp:always` (recorded in `vector.json` as image `ts-thp-always`); TerraServe
  is built with jemalloc and the cgroup showed about 300 MB of huge pages in use under load, so
  the setting took effect. It holds 300 to 340 MB more 30 s after the load and changes nothing in
  speed. The admission limit is not it either: `--max-inflight` defaults to twice the cores,
  64 here (its `--help`).
- **Tested the same day with scratch builds:** see the next section.

### What removing SQLite's process-wide locks does: scratch builds, same day

Not a release, and nothing here was published. Five builds of the `v0.3.7` source with the
release `Dockerfile` plus one added build argument that passes extra compile flags to the
bundled SQLite (empty for the control and the memory-map build). They differ only in what the
table lists. Measured on the same machine with the GeoPackage served from RAM and the
machine's other workloads running: 15 s of warm-up and 60 s measured, single runs,
interleaved by client count. `results/20261003-1[89]*-moura001-vmsup-tmpfs-variant-*/`.

| build | what changes | req/s, 8 clients | 16 clients | 32 clients | cores used at 32 | CPU per tile at 32 | p95 at 32 | peak memory at 32, measured minute |
|---|---|---|---|---|---|---|---|---|
| control | nothing | 303.8 | 288.2 | 266.6 | 15.0 | 56.2 ms | 182 ms | 542 MB |
| memory map | `PRAGMA mmap_size` on the connection, 9 source lines | 396.3 | 569.2 | 519.2 | 13.4 | 25.9 ms | 80 ms | 565 MB |
| shared page cache off | SQLite compiled without `SQLITE_ENABLE_MEMORY_MANAGEMENT`, source untouched | 370.4 | 451.5 | 424.2 | 11.4 | 26.9 ms | 98 ms | 572 MB |
| shared page cache off, malloc statistics off | the same plus `SQLITE_DEFAULT_MEMSTATUS=0` | 394.9 | 596.3 | 663.4 | 24.1 | 36.3 ms | 79 ms | 1037 MB |
| all three | both compile options and the memory map | 406.9 | 654.5 | 767.0 | 24.0 | 31.3 ms | 66 ms | 874 MB |

Same conditions, 32 clients, for reference: MapServer 1095.4 req/s (28.3 cores, 25.8 ms per
tile, p95 43 ms), GeoServer 854.7 req/s (26.8 cores, 31.4 ms per tile, p95 61 ms). The control
reproduces the release: the released image gave 301.6, 288.3 and 267.0 req/s at 8, 16 and 32
clients earlier the same day under the same conditions, and 287.0 at 16 in this session.

- **Overall:** with all three changes TerraServe goes from 267 to 767 req/s at 32 clients, 2.9
  times. The two compile options alone, with no change to TerraServe's source, give 663, 2.5
  times.
- **Which change bought what.** Read the rows against each other:
  - Taking the shared page cache lock away (third row), or going around the page cache with
    the memory map (second row), makes every request cheaper, but the throughput still does
    not grow from 16 to 32 clients (452 to 424, and 569 to 519).
  - Only the two builds that also switch the malloc statistics off grow with the clients (395,
    596, 663 and 407, 655, 767). That option removes a second process-wide lock, taken on every
    allocation SQLite makes. So there were two such locks, and the second one is what stops the
    scaling once the first is gone.
  - With both locks gone the memory map adds the rest, 663 to 767 at 32 clients, by removing the
    reads: from about 1,290 per request to 16.
- **Part of the gain is not about scaling.** At 8 clients, where the lock was 0.5% of the
  profile, every changed build is already 22 to 34% faster than the control (370 to 407 req/s
  against 304).
- **The settings took, and the sample tile did not change.** The compile options were read back
  from each binary (`ENABLE_MEMORY_MANAGEMENT` present in the control and the memory-map
  build, absent in the other three; `DEFAULT_MEMSTATUS=0` present in the last two). SQLite
  reported the memory map as accepted (2,147,418,112 bytes). The one sample tile each run
  saves is byte-identical across all runs of the five builds and the released image.
- **In the profiles** (control, memory-map build, build with both compile options; 32 clients,
  `results/20261003-19*-moura001-vmsup-tmpfs-profile-*/`): the control spends 22.7% of its CPU
  on the kernel spin lock; in the memory-map build it is 0.5% and in the build with both
  compile options it does not appear. The `terraserve` binary, which contains the bundled
  SQLite, is 72% and 75% of the CPU instead of 37%. The memory-map build still makes about 610
  `futex` calls per request, against about 170 with both compile options, which fits the
  second lock still being there.
- **What it costs:** memory under load. The column is the peak during the measured minute:
  874 MB with all three changes and 1037 MB with the two compile options, against 542 MB for
  the control. A coarser reading, every 2 s over the whole run with the
  warm-up included, saw 1243 MB for the build with the two compile options and nothing above
  the measured-minute peak for the other two.
  The higher peaks are in the two builds that run more requests at once. Thirty seconds after
  the load they hold 38 and 40 MB, against 31.
- **What is left:** rule evaluation is now the largest cost. `Filter::eval` and the number
  parsing next to it are about 20% of the CPU in the memory-map build and in the build with
  both compile options.
- **Where that leaves the comparison:** at 32 clients the best build reaches 90% of GeoServer's
  throughput and 70% of MapServer's, with a p95 of 66 ms against 61 and 43, on 24 of the 32
  threads where they use 27 and 28. The other two engines ran as shipped; TerraServe ran as a
  scratch build nobody else can download. These are single runs with other workloads on the
  machine, so a difference of a few percent between two builds is not a result.

## Two clean runs from RAM, and an engine branch, night of 2026-10-03 to 04

Same machine as the section above (Ryzen 9 7950X, 32 threads, transparent huge pages on
`madvise`), its other workloads shut down, and this time the GeoPackage served from RAM
(tmpfs), so the storage distortion of the two full runs above is gone. Default settings: 32
clients, 30 s of warm-up and 120 s measured, every request a new one. Two rounds, back to
back. Each container's cgroup `cpu.stat` was read every 2 s by a side script; "cores" is the
median while under load, "CPU per tile" is cores divided by req/s.
`results/2026100[34]-*-moura001-clean-tmpfs-run[12]-vector-*/`.

### The engines as shipped

This table replaces the vector table of the two full runs above, which was measured on ZFS.

| engine | req/s, round 1 | round 2 | p50 | p95 | cores used, of 32 | CPU per tile | average PNG | peak memory | 30 s after the load |
|---|---|---|---|---|---|---|---|---|---|
| MapServer 8.6.6 | 1184.7 | 1183.9 | 26 ms | 40 ms | 29.9 | 25.3 ms | 73.7 kB | 1034 MB | 1027, 1024 MB |
| GeoServer 3.0.1 | 918.5 | 929.0 | 34, 33 ms | 56, 55 ms | 29.0, 28.9 | 31.5, 31.1 ms | 98.1 kB | 2050, 1942 MB | 979, 903 MB |
| TerraServe 0.3.7 | 272.0 | 276.6 | 114, 112 ms | 179, 176 ms | 15.4, 15.6 | 56.6, 56.4 ms | 127.0 kB | 564, 553 MB | 31, 32 MB |
| TerraServe 0.3.7, response cache on | 274.4 | 273.8 | 113 ms | 176, 177 ms | 15.2, 15.4 | 55.6, 56.1 ms | 127.0 kB | 798, 804 MB | 341, 340 MB |

- **The order did not change from the ZFS runs, the distances did.** MapServer is at 1184
  req/s where the ZFS table had it at 535 and 599. TerraServe as released is at 272 to 277,
  4.3 times behind MapServer and 3.4 times behind GeoServer.
- **The two rounds agree within 1.7%** on every row.
- **The response cache row is the no-cache row plus memory.** No request repeats, so the cache
  cannot answer anything; thirty seconds after the load the row holds 340 MB, against 31
  without it.
- The three engines do not send the same bytes: MapServer's tiles average 73.7 kB, GeoServer's
  98.1 kB, TerraServe's 127.0 kB. A larger PNG is usually a cheaper encode, so part of each
  engine's CPU per tile may be its encoder settings. Not separated here.

### The same runs with an engine branch, commit by commit

**Not a release.** These are scratch images built with the release `Dockerfile` from three
states of a six-commit private branch of the engine (`perf/sqlite-locks`, not merged, not
published; its last commit, `2056484`, changes comments and documents only, so `ad47996` is
its code).
Nobody else can download them. MapServer and GeoServer ran as shipped. Same session, same
conditions, TerraServe without its response cache.

| build | what it adds to the row above | req/s, round 1 | round 2 | p50 | p95 | cores used | CPU per tile | peak memory | 30 s after the load |
|---|---|---|---|---|---|---|---|---|---|
| released 0.3.7 | | 272.0 | 276.6 | 114, 112 ms | 179, 176 ms | 15.4, 15.6 | 56.6, 56.4 ms | 564, 553 MB | 31, 32 MB |
| `fff5d02` | the bundled SQLite compiled without its two process-wide locks | 697.0 | 695.4 | 45 ms | 73, 74 ms | 24.4, 24.3 | 34.9 ms | 1066, 1030 MB | 40, 41 MB |
| built at `732e763` | rule evaluation (the change is commit `983ab9c`): no number parsing for a text attribute, no label pass for a style without labels. The memory-map code is in this build too, switched off | 761.5 | 762.1 | 42 ms | 64 ms | 22.3, 22.2 | 29.2, 29.1 ms | 1027, 1023 MB | 41, 40 MB |
| `ad47996`, the code of the branch as it stands | the WMS axis order of a CRS asked of PROJ once, not on every request | 872.0 | 885.5 | 35, 34 ms | 60 ms | 28.6, 29.1 | 32.8 ms | 1026, 1231 MB | 45, 43 MB |
| `ad47996` with `TERRASERVE_GPKG_MMAP=1` | the GeoPackage read through a memory map (off unless asked for) | 1032.8 | 1034.7 | 30 ms | 46 ms | 25.7 | 24.9, 24.8 ms | 944 MB | 42, 41 MB |

- **The branch as it stands serves 3.2 times what the release serves** (872 to 886 req/s
  against 272 to 277): 95% of GeoServer and 74% of MapServer. With the memory map switched on,
  3.8 times (1033 to 1035): 12% above GeoServer and 87% of MapServer.
- **TerraServe now uses the machine.** The release kept 15 of 32 threads busy while 32 clients
  waited. The branch keeps 26 to 29 busy, like the other two (29 to 30).
- **Every tile is the same tile.** The sample tile each run saves has the same md5 in all 16
  vector runs of the night: the release, the three commits, the memory-map variant, both
  rounds and the series below. Separately, on a laptop, 25 tiles (12 windows at 256 and 512
  px, one opaque) had the same md5 from the builds before and after the rule-evaluation
  commit.
- **Memory.** Under load the branch peaks at about 1 GB, where the release peaked at 0.56 GB.
  The likely reason: more requests really in flight at once. That is MapServer's level and
  half of GeoServer's (a JVM with `-Xmx4096m` grows into its heap, so its peak is policy as
  much as need). Thirty seconds after the load TerraServe holds 40 to 45 MB; MapServer holds
  1025 MB and GeoServer 900 to 980 MB. One round of the branch saw 1231 MB instead of 1026
  MB: "peak" is the highest of the 0.2 s samples of the container's own memory during the
  measured window, and a single sample can be that far off.
- **What each step was, and how it was found:**
  - The two SQLite locks: the profile and the scratch builds of the section above.
  - Rule evaluation: after those locks were gone, the profile had `Filter::eval` at 11.5% and
    number parsing at 8.1% of the server's CPU. This style has 108 rules that compare a text
    attribute with values like `1.1.1.1`, and for each rule the engine first tried to read
    that value as a number. It also selected the rules a second time per feature to place
    labels and markers the style does not have.
  - The axis order: found on the raster benchmark, where it was the whole ceiling
    (`../throughput/RESULTS.md`, "Why the TerraServe rows stopped growing"). Every WMS 1.3.0
    GetMap asked PROJ whether the CRS is northing-first, and all those questions queued on one
    lock inside PROJ. The vector GetMap goes through the same code.
- **The memory map was a decision, and it was taken on 2026-10-04: on by default.** It is
  the largest single step here after the locks (872 to 886 -> 1033 to 1035) and it lowers the
  peak (of the process's own memory, which is what this repo measures; the pages of the file
  are the kernel's file cache with and without the map, so a container limit does not drop
  by the difference). It also changes what a failing disk or a vanished network mount does:
  without it one request fails, with it the server process dies (how a memory map behaves on
  a read fault; not tested here). The branch first shipped it switched off for that reason.
  The owner chose on, with `TERRASERVE_GPKG_MMAP=0` to turn it off where the storage can
  fail under the server. So the row "with `TERRASERVE_GPKG_MMAP=1`" is what the branch does
  by default since commit `cf8d2ea`, and the row above it is what it does with the map
  switched off. A build of `cf8d2ea` itself was not measured: same read path, switched by
  the environment in these runs and by the default now.

The branch at three client counts, the machine's other workloads running, 15 s of warm-up and
60 s measured, single runs: the same conditions as the scratch-build table of the section
above, so the rows can be read against it.
`results/20261003-23*-moura001-vmsup-tmpfs-variant-branch-head-*/`.

| build | req/s, 8 clients | 16 clients | 32 clients | cores used at 8, 16, 32 | CPU per tile at 32 | p50 at 8, 16, 32 | peak memory at 32 |
|---|---|---|---|---|---|---|---|
| `ad47996` | 523.9 | 747.7 | 817.5 | 8.0, 16.1, 27.2 | 33.3 ms | 14, 20, 37 ms | 1237 MB |
| `ad47996` with `TERRASERVE_GPKG_MMAP=1` | 555.7 | 864.5 | 967.5 | 8.0, 15.5, 24.2 | 25.0 ms | 14, 18, 32 ms | 1044 MB |

For comparison, the control build of the release source under these conditions gave 303.8,
288.2 and 266.6 (the released image itself 301.6, 288.3 and 267.0): it did not grow with the
clients at all. The branch grows until the machine is full: at 32 clients it
uses 27 of 32 threads, and the load generator and the machine's other workloads need the
rest.

### Confirmation with the map on by default, 2026-10-04

The branch merged with the engine's `main` of that morning (commit `dcd4b2f`, a scratch image,
still not a release), the memory map on by default, nothing set in the environment. Same
machine, its other workloads off, GeoPackage from RAM, 32 clients, 30 s + 120 s, two rounds,
MapServer and GeoServer in the same session. The last row is the same image with
`TERRASERVE_GPKG_MMAP=0`. `results/20261004-09*-moura001-confirm-tmpfs-run[12]-vector-*/`.

| engine | req/s, round 1 | round 2 | p50 | p95 | cores used, of 32 | CPU per tile | peak memory | 30 s after the load |
|---|---|---|---|---|---|---|---|---|
| MapServer 8.6.6 | 1188.3 | 1187.8 | 26 ms | 40 ms | 29.9 | 25.2 ms | 1031, 1032 MB | 1026, 1023 MB |
| TerraServe, merged build, as it comes | 1034.6 | 1032.8 | 30 ms | 46 ms | 25.4, 25.5 | 24.6, 24.7 ms | 1135, 1109 MB | 39 MB |
| GeoServer 3.0.1 | 914.0 | 919.3 | 34 ms | 56 ms | 28.9 | 31.6, 31.5 ms | 1991, 1899 MB | 970, 908 MB |
| TerraServe, merged build, map switched off | 878.9 | 880.9 | 34 ms | 60 ms | 28.9, 29.0 | 32.9, 33.0 ms | 1234, 1271 MB | 44 MB |

- **It reproduces last night's rows**: 1033 and 1035 with the map on then, 1035 and 1033 now;
  872 and 886 with it off then, 879 and 881 now. MapServer and GeoServer are within 1% of
  their rows of last night.
- The sample tile has the same md5 as in every run of last night.
- The peak with the map on is 1109 to 1135 MB here, against 944 MB last night; "peak" is the
  highest single sample of the window and moves that much (see the note on memory above).

**What these numbers are not.** One machine, one dataset, one style, one tile size, and a
private branch against two engines as shipped. The rule-evaluation step is specific to
styles with many rules on a text attribute; a style with five numeric rules gains nothing
from it. The comparison with MapServer and GeoServer at 32 clients is now a comparison of
three engines that all fill the machine, with the load generator on the same machine.

