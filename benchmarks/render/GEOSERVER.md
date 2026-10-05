---
concepts: [geoserver, render-benchmark, memory-measurement, correctness-canary]
status: active
confidence: measured
updated: 2026-10-03
corrections:
  - section: "The baseline is not a tuning artifact."
    reason: "the -Xms experiment edited GEOSERVER_JAVA_OPTS, which the osgeo image does not read, so both runs had the same heap settings and it tested nothing. Whether -Xms moves the baseline is untested."
  - section: "Comparison Results"
    reason: "the GeoServer rows of both tables are labelled -Xms1g -Xmx4g. That was what the compose file asked for; the JVM ran on the image default, -Xms256m -Xmx1g. The measured numbers stand, the label does not."
  - section: "TerraServe uses about 5.9x MapServer's memory per render here"
    reason: "the 254 MB is what the render costs on a host with transparent huge pages set to always. With THP off for the process the same render costs 54 MB against MapServer's 45. The floor is not the COG open path. See the 2026-10-03 section."
  - section: "Could not find layer benchmarks:cascais_rgb_cog"
    reason: "the catalog was lost because the data volume was mounted at a path the image does not use, not because the COG was unreachable at start. The mount is fixed since PR 1."
---

# GeoServer Benchmarking

Automated setup and benchmarking of GeoServer WMS performance against MapServer and TerraServe.

## Quick Start

### 1. Start GeoServer Stack

```bash
cd ../..
docker compose up -d geoserver postgis
# Wait 30-60s for GeoServer startup
```

Check status:
```bash
docker compose logs geoserver | grep "Started Application"
```

### 2. Run Render Benchmark

```bash
cd benchmarks/render
./run.sh
```

This will:
1. Health-check GeoServer at `http://localhost:8080/geoserver`
2. Switch logging to `PRODUCTION_LOGGING` (the image starts with `DEFAULT_LOGGING`, which logs every request)
3. Create workspace `benchmarks` via REST API
4. Create coverage store for Cascais RGB COG
5. Publish coverage layer `cascais_rgb_cog`
6. Run 6 timed WMS GetMap requests
7. Report best/median time and output size

## How It Works

### GeoServer REST API Setup

`geoserver-setup.py` automates layer creation:

```python
# Creates workspace
POST /geoserver/rest/workspaces

# Creates coverage store (mounts COG file)
POST /geoserver/rest/workspaces/benchmarks/coveragestores

# Creates layer and publishes it
POST /geoserver/rest/workspaces/benchmarks/coveragestores/.../coverages
```

Idempotent: safe to run multiple times (existing layers are skipped).

### WMS GetMap Benchmarking

`geoserver-benchmark.py` issues GetMap requests:

```
GET /geoserver/ows?
  service=WMS&version=1.3.0&request=GetMap&
  layers=benchmarks:cascais_rgb_cog&
  crs=EPSG:3763&
  bbox=minx,miny,maxx,maxy&
  width=800&height=536&
  format=image/png
```

Measures:
- Response time per request
- Output validation (PNG magic bytes)
- Best/median/tail latency across N runs

## Configuration

### Environment Variables

```bash
# GeoServer URL (default: http://localhost:8080/geoserver)
GS_URL=http://my-geoserver:8080/geoserver ./run.sh

# Output dimensions
W=1024 H=768 ./run.sh

# Warmup + timed runs
python3 geoserver-benchmark.py --warmup 2 --runs 10
```

### Custom Credentials

If GeoServer has non-default admin credentials:

```bash
python3 geoserver-setup.py \
  --gs-url http://localhost:8080/geoserver \
  --user myuser --passwd mypass \
  --cog-cascais /path/to/cascais.cog.tif
```

## Output

A real run, captured 2026-09-04 (a different run from the table in Comparison Results, same day and build; see there for the caveats):

```
[3/3] GeoServer (docker.osgeo.org/geoserver:2.26.1)
    Checking GeoServer at http://localhost:8080/geoserver  ok
    Setting up layers...
[OK] GeoServer 2.26.1 is running
[*] Creating workspace 'benchmarks'...
    [SKIP] Workspace already exists
[*] Creating coverage store 'cascais-rgb'...
    [OK] Coverage store ready
[*] Creating coverage layer 'cascais_rgb_cog'...
    [OK] Coverage layer ready
    Layers ready.
    Running WMS GetMap benchmark...

GeoServer WMS benchmark:
  Layer: benchmarks:cascais_rgb_cog
  Size: 800x536
  Best:       64.6ms
  Median:     82.5ms
  Output:   911482B
```

