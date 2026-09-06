#!/usr/bin/env bash
# Provision GeoServer for the COS2023 vector WMS benchmark: workspace + GeoPackage store +
# featuretype + the cos2023 SLD as default style. Reads the SAME gpkg + SAME SLD as TerraServe
# and MapServer, for real styling parity. GWC is left OFF (dynamic-render parity).
#
# Usage: geoserver_cos2023_setup.sh <rest_root> <sld_path>
#   rest_root e.g. http://localhost:19100/geoserver/rest
set -u
REST="$1"; SLD="$2"
AUTH="-u admin:geoserver"
WS="bench"; STORE="cos2023"; LAYER="cos2023v1"; STYLE="cos2023"

# 1) wait for the REST API (JVM/Tomcat boot can take ~30-60s)
for i in $(seq 1 90); do
  code=$(curl -s -o /dev/null -w "%{http_code}" $AUTH "$REST/about/version.json" 2>/dev/null)
  [ "$code" = "200" ] && { echo "  geoserver REST up after ${i}s"; break; }
  sleep 1
done

j() { curl -s $AUTH -H "Content-Type: application/json" "$@"; }

# 2) workspace
j -XPOST -d "{\"workspace\":{\"name\":\"$WS\"}}" "$REST/workspaces" >/dev/null

# 3) GeoPackage datastore -> the mounted /data/COS2023v1-S2.gpkg
j -XPOST -d "{\"dataStore\":{\"name\":\"$STORE\",\"connectionParameters\":{\"entry\":[
  {\"@key\":\"database\",\"\$\":\"file:/data/COS2023v1-S2.gpkg\"},
  {\"@key\":\"dbtype\",\"\$\":\"geopkg\"}]}}}" \
  "$REST/workspaces/$WS/datastores" >/dev/null

# 4) publish the featuretype (native SRS EPSG:3763)
j -XPOST -d "{\"featureType\":{\"name\":\"$LAYER\",\"nativeName\":\"$LAYER\",
  \"srs\":\"EPSG:3763\",\"enabled\":true}}" \
  "$REST/workspaces/$WS/datastores/$STORE/featuretypes" >/dev/null

# 5) create the style (raw=true skips GeoServer's strict SLD validation) and upload the SLD body
j -XPOST -d "{\"style\":{\"name\":\"$STYLE\",\"filename\":\"$STYLE.sld\"}}" \
  "$REST/workspaces/$WS/styles" >/dev/null
curl -s $AUTH -H "Content-Type: application/vnd.ogc.sld+xml" \
  -XPUT --data-binary "@$SLD" "$REST/workspaces/$WS/styles/$STYLE?raw=true" >/dev/null

# 6) make it the layer's default style
j -XPUT -d "{\"layer\":{\"defaultStyle\":{\"name\":\"$STYLE\",\"workspace\":\"$WS\"}}}" \
  "$REST/layers/$WS:$LAYER" >/dev/null

# 7) verify the featuretype resolves
code=$(curl -s -o /dev/null -w "%{http_code}" $AUTH "$REST/workspaces/$WS/datastores/$STORE/featuretypes/$LAYER.json")
if [ "$code" = "200" ]; then echo "  geoserver: $WS:$LAYER published with style $STYLE"; exit 0; fi
echo "  geoserver: featuretype not resolvable (http $code)"; exit 1
