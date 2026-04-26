/* =================================================================
   River Guide — app.js
   Flow: Select screen → Loading/download screen → Map
   ================================================================= */

// ─────────────────────────────────────────────────────────
// RIVER CONFIG
// ─────────────────────────────────────────────────────────
const RIVERS = {
  selway: {
    id: 'selway',
    name: 'Selway River',
    sub: 'Paradise → Race Creek · 47.9 mi',
    center: [46.08, -115.50],
    zoom: 11,
    totalMiles: 47.9,
    putIn:   { lat: 46.0747, lon: -115.2153, name: 'Paradise Launch Site' },
    takeOut: { lat: 46.0958, lon: -115.8342, name: 'Race Creek Takeout' },
    tileBbox:  { w: -115.90, s: 45.97, e: -115.10, n: 46.18 },
    tileZooms: [11, 12, 13, 14],
    gaugeUrl: 'https://waterservices.usgs.gov/nwis/iv/?format=json&sites=13336500&parameterCd=00060&siteStatus=all',
    noaaUrl:  'https://api.water.noaa.gov/nwps/v1/gauges/seli1/stageflow',
    dataFiles: {
      river: 'data/river_centerline.geojson',
      pois:  'data/pois.geojson',
    },
  },
};

const TILE_SOURCES = {
  satellite: { url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', label: '🛰 Satellite' },
  topo:      { url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', label: '📐 Topo' },
  osm:       { url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', label: '🗺 Street' },
};

const CLASS_COLORS = {
  'II': '#66BB6A', 'II+': '#9CCC65', 'III': '#FFA726', 'III+': '#FF7043',
  'III+/IV': '#EF5350', 'IV-': '#EF5350', 'IV': '#E53935', 'IV+': '#B71C1C', 'V': '#880E4F',
};

// ─────────────────────────────────────────────────────────
// APP STATE
// ─────────────────────────────────────────────────────────
const S = {
  river: null,          // active river config
  map: null,
  tileLayer: null,
  currentLayer: 'satellite',
  riverCoords: [],      // [[lat,lon],…]
  vertexMiles: [],      // cumulative miles per vertex
  pois: [],
  poiGroups: {},        // { rapid: LayerGroup, … }
  gpsWatch: null,
  gpsPos: null,
  gpsMarker: null,
  gpsCircle: null,
  measureMode: false,
  measurePts: [],       // [{lat,lon,idx,marker},…]
  measureLine: null,
  gaugeData: null,
  noaaForecast: null,
  downloadState: 'idle', // 'idle' | 'downloading' | 'done'
};

// ─────────────────────────────────────────────────────────
// SCREEN MANAGEMENT
// ─────────────────────────────────────────────────────────
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => {
    s.classList.toggle('hidden', s.id !== id);
  });
}

// ─────────────────────────────────────────────────────────
// SELECT SCREEN — boot check
// ─────────────────────────────────────────────────────────
function bootSelectScreen() {
  // If user already downloaded a river, show shortcut message
  const cached = localStorage.getItem('rg_cached_river');
  if (cached && RIVERS[cached]) {
    const el = document.getElementById('returning-msg');
    const nm = document.getElementById('ret-name');
    if (el && nm) {
      nm.textContent = RIVERS[cached].name;
      el.style.display = 'block';
    }
  }
}

// ─────────────────────────────────────────────────────────
// SELECT A RIVER → go to loading screen
// ─────────────────────────────────────────────────────────
async function selectRiver(riverId) {
  S.river = RIVERS[riverId];
  if (!S.river) return;

  const cached = localStorage.getItem('rg_cached_river') === riverId;

  showScreen('screen-loading');

  if (cached) {
    // Data already in service worker cache — fast path, just load data
    await runLoadSequenceFast();
  } else {
    // Full download
    await runLoadSequence();
  }
}

// ─────────────────────────────────────────────────────────
// LOADING SEQUENCE — helpers
// ─────────────────────────────────────────────────────────
function setStage(n, state) {
  // state: 'active' | 'done' | ''
  const el = document.getElementById(`s${n}`);
  if (!el) return;
  el.classList.remove('s-active', 's-done');
  if (state) el.classList.add(`s-${state}`);
}

function setStageSub(n, txt) {
  const el = document.getElementById(`s${n}-sub`);
  if (el) el.textContent = txt;
}

function setProgress(pct, label) {
  const bar = document.getElementById('prog-bar');
  const txt = document.getElementById('prog-txt');
  const pctEl = document.getElementById('prog-pct');
  if (bar) bar.style.width = `${pct}%`;
  if (txt) txt.textContent = label || '';
  if (pctEl) pctEl.textContent = `${Math.round(pct)}%`;
}

function setStatus(html) {
  const el = document.getElementById('load-status');
  if (el) el.innerHTML = html;
}

// ─────────────────────────────────────────────────────────
// FULL LOAD SEQUENCE
// ─────────────────────────────────────────────────────────
async function runLoadSequence() {
  const rv = S.river;

  // ── Stage 0: River centerline (bundled GeoJSON) ──────
  setStage(0, 'active');
  setStageSub(0, 'Reading bundled data…');
  setStatus('Loading river centerline…');
  setProgress(0, 'River data');
  const riverOk = await loadRiverData();
  setStage(0, 'done');
  setStageSub(0, riverOk ? `${S.riverCoords.length} nodes loaded` : 'Using estimated line');
  setProgress(28, 'River data ✓');

  // ── Stage 1: POIs (bundled GeoJSON) ─────────────────
  setStage(1, 'active');
  setStageSub(1, 'Loading rapids & camps…');
  setStatus('Parsing rapids & camps…');
  const poisOk = await loadPOIData();
  setStage(1, 'done');
  const rapidCount = S.pois.filter(f => f.properties.type === 'rapid').length;
  const campCount  = S.pois.filter(f => f.properties.type === 'camp').length;
  setStageSub(1, `${rapidCount} rapids · ${campCount} camps`);
  setProgress(55, 'Points of interest ✓');

  // ── Stage 2: USGS gauge + NOAA forecast ─────────────
  setStage(2, 'active');
  setStageSub(2, 'Fetching flow & NOAA forecast…');
  setStatus('Checking USGS gauge and NOAA discharge forecast…');
  const [gauge, noaaFc] = await Promise.all([fetchGaugeData(), fetchNOAAForecast()]);
  setStage(2, 'done');
  const trendArrow = noaaFc?.trend === 'rising' ? ' ↑' : noaaFc?.trend === 'falling' ? ' ↓' : '';
  setStageSub(2, gauge ? `${gauge.cfs} cfs${trendArrow}` : 'Offline — will retry');
  setProgress(82, 'Flow data ✓');

  // ── Stage 3: Activate offline caching ───────────────
  // Satellite tiles download passively as you browse — no bulk download needed
  setStage(3, 'active');
  setStageSub(3, 'Tiles load & cache as you browse');
  setStatus('Activating offline mode…');
  await delay(350);
  setStage(3, 'done');
  setStageSub(3, 'Offline service worker active');
  setProgress(100, 'All done!');

  // ── Done ─────────────────────────────────────────────
  localStorage.setItem('rg_cached_river', rv.id);
  setStatus('<strong>Ready!</strong> Launching map…');
  await delay(700);
  launchMap();
}

async function runLoadSequenceFast() {
  // Already cached — just load data files, skip tile caching
  setStatus('Returning to cached river…');

  setStage(0, 'active');
  setStageSub(0, 'Loading from cache…');
  setProgress(10, 'River data');
  await loadRiverData();
  setStage(0, 'done');
  setProgress(40, 'River data ✓');

  setStage(1, 'active');
  setProgress(55, 'POIs');
  await loadPOIData();
  setStage(1, 'done');
  setProgress(70, 'POIs ✓');

  setStage(2, 'active');
  const [gauge, noaaFc] = await Promise.all([fetchGaugeData(), fetchNOAAForecast()]);
  setStage(2, 'done');
  const trendArrow = noaaFc?.trend === 'rising' ? ' ↑' : noaaFc?.trend === 'falling' ? ' ↓' : '';
  setStageSub(2, gauge ? `${gauge.cfs} cfs${trendArrow}` : 'Offline');
  setProgress(85, 'Gauge ✓');

  setStage(3, 'done');
  setStageSub(3, 'Already cached ✓');
  setProgress(100, 'Ready!');

  setStatus('<strong>Map ready!</strong>');
  await delay(600);
  launchMap();
}

// ─────────────────────────────────────────────────────────
// DATA LOADERS
// ─────────────────────────────────────────────────────────
async function loadRiverData() {
  try {
    const resp = await fetch(S.river.dataFiles.river);
    if (!resp.ok) throw new Error('Not found');
    const gj = await resp.json();
    const feat = gj.features?.[0];
    if (!feat) throw new Error('No feature');
    const coords = feat.geometry.coordinates;
    S.riverCoords  = coords.map(c => [c[1], c[0]]);
    S.vertexMiles  = feat.properties?.vertex_miles || [];
    return true;
  } catch (e) {
    console.warn('River data:', e.message);
    return false;
  }
}

async function loadPOIData() {
  try {
    const resp = await fetch(S.river.dataFiles.pois);
    if (!resp.ok) throw new Error('Not found');
    const gj = await resp.json();
    S.pois = gj.features || [];
    return true;
  } catch (e) {
    console.warn('POI data:', e.message);
    return false;
  }
}

async function fetchGaugeData() {
  try {
    const resp = await fetch(S.river.gaugeUrl, { signal: AbortSignal.timeout(6000) });
    if (!resp.ok) throw new Error();
    const data = await resp.json();
    const vals = data?.value?.timeSeries?.[0]?.values?.[0]?.value;
    if (!vals?.length) throw new Error();
    const latest = vals[vals.length - 1];
    const cfs = parseFloat(latest.value).toLocaleString();
    const date = new Date(latest.dateTime).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    S.gaugeData = { cfs, date };
    return S.gaugeData;
  } catch {
    return null;
  }
}

async function fetchNOAAForecast() {
  try {
    const url = S.river.noaaUrl;
    if (!url) return null;
    const resp = await fetch(url, { signal: AbortSignal.timeout(8000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    const observed  = data?.observed?.data  || [];
    const forecasts = data?.forecast?.data  || [];

    // NOAA reports flow in kcfs — convert to whole cfs
    const toCfs = v => Math.round(parseFloat(v) * 1000);

    // Trend from the most recent observed readings
    let trend = 'steady';
    if (observed.length >= 3) {
      const vals = observed.slice(-6).map(d => toCfs(d.secondary)).filter(v => !isNaN(v));
      if (vals.length >= 2) {
        const delta = vals[vals.length - 1] - vals[0];
        if (delta > 100) trend = 'rising';
        else if (delta < -100) trend = 'falling';
      }
    }

    // Daily max forecast for the next 4 days
    const dayMap = new Map();
    for (const d of forecasts) {
      const dt  = new Date(d.validTime);
      const key = dt.toLocaleDateString('en-CA'); // YYYY-MM-DD in local tz
      const cfs = toCfs(d.secondary);
      if (!isNaN(cfs) && cfs > 0) {
        const prev = dayMap.get(key);
        if (!prev || cfs > prev.cfs) dayMap.set(key, { date: dt, cfs });
      }
    }

    const days = [...dayMap.values()]
      .sort((a, b) => a.date - b.date)
      .slice(0, 4);

    S.noaaForecast = { trend, days };
    return S.noaaForecast;
  } catch (e) {
    console.warn('[NOAA]', e.message);
    return null;
  }
}


// ─────────────────────────────────────────────────────────
// OFFLINE TILE DOWNLOAD
// ─────────────────────────────────────────────────────────
function deg2tile(lat, lon, z) {
  const n = Math.pow(2, z);
  const lr = lat * Math.PI / 180;
  return [
    Math.floor((lon + 180) / 360 * n),
    Math.floor((1 - Math.log(Math.tan(lr) + 1 / Math.cos(lr)) / Math.PI) / 2 * n),
  ];
}

function generateTileUrls(rv) {
  const coords = S.riverCoords.length ? S.riverCoords : null;
  const urls = new Set();
  const base = TILE_SOURCES.satellite.url;
  const BUFFER = 1; // ±1 tile buffer around each river vertex

  for (const z of rv.tileZooms) {
    if (coords) {
      for (const [lat, lon] of coords) {
        const [cx, cy] = deg2tile(lat, lon, z);
        for (let dx = -BUFFER; dx <= BUFFER; dx++)
          for (let dy = -BUFFER; dy <= BUFFER; dy++)
            urls.add(base.replace('{z}', z).replace('{y}', cy + dy).replace('{x}', cx + dx));
      }
    } else {
      const { w, s, e, n } = rv.tileBbox;
      const [xMin, yMax] = deg2tile(s, w, z);
      const [xMax, yMin] = deg2tile(n, e, z);
      for (let x = xMin; x <= xMax; x++)
        for (let y = yMin; y <= yMax; y++)
          urls.add(base.replace('{z}', z).replace('{y}', y).replace('{x}', x));
    }
  }
  return [...urls];
}

async function downloadForOffline() {
  if (S.downloadState === 'downloading') return;

  const rv = S.river;
  const urls = generateTileUrls(rv);

  S.downloadState = 'downloading';
  _setOfflineUI('downloading', 0, urls.length);

  const sw = navigator.serviceWorker?.controller;

  if (sw) {
    await new Promise(resolve => {
      const ch = new MessageChannel();
      ch.port1.onmessage = ev => {
        if (ev.data?.type === 'PRECACHE_PROGRESS')
          _setOfflineUI('downloading', ev.data.done, ev.data.total);
        if (ev.data?.type === 'PRECACHE_DONE') resolve();
      };
      sw.postMessage({ type: 'PRECACHE_TILES', urls }, [ch.port2]);
    });
  } else {
    const cache = await caches.open(`selway-tiles-v1.0.0`);
    const BATCH = 8;
    for (let i = 0; i < urls.length; i += BATCH) {
      await Promise.allSettled(urls.slice(i, i + BATCH).map(async url => {
        if (await cache.match(url)) return;
        try { const r = await fetch(url); if (r.ok) await cache.put(url, r); } catch {}
      }));
      _setOfflineUI('downloading', Math.min(i + BATCH, urls.length), urls.length);
      await delay(0);
    }
  }

  S.downloadState = 'done';
  localStorage.setItem(`rg_tiles_dl_${rv.id}`, new Date().toISOString());
  _setOfflineUI('done', urls.length, urls.length);
}

function updateOfflineUI() {
  const rv = S.river;
  if (!rv) return;
  const saved = localStorage.getItem(`rg_tiles_dl_${rv.id}`);
  if (saved || S.downloadState === 'done') {
    S.downloadState = 'done';
    _setOfflineUI('done', 0, 0, saved);
  } else {
    _setOfflineUI('idle', 0, generateTileUrls(rv).length);
  }
}

function _setOfflineUI(state, done, total, savedDate) {
  const btn    = document.getElementById('offline-btn');
  const status = document.getElementById('offline-status');
  const wrap   = document.getElementById('offline-prog-wrap');
  const bar    = document.getElementById('offline-prog-bar');
  const txt    = document.getElementById('offline-prog-txt');
  const pct    = document.getElementById('offline-prog-pct');
  if (!btn) return;

  if (state === 'idle') {
    const mb = Math.round(total * 38 / 1024);
    status.textContent = 'Not downloaded';
    status.style.color = '';
    wrap.style.display = 'none';
    btn.disabled = false;
    btn.textContent = `⬇ Download for offline · ~${mb} MB`;
    btn.className = 'dl-btn';
  } else if (state === 'downloading') {
    const p = total ? Math.round(done / total * 100) : 0;
    status.textContent = `${p}% · ${done} / ${total} tiles`;
    status.style.color = 'var(--amber-lt)';
    wrap.style.display = 'block';
    bar.style.width = `${p}%`;
    txt.textContent = `${done} of ${total} tiles cached`;
    pct.textContent = `${p}%`;
    btn.disabled = true;
    btn.textContent = 'Downloading…';
    btn.className = 'dl-btn';
  } else if (state === 'done') {
    const d = savedDate
      ? new Date(savedDate).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
      : 'today';
    status.textContent = `Ready offline · ${d}`;
    status.style.color = 'var(--water-lt)';
    wrap.style.display = 'none';
    btn.disabled = false;
    btn.textContent = '↻ Re-download';
    btn.className = 'dl-btn dl-btn-done';
  }
}

// ─────────────────────────────────────────────────────────
// LAUNCH MAP
// ─────────────────────────────────────────────────────────
function launchMap() {
  showScreen('screen-map');

  if (!S.map) {
    initMap();
  }

  drawRiverLine();
  drawPOIs();
  buildPOILists();
  updateGaugeUI();
  updateOfflineUI();
  Promise.all([fetchGaugeData(), fetchNOAAForecast()]).then(updateGaugeUI);
}

// ─────────────────────────────────────────────────────────
// MAP INIT
// ─────────────────────────────────────────────────────────
function initMap() {
  S.map = L.map('map', {
    center: S.river.center,
    zoom: S.river.zoom,
    zoomControl: false,
    attributionControl: false,
  });

  L.control.attribution({ prefix: false, position: 'bottomleft' }).addTo(S.map);
  L.control.zoom({ position: 'topright' }).addTo(S.map);

  setTileLayer('satellite');

  S.map.on('click', onMapClick);
}

function setTileLayer(key) {
  if (S.tileLayer) S.map.removeLayer(S.tileLayer);
  const src = TILE_SOURCES[key];
  S.tileLayer = L.tileLayer(src.url, {
    attribution: src.label,
    maxZoom: 18,
    crossOrigin: true,
  }).addTo(S.map);
  S.currentLayer = key;
  document.querySelectorAll('.lchip').forEach(b => {
    b.classList.toggle('active', b.dataset.layer === key);
  });
}

// ─────────────────────────────────────────────────────────
// RIVER LINE
// ─────────────────────────────────────────────────────────
function drawRiverLine() {
  if (!S.riverCoords.length) return;

  L.polyline(S.riverCoords, {
    color: '#5ba8c4',
    weight: 3,
    opacity: 0.8,
  }).addTo(S.map);

  addAccessMarker(S.river.putIn.lat,   S.river.putIn.lon,   '🚣', S.river.putIn.name,   'Mile 0');
  addAccessMarker(S.river.takeOut.lat, S.river.takeOut.lon, '🏁', S.river.takeOut.name, `Mile ${S.river.totalMiles}`);
}

function addAccessMarker(lat, lon, emoji, name, sub) {
  const icon = L.divIcon({
    html: `<div class="mk mk-access">${emoji}</div>`,
    className: '', iconSize: [32,32], iconAnchor: [16,16],
  });
  L.marker([lat, lon], { icon })
    .bindPopup(`<div class="pp"><div class="pp-name">${name}</div><div class="pp-desc">${sub}</div></div>`, { maxWidth: 240 })
    .addTo(S.map);
}

// ─────────────────────────────────────────────────────────
// POIs
// ─────────────────────────────────────────────────────────
function drawPOIs() {
  S.poiGroups = { rapid: L.layerGroup(), camp: L.layerGroup(), poi: L.layerGroup(), access: L.layerGroup() };

  S.pois.forEach(feat => {
    const p = feat.properties;
    const [lon, lat] = feat.geometry.coordinates;
    const type = p.type || 'poi';
    const dColor = CLASS_COLORS[p.difficulty] || '#607D8B';

    const emojiMap = { rapid: '🌊', camp: '⛺', access: '🚣', poi: '📍' };
    const clsMap   = { rapid: 'mk-rapid', camp: 'mk-camp', access: 'mk-access', poi: 'mk-poi' };
    const emoji = emojiMap[type] || '📍';
    const cls   = clsMap[type]   || 'mk-poi';
    const borderStyle = p.difficulty ? `border-color:${dColor}` : '';

    const icon = L.divIcon({
      html: `<div class="mk ${cls}" style="${borderStyle}">${emoji}</div>`,
      className: '', iconSize: [30,30], iconAnchor: [15,15],
    });

    const diffBadge = p.difficulty
      ? `<span class="dpill" style="background:${dColor}">${p.difficulty}</span>` : '';

    const marker = L.marker([lat, lon], { icon });
    marker.bindPopup(`
      <div class="pp">
        <div class="pp-mile">Mile ${p.mile}</div>
        <div class="pp-name">${p.name}</div>
        ${diffBadge}
        <div class="pp-desc">${p.description || ''}</div>
      </div>
    `, { maxWidth: 280 });

    if (S.poiGroups[type]) marker.addTo(S.poiGroups[type]);
  });

  Object.values(S.poiGroups).forEach(g => g.addTo(S.map));
}

function togglePOILayer(type) {
  const g = S.poiGroups[type];
  const btn = document.querySelector(`.fchip[data-poi="${type}"]`);
  if (!g || !btn) return;
  if (S.map.hasLayer(g)) { S.map.removeLayer(g); btn.classList.remove('active'); }
  else                    { g.addTo(S.map);       btn.classList.add('active');    }
}

// ─────────────────────────────────────────────────────────
// POI LISTS IN PANEL
// ─────────────────────────────────────────────────────────
function buildPOILists() {
  const rapids = S.pois.filter(f => f.properties.type === 'rapid').sort((a,b) => a.properties.mile - b.properties.mile);
  const camps  = S.pois.filter(f => ['camp','access'].includes(f.properties.type)).sort((a,b) => a.properties.mile - b.properties.mile);

  document.getElementById('rapid-list').innerHTML = rapids.map(f => {
    const p = f.properties;
    const c = CLASS_COLORS[p.difficulty] || '#888';
    const [lon, lat] = f.geometry.coordinates;
    return `
      <div class="prow" onclick="flyTo(${lat},${lon})">
        <div class="pmile">Mi ${p.mile.toFixed(1)}</div>
        <div>
          <div class="pname">${p.name}</div>
          ${p.difficulty ? `<span class="dpill" style="background:${c}">${p.difficulty}</span>` : ''}
          <div class="pdesc">${(p.description||'').substring(0,90)}${p.description?.length>90?'…':''}</div>
        </div>
      </div>`;
  }).join('');

  document.getElementById('camp-list').innerHTML = camps.map(f => {
    const p = f.properties;
    const em = p.type === 'access' ? '🚣' : '⛺';
    const [lon, lat] = f.geometry.coordinates;
    return `
      <div class="prow" onclick="flyTo(${lat},${lon})">
        <div class="pmile">${em}<br>Mi ${p.mile.toFixed(1)}</div>
        <div>
          <div class="pname">${p.name}</div>
          <div class="pdesc">${(p.description||'').substring(0,100)}${p.description?.length>100?'…':''}</div>
        </div>
      </div>`;
  }).join('');
}

function flyTo(lat, lon) {
  closePanel();
  S.map.flyTo([lat, lon], 14, { duration: 1.2 });
}

// ─────────────────────────────────────────────────────────
// GAUGE UI
// ─────────────────────────────────────────────────────────
function updateGaugeUI() {
  const g  = S.gaugeData;
  const nf = S.noaaForecast;

  const arrows = { rising: ' ↑', falling: ' ↓', steady: ' →' };
  const arrow = nf?.trend ? (arrows[nf.trend] || '') : '';
  const txt = g ? `${g.cfs} cfs${arrow}` : '—';

  const gvalEl = document.getElementById('gval');
  const gpEl   = document.getElementById('gauge-panel');
  if (gvalEl) gvalEl.textContent = txt;
  if (gpEl)   gpEl.textContent   = txt;

  // Populate NOAA daily forecast rows in the info panel
  const fc = document.getElementById('noaa-forecast-rows');
  if (!fc) return;

  if (!nf?.days?.length) {
    fc.innerHTML = '<div class="irow"><span class="ilbl" style="color:var(--sage)">Unavailable offline</span></div>';
    return;
  }

  const now          = new Date();
  const todayStr     = now.toDateString();
  const tomorrowStr  = new Date(now.getTime() + 86400000).toDateString();

  fc.innerHTML = nf.days.map(d => {
    const label = d.date.toDateString() === todayStr    ? 'Today'    :
                  d.date.toDateString() === tomorrowStr ? 'Tomorrow' :
                  d.date.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
    return `<div class="irow"><span class="ilbl">${label}</span><span class="ival">${d.cfs.toLocaleString()} cfs</span></div>`;
  }).join('');
}

// ─────────────────────────────────────────────────────────
// GPS
// ─────────────────────────────────────────────────────────
function toggleGPS() {
  if (S.gpsWatch !== null) stopGPS(); else startGPS();
}

function startGPS() {
  if (!navigator.geolocation) { toast('GPS not available on this device', 'error'); return; }
  const btn = document.getElementById('gps-btn');
  btn.classList.add('active');
  toast('Finding location…');
  S.gpsWatch = navigator.geolocation.watchPosition(onGPSPos, onGPSErr, {
    enableHighAccuracy: true, maximumAge: 5000, timeout: 12000,
  });
}

function stopGPS() {
  if (S.gpsWatch !== null) { navigator.geolocation.clearWatch(S.gpsWatch); S.gpsWatch = null; }
  if (S.gpsMarker) { S.map.removeLayer(S.gpsMarker); S.gpsMarker = null; }
  if (S.gpsCircle) { S.map.removeLayer(S.gpsCircle); S.gpsCircle = null; }
  document.getElementById('gps-btn').classList.remove('active');
  setMilePill(null);
}

function onGPSPos(pos) {
  const { latitude: lat, longitude: lon, accuracy } = pos.coords;
  S.gpsPos = { lat, lon, accuracy };

  const icon = L.divIcon({
    html: '<div class="gps-wrap"><div class="gps-ring"></div><div class="gps-core"></div></div>',
    className: '', iconSize: [20,20], iconAnchor: [10,10],
  });

  if (!S.gpsMarker) {
    S.gpsMarker = L.marker([lat, lon], { icon, zIndexOffset: 1000 }).addTo(S.map);
    S.map.setView([lat, lon], Math.max(S.map.getZoom(), 13));
  } else {
    S.gpsMarker.setLatLng([lat, lon]);
  }

  if (!S.gpsCircle) {
    S.gpsCircle = L.circle([lat, lon], { radius: accuracy, color: '#5ba8c4', fillColor: '#5ba8c4', fillOpacity: 0.08, weight: 1 }).addTo(S.map);
  } else {
    S.gpsCircle.setLatLng([lat, lon]).setRadius(accuracy);
  }

  const mile = snapToMile(lat, lon);
  setMilePill(mile);
}

function onGPSErr(err) {
  const m = { 1:'Permission denied', 2:'Location unavailable', 3:'GPS timed out' };
  toast(m[err.code] || 'GPS error', 'error');
  document.getElementById('gps-btn').classList.remove('active');
  S.gpsWatch = null;
}

function centerOnGPS() {
  if (S.gpsPos) S.map.setView([S.gpsPos.lat, S.gpsPos.lon], 14);
  else startGPS();
}

function setMilePill(mile) {
  const el = document.getElementById('mile-pill');
  if (!el) return;
  if (mile === null) { el.style.display = 'none'; return; }
  el.textContent = `River Mile ${mile.toFixed(1)}`;
  el.style.display = 'block';
}

function snapToMile(lat, lon) {
  if (!S.riverCoords.length) return null;
  let bi = 0, bd = Infinity;
  for (let i = 0; i < S.riverCoords.length; i++) {
    const d = hav(lat, lon, S.riverCoords[i][0], S.riverCoords[i][1]);
    if (d < bd) { bd = d; bi = i; }
  }
  if (S.vertexMiles.length > bi) return S.vertexMiles[bi];
  return (bi / (S.riverCoords.length - 1)) * S.river.totalMiles;
}

// ─────────────────────────────────────────────────────────
// MEASURE TOOL
// ─────────────────────────────────────────────────────────
function toggleMeasure() {
  S.measureMode = !S.measureMode;
  document.getElementById('measure-btn').classList.toggle('active', S.measureMode);
  S.map.getContainer().style.cursor = S.measureMode ? 'crosshair' : '';
  if (!S.measureMode) clearMeasure();
  else { clearMeasure(); toast('Tap point A on the river', 'warn', 4000); }
}

function clearMeasure() {
  S.measurePts.forEach(p => { if (p.marker) S.map.removeLayer(p.marker); });
  S.measurePts = [];
  if (S.measureLine) { S.map.removeLayer(S.measureLine); S.measureLine = null; }
  document.getElementById('measure-result').style.display = 'none';
}

function onMapClick(e) {
  if (!S.measureMode) return;
  const { lat, lng } = e.latlng;
  const snapped = snapToRiver(lat, lng);
  const label = S.measurePts.length === 0 ? 'A' : 'B';

  const icon = L.divIcon({
    html: `<div class="mpt">${label}</div>`,
    className: '', iconSize: [26,26], iconAnchor: [13,13],
  });
  const marker = L.marker([snapped.lat, snapped.lon], { icon }).addTo(S.map);
  S.measurePts.push({ ...snapped, marker });

  if (S.measurePts.length === 1) {
    toast('Now tap point B', 'warn', 3000);
  } else if (S.measurePts.length === 2) {
    computeDistance();
  } else {
    // Reset for new measurement
    S.measurePts.slice(0, -1).forEach(p => S.map.removeLayer(p.marker));
    if (S.measureLine) { S.map.removeLayer(S.measureLine); S.measureLine = null; }
    const last = S.measurePts[S.measurePts.length - 1];
    S.measurePts = [last];
    toast('Point A set — tap point B', 'warn', 3000);
  }
}

function snapToRiver(lat, lon) {
  if (!S.riverCoords.length) return { lat, lon, idx: 0 };
  let bi = 0, bd = Infinity;
  for (let i = 0; i < S.riverCoords.length; i++) {
    const d = hav(lat, lon, S.riverCoords[i][0], S.riverCoords[i][1]);
    if (d < bd) { bd = d; bi = i; }
  }
  return { lat: S.riverCoords[bi][0], lon: S.riverCoords[bi][1], idx: bi };
}

function computeDistance() {
  const [a, b] = S.measurePts;
  const si = Math.min(a.idx, b.idx);
  const ei = Math.max(a.idx, b.idx);
  const path = S.riverCoords.slice(si, ei + 1);

  if (S.measureLine) S.map.removeLayer(S.measureLine);
  S.measureLine = L.polyline(path, {
    color: '#f0a830', weight: 4, opacity: 0.9, dashArray: '8 4',
  }).addTo(S.map);

  let miles;
  if (S.vertexMiles.length > ei) {
    miles = Math.abs(S.vertexMiles[ei] - S.vertexMiles[si]);
  } else {
    let km = 0;
    for (let i = si; i < ei; i++) {
      km += hav(S.riverCoords[i][0], S.riverCoords[i][1],
                S.riverCoords[i+1][0], S.riverCoords[i+1][1]) / 1000;
    }
    miles = km * 0.621371;
  }

  // What's between?
  const mA = S.vertexMiles[si] || 0;
  const mB = S.vertexMiles[ei] || S.river.totalMiles;
  const mLo = Math.min(mA, mB), mHi = Math.max(mA, mB);
  const between = S.pois.filter(f => f.properties.mile >= mLo && f.properties.mile <= mHi);
  const rapids = between.filter(f => f.properties.type === 'rapid');
  const camps  = between.filter(f => f.properties.type === 'camp');

  const rText = rapids.length ? `<span>🌊 ${rapids.length} rapid${rapids.length>1?'s':''}: </span>${rapids.slice(0,4).map(f=>f.properties.name).join(', ')}${rapids.length>4?' …':''}` : '';
  const cText = camps.length  ? `<span>⛺ ${camps.length} camp${camps.length>1?'s':''}: </span>${camps.slice(0,3).map(f=>f.properties.name).join(', ')}${camps.length>3?' …':''}` : '';
  const betweenHtml = [rText, cText].filter(Boolean).map(t => `<div>${t}</div>`).join('');

  document.getElementById('mr-mi').textContent    = `${miles.toFixed(1)} river miles`;
  document.getElementById('mr-time').textContent  = `~${(miles/3.5).toFixed(1)}–${(miles/2).toFixed(1)} hrs float time`;
  document.getElementById('mr-between').innerHTML = betweenHtml;
  document.getElementById('measure-result').style.display = 'block';

  S.map.fitBounds(S.measureLine.getBounds(), { padding: [60, 60] });
}

// ─────────────────────────────────────────────────────────
// RIVER LOG (scroll view)
// ─────────────────────────────────────────────────────────
function openRiverLog() {
  buildRiverLog();
  showScreen('screen-river');
}

function closeRiverLog() {
  showScreen('screen-map');
}

function rlogFlyTo(lat, lon) {
  showScreen('screen-map');
  if (S.map) S.map.flyTo([lat, lon], 14, { duration: 1.2 });
}

function buildRiverLog() {
  const container = document.getElementById('rlog-body');
  if (!container) return;

  const sorted = [...S.pois].sort((a, b) => a.properties.mile - b.properties.mile);
  if (!sorted.length) {
    container.innerHTML = '<div style="color:var(--sage);font-size:13px;padding:40px;text-align:center">No features loaded</div>';
    return;
  }

  const typeIcon = { rapid: '🌊', camp: '⛺', access: '🚣', poi: '📍' };

  let html = '';
  sorted.forEach((feat, idx) => {
    const p = feat.properties;
    const [lon, lat] = feat.geometry.coordinates;
    const type = p.type || 'poi';

    if (idx > 0) {
      const gap = p.mile - sorted[idx - 1].properties.mile;
      if (gap >= 1.5) {
        html += `
          <div class="rlog-gap">
            <div class="rlog-gap-track"></div>
            <div class="rlog-gap-label">${gap.toFixed(1)} mi ·· ·· ··</div>
          </div>`;
      }
    }

    const diffBadge = p.difficulty
      ? `<span class="dpill" style="background:${CLASS_COLORS[p.difficulty]||'#888'};margin-top:4px;display:inline-block">${p.difficulty}</span>`
      : '';

    html += `
      <div class="rlog-entry" onclick="rlogFlyTo(${lat},${lon})">
        <div class="rlog-dot-col">
          <div class="rlog-dot d-${type}"></div>
        </div>
        <div class="rlog-card-item">
          <div class="rlog-mi">Mile ${p.mile.toFixed(1)} · ${typeIcon[type] || '📍'}</div>
          <div class="rlog-nm">${p.name}</div>
          ${diffBadge}
          <div class="rlog-dc">${(p.description || '').substring(0, 130)}${(p.description || '').length > 130 ? '…' : ''}</div>
        </div>
      </div>`;
  });

  container.innerHTML = html;
}

// ─────────────────────────────────────────────────────────
// PANEL
// ─────────────────────────────────────────────────────────
function openPanel()  { document.getElementById('panel').classList.add('open'); }
function closePanel() { document.getElementById('panel').classList.remove('open'); }

function showTab(tab) {
  document.querySelectorAll('.tabbtn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.tpane').forEach(p => p.classList.toggle('active', p.id === `tab-${tab}`));
}

// ─────────────────────────────────────────────────────────
// TOAST
// ─────────────────────────────────────────────────────────
let _toastTimer;
function toast(msg, type = '', ms = 3000) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.className = `show${type ? ' t-'+type : ''}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), ms);
}

// ─────────────────────────────────────────────────────────
// UTILS
// ─────────────────────────────────────────────────────────
function hav(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const φ1 = lat1 * Math.PI / 180, φ2 = lat2 * Math.PI / 180;
  const dφ = (lat2 - lat1) * Math.PI / 180;
  const dλ = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dφ/2)**2 + Math.cos(φ1)*Math.cos(φ2)*Math.sin(dλ/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

// ─────────────────────────────────────────────────────────
// SERVICE WORKER
// ─────────────────────────────────────────────────────────
async function registerSW() {
  if ('serviceWorker' in navigator) {
    try {
      await navigator.serviceWorker.register('/sw.js');
      console.log('[App] SW registered');
    } catch (e) {
      console.warn('[App] SW failed:', e);
    }
  }
}

// ─────────────────────────────────────────────────────────
// BOOT
// ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await registerSW();
  bootSelectScreen();

  // ── Map screen wiring ──────────────────────────────
  document.getElementById('menu-btn')   ?.addEventListener('click', openPanel);
  document.getElementById('pclose')     ?.addEventListener('click', closePanel);
  document.getElementById('gps-btn')    ?.addEventListener('click', toggleGPS);
  document.getElementById('center-btn') ?.addEventListener('click', centerOnGPS);
  document.getElementById('measure-btn')?.addEventListener('click', toggleMeasure);
  document.getElementById('log-btn')    ?.addEventListener('click', openRiverLog);

  document.querySelectorAll('.lchip').forEach(b =>
    b.addEventListener('click', () => setTileLayer(b.dataset.layer)));

  document.querySelectorAll('.fchip').forEach(b =>
    b.addEventListener('click', () => togglePOILayer(b.dataset.poi)));

  document.querySelectorAll('.tabbtn').forEach(b =>
    b.addEventListener('click', () => showTab(b.dataset.tab)));

  document.getElementById('offline-btn')
    ?.addEventListener('click', downloadForOffline);

  // Warn before leaving if GPS is on
  window.addEventListener('beforeunload', e => {
    if (S.gpsWatch !== null) {
      e.preventDefault();
      return 'GPS tracking is active. Leave the app?';
    }
  });
});
