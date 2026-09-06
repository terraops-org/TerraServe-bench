---
concepts: [geoserver, render-benchmark, memory-measurement, correctness-canary]
status: active
confidence: measured
updated: 2026-09-06
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
2. Create workspace `benchmarks` via REST API
3. Create coverage store for Cascais RGB COG
4. Publish coverage layer `cascais_rgb_cog`
5. Run 6 timed WMS GetMap requests
6. Report best/median time and output size

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
    GEOSERVER_JAVA_OPTS: "-Xms2g -Xmx8g"
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
  /opt/geoserver/data_dir/logs/geoserver.log
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

**TerraServe uses about 5.9x MapServer's memory per render here** (254.2 against 43.4 MB), and that is the number
worth chasing rather than explaining away. It is a fork-per-render CLI path rather
than the sustained serving path the design targets, but it is measured the same way
as the other two and it is not small.

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
