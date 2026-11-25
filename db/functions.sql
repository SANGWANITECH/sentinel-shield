-- =====================================================================
-- Sentinel Shield — live analysis for one flood level.
-- Apply after the data is loaded:
--   psql -U postgres -d sentinel_shield -f db/functions.sql
-- Re-runnable (CREATE OR REPLACE / IF NOT EXISTS).
-- =====================================================================

-- ---------------------------------------------------------------------
-- flood_extent_parts: each flood polygon cut into small pieces (max 255
-- vertices). One big, detailed polygon has one huge bounding box, so the
-- GIST index can't rule anything out. Small pieces have small boxes, so
-- ST_Intersects / ST_DWithin only test the few pieces that are nearby.
-- Refreshed by scripts/process_terrain.py after the flood levels are rebuilt.
-- ---------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS flood_extent_parts AS
SELECT level_m, ST_Subdivide(geom, 255) AS geom
FROM flood_extent;

CREATE INDEX IF NOT EXISTS flood_extent_parts_geom_gix  ON flood_extent_parts USING GIST (geom);
CREATE INDEX IF NOT EXISTS flood_extent_parts_level_idx ON flood_extent_parts (level_m);
COMMENT ON MATERIALIZED VIEW flood_extent_parts IS
  'flood_extent split with ST_Subdivide so spatial index lookups stay fast. Refresh after reloading flood_extent.';

