#!/usr/bin/env bash
# Vector benchmark: COS2023 land cover served as WMS GetMap by
# TerraServe vs MapServer vs GeoServer, from the SAME GeoPackage with the SAME
# classification (all three read cos2023.sld; MapServer reads a mapfile generated
# from it).

set -euo pipefail

BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$(dirname "$BENCH_DIR")")"
LIB_DIR="${REPO_DIR}/lib"

# The containers mount this directory as /data, so it must hold the REAL GeoPackage.
# Docker does not follow a host symlink inside a bind mount.
GPKG_DIR="${GPKG_DIR:-${REPO_DIR}/data}"
GPKG="${GPKG_DIR}/COS2023v1-S2.gpkg"

N=${N:-600}
WARMUP=${WARMUP:-300}
CONC=${CONC:-16}
ENGINES=${ENGINES:-ts-nocache,ts-wmscache,mapserver,geoserver}
# Engine images come from config.yaml (one place to bump a version); env overrides win.
# TerraServe is the pinned public release image here, run as-is (fonts baked in).
source "${LIB_DIR}/config.sh"
TS_IMAGE=${TS_IMAGE:-$(cfg_engine_image terraserve)}
MS_IMAGE=${MS_IMAGE:-$(cfg_engine_image mapserver)}
GS_IMAGE=${GS_IMAGE:-$(cfg_engine_image geoserver)}

# Results as JSON into one directory (shared by all three benchmarks under run_all.sh).
RESULTS_DIR="${RESULTS_DIR:-${REPO_DIR}/results/$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$RESULTS_DIR"
RESULTS_DIR="$(cd "$RESULTS_DIR" && pwd)"
ln -sfn "$RESULTS_DIR" "${REPO_DIR}/results/latest"
[[ -f "${RESULTS_DIR}/host.json" ]] || python3 "${LIB_DIR}/report.py" --host "$RESULTS_DIR"

echo "=========================================="
echo "Vector Benchmark: COS2023 land cover"
echo "=========================================="

if [[ ! -f "$GPKG" ]] || [[ -L "$GPKG" && ! -e "$GPKG" ]]; then
    echo "[ERROR] GeoPackage not found (or is a dangling symlink): $GPKG"
    echo "        Run ./setup.sh, or point GPKG_DIR at a directory holding the real file:"
    echo "          GPKG_DIR=/path/to/gpkg ./run.sh"
    exit 1
fi

# Local image first, pull only if absent: the pin is a digest so a re-pull gains nothing,
# an offline re-run must still work, and TS_IMAGE may name an image that only exists locally.
if ! docker image inspect "$TS_IMAGE" >/dev/null 2>&1; then
    if ! docker pull -q "$TS_IMAGE" >/dev/null 2>&1; then
        echo "[ERROR] TerraServe image '$TS_IMAGE' is not local and cannot be pulled."
        echo "        Check the pin in config.yaml (engines.terraserve.image) or override with TS_IMAGE=<image>."
        exit 1
    fi
fi

echo "GeoPackage: $(basename "$GPKG") ($(du -hL "$GPKG" | cut -f1))"
echo "TerraServe: $(docker run --rm "$TS_IMAGE" --version) from ${TS_IMAGE}"
echo "MapServer:  ${MS_IMAGE}"
echo "GeoServer:  ${GS_IMAGE}"
echo "Engines:    $ENGINES"
echo "Load:       N=$N warmup=$WARMUP conc=$CONC"
echo ""

echo "Cross-engine WMS GetMap (TerraServe vs MapServer vs GeoServer)"
# A failed engine exits non-zero. Still write the report (it marks the engine FAILED),
# then propagate the failure so run_all.sh shows FAIL rather than a clean PASS.
rc=0
GPKG_DIR="$GPKG_DIR" N="$N" WARMUP="$WARMUP" CONC="$CONC" ENGINES="$ENGINES" \
    TS_IMAGE="$TS_IMAGE" MS_IMAGE="$MS_IMAGE" GS_IMAGE="$GS_IMAGE" RESULTS_DIR="$RESULTS_DIR" \
    python3 "${BENCH_DIR}/cos2023_vector_bench.py" || rc=$?


python3 "${LIB_DIR}/report.py" "$RESULTS_DIR" --write --quiet || true
echo ""
echo "=========================================="
echo "Results: ${RESULTS_DIR} (REPORT.md, vector.json, sample_cos_<engine>.png for visual parity)"
exit $rc
