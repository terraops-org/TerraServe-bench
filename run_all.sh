#!/usr/bin/env bash

# Orchestrate all benchmarks: render, throughput, vector

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_section() {
    echo -e "${GREEN}===============================================================================${NC}"
    echo -e "${GREEN}$1${NC}"
    echo -e "${GREEN}===============================================================================${NC}"
}

log_error() {
    echo -e "${RED}[ERROR] $1${NC}" >&2
}

log_warn() {
    echo -e "${YELLOW}[WARN] $1${NC}"
}

# Parse arguments
RUN_RENDER=${RUN_RENDER:-1}
RUN_THROUGHPUT=${RUN_THROUGHPUT:-1}
RUN_VECTOR=${RUN_VECTOR:-1}

# Any number of names: `run_all.sh throughput vector` runs those two. An unknown name is
# an error, not a silent "all" (and not a silent "first one only", which is what a
# single-argument parser does with the README's two-word example).
if [[ $# -gt 0 ]]; then
    RUN_RENDER=0; RUN_THROUGHPUT=0; RUN_VECTOR=0
    for arg in "$@"; do
    case "$arg" in
        render)
            RUN_RENDER=1
            ;;
        throughput)
            RUN_THROUGHPUT=1
            ;;
        vector)
            RUN_VECTOR=1
            ;;
        all)
            RUN_RENDER=1; RUN_THROUGHPUT=1; RUN_VECTOR=1
            ;;
        *)
            echo "Usage: $0 [render|throughput|vector|all ...]"
            echo ""
            echo "Runs benchmarks for:"
            echo "  render      - Single-render performance (MapServer, TerraServe, GeoServer)"
            echo "  throughput  - Sustained GetMap under panning (TerraServe, MapServer, GeoServer)"
            echo "  vector      - COS2023 land cover as WMS (TerraServe, MapServer, GeoServer)"
            echo "  all         - All benchmarks (default)"
            exit 1
            ;;
    esac
    done
fi

# Verify prerequisites
if [[ ! -f "${REPO_DIR}/setup.sh" ]]; then
    log_error "setup.sh not found. Ensure you're in the repo root."
    exit 1
fi

# Check if fixtures are available
if [[ ! -d "${REPO_DIR}/data" ]] || [[ -z "$(ls -A "${REPO_DIR}/data" 2>/dev/null || true)" ]]; then
    log_warn "Fixtures not found. Running setup..."
    bash "${REPO_DIR}/setup.sh" || {
        log_error "Failed to download fixtures. Check VPS_HOST configuration."
        exit 1
    }
fi

# One results directory for the whole run; each benchmark drops its JSON there and
# lib/report.py turns the lot into REPORT.md at the end.
RESULTS_DIR="${RESULTS_DIR:-${REPO_DIR}/results/$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$RESULTS_DIR"
RESULTS_DIR="$(cd "$RESULTS_DIR" && pwd)"
export RESULTS_DIR
ln -sfn "$RESULTS_DIR" "${REPO_DIR}/results/latest"
python3 "${REPO_DIR}/lib/report.py" --host "$RESULTS_DIR"
echo "Results directory: ${RESULTS_DIR}"
echo ""

# Track results
RESULTS=()
FAILED=()
START_TIME=$(date +%s)

run_benchmark() {
    local name=$1
    local script=$2

    if [[ ! -f "$script" ]]; then
        log_warn "Benchmark script not found: $script"
        RESULTS+=("SKIP: $name")
        return
    fi

    log_section "$name Benchmark"

    if bash "$script"; then
        RESULTS+=("PASS: $name")
    else
        RESULTS+=("FAIL: $name")
        FAILED+=("$name")
    fi
    echo ""
}

# Run selected benchmarks
[[ $RUN_RENDER -eq 1 ]] && run_benchmark "Render" "${REPO_DIR}/benchmarks/render/run.sh"
[[ $RUN_THROUGHPUT -eq 1 ]] && run_benchmark "Throughput" "${REPO_DIR}/benchmarks/throughput/run.sh"
[[ $RUN_VECTOR -eq 1 ]] && run_benchmark "Vector (COS2023 WMS)" "${REPO_DIR}/benchmarks/vector/run.sh"

# Summary
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

log_section "Benchmark Summary"
echo ""
for result in "${RESULTS[@]}"; do
    echo "  $result"
done
echo ""
echo "Elapsed time: $(printf '%dh %dm %ds' $((ELAPSED/3600)) $((ELAPSED%3600/60)) $((ELAPSED%60)))"
echo ""

if [[ ${#FAILED[@]} -eq 0 ]]; then
    echo -e "${GREEN}All benchmarks completed successfully!${NC}"
    rc=0
else
    log_error "Some benchmarks failed:"
    for name in "${FAILED[@]}"; do
        echo "  - $name"
    done
    rc=1
fi

echo ""
log_section "Report"
if python3 "${REPO_DIR}/lib/report.py" "$RESULTS_DIR" --write; then
    echo ""
    echo "Written to ${RESULTS_DIR}/REPORT.md (also results/latest/)."
    echo "Compare two runs: python3 lib/report.py --compare results/<A> results/<B>"
fi
exit $rc
