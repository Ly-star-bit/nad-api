// Map for verification results, shared by / and /batch. Leaflet is vendored in static/vendor/leaflet and
// loaded on first use. Tiles come from openstreetmap.org, which is fine for an internal tool under their
// usage policy (https://operations.osmfoundation.org/policies/tiles/); for heavy or public use, change
// TILES (and ATTRIBUTION) to your own tile source.
export const TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
export const ATTRIBUTION = '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors';

let loading = null;

function loadLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  loading ||= Promise.all([
    // wait for the stylesheet too: a map created before leaflet.css applies lays its tiles out wrong
    new Promise((resolve, reject) => {
      const link = Object.assign(document.createElement('link'), {
        rel: 'stylesheet', href: 'static/vendor/leaflet/leaflet.css', onload: resolve, onerror: reject,
      });
      // ahead of style.css, so the page's .map overrides win over Leaflet's defaults
      document.head.insertBefore(link, document.head.querySelector('link[rel="stylesheet"]'));
    }),
    new Promise((resolve, reject) => {
      document.head.append(Object.assign(document.createElement('script'), {
        src: 'static/vendor/leaflet/leaflet.js', onload: resolve, onerror: reject,
      }));
    }),
  ]).then(() => window.L, (e) => { loading = null; throw e; });
  return loading;
}

// -> { L, map }, or null when `el` left the page while Leaflet was loading. Rejects if Leaflet can't load.
export async function createMap(el, { canvas = false } = {}) {
  const L = await loadLeaflet();
  if (!el.isConnected) return null;
  const map = L.map(el, { attributionControl: false, scrollWheelZoom: false, preferCanvas: canvas, zoomSnap: 0.5 });
  L.control.attribution({ prefix: false }).addTo(map);
  L.tileLayer(TILES, { maxZoom: 19, attribution: ATTRIBUTION }).addTo(map);
  // wheel zoom only once the map has been clicked, so scrolling the page never gets caught by it
  map.on('click focus', () => map.scrollWheelZoom.enable());
  el.addEventListener('mouseleave', () => map.scrollWheelZoom.disable());
  return { L, map };
}

export function fit(map, latlngs) {
  if (!latlngs.length) map.setView([39.5, -98.35], 4);  // contiguous US
  else if (latlngs.length === 1) map.setView(latlngs[0], 17);
  else map.fitBounds(latlngs, { paddingTopLeft: [32, 32], paddingBottomRight: [64, 32], maxZoom: 18 });  // room for labels on the right
}

// Marker colours come from the CSS tokens, so they follow light / dark mode; read them at draw time.
export function colors() {
  const s = getComputedStyle(document.documentElement);
  const v = (name) => s.getPropertyValue(name).trim();
  return {
    ok: v('--ok-fill'), warn: v('--warn-fill'), bad: v('--bad-fill'), none: v('--none-fill'),
    ink: v('--ink'), ink2: v('--ink-2'), surface: v('--surface'), accent: v('--accent'),
  };
}

// best_match, else the geocoded point (a ZIP-area search has an extent, not a point), else - for an official
// register's street_only / not_found, which has neither - the nearest number it suggests on the same street.
// Candidates (other streets or towns) are never used: they are not where the input is.
export function resultPoint(r) {
  const p = pointOf(r);
  return p && p.point;
}

// -> { point, kind: 'match' | 'geocode' | 'nearby' } or null
export function pointOf(r) {
  const ok = (p) => p && Number.isFinite(p.lat) && Number.isFinite(p.lon);
  if (!r) return null;
  if (ok(r.best_match)) return { point: r.best_match, kind: 'match' };
  if (ok(r.geocode)) return { point: r.geocode, kind: 'geocode' };
  const s = (r.suggestions || []).find(ok);
  return s ? { point: s, kind: 'nearby' } : null;
}
