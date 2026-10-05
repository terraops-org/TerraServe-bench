---
concepts: [throughput-benchmark, mapserver, terraserve, memory-measurement]
status: active
confidence: measured
updated: 2026-10-04
corrections:
  - section: "What limits the two rows now"
    reason: "the interpretation that the tile LRU row pays for the allocator handing memory back every second was tested the same evening and is wrong: with a 10 s delay or with no hand-back at all, throughput and kernel time are unchanged. Dated note in the text; table in 'Three things measured the same evening'."
  - section: "How much CPU each engine uses under this load"
    reason: "it says the vector finding (SQLite) does not carry over because the raster path does not use SQLite. Wrong: every WMS 1.3.0 GetMap asked PROJ for the CRS's axis order, PROJ reads its database through SQLite, and that was the limit on both raster rows. Found and measured on the night of 2026-10-03 to 04, section 'Why the TerraServe rows stopped growing'."
  - section: "What the shape says"
    reason: "every table before the 2026-10-03 sections was measured with the method PR 1 replaced. A threaded client with 4 connections, a few hundred requests, MapServer with the GDAL block cache at its default of 5% of RAM per worker and the image's default pool of 5 workers. They show the harness ran; they are not an engine comparison."
  - section: "Throughput results"
    reason: "both TerraServe rows of every table ran with the WMS response cache at its 256 MiB default; the driver passed only --no-cache-lru or --cache-lru. In the two N=800 tables part of the requests repeat, so part of TerraServe's req/s there is a cache-hit rate."
---

# Throughput results

One run, 2026-09-04, one developer box. `N=300 CONC=4`, 256x256 WMS GetMaps at 600
distinct bboxes (panning), Cascais RGB COG, EPSG:3763. Memory is cgroup v2 `anon`.

**Which TerraServe:** this table used a private host build that self-reports 0.1.0. The
repo now runs the pinned public release `ghcr.io/terraops-org/terraserve:0.2.0`; a smoke
run and the full default run (`N=800`) with it are at the end of this file.

