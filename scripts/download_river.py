#!/usr/bin/env python3
"""
download_river.py — Build Selway River centerline.

Sources:
  1. OSM Overpass  — upper run, high-quality detail
  2. USGS NHD+ HR  — extends coverage into lower canyon
  3. Hand-crafted fallback coords — fills remaining gap to take-out

Run once from the repo root:
    python3 scripts/download_river.py

Requires: requests (conda install -n geodata requests -c conda-forge)
"""

import json, math, sys
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Install requests: conda install -n geodata requests -c conda-forge")

OUT = Path(__file__).parent.parent / "data" / "river_centerline.geojson"

# ── Put-in / take-out (lon, lat) ────────────────────────────────────────────
PUT_IN   = (-115.2153, 46.0747)   # Paradise
TAKE_OUT = (-115.8342, 46.0958)   # Race Creek

# ── Endpoints for OSM fetch ──────────────────────────────────────────────────
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OSM_QUERY = """
[out:json][timeout:120];
(
  way["waterway"="river"]["name"="Selway River"]
    (45.80,-116.30,46.40,-114.80);
);
out body geom;
"""

# ── NHD service ───────────────────────────────────────────────────────────────
NHD_URL = (
    "https://hydro.nationalmap.gov/arcgis/rest/services"
    "/NHDPlus_HR/MapServer/3/query"
)

# ── Hand-crafted upper-run fallback ──────────────────────────────────────────
# The OSM ways in the upper section (lon -115.21 to -115.38) trace the river
# too far south (~lat 46.04-46.06 instead of ~46.07). This fallback uses known
# POI locations as waypoints from PUT_IN to just above Ladle, where OSM becomes
# accurate (~lon -115.384, lat 46.0717).
UPPER_FALLBACK = [
    (-115.2153, 46.0747),  # Paradise put-in
    (-115.2270, 46.0755),  # Double Drop (~mile 1.3)
    (-115.2380, 46.0762),  # Little Niagara (~mile 2.1)
    (-115.2500, 46.0768),
    (-115.2620, 46.0773),  # Wolf Creek (~mile 4.2)
    (-115.2800, 46.0780),
    (-115.2970, 46.0783),  # Galloping Gertie (~mile 6.8)
    (-115.3100, 46.0777),
    (-115.3250, 46.0765),
    (-115.3390, 46.0752),  # Glover Creek (~mile 9.5)
    (-115.3550, 46.0738),
    (-115.3700, 46.0725),
    (-115.3840, 46.0717),  # Ladle (~mile 12.4) — splice point into OSM chain
]

# ── Hand-crafted lower-canyon fallback ───────────────────────────────────────
# Bridges from the OSM western endpoint (~-115.60, 46.14) southwest to Race Creek.
# OSM Way 1348381110 ends at (-115.5997, 46.1405) — the Selway at that point is
# heading toward the lower canyon. The river descends SW from lat 46.14 to 46.10
# over the final ~18 km to Race Creek.
LOWER_FALLBACK = [
    (-115.600, 46.140),
    (-115.615, 46.137),
    (-115.630, 46.133),
    (-115.645, 46.129),
    (-115.660, 46.125),
    (-115.675, 46.121),
    (-115.690, 46.117),
    (-115.705, 46.113),
    (-115.720, 46.109),
    (-115.735, 46.106),
    (-115.750, 46.103),   # approaching Selway Falls area
    (-115.765, 46.101),
    (-115.780, 46.099),
    (-115.795, 46.098),
    (-115.810, 46.097),
    (-115.820, 46.096),
    (-115.834, 46.096),   # Race Creek take-out
]


def haversine_m(c1, c2):
    lon1, lat1 = c1;  lon2, lat2 = c2
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


# ── OSM ───────────────────────────────────────────────────────────────────────

