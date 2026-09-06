#!/usr/bin/env python3
"""Sample a container's anonymous memory from its cgroup, on the host.

Why anon and not peak RSS: `anon` is memory the process allocated and must free
itself. 

It EXCLUDES file-backed page cache, which every engine here
accumulates just by reading the same COG and which the kernel reclaims for free.
Comparing `ru_maxrss` (per process, includes file pages) against a JVM's container
footprint compares two different quantities, so all three engines use this instead.

Requires cgroup v2 and a Docker driver that puts containers under a readable
cgroup path. Fails loudly rather than reporting zero.

Use as a library:

    with AnonSampler.for_container("my-container") as s:
        ...do the work...
    print(s.peak, s.delta)

Or wrap a host command:

    python3 lib/cgroup_mem.py --container my-container -- curl http://...
"""
import argparse
import os
import subprocess
import sys
import threading
import time

# Where Docker puts a container's cgroup, most common layouts first.
CGROUP_LAYOUTS = [
    "/sys/fs/cgroup/system.slice/docker-{id}.scope",
    "/sys/fs/cgroup/docker/{id}",
    "/sys/fs/cgroup/system.slice/docker.service/docker-{id}.scope",
    "/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/user.slice/docker-{id}.scope",
]


class CgroupNotFound(RuntimeError):
    pass


