#!/usr/bin/env python3
"""
download_river.py — Fetch Selway River centerline from OSM Overpass API
and save to data/river_centerline.geojson with cumulative mile markers.

Run once from the repo root:
    python3 scripts/download_river.py

Requires: requests (conda install -n geodata requests -c conda-forge)
"""

import json, math, sys, time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Install requests: conda install -n geodata requests -c conda-forge")

OUT = Path(__file__).parent.parent / "data" / "river_centerline.geojson"

# Overpass query — all OSM ways tagged waterway=river named Selway River
# in the rough bounding box of the permitted run
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
QUERY = """
[out:json][timeout:90];
(
  way["waterway"="river"]["name"="Selway River"]
    (45.90,-116.10,46.30,-115.00);
);
out body geom;
"""

# Put-in and take-out coordinates for direction check
PUT_IN  = (-115.2153, 46.0747)   # Paradise (lon, lat)
TAKE_OUT = (-115.8342, 46.0958)  # Race Creek (lon, lat)


def haversine_m(c1, c2):
    """Distance in metres between two (lon, lat) points."""
    lon1, lat1 = c1;  lon2, lat2 = c2
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def fetch_ways():
    print("Querying Overpass API for Selway River ways…")
    r = requests.post(OVERPASS_URL, data={"data": QUERY}, timeout=120)
    r.raise_for_status()
    data = r.json()
    ways = data.get("elements", [])
    print(f"  Got {len(ways)} way segments")
    return ways


def build_node_index(ways):
    """Build {node_id: (lon, lat)} from way geometry."""
    nodes = {}
    for w in ways:
        for g in w.get("geometry", []):
            nodes[g.get("ref", id(g))] = (g["lon"], g["lat"])
    return nodes


def ways_to_segments(ways):
    """Each way → ordered list of (lon, lat) from its geometry."""
    segs = []
    for w in ways:
        coords = [(g["lon"], g["lat"]) for g in w.get("geometry", [])]
        if len(coords) >= 2:
            segs.append(coords)
    return segs


def chain_segments(segs, tol=0.0005):
    """
    Greedy chain: start with the segment whose first node is closest to put-in,
    then repeatedly find the next segment that shares an endpoint.
    """
    if not segs:
        return []

    def dist_to_putin(seg):
        return min(
            haversine_m(seg[0], PUT_IN),
            haversine_m(seg[-1], PUT_IN)
        )

    segs = [list(s) for s in segs]
    # Start with segment nearest put-in
    segs.sort(key=dist_to_putin)
    chain = segs.pop(0)
    # Orient so first point is closer to put-in
    if haversine_m(chain[-1], PUT_IN) < haversine_m(chain[0], PUT_IN):
        chain.reverse()

    def close(a, b):
        return haversine_m(a, b) < tol * 111_000  # tol degrees → metres approx

    max_iter = len(segs) * 2
    itr = 0
    while segs and itr < max_iter:
        itr += 1
        tail = chain[-1]
        best_i, best_d, best_flip = -1, float("inf"), False
        for i, seg in enumerate(segs):
            d0 = haversine_m(tail, seg[0])
            d1 = haversine_m(tail, seg[-1])
            if d0 < best_d:
                best_d, best_i, best_flip = d0, i, False
            if d1 < best_d:
                best_d, best_i, best_flip = d1, i, True

        if best_d > 500:  # > 500 m gap — give up searching for this segment
            break

        seg = segs.pop(best_i)
        if best_flip:
            seg.reverse()
        # Skip duplicate endpoint
        if close(chain[-1], seg[0]):
            chain.extend(seg[1:])
        else:
            chain.extend(seg)

    print(f"  Chained {len(chain)} coordinate nodes "
          f"({len(segs)} segment(s) not connected)")
    return chain


def trim_to_run(coords):
    """
    Keep only nodes between put-in and take-out (the permitted 47.9 mi run).
    Find the nodes nearest each endpoint and slice.
    """
    def nearest_idx(target):
        return min(range(len(coords)), key=lambda i: haversine_m(coords[i], target))

    i0 = nearest_idx(PUT_IN)
    i1 = nearest_idx(TAKE_OUT)
    if i0 > i1:
        coords.reverse()
        i0, i1 = len(coords) - 1 - i1, len(coords) - 1 - i0
    trimmed = coords[i0: i1 + 1]
    print(f"  Trimmed to {len(trimmed)} nodes (put-in idx {i0} → take-out idx {i1})")
    return trimmed


def cumulative_miles(coords):
    miles = [0.0]
    total = 0.0
    for i in range(1, len(coords)):
        total += haversine_m(coords[i - 1], coords[i]) / 1609.344
        miles.append(round(total, 3))
    return miles


def save_geojson(coords, miles):
    gj = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "name": "Selway River",
                "source": "OpenStreetMap",
                "vertex_miles": miles,
                "total_miles": miles[-1],
            },
            "geometry": {
                "type": "LineString",
                # GeoJSON uses [lon, lat]
                "coordinates": [[round(c[0], 6), round(c[1], 6)] for c in coords],
            },
        }],
    }
    OUT.write_text(json.dumps(gj, indent=2))
    print(f"\n✓ Saved {len(coords)} nodes, {miles[-1]:.2f} miles → {OUT}")


def main():
    ways = fetch_ways()
    if not ways:
        sys.exit("No ways returned. Check Overpass query or network.")

    segs = ways_to_segments(ways)
    chain = chain_segments(segs)

    if not chain:
        sys.exit("Could not build a connected chain. Check segment overlap.")

    coords = trim_to_run(chain)
    miles = cumulative_miles(coords)

    total = miles[-1]
    print(f"  Total distance: {total:.2f} miles (expected ~47.9)")

    save_geojson(coords, miles)


if __name__ == "__main__":
    main()
