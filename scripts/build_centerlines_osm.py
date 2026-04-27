#!/usr/bin/env python3
"""
build_centerlines_osm.py — Replace interpolated centerlines with actual OSM river geometry.

For each river:
  1. Fetch ways from Overpass API (real river channel, every bend)
  2. Stitch ways into a directed LineString (put-in → take-out)
  3. Project GPX mile markers onto the OSM line
  4. Interpolate river miles at every OSM vertex
  5. Write GeoJSON with vertex_miles (same format — no app.js changes needed)

Mile marker sources (all from RiverMaps GPX files):
  selway     — M-0 … M-47       (Selway 2nd Ed.)
  mfsalmon   — M-000-MF … M-103-MF  (MF & Main Salmon 5th Ed.)
  mainsalmon — M-000 … M-080    (MF & Main Salmon 5th Ed.)

Run from repo root:
    /opt/anaconda3/envs/geodata/bin/python3 scripts/build_centerlines_osm.py
"""

import json, math, re, sys, time
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("requests not found — run: conda install -n geodata requests -c conda-forge")

REPO         = Path(__file__).parent.parent
GPX_SELWAY   = REPO / "rivermaps" / "RiverMaps - Selway 2nd Edition.gpx"
GPX_SALMON   = REPO / "rivermaps" / "RiverMaps - Middle Fork & Main Salmon 5th Edition.gpx"
NS           = {"gpx": "http://www.topografix.com/GPX/1/1"}
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

RIVERS = {
    "selway": {
        "display_name": "Selway River",
        "osm_name":     "Selway River",
        "bbox":         (45.90, -115.95, 46.20, -114.70),   # (S, W, N, E)
        "putin":        (45.860251, -114.744262),            # matches M-0
        "takeout":      (46.0958,   -115.8342),
        "total_miles":  47.9,
        "out_dir":      REPO / "data",
        "gpx":          GPX_SELWAY,
        "marker_re":    r"^M-(\d+)$",
        "marker_group": 1,
        "source":       "RiverMaps LLC · Selway 2nd Ed. / OSM",
    },
    "mfsalmon": {
        "display_name": "Middle Fork of the Salmon River",
        "osm_name":     "Middle Fork Salmon River",
        "bbox":         (44.35, -115.50, 45.50, -114.55),
        "putin":        (44.532, -115.294),
        "takeout":      (45.365, -114.685),
        "total_miles":  103.0,
        "out_dir":      REPO / "data" / "mfsalmon",
        "gpx":          GPX_SALMON,
        "marker_re":    r"^M-(\d+)-MF$",
        "marker_group": 1,
        "source":       "RiverMaps LLC · MF & Main Salmon 5th Ed. / OSM",
    },
    "mainsalmon": {
        "display_name": "Main Salmon River",
        "osm_name":     "Salmon River",
        "bbox":         (45.25, -116.10, 45.70, -114.55),
        "putin":        (45.370, -114.688),
        "takeout":      (45.458, -115.934),
        "total_miles":  80.0,
        "out_dir":      REPO / "data" / "mainsalmon",
        "gpx":          GPX_SALMON,
        "marker_re":    r"^M-(\d+)$",
        "marker_group": 1,
        "source":       "RiverMaps LLC · MF & Main Salmon 5th Ed. / OSM",
    },
}


# ── Geometry helpers ──────────────────────────────────────────────────────────

