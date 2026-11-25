import { useEffect, useRef } from "react";
import L from "leaflet";

// Roads that get a "ROAD CUT" label when flooded (smaller tracks are only drawn red).
const MAJOR_ROADS = new Set([
  "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
  "trunk_link", "primary_link", "secondary_link", "tertiary_link",
]);

const ROAD_STYLE = (hw) => {
  if (["motorway", "trunk", "primary", "trunk_link", "primary_link"].includes(hw))
    return { color: "#c9d3da", weight: 2.6, opacity: 0.85 };
  if (["secondary", "tertiary", "secondary_link", "tertiary_link"].includes(hw))
    return { color: "#9fb0bc", weight: 1.8, opacity: 0.75 };
  if (["unclassified", "residential"].includes(hw)) return { color: "#7d8f9b", weight: 1.1, opacity: 0.6 };
  return { color: "#5f707c", weight: 0.8, opacity: 0.5 };
};

const PANES = [
  ["basePane", 150],            // hillshade sits below everything (Leaflet's overlayPane is 400)
  ["osmPane", 160],             // optional online tiles, drawn translucent over the hillshade
  ["riverPane", 380], ["floodPane", 390], ["roadPane", 400], ["powerPane", 405],
  ["cutPane", 420], ["linkPane", 430], ["lzPane", 620], ["vilPane", 640], ["cutLabelPane", 650],
];

const NORTH_SVG = `<svg viewBox="0 0 40 52" width="34" height="44" aria-label="North">
  <text x="20" y="11" text-anchor="middle" font-size="11" font-family="Barlow Condensed" font-weight="700" fill="#d6e2ea">N</text>
  <path d="M20 15 L29 46 L20 39 L11 46 Z" fill="#0d151c" stroke="#7d8b96" stroke-width="1.2"/>
  <path d="M20 15 L20 39 L11 46 Z" fill="#d6e2ea"/></svg>`;

function lzIcon() {
  return L.divIcon({ className: "lz-icon", html: '<div class="lz safe"><span>H</span></div>', iconSize: [26, 26] });
}
function villageIcon() {
  return L.divIcon({
    className: "vil-icon",
    html: '<div class="vil"><span class="ring"></span><span class="dot"></span></div>',
    iconSize: [26, 26],
  });
}