**Corrected 2026-10-03: the "no cache" rows were not cache-free.** `terraserve serve` has a
WMS response cache that defaults to 256 MiB (`--wms-cache`), and the driver passed only
`--no-cache-lru` or `--cache-lru 256`. Both TerraServe rows of every table in this file ran
with that cache on. In the `N=300` and `N=100` tables no request repeats (600 distinct
bboxes), so the cache could not answer anything; it only held memory. In the two `N=800`
tables the grid wraps: 200 of the 800 measured requests repeat an earlier one, and 300 once
the 100 warm-up requests were added, so part of TerraServe's req/s there is a cache-hit rate.
Found while reading [PR #1](https://github.com/terraops-org/TerraServe-bench/pull/1); the
driver passes `--wms-cache 0` to both rows since 2026-10-03.

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

## Response cache on and off, 2026-10-03, TerraServe 0.3.6

Not a full run: the two TerraServe rows only, to measure what `--wms-cache 0` changes (see the
correction at the top of this file). These are the first numbers in this file measured with
the load shape of [PR #1](https://github.com/terraops-org/TerraServe-bench/pull/1): 16 client
processes (one per host thread), 15 s of warm-up, 60 s measured, no repeated request, average
answer 129 KB. One developer laptop (Ryzen 7 8845HS, 8 cores, 16 threads), in interactive use
during the runs. Four runs, in this order: on, off, off, on. "On" is the code as merged
(`363a443`), "off" is the same code with `--wms-cache 0` on both rows.
`results/20261003-wmscache-{on,off,off-2,on-2}/` on the owner's box; `results/` is not in
the repo. Every run answered every request with a PNG.

| row | response cache | req/s | baseline | peak | settle 2 / 10 / 30 s |
|---|---|---|---|---|---|
| TerraServe 0.3.6 (no LRU) | on, run 1 | 590.9 | 528.5 MB | 856.8 MB | 521.9 / 518.8 / 512.3 MB |
| TerraServe 0.3.6 (no LRU) | on, run 2 | 591.5 | 539.9 MB | 860.6 MB | 511.8 / 510.0 / 503.7 MB |
| TerraServe 0.3.6 (no LRU) | off, run 1 | 596.2 | 257.7 MB | 557.6 MB | 211.4 / 211.0 / 203.2 MB |
| TerraServe 0.3.6 (no LRU) | off, run 2 | 311.8 | 248.2 MB | 566.5 MB | 221.7 / 221.6 / 213.7 MB |
| TerraServe 0.3.6 (LRU 256) | on, run 1 | 1081.7 | 696.9 MB | 885.1 MB | 686.4 / 686.4 / 680.7 MB |
| TerraServe 0.3.6 (LRU 256) | on, run 2 | 1063.6 | 718.9 MB | 892.3 MB | 692.0 / 691.7 / 686.2 MB |
| TerraServe 0.3.6 (LRU 256) | off, run 1 | 864.5 | 422.3 MB | 622.3 MB | 402.7 / 402.7 / 396.2 MB |
| TerraServe 0.3.6 (LRU 256) | off, run 2 | 1014.2 | 455.0 MB | 632.9 MB | 436.4 / 432.3 / 426.3 MB |

**Memory: the response cache was about 260 to 310 MB of TerraServe's number.** With it off,
both rows hold that much less at the baseline, at the peak and 30 s after the load, in both
runs. At these rates a 256 MiB cache fills within seconds. No request repeats, so it can
never answer one; it was only being counted as memory TerraServe needs.

**Throughput: no conclusion.** The two cache-on runs agree within 2%. The two cache-off runs
do not agree with each other: the no-LRU row gave 596.2 and then 311.8 req/s, the LRU row
864.5 and then 1014.2. A cache that cannot hit has no way to make a request faster, and the
no-LRU row of the first cache-off run matches the cache-on runs. Four runs cannot tell whether
something else used the laptop's CPU during those two rows or whether TerraServe is less
steady with the cache off. The harness does not record how much CPU each engine got, which is
the reading that would tell the two apart. Do not read a throughput effect out of this table.

This laptop also runs with transparent huge pages set to `always`, which inflates TerraServe's
memory (`../render/GEOSERVER.md`, 2026-10-03). The difference between the rows is what this
table shows; the absolute values belong to that host setting.

## Two full runs on a second machine, 2026-10-03 (new method, new pins)

The first complete numbers with the method of
[PR #1](https://github.com/terraops-org/TerraServe-bench/pull/1), and the first where the
TerraServe rows have the response cache off. Two full default runs of the suite back to back
on a desktop-class server: Ryzen 9 7950X, 16 cores and 32 threads, 124 GB, Ubuntu 24.04,
kernel 6.8.0, Docker 29.8.1, transparent huge pages `madvise`, `amd-pstate-epp` with the
performance preference. Its other workloads were stopped for the runs. A file transfer of
another project was using about 15% of one core when run 1 started and ended on its own
during run 2.
32 client processes (one per host thread), 30 s of warm-up and 120 s measured per engine,
256x256 GetMaps, no repeated request. MapServer with 32 FastCGI workers, recycled every 10000
requests, `GDAL_CACHEMAX=16`; GeoServer `-Xms256m -Xmx4096m` with a periodic G1 collection, GWC
off. Code: `main` at `363a443` plus that day's changes (the pins, `--wms-cache 0`), the exact
diff saved next to the results. `results/20261003-143704-moura001-run1/` and
`results/20261003-150533-moura001-run2/`, not in the repo. Every engine answered every request
with a PNG (100,150 to 166,076 per engine and run). Every cell is run 1 / run 2.

| engine | req/s | avg PNG | baseline | peak | settle 2 s | settle 10 s | settle 30 s |
|---|---|---|---|---|---|---|---|
| MapServer 8.6.6 | 1341.5 / 1383.6 | 130.7 KB | 1106 / 1108 MB | 1116 / 1122 MB | 1113 / 1120 MB | 1113 / 1120 MB | 1113 / 1120 MB |
| TerraServe 0.3.7 (no cache) | 834.3 / 844.0 | 129.0 KB | 112 / 106 MB | 588 / 582 MB | 55 / 58 MB | 53 / 53 MB | 41 / 42 MB |
| TerraServe 0.3.7 (LRU 256) | 1168.2 / 1149.6 | 129.0 KB | 257 / 241 MB | 549 / 567 MB | 192 / 195 MB | 192 / 194 MB | 180 / 182 MB |
| GeoServer 3.0.1 | 969.8 / 1004.4 | 115.5 KB | 1262 / 1389 MB | 2606 / 2761 MB | 1838 / 1833 MB | 859 / 1833 MB | 861 / 921 MB |
| GeoServer 3.0.1 + gs-libdeflate | 1095.2 / 1122.4 | 115.5 KB | 1925 / 1570 MB | 2789 / 2819 MB | 1171 / 1040 MB | 1171 / 1040 MB | 950 / 933 MB |

- **The two runs agree within 4% on req/s for every engine.** That is the noise to keep in
  mind: a difference smaller than that between two rows is not a difference.
- **Throughput:** MapServer first. TerraServe with its tile LRU and GeoServer with libdeflate
  come next, close to each other. TerraServe without any cache is last, about 15% behind
  plain GeoServer.
- **Memory after the load:** TerraServe gives almost everything back, 41 MB held 30 s later
  (180 MB with the LRU, which is the cache doing its job). GeoServer falls from 2.6 to 2.8 GB
  at the peak to about 0.9 GB once the JVM collects, and how fast it does so differs between the
  runs. MapServer keeps its 1.1 GB: 32 workers that do not shrink.
- **Deflate:** GeoServer gains about 12% from libdeflate (12.9% and 11.7%). MapServer's GDAL is linked against
  libdeflate too (`ldd` on the pinned image). TerraServe 0.3.7 inflates COG tiles with the
  default pure-Rust backend of `flate2` (`src/decode.rs` and `Cargo.lock` at its tag `v0.3.7`); the same swap is open to it and its effect is not measured.

### The response cache on and off, same machine, same day

The laptop table further up left the throughput question open. Here it is again on the
machine where runs repeat: the two TerraServe rows, 32 clients, 15 s of warm-up and 60 s
measured, order on, off, off, on. "On" is the same code without `--wms-cache 0`.
`results/20261003-15*-moura001-wmscache-{on1,off1,off2,on2}/`. Cells are first / second run.

| row | response cache | req/s | baseline | peak | settle 30 s |
|---|---|---|---|---|---|
| TerraServe 0.3.7 (no LRU) | on | 828.8 / 832.2 | 396 / 380 MB | 915 / 884 MB | 345 / 347 MB |
| TerraServe 0.3.7 (no LRU) | off | 847.3 / 845.5 | 91 / 86 MB | 574 / 600 MB | 41 / 42 MB |
| TerraServe 0.3.7 (LRU 256) | on | 1131.9 / 1193.0 | 503 / 508 MB | 832 / 843 MB | 477 / 480 MB |
| TerraServe 0.3.7 (LRU 256) | off | 1214.6 / 1212.0 | 238 / 257 MB | 616 / 631 MB | 182 / 184 MB |

- **Memory:** the cache is about 300 MB 30 s after the load (295 to 305 MB), and 210 to 340 MB
  at the baseline and at the peak.
- **Throughput:** turning it off costs nothing. The no-LRU row is about 2% faster without it,
  the LRU row 2 to 7%. Nothing like the laptop's half-rate run appeared here. The laptop runs
  were on 0.3.6 and these on 0.3.7, so this does not prove where that run came from; it does
  show the cache is not needed for speed.

### How much CPU each engine uses under this load

One more run on the same machine and day, 32 clients, 15 s of warm-up and 60 s measured, with
each container's cgroup `cpu.stat` read every 2 s by a side script (not part of the harness
yet). "Cores" is the median CPU use while the engine was under load, "CPU per tile" is cores
divided by req/s. `results/20261003-154821-moura001-cpu-raster/`.

| engine | req/s | cores used, of 32 | of which kernel | CPU per tile |
|---|---|---|---|---|
| MapServer 8.6.6 | 1378.5 | 29.0 | 1.4 | 21.1 ms |
| TerraServe 0.3.7 (no cache) | 846.5 | 17.0 | 4.4 | 20.1 ms |
| TerraServe 0.3.7 (LRU 256) | 1220.1 | 6.5 | 2.5 | 5.4 ms |
| GeoServer 3.0.1 | 1004.4 | 25.8 | 1.7 | 25.7 ms |

**TerraServe's CPU per tile is on a par with MapServer's (20.1 against 21.1 ms) and it uses
the least of the machine.** MapServer and GeoServer keep 26 to 29 of the 32 threads busy. TerraServe without a cache stops at 17, and
with its LRU at 6.5, while 32 clients wait. Its throughput is not held by what a render
costs. Something inside the server keeps it from running more of them at once, and what
that is has not been established. It is not the admission limit: `--max-inflight` defaults to
twice the cores (its `--help`), 64 here. The raster path was not profiled. On
the vector benchmark a profile of the same day found the limit in SQLite
(`../vector/RESULTS.md`), which the raster path does not use, so that finding does not carry
over.

**Corrected 2026-10-04: the last sentence is wrong.** The raster path does reach SQLite, through
PROJ, on every request, and that was the limit. See "Why the TerraServe rows stopped growing"
at the end of this file. The table and the "something inside the server" reading stand.

One number here is specific to TerraServe: it spends 26% of its CPU in the kernel on this
benchmark (4.4 of 17.0 cores), against 5% for MapServer and 7% for GeoServer. A profile
should start there. It is not the storage: see the control below.

### The storage under the COG: a control

On this machine the fixtures sat on a ZFS dataset with compression on and 1 MB records. On the
vector benchmark that cost MapServer about half its speed (`../vector/RESULTS.md`). Here it
changes nothing. Same day, single runs, 32 clients, 15 s of warm-up and 60 s measured, the
machine's other workloads running this time: once with the COG served from RAM (tmpfs), once
from ZFS. `results/20261003-17*-moura001-vmsup-{tmpfs,zfs}-raster-conc32/`.

| engine | req/s, COG on ZFS | req/s, COG from RAM | cores used and kernel cores, from RAM |
|---|---|---|---|
| MapServer 8.6.6 | 1270.8 | 1278.2 | 26.8, 1.4 |
| TerraServe 0.3.7 (no cache) | 782.3 | 780.8 | 16.0, 4.2 |
| TerraServe 0.3.7 (LRU 256) | 1103.5 | 1112.2 | 6.1, 2.3 |
| GeoServer 3.0.1 | 947.6 | 937.9 | 24.2, 1.6 |

- **The raster numbers of this machine do not depend on the storage.** Every engine is within
  about 1% between the two.
- **TerraServe's kernel time is not ZFS:** 4.2 kernel cores from RAM, 4.1 from ZFS.
- These rows are 2 to 8% under the two full runs above because the machine's other workloads
  were running.

## Why the TerraServe rows stopped growing: found on the night of 2026-10-03 to 04

Same machine as the section above. The question it left open: TerraServe keeps 6 to 17 of 32
threads busy while 32 clients wait, so what is it waiting for?

### The symptom, at three client counts

The `v0.3.7` source built with the release `Dockerfile`, the COG served from RAM, the
machine's other workloads running, 15 s of warm-up and 60 s measured, single runs.
`results/20261003-22*-moura001-vmsup-tmpfs-rastervariant-rebuild-*/`.

| row | req/s, 8 clients | 16 clients | 32 clients | cores used at 8, 16, 32 |
|---|---|---|---|---|
| no cache | 687.0 | 789.2 | 783.2 | 11.6, 16.5, 16.1 |
| tile LRU | 1199.0 | 1185.8 | 1114.8 | 5.7, 6.9, 6.2 |

With the tile LRU the server answers no more requests for 32 clients than for 8.

### What it was not: the thread pools

A first suspect, from reading the source: every request hands its tile reads to an I/O
thread pool and its tile decoding to a second pool, even when it has two tiles to read or
none. A scratch build that reads and decodes on the request's own thread when a request
touches 8 tiles or fewer (every request of this benchmark; 75 changed lines), against the
control above, alternating at each client count. Six fixed tiles, without and with the LRU,
have the same md5 from both builds.
`results/20261003-22*-moura001-vmsup-tmpfs-rastervariant-rasterserial-*/`.

| row | build | req/s, 8 clients | 16 clients | 32 clients |
|---|---|---|---|---|
| no cache | control | 687.0 | 789.2 | 783.2 |
| no cache | no pools for small requests | 563.1 | 851.7 | 843.9 |
| tile LRU | control | 1199.0 | 1185.8 | 1114.8 |
| tile LRU | no pools for small requests | 1196.0 | 1202.7 | 1121.3 |

- **Not the cause.** The LRU row does not move at any client count. The no-cache row gains 8%
  at 16 and 32 clients and loses 18% at 8, likely because there the pools let one request
  use cores that would otherwise sit idle.
- The suspect came from reading code and was wrong, which is why the next step measured where
  the threads actually were.

### What it was: one question to PROJ on every request

A CPU profile shows where a program burns CPU, not where it sleeps, and this server was
mostly asleep. So, under the same load at 32 clients, the server was stopped 25 times, one
second apart, and every thread's stack (`eu-stack`) and kernel state (`/proc/1/task/*/stat`)
written down. `results/20261003-23*-moura001-vmsup-tmpfs-rasterwait-rebuild-*/`.

| row | request threads per sample | asleep | inside a mutex lock in SQLite | of those, reached from the axis-order question |
|---|---|---|---|---|
| tile LRU | 31.6 | 632 of 791 samples (80%) | 655 | 643 (98%) |
| no cache | 33.3 | 662 of 832 samples (80%) | about two thirds of the samples | not counted separately |

On the no-cache row another 12% of the samples were waiting for the decode pool.

- **Measured:** the stack above almost every blocked thread is `proj_create` <-
  `reproj::crs_is_northing_first` <- `wms::parse_map_frame` <- `wms::get_map`.
- **Read from the sources:** WMS 1.3.0 orders a BBOX by the CRS's own axis order, so TerraServe
  asks PROJ, on every GetMap, whether the CRS is northing-first. PROJ answers from its
  database, `proj.db`, and it keeps one SQLite connection per database file, shared by every
  PROJ context in the process and opened in SQLite's serialized mode (PROJ 9.6 `factory.cpp`,
  `SQLiteHandleCache`). So 32 requests asking the same question queue on that one
  connection's mutex.
- The system calls agree: 4 `openat` per response, half of them failing (counted in a 6 s
  `strace -c` against the `writev` calls of the same trace, one per response). That is
  consistent with PROJ looking for `proj.db` and `proj.ini` for each new context, which is
  what its source does; `strace -c` records no paths.
- **Not measured, read from how it is deployed:** MapServer serves from separate processes, and
  each process has its own PROJ, so its requests do not share one such connection.

### A side finding: PROJ runs on the SQLite inside the TerraServe binary

`perf` showed SQLite functions inside the `terraserve` binary on a server that has no vector
layer. The binary bundles its own SQLite for GeoPackages, and it exports the 42 `sqlite3_*`
functions that `libproj.so` imports (`nm -D` on the release-recipe binary), so the dynamic
linker binds PROJ to the bundled SQLite and not to the system's `libsqlite3.so`. The two
compile options that fixed the vector path (`../vector/RESULTS.md`) therefore reach PROJ too.
The scratch build of the vector section that has only those two options, against a control
of the same session. `results/20261003-23*-moura001-vmsup-tmpfs-rastervariant-{noshared-nostat,rebuild2}-*/`.

| row | build | req/s, 8 clients | 32 clients |
|---|---|---|---|
| no cache | control | 686.8 | 783.7 |
| no cache | SQLite without its two process-wide locks | 701.4 | 822.6 |
| tile LRU | control | 1196.3 | 1108.1 |
| tile LRU | SQLite without its two process-wide locks | 1263.7 | 1210.6 |

2 to 9% more. The compile options take two locks away; the connection's own mutex, the one
the threads above were queueing on, stays.

### With the question asked once: two clean runs

**Not a release.** Scratch images built with the release `Dockerfile` from two states of a
private branch of the engine (`perf/sqlite-locks`, not merged, not published): `fff5d02` has
only the SQLite compile options, `ad47996` also remembers the axis order per CRS, so PROJ is
asked once per CRS and not once per request. The machine's other workloads shut down, the COG
served from RAM, default settings (32 clients, 30 s of warm-up and 120 s measured), two
rounds. `results/2026100[34]-*-moura001-clean-tmpfs-run[12]-raster-*/`.

| row | build | req/s, round 1 | round 2 | cores used, of 32 | CPU per tile | peak memory | 30 s after the load |
|---|---|---|---|---|---|---|---|
| no cache | released 0.3.7 | 841.0 | 841.3 | 17.2, 17.1 | 20.5, 20.3 ms | 644, 613 MB | 42, 43 MB |
| no cache | `fff5d02` | 874.5 | 878.8 | 18.8 | 21.5, 21.3 ms | 690, 612 MB | 44, 43 MB |
| no cache | `ad47996` | 1113.1 | 1112.2 | 28.6 | 25.7 ms | 751, 764 MB | 42 MB |
| tile LRU | released 0.3.7 | 1217.0 | 1217.5 | 6.6 | 5.4 ms | 570, 568 MB | 181, 182 MB |
| tile LRU | `fff5d02` | 1293.5 | 1321.7 | 7.3, 7.4 | 5.7, 5.6 ms | 549, 569 MB | 180, 182 MB |
| tile LRU | `ad47996` | 2164.9 | 2166.1 | 24.8, 24.7 | 11.5, 11.4 ms | 583, 618 MB | 183, 185 MB |

- **No cache: 841 -> 1113 req/s, 32% more. Tile LRU: 1217 -> 2165, 78% more.** The two rounds
  agree within 0.1% on the released and the final build.
- **MapServer and GeoServer were not rerun in this session.** Their rows in the two full runs
  of 2026-10-03 on this machine (other workloads also shut down; the COG on ZFS, which the
  control above showed makes no difference to raster): MapServer 1341.5 and 1383.6, GeoServer
  969.8 and 1004.4, GeoServer with libdeflate 1095.2 and 1122.4. The released TerraServe rows
  of those two runs (834.3 and 844.0, 1168.2 and 1149.6) are within 1% of tonight's on the
  no-cache row and within 6% on the LRU row, so the sessions can be read side by side, with
  a 6% margin. Read that way, the branch without a
  cache is above GeoServer, level with GeoServer's libdeflate row and below MapServer, and
  with its tile LRU it is 1.6 times MapServer.
- **The CPU per tile went up**, from 5.4 to 11.5 ms on the LRU row and from 20.5 to 25.7 ms
  on the no-cache row. A likely part of the reason, not shown here: the server now runs 25
  to 29 threads at once on a machine with 16 physical cores, where it ran 7 to 17 before, and
  two threads of one core are counted as two. The kernel's share of that CPU also rose (2.5
  to 10.3 of the cores on the LRU row), which the last section looks at.
