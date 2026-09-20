#!/usr/bin/env python3
"""Turn one results directory into REPORT.md, or compare two directories.

    python3 lib/report.py results/2026-09-06-081203            print the report
    python3 lib/report.py results/latest --write               also write REPORT.md there
    python3 lib/report.py --compare results/A results/B        deltas, B relative to A
    python3 lib/report.py --host results/X                     write host.json (CPU, RAM, ...)

What a results directory holds (each benchmark writes its own file, a partial run is fine):
    host.json          machine facts, written once per run
    render.meta.json   engine versions, images, shapes and parameters, written by render/run.sh
    render.jsonl       one line per measurement, appended by bench.py, cgroup_mem.py and
                       geoserver-benchmark.py (they run in different places, so they append)
    throughput.json    written whole by sustained.py
    vector.json        written whole by cos2023_vector_bench.py

Stdlib only, on purpose: this runs on the GeoServer team's machine too.
"""
import argparse
import datetime
import json
import os
import platform
import subprocess
import sys

FAMILIES = ["mapserver", "terraserve", "geoserver"]
FAMILY_NAME = {"mapserver": "MapServer", "terraserve": "TerraServe", "geoserver": "GeoServer"}

# ---------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------


def _json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _render(d):
    meta = _json(os.path.join(d, "render.meta.json"))
    if meta is None:
        return None
    engines = meta.get("engines", {})
    for e in engines.values():
        e.setdefault("metrics", {})
    try:
        with open(os.path.join(d, "render.jsonl")) as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                line = json.loads(raw)
                e = engines.setdefault(line.pop("engine"), {"metrics": {}})
                kind = line.pop("kind", "")
                if kind == "version":
                    e["version"] = line.get("version")
                else:
                    e["metrics"].update(line)
    except OSError:
        pass
    # `unreachable` is written by the render runner when an engine never answered, so the
    # report can say that rather than leave a cell that reads like "not part of this run".
    return {"date": meta.get("date"), "params": meta.get("params", {}), "engines": engines,
            "unreachable": meta.get("unreachable", [])}


def load(d):
    """Everything a results directory holds; a benchmark that did not run is None."""
    return {
        "dir": d,
        "host": _json(os.path.join(d, "host.json")),
        "render": _render(d),
        "throughput": _json(os.path.join(d, "throughput.json")),
        "vector": _json(os.path.join(d, "vector.json")),
    }


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------


def version_label(family, version=None, image=None):
    """'MapServer version 8.6.5' -> 'MapServer 8.6.5'; 'terraserve 0.2.0' -> 'TerraServe 0.2.0'."""
    name = FAMILY_NAME.get(family, family)
    if version:
        v = version.replace("version", "").split()
        v = [t for t in v if t.lower() != name.lower()]
        if v:
            return f"{name} {v[0]}"
    if image and ":" in image:
        return f"{name} {image.rsplit(':', 1)[1].split('@')[0]}"
    return name


def fmt(x, unit="", nd=1):
    if x is None:
        return "?"
    if isinstance(x, (int, float)):
        return f"{x:.{nd}f}{unit}" if nd else f"{x:.0f}{unit}"
    return f"{x}{unit}"


def render_engines(run):
    r = run.get("render")
    return (r or {}).get("engines", {})


def by_family(engines_list, family, variant=None, exclude_cache=False):
    out = []
    for e in engines_list or []:
        if e.get("family") != family:
            continue
        if variant and e.get("variant") != variant:
            continue
        if exclude_cache and e.get("cache"):
            continue
        out.append(e)
    return out


def render_engine_for(run, family):
    for label, e in render_engines(run).items():
        if e.get("family") == family:
            e = dict(e)
            e["label"] = label
            return e
    return None


def mem_per_render(m):
    return m.get("delta_median_mb", m.get("delta_mb"))


def engine_versions(run):
    """One version label per family, from whichever benchmark ran."""
    out = {}
    for fam in FAMILIES:
        e = render_engine_for(run, fam)
        cands = [e] if e else []
        for b in ("throughput", "vector"):
            cands += by_family((run.get(b) or {}).get("engines"), fam)
        for c in cands:
            if c:
                out[fam] = version_label(fam, c.get("version"), c.get("image"))
                break
    return out


# ---------------------------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------------------------

