#!/usr/bin/env python3
"""Cross-engine VECTOR benchmark: COS2023 (842,413 MultiPolygons, EPSG:3763) served as WMS
GetMap by TerraServe vs MapServer 8.6 vs GeoServer - the SAME GeoPackage, the SAME classification
(TerraServe + GeoServer read cos2023.sld directly; MapServer reads a mapfile generated from it by
cos2023_sld_to_mapfile.py). Fresh matrix for the landing page.

Fairness::
  - MapServer = camptocamp apache/mod_fcgid, N persistent `mapserv` workers (NOT single mapscript),
    GDAL_CACHEMAX capped to match TerraServe's budget.
  - GeoServer = warmed (JIT) before measuring; heap ceiling stated; GWC OFF (dynamic-render parity -
    turn it on only for a cache-vs-cache claim).
  - Saturation concurrency (CONC about the core count), p50/p95, long window; identical panning GetMaps to every
    engine; cgroup `anon` sampled identically (page cache excluded - it's shared read-only data).

Run:  benchmarks/vector/run.sh, or directly python3 benchmarks/vector/cos2023_vector_bench.py
Env:  N (default 600) WARMUP (300) CONC (16) ENGINES (csv of keys; default ts+mapserver)
      TS_IMAGE / MS_IMAGE / GS_IMAGE default to the pins in config.yaml
"""
import concurrent.futures as cf
import os
import subprocess
import sys
import threading
import time
import urllib.request

# Self-locating: benchmarks/vector/this_file.py -> repo root.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH = os.path.join(REPO, "benchmarks", "vector")
sys.path.insert(0, os.path.join(REPO, "lib"))
from cgroup_mem import find_cgroup, read_anon  # noqa: E402

# GPKG_DIR is mounted into the containers as /data, so it must be a directory holding
# the REAL GeoPackage. Docker does not follow a host symlink inside a bind mount, so
# if ./data holds symlinks (a local dev checkout) point this at the real directory.
GPKG_DIR = os.environ.get("GPKG_DIR", os.path.join(REPO, "data"))
SLD = os.environ.get("SLD", os.path.join(REPO, "config", "styles", "cos2023.sld"))
MAPFILE = os.environ.get("MAPFILE", os.path.join(REPO, "config", "mapfiles", "cos2023.map"))
# Mount the FILE, resolved, never the directory. data/ on a developer box often holds
# symlinks into another checkout, and a symlink inside a bind-mounted directory dangles
# in the container: 2026-09-06 every engine failed on "unable to open database file"
# while the host-side existence check passed.
GPKG = os.path.realpath(os.path.join(GPKG_DIR, "COS2023v1-S2.gpkg"))

# Where vector.json and the sample renders go. run.sh always sets it; a bare run falls
# back to this directory so the script still works on its own.
RESULTS_DIR = os.environ.get("RESULTS_DIR") or BENCH

N = int(os.environ.get("N", "600"))
WARMUP = int(os.environ.get("WARMUP", "300"))
CONC = int(os.environ.get("CONC", "16"))
WMS_VERSION = "1.3.0"
WANT = [k for k in os.environ.get("ENGINES", "").split(",") if k]

def pinned_image(engine, default):
    """Image for <engine> from config.yaml, so a version is bumped in ONE place. Stdlib only:
    the file is simple enough that a line scan beats a PyYAML dependency."""
    try:
        lines = open(f"{REPO}/config.yaml").read().splitlines()
    except OSError:
        return default
    inside = False
    for line in lines:
        if line.startswith(f"  {engine}:"):
            inside = True
        elif inside and line.startswith("    image:"):
            return line.split(":", 1)[1].strip().strip('"')
        elif inside and line.startswith("  ") and not line.startswith("    "):
            inside = False
    return default


