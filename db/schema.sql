-- =====================================================================
-- Sentinel Shield — PostGIS schema
-- All geometry is stored in EPSG:32736 (WGS 84 / UTM zone 36S) so that
-- ST_Distance, ST_Area and ST_Buffer work directly in metres.
-- The API transforms to EPSG:4326 when returning GeoJSON.
--
-- Safe to re-run: uses IF NOT EXISTS and never drops data.
-- Apply:  psql -U postgres -d sentinel_shield -f db/schema.sql
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS postgis;

BEGIN;

-- ---------------------------------------------------------------------
-- Context layers (boundaries)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS district_boundary (
    id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name  text NOT NULL,
    geom  geometry(MultiPolygon, 32736) NOT NULL
);
COMMENT ON TABLE district_boundary IS
  'Administrative boundary of Dowa District. Map context only; used to frame the map and clip source data.';

CREATE TABLE IF NOT EXISTS study_area (
    id           integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         text NOT NULL,
    description  text,
    geom         geometry(MultiPolygon, 32736) NOT NULL
);
COMMENT ON TABLE study_area IS
  'Analysis window around the Kasangadzi River near Mponela. All other layers are clipped to it, which keeps the data small and the queries fast.';

-- ---------------------------------------------------------------------
-- Infrastructure and hazards (vector inputs)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rivers (
    id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name      text,
    waterway  text,                       -- OSM class: river, stream, ...
    geom      geometry(MultiLineString, 32736) NOT NULL
);
COMMENT ON TABLE rivers IS
  'River and stream centrelines. Used for the 120 m water-clearance buffer that a landing zone must stay outside of.';

CREATE TABLE IF NOT EXISTS roads (
    id       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name     text,
    highway  text,                        -- OSM class: primary, track, ...
    geom     geometry(MultiLineString, 32736) NOT NULL
);
COMMENT ON TABLE roads IS
  'Road network. Shown for context, so you can see which roads go under water as the flood rises.';

CREATE TABLE IF NOT EXISTS villages (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text NOT NULL,
    population  integer CHECK (population IS NULL OR population >= 0),
    geom        geometry(Point, 32736) NOT NULL
);
COMMENT ON TABLE villages IS
  'Settlement points with population. A village whose point falls inside the flood extent for the current level is "stranded" and is linked to its nearest safe landing zone.';

CREATE TABLE IF NOT EXISTS buildings (
    id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    building  text,                       -- OSM building tag
    geom      geometry(MultiPolygon, 32736) NOT NULL
);
COMMENT ON TABLE buildings IS
  'Building footprints. A landing zone must be clear of these, so they are buffered and subtracted from candidate zones.';

CREATE TABLE IF NOT EXISTS power_lines (
    id       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    voltage  text,
    geom     geometry(MultiLineString, 32736) NOT NULL
);
COMMENT ON TABLE power_lines IS
  'Overhead power lines, a major helicopter hazard. They are buffered and subtracted from candidate zones.';

-- ---------------------------------------------------------------------
-- Precomputed analysis outputs (made offline in QGIS/Python)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flood_extent (
    id       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    level_m  numeric(3,1) NOT NULL UNIQUE CHECK (level_m BETWEEN 0 AND 6),
    area_m2  double precision,
    geom     geometry(MultiPolygon, 32736) NOT NULL
);
COMMENT ON TABLE flood_extent IS
  'One flooded-area polygon per slider level (0 to 6 m), made from the DEM. level_m is UNIQUE so the API gets exactly one row per level. This is the layer that drives the live queries.';

CREATE TABLE IF NOT EXISTS candidate_lz (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code        text NOT NULL UNIQUE,     -- e.g. LZ-001, used as the radio call-sign
    area_m2     double precision NOT NULL CHECK (area_m2 > 0),
    mean_slope  double precision,         -- degrees, from DEM slope analysis
    landcover   text,                     -- reclassified class, e.g. grassland, cropland, bare
    geom        geometry(MultiPolygon, 32736) NOT NULL
);
COMMENT ON TABLE candidate_lz IS
  'Polygons that pass the static criteria (flat, open land cover, at least 50x50 m), made by multi-criteria suitability analysis. At each flood level the API removes the flooded, water-buffer and hazard areas (ST_Difference) to find which zones are still safe.';

CREATE TABLE IF NOT EXISTS staging_base (
    id           integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         text NOT NULL,
    description  text,
    geom         geometry(Point, 32736) NOT NULL
);
COMMENT ON TABLE staging_base IS
  'Where rescue helicopters take off (e.g. an airfield or a forward base near Mponela). Used as the starting point for helicopter flight distance and ETA.';

-- ---------------------------------------------------------------------
-- GIST spatial indexes: let ST_Intersects, ST_DWithin and KNN (<->)
-- skip non-candidate rows using bounding boxes instead of scanning every row.
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS district_boundary_geom_gix ON district_boundary USING GIST (geom);
CREATE INDEX IF NOT EXISTS study_area_geom_gix        ON study_area        USING GIST (geom);
CREATE INDEX IF NOT EXISTS rivers_geom_gix            ON rivers            USING GIST (geom);
CREATE INDEX IF NOT EXISTS roads_geom_gix             ON roads             USING GIST (geom);
CREATE INDEX IF NOT EXISTS villages_geom_gix          ON villages          USING GIST (geom);
CREATE INDEX IF NOT EXISTS buildings_geom_gix         ON buildings         USING GIST (geom);
CREATE INDEX IF NOT EXISTS power_lines_geom_gix       ON power_lines       USING GIST (geom);
CREATE INDEX IF NOT EXISTS flood_extent_geom_gix      ON flood_extent      USING GIST (geom);
CREATE INDEX IF NOT EXISTS candidate_lz_geom_gix      ON candidate_lz      USING GIST (geom);
CREATE INDEX IF NOT EXISTS staging_base_geom_gix      ON staging_base      USING GIST (geom);

COMMIT;