## Troubleshooting

### GeoServer not accessible

```bash
# Check container is running
docker compose ps geoserver

# View logs
docker compose logs geoserver | tail -50

# Manually test connection
curl http://localhost:8080/geoserver/web/

# If stuck on startup, restart
docker compose restart geoserver
docker compose logs -f geoserver
```

### "Could not find layer benchmarks:cascais_rgb_cog"

The catalog does not always survive the container being recreated, even though the
data directory is a named volume: if the COG is unreachable at the moment GeoServer
starts, it can drop the store and persist that. Re-running the setup brings it back,
which is why `run.sh` runs `geoserver-setup.py` on every single invocation instead of
only the first. Do not "optimise" that call away.

**Corrected 2026-10-03.** The cause given above was a guess, and it was wrong. The compose
file mounted the named volume at `/opt/geoserver/data_dir`, while the image keeps its data in
`/opt/geoserver_data/`, so the catalog lived inside the container and went with it. The mount
is right since [PR #1](https://github.com/terraops-org/TerraServe-bench/pull/1). The setup
still runs on every invocation, which costs little.

```bash
python3 geoserver-setup.py --cog-cascais ./data/cascais.cog.deflate.tif
```

### The memory column says "unavailable"

`lib/cgroup_mem.py` needs cgroup v2 and a Docker driver that puts containers under a
readable cgroup path. It prints every path it tried. It has been run against the
systemd driver only; cgroup v1, rootless Docker and Podman are untested and will
most likely need another entry in `CGROUP_LAYOUTS`. It fails loudly rather than
reporting zero, so a missing number is visible instead of silently wrong.

### Layer creation fails

```bash
# Test layer creation manually
python3 geoserver-setup.py \
  --gs-url http://localhost:8080/geoserver \
  --cog-cascais /path/to/cascais.cog.deflate.tif

# Check GeoServer workspace via REST
curl -u admin:geoserver \
  http://localhost:8080/geoserver/rest/workspaces/benchmarks
```

### WMS GetMap timeout

The first GetMap after startup pays COG open, TIFF header parse and JVM warm-up, so it
runs several times slower than the ones after it. On the reference run the warm-up was
403 ms against a 64 ms steady state. That is why the harness always discards a warm-up
request before timing anything, and why a single un-warmed request tells you nothing.

If every request stays slow rather than just the first, raise the JVM heap in
`docker-compose.yml`:

```yaml
geoserver:
  environment:
    EXTRA_JAVA_OPTS: "-Xms2g -Xmx8g"
```

## Advanced

### Vector layer

The vector benchmark does not use this GeoServer: `benchmarks/vector/run.sh` starts its own
container and loads the GeoPackage directly (`geoserver_cos2023_setup.sh`, GeoPackage store,
no PostGIS). To benchmark a layer you published by hand in this stack:

```bash
python3 geoserver-benchmark.py --layer benchmarks:<your_layer>
```

### Use a different COG

`geoserver-setup.py` also takes `--cog-ndvi <file>` and publishes it as
`benchmarks:cascais_ndvi_cog` when the file exists. No such file is part of the fixture set;
the NDVI benchmark stays in the engine repo.

### Monitor Performance

View GeoServer request logs:

```bash
docker compose exec geoserver tail -f \
  /opt/geoserver_data/logs/geoserver.log
```

Monitor memory/CPU:

```bash
watch docker stats geoserver
```

## Comparison Results

One run on one developer box, Cascais RGB at 800x536, EPSG:3763, nearest resampling.
Treat it as a smoke test that the harness works, not as a published result.

**Which TerraServe:** the table below (2026-09-04) used a private host build that
self-reports 0.1.0. The re-run further down (2026-09-06) used the pinned public release
`ghcr.io/terraops-org/terraserve:0.2.0`, which is what the repo runs now.

| Engine | best | median | anon resident | anon per render (median of 5) | how it was invoked |
|---|---|---|---|---|---|
| MapServer 8.6.5 | 145.2 ms | 152.9 ms | 4.9 MB | 43.4 MB | `map2img`, new process per render |
| TerraServe | 30.5 ms | 33.7 ms | 4.9 MB | 254.2 MB | `terraserve render`, new process per render |
| GeoServer 2.26.1 | 71.3 ms | 92.4 ms | 872.1 MB | 0.0 MB | WMS GetMap over HTTP, warm JVM, `-Xms1g -Xmx4g` |

MapServer and TerraServe share one image built `FROM camptocamp/mapserver:8.6-gdal3.12`
(MapServer 8.6.5, GDAL 3.12, PROJ 9.7, Ubuntu 24.04). That base is a public image, so
this runs on a machine that has never seen an internal registry.

The base moved from a private Debian trixie / PROJ 9.6 image, which was checked rather
than assumed: MapServer 8.4.1 and 8.6.5 render **byte-identical** output on this
dataset, and the TerraServe-vs-GeoServer pixel identity below still holds at 0.00. The
host-built terraserve binary also runs unchanged on the older glibc (2.39 vs the 2.41
it was built against).

Memory is cgroup v2 `anon` for all three, sampled by `lib/cgroup_mem.py` on the host.
`anon` is memory the process allocated and must free itself; it excludes file-backed
page cache, which all three accumulate for free just by reading the same COG. An
earlier version of this table used `ru_maxrss`, which is per-process and *includes*
file pages, so it could not be compared with a JVM's container footprint at all.

The memory pass runs the render 5 times and reports the median, waiting between runs
for the previous one's pages to be reclaimed. That wait is load-bearing: without it
five back-to-back renders reported a 470 MB peak for a render that costs 248 MB,
because the lifetimes overlapped. The reported `base=` range is the check on this. If
it does not start near zero on every repeat for a fork-per-render engine, the peaks
are piled up and the number is wrong.

### Read this before quoting any of it

**Two columns, because one number cannot describe both shapes.** MapServer and
TerraServe fork a fresh process per render, so they start at ~0 and everything they
use shows up in "added per render". GeoServer is a long-running JVM: it pays about 770 to
870 MB once at startup (872 MB in the table above) and then serves each request out of that, adding almost nothing. The
row that looks cheapest per render is the one holding the most memory overall.

**The timings are not comparable either.** MapServer and TerraServe pay full process
startup on every render. GeoServer does not. For a comparison where all three are
long-running servers, use `../throughput/` and `../vector/`.

**How much of GeoServer's number is HTTP plumbing.** One-off measurements on the compose
GeoServer, warm, not a results run, both on 2026-09-06: a 1x1 GetMap of the same layer,
which renders next to nothing, took 10.1 ms best / 12.1 ms median in the morning and
18.1 / 19.0 ms in the afternoon; the 800x536 GetMap 63.2 / 67.1 ms and 86.2 / 107.7 ms in
the same two sessions (`geoserver-benchmark.py --width 1 --height 1`). So between 15 and 20
percent of GeoServer's render number is servlet and transport; the rest is the render.
GeoServer has no CLI renderer, and a fork-per-render GeoTools program would pay JVM start
and a cold JIT on every call, so the warm server is its fair shape.

**The baseline is not a tuning artifact.** Dropping `-Xms` from `1g` to `64m` moved the
baseline only to 808 MB in one run and 765 MB in another (872 MB with `-Xms1g` in the table), i.e. within the noise of
where the JVM happens to sit. It is the real working set: loaded classes, metaspace,
code cache, native buffers and grown heap, not the flag.

**Corrected 2026-10-03: the paragraph above does not hold.** Until
[PR #1](https://github.com/terraops-org/TerraServe-bench/pull/1) the compose file set
`GEOSERVER_JAVA_OPTS`, which the osgeo image does not read (it reads `EXTRA_JAVA_OPTS`). That
is how the file was first committed on 2026-09-06; for the 2026-09-04 run, two days before
the first commit, it is inferred from the label the script printed, which it reads from that
same variable. The
`-Xms` change never reached the JVM, so the three readings (872, 808 and 765 MB) were taken
with the same heap settings, the image default `-Xms256m -Xmx1g`. They show how far a warm
JVM's baseline moves between runs with nothing changed, and nothing about `-Xms`. For the same
reason the `-Xms1g -Xmx4g` label on the GeoServer rows of both tables here (2026-09-04 above,
2026-09-06 below) is what the compose file asked for, not what ran. The measured numbers
stand.

**TerraServe uses about 5.9x MapServer's memory per render here** (254.2 against 43.4 MB), and that is the number
worth chasing rather than explaining away. It is a fork-per-render CLI path rather
than the sustained serving path the design targets, but it is measured the same way
as the other two and it is not small.

**Corrected 2026-10-03:** most of this number is transparent huge pages, a setting of the host
it was measured on. The section of that date below has the measurement.

The interesting part is that it barely depends on the request. Same COG, same bbox,
only the output size changing (2026-09-04, private host build, isolated single renders):

| output | anon |
|---|---|
| 64x64 | ~150-190 MB |
| 256x256 | ~182 MB |
| 800x536 | ~248 MB |
| 1600x1072 | ~337 MB |

A 64x64 thumbnail costs within ~60 MB of a full 800x536 render. So most of it is a
fixed floor paid before output size matters. The floor is not the file either: a
955 MB COG and an 86 MB COG both sat near 146 MB at 64x64. Something is being
allocated per-open rather than per-request. That is the thread to pull.

Caveat on those small-size rows: at 800x536 the measurement is tight (248.2 to
248.6 MB over five runs), but at 64x64 it ranged 146 to 186 MB across runs and
sampling ten times faster did not narrow it. So treat the small-output rows as
indicative, not precise. The floor is real; its exact height at small sizes is not
pinned down.

### 2026-10-03: the memory floor is transparent huge pages

The floor described above is real on the machine it was measured on, and it is mostly a
kernel setting. It was found when the same pinned image ran on a second machine and the same
render cost a fifth of the memory.

The two machines differ in `/sys/kernel/mm/transparent_hugepage/enabled`: `always` on the
developer laptop that every earlier number in this file comes from (Debian 13, kernel 7.1),
`madvise` on the second one (Ubuntu 24.04, kernel 6.8). With `always` the kernel backs fresh
anonymous memory with 2 MB pages on first touch, and `anon` counts all of it.

Checked on the laptop itself, same container, same image (TerraServe 0.3.7, MapServer 8.6.6),
800x536. Memory is `lib/cgroup_mem.py` with 5 isolated renders, time is `lib/bench.py`.
"THP off" is the same command started through a wrapper that calls
`prctl(PR_SET_THP_DISABLE)`, so nothing on the host changes and no root is needed.

| render on the laptop | anon per render (median of 5) | median time (6 runs) |
|---|---|---|
| TerraServe 0.3.7, host default (THP `always`) | 251.8 MB, 253.2 MB on a second pass | 30.0 ms, then 29.9 ms |
| TerraServe 0.3.7, THP off for the process | 54.3 MB | 35.5 ms, then 34.4 ms |
| MapServer 8.6.6, host default (THP `always`) | 49.5 MB | 119.0 ms |
| MapServer 8.6.6, THP off for the process | 44.9 MB | 114.6 ms |

The cgroup says it directly. At the peak of one TerraServe render `memory.stat` showed
`anon_thp` at 242 MB out of 256 MB of `anon` (244 of 259 on a repeat), and 6 of 58 MB with
THP off. One sample taken 15 ms into a render counted 53 threads on this 16-thread machine.
The likely mechanism, not proven: every thread brings a stack and an allocator arena, each a
fresh mapping, and under `always` the first touch of each takes a whole 2 MB page. That would
explain why the floor follows neither the output size nor the file.

The same test on the `madvise` machine changes nothing, as expected: TerraServe 50.2 MB by
default and 49.6 MB with THP off (35.9 against 36.4 ms), MapServer 41.7 MB both ways. One
sample there counted 69 threads for one render, on 32 hardware threads.

What this changes:

- "5.9x MapServer's memory" holds only on a host with THP `always`. With it off it is about
  1.2x (54.3 against 44.9 MB), and the `madvise` machine agrees: see the next section.
- Huge pages are not free to give up. TerraServe's render is 15 to 18% slower without them
  (35 against 30 ms). MapServer shows no clear change either way.
- It is still an engine matter. Both settings are in common use, and on an `always` host
  TerraServe holds 4.6 times what it needs while MapServer does not.
- The laptop runs THP `always` today. Nobody recorded the setting before, so the earlier
  memory numbers in this file, GeoServer's included, were most likely taken under it and none
  of them says so. A memory number needs the THP setting next
  to it; the harness does not record it yet.

### Two runs on a second machine, 2026-10-03 (new pins, THP `madvise`)

Two full default runs of the suite back to back, on a machine that is not the laptop: a
desktop-class server (Ryzen 9 7950X, 16 cores and 32 threads, 124 GB, Ubuntu 24.04, kernel
6.8.0, Docker 29.8.1, transparent huge pages `madvise`, `amd-pstate-epp` with the performance
preference). Its other workloads were stopped for the runs. A file transfer of another project
was using about 15% of one core when run 1 started and ended on its own during run 2. Engines as pinned
that day: MapServer 8.6.6, TerraServe 0.3.7, GeoServer 3.0.1. Code: `main` at `363a443` plus
that day's changes (the pins, `--wms-cache 0` in the throughput driver); the exact diff is
saved next to the results. `results/20261003-143704-moura001-run1/` and
`results/20261003-150533-moura001-run2/`, not in the repo. Every cell is run 1 / run 2.

| Engine | shape | best | median | max | anon per render (median of 5) |
|---|---|---|---|---|---|
| MapServer 8.6.6 | `map2img`, new process per render | 101.5 / 99.3 ms | 103.5 / 100.7 ms | 104.0 / 102.3 ms | 41.7 / 41.6 MB |
| TerraServe 0.3.7 | `terraserve render`, new process per render | 37.0 / 35.3 ms | 37.2 / 36.0 ms | 38.2 / 37.2 ms | 50.1 / 50.9 MB |
| GeoServer 3.0.1 | WMS GetMap over HTTP, warm JVM | 60.7 / 58.5 ms | 68.2 / 64.1 ms | 76.1 / 74.3 ms | 0.0 / 12.6 MB (holds 712.3 / 705.4 MB) |

GeoServer's JVM options, read from the variable the image really uses: `-Xms256m -Xmx4g
-XX:G1PeriodicGCInterval=5000 -XX:MinHeapFreeRatio=10 -XX:MaxHeapFreeRatio=30`.

- On this host a TerraServe render costs 1.2x MapServer's memory (50 against 42 MB), not
  5.9x. This is the `madvise` machine the section above refers to.
- The medians of the two runs agree within 4% for the CLI engines and within 7% for
  GeoServer. The CLI engines' memory per render repeats within 1 MB.
- The pixel canary holds for these pins (images of run 1): TerraServe 0.3.7 against GeoServer
  3.0.1, 0.00 mean absolute difference over 319,242 opaque pixels, no pixel differing by more
  than 8, and both leave exactly 109,558 pixels transparent. MapServer 8.6.6 differs from
  both by the same 17.57 mean as 8.6.5 did.
- The shapes still differ: the two CLI engines pay process start on every render, GeoServer
  does not. For all three as servers read `../throughput/` and `../vector/`.

### Re-run 2026-09-06 with the pinned release (TerraServe 0.2.0)

Same box, same harness, TerraServe taken from `ghcr.io/terraops-org/terraserve:0.2.0`
(digest `a482e6cf...`), its binary copied into the MapServer image. GeoServer had been up
for 30 seconds when measured, so its median is a cold JIT, not a steady state.

| Engine | best | median | anon per render (median of 5) |
|---|---|---|---|
| MapServer 8.6.5 | 98.1 ms | 108.7 ms | 45.8 MB |
| TerraServe 0.2.0 | 26.7 ms | 28.8 ms | 254.5 MB |
| GeoServer 2.26.1 | 76.7 ms | 147.4 ms | 0.0 MB (holds 774.8 MB, `-Xms1g -Xmx4g`) |

The memory floor is in the public release too: 254.5 MB per render, within 0.3 MB of
the 2026-09-04 host build. The pixel canary holds for 0.2.0: against GeoServer, 0.00 mean
absolute difference over 319,242 opaque pixels, 0 pixels differing by more than 8, and
both engines leave exactly 109,558 pixels transparent.

### The useful part: the engines agree

Comparing the rendered pixels (opaque area only, since all three leave the nodata region
transparent; 2026-09-04 run, private host build; the 0.2.0 re-run above repeats the
TerraServe vs GeoServer row):

| pair | mean abs difference | pixels differing by more than 8 |
|---|---|---|
| TerraServe vs GeoServer | **0.00** | **0 (0.00%)** |
| MapServer vs TerraServe | 17.57 | 185,449 (58.4%) |
| MapServer vs GeoServer | 17.57 | 185,449 (58.4%) |

TerraServe and GeoServer are pixel-identical. Two independent implementations landing on
the same output is a much stronger correctness signal than either one passing its own
tests. MapServer differs from both by the same amount, which points at its own resampling
or pixel-centre convention rather than at anything in this harness.

If you change the harness and this table stops showing a 0.00, something broke.

## See Also

- `../throughput/` - sustained load benchmark (req/s over time)
- `../../config/geoserver/README.md` - manual GeoServer setup
- GeoServer docs: https://geoserver.org/