-- ---------------------------------------------------------------------
-- sentinel_scenario(level): everything the map needs for one slider level,
-- as one JSON document. All geometry is returned in EPSG:4326.
--
--   flood       the flood polygon
--   safe_lz     candidate zones NOT within p_clearance_m of the flood
--   lost_lz     candidate zones that the flood (plus clearance) has taken
--   stranded    villages inside the flood, each with its nearest safe zone
--   links       lines village -> landing spot for reachable villages
--   roads_cut   road pieces under water
--   totals      counters for the side panel
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sentinel_scenario(
    p_level       numeric,
    p_clearance_m double precision DEFAULT 120,    -- LZ must be this far from water
    p_reach_m     double precision DEFAULT 1300,   -- max village -> LZ distance
    p_speed_kmh   double precision DEFAULT 180     -- helicopter cruise speed
) RETURNS jsonb
LANGUAGE sql STABLE PARALLEL SAFE AS
$$
WITH
flood AS (                       -- the pieces of this level's flood
    SELECT geom FROM flood_extent_parts WHERE level_m = p_level
),
base AS (                        -- helicopter staging base (KIA)
    SELECT geom FROM staging_base ORDER BY id LIMIT 1
),
lz AS (                          -- every candidate zone, flagged lost / safe
    SELECT c.id, c.code, c.area_m2, c.mean_slope, c.landcover, c.geom, c.pad,
           EXISTS (SELECT 1 FROM flood f WHERE ST_DWithin(c.geom, f.geom, p_clearance_m)) AS lost
    FROM candidate_lz c
),
channel AS (                     -- level 0 = the normal river channel (baseline)
    SELECT geom FROM flood_extent WHERE level_m = 0
),
stranded AS (                    -- villages whose built-up area touches the flood
    SELECT DISTINCT v.id, v.name, v.population, v.geom
    FROM villages v
    JOIN flood f ON ST_Intersects(COALESCE(v.footprint, v.geom), f.geom)
    WHERE p_level > 0            -- nobody is stranded by the river at its normal level
),
assign AS (                      -- nearest SAFE landing spot per stranded village (KNN)
    SELECT s.id, s.name, s.population, s.geom,
           n.code AS lz_code, n.pad AS lz_pad, n.dist_m,
           ST_Distance(b.geom, n.pad) AS base_dist_m
    FROM stranded s
    LEFT JOIN LATERAL (
        SELECT c.code, c.pad, ST_Distance(s.geom, c.pad) AS dist_m
        FROM candidate_lz c
        WHERE NOT EXISTS (SELECT 1 FROM flood f WHERE ST_DWithin(c.geom, f.geom, p_clearance_m))
        ORDER BY c.pad <-> s.geom          -- index-assisted nearest-neighbour ordering
        LIMIT 1
    ) n ON true
    LEFT JOIN base b ON true
),
assign2 AS (
    SELECT a.*,
           (a.dist_m IS NOT NULL AND a.dist_m <= p_reach_m) AS reachable,
           a.base_dist_m / (p_speed_kmh * 1000.0 / 60.0) AS eta_min
    FROM assign a
),
cut_raw AS (                     -- road pieces under water, gathered per road
    SELECT r.id, r.name, r.highway,
           ST_Collect(ST_Intersection(r.geom, f.geom)) AS geom
    FROM roads r
    JOIN flood f ON ST_Intersects(r.geom, f.geom)
    WHERE p_level > 0
    GROUP BY r.id, r.name, r.highway
),
cut AS (                         -- ignore the bridge span over the normal channel:
    SELECT c.id, c.name, c.highway,  -- a road is cut only if > 20 m is flooded OUTSIDE the channel
           COALESCE(ST_Difference(c.geom, ch.geom), c.geom) AS geom
    FROM cut_raw c LEFT JOIN channel ch ON true
),
cut2 AS (
    SELECT * FROM cut WHERE NOT ST_IsEmpty(geom) AND ST_Length(geom) > 20
)
SELECT jsonb_build_object(
  'level_m', p_level,
  'flood', (
    SELECT jsonb_build_object('type', 'FeatureCollection', 'features', COALESCE(jsonb_agg(
      jsonb_build_object('type', 'Feature',
        'geometry', ST_AsGeoJSON(ST_Transform(fe.geom, 4326), 6)::jsonb,
        'properties', jsonb_build_object('level_m', fe.level_m, 'area_km2', round((fe.area_m2 / 1e6)::numeric, 2)))
    ), '[]'::jsonb))
    FROM flood_extent fe WHERE fe.level_m = p_level),
  'safe_lz', (
    SELECT jsonb_build_object('type', 'FeatureCollection', 'features', COALESCE(jsonb_agg(
      jsonb_build_object('type', 'Feature',
        'geometry', ST_AsGeoJSON(ST_Transform(l.pad, 4326), 6)::jsonb,
        'properties', jsonb_build_object('code', l.code, 'area_m2', round(l.area_m2::numeric),
                                         'mean_slope', round(l.mean_slope::numeric, 1), 'landcover', l.landcover))
      ORDER BY l.code), '[]'::jsonb))
    FROM lz l WHERE NOT l.lost),
  'lost_lz', (
    SELECT jsonb_build_object('type', 'FeatureCollection', 'features', COALESCE(jsonb_agg(
      jsonb_build_object('type', 'Feature',
        'geometry', ST_AsGeoJSON(ST_Transform(l.pad, 4326), 6)::jsonb,
        'properties', jsonb_build_object('code', l.code))
      ORDER BY l.code), '[]'::jsonb))
    FROM lz l WHERE l.lost),
  'stranded', (
    SELECT jsonb_build_object('type', 'FeatureCollection', 'features', COALESCE(jsonb_agg(
      jsonb_build_object('type', 'Feature',
        'geometry', ST_AsGeoJSON(ST_Transform(a.geom, 4326), 6)::jsonb,
        'properties', jsonb_build_object(
            'id', a.id, 'code', 'V' || lpad(a.id::text, 2, '0'), 'name', a.name,
            'population', a.population, 'status', CASE WHEN a.reachable THEN 'reachable' ELSE 'no_safe_zone' END,
            'lz_code', a.lz_code, 'dist_m', round(a.dist_m::numeric),
            'eta_min', round(a.eta_min::numeric, 1)))
      ORDER BY a.population DESC NULLS LAST), '[]'::jsonb))
    FROM assign2 a),
  'links', (
    SELECT jsonb_build_object('type', 'FeatureCollection', 'features', COALESCE(jsonb_agg(
      jsonb_build_object('type', 'Feature',
        'geometry', ST_AsGeoJSON(ST_Transform(ST_MakeLine(a.geom, a.lz_pad), 4326), 6)::jsonb,
        'properties', jsonb_build_object('village_id', a.id, 'lz_code', a.lz_code,
                                         'dist_m', round(a.dist_m::numeric)))
    ), '[]'::jsonb))
    FROM assign2 a WHERE a.reachable),
  'roads_cut', (
    SELECT jsonb_build_object('type', 'FeatureCollection', 'features', COALESCE(jsonb_agg(
      jsonb_build_object('type', 'Feature',
        'geometry', ST_AsGeoJSON(ST_Transform(c.geom, 4326), 6)::jsonb,
        'properties', jsonb_build_object(
            'road_id', c.id, 'name', c.name, 'highway', c.highway,
            'length_m', round(ST_Length(c.geom)::numeric),
            'marker', ST_AsGeoJSON(ST_Transform(ST_PointOnSurface(c.geom), 4326), 6)::jsonb))
    ), '[]'::jsonb))
    FROM cut2 c),
  'totals', (
    SELECT jsonb_build_object(
      'people_exposed',     COALESCE((SELECT sum(population) FROM assign2), 0),
      'villages_stranded',  (SELECT count(*) FROM assign2),
      'reachable',          (SELECT count(*) FROM assign2 WHERE reachable),
      'no_safe_zone',       (SELECT count(*) FROM assign2 WHERE NOT reachable),
      'safe_lz',            (SELECT count(*) FROM lz WHERE NOT lost),
      'lost_lz',            (SELECT count(*) FROM lz WHERE lost),
      'roads_cut',          (SELECT count(*) FROM cut2),
      'flood_area_km2',     COALESCE((SELECT round((area_m2 / 1e6)::numeric, 2) FROM flood_extent WHERE level_m = p_level), 0)
    ))
);
$$;

