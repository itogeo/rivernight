#!/usr/bin/env python3
"""
download_river.py — Build Selway River centerline.

Sources:
  Upper run (Paradise → Ham Rapid, miles 0–26.5):
    OSM relation 17877072 — all ways filtered to canyon lat band (< 46.12)
  Lower run (Ham Rapid → Race Creek, miles 26.5–47.9):
    Catmull-Rom spline through POI anchors (OSM has nothing here)

Mileage: pure arc-length scaled to 47.9 mi. No rubber-sheeting.

Run from repo root:
    python3 scripts/download_river.py
"""

import json, math, sys
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Install requests: conda install -n geodata requests -c conda-forge")

OUT       = Path(__file__).parent.parent / "data" / "river_centerline.geojson"
POIS_PATH = Path(__file__).parent.parent / "data" / "pois.geojson"

PUT_IN      = (-115.2153, 46.0747)   # Paradise Launch, mile 0
TAKE_OUT    = (-115.8342, 46.0958)   # Race Creek, mile 47.9
TOTAL_MILES = 47.9

OSM_RELATION_ID = 17877072
# Strip nodes north of this lat — filters out the Lochsa confluence tangent
CANYON_LAT_MAX = 46.12
# NHD fallback if OSM relation fetch fails
NHD_URL = (
    "https://hydro.nationalmap.gov/arcgis/rest/services"
    "/NHDPlus_HR/MapServer/3/query"
)
OVERPASS_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
# Ham Rapid — where OSM coverage ends, spline takes over
SPLINE_START_MILE = 26.5


# ── Geometry ──────────────────────────────────────────────────────────────────

def haversine_m(c1, c2):
    lon1, lat1 = c1; lon2, lat2 = c2
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── OSM relation fetch ────────────────────────────────────────────────────────

def fetch_osm_relation(relation_id):
    """Fetch all ways in an OSM relation, return as list of coord-lists.
    Nodes north of CANYON_LAT_MAX are dropped to remove confluence artifacts."""
    query = f"""
[out:json][timeout:120];
relation({relation_id});
way(r);
out body geom;
"""
    last_err = None
    for url in OVERPASS_URLS:
        try:
            r = requests.get(url, params={"data": query}, timeout=150)
            r.raise_for_status()
            print(f"  Using {url}")
            break
        except Exception as e:
            print(f"  {url} failed: {e}")
            last_err = e
    else:
        print(f"  All Overpass endpoints failed: {last_err}")
        return []

    elements = [e for e in r.json().get("elements", []) if e["type"] == "way"]
    segs = []
    for e in elements:
        coords = [
            (g["lon"], g["lat"])
            for g in e.get("geometry", [])
            if "lon" in g and "lat" in g
            and g["lat"] < CANYON_LAT_MAX       # drop confluence tangent
            and g["lon"] < PUT_IN[0] + 0.05     # drop headwaters east of put-in
        ]
        if len(coords) >= 2:
            segs.append(coords)
    print(f"  Relation {relation_id}: {len(elements)} ways → "
          f"{len(segs)} usable canyon segments")
    return segs


# ── NHD fallback ──────────────────────────────────────────────────────────────