- **Memory:** the no-cache peak rises from 613 to 644 MB to 751 to 764 MB, more requests in
  flight at once. Thirty seconds after the load nothing changed.

### What limits the two rows now

The branch at three client counts, other workloads running, 15 s + 60 s, single runs.
`results/20261003-23*-moura001-vmsup-tmpfs-rastervariant-branch-head-*/`.

| row | req/s, 8 clients | 16 clients | 32 clients | cores used at 8, 16, 32 |
|---|---|---|---|---|
| no cache | 817.9 | 1044.9 | 1036.4 | 13.0, 22.3, 25.9 |
| tile LRU | 2128.9 | 2214.2 | 2031.7 | 7.6, 14.8, 21.5 |

The LRU row is flat again, at twice the old level, and now its CPU triples from 8 to 32
clients for the same throughput. A profile and a second round of stack samples of the branch
at 32 clients. `results/20261004-03*-moura001-vmsup-tmpfs-{rasterprofile,rasterwait}-branch-head-*/`.

- **Tile LRU row, measured:**
  - Only 14 of the 32 requests are inside the server's WMS code at any instant (14.2 request
    threads per sample, 31.6 before; a thread counts when a WMS function is on its stack).
    Where the rest of each round trip goes was not measured. The likely places are the load
    generator and Docker's port proxy, which run on the same machine. If so, this row is now
    partly a measurement of the benchmark itself.
  - Inside the server, 46% of the CPU is kernel time. The largest items are the kernel zeroing
    fresh pages (`clear_page_erms`, 13.9%) and the server zeroing its new buffers
    (`vec![0; n]`, 13.0%), and 10.7% of the request-thread samples were inside `madvise`,
    the allocator handing pages back to the system.
  - The `openat` calls are gone, and `futex` calls per response fell about six-fold (same
    6 s `strace -c` method; tracing slows the server, so only the ratio is worth reading).
