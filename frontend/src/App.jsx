import { useEffect, useRef, useState } from "react";
import MapView from "./MapView.jsx";
import Panel from "./Panel.jsx";
import { fetchBaseLayers, fetchHillshadeBounds, fetchMeta, fetchScenario } from "./api.js";

const STEP = 0.5;
const PLAY_MS = 700;

const DEFAULT_LAYERS = {
  flood: true, lz: true, links: true, villages: true, roads: true,
  rivers: true, power: true, cuts: true, osm: false,
};

function Logo() {
  return (
    <svg className="logo" viewBox="0 0 32 32" aria-hidden="true">
      <path d="M16 2 4 6.5v8.3c0 7.3 5.1 13.2 12 15.2 6.9-2 12-7.9 12-15.2V6.5z" fill="#0f1a22" stroke="#3ddc84" strokeWidth="1.8" />
      <path d="M11 10v12M21 10v12M11 16h10" stroke="#3ddc84" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
  );
}

export default function App() {
  const [meta, setMeta] = useState(null);
  const [base, setBase] = useState(null);
  const [hillshade, setHillshade] = useState(null);
  // Optional ?level=3.5 in the URL opens the app at that water level (handy for the demo).
  const [level, setLevel] = useState(() => {
    const v = parseFloat(new URLSearchParams(window.location.search).get("level"));
    return Number.isFinite(v) ? Math.min(6, Math.max(0, Math.round(v * 2) / 2)) : 0;
  });
  const [scenario, setScenario] = useState(null);
  const [queryMs, setQueryMs] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [layers, setLayers] = useState(DEFAULT_LAYERS);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  const maxLevel = meta ? Math.max(...meta.levels) : 6;

  useEffect(() => {
    Promise.all([fetchMeta(), fetchBaseLayers(), fetchHillshadeBounds()])
      .then(([m, b, h]) => { setMeta(m); setBase(b); setHillshade(h); })
      .catch((e) => setError(`Cannot reach the API (${e.message}). Is the backend running on port 8000?`));
  }, []);

  // Every slider change asks PostGIS for that level; a newer request cancels the older one.
  useEffect(() => {
    if (!meta) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    fetchScenario(level, ctrl.signal)
      .then(({ data, ms }) => { setScenario(data); setQueryMs(ms); setError(null); })
      .catch((e) => { if (e.name !== "AbortError") setError(e.message); });
  }, [level, meta]);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setLevel((l) => {
        if (l + STEP > maxLevel) { setPlaying(false); return l; }
        return l + STEP;
      });
    }, PLAY_MS);
    return () => clearInterval(id);
  }, [playing, maxLevel]);

  const togglePlay = () => {
    if (!playing && level >= maxLevel) setLevel(0);
    setPlaying((p) => !p);
  };
  const reset = () => { setPlaying(false); setLevel(0); };

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <Logo />
          <div>
            <h1>SENTINEL SHIELD</h1>
            <p>Helicopter landing zone finder for cyclone rescue</p>
          </div>
        </div>
        <div className="badges">
          <span className="badge">Dowa District &middot; Kasangadzi River, Mponela</span>
          <span className="badge warn">SIMULATION</span>
          <span className={`badge live ${error ? "down" : ""}`}><i />PostGIS live</span>
        </div>
      </header>
      <main className="main">
        <section className="map-wrap">
          <MapView meta={meta} base={base} hillshade={hillshade} scenario={scenario} layers={layers} level={level} />
          <div className="legend">
            <div><span className="sw flood" />Flood extent</div>
            <div><span className="sw lzs">H</span>Safe landing zone</div>
            <div><span className="sw lzl">H</span>Lost landing zone</div>
            <div><span className="sw vn" />Village</div>
            <div><span className="sw va" />Stranded, reachable</div>
            <div><span className="sw vr" />No safe zone in reach</div>
            <div><span className="sw link" />Rescue link</div>
          </div>
          {error && <div className="error">{error}</div>}
          {!meta && !error && <div className="loading">Connecting to PostGIS&hellip;</div>}
        </section>
        <Panel
          meta={meta} scenario={scenario} level={level} maxLevel={maxLevel} step={STEP}
          onLevel={(v) => { setPlaying(false); setLevel(v); }}
          playing={playing} onPlay={togglePlay} onReset={reset}
          layers={layers} onLayers={setLayers} queryMs={queryMs}
        />
      </main>
    </div>
  );
}