HOW_TO_READ = """## How to read this

- **Shapes differ in the render benchmark.** MapServer and TerraServe are a fresh process per
  render (cold CLI); GeoServer is a warm JVM answering HTTP. Their render timings are not
  comparable with each other. Throughput and vector are warm HTTP servers for every engine.
- **Memory is cgroup v2 `anon` for every engine.** For a cold process it is what one render
  cost; for the JVM it is what it holds. Page cache is excluded everywhere.
- **A cache-hit row is not a render rate.** TerraServe's WMS-cache row in the vector table
  serves repeats from memory while the other engines render every request. It is listed for
  completeness and kept out of the summary.
- **Which TerraServe:** the version column says what ran. A LOCAL BUILD marker means a
  developer binary was substituted for the pinned release.
- **Empty cells mean several different things.** `not run`: that benchmark was not part of
  this run. `FAILED`: the engine started but produced no PNG (the run's terminal output says
  why). `not reachable`: the engine never answered, so it was skipped (GeoServer, when the
  render benchmark could not start or reach it). `not selected`: left out with `ENGINES=`.
  `not in this benchmark`: the benchmark has no engine for it.
- **One run on one machine.** Re-run before quoting; the box drifts a few percent within a
  session. Use `lib/report.py --compare` for two runs.
"""


def _failed_keys(run, bench):
    return list((run.get(bench) or {}).get("failed") or [])


def _key_family(key):
    return "terraserve" if key.startswith("ts") else key


def _absent_cell(run, bench, fam):
    """Why a summary cell has no number. Three different things, and a reader must be able
    to tell them apart: the benchmark did not run at all, the engine ran and failed, it was
    left out with ENGINES=, or the benchmark has no such engine."""
    if run.get(bench) is None:
        return "not run"
    if any(_key_family(k) == fam for k in _failed_keys(run, bench)):
        return "FAILED"
    if any(_key_family(k) == fam for k in ((run.get(bench) or {}).get("skipped") or [])):
        return "not selected"
    if fam in ((run.get(bench) or {}).get("unreachable") or []):
        return "not reachable"
    return "not in this benchmark"


def _failed_note(run, bench, fam):
    keys = [k for k in _failed_keys(run, bench) if _key_family(k) == fam]
    return f" ({', '.join(keys)} FAILED)" if keys else ""


def _summary_cells(run, fam):
    # render
    e = render_engine_for(run, fam)
    if e and e.get("metrics", {}).get("median_ms") is not None:
        m = e["metrics"]
        if e.get("shape") == "warm-http":
            cell = f"{fmt(m.get('median_ms'), ' ms')}, holds {fmt(m.get('base_mb'), ' MB')}, warm HTTP"
        else:
            cell = f"{fmt(m.get('median_ms'), ' ms')}, {fmt(mem_per_render(m), ' MB')} per render, cold CLI"
        if e.get("local_build"):
            cell += " (LOCAL BUILD)"
        render_cell = cell
    elif fam in ((run.get("render") or {}).get("unreachable") or []):
        render_cell = "not reachable"
    else:
        render_cell = "not run"
    # throughput
    tp = (run.get("throughput") or {}).get("engines")
    prim = by_family(tp, fam, "nocache") or by_family(tp, fam)
    if prim:
        m = prim[0]["metrics"]
        cell = f"{fmt(m.get('req_s'))} req/s, settle {fmt(m.get('settle_mb'), ' MB')}"
        lru = by_family(tp, fam, "lru")
        if lru and lru[0] is not prim[0]:
            cell += f" (LRU: {fmt(lru[0]['metrics'].get('req_s'))} req/s)"
        tp_cell = cell + _failed_note(run, "throughput", fam)
    else:
        tp_cell = _absent_cell(run, "throughput", fam)
    # vector, dynamic render only
    vec = (run.get("vector") or {}).get("engines")
    prim = by_family(vec, fam, exclude_cache=True)
    if prim:
        m = prim[0]["metrics"]
        vec_cell = (f"{fmt(m.get('req_s'))} req/s, p50 {fmt(m.get('p50_ms'), ' ms', 0)}, "
                    f"settle {fmt(m.get('settle_mb'), ' MB', 0)}") + _failed_note(run, "vector", fam)
    else:
        vec_cell = _absent_cell(run, "vector", fam)
    return render_cell, tp_cell, vec_cell


def _header(run):
    dates = [b.get("date") for b in (run.get("render"), run.get("throughput"), run.get("vector")) if b and b.get("date")]
    out = ["# Benchmark report", ""]
    out.append(f"Run: `{os.path.basename(os.path.abspath(run['dir']))}`" + (f", {min(dates)}" if dates else ""))
    h = run.get("host")
    if h:
        out.append(f"Host: {h.get('hostname', '?')}, {h.get('cpu', '?')}, {h.get('cores', '?')} cores, "
                   f"{h.get('ram_gb', '?')} GB RAM, kernel {h.get('kernel', '?')}, Docker {h.get('docker', '?')}")
    vs = engine_versions(run)
    if vs:
        out.append("Engines: " + ", ".join(vs[f] for f in FAMILIES if f in vs))
    out.append("")
    return out


