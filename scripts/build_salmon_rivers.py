#!/usr/bin/env python3
"""
build_salmon_rivers.py — Generate centerline + POI GeoJSON for:
  - Middle Fork of the Salmon River  (Boundary Creek → Main Salmon confluence)
  - Main Salmon River                (Corn Creek / Cache Bar → Vinegar Creek)

Source: RiverMaps - Middle Fork & Main Salmon 5th Edition.gpx
  Mileage markers:
    Middle Fork → M-000-MF … M-103-MF  (104 markers, 0-103 mi)
    Main Salmon → M-000     … M-080     (81 markers,  0-80 mi)
  POI prefixes: R- rapids, C- camps, P- points of interest

Run from repo root:
    python3 scripts/build_salmon_rivers.py
"""

import json, math, re, sys
from pathlib import Path
import xml.etree.ElementTree as ET

GPX_PATH = Path(__file__).parent.parent / "rivermaps" / "RiverMaps - Middle Fork & Main Salmon 5th Edition.gpx"
OUT_MF   = Path(__file__).parent.parent / "data" / "mfsalmon"
OUT_MAIN = Path(__file__).parent.parent / "data" / "mainsalmon"

NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


# ── Geometry ──────────────────────────────────────────────────────────────────

def hav_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── GPX parse ─────────────────────────────────────────────────────────────────

def load_gpx(path):
    tree = ET.parse(path)
    root = tree.getroot()
    wpts = root.findall("gpx:wpt", NS)
    result = []
    for w in wpts:
        name_el = w.find("gpx:name", NS)
        desc_el = w.find("gpx:desc", NS)
        if name_el is None:
            continue
        result.append({
            "name": name_el.text.strip(),
            "desc": (desc_el.text or "").strip() if desc_el is not None else "",
            "lat":  float(w.get("lat")),
            "lon":  float(w.get("lon")),
        })
    return result


# ── Difficulty mapping ────────────────────────────────────────────────────────

DIFF_MAP = {
    "II":     "II",
    "II-III": "III",
    "II-IV":  "III+",
    "III":    "III",
    "III-IV": "III+/IV",
    "III-V":  "IV+",
    "IV":     "IV",
    "IV-V":   "IV+",
    "V":      "V",
}

def parse_rapid(desc):
    """Return (display_name, difficulty) from description like 'Alder Creek Rapid (III)'."""
    m = re.search(r"\(([^)]+)\)$", desc.strip())
    if m:
        raw_diff = m.group(1)
        diff = DIFF_MAP.get(raw_diff, None)
        name = desc[: m.start()].strip()
        return name, diff
    return desc, None


# ── Interpolate between markers to densify the centerline ────────────────────

def densify(markers, n_between=4):
    """Add n_between evenly-spaced lat/lon points between each consecutive marker pair."""
    if len(markers) < 2:
        return markers
    out = []
    for i in range(len(markers) - 1):
        lat1, lon1, m1 = markers[i]["lat"], markers[i]["lon"], markers[i]["mile"]
        lat2, lon2, m2 = markers[i + 1]["lat"], markers[i + 1]["lon"], markers[i + 1]["mile"]
        out.append({"lat": lat1, "lon": lon1, "mile": m1})
        for k in range(1, n_between + 1):
            t = k / (n_between + 1)
            out.append({
                "lat":  lat1 + t * (lat2 - lat1),
                "lon":  lon1 + t * (lon2 - lon1),
                "mile": m1   + t * (m2   - m1),
            })
    out.append({"lat": markers[-1]["lat"], "lon": markers[-1]["lon"], "mile": markers[-1]["mile"]})
    return out


# ── Build centerline GeoJSON ──────────────────────────────────────────────────

def build_centerline(nodes, river_name, total_miles, source):
    coords = [[round(n["lon"], 7), round(n["lat"], 7)] for n in nodes]
    vertex_miles = [round(n["mile"], 3) for n in nodes]
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "name": river_name,
                "source": source,
                "vertex_miles": vertex_miles,
                "total_miles": total_miles,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
        }],
    }


# ── Assign POI to nearest river and return its river mile ────────────────────

def nearest_in(lat, lon, markers):
    best_d, best_m = float("inf"), 0.0
    for mk in markers:
        d = hav_m(lat, lon, mk["lat"], mk["lon"])
        if d < best_d:
            best_d = d
            best_m = mk["mile"]
    return best_d, best_m


# ── Build POI GeoJSON for one river ──────────────────────────────────────────