def fetch_nhd_named_selway():
    print("Querying NHD+ HR for 'Selway River' named segments…")
    params = {
        "where": "GNIS_Name = 'Selway River'",
        "geometry": "-116.10,45.80,-115.00,46.40",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "OBJECTID,GNIS_Name,StreamOrde",
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
    print(f"  {len(feats)} features → {len(segs)} usable segments")
    return segs


# ── Westward-only chain ───────────────────────────────────────────────────────

def chain_westward(segs, start_anchor, gap_m=2000, label=""):
    """Chain segments starting near start_anchor, only accepting segments
    whose far end is west (lower longitude) of the current tail."""
    if not segs:
        return []
    segs = [list(s) for s in segs]

    def dist_to(seg):
        return min(haversine_m(start_anchor, seg[0]), haversine_m(start_anchor, seg[-1]))

    segs.sort(key=dist_to)
    first = segs.pop(0)
    if haversine_m(start_anchor, first[-1]) < haversine_m(start_anchor, first[0]):
        first.reverse()
    chain = first[:]

    for _ in range(len(segs) * 4 + 20):
        if not segs:
            break
        tail = chain[-1]
        best_i, best_d, flip = -1, float("inf"), False
        for i, s in enumerate(segs):
            d0 = haversine_m(tail, s[0])
            d1 = haversine_m(tail, s[-1])
            if d0 <= d1:
                near_d, far, do_flip = d0, s[-1], False
            else:
                near_d, far, do_flip = d1, s[0],  True
            if far[0] < tail[0] and near_d < gap_m and near_d < best_d:
                best_d, best_i, flip = near_d, i, do_flip
        if best_i == -1:
            break
        seg = segs.pop(best_i)
        if flip:
            seg.reverse()
        if haversine_m(chain[-1], seg[0]) < 30:
            chain.extend(seg[1:])
        else:
            chain.extend(seg)

    d_tail = haversine_m(chain[-1], TAKE_OUT)
    print(f"  {label}chain: {len(chain)} nodes  "
          f"lon {chain[0][0]:.4f} → {chain[-1][0]:.4f}  "
          f"tail {d_tail/1000:.1f} km from take-out")
    return chain


# ── Catmull-Rom spline ────────────────────────────────────────────────────────

def catmull_rom_spline(points, n_per_seg=40):
    if len(points) < 2:
        return list(points)
    pts_ext = [points[0]] + list(points) + [points[-1]]
    result = []
    for i in range(1, len(pts_ext) - 2):
        p0, p1, p2, p3 = pts_ext[i-1], pts_ext[i], pts_ext[i+1], pts_ext[i+2]
        for k in range(n_per_seg):
            t  = k / n_per_seg
            t2 = t * t
            t3 = t2 * t
            lon = 0.5 * (
                2*p1[0] + (-p0[0]+p2[0])*t
                + (2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*t2
                + (-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3
            )
            lat = 0.5 * (
                2*p1[1] + (-p0[1]+p2[1])*t
                + (2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*t2
                + (-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3
            )
            result.append((lon, lat))
    result.append(tuple(points[-1]))
    return result


# ── Trim ──────────────────────────────────────────────────────────────────────

def trim_to_run(coords):
    def nearest_idx(target):
        return min(range(len(coords)), key=lambda i: haversine_m(coords[i], target))
    i0 = nearest_idx(PUT_IN)
    i1 = nearest_idx(TAKE_OUT)
    d0 = haversine_m(PUT_IN,   coords[i0])
    d1 = haversine_m(TAKE_OUT, coords[i1])
    print(f"  Put-in  snap: lon={coords[i0][0]:.4f} lat={coords[i0][1]:.4f} dist={d0:.0f}m")
    print(f"  Take-out snap: lon={coords[i1][0]:.4f} lat={coords[i1][1]:.4f} dist={d1:.0f}m")
    if i0 > i1:
        coords = list(reversed(coords))
        n = len(coords)
        i0, i1 = n-1-i0, n-1-i1
    return list(coords[i0 : i1+1])


# ── Mileage ───────────────────────────────────────────────────────────────────

def calibrated_arc_miles(coords, anchor_pois):
    """Piecewise arc-length mileage calibrated at each anchor POI.

    Geometry (lat/lon) is never touched. Only the mile parameter is adjusted
    so that each POI's nearest chain node aligns with its guidebook mile.
    Between anchors, miles are proportional to physical arc-length.
    """
    arc_m = [0.0]
    for i in range(1, len(coords)):
        arc_m.append(arc_m[-1] + haversine_m(coords[i-1], coords[i]))

    # Find nearest chain index for each guidebook POI
    pairs = []
    for feat in anchor_pois:
        coord = feat['geometry']['coordinates']
        mile  = feat['properties']['mile']
        idx   = min(range(len(coords)), key=lambda i: haversine_m(coords[i], tuple(coord)))
        pairs.append((idx, mile))

    # Always include the exact endpoints as anchors
    pairs.append((0, 0.0))
    pairs.append((len(coords) - 1, TOTAL_MILES))

    # Sort by chain index; keep first occurrence when indices collide
    pairs.sort()
    mono = [pairs[0]]
    for idx, mile in pairs[1:]:
        if idx > mono[-1][0]:
            mono.append((idx, mile))
    pairs = mono

    result = [0.0] * len(coords)
    for k in range(len(pairs) - 1):
        i0, m0 = pairs[k]
        i1, m1 = pairs[k + 1]
        a0, a1 = arc_m[i0], arc_m[i1]
        for i in range(i0, i1 + 1):
            result[i] = m0 if a1 == a0 else m0 + (arc_m[i] - a0) / (a1 - a0) * (m1 - m0)

    print(f"  Raw arc: {arc_m[-1]/1609.344:.2f} mi  ({len(pairs)} calibration anchors)")
    return [round(m, 3) for m in result]


# ── Report ────────────────────────────────────────────────────────────────────

def report_poi_snaps(coords, miles, pois):
    print("\nPOI snap report:")
    for feat in pois:
        pc   = feat['geometry']['coordinates']
        name = feat['properties']['name']
        ref  = feat['properties']['mile']
        idx  = min(range(len(coords)), key=lambda i: haversine_m(pc, coords[i]))
        dist = haversine_m(pc, coords[idx])
        print(f"  {name:<32} ref={ref:5.1f}  snap={miles[idx]:5.1f}  dist={dist:.0f}m")


# ── Save ──────────────────────────────────────────────────────────────────────

def save_geojson(coords, miles, source):
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
    print(f"\n✓ Saved {len(coords)} nodes, {miles[-1]:.2f} mi → {OUT}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not POIS_PATH.exists():
        sys.exit(f"pois.geojson not found at {POIS_PATH}")
    pois = json.loads(POIS_PATH.read_text())['features']
    pois.sort(key=lambda f: f['properties']['mile'])
    lower_pois = [p for p in pois if p['properties']['mile'] >= SPLINE_START_MILE]

    upper_chain = []
    source_upper = ""

    # ── Step 1: OSM relation for upper canyon ─────────────────────────────────
    print(f"\n── Step 1: OSM relation {OSM_RELATION_ID} (upper canyon) ──")
    try:
        osm_segs = fetch_osm_relation(OSM_RELATION_ID)
        if osm_segs:
            upper_chain = chain_westward(osm_segs, PUT_IN, gap_m=3000, label="OSM ")
            source_upper = "OSM relation 17877072"
    except Exception as e:
        print(f"  OSM failed: {e}")

    # ── Step 2: NHD fallback if OSM failed ───────────────────────────────────
    if not upper_chain:
        print("\n── Step 2: NHD+ HR fallback ──")
        try:
            nhd_segs = fetch_nhd_named_selway()
            if nhd_segs:
                upper_chain = chain_westward(nhd_segs, PUT_IN, gap_m=2000, label="NHD ")
                source_upper = "NHD+ HR"
        except Exception as e:
            print(f"  NHD failed: {e}")

    # ── Step 3: Catmull-Rom lower canyon (OSM has nothing here) ───────────────
    print(f"\n── Step 3: Catmull-Rom lower canyon "
          f"(miles {SPLINE_START_MILE}–{TOTAL_MILES}) ──")

    if upper_chain:
        tail = upper_chain[-1]
        # Lower POIs west of the current upper chain tail
        rem_pois = [p for p in lower_pois
                    if p['geometry']['coordinates'][0] < tail[0] - 0.005]
        spline_pts = [tail] + [tuple(p['geometry']['coordinates']) for p in rem_pois]
        lower_spline = catmull_rom_spline(spline_pts, n_per_seg=40)
        print(f"  Spline: {len(lower_spline)} nodes through {len(spline_pts)-1} anchors")
        full_chain = upper_chain + list(lower_spline[1:])
        source = f"{source_upper} + Catmull-Rom lower canyon"
    else:
        # Complete fallback: spline through all POIs
        all_pts   = [tuple(p['geometry']['coordinates']) for p in pois]
        full_chain = catmull_rom_spline(all_pts, n_per_seg=40)
        source    = "Catmull-Rom spline (all POIs)"
        print(f"  Full POI spline: {len(full_chain)} nodes")

    # ── Step 4: Trim ─────────────────────────────────────────────────────────
    print("\n── Step 4: Trim to put-in / take-out ──")
    full_chain = trim_to_run(list(full_chain))
    print(f"  {len(full_chain)} nodes after trim")

    # ── Step 5: Calibrated mileage ────────────────────────────────────────────
    print("\n── Step 5: Calibrated mileage ──")
    miles = calibrated_arc_miles(full_chain, pois)

    report_poi_snaps(full_chain, miles, pois)
    save_geojson(full_chain, miles, source)


if __name__ == "__main__":
    main()
