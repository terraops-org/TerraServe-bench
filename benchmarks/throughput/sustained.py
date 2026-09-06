#!/usr/bin/env python3
"""Sustained-load memory + throughput benchmark: TerraServe serve vs MapServer (Apache + mod_fcgid)
vs GeoServer (own container, provisioned over REST by the render benchmark's script, GWC off).

All run as long-running HTTP servers (containers). We fire ~N GetMaps at VARYING bboxes
(panning -> grows GDAL's process-global block cache) and sample each container's
ANONYMOUS memory over time (cgroup memory.stat `anon` = memory the process holds and must
free itself; excludes reclaimable page cache, which both share reading the same COG).

Reports baseline / peak / post-load-settle anon + throughput, an ASCII curve, and (if
Pillow is present) a PNG plot. Run on the host via benchmarks/throughput/run.sh, or directly:
    N=300 CONC=4 WARMUP=100 ENGINES=mapserver,ts-nocache,ts-lru,geoserver python3 benchmarks/throughput/sustained.py
"""
import atexit
import base64
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

# Repo root is two levels up: benchmarks/throughput/sustained.py -> repo.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COG = os.environ.get("COG", f"{REPO}/data/cascais.cog.deflate.tif")
sys.path.insert(0, os.path.join(REPO, "lib"))
from cgroup_mem import find_cgroup, read_anon  # noqa: E402

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


MS_IMAGE = os.environ.get("MS_IMAGE") or pinned_image("mapserver", "camptocamp/mapserver:8.6-gdal3.12")
TS_IMAGE = os.environ.get("TS_IMAGE") or pinned_image("terraserve", "ghcr.io/terraops-org/terraserve:0.2.0")
TS_BIN = os.environ.get("TS_BIN") or None  # a local build instead of the pinned release
GS_IMAGE = os.environ.get("GS_IMAGE") or pinned_image("geoserver", "docker.osgeo.org/geoserver:2.26.1")
GS_XMX = os.environ.get("GS_XMX", "4096m")
GS_JAVA_OPTS = f"-Xms512m -Xmx{GS_XMX}"          # same shape as the vector benchmark's GeoServer
# Where the JSON record, the plot and the raw series go. run.sh always sets it; a bare
# python3 sustained.py falls back to /tmp so it still works.
RESULTS_DIR = os.environ.get("RESULTS_DIR") or "/tmp"

# Everything the containers mount at /work is staged into one directory, because the
# pieces live in two places (style in config/styles/, mapfile in config/mapfiles/). The
# containers should not have to know that.
BENCH = tempfile.mkdtemp(prefix="ts-sustained-")
atexit.register(shutil.rmtree, BENCH, True)

for src, dst in [
    (f"{REPO}/config/styles/rgb.json", "rgb.json"),
    (f"{REPO}/config/mapfiles/cascais_wms.map", "cascais_wms.map"),
]:
    if not os.path.exists(src):
        sys.exit(f"[ERROR] missing {src}")
    shutil.copy(src, os.path.join(BENCH, dst))

if not os.path.exists(COG):
    sys.exit(f"[ERROR] COG not found at {COG}. Run ./setup.sh")

N = int(os.environ.get("N", "800"))
CONC = int(os.environ.get("CONC", "4"))
# Discarded requests before the measured N, for EVERY engine, so a JVM's cold JIT and a
# FastCGI pool still growing are not what "sustained" measures (cold starts are the render
# benchmark's subject).
WARMUP = int(os.environ.get("WARMUP", "100"))
ENGINE_KEYS = ["mapserver", "ts-nocache", "ts-lru", "geoserver"]
ENGINES = [k for k in os.environ.get("ENGINES", ",".join(ENGINE_KEYS)).split(",") if k]
_unknown = [k for k in ENGINES if k not in ENGINE_KEYS]
if _unknown:
    sys.exit(f"[ERROR] unknown engine key(s): {', '.join(_unknown)}. Known: {', '.join(ENGINE_KEYS)}")
EXT = (-116201.25, -108717.25, -109034.0, -103918.25)  # EPSG:3763
WIN = 300.0


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


