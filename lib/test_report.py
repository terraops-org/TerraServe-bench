#!/usr/bin/env python3
"""Tests for lib/report.py. Run: python3 lib/test_report.py"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import report  # noqa: E402


def write(dirpath, name, obj):
    with open(os.path.join(dirpath, name), "w") as f:
        if name.endswith(".jsonl"):
            for line in obj:
                f.write(json.dumps(line) + "\n")
        else:
            json.dump(obj, f)


def make_run(d, render=True, throughput=True, vector=True, ts_median=28.8, ts_req=325.4):
    write(d, "host.json", {"hostname": "box", "cpu": "CPU X", "cores": 16, "ram_gb": 62, "kernel": "7.1", "docker": "27"})
    if render:
        write(d, "render.meta.json", {
            "benchmark": "render", "date": "2026-09-06T08:00:00Z",
            "params": {"width": 800, "height": 536, "timed_runs": 6, "mem_runs": 5},
            "engines": {
                "MapServer": {"family": "mapserver", "shape": "cold-cli", "version": "MapServer version 8.6.5", "image": "ms:8.6"},
                "TerraServe": {"family": "terraserve", "shape": "cold-cli", "version": "terraserve 0.2.0", "image": "ts:0.2.0", "local_build": False},
                "GeoServer": {"family": "geoserver", "shape": "warm-http", "image": "gs:2.26.1"},
            }})
        write(d, "render.jsonl", [
            {"engine": "MapServer", "kind": "timing", "best_ms": 98.1, "median_ms": 108.7, "max_ms": 131.9, "out_bytes": 946341},
            {"engine": "MapServer", "kind": "memory", "base_mb": 4.8, "peak_median_mb": 50.6, "delta_median_mb": 45.8, "n": 5},
            {"engine": "TerraServe", "kind": "timing", "best_ms": 26.7, "median_ms": ts_median, "out_bytes": 1021882},
            {"engine": "TerraServe", "kind": "memory", "base_mb": 4.8, "peak_median_mb": 259.2, "delta_median_mb": 254.5, "n": 5},
            {"engine": "GeoServer", "kind": "version", "version": "GeoServer 2.26.1"},
            {"engine": "GeoServer", "kind": "timing", "best_ms": 76.7, "median_ms": 147.4, "out_bytes": 911482},
            {"engine": "GeoServer", "kind": "memory", "base_mb": 774.8, "peak_mb": 774.8, "delta_mb": 0.0, "jvm_opts": "-Xms1g -Xmx4g"},
        ])
    if throughput:
        write(d, "throughput.json", {
            "benchmark": "throughput", "date": "2026-09-06T08:01:00Z",
            "params": {"n": 100, "warmup": 100, "conc": 4, "size": 256, "distinct_bboxes": 600},
            "engines": [
                {"key": "mapserver", "label": "MapServer", "family": "mapserver", "shape": "warm-http", "version": "MapServer version 8.6.5",
                 "metrics": {"req_s": 93.9, "ok": 100, "n": 100, "baseline_mb": 30.9, "peak_mb": 201.6, "settle_mb": 201.6}},
                {"key": "ts-nocache", "label": "TerraServe-nocache", "family": "terraserve", "variant": "nocache", "shape": "warm-http", "version": "terraserve 0.2.0",
                 "metrics": {"req_s": ts_req, "ok": 100, "n": 100, "baseline_mb": 280.4, "peak_mb": 338.7, "settle_mb": 231.6}},
                {"key": "ts-lru", "label": "TerraServe-LRU", "family": "terraserve", "variant": "lru", "shape": "warm-http", "version": "terraserve 0.2.0",
                 "metrics": {"req_s": 373.8, "ok": 100, "n": 100, "baseline_mb": 276.8, "peak_mb": 372.0, "settle_mb": 291.2}},
            ]})
    if vector:
        write(d, "vector.json", {
            "benchmark": "vector", "date": "2026-09-06T08:02:00Z",
            "params": {"n": 100, "warmup": 50, "conc": 8, "size": 256, "distinct_bboxes": 81},
            "engines": [
                {"key": "ts-nocache", "label": "TerraServe-nocache", "family": "terraserve", "variant": "nocache", "cache": False, "shape": "warm-http", "version": "terraserve 0.2.0",
                 "metrics": {"req_s": 154.3, "p50_ms": 50, "p95_ms": 82, "base_mb": 199, "peak_mb": 298, "settle_mb": 153, "ok": 100, "n": 100}},
                {"key": "ts-wmscache", "label": "TerraServe-wmscache", "family": "terraserve", "variant": "wmscache", "cache": True, "shape": "warm-http", "version": "terraserve 0.2.0",
                 "metrics": {"req_s": 3772.7, "p50_ms": 2, "p95_ms": 3, "base_mb": 160, "peak_mb": 160, "settle_mb": 106, "ok": 100, "n": 100}},
                {"key": "mapserver", "label": "MapServer-8.6-FastCGI", "family": "mapserver", "cache": False, "shape": "warm-http", "version": "MapServer version 8.6.5",
                 "metrics": {"req_s": 96.9, "p50_ms": 26, "p95_ms": 43, "base_mb": 178, "peak_mb": 198, "settle_mb": 198, "ok": 100, "n": 100}},
            ]})


class Load(unittest.TestCase):
    def test_render_merges_meta_and_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            make_run(d)
            run = report.load(d)
            ts = run["render"]["engines"]["TerraServe"]
            self.assertEqual(ts["metrics"]["median_ms"], 28.8)
            self.assertEqual(ts["metrics"]["delta_median_mb"], 254.5)
            self.assertEqual(run["render"]["engines"]["GeoServer"]["version"], "GeoServer 2.26.1")

    def test_partial_run_has_none_for_missing_benchmarks(self):
        with tempfile.TemporaryDirectory() as d:
            make_run(d, throughput=False, vector=False)
            run = report.load(d)
            self.assertIsNone(run["throughput"])
            self.assertIsNone(run["vector"])
            self.assertIsNotNone(run["render"])


class Markdown(unittest.TestCase):
    def test_summary_has_one_row_per_engine_family_and_flags_the_shape(self):
        with tempfile.TemporaryDirectory() as d:
            make_run(d)
            md = report.render_markdown(report.load(d))
            self.assertIn("| MapServer 8.6.5 |", md)
            self.assertIn("| TerraServe 0.2.0 |", md)
            self.assertIn("| GeoServer 2.26.1 |", md)
            self.assertIn("warm HTTP", md)          # GeoServer's render cell says so
            self.assertIn("108.7 ms", md)
            self.assertIn("254.5 MB", md)

    def test_cache_hit_row_is_kept_out_of_the_summary(self):
        with tempfile.TemporaryDirectory() as d:
            make_run(d)
            md = report.render_markdown(report.load(d))
            summary = md.split("## Vector")[0]
            self.assertNotIn("3772", summary)
            self.assertIn("3772", md)               # but present in the detailed vector table
            self.assertIn("cache-hit", md)

    def test_missing_benchmark_says_not_run(self):
        with tempfile.TemporaryDirectory() as d:
            make_run(d, throughput=False)
            md = report.render_markdown(report.load(d))
            self.assertIn("not run", md)

    def test_skipped_geoserver_row_says_not_run_instead_of_blanks(self):
        with tempfile.TemporaryDirectory() as d:
            make_run(d)
            lines = [l for l in open(os.path.join(d, "render.jsonl")) if '"GeoServer"' not in l]
            open(os.path.join(d, "render.jsonl"), "w").writelines(lines)
            md = report.render_markdown(report.load(d))
            row = [l for l in md.splitlines() if l.startswith("| GeoServer") and "warm HTTP" in l][0]
            self.assertIn("not run", row)
            self.assertNotIn("| ? |", row)

    def test_render_table_has_a_max_column(self):
        with tempfile.TemporaryDirectory() as d:
            make_run(d)
            md = report.render_markdown(report.load(d))
            self.assertIn("| best | median | max |", md)
            row = [l for l in md.splitlines() if l.startswith("| MapServer 8.6.5 | cold CLI")][0]
            self.assertIn("| 131.9 ms |", row)

    def test_failed_engine_is_marked_failed_not_not_run(self):
        with tempfile.TemporaryDirectory() as d:
            make_run(d)
            vec = json.load(open(os.path.join(d, "vector.json")))
            vec["engines"] = [e for e in vec["engines"] if e["key"] == "mapserver"]
            vec["failed"] = ["ts-nocache", "ts-wmscache", "geoserver"]
            write(d, "vector.json", vec)
            md = report.render_markdown(report.load(d))
            summary = md.split("## Render")[0]
            ts = [l for l in summary.splitlines() if l.startswith("| TerraServe")][0]
            gs = [l for l in summary.splitlines() if l.startswith("| GeoServer")][0]
            self.assertTrue(ts.rstrip().endswith("| FAILED |"), ts)
            self.assertTrue(gs.rstrip().endswith("| FAILED |"), gs)
            self.assertIn("| ts-nocache | FAILED |", md)     # and in the detailed table

    def test_engine_absent_from_a_benchmark_is_not_called_not_run(self):
        with tempfile.TemporaryDirectory() as d:
            make_run(d)
            md = report.render_markdown(report.load(d))
            summary = md.split("## Render")[0]
            gs = [l for l in summary.splitlines() if l.startswith("| GeoServer")][0]
            cells = [c.strip() for c in gs.strip("|").split("|")]
            self.assertEqual(cells[2], "not in this benchmark")   # throughput has no GeoServer engine
            self.assertEqual(cells[3], "not in this benchmark")   # vector: not in this fixture either

    def test_throughput_states_its_warm_up(self):
        with tempfile.TemporaryDirectory() as d:
            make_run(d)
            md = report.render_markdown(report.load(d))
            self.assertIn("N=100 requests after 100 warm-up per engine", md)

    def test_engine_left_out_with_engines_says_not_selected(self):
        with tempfile.TemporaryDirectory() as d:
            make_run(d)
            tp = json.load(open(os.path.join(d, "throughput.json")))
            tp["skipped"] = ["geoserver"]
            write(d, "throughput.json", tp)
            md = report.render_markdown(report.load(d))
            gs = [l for l in md.split("## Render")[0].splitlines() if l.startswith("| GeoServer")][0]
            self.assertEqual([c.strip() for c in gs.strip("|").split("|")][2], "not selected")

    def test_local_build_is_called_out(self):
        with tempfile.TemporaryDirectory() as d:
            make_run(d)
            meta = json.load(open(os.path.join(d, "render.meta.json")))
            meta["engines"]["TerraServe"]["local_build"] = True
            write(d, "render.meta.json", meta)
            md = report.render_markdown(report.load(d))
            self.assertIn("LOCAL BUILD", md)


class Compare(unittest.TestCase):
    def test_compare_shows_both_values_and_percent(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            make_run(a, ts_median=30.0, ts_req=300.0)
            make_run(b, ts_median=27.0, ts_req=330.0)
            md = report.compare_markdown(report.load(a), report.load(b))
            self.assertIn("30.0 -> 27.0", md)
            self.assertIn("-10.0%", md)
            self.assertIn("300.0 -> 330.0", md)
            self.assertIn("+10.0%", md)

    def test_compare_tolerates_an_engine_missing_on_one_side(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            make_run(a); make_run(b, vector=False)
            md = report.compare_markdown(report.load(a), report.load(b))
            self.assertIn("n/a", md)


if __name__ == "__main__":
    unittest.main()
