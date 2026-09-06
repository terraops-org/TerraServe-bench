#!/usr/bin/env bash
# Sustained throughput benchmark: memory + requests/sec over time
# Tests long-running servers with panning (varying bbox)

set -euo pipefail

BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$(dirname "$BENCH_DIR")")"
DATA_DIR="${DATA_DIR:-${REPO_DIR}/data}"
LIB_DIR="${REPO_DIR}/lib"

# Verify setup
if [[ ! -f "${DATA_DIR}/cascais.cog.deflate.tif" ]]; then
    echo "[ERROR] Cascais COG not found. Run: ./setup.sh"
    exit 1
fi

# Configuration
COG="${DATA_DIR}/cascais.cog.deflate.tif"
STYLE="${REPO_DIR}/config/styles/rgb.json"
MAPFILE="${REPO_DIR}/config/mapfiles/cascais_wms.map"

# Benchmark parameters (environment overrides, defaults here; config.yaml holds only the pins)
N=${N:-800}              # Total requests
CONC=${CONC:-4}          # Concurrent connections
WARMUP=${WARMUP:-100}    # Discarded requests per engine before the measured N
ENGINES=${ENGINES:-mapserver,ts-nocache,ts-lru,geoserver}
# Engine images come from config.yaml (one place to bump a version); env overrides win.
# TS_BIN=/path/to/terraserve swaps a local build in for the pinned release (developer use).
source "${LIB_DIR}/config.sh"
MS_IMAGE=${MS_IMAGE:-$(cfg_engine_image mapserver)}
TS_IMAGE=${TS_IMAGE:-$(cfg_engine_image terraserve)}
GS_IMAGE=${GS_IMAGE:-$(cfg_engine_image geoserver)}

# Results as JSON into one directory (shared by all three benchmarks under run_all.sh).
RESULTS_DIR="${RESULTS_DIR:-${REPO_DIR}/results/$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$RESULTS_DIR"
RESULTS_DIR="$(cd "$RESULTS_DIR" && pwd)"
ln -sfn "$RESULTS_DIR" "${REPO_DIR}/results/latest"
[[ -f "${RESULTS_DIR}/host.json" ]] || python3 "${LIB_DIR}/report.py" --host "$RESULTS_DIR"

echo "=========================================="
echo "Sustained Throughput Benchmark"
echo "=========================================="
echo "Concurrent: ${CONC}"
echo "Total requests: ${N} (after ${WARMUP} warm-up per engine)"
echo "Engines:    ${ENGINES}"
echo ""

# sustained.py builds its own image and starts/stops its own containers. It reads its
# settings from the ENVIRONMENT, not from argv, so export rather than pass them.
echo "Sustained load: MapServer vs TerraServe (no-cache and LRU) vs GeoServer"
# A failed engine exits non-zero. Still write the report (it marks the engine FAILED),
# then propagate the failure so run_all.sh shows FAIL rather than a clean PASS.
rc=0
N="$N" CONC="$CONC" WARMUP="$WARMUP" ENGINES="$ENGINES" MS_IMAGE="$MS_IMAGE" TS_IMAGE="$TS_IMAGE" \
    GS_IMAGE="$GS_IMAGE" TS_BIN="${TS_BIN:-}" RESULTS_DIR="$RESULTS_DIR" python3 "${BENCH_DIR}/sustained.py" || rc=$?


python3 "${LIB_DIR}/report.py" "$RESULTS_DIR" --write --quiet || true
echo ""
echo "Results: ${RESULTS_DIR} (REPORT.md, throughput.json, sustained.png)"
echo ""
echo "=========================================="
echo "For single-render tests, run:"
echo "  cd ../render && ./run.sh"
echo ""
exit $rc