def url(target, i):
    """target = (base_url, layer, extra_query). GeoServer needs its workspace-qualified
    layer and TRANSPARENT=true (without it a quarter of the image is opaque white where
    the other engines leave nodata transparent, see the render benchmark)."""
    base, layer, extra = target
    x0, y0, x1, y1 = BB[i % len(BB)]
    # MapServer's base already carries ?map=..., so keep appending with & in that case.
    sep = "&" if "?" in base else "?"
    return (f"{base}{sep}SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS={layer}&STYLES="
            f"&CRS=EPSG:3763&BBOX={x0},{y0},{x1},{y1}&WIDTH=256&HEIGHT=256&FORMAT=image/png{extra}")


def docker_run(args):
    # No --rm: the finally below removes them, and a container that died on start must
    # keep its logs long enough for wait_ready() to print them.
    return subprocess.run(["docker", "run", "-d"] + args,
                          capture_output=True, text=True).stdout.strip()


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
    """The only definition of a good response: a WMS ServiceException is HTTP 200 with an
    XML body, and the vector benchmark once scored 600 of those as 517 req/s (2026-09-06)."""
    return data[:8] == PNG_MAGIC


def container_running(cid):
    out = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", cid],
                         capture_output=True, text=True).stdout.strip()
    return out == "true"


