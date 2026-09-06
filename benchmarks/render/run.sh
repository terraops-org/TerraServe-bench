#!/usr/bin/env bash
# Render benchmark: MapServer vs TerraServe vs GeoServer
# Measures per-invocation CLI/HTTP render time and peak memory

set -euo pipefail

BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$(dirname "$BENCH_DIR")")"
DATA_DIR="${DATA_DIR:-${REPO_DIR}/data}"
CONFIG_DIR="${REPO_DIR}/config"
LIB_DIR="${REPO_DIR}/lib"

# Verify fixtures exist
if [[ ! -f "${DATA_DIR}/cascais.cog.deflate.tif" ]]; then
    echo "[ERROR] Cascais COG not found. Run: ./setup.sh"
    exit 1
fi

# Configuration
COG="${DATA_DIR}/cascais.cog.deflate.tif"
STYLE="${CONFIG_DIR}/styles/rgb.json"
MAPFILE="${CONFIG_DIR}/mapfiles/cascais.map"

# Benchmark parameters
W=${W:-800}
H=${H:-536}
BBOX="-116201.25,-108717.25,-109034.0,-103918.25"  # Full Cascais extent, EPSG:3763
# Engine images come from config.yaml (one place to bump a version); env overrides win.
source "${LIB_DIR}/config.sh"
MS_IMAGE=${MS_IMAGE:-$(cfg_engine_image mapserver)}
GS_IMAGE=${GS_IMAGE:-$(cfg_engine_image geoserver)}
TS_IMAGE=${TS_IMAGE:-$(cfg_engine_image terraserve)}

# Every run writes its numbers as JSON into one results directory (run_all.sh sets it so
# the three benchmarks share one); lib/report.py turns that into REPORT.md.
RESULTS_DIR="${RESULTS_DIR:-${REPO_DIR}/results/$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$RESULTS_DIR"
RESULTS_DIR="$(cd "$RESULTS_DIR" && pwd)"
ln -sfn "$RESULTS_DIR" "${REPO_DIR}/results/latest"
[[ -f "${RESULTS_DIR}/host.json" ]] || python3 "${LIB_DIR}/report.py" --host "$RESULTS_DIR"

finish() {
    python3 "${LIB_DIR}/report.py" "$RESULTS_DIR" --write --quiet || true
    echo ""
    echo "Results: ${RESULTS_DIR} (REPORT.md, render.jsonl, the rendered PNGs)"
}

echo "=========================================="
echo "Render Benchmark: MapServer vs TerraServe vs GeoServer"
echo "=========================================="
echo "Dataset: Cascais (EPSG:3763)"
echo "Size: ${W}x${H}, Resampling: nearest"
echo "COG: $(basename $COG)"
echo ""

# DO NOT split this into two images.
# TS_BIN=/path/to/terraserve swaps in a local build instead (developer use). The version
# lines printed below are what tell a reader which TerraServe a run actually used.
STAGE="$(mktemp -d)"
CLI_CONTAINER=ts-bench-run

cleanup() {
    rm -rf "$STAGE"
    docker rm -f "$CLI_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Building bench image on ${MS_IMAGE} ..."
if [[ -n "${TS_BIN:-}" ]]; then
    if [[ ! -f "$TS_BIN" ]]; then
        echo "[ERROR] TS_BIN=${TS_BIN} does not exist"
        exit 1
    fi
    cp -L "$TS_BIN" "$STAGE/terraserve"
    printf 'FROM %s\nCOPY terraserve /usr/local/bin/terraserve\n' "$MS_IMAGE" > "$STAGE/Dockerfile"
    docker build -q -f "$STAGE/Dockerfile" -t ts-bench:latest "$STAGE" >/dev/null
    echo "TerraServe: LOCAL BUILD from ${TS_BIN}, NOT the pinned release"
else
    docker build -q --build-arg MS_IMAGE="$MS_IMAGE" --build-arg TS_IMAGE="$TS_IMAGE" \
        -f "${REPO_DIR}/dockerfiles/Dockerfile.terraserve" -t ts-bench:latest \
        "${REPO_DIR}/dockerfiles" >/dev/null
    echo "TerraServe: ${TS_IMAGE}"
fi
TS_VERSION="$(docker run --rm --entrypoint terraserve ts-bench:latest --version)"
MS_VERSION="$(docker run --rm --entrypoint map2img ts-bench:latest -v 2>&1 | head -1 | cut -d' ' -f1-3)"
echo "TerraServe: ${TS_VERSION}"
echo "MapServer:  ${MS_VERSION}"

# Engine identities and parameters for the report; the measurements are appended to
# render.jsonl by bench.py, cgroup_mem.py and geoserver-benchmark.py as they run.
W="$W" H="$H" BBOX="$BBOX" MEM_RUNS="${MEM_RUNS:-5}" TS_IMAGE="$TS_IMAGE" MS_IMAGE="$MS_IMAGE" \
GS_IMAGE="$GS_IMAGE" TS_VERSION="$TS_VERSION" MS_VERSION="$MS_VERSION" TS_BIN="${TS_BIN:-}" \
python3 - "$RESULTS_DIR" <<'PY'
import datetime, json, os, sys
e = os.environ
local = bool(e["TS_BIN"])
meta = {
    "benchmark": "render",
    "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "params": {"width": int(e["W"]), "height": int(e["H"]), "bbox": e["BBOX"], "crs": "EPSG:3763",
               "resample": "nearest", "timed_runs": 6, "mem_runs": int(e["MEM_RUNS"])},
    "engines": {
        "MapServer": {"family": "mapserver", "shape": "cold-cli", "version": e["MS_VERSION"], "image": e["MS_IMAGE"]},
        "TerraServe": {"family": "terraserve", "shape": "cold-cli", "version": e["TS_VERSION"],
                       "image": f"local:{e['TS_BIN']}" if local else e["TS_IMAGE"], "local_build": local},
        "GeoServer": {"family": "geoserver", "shape": "warm-http", "image": e["GS_IMAGE"]},
    },
}
json.dump(meta, open(os.path.join(sys.argv[1], "render.meta.json"), "w"), indent=2)
open(os.path.join(sys.argv[1], "render.jsonl"), "w").close()   # fresh file for this run
PY