def render_markdown(run):
    out = _header(run)
    rp = (run.get("render") or {}).get("params", {})
    size = f"{rp.get('width', 800)}x{rp.get('height', 536)}"
    out += ["## Summary", "",
            f"| engine | render, one {size} image | throughput, 256x256 panning | vector COS2023, 256x256 |",
            "|---|---|---|---|"]
    vs = engine_versions(run)
    for fam in FAMILIES:
        failed_somewhere = any(_key_family(k) == fam for b in ("throughput", "vector") for k in _failed_keys(run, b))
        if fam not in vs and not failed_somewhere:
            continue
        r, t, v = _summary_cells(run, fam)
        out.append(f"| {vs.get(fam, FAMILY_NAME[fam])} | {r} | {t} | {v} |")
    out.append("")

    r = run.get("render")
    out += ["## Render (single request)", ""]
    if r:
        p = r.get("params", {})
        out.append(f"{p.get('width', '?')}x{p.get('height', '?')}, {p.get('timed_runs', '?')} timed runs after a "
                   f"warm-up, memory over {p.get('mem_runs', '?')} isolated renders (median).")
        out += ["", "| engine | shape | best | median | max | anon base | anon per render | output |", "|---|---|---|---|---|---|---|---|"]
        for label, e in r["engines"].items():
            m = e.get("metrics", {})
            shape = "warm HTTP" if e.get("shape") == "warm-http" else "cold CLI"
            name = version_label(e.get("family", label), e.get("version"), e.get("image"))
            if e.get("local_build"):
                name += " (LOCAL BUILD)"
            if m.get("median_ms") is None:          # listed in the meta but never measured
                why = ("not reachable" if e.get("family") in (r.get("unreachable") or [])
                       else "not run")
                out.append(f"| {name} | {shape} | {why} | | | | | |")
                continue
            out.append(f"| {name} | {shape} | {fmt(m.get('best_ms'), ' ms')} | {fmt(m.get('median_ms'), ' ms')} | "
                       f"{fmt(m.get('max_ms'), ' ms')} | {fmt(m.get('base_mb'), ' MB')} | {fmt(mem_per_render(m), ' MB')} | "
                       f"{'NOT A PNG' if m.get('png') is False else fmt(m.get('out_bytes'), ' B', 0)} |")
        gs = render_engine_for(run, "geoserver")
        if gs and gs.get("metrics", {}).get("jvm_opts"):
            out.append("")
            out.append(f"GeoServer JVM: `{gs['metrics']['jvm_opts']}`")
    else:
        out.append("not run")
    out.append("")

    t = run.get("throughput")
    out += ["## Throughput (sustained GetMap under panning)", ""]
    if t:
        p = t.get("params", {})
        warm = f" after {p['warmup']} warm-up per engine" if p.get("warmup") else ""
        out.append(f"N={p.get('n', '?')} requests{warm}, {p.get('conc', '?')} concurrent, {p.get('size', '?')}x{p.get('size', '?')}, "
                   f"{p.get('distinct_bboxes', '?')} distinct bboxes.")
        out += ["", "| engine | req/s | ok/N | baseline | peak | settle |", "|---|---|---|---|---|---|"]
        for e in t.get("engines", []):
            m = e.get("metrics", {})
            out.append(f"| {e.get('label')} | {fmt(m.get('req_s'))} | {m.get('ok', '?')}/{m.get('n', '?')} | "
                       f"{fmt(m.get('baseline_mb'), ' MB')} | {fmt(m.get('peak_mb'), ' MB')} | {fmt(m.get('settle_mb'), ' MB')} |")
        for k in _failed_keys(run, "throughput"):
            out.append(f"| {k} | FAILED | | | | |")
        gs = by_family(t.get("engines"), "geoserver")
        if gs and gs[0].get("jvm_opts"):
            out += ["", f"GeoServer JVM: `{gs[0]['jvm_opts']}`, GWC off, own container."]
        if t.get("plot"):
            out += ["", f"Memory-over-time plot: `{t['plot']}`"]
    else:
        out.append("not run")
    out.append("")

    v = run.get("vector")
    out += ["## Vector (COS2023 land cover as WMS)", ""]
    if v:
        p = v.get("params", {})
        out.append(f"N={p.get('n', '?')} requests after {p.get('warmup', '?')} warm-up, {p.get('conc', '?')} concurrent, "
                   f"{p.get('size', '?')}x{p.get('size', '?')}, {p.get('distinct_bboxes', '?')} distinct bboxes.")
        out += ["", "| engine | req/s | ok/N | p50 | p95 | base | peak | settle | note |", "|---|---|---|---|---|---|---|---|---|"]
        for e in v.get("engines", []):
            m = e.get("metrics", {})
            note = "cache-hit rate, not a render rate" if e.get("cache") else ""
            out.append(f"| {e.get('label')} | {fmt(m.get('req_s'))} | {m.get('ok', '?')}/{m.get('n', '?')} | "
                       f"{fmt(m.get('p50_ms'), ' ms', 0)} | {fmt(m.get('p95_ms'), ' ms', 0)} | {fmt(m.get('base_mb'), ' MB', 0)} | "
                       f"{fmt(m.get('peak_mb'), ' MB', 0)} | {fmt(m.get('settle_mb'), ' MB', 0)} | {note} |")
        for k in _failed_keys(run, "vector"):
            out.append(f"| {k} | FAILED | | | | | | | |")
    else:
        out.append("not run")
    out.append("")
    out.append(HOW_TO_READ)
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------------------------