def fetch_osm_segs():
    print("Querying Overpass for Selway River ways…")
    last_err = None
    for url in OVERPASS_URLS:
        try:
            r = requests.get(url, params={"data": OSM_QUERY}, timeout=150)
            r.raise_for_status()
            print(f"  Using {url}")
            break
        except Exception as e:
            print(f"  {url} failed: {e}")
            last_err = e
    else:
        sys.exit(f"All Overpass endpoints failed: {last_err}")
    ways = [e for e in r.json().get("elements", []) if e["type"] == "way"]
    segs = []
    for w in ways:
        coords = [(g["lon"], g["lat"]) for g in w.get("geometry", [])
                  if "lon" in g and "lat" in g]
        if len(coords) >= 2:
            segs.append(coords)
    print(f"  {len(ways)} ways → {len(segs)} segments")
    return segs


# ── NHD ───────────────────────────────────────────────────────────────────────

def fetch_nhd_segs(where, bbox, label):
    w, s, e, n = bbox
    params = {
        "where": where,
        "geometry": f"{w},{s},{e},{n}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "OBJECTID",
        "returnGeometry": "true",
        "f": "geojson",
        "outSR": "4326",
        "resultRecordCount": "2000",
    }
    r = requests.get(NHD_URL, params=params, timeout=60)
    r.raise_for_status()
    feats = r.json().get("features", [])
    segs = []
    for f in feats:
        g = f.get("geometry", {})
        if g.get("type") != "LineString":
            continue
        coords = [(c[0], c[1]) for c in g["coordinates"] if len(c) >= 2]
        if len(coords) >= 2:
            segs.append(coords)
    print(f"  NHD {label}: {len(feats)} features → {len(segs)} segments")
    return segs


# ── Greedy chain (standard — tight gap) ──────────────────────────────────────

def chain_tight(segs, anchor, gap_m=500, label=""):
    """Chain segments starting from the one nearest `anchor`.
    Stops when the nearest unused segment is > gap_m away."""
    if not segs:
        return []
    segs = [list(s) for s in segs]

    def near_d(seg):
        return min(haversine_m(anchor, seg[0]), haversine_m(anchor, seg[-1]))

    segs.sort(key=near_d)
    first = segs.pop(0)
    if haversine_m(anchor, first[-1]) < haversine_m(anchor, first[0]):
        first.reverse()
    chain = first

    for _ in range(len(segs) * 3 + 10):
        if not segs:
            break
        tail = chain[-1]
        best_i, best_d, flip = -1, float("inf"), False
        for i, s in enumerate(segs):
            d0 = haversine_m(tail, s[0])
            d1 = haversine_m(tail, s[-1])
            if d0 < best_d:
                best_d, best_i, flip = d0, i, False
            if d1 < best_d:
                best_d, best_i, flip = d1, i, True
        if best_d > gap_m:
            break
        seg = segs.pop(best_i)
        if flip:
            seg.reverse()
        if haversine_m(chain[-1], seg[0]) < 20:
            chain.extend(seg[1:])
        else:
            chain.extend(seg)

    d_tail = haversine_m(chain[-1], TAKE_OUT)
    print(f"  {label}chain: {len(chain)} nodes, "
          f"tail {d_tail/1000:.1f} km from take-out "
          f"(lon {chain[-1][0]:.4f})")
    return chain


# ── Directed westward chain (for fragmented lower canyon) ────────────────────