export default function MapView({ meta, base, hillshade, scenario, layers, level }) {
  const elRef = useRef(null);
  const st = useRef(null);

  // ---- create the map once
  useEffect(() => {
    const map = L.map(elRef.current, { zoomControl: false, minZoom: 9, maxZoom: 17, zoomSnap: 0.25 });
    map.attributionControl.setPrefix(false);
    PANES.forEach(([name, z]) => (map.createPane(name).style.zIndex = z));
    L.control.zoom({ position: "bottomright" }).addTo(map);
    L.control.scale({ position: "bottomleft", imperial: false, maxWidth: 180 }).addTo(map);
    const North = L.Control.extend({
      onAdd() { const d = L.DomUtil.create("div", "north-arrow"); d.innerHTML = NORTH_SVG; return d; },
    });
    new North({ position: "topright" }).addTo(map);
    const Readout = L.Control.extend({
      onAdd() { return L.DomUtil.create("div", "level-readout"); },
    });
    const readout = new Readout({ position: "topleft" }).addTo(map);
    const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors", maxZoom: 19, className: "osm-tiles", pane: "osmPane",
    });
    st.current = {
      map, readout, osm, flood: null,
      lz: new Map(), vil: new Map(),
      links: L.layerGroup().addTo(map), cuts: L.layerGroup().addTo(map),
      cutLabels: L.layerGroup().addTo(map),
    };
    return () => map.remove();
  }, []);

  // ---- static layers
  useEffect(() => {
    const s = st.current;
    if (!s || !meta || !base) return;
    const { map } = s;
    const [w, so, e, n] = meta.study_area.bbox;
    map.fitBounds([[so, w], [n, e]], { padding: [20, 20] });
    map.setMaxBounds([[so - 0.15, w - 0.15], [n + 0.15, e + 0.15]]);
    if (hillshade) {
      L.imageOverlay("/basemap/hillshade.png", [[hillshade.south, hillshade.west], [hillshade.north, hillshade.east]], {
        className: "hillshade", interactive: false, pane: "basePane",
      }).addTo(map);
    }
    L.geoJSON(base.district, {
      interactive: false, style: { color: "#56687a", weight: 1.2, fill: false, dashArray: "2 6" },
    }).addTo(map);
    L.geoJSON(base.study_area, {
      interactive: false, style: { color: "#3ddc84", weight: 1, opacity: 0.45, fill: false },
    }).addTo(map);
    L.geoJSON(base.rivers, {
      pane: "riverPane", interactive: false,
      style: (f) => ({ color: "#3d8fd6", opacity: 0.9, weight: f.properties.waterway === "river" ? 2.4 : 1.1 }),
    }).addTo(map);
    L.geoJSON(base.roads, { pane: "roadPane", interactive: false, style: (f) => ROAD_STYLE(f.properties.highway) }).addTo(map);
    L.geoJSON(base.power_lines, {
      pane: "powerPane", interactive: false, style: { color: "#c18cff", weight: 1.2, opacity: 0.8, dashArray: "1 4" },
    }).addTo(map);
    base.villages.features.forEach((f) => {
      const [lon, lat] = f.geometry.coordinates;
      const p = f.properties;
      const m = L.marker([lat, lon], { icon: villageIcon(), pane: "vilPane", keyboard: false })
        .bindTooltip(`<b>${p.code}</b> ${p.name}<br/>pop. ~${(p.population ?? 0).toLocaleString()}`, {
          direction: "top", offset: [0, -8], className: "tip",
        })
        .addTo(map);
      s.vil.set(p.id, m);
    });
  }, [meta, base, hillshade]);

  // ---- scenario (changes with the slider)
  useEffect(() => {
    const s = st.current;
    if (!s || !scenario) return;
    const { map } = s;

    // Flood: fade the new polygon in and the old one out.
    const old = s.flood;
    const flood = L.geoJSON(scenario.flood, {
      pane: "floodPane", interactive: false,
      style: { className: "flood-shape flood-enter", color: "#5fb2ff", weight: 1, fillColor: "#2f7fd8", fillOpacity: 0.4 },
    }).addTo(map);
    // Force the browser to compute the starting (transparent) style, then remove the class so
    // the CSS transition runs. No requestAnimationFrame: it is paused in background tabs.
    flood.eachLayer((l) => {
      const el = l.getElement();
      if (!el) return;
      el.getBoundingClientRect();
      el.classList.remove("flood-enter");
    });
    if (old) {
      old.eachLayer((l) => l.getElement()?.classList.add("flood-exit"));
      setTimeout(() => map.removeLayer(old), 600);
    }
    s.flood = flood;

    // Landing zones: one marker per zone; only its class changes, so CSS can animate it.
    const setLz = (f, safe) => {
      const p = f.properties;
      let m = s.lz.get(p.code);
      if (!m) {
        const [lon, lat] = f.geometry.coordinates;
        m = L.marker([lat, lon], { icon: lzIcon(), pane: "lzPane", keyboard: false }).addTo(map);
        s.lz.set(p.code, m);
      }
      const tip = safe
        ? `<b>${p.code}</b> safe<br/>${p.landcover}, slope ${p.mean_slope}&deg;, ${(p.area_m2 / 10000).toFixed(1)} ha`
        : `<b>${p.code}</b> lost to flood (within clearance)`;
      m.unbindTooltip().bindTooltip(tip, { direction: "top", offset: [0, -10], className: "tip" });
      const el = m.getElement()?.querySelector(".lz");
      if (el) el.className = `lz ${safe ? "safe" : "lost"}`;
    };
    scenario.safe_lz.features.forEach((f) => setLz(f, true));
    scenario.lost_lz.features.forEach((f) => setLz(f, false));

    // Villages: reset, then colour the stranded ones.
    s.vil.forEach((m) => {
      const el = m.getElement()?.querySelector(".vil");
      if (el) el.className = "vil";
      m.setZIndexOffset(0);
    });
    scenario.stranded.features.forEach((f) => {
      const p = f.properties;
      const m = s.vil.get(p.id);
      if (!m) return;
      const el = m.getElement()?.querySelector(".vil");
      if (el) el.className = `vil ${p.status === "reachable" ? "stranded" : "nozone"}`;
      m.setZIndexOffset(p.status === "reachable" ? 500 : 1000);
    });

    // Rescue links (village -> landing spot).
    s.links.clearLayers();
    L.geoJSON(scenario.links, {
      pane: "linkPane", interactive: false,
      style: { color: "#3ddc84", weight: 2, opacity: 0.95, dashArray: "6 6", className: "link" },
    }).addTo(s.links);

    // Road cuts: red segments, plus labels for the main roads (spaced out to avoid clutter).
    s.cuts.clearLayers();
    s.cutLabels.clearLayers();
    L.geoJSON(scenario.roads_cut, {
      pane: "cutPane", interactive: false, style: { color: "#ff4d4f", weight: 3, opacity: 0.9, className: "cut" },
    }).addTo(s.cuts);
    let feats = scenario.roads_cut.features.filter((f) => MAJOR_ROADS.has(f.properties.highway));
    if (!feats.length) feats = [...scenario.roads_cut.features].sort((a, b) => b.properties.length_m - a.properties.length_m).slice(0, 5);
    const placed = [];
    feats.forEach((f) => {
      const [lon, lat] = f.properties.marker.coordinates;
      const ll = L.latLng(lat, lon);
      if (placed.some((q) => q.distanceTo(ll) < 450)) return;
      placed.push(ll);
      L.marker(ll, {
        pane: "cutLabelPane", interactive: true, keyboard: false,
        icon: L.divIcon({ className: "cut-icon", html: '<div class="roadcut">ROAD CUT</div>', iconSize: [70, 18] }),
      }).bindTooltip(`${f.properties.name || "Unnamed road"} (${f.properties.highway})<br/>${f.properties.length_m} m under water`, {
        direction: "top", className: "tip",
      }).addTo(s.cutLabels);
    });
  }, [scenario]);

  // ---- water level readout
  useEffect(() => {
    const s = st.current;
    if (!s) return;
    const t = scenario?.totals;
    s.readout.getContainer().innerHTML =
      `<div class="lbl">WATER LEVEL</div><div class="val">+${level.toFixed(1)}<small> m</small></div>` +
      `<div class="sub">${t ? `${t.flood_area_km2} km&sup2; flooded` : "&nbsp;"}</div>`;
  }, [level, scenario]);

  // ---- layer toggles (each layer type lives in its own pane)
  useEffect(() => {
    const s = st.current;
    if (!s) return;
    const show = (pane, on) => (s.map.getPane(pane).style.display = on ? "" : "none");
    show("floodPane", layers.flood);
    show("lzPane", layers.lz);
    show("linkPane", layers.links);
    show("vilPane", layers.villages);
    show("roadPane", layers.roads);
    show("riverPane", layers.rivers);
    show("powerPane", layers.power);
    show("cutPane", layers.cuts);
    show("cutLabelPane", layers.cuts);
    if (layers.osm && !s.map.hasLayer(s.osm)) s.osm.addTo(s.map);
    if (!layers.osm && s.map.hasLayer(s.osm)) s.map.removeLayer(s.osm);
  }, [layers]);

  return <div ref={elRef} className="map" />;
}