def build_pois_geojson(pois):
    return {
        "type": "FeatureCollection",
        "features": pois,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not GPX_PATH.exists():
        sys.exit(f"GPX not found: {GPX_PATH}")

    print(f"Loading {GPX_PATH.name}…")
    all_wpts = load_gpx(GPX_PATH)
    print(f"  {len(all_wpts)} waypoints")

    # ── Mileage markers for each river ───────────────────────────────────────
    mf_markers_raw   = [w for w in all_wpts if re.match(r"^M-\d{3}-MF$", w["name"])]
    main_markers_raw = [w for w in all_wpts if re.match(r"^M-\d{3}$",    w["name"])]

    def extract_mile(name, suffix=""):
        return int(name.replace("M-", "").replace(suffix, ""))

    mf_markers = sorted([
        {"lat": w["lat"], "lon": w["lon"], "mile": extract_mile(w["name"], "-MF")}
        for w in mf_markers_raw
    ], key=lambda x: x["mile"])

    main_markers = sorted([
        {"lat": w["lat"], "lon": w["lon"], "mile": extract_mile(w["name"])}
        for w in main_markers_raw
    ], key=lambda x: x["mile"])

    print(f"  Middle Fork markers: {len(mf_markers)}  (mi {mf_markers[0]['mile']}–{mf_markers[-1]['mile']})")
    print(f"  Main Salmon markers: {len(main_markers)} (mi {main_markers[0]['mile']}–{main_markers[-1]['mile']})")

    # ── Densify centerlines ────────────────────────────────────────────────────
    mf_nodes   = densify(mf_markers,   n_between=5)
    main_nodes = densify(main_markers, n_between=5)
    print(f"  MF densified: {len(mf_nodes)} nodes")
    print(f"  Main densified: {len(main_nodes)} nodes")

    # ── Build centerline GeoJSONs ──────────────────────────────────────────────
    mf_cl   = build_centerline(mf_nodes,   "Middle Fork of the Salmon River", 103.0, "RiverMaps 5th Ed. mileage markers")
    main_cl = build_centerline(main_nodes, "Main Salmon River",               80.0,  "RiverMaps 5th Ed. mileage markers")

    # ── Assign POIs to rivers ─────────────────────────────────────────────────
    mf_pois, main_pois = [], []

    for w in all_wpts:
        n = w["name"]
        prefix = n[0] if n else ""

        if prefix == "M":  # skip mileage markers
            continue
        if prefix not in ("R", "C", "P"):  # skip unknown
            continue

        lat, lon = w["lat"], w["lon"]
        mf_d,   mf_mile   = nearest_in(lat, lon, mf_markers)
        main_d, main_mile = nearest_in(lat, lon, main_markers)

        if prefix == "R":
            display_name, diff = parse_rapid(w["desc"])
            props = {
                "type":        "rapid",
                "name":        display_name or n,
                "description": w["desc"],
                "difficulty":  diff,
            }
        elif prefix == "C":
            props = {
                "type":        "camp",
                "name":        w["desc"] or n,
                "description": "",
            }
        else:  # P
            props = {
                "type":        "poi",
                "name":        w["desc"] or n,
                "description": "",
            }
            # Classify known access/launch points as 'access'
            desc_lower = (w["desc"] or n).lower()
            if any(kw in desc_lower for kw in ("boat launch", "boat ramp", "ramp", "ranger station", "put-in", "take-out", "takeout")):
                props["type"] = "access"

        feat = {
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]},
        }

        # Assign to closer river; apply mile
        if mf_d <= main_d:
            feat["properties"]["mile"] = round(mf_mile, 1)
            mf_pois.append(feat)
        else:
            feat["properties"]["mile"] = round(main_mile, 1)
            main_pois.append(feat)

    print(f"\n  Middle Fork POIs: {len(mf_pois)}  ({sum(1 for p in mf_pois if p['properties']['type']=='rapid')} rapids, {sum(1 for p in mf_pois if p['properties']['type']=='camp')} camps)")
    print(f"  Main Salmon POIs: {len(main_pois)} ({sum(1 for p in main_pois if p['properties']['type']=='rapid')} rapids, {sum(1 for p in main_pois if p['properties']['type']=='camp')} camps)")

    # ── Save ───────────────────────────────────────────────────────────────────
    OUT_MF.mkdir(parents=True, exist_ok=True)
    OUT_MAIN.mkdir(parents=True, exist_ok=True)

    (OUT_MF / "river_centerline.geojson").write_text(json.dumps(mf_cl, indent=2))
    (OUT_MF / "pois.geojson").write_text(json.dumps(build_pois_geojson(mf_pois), indent=2))

    (OUT_MAIN / "river_centerline.geojson").write_text(json.dumps(main_cl, indent=2))
    (OUT_MAIN / "pois.geojson").write_text(json.dumps(build_pois_geojson(main_pois), indent=2))

    mf_nodes_count   = len(mf_cl["features"][0]["geometry"]["coordinates"])
    main_nodes_count = len(main_cl["features"][0]["geometry"]["coordinates"])

    print(f"\n✓ Middle Fork → {OUT_MF}/")
    print(f"    river_centerline.geojson ({mf_nodes_count} nodes)")
    print(f"    pois.geojson ({len(mf_pois)} features)")
    print(f"\n✓ Main Salmon → {OUT_MAIN}/")
    print(f"    river_centerline.geojson ({main_nodes_count} nodes)")
    print(f"    pois.geojson ({len(main_pois)} features)")


if __name__ == "__main__":
    main()