def chain_westward(segs, start, gap_m=5000, label=""):
    """
    Greedy chain that only accepts segments whose FAR endpoint
    (from the current tail) is at lower longitude than the tail.
    This ensures we always make westward progress, preventing the
    algorithm from following north/south tributaries.
    """
    if not segs:
        return []
    segs = [list(s) for s in segs]

    def near_d(seg):
        return min(haversine_m(start, seg[0]), haversine_m(start, seg[-1]))

    segs.sort(key=near_d)
    first = segs.pop(0)
    if haversine_m(start, first[-1]) < haversine_m(start, first[0]):
        first.reverse()
    chain = first

    for _ in range(500):
        if not segs:
            break
        tail = chain[-1]
        tail_lon = tail[0]
        tail_lat = tail[1]
        best_i, best_d, best_flip = -1, float("inf"), False
        for i, s in enumerate(segs):
            d0 = haversine_m(tail, s[0])
            d1 = haversine_m(tail, s[-1])
            # Near end is s[0] if d0 < d1, far end is s[-1] (and vice versa)
            if d0 <= d1:
                near, far = s[0], s[-1]
                do_flip = False
            else:
                near, far = s[-1], s[0]
                do_flip = True
            near_d_val = min(d0, d1)
            # Only accept if we move west and don't jump north (guards against Lochsa)
            if far[0] < tail_lon and near_d_val < gap_m and far[1] <= tail_lat + 0.01:
                if near_d_val < best_d:
                    best_d, best_i, best_flip = near_d_val, i, do_flip
        if best_i == -1:
            break
        seg = segs.pop(best_i)
        if best_flip:
            seg.reverse()
        if haversine_m(chain[-1], seg[0]) < 50:
            chain.extend(seg[1:])
        else:
            chain.extend(seg)

    d_tail = haversine_m(chain[-1], TAKE_OUT)
    print(f"  {label}westward chain: {len(chain)} nodes, "
          f"tail {d_tail/1000:.1f} km from take-out "
          f"(lon {chain[-1][0]:.4f})")
    return chain


# ── Patch gap to take-out ─────────────────────────────────────────────────────

def patch_to_takeout(chain, gap_threshold_m=3000):
    """
    If the chain tail is more than gap_threshold_m from the take-out,
    splice in the hand-crafted fallback from the nearest fallback point onward.
    """
    tail = chain[-1]
    d = haversine_m(tail, TAKE_OUT)
    if d <= gap_threshold_m:
        print(f"  Tail is {d/1000:.1f} km from take-out — no patch needed")
        return chain
    print(f"  Tail is {d/1000:.1f} km from take-out — patching with fallback")
    # Find fallback splice point nearest to current tail
    bi = min(range(len(LOWER_FALLBACK)),
             key=lambda i: haversine_m(LOWER_FALLBACK[i], tail))
    # Only append fallback from that point westward
    patch = [p for p in LOWER_FALLBACK[bi:] if p[0] < tail[0] + 0.01]
    chain.extend(patch)
    d2 = haversine_m(chain[-1], TAKE_OUT)
    print(f"  After patch: tail {d2/1000:.1f} km from take-out")
    return chain


# ── Trim to permitted run ─────────────────────────────────────────────────────

def trim_to_run(coords):
    def nearest_idx(target):
        return min(range(len(coords)), key=lambda i: haversine_m(coords[i], target))

    # For put-in: find nearest node that is at or downstream (west) of PUT_IN longitude.
    # This prevents trimming to an upstream node that would create a backwards segment.
    west_of_putin = [i for i in range(len(coords)) if coords[i][0] <= PUT_IN[0] + 0.003]
    if west_of_putin:
        i0 = min(west_of_putin, key=lambda i: haversine_m(coords[i], PUT_IN))
    else:
        i0 = nearest_idx(PUT_IN)

    i1 = nearest_idx(TAKE_OUT)
    print(f"  Put-in idx {i0} (lon {coords[i0][0]:.4f}), "
          f"take-out idx {i1} (lon {coords[i1][0]:.4f})")

    if i0 == i1:
        sys.exit("Put-in and take-out map to same node")

    if i0 > i1:
        coords.reverse()
        n = len(coords)
        i0, i1 = n - 1 - i0, n - 1 - i1

    return coords[i0: i1 + 1]


# ── Miles ─────────────────────────────────────────────────────────────────────

def cumulative_miles(coords):
    miles = [0.0]
    total = 0.0
    for i in range(1, len(coords)):
        total += haversine_m(coords[i - 1], coords[i]) / 1609.344
        miles.append(round(total, 3))
    return miles


# ── Save ─────────────────────────────────────────────────────────────────────

def save_geojson(coords, miles, source="POI-based centerline"):
    gj = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "name": "Selway River",
                "source": source,
                "vertex_miles": miles,
                "total_miles": miles[-1],
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[round(c[0], 6), round(c[1], 6)] for c in coords],
            },
        }],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(gj, indent=2))
    print(f"\n✓ Saved {len(coords)} nodes, {miles[-1]:.2f} miles → {OUT}")