# The pinned public release image, run as-is (fonts baked in, --wms-cache present).
TS_IMAGE = os.environ.get("TS_IMAGE") or pinned_image("terraserve", "ghcr.io/terraops-org/terraserve:0.2.0")
MS_IMAGE = os.environ.get("MS_IMAGE") or pinned_image("mapserver", "camptocamp/mapserver:8.6-gdal3.12")
GS_IMAGE = os.environ.get("GS_IMAGE") or pinned_image("geoserver", "docker.osgeo.org/geoserver:2.26.1")
GDAL_CACHEMAX = os.environ.get("GDAL_CACHEMAX", "64")
GS_XMX = os.environ.get("GS_XMX", "4096m")
MS_MAX_PROCS = os.environ.get("MS_MAX_PROCS", "16")

# --- profile: central/interior Portugal, all-land so every tile does real render work ---
EXT = (-100000.0, -140000.0, -20000.0, -60000.0)  # EPSG:3763, 80km x 80km interior
WIN = 12000.0
CRS = "EPSG:3763"
FMT = "image/png"
SIZE = 256


def bboxes():
    minx, miny, maxx, maxy = EXT
    out, y = [], miny
    while y < maxy:
        x = minx
        while x < maxx:
            out.append((x, y, x + WIN, y + WIN))
            x += WIN * 0.8
        y += WIN * 0.8
    return out


BB = bboxes()


class Engine:
    def __init__(self, key, label, port, layer, base_path, docker_args,
                 mem_note, extra_query="", setup=None):
        self.key, self.label, self.port, self.layer = key, label, port, layer
        self.base_path, self.docker_args, self.mem_note = base_path, docker_args, mem_note
        self.extra_query, self.setup = extra_query, setup

    def base_url(self, host="localhost"):
        return f"http://{host}:{self.port}{self.base_path}"


def engines():
    ts_mounts = ["-v", f"{GPKG}:/data/COS2023v1-S2.gpkg:ro", "-v", f"{SLD}:/style/cos2023.sld:ro"]
    ts_serve = [TS_IMAGE, "serve", "--vector", "/data/COS2023v1-S2.gpkg",
                "--vec-style", "/style/cos2023.sld", "--name", "cos2023",
                "--host", "0.0.0.0", "--port", "8080"]
    return [
        Engine("ts-nocache", "TerraServe-nocache", 18080, "cos2023", "/wms",
               ["-p", "18080:8080"] + ts_mounts + ts_serve + ["--no-cache-lru", "--wms-cache", "0"],
               "bounded: windowed GPKG reads, request buffers freed on return, no cache"),
        Engine("ts-wmscache", "TerraServe-wmscache", 18081, "cos2023", "/wms",
               ["-p", "18081:8080"] + ts_mounts + ts_serve + ["--no-cache-lru", "--wms-cache", "256"],
               "bounded: windowed GPKG + 256 MiB WMS response LRU (hard byte cap)"),
        Engine("mapserver", "MapServer-8.6-FastCGI", 19092, "cos2023", "/",
               ["-p", "19092:80", "-v", f"{MAPFILE}:/etc/mapserver/cos2023.map:ro",
                "-v", f"{GPKG}:/data/COS2023v1-S2.gpkg:ro", "-e", f"MAX_PROCESSES={MS_MAX_PROCS}",
                "-e", "MIN_PROCESSES=2", "-e", f"GDAL_CACHEMAX={GDAL_CACHEMAX}", MS_IMAGE],
               f"apache/mod_fcgid: <= {MS_MAX_PROCS} persistent mapserv workers; GDAL cache {GDAL_CACHEMAX}MB/worker",
               extra_query="map=/etc/mapserver/cos2023.map"),
        Engine("geoserver", "GeoServer-2.26", 19100, "bench:cos2023v1", "/geoserver/wms",
               ["-p", "19100:8080", "-v", f"{GPKG}:/data/COS2023v1-S2.gpkg:ro",
                "-e", f"EXTRA_JAVA_OPTS=-Xms512m -Xmx{GS_XMX}", GS_IMAGE],
               f"JVM commits heap toward -Xmx={GS_XMX} regardless of per-request use; GWC OFF (dynamic render)",
               setup=lambda cid, url: geoserver_setup(url)),
    ]


