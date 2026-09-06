# GeoServer Configuration

Manual walkthrough of the GeoServer in the compose stack, for poking around in the UI.
**The benchmarks do not need any of this:** `benchmarks/render/run.sh` creates the
workspace `benchmarks`, the store `cascais-rgb` and the layer `cascais_rgb_cog` over REST
on every run (`geoserver-setup.py`), and the vector benchmark starts its own GeoServer
container with the GeoPackage as a GeoPackage store, no PostGIS
(`benchmarks/vector/geoserver_cos2023_setup.sh`).

## Quick Start

```bash
cd ../..
docker compose up -d geoserver
# Wait ~30s for startup
curl http://localhost:8080/geoserver/web/
```

Access at: `http://localhost:8080/geoserver`
- User: `admin`
- Password: `geoserver`

## Adding Raster Layer (COG)

### 1. Create a Coverage Store (basic import)

1. Go to **Data** -> **Stores** -> **Add New Store**
2. Select **GeoTIFF** (or appropriate format)
3. Fill in:
   - Workspace: `benchmarks`
   - Data Source Name: `cascais-rgb`
   - URL: `/data/cogs/cascais.cog.deflate.tif`
4. Click **Create New Resource** -> and then  **Publish**

### 2. Configure Layer

1. Set:
   - Name: `cascais_rgb_cog` (the name the REST script uses; keep it so both agree)
   - Title: "Cascais RGB COG"
   - CRS: `EPSG:3763`
2. **Save it**

### 3. Preview Layer

Go to **Layer Preview** and click **OpenLayers** to verify.

## Adding Vector Layer (GeoPackage/PostGIS)

### 1. Import Data to PostGIS

```bash
# (Optional) Load COS2023 into PostGIS
docker compose exec -T postgis psql -U geoserver geoserverdb <<END
CREATE SCHEMA IF NOT EXISTS cos2023;
-- Load GeoPackage or GeoJSON here
-- ogr2ogr -f PostgreSQL -lco OVERWRITE=YES \
--   PG:"dbname=geoserverdb user=geoserver password=geoserver" \
--   /data/cogs/COS2023v1-S2.gpkg   # ./data is mounted at /data/cogs in the compose stack
END
```

### 2. Create Data Store

1. Go to **Data** then  **Stores** then **Add New Store**
2. Select **PostGIS** (connection database)
3. Fill in:
   - Workspace: `benchmarks`
   - Data Source Name: `postgis-cos2023`
   - Host: `postgis`
   - Port: `5432`
   - Database: `geoserverdb`
   - User: `geoserver`
   - Password: `geoserver`
4. Click **Save**

### 3. Publish Layer

1. Click **New Resource** -> select table
2. Publish with:
   - Layer name: `cos2023_landcover`
   - CRS: `EPSG:3857` or `EPSG:3763`
3. **Save**

## Styling Layers

### Apply SLD Style

1. Upload style file:
   - Go to **Data** -> **Styles** -> **Add New Style**
   - Upload SLD from `../styles/`
   - **Save**

2. Apply to layer:
   - Select layer
   - Go to **Publishing** tab
   - Assign default style
   - **Save**

### Create Style via GeoServer UI

1. Go to **Data** -> **Styles** ->**Add New Style**
2. Choose rendering transformation (SLD)
3. Edit via form or XML editor
4. **Save**

## WMS Requests

Once configured, test via WMS:

```bash
# GetCapabilities
curl 'http://localhost:8080/geoserver/ows?service=WMS&version=1.3.0&request=GetCapabilities'

# GetMap (RGB)
curl 'http://localhost:8080/geoserver/ows?service=WMS&version=1.3.0&request=GetMap&layers=benchmarks:cascais_rgb_cog&bbox=minx,miny,maxx,maxy&crs=EPSG:3763&width=800&height=536&format=image/png' -o cascais.png

# GetMap (Vector)
curl 'http://localhost:8080/geoserver/ows?service=WMS&version=1.3.0&request=GetMap&layers=benchmarks:cos2023_landcover&bbox=...&crs=EPSG:3857&width=256&height=256&format=image/png' -o vector.png
```

## Performance Tuning

Edit `docker-compose.yml` to adjust JVM heap:

```yaml
geoserver:
  environment:
    GEOSERVER_JAVA_OPTS: "-Xms2g -Xmx8g"
```

Then restart:
```bash
docker compose restart geoserver
```

## Debugging

### View logs

```bash
docker compose logs -f geoserver
```

### Shell into container

```bash
docker compose exec geoserver bash
```

### Check connectivity to PostGIS

```bash
docker compose exec postgis psql -U geoserver geoserverdb -c "SELECT version();"
```

## Reset GeoServer Data

```bash
# Stop the stack and delete ITS volumes only (WARNING: deletes this GeoServer's config)
docker compose down -v

# Restart; the render run.sh re-creates the benchmark layers over REST
docker compose up -d geoserver postgis
```

---

**GeoServer Docs:** https://geoserver.org/  
**Workspace:** `benchmarks`  
**Default Admin:** admin / geoserver