def _delta(a, b, lower_is_better=False):
    if a is None or b is None:
        return "n/a"
    try:
        pct = (b - a) / a * 100 if a else 0.0
    except TypeError:
        return "n/a"
    return f"{a:.1f} -> {b:.1f} ({pct:+.1f}%)"


def _rows_render(run):
    return {label: e.get("metrics", {}) for label, e in render_engines(run).items()}


def _rows_list(run, bench):
    return {e.get("label"): e.get("metrics", {}) for e in (run.get(bench) or {}).get("engines", [])}


def compare_markdown(a, b):
    out = ["# Benchmark comparison", "",
           f"A: `{os.path.basename(os.path.abspath(a['dir']))}`  ->  B: `{os.path.basename(os.path.abspath(b['dir']))}`. "
           "Percentages are B relative to A.", ""]
    for fam in FAMILIES:
        va, vb = engine_versions(a).get(fam), engine_versions(b).get(fam)
        if va or vb:
            out.append(f"- {FAMILY_NAME[fam]}: {va or 'absent'} -> {vb or 'absent'}")
    out.append("")
    specs = [
        ("Render", _rows_render, [("median_ms", "median"), ("best_ms", "best"), ("max_ms", "max"), ("delta_median_mb", "anon per render"), ("base_mb", "anon base")]),
        ("Throughput", lambda r: _rows_list(r, "throughput"), [("req_s", "req/s"), ("peak_mb", "peak"), ("settle_mb", "settle")]),
        ("Vector", lambda r: _rows_list(r, "vector"), [("req_s", "req/s"), ("p50_ms", "p50"), ("p95_ms", "p95"), ("settle_mb", "settle")]),
    ]
    for title, rows_of, metrics in specs:
        ra, rb = rows_of(a), rows_of(b)
        out += [f"## {title}", "", "| engine | " + " | ".join(n for _, n in metrics) + " |", "|---|" + "---|" * len(metrics)]
        for label in sorted(set(ra) | set(rb)):
            ma, mb = ra.get(label, {}), rb.get(label, {})
            cells = []
            for key, _ in metrics:
                va = ma.get(key, mem_per_render(ma) if key == "delta_median_mb" else None)
                vb = mb.get(key, mem_per_render(mb) if key == "delta_median_mb" else None)
                cells.append(_delta(va, vb))
            out.append(f"| {label} | " + " | ".join(cells) + " |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------------------------
# Host facts
# ---------------------------------------------------------------------------------------------


def host_info():
    cpu, cores, ram = "?", os.cpu_count(), "?"
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal"):
                ram = round(int(line.split()[1]) / 1048576, 1)
                break
    except OSError:
        pass
    try:
        docker = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        docker = docker.replace("Docker version ", "").split(",")[0]
    except (OSError, subprocess.SubprocessError):
        docker = "?"
    return {"hostname": platform.node(), "cpu": cpu, "cores": cores, "ram_gb": ram,
            "kernel": platform.release(), "docker": docker,
            "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dir", nargs="?", help="results directory")
    ap.add_argument("--write", action="store_true", help="also write REPORT.md into the directory")
    ap.add_argument("--quiet", action="store_true", help="do not print the report")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"), help="compare two results directories")
    ap.add_argument("--host", metavar="DIR", help="write host.json into DIR and exit")
    args = ap.parse_args(argv)

    if args.host:
        os.makedirs(args.host, exist_ok=True)
        with open(os.path.join(args.host, "host.json"), "w") as f:
            json.dump(host_info(), f, indent=2)
        return 0
    if args.compare:
        sys.stdout.write(compare_markdown(load(args.compare[0]), load(args.compare[1])))
        return 0
    if not args.dir:
        ap.error("a results directory is required")
    run = load(args.dir)
    if not any(run[k] for k in ("render", "throughput", "vector")):
        print(f"[ERROR] nothing to report in {args.dir} (no render.meta.json, throughput.json or vector.json)", file=sys.stderr)
        return 1
    md = render_markdown(run)
    if args.write:
        with open(os.path.join(args.dir, "REPORT.md"), "w") as f:
            f.write(md)
    if not args.quiet:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