def geoserver_setup(base_url):
    """Provision workspace + GPKG store + layer + the cos2023 SLD via the REST API."""
    rest = base_url.rsplit("/wms", 1)[0] + "/rest"
    r = subprocess.run(["bash", f"{BENCH}/geoserver_cos2023_setup.sh", rest, SLD],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  geoserver setup FAILED:\n{r.stdout[-2000:]}\n{r.stderr[-1000:]}")
        return False
    return True


def url(engine, i):
    x0, y0, x1, y1 = BB[i % len(BB)]
    extra = f"&{engine.extra_query}" if engine.extra_query else ""
    return (f"{engine.base_url()}?SERVICE=WMS&VERSION={WMS_VERSION}&REQUEST=GetMap"
            f"&LAYERS={engine.layer}&STYLES=&CRS={CRS}{extra}"
            f"&BBOX={x0},{y0},{x1},{y1}&WIDTH={SIZE}&HEIGHT={SIZE}&FORMAT={FMT}")


_STAT = {}


def cg_anon(cid):
    """anon bytes of the container's cgroup, through lib/cgroup_mem.py so every benchmark
    knows the same cgroup layouts and fails loudly (paths tried) instead of reporting 0 on a
    box whose Docker is not on the systemd driver. None (container gone) reads as 0."""
    if cid not in _STAT:
        _STAT[cid] = os.path.join(find_cgroup(cid), "memory.stat")
    return read_anon(_STAT[cid]) or 0


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def is_png(data):
    """The only definition of a good response. A WMS ServiceException is HTTP 200 with an
    XML body; counting 'any bytes' as ok once scored 600 MapServer error pages at 517 req/s
    (2026-09-06)."""
    return data[:8] == PNG_MAGIC


def container_running(cid):
    out = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", cid],
                         capture_output=True, text=True).stdout.strip()
    return out == "true"


def container_logs(cid, tail=5):
    r = subprocess.run(["docker", "logs", "--tail", str(tail), cid], capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def wait_ready(engine, cid, timeout_s=240):
    """True once a GetMap returns a PNG. Gives up at once if the container has exited, and
    says what the server answered instead of a PNG, so a misconfiguration costs seconds
    and a message rather than the whole timeout in silence."""
    deadline = time.time() + timeout_s
    last, answered = b"", 0
    while time.time() < deadline and answered < 5:
        if not container_running(cid):
            print(f"  {engine.label}: container exited. Last log lines:\n    "
                  + container_logs(cid).replace("\n", "\n    "))
            return False
        try:
            with urllib.request.urlopen(url(engine, 0), timeout=8) as r:
                data = r.read()
                if is_png(data):
                    return True
                # A server that is up and answers 200 with something else (a WMS
                # ServiceException) will not get better by waiting; five in a row is enough.
                last, answered = data, answered + 1
        except Exception:
            pass
        time.sleep(1)
    if last:
        print(f"  {engine.label}: server up but no PNG. Last response:\n    "
              + last[:500].decode(errors="replace").strip().replace("\n", "\n    "))
    return False


def one(engine, i):
    t = time.time()
    try:
        with urllib.request.urlopen(url(engine, i), timeout=120) as r:
            ok = is_png(r.read())
    except Exception:
        ok = False
    return ok, (time.time() - t) * 1000.0


def load(engine, n):
    ok, lat = 0, []
    with cf.ThreadPoolExecutor(max_workers=CONC) as ex:
        for good, ms in ex.map(lambda i: one(engine, i), range(n)):
            ok += int(good)
            lat.append(ms)
    return ok, lat


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))]


