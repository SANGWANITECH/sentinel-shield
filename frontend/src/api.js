// Thin wrappers around the FastAPI endpoints (proxied by Vite at /api).

async function getJson(url, signal) {
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
  return { data: await res.json(), ms: res.headers.get("X-Query-Ms") };
}

export const fetchMeta = () => getJson("/api/meta").then((r) => r.data);
export const fetchBaseLayers = () => getJson("/api/base-layers").then((r) => r.data);
export const fetchScenario = (level, signal) => getJson(`/api/scenario/${level.toFixed(1)}`, signal);
export const fetchHillshadeBounds = () =>
  fetch("/basemap/hillshade.json").then((r) => (r.ok ? r.json() : null)).catch(() => null);