-- ---------------------------------------------------------------------
-- sentinel_base_layers(): static context layers for the map, simplified
-- (ST_SimplifyPreserveTopology, tolerance in metres because data is UTM)
-- to keep the JSON small. The API caches the result.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sentinel_base_layers() RETURNS jsonb
LANGUAGE sql STABLE AS
$$
SELECT jsonb_build_object(
  'district', (SELECT jsonb_build_object('type','FeatureCollection','features', COALESCE(jsonb_agg(
      jsonb_build_object('type','Feature',
        'geometry', ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(geom, 50), 4326), 5)::jsonb,
        'properties', jsonb_build_object('name', name))), '[]'::jsonb)) FROM district_boundary),
  'study_area', (SELECT jsonb_build_object('type','FeatureCollection','features', COALESCE(jsonb_agg(
      jsonb_build_object('type','Feature',
        'geometry', ST_AsGeoJSON(ST_Transform(geom, 4326), 6)::jsonb,
        'properties', jsonb_build_object('name', name))), '[]'::jsonb)) FROM study_area),
  'rivers', (SELECT jsonb_build_object('type','FeatureCollection','features', COALESCE(jsonb_agg(
      jsonb_build_object('type','Feature',
        'geometry', ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(geom, 5), 4326), 6)::jsonb,
        'properties', jsonb_build_object('name', name, 'waterway', waterway))), '[]'::jsonb)) FROM rivers),
  'roads', (SELECT jsonb_build_object('type','FeatureCollection','features', COALESCE(jsonb_agg(
      jsonb_build_object('type','Feature',
        'geometry', ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(geom, 5), 4326), 6)::jsonb,
        'properties', jsonb_build_object('name', name, 'highway', highway))), '[]'::jsonb)) FROM roads),
  'power_lines', (SELECT jsonb_build_object('type','FeatureCollection','features', COALESCE(jsonb_agg(
      jsonb_build_object('type','Feature',
        'geometry', ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(geom, 5), 4326), 6)::jsonb,
        'properties', jsonb_build_object('voltage', voltage))), '[]'::jsonb)) FROM power_lines),
  'villages', (SELECT jsonb_build_object('type','FeatureCollection','features', COALESCE(jsonb_agg(
      jsonb_build_object('type','Feature',
        'geometry', ST_AsGeoJSON(ST_Transform(geom, 4326), 6)::jsonb,
        'properties', jsonb_build_object('id', id, 'code', 'V' || lpad(id::text, 2, '0'),
                                         'name', name, 'population', population))
      ORDER BY id), '[]'::jsonb)) FROM villages),
  'staging_base', (SELECT jsonb_build_object('type','FeatureCollection','features', COALESCE(jsonb_agg(
      jsonb_build_object('type','Feature',
        'geometry', ST_AsGeoJSON(ST_Transform(geom, 4326), 6)::jsonb,
        'properties', jsonb_build_object('name', name))), '[]'::jsonb)) FROM staging_base)
);
$$;

COMMENT ON FUNCTION sentinel_base_layers() IS
  'Static map context layers (simplified) as EPSG:4326 GeoJSON FeatureCollections.';

COMMENT ON FUNCTION sentinel_scenario(numeric, double precision, double precision, double precision) IS
  'Live rescue scenario for one flood level: flood, safe/lost landing zones, stranded villages with nearest safe LZ (LATERAL + KNN), road cuts and totals, as EPSG:4326 GeoJSON.';
