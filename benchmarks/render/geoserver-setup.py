#!/usr/bin/env python3
"""
GeoServer REST API setup for benchmark layers.

Creates workspace, coverage stores, and layers for:

- Cascais RGB COG (raster)
- Cascais NDVI (raster with band math)
- COS2023 vector (via PostGIS)

Note: This could have been da data_dir mount on GS image
"""
import requests
import json
import sys
import time
from pathlib import Path

class GeoServerSetup:
    def __init__(self, base_url="http://localhost:8080/geoserver", user="admin", passwd="geoserver"):
        self.base_url = base_url
        self.auth = (user, passwd)
        self.headers = {"Content-Type": "application/json"}
        self.workspace = "benchmarks"
        self.timeout = 10

    def request(self, method, endpoint, data=None, expect_status=None):
        """Make REST API request."""
        url = f"{self.base_url}/rest{endpoint}"
        try:
            if method == "GET":
                r = requests.get(url, auth=self.auth, timeout=self.timeout)
            elif method == "POST":
                r = requests.post(url, auth=self.auth, headers=self.headers, json=data, timeout=self.timeout)
            elif method == "PUT":
                r = requests.put(url, auth=self.auth, headers=self.headers, json=data, timeout=self.timeout)
            elif method == "DELETE":
                r = requests.delete(url, auth=self.auth, timeout=self.timeout)
            else:
                raise ValueError(f"Unknown method: {method}")

            if expect_status and r.status_code not in (expect_status if isinstance(expect_status, list) else [expect_status]):
                print(f"[ERROR] {method} {endpoint} returned {r.status_code}: {r.text[:200]}", file=sys.stderr)
                return None

            return r
        except requests.ConnectionError:
            print(f"[ERROR] Cannot connect to GeoServer at {self.base_url}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"[ERROR] {method} {endpoint}: {e}", file=sys.stderr)
            return None

    def create_workspace(self):
        """Create 'benchmarks' workspace."""
        print(f"[*] Creating workspace '{self.workspace}'...")

        # Check if exists
        r = self.request("GET", f"/workspaces/{self.workspace}")
        if r and r.status_code == 200:
            print(f"    [SKIP] Workspace already exists")
            return True

        # Create
        data = {
            "workspace": {
                "name": self.workspace
            }
        }
        r = self.request("POST", "/workspaces", data, expect_status=[201, 400])
        if r and r.status_code in [201, 400]:  # 400 = already exists
            print(f"    [OK] Workspace ready")
            return True
        return False

    def create_coverage_store(self, name, file_path, description=""):
        """Create coverage store for COG.

        file_path must be the path as GeoServer sees it, inside its own container,
        not the host path. docker compose mounts ./data at /data/cogs, so a host
        ./data/x.tif is /data/cogs/x.tif for GeoServer.
        """
        print(f"[*] Creating coverage store '{name}'...")

        # Check if exists
        r = self.request("GET", f"/workspaces/{self.workspace}/coveragestores/{name}")
        if r and r.status_code == 200:
            print(f"    [SKIP] Coverage store already exists")
            return True

        # The workspace key is required. Without it GeoServer answers
        # 500 "Store must be part of a workspace", which reads like a server fault
        # but is really a malformed payload.
        data = {
            "coverageStore": {
                "name": name,
                "type": "GeoTIFF",
                "enabled": True,
                "description": description,
                "workspace": {"name": self.workspace},
                "url": f"file:{file_path}"
            }
        }
        r = self.request("POST", f"/workspaces/{self.workspace}/coveragestores", data, expect_status=[201, 400])
        if r and r.status_code in [201, 400]:
            print(f"    [OK] Coverage store ready")
            return True
        return False

    def create_coverage(self, store_name, coverage_name, crs="EPSG:3763"):
        """Create coverage layer from store."""
        print(f"[*] Creating coverage layer '{coverage_name}'...")

        # Check if exists
        r = self.request("GET", f"/workspaces/{self.workspace}/coveragestores/{store_name}/coverages/{coverage_name}")
        if r and r.status_code == 200:
            print(f"    [SKIP] Coverage already exists")
            return True

        data = {
            "coverage": {
                "name": coverage_name,
                "title": coverage_name,
                "description": f"Coverage from {store_name}",
                "enabled": True,
                "projectionPolicy": "REPROJECT_TO_DECLARED",
                "srs": crs,
                "metadata": {}
            }
        }
        r = self.request("POST", f"/workspaces/{self.workspace}/coveragestores/{store_name}/coverages",
                        data, expect_status=[201, 400])
        if r and r.status_code in [201, 400]:
            print(f"    [OK] Coverage layer ready")
            return True
        return False

    def set_default_style(self, layer_name, style_name=None):
        """Assign default style to layer."""
        if not style_name:
            return True

        print(f"[*] Setting default style for '{layer_name}'...")

        # Get layer to update
        r = self.request("GET", f"/layers/{self.workspace}:{layer_name}")
        if not r or r.status_code != 200:
            print(f"    [WARN] Layer not found, skipping style assignment")
            return False

        data = {
            "layer": {
                "defaultStyle": {
                    "name": style_name
                }
            }
        }
        r = self.request("PUT", f"/layers/{self.workspace}:{layer_name}", data, expect_status=[200, 201])
        if r:
            print(f"    [OK] Style applied")
            return True
        return False

    def setup_layers(self, cog_cascais, cog_ndvi=None, container_dir="/data/cogs"):
        """Set up all layers for benchmarking.

        cog_cascais / cog_ndvi are HOST paths (so we can check they exist). What we
        hand GeoServer is container_dir + the file name, because GeoServer opens the
        file from inside its own container.
        """
        print("\n=== GeoServer Benchmark Layer Setup ===\n")

        if not self.create_workspace():
            return False

        def in_container(host_path):
            return f"{container_dir.rstrip('/')}/{Path(host_path).name}"

        # Cascais RGB COG
        if not self.create_coverage_store("cascais-rgb", in_container(cog_cascais),
                                          "Cascais RGB COG (DEFLATE)"):
            return False
        if not self.create_coverage("cascais-rgb", "cascais_rgb_cog", crs="EPSG:3763"):
            return False

        # Cascais NDVI (if available)
        if cog_ndvi and Path(cog_ndvi).exists():
            if not self.create_coverage_store("cascais-ndvi", in_container(cog_ndvi),
                                              "Cascais NDVI (band math)"):
                print(f"    [WARN] NDVI COG not available, skipping")
            else:
                if not self.create_coverage("cascais-ndvi", "cascais_ndvi_cog", crs="EPSG:3763"):
                    print(f"    [WARN] Failed to create NDVI coverage")

        print("\n[OK] All layers initialized. Ready for benchmarking.\n")
        return True

    def health_check(self):
        """Verify GeoServer is accessible."""
        r = self.request("GET", "/about/version.json")
        if r and r.status_code == 200:
            # Shape is {"about": {"resource": [{"@name": "GeoServer", "Version": "2.26.1"}, ...]}}
            version = "unknown"
            for res in r.json().get("about", {}).get("resource", []):
                if res.get("@name") == "GeoServer":
                    version = res.get("Version", "unknown")
                    break
            print(f"[OK] GeoServer {version} is running")
            return True
        else:
            print(f"[ERROR] GeoServer is not responding")
            return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Set up GeoServer layers for benchmarking")
    parser.add_argument("--gs-url", default="http://localhost:8080/geoserver", help="GeoServer base URL")
    parser.add_argument("--user", default="admin", help="GeoServer admin user")
    parser.add_argument("--passwd", default="geoserver", help="GeoServer admin password")
    parser.add_argument("--cog-cascais", required=True, help="HOST path to Cascais RGB COG")
    parser.add_argument("--cog-ndvi", help="HOST path to Cascais NDVI COG (optional)")
    parser.add_argument("--container-dir", default="/data/cogs",
                        help="Where ./data is mounted inside the GeoServer container "
                             "(see docker-compose.yml). Default: /data/cogs")
    parser.add_argument("--health-check-only", action="store_true", help="Only verify GeoServer is running")

    args = parser.parse_args()

    gs = GeoServerSetup(base_url=args.gs_url, user=args.user, passwd=args.passwd)

    # Health check
    if not gs.health_check():
        sys.exit(1)

    if args.health_check_only:
        sys.exit(0)

    # Setup layers
    if gs.setup_layers(args.cog_cascais, args.cog_ndvi, args.container_dir):
        sys.exit(0)
    else:
        sys.exit(1)