# The CLI engines run inside ONE long-lived container that just sleeps, and each render
# goes in over docker exec. That is not for speed: memory is sampled from this
# container's cgroup on the host, and a --rm container that appears and vanishes per
# render has no cgroup to point a sampler at.


docker rm -f "$CLI_CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CLI_CONTAINER" \
    -v "${COG}:/data/cog.tif:ro" \
    -v "${LIB_DIR}:/bench:ro" \
    -v "${CONFIG_DIR}:/config:ro" \
    --entrypoint sleep ts-bench:latest infinity >/dev/null
echo ""

# Timing and memory are two SEPARATE passes over the same command, on purpose.
#
# Timing is a warm-up plus 6 runs, because one render is too noisy to trust.
# Memory is exactly ONE render, because the sampler watches a cgroup and a cgroup
# has no idea where one process ends and the next begins. Sampling the 7-run loop
# reported 682 MB for terraserve where a single render peaks at ~245 MB: consecutive
# renders' pages had not been reclaimed yet, so their lifetimes overlapped and the
# sum looked like a peak. If you ever merge these two passes back together, that
# number comes straight back.
run_in_docker() {
    local engine=$1
    shift

    # bench.py runs inside the container and prints its JSON record on stdout (the
    # results directory is on the host); split the human line from the JSON line here.
    local out
    out="$(docker exec "$CLI_CONTAINER" python3 /bench/bench.py --json "$engine" "/tmp/out.png" -- "$@")"
    printf '%s\n' "$out" | grep -v '^{' || true
    printf '%s\n' "$out" | grep '^{' >> "${RESULTS_DIR}/render.jsonl" || true
    docker cp "${CLI_CONTAINER}:/tmp/out.png" \
        "${RESULTS_DIR}/render-$(echo "$engine" | tr '[:upper:]' '[:lower:]').png" >/dev/null 2>&1 || true

    python3 "${LIB_DIR}/cgroup_mem.py" --container "$CLI_CONTAINER" --label "$engine" \
        --repeat "${MEM_RUNS:-5}" --quiet-cmd --json "${RESULTS_DIR}/render.jsonl" \
        -- docker exec "$CLI_CONTAINER" "$@"
}

# MapServer
echo "[1/3] MapServer (${MS_IMAGE})"
run_in_docker MapServer \
    map2img -m /config/mapfiles/cascais.map -o /tmp/out.png

# TerraServe
echo "[2/3] TerraServe"
run_in_docker TerraServe \
    terraserve render --cog /data/cog.tif \
    --bbox "$BBOX" --crs EPSG:3763 \
    --width "$W" --height "$H" \
    --resample nearest --style /config/styles/rgb.json \
    --out /tmp/out.png

# GeoServer benchmark
echo "[3/3] GeoServer (${GS_IMAGE})"

GS_URL=${GS_URL:-http://localhost:8080/geoserver}
GS_TIMEOUT=30

# Is GeoServer up? If its compose container exists it may still be booting (a fresh data
# volume takes a minute or two to initialise), so wait for it. With no container at all,
# give up after a few seconds and skip.
echo -n "    Checking GeoServer at ${GS_URL} "
gs_up=0
if [[ -n "$(cd "$REPO_DIR" && docker compose ps -q geoserver 2>/dev/null)" ]]; then
    tries=60; pause=3        # up to three minutes for a booting container
else
    tries=3; pause=2
fi
for i in $(seq 1 "$tries"); do
    if python3 "${BENCH_DIR}/geoserver-setup.py" \
        --gs-url "$GS_URL" --cog-cascais "$COG" --health-check-only >/dev/null 2>&1; then
        gs_up=1
        break
    fi
    echo -n "."
    sleep "$pause"
done

if [[ $gs_up -eq 0 ]]; then
    echo " not reachable"
    echo "    [SKIP] Start it with: docker compose up -d geoserver postgis"
    finish
    exit 0
fi
echo " ok"

# Initialize layers via REST API
echo "    Setting up layers..."
if python3 "${BENCH_DIR}/geoserver-setup.py" --gs-url "$GS_URL" --cog-cascais "$COG" 2>&1 | grep -E '^\[' ; then
    echo "    Layers ready."
else
    # pipefail: a non-zero from the setup script reaches here even though grep succeeded.
    echo "    [ERROR] GeoServer provisioning failed (see the lines above)."
    finish
    exit 1
fi

# Run WMS GetMap benchmark
echo "    Running WMS GetMap benchmark..."
# --bbox=... and not --bbox ... : the bbox starts with a minus sign, so in the
# separated form argparse reads it as another flag and dies on "expected one argument".
python3 "${BENCH_DIR}/geoserver-benchmark.py" \
    --gs-url "$GS_URL" \
    --layer "benchmarks:cascais_rgb_cog" \
    --bbox="$BBOX" \
    --crs EPSG:3763 \
    --width "$W" \
    --height "$H" \
    --warmup 1 \
    --runs 6 \
    --json "${RESULTS_DIR}/render.jsonl" \
    --output "${RESULTS_DIR}/render-geoserver.png"

finish
echo ""
echo "=========================================="
echo "For sustained throughput tests, run:"
echo "  cd ../throughput && ./run.sh"
echo ""