def engine_version(engine, cid):
    """What actually ran, asked of the container itself; GeoServer keeps its image tag."""
    cmd = {"terraserve": ["terraserve", "--version"], "mapserver": ["mapserv", "-v"]}.get(FAMILY[engine.key])
    if not cmd:
        return None
    out = subprocess.run(["docker", "exec", cid] + cmd, capture_output=True, text=True).stdout
    words = out.split()
    return " ".join(words[:3] if FAMILY[engine.key] == "mapserver" else words[:2]) or None


def run_engine(engine):
    # No --rm: the finally below removes the container, and one that died on start must
    # keep its logs long enough for wait_ready() to print them.
    started = subprocess.run(["docker", "run", "-d"] + engine.docker_args, capture_output=True, text=True)
    cid = started.stdout.strip()
    if not cid:
        print(f"  {engine.label}: FAILED to start: {started.stderr.strip()[-300:]}")
        return None
    try:
        if engine.setup:                       # GeoServer: provision (waits for REST) before serving
            if not engine.setup(cid, engine.base_url()):
                return None
        if not wait_ready(engine, cid):
            print(f"  {engine.label}: NOT READY (no PNG GetMap within the timeout)")
            return None
        version = engine_version(engine, cid)
        if WARMUP:
            load(engine, WARMUP)          # discarded - JIT/warm caches so we measure steady state
        time.sleep(1)
        baseline = cg_anon(cid)
        series, stop = [], threading.Event()

        def sampler():
            t0 = time.time()
            while not stop.is_set():
                series.append(cg_anon(cid))
                time.sleep(0.2)

        s = threading.Thread(target=sampler)
        s.start()
        t0 = time.time()
        ok, lat = load(engine, N)
        dur = time.time() - t0
        time.sleep(2)
        settle = cg_anon(cid)
        stop.set()
        s.join()
        try:
            with urllib.request.urlopen(url(engine, 3), timeout=120) as r:
                open(f"{RESULTS_DIR}/sample_cos_{engine.key}.png", "wb").write(r.read())
        except Exception:
            pass
        if ok == 0:
            print(f"  {engine.label}: FAILED, 0/{N} PNG responses (see sample_cos_{engine.key}.png for what came back)")
            return None
        if ok < N:
            print(f"  {engine.label}: WARNING only {ok}/{N} responses were PNGs")
        return dict(engine=engine, ok=ok, dur=dur, lat=lat, baseline=baseline,
                    peak=max(series, default=0), settle=settle, version=version)
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


def mb(x):
    return x / 1048576


FAMILY = {"ts-nocache": "terraserve", "ts-wmscache": "terraserve", "mapserver": "mapserver", "geoserver": "geoserver"}
VARIANT = {"ts-nocache": "nocache", "ts-wmscache": "wmscache"}
IMAGE = {"terraserve": lambda: TS_IMAGE, "mapserver": lambda: MS_IMAGE, "geoserver": lambda: GS_IMAGE}


def write_results(results, failed=(), skipped=()):
    """vector.json, the record lib/report.py reads: the table above, what ran, what failed,
    what was left out with ENGINES=."""
    import datetime
    import json
    engines = []
    for r in results:
        e = r["engine"]
        fam = FAMILY[e.key]
        rec = {"key": e.key, "label": e.label, "family": fam, "shape": "warm-http",
               "cache": e.key == "ts-wmscache", "version": r.get("version"), "image": IMAGE[fam](),
               "mem_note": e.mem_note,
               "metrics": {"req_s": round(r["ok"] / r["dur"], 1), "ok": r["ok"], "n": N, "dur_s": round(r["dur"], 1),
                           "p50_ms": round(pct(r["lat"], 50)), "p95_ms": round(pct(r["lat"], 95)),
                           "p99_ms": round(pct(r["lat"], 99)),
                           "base_mb": round(mb(r["baseline"])), "peak_mb": round(mb(r["peak"])),
                           "settle_mb": round(mb(r["settle"]))}}
        if e.key in VARIANT:
            rec["variant"] = VARIANT[e.key]
        engines.append(rec)
    json.dump({"benchmark": "vector",
               "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "params": {"n": N, "warmup": WARMUP, "conc": CONC, "size": SIZE, "distinct_bboxes": len(BB),
                          "crs": CRS, "wms": WMS_VERSION, "gpkg": "COS2023v1-S2.gpkg",
                          "gdal_cachemax_mb": int(GDAL_CACHEMAX), "ms_max_procs": int(MS_MAX_PROCS), "gs_xmx": GS_XMX},
               "engines": engines, "failed": list(failed), "skipped": list(skipped)},
              open(os.path.join(RESULTS_DIR, "vector.json"), "w"), indent=2)