- **Corrected 2026-10-04 (evening): the interpretation below was tested and is wrong.** Giving
  memory back later, or never, changes neither the throughput nor the kernel time. See "Three
  things measured the same evening" at the end of this file.
- **Tile LRU row, interpretation, not tested:** every request gets fresh memory, pays for it
  to be zeroed twice, and gives it back within a second. That is TerraServe's allocator
  setting doing what it is set to do: it is why its memory falls right after the load. It is
  a trade between footprint and throughput, and no build without it was measured.
- **No-cache row, measured:** it is CPU-bound in tile decoding now. Inflate
  (`miniz_oxide`) is 21.7% of the CPU and the tile decode around it 16.3%; half of the
  request-thread samples are waiting for the decode pool, which is busy, not locked. The
  server uses 26 to 29 of 32 threads.
- **No-cache row, not tested:** a faster inflate. GeoServer gained 10 to 13% from libdeflate
  on this benchmark (the libdeflate row of the full runs above).

**What these numbers are not.** One machine, one COG, one tile size, requests in the data's
own CRS, a private branch against two engines as shipped and measured in another session.
A request that needs a reprojection still builds its PROJ objects each time and was not
measured here.

## Requests that need a reprojection: a side measurement, 2026-10-04

Every benchmark of this repo asks in the data's own CRS (EPSG:3763). Web clients mostly ask
in EPSG:3857, and then the server has to reproject. This is NOT a harness run and compares
TerraServe only with itself: a small forked load generator (keep-alive, every request a new
window, the benchmark's windows converted to EPSG:3857), 10 s of warm-up and 40 s measured,
single runs, the machine's other workloads off, fixtures from RAM, cores from the container's
cgroup. Two scratch builds of the engine's private branch: before and after one change.
`results/20261004-17*-moura001-tmpfs-reprojprobe-{merged,reprojfix}/`, scripts in
`results/20261004-night-scripts/reproj/`.

| row | CRS asked | build | req/s, 8 clients | 32 clients | cores used at 32 | p50 at 32 |
|---|---|---|---|---|---|---|
| raster, tile LRU | EPSG:3763 | before | 1978 | 2170 | 24.9 | 14 ms |
| raster, tile LRU | EPSG:3857 | before | 400 | 851 | 18.2 | 36 ms |
| raster, tile LRU | EPSG:3857 | after | 627 | 1585 | 27.6 | 20 ms |
| raster, no cache | EPSG:3763 | before | 834 | 1106 | 28.7 | 27 ms |
| raster, no cache | EPSG:3857 | before | 308 | 607 | 22.9 | 51 ms |
| raster, no cache | EPSG:3857 | after | 437 | 874 | 28.9 | 35 ms |
| vector (COS2023) | EPSG:3763 | before | 541 | 1045 | 26.4 | 30 ms |
| vector (COS2023) | EPSG:3857 | before | 155 | 353 | 20.9 | 90 ms |
| vector (COS2023) | EPSG:3857 | after | 232 | 572 | 29.9 | 55 ms |

- **Measured before the change** (15 samples of every thread's stack at 32 clients, EPSG:3857):
  41 to 50% of the request threads on the raster rows and 44% on the vector row were building
  a PROJ transformation, the same one for every request, and 15 to 20% of all request threads
  were blocked on PROJ's one SQLite connection while doing it. Read from the sources of PROJ
  and of the Rust `proj` crate: throwing such an object away also empties PROJ's process-wide
  caches, so every request made every thread reopen PROJ's database.
- **The change:** the transformation is built once per thread and reused (commit `0b248ff` of
  the private branch; the vector render path already did this, the raster path and the
  vector bounding-box filter did not).
- **After:** 44 to 86% more requests in EPSG:3857 at 32 clients, the server uses 28 to 30 of
  32 threads, and no sampled thread is building a transformation. The EPSG:3763 rows did not
  move (2168, 1115 and 1051 req/s at 32 clients with the new build). 35 raster and 25 vector
  answers in EPSG:3857 and EPSG:4326 have the same md5 from both builds (checked on a laptop,
  each window asked twice).
- **What a reprojection still costs, measured:** after the change, 37 to 60% of the request
  threads are transforming coordinates, one PROJ call per output pixel or per vertex. That
  is the work itself. The windows cover the same ground in both CRSs (the Mercator ones
  about 3% more), so the remaining distance to the EPSG:3763 rows is the reprojection, not
  more data.
- **Repeated on the commit the branch now has** (`55e78f0`, the same change after its review;
  `...-reprojprobe-reprojfix2/`), EPSG:3857 at 8 and 32 clients: raster with the tile LRU 636
  and 1595 req/s, without a cache 431 and 860, vector 232 and 566. Within 2% of the "after"
  rows above.
- **Not measured:** MapServer and GeoServer in EPSG:3857. This says nothing about how
  TerraServe compares with them when reprojecting.

### The reprojection computed on a coarse grid and interpolated

The owner's decision of the same evening, for raster: PROJ is asked on a grid of 16 output
pixels and the pixels in between are interpolated, each grid cell checked against the exact
transform and computed exactly when it is off by more than an eighth of what an output pixel
covers. **This changes pixels**, for reprojected raster output only. Same probe, the commit
with that change and its review (`f9110aa`). `...-reprojprobe-warpgrid2/`.

| row | CRS asked | req/s, 8 clients | 32 clients | cores used at 32 |
|---|---|---|---|---|
| raster, tile LRU | EPSG:3763 | 1982 | 2189 | 25.0 |
| raster, tile LRU | EPSG:3857, per-pixel transform (the row "after" above) | 627 | 1585 | 27.6 |
| raster, tile LRU | EPSG:3857, interpolated | 1797 | 2128 | 24.9 |
| raster, no cache | EPSG:3763 | 839 | 1121 | 28.7 |
| raster, no cache | EPSG:3857, per-pixel transform | 437 | 874 | 28.9 |
| raster, no cache | EPSG:3857, interpolated | 791 | 1084 | 28.4 |

- **A raster request in EPSG:3857 now costs what one in the data's own CRS costs**, within 3%
  at 32 clients and within 10% at 8, at this zoom.
- **How much the pixels change, measured:** one binary, exact transform against
  interpolated, 31 tiles (27 in EPSG:3857 at 256 px, one at 512 px, two in EPSG:4326, one in
  EPSG:3763): 24 tiles byte-identical, 14 of 2,424,832 pixels differ (0.001%), single pixels
  whose source position sat on the border between two source pixels; the largest difference
  in a channel is 63 of 255. The EPSG:3763 tile is identical. The engine's six reprojection
  test images, which come from GDAL, score what they scored before.
- Vector is not touched: it transforms per vertex, exactly (232 and 570 req/s in EPSG:3857).
- Measured only at this zoom and on this small area. A window with a pole or the antimeridian
  in it is covered by the engine's tests (cells there are computed exactly), not by this probe.

### Three things measured the same evening that did NOT move the numbers, or barely

Same machine and method, requests in EPSG:3763, single runs.
`results/20261004-moura001-tmpfs-variantprobe/variants.jsonl`.

| what was tried | row | req/s, 8 clients | 32 clients | kernel cores at 32 | peak memory | 30 s after |
|---|---|---|---|---|---|---|
| as built (allocator gives memory back after 1 s) | tile LRU | 1974, 1989 | 2185, 2184 | 10.3 | 548, 597 MB | 177 MB |
| allocator gives memory back after 10 s | tile LRU | 1979 | 2180 | 10.1 | 630 MB | 178 MB |
| allocator never gives memory back | tile LRU | 1976 | 2162 | 10.0 | 819 MB | 819 MB |
| as built | no cache | 839, 837 | 1115, 1114 | 9.2 | 877, 780 MB | 38 MB |
| inflate with `zlib-rs` instead of `miniz_oxide` (scratch build) | no cache | 898 | 1141 | 10.0 | 869 MB | 37 MB |

- **The allocator setting is not what limits the tile LRU row.** The earlier section read the
  kernel time as the allocator handing pages back every second ("interpretation, not
  tested"). Tested now: with a ten times longer delay, or never, the throughput and the
  kernel time are the same, and "never" only keeps 819 MB. That interpretation was wrong.
  What the 10 kernel cores are is not established.
- **A faster inflate helps a little:** 7% at 8 clients, 2% at 32, with byte-identical tiles
  (15 checked). At 32 clients the row already uses 28.5 of 32 threads. libdeflate was not
  tried.
- A third probe, of the DGGS service's zone data, measured that service's own response cache
  and says nothing about the engine; it is not reported.


### Two routes outside the benchmark, before and after one more engine change, 2026-10-05

Not rows of this benchmark: a side measurement of two routes that asked PROJ for a CRS's PROJ.4
string on every request, on the engine commit before that string was remembered (`ef5de4c`) and
the one with it (`743623a`, private repo). Same machine (moura001, 32 threads, VMs off, COG from
RAM), the side load generator on the same host, 10 s warm-up and 40 s measured, a fresh
container per run, order before, after, after, before. Scripts and raw output:
`results/20261005-proj4memo/`.

| route | clients | before, req/s | after, req/s | server cores, before -> after |
|---|--:|--:|--:|--:|
| `GET /tileMatrixSets/WebMercatorQuad` (a 4 kB JSON document) | 32 | 1937, 1968 | 115594, 114863 | 2.4 -> 6.1 |
| same | 8 | 2295, 2295 | 57828, 58255 | 1.9 -> 2.6 |
| DGGS zone data, response cache off (1 MB of JSON per zone) | 16 | 82.1, 81.6 | 83.7, 83.9 | 15.7 -> 15.9 |
| same | 4 | 22.1, 22.2 | 22.5, 22.5 | 4.0 -> 4.0 |

- Same bytes before and after on both routes (md5 of one response each).
- **The document route was waiting, not working.** Before, in 12 samples of every thread's
  stack at 32 clients, 31 of 37 threads stood in that one function, 29 of them waiting on a
  lock. After, none. The figures after are a floor, not the server's limit: it was on 6 of 32
  cores, and the clients are single-threaded Python processes on the same machine (their CPU
  was not recorded).
- **The zone data route is the first measurement of the zonal sampler here** (the probe of
  2026-10-04 had measured the service's response cache). With the cache off
  (`data.cache_mb: 0`, `data.max_in_flight: 32`), 60 distinct ISEA3H zones of level 20: one
  core per request in flight, 180 ms per zone with 4 clients and 190 with 16 (medians). The remembered string was about 1% of
  that work and moves the row by about 2%: every run after is above every run before, which
  is as much as two runs can say.
