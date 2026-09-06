#!/usr/bin/env bash
# Source this. Reads engine image pins from config.yaml so each version lives in ONE place.
# Needs REPO_DIR set by the caller. No PyYAML, no yq: the file is simple enough for grep.
#
#   cfg_engine_image mapserver   -> camptocamp/mapserver:8.6-gdal3.12
#   cfg_engine_image terraserve  -> ghcr.io/terraops-org/terraserve:0.2.0@sha256:...
cfg_engine_image() {
    # From the "  <engine>:" line to the next two-space key, print the first "    image:".
    # (A fixed grep -A12 window broke as soon as the terraserve block grew comments.)
    awk -v key="  $1:" '
        $0 == key { inside = 1; next }
        inside && /^  [A-Za-z]/ { inside = 0 }
        inside && /^    image:/ { sub(/^    image:[ \t]*/, ""); gsub(/"/, ""); print; exit }
    ' "${REPO_DIR}/config.yaml"
}
