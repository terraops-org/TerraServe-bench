#!/usr/bin/env python3
"""Translate the COS2023 SLD (fill-per-class on COS23_n4_C) into a MapServer 8.6 mapfile,
so MapServer renders the SAME classification from the SAME GeoPackage as TerraServe and
GeoServer, real styling parity for the cross-engine benchmark (cos2023_vector_bench.py).

Fill-only (the COS2023 SLD has no stroke), so each
SLD Rule -> one mapfile CLASS: EXPRESSION on the class value + STYLE COLOR (the fill hex).

Usage: python3 cos2023_sld_to_mapfile.py <sld> <out.map>
  (data path / layer / crs / extent are COS2023 constants below; --so verified via ogrinfo)
"""
import re
import sys

# COS2023v1-S2.gpkg facts (ogrinfo -so): layer cos2023v1, EPSG:3763, geom "geom", field COS23_n4_C.
DATA_CONN = "/data/COS2023v1-S2.gpkg"   # mounted read-only into the MapServer container
DATA_LAYER = "cos2023v1"
CLASS_FIELD = "COS23_n4_C"
EPSG = 3763
EXTENT = (-119191.408, -300404.804, 162129.081, 276083.767)


def parse_classes(sld_text):
    """Return [(value, '#rrggbb'), ...] for every Rule keyed on COS23_n4_C with a fill.
    Robust to namespace prefixes; pairs each Rule's Literal with its fill CssParameter."""
    out = []
    # Split into Rule blocks (tolerate se:/plain Rule tags).
    rules = re.split(r"</(?:se:)?Rule>", sld_text)
    for r in rules:
        # only polygon-fill rules classified on our field
        if CLASS_FIELD not in r:
            continue
        mval = re.search(r"<(?:ogc:)?Literal>\s*([^<]+?)\s*</(?:ogc:)?Literal>", r)
        mfill = re.search(r'<(?:Css|Svg)Parameter name="fill">\s*(#[0-9A-Fa-f]{6})', r)
        if mval and mfill:
            out.append((mval.group(1).strip(), mfill.group(1)))
    return out


def emit_mapfile(classes):
    lines = []
    a = lines.append
    a("MAP")
    a('  NAME "cos2023"')
    a("  SIZE 256 256")
    a("  MAXSIZE 4096")
    a("  EXTENT %s %s %s %s" % EXTENT)
    a("  UNITS METERS")
    a("  IMAGETYPE png")
    a('  PROJECTION\n    "init=epsg:%d"\n  END' % EPSG)
    a('  OUTPUTFORMAT\n    NAME "png"\n    DRIVER "AGG/PNG"\n    IMAGEMODE RGBA'
      '\n    TRANSPARENT ON\n    FORMATOPTION "GAMMA=0.75"\n  END')
    a('  WEB\n    METADATA')
    a('      "wms_title" "cos2023"')
    a('      "wms_srs" "EPSG:3763 EPSG:3857 EPSG:4326"')
    a('      "wms_enable_request" "*"')
    a('      "wms_onlineresource" "http://localhost/?map=/etc/mapserver/cos2023.map&"')
    a('    END\n  END')
    a('  LAYER')
    a('    NAME "cos2023"')
    a("    TYPE POLYGON")
    a("    STATUS ON")
    a("    CONNECTIONTYPE OGR")
    a('    CONNECTION "%s"' % DATA_CONN)
    a('    DATA "%s"' % DATA_LAYER)
    a('    PROJECTION\n      "init=epsg:%d"\n    END' % EPSG)
    a('    CLASSITEM "%s"' % CLASS_FIELD)
    a('    METADATA\n      "wms_title" "cos2023"'
      '\n      "wms_srs" "EPSG:3763 EPSG:3857 EPSG:4326"\n    END')
    for value, fill in classes:
        a('    CLASS')
        a('      EXPRESSION "%s"' % value)
        a('      STYLE\n        COLOR "%s"\n      END' % fill)
        a('    END')
    a('  END')  # LAYER
    a('END')    # MAP
    return "\n".join(lines) + "\n"


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sld = open(sys.argv[1], encoding="utf-8").read()
    classes = parse_classes(sld)
    if not classes:
        print("ERROR: no COS23_n4_C fill classes parsed from the SLD", file=sys.stderr)
        sys.exit(1)
    open(sys.argv[2], "w", encoding="utf-8").write(emit_mapfile(classes))
    print("wrote %s: %d classes on %s" % (sys.argv[2], len(classes), CLASS_FIELD))


if __name__ == "__main__":
    main()