def resolve_container_id(name_or_id):
    """Full container ID for a name, ID prefix, or compose service."""
    try:
        out = subprocess.run(
            ["docker", "inspect", "--format", "{{.Id}}", name_or_id],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    raise CgroupNotFound(f"docker inspect could not resolve container {name_or_id!r}")


def find_cgroup(container_id):
    """Directory holding memory.stat for this container."""
    uid = os.getuid()
    tried = []
    for layout in CGROUP_LAYOUTS:
        path = layout.format(id=container_id, uid=uid)
        tried.append(path)
        if os.path.exists(os.path.join(path, "memory.stat")):
            return path
    raise CgroupNotFound(
        "no cgroup v2 memory.stat found for this container. Tried:\n  "
        + "\n  ".join(tried)
        + "\nThe memory column needs cgroup v2 and the systemd or cgroupfs Docker driver."
    )


def read_anon(stat_path):
    """Anonymous bytes from memory.stat, or None if it went away."""
    try:
        with open(stat_path) as f:
            for line in f:
                if line.startswith("anon "):
                    return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


class AnonSampler:
    """Poll cgroup anon in a background thread; keep baseline, peak and delta."""

    def __init__(self, cgroup_path, interval=0.001):
        self.stat_path = os.path.join(cgroup_path, "memory.stat")
        self.interval = interval
        self.baseline = None
        self.peak = 0
        self.samples = 0
        self._stop = threading.Event()
        self._thread = None

    @classmethod
    def for_container(cls, name_or_id, interval=0.001):
        return cls(find_cgroup(resolve_container_id(name_or_id)), interval)

    def _run(self):
        while not self._stop.is_set():
            v = read_anon(self.stat_path)
            if v is not None:
                if self.baseline is None:
                    self.baseline = v
                if v > self.peak:
                    self.peak = v
                self.samples += 1
            # sleep, never spin: a busy sampler steals CPU from the thing being timed
            self._stop.wait(self.interval)

    def __enter__(self):
        v = read_anon(self.stat_path)
        if v is None:
            raise CgroupNotFound(f"cannot read {self.stat_path}")
        self.baseline = v
        self.peak = v
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        return False

    @property
    def delta(self):
        """Growth over baseline: what the work itself added."""
        return self.peak - (self.baseline or 0)

    def summary(self, label=""):
        mb = 1024 * 1024
        pre = f"{label:14s} " if label else ""
        return (f"{pre}anon base={self.baseline / mb:8.1f}MB "
                f"peak={self.peak / mb:8.1f}MB "
                f"delta={self.delta / mb:8.1f}MB  ({self.samples} samples)")


def append_json(path, record):
    """One JSON object per line, appended: several tools write the same file in turn."""
    import json
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Sample container anon memory while a command runs")
    ap.add_argument("--container", required=True, help="Container name or ID to sample")
    ap.add_argument("--label", default="", help="Label for the summary line")
    ap.add_argument("--interval", type=float, default=0.001, help="Sample interval in seconds")
    ap.add_argument("--quiet-cmd", action="store_true",
                    help="Discard the wrapped command's output, keep the summary line. "
                         "Use this instead of redirecting the whole invocation, which "
                         "would swallow the summary too.")
    ap.add_argument("--repeat", type=int, default=1, metavar="N",
                    help="Run the command N times, each with a fresh sampler, and report "
                         "median and max peak. A single sample is not enough: the same "
                         "render varied by 40 MB run to run at small output sizes.")
    ap.add_argument("--settle-slack", type=float, default=8.0, metavar="MB",
                    help="Between repeats, wait until anon falls back to within this "
                         "many MB of where it started. Default 8.")
    ap.add_argument("--settle-timeout", type=float, default=10.0, metavar="SEC",
                    help="Give up waiting for reclaim after this long. The reported "
                         "baseline range shows whether it actually settled.")
    ap.add_argument("--json", metavar="FILE",
                    help="Append the result as one JSON line to FILE (the results directory).")
    ap.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="-- followed by the host command to run while sampling")
    args = ap.parse_args()

    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        ap.error("expected -- followed by a command")

    try:
        cgroup = find_cgroup(resolve_container_id(args.container))
    except CgroupNotFound as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    sink = subprocess.DEVNULL if args.quiet_cmd else None
    peaks, deltas, bases, rc = [], [], [], 0
    stat_path = os.path.join(cgroup, "memory.stat")
    settle_floor = (read_anon(stat_path) or 0) + args.settle_slack * 1024 * 1024

    for _ in range(max(1, args.repeat)):
        # Wait for the previous run's pages to be reclaimed. Without this the next
        # run starts on top of memory that is still held and the peaks overlap: five
        # back-to-back renders reported a 470 MB "peak" for a render that costs 250 MB.
        deadline = time.monotonic() + args.settle_timeout
        while time.monotonic() < deadline:
            cur = read_anon(stat_path)
            if cur is None or cur <= settle_floor:
                break
            time.sleep(0.02)

        sampler = AnonSampler(cgroup, args.interval)
        with sampler:
            rc = subprocess.run(cmd, stdout=sink, stderr=sink).returncode
        peaks.append(sampler.peak)
        deltas.append(sampler.delta)
        bases.append(sampler.baseline or 0)
        if rc != 0:
            break

    mb = 1024 * 1024
    if args.repeat <= 1:
        sampler.baseline, sampler.peak = bases[0], peaks[0]
        print(sampler.summary(args.label))
        if args.json:
            append_json(args.json, {"engine": args.label, "kind": "memory", "base_mb": round(bases[0] / mb, 1),
                                    "peak_mb": round(peaks[0] / mb, 1), "delta_mb": round(deltas[0] / mb, 1), "n": 1})
        return rc

    # A baseline that does not return to its starting value between repeats means the
    # previous run's memory was still held, so the peaks overlap and are not per-run.
    peaks.sort()
    deltas.sort()
    mid = len(peaks) // 2
    pre = f"{args.label:14s} " if args.label else ""
    print(f"{pre}anon base={min(bases) / mb:7.1f}..{max(bases) / mb:.1f}MB "
          f"peak_median={peaks[mid] / mb:8.1f}MB peak_max={peaks[-1] / mb:8.1f}MB "
          f"delta_median={deltas[mid] / mb:8.1f}MB  (n={len(peaks)})")
    if args.json:
        append_json(args.json, {"engine": args.label, "kind": "memory",
                                "base_mb": round(min(bases) / mb, 1), "base_max_mb": round(max(bases) / mb, 1),
                                "peak_median_mb": round(peaks[mid] / mb, 1), "peak_max_mb": round(peaks[-1] / mb, 1),
                                "delta_median_mb": round(deltas[mid] / mb, 1), "n": len(peaks)})
    return rc


if __name__ == "__main__":
    sys.exit(main())