def hav_m(lat1, lon1, lat2, lon2):
    """Haversine distance in metres."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def cum_distances(coords):
    """Cumulative haversine distances along a polyline. Returns list of length len(coords)."""
    dists = [0.0]
    for i in range(1, len(coords)):
        dists.append(dists[-1] + hav_m(coords[i-1][0], coords[i-1][1],
                                       coords[i][0],   coords[i][1]))
    return dists


def project_onto_line(lat, lon, coords, cum_dists):
    """
    Find where (lat, lon) falls along a polyline.
    Returns the interpolated cumulative distance (metres) at the nearest foot of perpendicular.
    Uses a flat-earth approximation scaled by cos(lat) — accurate enough for river segments.
    """
    best_perp = float("inf")
    best_cum  = 0.0

    for i in range(len(coords) - 1):
        lat1, lon1 = coords[i]
        lat2, lon2 = coords[i + 1]

        mid_lat  = (lat1 + lat2) / 2
        cos_lat  = math.cos(math.radians(mid_lat))
        sx, sy   = 111_320 * cos_lat, 110_540   # metres per degree

        ax, ay = 0.0, 0.0
        bx = (lon2 - lon1) * sx
        by = (lat2 - lat1) * sy
        px = (lon  - lon1) * sx
        py = (lat  - lat1) * sy

        seg_len_sq = bx * bx + by * by
        t = 0.0 if seg_len_sq == 0 else max(0.0, min(1.0, (px*bx + py*by) / seg_len_sq))

        nx, ny  = t * bx, t * by
        perp_d  = math.hypot(px - nx, py - ny)

        if perp_d < best_perp:
            best_perp = perp_d
            best_cum  = cum_dists[i] + t * (cum_dists[i + 1] - cum_dists[i])

    return best_cum


def interpolate_miles(cum_dists, control_points):
    """
    Given control_points = [(cum_dist_m, river_mile), ...] (sorted),
    return a river-mile value for each cumulative distance in cum_dists.
    Extrapolates linearly beyond the first/last control point.
    """
    cps = sorted(control_points)
    miles = []

    for cd in cum_dists:
        if cd <= cps[0][0]:
            # Before first marker — linear back-extrapolation from first segment
            if len(cps) > 1 and cps[1][0] > cps[0][0]:
                slope = (cps[1][1] - cps[0][1]) / (cps[1][0] - cps[0][0])
                miles.append(cps[0][1] + slope * (cd - cps[0][0]))
            else:
                miles.append(cps[0][1])
        elif cd >= cps[-1][0]:
            # After last marker — linear forward-extrapolation from last segment
            if len(cps) > 1 and cps[-1][0] > cps[-2][0]:
                slope = (cps[-1][1] - cps[-2][1]) / (cps[-1][0] - cps[-2][0])
                miles.append(cps[-1][1] + slope * (cd - cps[-1][0]))
            else:
                miles.append(cps[-1][1])
        else:
            # Binary-search for surrounding pair
            lo, hi = 0, len(cps) - 1
            while lo + 1 < hi:
                mid = (lo + hi) // 2
                if cps[mid][0] <= cd:
                    lo = mid
                else:
                    hi = mid
            d0, m0 = cps[lo]
            d1, m1 = cps[hi]
            t = (cd - d0) / (d1 - d0) if d1 > d0 else 0.0
            miles.append(m0 + t * (m1 - m0))

    return [round(m, 3) for m in miles]


# ── GPX helpers ───────────────────────────────────────────────────────────────

def load_gpx_markers(gpx_path, marker_re, group):
    """Return [(mile, lat, lon), ...] sorted by mile."""
    tree = ET.parse(gpx_path)
    root = tree.getroot()
    out  = []
    for w in root.findall("gpx:wpt", NS):
        name_el = w.find("gpx:name", NS)
        if name_el is None:
            continue
        m = re.match(marker_re, name_el.text.strip())
        if m:
            out.append((int(m.group(group)), float(w.get("lat")), float(w.get("lon"))))
    return sorted(out)


# ── Overpass fetch ────────────────────────────────────────────────────────────

def fetch_osm_ways(bbox, osm_name):
    """Fetch river ways from Overpass within bbox, preferring those named osm_name."""
    s, w, n, e = bbox
    query = f"""