# ── East-to-west OSM chain ────────────────────────────────────────────────────

def chain_osm_east_to_west(segs, gap_m=2000):
    """
    Sort segments by their easternmost longitude (descending) and chain
    them west, which is the direction the Selway flows (Paradise→Race Creek).
    """
    if not segs:
        return []
    segs = [list(s) for s in segs]
    # Orient each segment so seg[0] is its eastern (higher-lon) end
    for i, seg in enumerate(segs):
        if seg[-1][0] > seg[0][0]:
            segs[i] = list(reversed(seg))
    # Sort by easternmost lon descending (chain east → west)
    segs.sort(key=lambda s: -s[0][0])
    chain = segs.pop(0)
    for _ in range(len(segs) * 3 + 10):
        if not segs:
            break
        tail = chain[-1]
        best_i, best_d, flip = -1, float("inf"), False
        for i, s in enumerate(segs):
            d0 = haversine_m(tail, s[0])
            d1 = haversine_m(tail, s[-1])
            if d0 < best_d:
                best_d, best_i, flip = d0, i, False
            if d1 < best_d:
                best_d, best_i, flip = d1, i, True
        if best_d > gap_m:
            break
        seg = segs.pop(best_i)
        if flip:
            seg.reverse()
        if haversine_m(chain[-1], seg[0]) < 20:
            chain.extend(seg[1:])
        else:
            chain.extend(seg)
    print(f"  OSM chain: {len(chain)} nodes, "
          f"lon {chain[0][0]:.4f} → {chain[-1][0]:.4f}")
    return chain


# ── POI-based centerline builder ──────────────────────────────────────────────

def build_poi_centerline(pois_path, nodes_per_mile=20):
    """
    Build a centerline from the 19 authoritative POI waypoints.
    Linear interpolation between consecutive POIs; miles derived from reference
    mile values (not cumulative geometry), so every POI snaps to dist=0m.
    """
    pois = json.loads(pois_path.read_text())['features']
    pois.sort(key=lambda f: f['properties']['mile'])
    coords = []
    miles_out = []
    for j in range(len(pois) - 1):
        lon0, lat0 = pois[j]['geometry']['coordinates']
        lon1, lat1 = pois[j+1]['geometry']['coordinates']
        m0 = pois[j]['properties']['mile']
        m1 = pois[j+1]['properties']['mile']
        n = max(2, round((m1 - m0) * nodes_per_mile))
        for k in range(n):
            t = k / n
            coords.append([lon0 + t*(lon1-lon0), lat0 + t*(lat1-lat0)])
            miles_out.append(round(m0 + t*(m1-m0), 3))
    coords.append(list(pois[-1]['geometry']['coordinates']))
    miles_out.append(pois[-1]['properties']['mile'])
    return coords, miles_out


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    pois_path = OUT.parent / 'pois.geojson'
    if not pois_path.exists():
        sys.exit(f"pois.geojson not found at {pois_path}")

    print("Building POI-based centerline…")
    coords, miles = build_poi_centerline(pois_path, nodes_per_mile=20)
    print(f"  {len(coords)} nodes, {miles[-1]:.2f} miles (expected 47.9)")

    # Verify every POI snaps cleanly
    pois = json.loads(pois_path.read_text())['features']
    pois.sort(key=lambda f: f['properties']['mile'])
    print("\nPOI alignment:")
    for feat in pois:
        name = feat['properties']['name']
        poi_mile = feat['properties']['mile']
        poi_coord = feat['geometry']['coordinates']
        dists = [haversine_m(poi_coord, c) for c in coords]
        snap_i = min(range(len(dists)), key=lambda i: dists[i])
        print(f"  {name:<32} poi={poi_mile:5.1f}  snap={miles[snap_i]:5.1f}  dist={dists[snap_i]:.0f}m")

    save_geojson(coords, miles)


if __name__ == "__main__":
    main()
