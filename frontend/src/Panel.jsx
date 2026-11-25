import { useEffect, useRef, useState } from "react";

// Smoothly count from the previous value to the new one.
function useTween(target, ms = 450) {
  const [val, setVal] = useState(target ?? 0);
  const from = useRef(target ?? 0);
  useEffect(() => {
    if (target == null) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      setVal(target);
      from.current = target;
      return;
    }
    const start = performance.now();
    const a = from.current;
    let raf;
    const tick = (t) => {
      const k = Math.min(1, (t - start) / ms);
      const e = 1 - Math.pow(1 - k, 3);
      const v = a + (target - a) * e;
      setVal(v);
      if (k < 1) raf = requestAnimationFrame(tick);
      else from.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); from.current = target; };
  }, [target, ms]);
  return Math.round(val);
}

function Counter({ label, value, tone }) {
  const v = useTween(value);
  return (
    <div className={`counter ${tone}`}>
      <div className="counter-val">{value == null ? "--" : v.toLocaleString()}</div>
      <div className="counter-lbl">{label}</div>
    </div>
  );
}

const LAYER_LABELS = [
  ["flood", "Flood extent"], ["lz", "Landing zones"], ["links", "Rescue links"],
  ["villages", "Villages"], ["cuts", "Road cuts"], ["roads", "Roads"],
  ["rivers", "Rivers"], ["power", "Power lines"], ["osm", "OSM tiles (online)"],
];

export default function Panel({
  meta, scenario, level, maxLevel, step, onLevel, playing, onPlay, onReset, layers, onLayers, queryMs,
}) {
  const t = scenario?.totals;
  const stranded = scenario?.stranded.features ?? [];
  const ticks = [];
  for (let i = 0; i <= maxLevel; i += 1) ticks.push(i);

  return (
    <aside className="panel">
      <div className="counters">
        <Counter label="People exposed" value={t?.people_exposed} tone="blue" />
        <Counter label="Villages stranded" value={t?.villages_stranded} tone="amber" />
        <Counter label="Reachable by helicopter" value={t?.reachable} tone="green" />
        <Counter label="No safe landing zone" value={t?.no_safe_zone} tone="red" />
      </div>

      <div className="block">
        <div className="block-head">
          <h2>Flood level</h2>
          <span className="mono big">{level.toFixed(1)} m</span>
        </div>
        <input
          className="slider" type="range" min="0" max={maxLevel} step={step} value={level}
          onChange={(e) => onLevel(parseFloat(e.target.value))}
          style={{ "--pct": `${(level / maxLevel) * 100}%` }}
          aria-label="Flood level in metres"
        />
        <div className="ticks">{ticks.map((i) => <span key={i}>{i}</span>)}</div>
        <div className="buttons">
          <button className={`btn primary ${playing ? "on" : ""}`} onClick={onPlay}>
            <svg viewBox="0 0 16 16" aria-hidden="true">
              {playing ? <path d="M4 3h3v10H4zM9 3h3v10H9z" /> : <path d="M4 2.5v11l9-5.5z" />}
            </svg>
            {playing ? "Pause" : "Simulate rising water"}
          </button>
          <button className="btn" onClick={onReset}>Reset</button>
        </div>
        <div className="stats mono">
          <span>{t ? `${t.safe_lz} safe LZ` : ""}</span>
          <span>{t ? `${t.lost_lz} lost` : ""}</span>
          <span>{t ? `${t.roads_cut} roads cut` : ""}</span>
        </div>
      </div>

      <div className="block grow">
        <div className="block-head">
          <h2>Rescue assignments</h2>
          <span className="muted small">by population</span>
        </div>
        <ul className="assign">
          {stranded.length === 0 && <li className="empty">No villages stranded at this level.</li>}
          {stranded.map((f) => {
            const p = f.properties;
            const ok = p.status === "reachable";
            return (
              <li key={p.id} className={ok ? "ok" : "bad"}>
                <div className="row1">
                  <span className="code mono">{p.code}</span>
                  <span className="name">{p.name}</span>
                  <span className="pop mono">{(p.population ?? 0).toLocaleString()}</span>
                </div>
                <div className="row2 mono">
                  {ok
                    ? <>&rarr; {p.lz_code} &middot; {(p.dist_m / 1000).toFixed(1)} km &middot; ETA {Math.round(p.eta_min)} min</>
                    : <>No safe zone in reach &middot; send boat team</>}
                </div>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="block">
        <h2>Layers</h2>
        <div className="toggles">
          {LAYER_LABELS.map(([k, label]) => (
            <label key={k} className="toggle">
              <input type="checkbox" checked={layers[k]} onChange={() => onLayers({ ...layers, [k]: !layers[k] })} />
              <span className="box" />
              {label}
            </label>
          ))}
        </div>
      </div>

      <div className="block">
        <h2>Landing zone criteria</h2>
        <div className="chips">
          {(meta?.criteria ?? []).map((c) => <span key={c} className="chip">{c}</span>)}
        </div>
        <div className="foot mono">
          {meta?.staging_base && <span>Base: KIA Lilongwe &middot; {meta.assumptions.helicopter_speed_kmh} km/h</span>}
          {queryMs && <span>PostGIS {queryMs} ms</span>}
        </div>
      </div>
    </aside>
  );
}
