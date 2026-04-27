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
OVERPASS_URL = "https://overpass.kumi.systems/api/interpreter"
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

# ── Hand-crafted lower-canyon fallback ───────────────────────────────────────
# Used only to patch any gap between available data and take-out.
# Coordinates derived from USGS topo / known landmarks along the lower Selway.
# Selway Falls is at approximately (-115.758, 46.110); Race Creek at take-out.
LOWER_FALLBACK = [
    (-115.620, 46.143),
    (-115.635, 46.148),
    (-115.648, 46.152),
    (-115.660, 46.155),
    (-115.672, 46.153),
    (-115.683, 46.149),
    (-115.693, 46.143),
    (-115.703, 46.137),
    (-115.712, 46.130),
    (-115.720, 46.125),
    (-115.728, 46.120),
    (-115.737, 46.117),
    (-115.745, 46.115),
    (-115.752, 46.113),
    (-115.758, 46.110),   # Selway Falls
    (-115.764, 46.107),
    (-115.771, 46.104),
    (-115.779, 46.102),
    (-115.787, 46.100),
    (-115.795, 46.098),
    (-115.804, 46.097),
    (-115.812, 46.097),
    (-115.820, 46.097),
    (-115.828, 46.097),
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
    r = requests.get(OVERPASS_URL, params={"data": OSM_QUERY}, timeout=150)
    r.raise_for_status()
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
            # Only accept if we move west (far endpoint has lower lon)
            if far[0] < tail_lon and near_d_val < gap_m:
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

def save_geojson(coords, miles):
    gj = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "name": "Selway River",
                "source": "OSM + USGS NHD+ HR + manual lower-canyon",
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


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    # ── 1. OSM upper run (east→west sort, works because ways connect perfectly)
    osm_segs = fetch_osm_segs()
    print("Building OSM east→west chain…")
    osm_chain = chain_osm_east_to_west(osm_segs, gap_m=2000)
    osm_tail = osm_chain[-1] if osm_chain else PUT_IN
    print(f"  OSM tail: lon {osm_tail[0]:.4f}, "
          f"{haversine_m(osm_tail, TAKE_OUT)/1000:.1f} km from take-out")

    # ── 2. NHD lower canyon (unnamed StreamRiver features) ───────────────────
    # The lower Selway canyon lacks GNIS_Name in NHD; query StreamRiver by bbox.
    print("\nQuerying NHD lower canyon…")
    nhd_lower = fetch_nhd_segs(
        where="FType=460",
        bbox=(-115.86, 46.09, -115.57, 46.17),
        label="lower canyon",
    )

    # ── 3. Chain lower canyon westward from OSM tail ──────────────────────────
    print("Building directed westward chain from OSM tail…")
    lower_chain = chain_westward(nhd_lower, osm_tail, gap_m=5000, label="NHD lower ")

    # ── 4. Merge: OSM upper + NHD lower points west of OSM tail ──────────────
    if lower_chain:
        west_pts = [p for p in lower_chain if p[0] < osm_tail[0] - 0.001]
        merged = osm_chain + west_pts
    else:
        merged = osm_chain
    print(f"\nMerged: {len(merged)} nodes, tail lon {merged[-1][0]:.4f}")

    # ── 5. Patch remaining gap to take-out ───────────────────────────────────
    merged = patch_to_takeout(merged, gap_threshold_m=2000)

    # ── 6. Trim to permitted run and compute miles ────────────────────────────
    print("\nTrimming to permitted run…")
    coords = trim_to_run(merged)
    if len(coords) < 10:
        sys.exit(f"Trimmed result too short ({len(coords)} nodes)")

    # Snap endpoints: prepend PUT_IN and extend tail to TAKE_OUT if needed
    if haversine_m(coords[0], PUT_IN) > 200:
        coords.insert(0, list(PUT_IN))
        print(f"  Prepended PUT_IN (was {haversine_m(coords[1], PUT_IN)/1000:.2f} km off)")
    if haversine_m(coords[-1], TAKE_OUT) > 200:
        tail = coords[-1]
        bi = min(range(len(LOWER_FALLBACK)),
                 key=lambda i: haversine_m(LOWER_FALLBACK[i], tail))
        for p in LOWER_FALLBACK[bi:]:
            coords.append(list(p))
        print(f"  Appended {len(LOWER_FALLBACK) - bi} fallback nodes to reach take-out")

    miles = cumulative_miles(coords)
    print(f"  Total: {miles[-1]:.2f} miles (expected ~47.9)")

    save_geojson(coords, miles)


if __name__ == "__main__":
    main()
