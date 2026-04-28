# River Night

A offline-first PWA river guide for three Idaho wilderness rivers.

**Live:** https://rivernight.itogeospatial.com

---

## Rivers

| River | Miles | Class | Season |
|---|---|---|---|
| Selway River | 47.9 mi | IV+ | Late May – Aug |
| Middle Fork Salmon | 103 mi | V | June – Aug |
| Main Salmon | 80 mi | IV | June – Sep |

## Features

- Real-time USGS gauge + NOAA 7-day flow forecast
- GPS position → river mile (accurate to the bend)
- Offline map tiles — download before you go, use without cell service
- POI markers: rapids, camps, access points
- Flow graph with fullscreen tap
- River log with mile profile strip

## Data

River centerlines are built from actual OSM waterway geometry with RiverMaps GPX mile markers projected onto them — so GPS → river mile follows the real channel, not straight-line segments between markers.

POIs (rapids, camps, access points) come from RiverMaps 2nd/5th Ed. GPX files, which are **not included** in this repo due to copyright.

## Rebuild centerlines

If you have the RiverMaps GPX files, place them in `rivermaps/` and run:

```
python3 scripts/build_centerlines_osm.py   # fetches OSM geometry + reprojects mile markers
python3 scripts/build_salmon_rivers.py     # rebuilds MF/Main Salmon POIs from GPX
```

Requires Python 3 + `requests` (`conda install -n geodata requests -c conda-forge`).

## Deploy

Static site — no server needed. Deploy to GitHub Pages, Netlify, or Cloudflare Pages from the repo root.

Service worker pre-caches the app shell and all river GeoJSON on first load. Map tiles are cached as you browse.

---

[github.com/itogeo/rivernight](https://github.com/itogeo/rivernight)
