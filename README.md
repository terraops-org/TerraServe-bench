# TerraServe Benchmark Suite

Benchmark suite for TerraServe, MapServer, and GeoServer. Measures single-render performance, sustained raster throughput, and vector WMS rendering, as best as possible.

All fixture files are downloaded from the project VPS ([terraserve.io/fixtures](https://terraserve.io/fixtures/)) at runtime

**No GIT LFS, no large files committed to this repo**.

## Quick Start

### Prerequisites

- Docker compose
- Python 3.9+ with `requests` (GeoServer REST scripts) and Pillow (throughput plot): `pip install -r requirements.txt`
- Bash 4.4+, `curl`
- Benchmarks pull the pinned public release image `ghcr.io/terraops-org/terraserve:0.2.0` (see `config.yaml`), no need for rustc

### TerraServe version under test

`config.yaml` pins down TerraServe by release tag, plus image digest:

```yaml
engines:
  terraserve:
    image: "ghcr.io/terraops-org/terraserve:0.2.0@sha256:a482e6cf..."
    source: "https://github.com/terraops-org/TerraServe/releases/tag/v0.2.0"
```


The TerraServe binary is copied out of that image into the MapServer image, so both CLI engines share
the same runtime; the vector benchmark runs the image as-is. 

Every `run.sh` prints TerraServe version that was run. To benchmark a local build instead, set
`TS_BIN=/path/to/terraserve` (render, throughput) or `TS_IMAGE=<image>` (vector); the printed version line then says LOCAL BUILD.


### 1. Download Fixtures

```bash
./setup.sh                 # the two files the benchmarks open, ~2.6 GB
FIXTURES=all ./setup.sh    # also s2_stack.cog.tif (683 MB), for the NDVI benchmark
```

Downloads from `https://terraserve.io/fixtures/` into `./data/` and verifies every file
against the server's `SHA256SUMS` file. Rerunning checks what is already present and will only download what is missing.

| file | size | used by |
|---|---|---|
| `cascais.cog.deflate.tif` | 956 MB | render, throughput |
| `COS2023v1-S2.gpkg` | 1.6 GB | vector |
| `s2_stack.cog.tif` | 683 MB | NDVI benchmark (not ported yet, opt-in) |

### 2. Run Benchmarks

**With GeoServer (recommended for full comparison):**
```bash
# Start GeoServer stack in background
docker compose up -d geoserver postgis

# Run render benchmarks (includes GeoServer)
cd benchmarks/render && ./run.sh
```

`run.sh` waits up to three minutes for GeoServer to answer (a new data volume takes a
minute or two to initialise) and creates the workspace, store and layer itself over REST.
Or all together, from a stopped stack:

```bash
docker compose down && docker compose up -d geoserver postgis && cd benchmarks/render && ./run.sh
```

**Individual benchmarks:**
```bash
cd benchmarks/render && ./run.sh        # MapServer vs TerraServe vs GeoServer
cd benchmarks/throughput && ./run.sh    # Sustained load: MapServer vs TerraServe vs GeoServer (800 requests, 4 concurrent)
cd benchmarks/vector && ./run.sh        # COS2023 vector WMS: TerraServe vs MapServer vs GeoServer
```

**All at once:**
```bash
./run_all.sh                    # Run all benchmarks sequentially
./run_all.sh render             # Run only render benchmarks
./run_all.sh throughput vector  # Run throughput and vector
```

Every run writes to `results/<timestamp>/` (also reachable as `results/latest/`):
- `REPORT.md`: one summary table (engine by benchmark) plus the three detailed tables and
  a short "how to read this". `run_all.sh` prints it at the end.
- `render.jsonl` + `render.meta.json`, `throughput.json`, `vector.json`: the numbers, the
  engine versions that ran, the parameters. `host.json`: CPU, RAM, kernel, Docker.
- The rendered PNGs (one per engine, for the pixel comparison) and the throughput
  memory-over-time plot.

A single `benchmarks/<x>/run.sh` writes the same files for its own benchmark, so partial
reruns land in the same place and the report says "not run" for the rest.

Compare two runs (two TerraServe versions, or your machine against another):

```bash
python3 lib/report.py --compare results/<A> results/<B>
```

## Benchmarks

### Render (`benchmarks/render/`)

Single CLI/HTTP render calls, measuring startup time + processing.

**IMPORTANT: Geoserver will have a extra overhead of HTTP,
15% of the number is HTTP and servlet overhead**. Other benchmarks below are **ALL** HTTP

**Engines:**
- MapServer (map2img)
- TerraServe (terraserve render)
- GeoServer (WMS GetMap HTTP) -> notice that it is HTTP

**Dataset:** Cascais, Portugal (RGB COG, EPSG:3763), one 800x536 render. The NDVI band-math
and multi-encoding benchmarks stay in the engine repo; they are not part of this suite.

**Output:** best, median and max time, cgroup `anon` memory per render, the rendered PNGs

**GeoServer Setup:** Layers are auto-created via REST API on first run. For details, see `benchmarks/render/GEOSERVER.md`


### Throughput (`benchmarks/throughput/`)

Long-running servers under load with varying bboxes (simulates panning).

**Metrics:**
- Memory over time (baseline -> peak -> settle)
- Throughput (req/s)
- Tail latency

**Configuration:**
- Total requests: 800 (default), over 600 distinct bboxes
- Concurrent connections: 4 (default)
- Warm-up: 100 discarded requests per engine before the measured N (default)
- Engines: MapServer (Apache + mod_fcgid), TerraServe (no cache, and LRU 256) and
  GeoServer (own container, `-Xms512m -Xmx4096m`, GWC off, provisioned over REST)

Set via environment:
```bash
CONC=8 N=2000 WARMUP=200 ./run.sh
ENGINES=mapserver,ts-nocache ./run.sh     # keys: mapserver ts-nocache ts-lru geoserver
```

### Vector (`benchmarks/vector/`)

WMS GetMap rendering of vector data, not vector tiles: every engine reads the same
GeoPackage and the same SLD classification and answers 256x256 PNG requests over HTTP.
This is the like-for-like server comparison, all three engines warm.

**Engines (default, `ENGINES=` to pick):** `ts-nocache`, `ts-wmscache` (TerraServe with its
response cache on, listed but kept out of the summary), `mapserver` (Apache + mod_fcgid),
`geoserver` (own container, GWC off).

**Dataset:** COS 2023 v1 (Portugal land cover, 842,413 polygons, EPSG:3763), 81 distinct
bboxes over an all-land interior window.

**Metrics:**
- req/s, p50 and p95 latency, ok/N (a response only counts when it is a PNG)
- cgroup `anon` memory: base, peak under load, settle
- `sample_cos_<engine>.png` per engine for a visual parity check

## Repository Structure

```
.
├── benchmarks/
│   ├── render/          run.sh + individual benchmark scripts
│   ├── throughput/      run.sh + sustained.py
│   └── vector/          COS2023 vector WMS: cos2023_vector_bench.py, GeoServer setup
├── config/
│   ├── mapfiles/        MapServer .map files
│   ├── styles/          TerraServe style.json files
│   └── geoserver/       manual GeoServer walkthrough (optional; run.sh provisions over REST)
├── lib/
│   ├── bench.py         Timing: warm-up + 6 runs, best, median and max
│   ├── cgroup_mem.py    Memory: cgroup v2 anon sampler
│   ├── config.sh        Reads the engine pins from config.yaml
│   └── report.py        results/<run>/ -> REPORT.md, and --compare between runs
├── dockerfiles/         Dockerfile.terraserve: MapServer image + the pinned TerraServe binary
├── data/                Downloaded fixtures (not in git)
├── results/             One directory per run: REPORT.md, JSON, PNGs (not in git)
├── config.yaml          Fixture files and checksums, engine image pins, VPS host
├── setup.sh             Download fixtures from VPS
├── run_all.sh           Orchestrate all benchmarks
└── docker-compose.yml   GeoServer (+ PostGIS) for the render benchmark
```

## Configuration

### config.yaml

Defines the fixture files with their checksums, the engine image pins and the VPS to
download from. Benchmark settings are environment variables (next section), not config:

```yaml
fixtures:
  cascais_rgb:
    file: "cascais.cog.deflate.tif"
    sha256: "4b8c8f58..."   # setup.sh verifies against the server's SHA256SUMS

engines:
  mapserver:
    image: "camptocamp/mapserver:8.6-gdal3.12"
  geoserver:
    image: "docker.osgeo.org/geoserver:2.26.1"
  terraserve:
    image: "ghcr.io/terraops-org/terraserve:0.2.0@sha256:..."

vps:
  host: "terraserve.io"
  path: "/fixtures"
```

### Environment Variables

Override benchmarks at runtime:

```bash
# Render: image size, memory repeats
W=1024 H=768 MEM_RUNS=5 ./benchmarks/render/run.sh

# Throughput: requests, warm-up, concurrency, engines
CONC=8 N=2000 WARMUP=200 ENGINES=mapserver,ts-nocache,geoserver ./benchmarks/throughput/run.sh

# Vector: engines, requests, warm-up, concurrency
ENGINES=ts-nocache,mapserver N=200 WARMUP=100 CONC=8 ./benchmarks/vector/run.sh

# A local TerraServe instead of the pinned release (the report says LOCAL BUILD)
TS_BIN=/path/to/terraserve ./benchmarks/render/run.sh
TS_IMAGE=my/terraserve:dev ./benchmarks/vector/run.sh

# Another host for the fixtures, or the optional third file
VPS_HOST=my-vps.example.com VPS_PATH=/fixtures ./setup.sh
FIXTURES=all ./setup.sh
```

## For the GeoServer Team

### Sharing Results

Send back the whole `results/<timestamp>/` directory of a run, or at least `REPORT.md`
and the three JSON files. `host.json` is in there so the numbers come with the machine
that produced them. `results/` is gitignored on purpose; attach it to an issue or mail.

### Setting Up GeoServer Locally

```bash
# Start GeoServer (and the PostGIS it depends on)
docker compose up -d geoserver postgis

# Access GeoServer
# URL: http://localhost:8080/geoserver
# User: admin / Password: geoserver

# The render run.sh creates the workspace, store and layer itself over REST on every
# run (benchmarks/render/geoserver-setup.py). A manual walkthrough is in
# config/geoserver/README.md.
```

NOTE: **Don't forget to close down Geoserver** (`docker compose down`)

### Reproducing on Different Hardware

The benchmark is designed for portability:

1. MapServer and TerraServe run in the same Docker image: same OS, PROJ and libraries
2. Self-contained fixture download (no repo cloning)
3. Every engine version pinned in `config.yaml`, TerraServe by tag and digest

To test on your own hardware:

```bash
git clone https://github.com/terraops-org/TerraServe-bench
cd TerraServe-bench
pip install -r requirements.txt
./setup.sh
docker compose up -d geoserver postgis     # optional: without it the render benchmark skips GeoServer
./run_all.sh
docker compose down
```

## Architecture

### Memory Management

All engines run in separate Docker containers without resource limits; the report shows
what each one took:

- MapServer: no limit; its FastCGI pool is capped at 16 workers in the vector benchmark
- GeoServer: `-Xms1g -Xmx4g` in the compose stack (render), `-Xms512m -Xmx4096m` in the
  vector benchmark's own container; the report prints the JVM options that ran
- TerraServe: no GC; memory freed immediately after request. This is RUST

Why anon and not peak RSS: `anon` is memory the process allocated and must free
itself.  It EXCLUDES file-backed page cache, which every engine here
accumulates just by reading the same COG and which the kernel reclaims for free.
Comparing `ru_maxrss` (per process, includes file pages) against a JVM's container
footprint compares two different quantities, so all three engines use this instead.

### Fixture Download Strategy

Fixtures are downloaded once and cached in `./data/`:

- Every file verified against the server's `SHA256SUMS` (the same hashes are in config.yaml)
- Sequential download to `<file>.part`, resumable; a corrupt partial is deleted with a message
- No fixtures committed to git (keep repo <100MB)

### Benchmark Harness

Timing and memory are two separate passes over the same command.

`lib/bench.py` does timing: one discarded warm-up, then 6 timed runs, reporting best,
median and max wall-clock plus a check that the output really is a PNG of a sane size.

`lib/cgroup_mem.py` does memory, it samples cgroup v2 `anon` for the engine's
container from the host, at 1 ms, and reports baseline, peak and delta. Every engine
goes through it, so the memory column means one thing. `anon` is memory the process
must free itself and excludes file-backed page cache, which all engines accumulate
for free reading the same COG.

Memory is sampled over exactly ONE render, not the timed loop. A cgroup cannot tell
where one process ends and the next begins, so sampling 7 sequential renders reported
682 MB for an engine whose single render peaks at 252 MB: the lifetimes overlapped
and the sum looked like a peak. Keep the passes separate.

Requires cgroup v2 and the systemd or cgroupfs Docker driver. It fails loudly and
names the paths it tried rather than reporting zero.

## Extending Benchmarks

### Add a New Engine (maybe TiTiler?)

1. Create a new Dockerfile in `dockerfiles/`
2. Add to `config.yaml` under `engines:`
3. Reference in `benchmarks/render/run.sh`

### Add a New Dataset

1. Host fixture on VPS (change default urls)
2. Add entry to `config.yaml` under `fixtures:`
3. Update `setup.sh` to include it
4. Reference in benchmark script

### Add a Custom Benchmark

Create `benchmarks/{category}/my_bench.py` and a `run.sh` next to it shaped like the three
existing ones: read the image pins with `lib/config.sh`, create or reuse `RESULTS_DIR`,
write a JSON record there, call `lib/report.py "$RESULTS_DIR" --write` at the end, and
teach `lib/report.py` to show the new record. Timing and memory helpers:

```python
#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from cgroup_mem import AnonSampler   # with AnonSampler.for_container(cid) as s: ... s.peak, s.delta

# Your benchmark here. Timing of a CLI render: lib/bench.py --json <label> <out.png> -- <cmd>
```

Include in `run_all.sh` or call directly.

## Troubleshooting

### Fixtures fail to download

```bash
# Check VPS connectivity
curl -I https://terraserve.io/fixtures/cascais.cog.deflate.tif

# Verify config
cat config.yaml | grep -A5 "fixtures:"
```

### Docker image build fails

The render and throughput benchmarks build `ts-bench:latest` from
`dockerfiles/Dockerfile.terraserve` on every run. To rebuild it from scratch:

```bash
docker build --no-cache \
  --build-arg MS_IMAGE=camptocamp/mapserver:8.6-gdal3.12 \
  --build-arg TS_IMAGE=ghcr.io/terraops-org/terraserve:0.2.0 \
  -f dockerfiles/Dockerfile.terraserve -t ts-bench:latest dockerfiles
```

### MapServer WMS errors

Render the mapfile once with MapServer's own tool; it prints the parse error if there is one:
```bash
docker run --rm -v "$PWD/config/mapfiles:/maps:ro" -v "$PWD/data/cascais.cog.deflate.tif:/data/cog.tif:ro" \
  --entrypoint map2img camptocamp/mapserver:8.6-gdal3.12 -m /maps/cascais.map -o /tmp/out.png
```

### Memory spikes

The benchmarks sample cgroup v2 `anon` themselves (`lib/cgroup_mem.py`). For a live look
while something runs:
```bash
docker stats --no-stream
```

## Performance Notes

- **Warmup:** First run is discarded (cold caches, I/O settle)
- **Drift:** System performance drifts 2-7% over a session
- **Sequential vs Concurrent:** All results are per-invocation (CLI) or per-connection (HTTP)

## Reporting Issues

Found a benchmark anomaly? File an issue with:
- Benchmark name and dataset
- Engine versions (from `REPORT.md`: the versions that actually ran)
- Hardware (CPU, RAM, storage type)
- Full benchmark log (from `run_all.sh` stdout) and the `results/<timestamp>/` directory

---

**TerraServe project:** https://terraserve.io  
**Repository:** https://github.com/terraops-org/TerraServe-bench