def container_logs(cid, tail=5):
    r = subprocess.run(["docker", "logs", "--tail", str(tail), cid], capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def wait_ready(name, cid, target):
    """True once a GetMap returns a PNG. Gives up at once if the container has exited and
    says what the server answered instead of a PNG."""
    last, answered = b"", 0
    for _ in range(80):
        if answered >= 5:      # up and answering 200 with something else: waiting will not help
            break
        if not container_running(cid):
            print(f"{name}: container exited. Last log lines:\n    "
                  + container_logs(cid).replace("\n", "\n    "))
            return False
        try:
            data = urllib.request.urlopen(url(target, 0), timeout=10).read()
            if is_png(data):
                return True
            last, answered = data, answered + 1
        except Exception:
            pass
        time.sleep(0.5)
    if last:
        print(f"{name}: server up but no PNG. Last response:\n    "
              + last[:500].decode(errors="replace").strip().replace("\n", "\n    "))
    return False


def one(target, i):
    try:
        with urllib.request.urlopen(url(target, i), timeout=60) as r:
            return is_png(r.read())
    except Exception:
        return False


def run_server(name, cid, target):
    if not wait_ready(name, cid, target):
        print(f"{name}: NOT READY (no PNG GetMap within the timeout)")
        return None
    if WARMUP:
        with cf.ThreadPoolExecutor(max_workers=CONC) as ex:
            list(ex.map(lambda i: one(target, i), range(WARMUP)))      # discarded
    time.sleep(1)
    baseline = cg_anon(cid)
    series, stop = [], threading.Event()

    def sampler():
        t0 = time.time()
        while not stop.is_set():
            series.append((time.time() - t0, cg_anon(cid)))
            time.sleep(0.2)

    s = threading.Thread(target=sampler)
    s.start()
    t0, ok = time.time(), 0
    with cf.ThreadPoolExecutor(max_workers=CONC) as ex:
        for r in ex.map(lambda i: one(target, i), range(N)):
            ok += int(r)
    dur = time.time() - t0
    time.sleep(2)  # settle: does it give memory back?
    settle = cg_anon(cid)
    stop.set()
    s.join()
    peak = max((v for _, v in series), default=0)
    if ok == 0:
        print(f"{name}: FAILED, 0/{N} PNG responses")
        return None
    if ok < N:
        print(f"{name}: WARNING only {ok}/{N} responses were PNGs")
    return dict(name=name, baseline=baseline, peak=peak, settle=settle, ok=ok, dur=dur, series=series)


def mb(x):
    return x / 1048576


def spark(series, mx):
    blocks = "▁▂▃▄▅▆▇█"
    if not series:
        return ""
    vals = [v for _, v in series]
    step = max(1, len(vals) // 40)
    return "".join(blocks[min(7, int(v / (mx or 1) * 7.99))] for v in vals[::step][:40])


def geoserver_setup(cid, base_url):
    """Wait for the JVM's REST API, then create workspace, store and layer with the render
    benchmark's script (same names: benchmarks:cascais_rgb_cog). Returns 'GeoServer x.y.z'
    or None with the reason printed."""
    auth = {"Authorization": "Basic " + base64.b64encode(b"admin:geoserver").decode()}
    deadline, ver = time.time() + 240, None
    while time.time() < deadline:
        if not container_running(cid):
            print("GeoServer: container exited. Last log lines:\n    "
                  + container_logs(cid).replace("\n", "\n    "))
            return None
        try:
            req = urllib.request.Request(f"{base_url}/rest/about/version.json", headers=auth)
            with urllib.request.urlopen(req, timeout=5) as r:
                about = json.loads(r.read()).get("about", {}).get("resource", [])
                ver = next((f"GeoServer {x.get('Version')}" for x in about if x.get("@name") == "GeoServer"),
                           "GeoServer")
                break
        except Exception:
            time.sleep(2)
    if ver is None:
        print("GeoServer: REST API not up within 240 s")
        return None
    setup = subprocess.run([sys.executable, f"{REPO}/benchmarks/render/geoserver-setup.py",
                            "--gs-url", base_url, "--cog-cascais", COG, "--container-dir", "/data/cogs"],
                           capture_output=True, text=True)
    if setup.returncode != 0:
        print(f"GeoServer: provisioning failed:\n{setup.stdout[-1500:]}\n{setup.stderr[-800:]}")
        return None
    return ver


# Build the bench image on the MapServer base, same as benchmarks/render/run.sh, so both
# engines run against identical Ubuntu, PROJ, libc and allocator. TerraServe comes from
# the PINNED release image (config.yaml) unless TS_BIN points at a local build.
if TS_BIN:
    if not os.path.exists(TS_BIN):
        sys.exit(f"[ERROR] TS_BIN={TS_BIN} does not exist")
    shutil.copy(TS_BIN, os.path.join(BENCH, "terraserve"))
    with open(f"{BENCH}/Dockerfile", "w") as f:
        f.write(f"FROM {MS_IMAGE}\nCOPY terraserve /usr/local/bin/terraserve\n")
    build = subprocess.run(["docker", "build", "-q", "-f", f"{BENCH}/Dockerfile",
                            "-t", "ts-bench:latest", BENCH], capture_output=True, text=True)
    ts_label = f"LOCAL BUILD from {TS_BIN}, NOT the pinned release"
else:
    build = subprocess.run(["docker", "build", "-q",
                            "--build-arg", f"MS_IMAGE={MS_IMAGE}", "--build-arg", f"TS_IMAGE={TS_IMAGE}",
                            "-f", f"{REPO}/dockerfiles/Dockerfile.terraserve",
                            "-t", "ts-bench:latest", f"{REPO}/dockerfiles"], capture_output=True, text=True)
    ts_label = TS_IMAGE
if build.returncode != 0:
    sys.exit(f"[ERROR] docker build failed:\n{build.stderr[-800:]}")
ts_ver = subprocess.run(["docker", "run", "--rm", "--entrypoint", "terraserve", "ts-bench:latest", "--version"],
                        capture_output=True, text=True).stdout.strip()
ms_ver = " ".join(subprocess.run(["docker", "run", "--rm", "--entrypoint", "map2img", "ts-bench:latest", "-v"],
                                 capture_output=True, text=True).stdout.split()[:3])
print(f"TerraServe: {ts_ver} ({ts_label})\nMapServer:  {ms_ver} ({MS_IMAGE})")

if "geoserver" in ENGINES:
    print(f"GeoServer:  {GS_IMAGE} ({GS_JAVA_OPTS}, GWC off)")

mounts = ["-v", f"{COG}:/data/cog.tif:ro", "-v", f"{BENCH}:/work"]
MS_MAPFILE = "/etc/mapserver/cascais_wms.map"
cids = {}
if "ts-lru" in ENGINES:
    cids["ts-lru"] = docker_run(mounts + ["-p", "18080:8080", "--entrypoint", "terraserve", "ts-bench",
                                          "serve", "--cog", "/data/cog.tif", "--style", "/work/rgb.json",
                                          "--host", "0.0.0.0", "--port", "8080", "--cache-lru", "256"])
if "ts-nocache" in ENGINES:
    cids["ts-nocache"] = docker_run(mounts + ["-p", "18081:8080", "--entrypoint", "terraserve", "ts-bench",
                                              "serve", "--cog", "/data/cog.tif", "--style", "/work/rgb.json",
                                              "--host", "0.0.0.0", "--port", "8080", "--no-cache-lru"])
# MapServer runs its OWN native stack (Apache + mod_fcgid), not a python wrapper. The
# public 8.6 image ships no mapscript, and this is the realistic deployment anyway.
# Two things that cost time to discover: the image EXPOSEs 8080 but Apache actually
# listens on 80, and the mapfile must land under /etc/mapserver/ to satisfy the
# image's MS_MAP_PATTERN, which rejects any other path.
if "mapserver" in ENGINES:
    cids["mapserver"] = docker_run(["-v", f"{COG}:/data/cog.tif:ro",
                                    "-v", f"{BENCH}/cascais_wms.map:{MS_MAPFILE}:ro",
                                    "-p", "18090:80", MS_IMAGE])
# GeoServer in its own container, like the vector benchmark: the COG lands where the
# render benchmark's REST script expects it (/data/cogs/<file>), heap as GS_XMX, GWC off.
if "geoserver" in ENGINES:
    cids["geoserver"] = docker_run(["-v", f"{COG}:/data/cogs/{os.path.basename(COG)}:ro",
                                    "-p", "18091:8080", "-e", f"EXTRA_JAVA_OPTS={GS_JAVA_OPTS}", GS_IMAGE])
print("  ".join(f"{k} {v[:12]}" for k, v in cids.items()) + f"  N={N} warmup={WARMUP} conc={CONC} distinct_bboxes={len(BB)}")
res, gs_ver = {}, None
try:
    if "mapserver" in cids:
        res["mapserver"] = run_server("MapServer", cids["mapserver"],
                                      (f"http://localhost:18090/?map={MS_MAPFILE}", "cascais", ""))
    if "ts-nocache" in cids:
        res["ts-nocache"] = run_server("TerraServe-nocache", cids["ts-nocache"], ("http://localhost:18081/wms", "cascais", ""))
    if "ts-lru" in cids:
        res["ts-lru"] = run_server("TerraServe-LRU", cids["ts-lru"], ("http://localhost:18080/wms", "cascais", ""))
    if "geoserver" in cids:
        gs_ver = geoserver_setup(cids["geoserver"], "http://localhost:18091/geoserver")
        if gs_ver:
            res["geoserver"] = run_server("GeoServer", cids["geoserver"],
                                          ("http://localhost:18091/geoserver/ows", "benchmarks:cascais_rgb_cog", "&TRANSPARENT=true"))
finally:
    if cids:
        subprocess.run(["docker", "rm", "-f"] + list(cids.values()), capture_output=True)

results = [res[k] for k in ENGINE_KEYS if res.get(k)]
failed = [k for k in ENGINES if not res.get(k)]
skipped = [k for k in ENGINE_KEYS if k not in ENGINES]
mx = max((r["peak"] for r in results), default=1)
print(f"\n{'engine':20s} {'ok/N':>10s} {'req/s':>7s} {'baseline':>9s} {'peak':>8s} {'settle':>8s}  anon under load")
for r in results:
    print(f"{r['name']:20s} {r['ok']:>4d}/{N:<5d} {r['ok']/r['dur']:>7.1f} "
          f"{mb(r['baseline']):>7.1f}MB {mb(r['peak']):>6.1f}MB {mb(r['settle']):>6.1f}MB  {spark(r['series'], mx)}")

import datetime
import json
json.dump({r["name"]: r["series"] for r in results}, open(f"{RESULTS_DIR}/sustained_series.json", "w"))

# The record lib/report.py reads. Same numbers as the table above, plus what ran.
IDENT = {"MapServer": ("mapserver", "mapserver", None),
         "TerraServe-nocache": ("ts-nocache", "terraserve", "nocache"),
         "TerraServe-LRU": ("ts-lru", "terraserve", "lru"),
         "GeoServer": ("geoserver", "geoserver", None)}
VERSION = {"mapserver": ms_ver, "terraserve": ts_ver, "geoserver": gs_ver}
IMAGE = {"mapserver": MS_IMAGE, "terraserve": (f"local:{TS_BIN}" if TS_BIN else TS_IMAGE), "geoserver": GS_IMAGE}
engines = []
for r in results:
    key, fam, variant = IDENT[r["name"]]
    e = {"key": key, "label": r["name"], "family": fam, "shape": "warm-http",
         "version": VERSION[fam], "image": IMAGE[fam],
         "metrics": {"req_s": round(r["ok"] / r["dur"], 1), "ok": r["ok"], "n": N, "dur_s": round(r["dur"], 1),
                     "baseline_mb": round(mb(r["baseline"]), 1), "peak_mb": round(mb(r["peak"]), 1),
                     "settle_mb": round(mb(r["settle"]), 1)}}
    if variant:
        e["variant"] = variant
    if TS_BIN and fam == "terraserve":
        e["local_build"] = True
    if fam == "geoserver":
        e["jvm_opts"] = GS_JAVA_OPTS
    engines.append(e)
json.dump({"benchmark": "throughput",
           "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "params": {"n": N, "warmup": WARMUP, "conc": CONC, "size": 256, "distinct_bboxes": len(BB),
                      "crs": "EPSG:3763", "cog": os.path.basename(COG), "gs_xmx": GS_XMX},
           "engines": engines, "failed": failed, "skipped": skipped, "plot": "sustained.png"},
          open(f"{RESULTS_DIR}/throughput.json", "w"), indent=2)

# Plot with Pillow (always available; no matplotlib dependency).
try:
    from PIL import Image, ImageDraw
    W, H, pad = 940, 480, 64
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    tmax = max((t for r in results for t, _ in r["series"]), default=1) or 1
    ymax = mb(mx) or 1
    def X(t): return pad + t / tmax * (W - 2 * pad)
    def Y(v): return (H - pad) - v / ymax * (H - 2 * pad)
    # axes + y gridlines
    d.line([(pad, H - pad), (W - pad, H - pad)], fill=(0, 0, 0))
    d.line([(pad, pad), (pad, H - pad)], fill=(0, 0, 0))
    for k in range(1, 5):
        yv = ymax * k / 4
        yy = Y(yv)
        d.line([(pad, yy), (W - pad, yy)], fill=(225, 225, 225))
        d.text((6, yy - 6), f"{yv:5.0f}MB", fill=(90, 90, 90))
    colors = {"MapServer": (206, 45, 45), "TerraServe-nocache": (40, 160, 90),
              "TerraServe-LRU": (30, 120, 205), "GeoServer": (120, 80, 200)}
    for r in results:
        c = colors.get(r["name"], (0, 0, 0))
        pts = [(X(t), Y(mb(v))) for t, v in r["series"]]
        if len(pts) > 1:
            d.line(pts, fill=c, width=3)
    d.text((pad, 18), "Sustained GetMap load: anonymous memory held by the process", fill=(0, 0, 0))
    d.text((pad, 34), f"({N} varying-bbox requests, {CONC} concurrent; lower is better; page cache excluded)", fill=(110, 110, 110))
    ly = pad + 8
    for r in results:
        c = colors.get(r["name"], (0, 0, 0))
        d.rectangle([(W - 350, ly), (W - 334, ly + 12)], fill=c)
        d.text((W - 328, ly), f"{r['name']}: peak {mb(r['peak']):.0f}MB  settle {mb(r['settle']):.0f}MB", fill=c)
        ly += 20
    d.text((W - pad - 70, H - pad + 8), f"{tmax:.0f}s", fill=(90, 90, 90))
    img.save(f"{RESULTS_DIR}/sustained.png")
    print(f"plot: {RESULTS_DIR}/sustained.png")
except Exception as e:  # noqa: BLE001
    print(f"(no plot: {e})")

if failed:
    sys.exit(f"[ERROR] engine(s) failed: {', '.join(failed)}. See the messages above; "
             f"throughput.json marks them FAILED.")
