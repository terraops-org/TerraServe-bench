#!/usr/bin/env python3
"""
GeoServer WMS benchmark: measure GetMap performance.

Issues WMS GetMap requests and measures:
- Response time (ms)
- Peak memory (optional, via monitoring)
- Output validation (PNG magic bytes)
"""
import json
import requests
import subprocess
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from cgroup_mem import AnonSampler, CgroupNotFound  # noqa: E402


def compose_container(service="geoserver", port=8080):
    """Container ID for a compose service, so nothing hardcodes a generated name. Falls back
    to whichever container publishes the GeoServer port: a GeoServer started by hand, or by
    another checkout's compose project, still gets its memory sampled (2026-09-06: a second
    copy of the repo found "could not resolve the geoserver container" and printed "holds ?")."""
    repo = Path(__file__).resolve().parents[2]
    for cmd in (["docker", "compose", "ps", "-q", service],
                ["docker", "ps", "-q", "--filter", f"publish={port}"]):
        try:
            out = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=20)
            cid = [c for c in out.stdout.strip().splitlines() if c]
            if cid:
                return cid[0]
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def java_opts(container):
    """GEOSERVER_JAVA_OPTS as the container was actually started with.

    The memory number is meaningless without this: -Xms alone moved anon by 218 MB
    on the reference box, with no change to the rendering work.
    """
    try:
        out = subprocess.run(
            ["docker", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", container],
            capture_output=True, text=True, timeout=20)
        for line in out.stdout.splitlines():
            if line.startswith("GEOSERVER_JAVA_OPTS="):
                return line.split("=", 1)[1]
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"

def append_json(path, record):
    """One JSON object per line, appended: bench.py and cgroup_mem.py write the same file."""
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


class GeoServerBenchmark:
    def __init__(self, base_url="http://localhost:8080/geoserver", layer="benchmarks:cascais_rgb_cog",
                 auth=("admin", "geoserver")):
        self.base_url = base_url
        self.layer = layer
        self.ows_endpoint = f"{base_url}/ows"
        self.timeout = 60
        self.auth = auth

    def version(self):
        """'GeoServer 2.26.1' from the REST about endpoint, or None."""
        try:
            r = requests.get(f"{self.base_url}/rest/about/version.json", auth=self.auth, timeout=10)
            for res in r.json().get("about", {}).get("resource", []):
                if res.get("@name") == "GeoServer":
                    return f"GeoServer {res.get('Version')}"
        except Exception:  # noqa: BLE001
            pass
        return None

    def health_check(self):
        """Verify GeoServer is responding."""
        try:
            r = requests.get(self.ows_endpoint, params={"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"},
                           timeout=5)
            return r.status_code == 200
        except:
            return False

    def get_map(self, bbox, crs, width=800, height=536):
        """
        Issue WMS GetMap request.

        Args:
            bbox: tuple (minx, miny, maxx, maxy)
            crs: EPSG code (e.g., "EPSG:3763")
            width, height: output size

        Returns:
            (response_time_ms, png_bytes) or (None, None) on error
        """
        minx, miny, maxx, maxy = bbox

        # transparent=true matters for more than looks. The MapServer mapfile is
        # IMAGEMODE RGBA / TRANSPARENT ON and terraserve writes RGBA too, so both leave
        # the nodata area outside the raster transparent. Without this GeoServer paints
        # that area opaque white, and roughly a quarter of the image stops matching the
        # other two engines, which makes the three numbers describe different pictures.
        params = {
            "service": "WMS",
            "version": "1.3.0",
            "request": "GetMap",
            "layers": self.layer,
            "styles": "",
            "crs": crs,
            "bbox": f"{minx},{miny},{maxx},{maxy}",
            "width": width,
            "height": height,
            "transparent": "true",
            "format": "image/png"
        }

        try:
            t0 = time.time()
            r = requests.get(self.ows_endpoint, params=params, timeout=self.timeout)
            elapsed_ms = (time.time() - t0) * 1000

            if r.status_code != 200:
                print(f"[ERROR] GetMap returned {r.status_code}: {r.text[:100]}", file=sys.stderr)
                return None, None

            # Validate PNG
            if not r.content.startswith(b"\x89PNG"):
                print(f"[ERROR] Response is not PNG (magic bytes missing)", file=sys.stderr)
                return None, None

            return elapsed_ms, r.content
        except requests.Timeout:
            print(f"[ERROR] GetMap request timed out ({self.timeout}s)", file=sys.stderr)
            return None, None
        except Exception as e:
            print(f"[ERROR] GetMap failed: {e}", file=sys.stderr)
            return None, None

    def benchmark(self, bbox, crs, width=800, height=536, warmup_runs=1, timed_runs=6):
        """
        Run benchmark: warmup + N timed runs.

        Returns:
            dict with 'best_ms', 'median_ms', 'output_size'
        """
        # Warmup
        for i in range(warmup_runs):
            elapsed_ms, png = self.get_map(bbox, crs, width, height)
            if elapsed_ms is None:
                return None
            print(f"[Warmup {i+1}] {elapsed_ms:.1f}ms", file=sys.stderr)

        # Timed runs
        times = []
        for i in range(timed_runs):
            elapsed_ms, png = self.get_map(bbox, crs, width, height)
            if elapsed_ms is None:
                return None
            times.append(elapsed_ms)
            print(f"[Run {i+1}] {elapsed_ms:.1f}ms", file=sys.stderr)

        times.sort()
        best = times[0]
        median = times[len(times) // 2]
        output_size = len(png) if png else 0

        return {
            "best_ms": best,
            "median_ms": median,
            "output_size": output_size,
            "runs": times
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Benchmark GeoServer WMS GetMap performance")
    parser.add_argument("--gs-url", default="http://localhost:8080/geoserver", help="GeoServer base URL")
    parser.add_argument("--layer", default="benchmarks:cascais_rgb_cog", help="Layer to benchmark")
    parser.add_argument("--bbox", default="-116201.25,-108717.25,-109034.0,-103918.25", help="Bounding box (minx,miny,maxx,maxy)")
    parser.add_argument("--crs", default="EPSG:3763", help="Coordinate reference system")
    parser.add_argument("--width", type=int, default=800, help="Output width (pixels)")
    parser.add_argument("--height", type=int, default=536, help="Output height (pixels)")
    parser.add_argument("--warmup", type=int, default=1, help="Warmup runs")
    parser.add_argument("--runs", type=int, default=6, help="Timed runs")
    parser.add_argument("--output", help="Save PNG to file")
    parser.add_argument("--container",
                        help="GeoServer container name or ID for the memory sample. "
                             "Default: resolved from docker compose.")
    parser.add_argument("--user", default="admin", help="GeoServer admin user (version lookup only)")
    parser.add_argument("--passwd", default="geoserver", help="GeoServer admin password (version lookup only)")
    parser.add_argument("--json", metavar="FILE",
                        help="Append version, timing and memory records as JSON lines to FILE")

    args = parser.parse_args()

    gs = GeoServerBenchmark(base_url=args.gs_url, layer=args.layer, auth=(args.user, args.passwd))

    if not gs.health_check():
        print(f"[ERROR] GeoServer not responding at {args.gs_url}", file=sys.stderr)
        sys.exit(1)

    bbox = tuple(map(float, args.bbox.split(",")))
    result = gs.benchmark(bbox, args.crs, args.width, args.height, args.warmup, args.runs)

    if result is None:
        sys.exit(1)

    version = gs.version()
    print(f"\nGeoServer WMS benchmark:")
    print(f"  Layer: {args.layer}")
    print(f"  Size: {args.width}x{args.height}")
    print(f"  Best:   {result['best_ms']:8.1f}ms")
    print(f"  Median: {result['median_ms']:8.1f}ms")
    print(f"  Max:    {result['runs'][-1]:8.1f}ms")
    print(f"  Output: {result['output_size']:8d}B")
    if args.json:
        if version:
            append_json(args.json, {"engine": "GeoServer", "kind": "version", "version": version})
        append_json(args.json, {"engine": "GeoServer", "kind": "timing",
                                "best_ms": round(result["best_ms"], 1), "median_ms": round(result["median_ms"], 1),
                                "max_ms": round(result["runs"][-1], 1),
                                "out_bytes": result["output_size"], "png": True,
                                "warmup_runs": args.warmup, "timed_runs": args.runs})

    # Memory in its own pass over ONE request, matching how the CLI engines are
    # measured in run.sh. Baseline here is the JVM's already-allocated heap, so
    # delta (what this request added) is the number comparable to the others.
    from urllib.parse import urlparse
    container = args.container or compose_container(port=urlparse(args.gs_url).port or 8080)
    if container:
        try:
            with AnonSampler.for_container(container) as s:
                gs.get_map(bbox, args.crs, args.width, args.height)
            mb = 1024 * 1024
            print(f"  anon:   base={s.baseline / mb:.1f}MB "
                  f"peak={s.peak / mb:.1f}MB delta={s.delta / mb:.1f}MB")
            print(f"  JVM:    {java_opts(container)}")
            if args.json:
                append_json(args.json, {"engine": "GeoServer", "kind": "memory",
                                        "base_mb": round(s.baseline / mb, 1), "peak_mb": round(s.peak / mb, 1),
                                        "delta_mb": round(s.delta / mb, 1), "n": 1, "jvm_opts": java_opts(container)})
        except CgroupNotFound as e:
            print(f"  anon:   unavailable ({e})", file=sys.stderr)
    else:
        print("  anon:   skipped (could not resolve the geoserver container)", file=sys.stderr)

    if args.output:
        # Fetch one more time to save
        _, png = gs.get_map(bbox, args.crs, args.width, args.height)
        if png:
            Path(args.output).write_bytes(png)
            print(f"  Saved:  {args.output}")

    sys.exit(0)
