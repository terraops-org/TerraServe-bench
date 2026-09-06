#!/usr/bin/env python3
"""Micro-benchmark one render command: warm-up, then N timed runs.

Reports best, median and max wall-clock only. 
Memory is NOT measured here: it is sampled from the container's cgroup by lib/cgroup_mem.py

    bench.py [--json] <label> <expect_out.png> -- <cmd> [args...]

--json prints a second line, a JSON object starting with "{", for the results directory.
It goes to stdout because this runs inside the container while the results directory is
on the host; run.sh splits the two lines apart.
"""
import json
import subprocess
import sys
import time

argv = sys.argv[1:]
json_out = False
if argv and argv[0] == "--json":
    json_out = True
    argv = argv[1:]
label = argv[0]
out = argv[1]
assert argv[2] == "--", "expected -- before the command"
cmd = argv[3:]

N = 6
subprocess.run(cmd, capture_output=True)  # warm-up (discarded)
times = []
for _ in range(N):
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True)
    times.append(time.time() - t0)
    if r.returncode != 0:
        sys.stderr.write(f"[{label}] FAILED rc={r.returncode}: {r.stderr.decode(errors='replace')[:400]}\n")
        sys.exit(1)

times.sort()
best = times[0] * 1000
median = times[len(times) // 2] * 1000

# confirm output looks like a PNG of the expected size
sz, png = None, False
try:
    with open(out, "rb") as f:
        head = f.read(8)
    import os
    sz = os.path.getsize(out)
    png = head[:4] == b"\x89PNG"
    okstr = f"out={sz}B png={png}"

except Exception as e:  # noqa: BLE001
    okstr = f"out=? ({e})"

worst = times[-1] * 1000
print(f"{label:14s} best={best:8.1f}ms  median={median:8.1f}ms  max={worst:8.1f}ms  {okstr}")
if json_out:
    print(json.dumps({"engine": label, "kind": "timing", "best_ms": round(best, 1),
                      "median_ms": round(median, 1), "max_ms": round(worst, 1),
                      "out_bytes": sz, "png": png, "timed_runs": N}))