def main():
    if not os.path.isfile(GPKG):
        sys.exit(f"[ERROR] {GPKG} is not a file (GPKG_DIR={GPKG_DIR}). Run ./setup.sh or point GPKG_DIR at the data.")
    all_e = engines()
    if WANT:
        # Refuse an unknown key rather than silently dropping it. ENGINES=ts once ran
        # the whole comparison with NO TerraServe in it, because the real keys are
        # ts-nocache and ts-wmscache, and the output looked perfectly normal.
        known = {e.key for e in all_e}
        unknown = [k for k in WANT if k not in known]
        if unknown:
            sys.exit(f"[ERROR] unknown engine key(s): {', '.join(unknown)}\n"
                     f"        known keys: {', '.join(sorted(known))}")
        all_e = [e for e in all_e if e.key in WANT]
    else:
        all_e = [e for e in all_e if e.key != "geoserver"]  # default: ts + mapserver (geoserver opt-in)
    selected = {e.key for e in all_e}
    skipped = [e.key for e in engines() if e.key not in selected]     # the report says "not selected"
    print(f"profile=cos2023-vector  data=COS2023v1-S2.gpkg(842413 MultiPolygons, EPSG:3763)")
    print(f"N={N} warmup={WARMUP} conc={CONC} size={SIZE}x{SIZE} win={WIN:.0f}m "
          f"distinct_bboxes={len(BB)} wms={WMS_VERSION}")
    results, failed = [], []
    for e in all_e:
        print(f"  running {e.label} ...", flush=True)
        r = run_engine(e)
        if r:
            results.append(r)
            print(f"    ok {r['ok']}/{N}  {r['ok']/r['dur']:.1f} req/s  "
                  f"p50 {pct(r['lat'],50):.0f}ms  p95 {pct(r['lat'],95):.0f}ms  "
                  f"peak {mb(r['peak']):.0f}M")
        else:
            failed.append(e.key)
    print(f"\n{'engine':24s} {'ok/N':>10s} {'req/s':>8s} {'p50ms':>8s} {'p95ms':>8s} "
          f"{'base':>7s} {'peak':>7s} {'settle':>7s}")
    for r in results:
        e = r["engine"]
        print(f"{e.label:24s} {r['ok']:>4d}/{N:<5d} {r['ok']/r['dur']:>8.1f} "
              f"{pct(r['lat'],50):>8.0f} {pct(r['lat'],95):>8.0f} "
              f"{mb(r['baseline']):>6.0f}M {mb(r['peak']):>6.0f}M {mb(r['settle']):>6.0f}M")
    print("\nmemory model (why `anon` differs per engine):")
    for r in results:
        print(f"  {r['engine'].label:24s} {r['engine'].mem_note}")
    write_results(results, failed, skipped)   # always: the report must tell FAILED from not run
    print(f"\ncorrectness: sample_cos_<engine>.png written to {RESULTS_DIR}/ (visual parity check)")
    if failed:
        sys.exit(f"[ERROR] engine(s) failed: {', '.join(failed)}. See the messages above; "
                 f"vector.json marks them FAILED.")


if __name__ == "__main__":
    main()