[out:json][timeout:120];
(
  way["waterway"="river"]({s},{w},{n},{e});
);
out body;
>;
out skel qt;
"""
    print(f"  Querying Overpass for '{osm_name}' …")
    resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=150)
    resp.raise_for_status()
    data = resp.json()

    nodes = {el["id"]: (el["lat"], el["lon"])
             for el in data["elements"] if el["type"] == "node"}
    all_ways = [el for el in data["elements"] if el["type"] == "way"]

    # Filter by name — try exact then substring
    def way_name(w):
        return (w.get("tags") or {}).get("name", "") or ""

    exact    = [w for w in all_ways if way_name(w) == osm_name]
    sub      = [w for w in all_ways if osm_name.lower() in way_name(w).lower()]
    chosen   = exact or sub or all_ways

    print(f"  OSM ways total={len(all_ways)}  name-matched={len(chosen)}")
    return chosen, nodes


# ── Way stitching ─────────────────────────────────────────────────────────────

def stitch_ways(ways, nodes, putin, takeout):
    """
    Chain ways into an ordered (lat, lon) list from put-in to take-out.
    At forks, pick the branch whose free end is closer to the take-out.
    """
    way_by_id = {w["id"]: w for w in ways}

    # Index each way's two endpoint nodes
    endpoint_index = {}   # node_id → [(way_id, already_reversed)]
    for w in ways:
        nds = w["nodes"]
        endpoint_index.setdefault(nds[0],  []).append(w["id"])
        endpoint_index.setdefault(nds[-1], []).append(w["id"])

    # Find the best starting endpoint (closest to put-in)
    best_d, start_wid, start_rev = float("inf"), None, False
    for wid, w in way_by_id.items():
        nds = w["nodes"]
        for nid, rev in [(nds[0], False), (nds[-1], True)]:
            lat, lon = nodes[nid]
            d = hav_m(putin[0], putin[1], lat, lon)
            if d < best_d:
                best_d, start_wid, start_rev = d, wid, rev

    coords       = []
    visited_ways = set()
    cur_wid      = start_wid
    cur_rev      = start_rev

    while cur_wid and cur_wid not in visited_ways:
        visited_ways.add(cur_wid)
        nds = way_by_id[cur_wid]["nodes"]
        if cur_rev:
            nds = list(reversed(nds))

        # Append vertices (skip first vertex after the first way to avoid duplicates)
        for nid in nds[1 if coords else 0:]:
            lat, lon = nodes[nid]
            coords.append((lat, lon))

        end_nid = nds[-1]
        candidates = endpoint_index.get(end_nid, [])

        # Pick next unvisited way from this end node, preferring free-end closer to takeout
        best_d  = float("inf")
        cur_wid = None
        for nxt_wid in candidates:
            if nxt_wid in visited_ways:
                continue
            nds2 = way_by_id[nxt_wid]["nodes"]
            if nds2[0] == end_nid:
                rev2, free_nid = False, nds2[-1]
            elif nds2[-1] == end_nid:
                rev2, free_nid = True,  nds2[0]
            else:
                continue
            lat, lon = nodes[free_nid]
            d = hav_m(takeout[0], takeout[1], lat, lon)
            if d < best_d:
                best_d, cur_wid, cur_rev = d, nxt_wid, rev2

    return coords


# ── GeoJSON output ────────────────────────────────────────────────────────────

def build_geojson(coords, vertex_miles, display_name, total_miles, source):
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "name":         display_name,
                "source":       source,
                "vertex_miles": vertex_miles,
                "total_miles":  total_miles,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[round(lon, 7), round(lat, 7)] for lat, lon in coords],
            },
        }],
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def process_river(rid, cfg):
    print(f"\n{'='*60}")
    print(f"  {cfg['display_name']}")
    print(f"{'='*60}")

    # Load GPX mile markers
    markers_raw = load_gpx_markers(cfg["gpx"], cfg["marker_re"], cfg["marker_group"])
    print(f"  GPX markers: {len(markers_raw)}  (mi {markers_raw[0][0]}–{markers_raw[-1][0]})")

    # Fetch OSM ways
    ways, nodes = fetch_osm_ways(cfg["bbox"], cfg["osm_name"])
    if not ways:
        print("  ERROR: no ways returned — skipping")
        return

    # Stitch into directed polyline
    coords = stitch_ways(ways, nodes, cfg["putin"], cfg["takeout"])
    if len(coords) < 2:
        print(f"  ERROR: stitched only {len(coords)} coords — skipping")
        return
    print(f"  Stitched polyline: {len(coords)} vertices")

    # Compute cumulative distances along OSM line
    cum_d = cum_distances(coords)
    total_osm_m = cum_d[-1]
    print(f"  OSM length: {total_osm_m/1609.34:.1f} mi  (guidebook: {cfg['total_miles']} mi)")

    # Project each GPX marker onto the OSM line → (cum_dist, river_mile)
    control_pts = []
    for mile, lat, lon in markers_raw:
        cd = project_onto_line(lat, lon, coords, cum_d)
        control_pts.append((cd, float(mile)))

    # Add a virtual takeout anchor at exactly total_miles
    control_pts.append((cum_d[-1], cfg["total_miles"]))
    control_pts.sort()

    # Interpolate river miles at every vertex
    vertex_miles = interpolate_miles(cum_d, control_pts)

    print(f"  Mile range on vertices: {min(vertex_miles):.2f} – {max(vertex_miles):.2f}")

    # Write GeoJSON
    cfg["out_dir"].mkdir(parents=True, exist_ok=True)
    out_path = cfg["out_dir"] / "river_centerline.geojson"
    gj = build_geojson(coords, vertex_miles, cfg["display_name"], cfg["total_miles"], cfg["source"])
    out_path.write_text(json.dumps(gj, indent=2))
    print(f"  ✓ Written: {out_path}  ({len(coords)} nodes)")


def main():
    for rid, cfg in RIVERS.items():
        process_river(rid, cfg)
        if rid != list(RIVERS.keys())[-1]:
            print("\n  (sleeping 5 s between Overpass requests…)")
            time.sleep(5)

    print("\n\nDone. Re-run scripts/build_salmon_rivers.py only if POI data needs updating.")
    print("River centerlines now use real OSM geometry with projected mileage markers.")


if __name__ == "__main__":
    main()
