#!/usr/bin/env bash
# Download the benchmark fixtures into ./data and verify each one against the server's
# SHA256SUMS. Safe to re-run: a file already present is verified, not downloaded again,
# and an interrupted download resumes from its .part file.
#
#   ./setup.sh                      the two files the benchmarks open (~2.6 GB)
#   FIXTURES=all ./setup.sh         everything on the server (adds s2_stack.cog.tif, 683 MB)
#   FIXTURES="COS2023v1-S2.gpkg" ./setup.sh
#   DATA_DIR=/mnt/big ./setup.sh    somewhere other than ./data
#
# Host and path come from config.yaml (vps: host / path) or VPS_HOST / VPS_PATH.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${DATA_DIR:-${SCRIPT_DIR}/data}"
CONFIG="${SCRIPT_DIR}/config.yaml"

cfg() { grep -m1 "^  $1:" "$CONFIG" | awk '{print $2}' | tr -d '"'; }
VPS_HOST="${VPS_HOST:-$(cfg host)}"
VPS_PATH="${VPS_PATH:-$(cfg path)}"
BASE="https://${VPS_HOST}${VPS_PATH}"

# Default: only what the ported benchmarks open. s2_stack.cog.tif is hosted for the NDVI
# benchmark, which is not ported yet, so it is opt-in.
FIXTURES="${FIXTURES:-cascais.cog.deflate.tif COS2023v1-S2.gpkg}"

mkdir -p "$DATA_DIR"
echo "Fixtures from ${BASE}/ into ${DATA_DIR}"

if ! curl -fsSL "${BASE}/SHA256SUMS" -o "${DATA_DIR}/SHA256SUMS"; then
    echo "[ERROR] cannot fetch ${BASE}/SHA256SUMS"
    echo "        Check VPS_HOST / VPS_PATH (config.yaml, section vps:) and that the server is up."
    exit 1
fi
if [[ "$FIXTURES" == "all" ]]; then
    FIXTURES="$(awk '{print $2}' "${DATA_DIR}/SHA256SUMS" | tr '\n' ' ')"
fi

# verify <name> [<file-on-disk>]: check a file in DATA_DIR against the SHA256SUMS entry for <name>.
verify() {
    local name=$1 file=${2:-$1}
    (cd "$DATA_DIR" && awk -v n="$name" -v f="$file" '$2==n {print $1"  "f}' SHA256SUMS \
        | sha256sum -c --quiet - >/dev/null 2>&1)
}

remote_size() {
    curl -sIL "${BASE}/$1" | awk 'tolower($1)=="content-length:" {gsub("\r","",$2); print $2}' | tail -1
}

local_size() { stat -c %s "$1" 2>/dev/null || stat -f %z "$1"; }

# download <name>: fetch to <name>.part (resuming if one exists), keep it as .part until the
# checksum passes, then rename. The checksum decides, not curl's exit code: resuming a .part
# that was already complete makes the server answer 416, which curl -f reports as a failure.
download() {
    local name=$1 dest="${DATA_DIR}/$1" resume=()
    [[ -f "$dest.part" ]] && resume=(-C -)
    curl -fL -# --retry 3 "${resume[@]}" -o "$dest.part" "${BASE}/$name" || true
    [[ -f "$dest.part" ]] || return 1
    if verify "$name" "$name.part"; then
        mv "$dest.part" "$dest"
        return 0
    fi
    # A .part at least as large as the remote file is complete and wrong: resuming it would
    # loop on 416 forever. A smaller one is a genuine partial and is kept for the next run.
    local have remote; have=$(local_size "$dest.part"); remote=$(remote_size "$name")
    if [[ -n "$remote" && "$have" -ge "$remote" ]]; then
        rm -f "$dest.part"
        echo "       (complete but checksum mismatch: partial deleted, run again)"
    else
        echo "       (partial kept at $dest.part, run again to resume)"
    fi
    return 1
}

ok=0; failed=0
for name in $FIXTURES; do
    if ! awk -v n="$name" '$2==n {found=1} END {exit !found}' "${DATA_DIR}/SHA256SUMS"; then
        echo "[FAIL] $name is not on the server (not in SHA256SUMS)"
        failed=$((failed+1)); continue
    fi
    dest="${DATA_DIR}/$name"
    if [[ -e "$dest" ]]; then
        if verify "$name"; then
            echo "[OK]   $name (present, checksum verified)"; ok=$((ok+1)); continue
        fi
        echo "[WARN] $name present but checksum mismatch, downloading again"; rm -f "$dest"
    fi
    echo "[GET]  $name"
    if download "$name"; then
        echo "[OK]   $name ($(du -h "$dest" | cut -f1), checksum verified)"; ok=$((ok+1))
    else
        echo "[FAIL] $name"; failed=$((failed+1))
    fi
done

echo
echo "Done: $ok verified, $failed failed. Data dir: $DATA_DIR"
[[ $failed -eq 0 ]] || exit 1
