"""Parks and green spaces from OpenStreetMap, via the Overpass API.

Returns shapely polygons with a category and name. Data © OpenStreetMap
contributors (ODbL).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests
from shapely import MultiPolygon, Polygon, box, make_valid
from shapely.ops import linemerge, polygonize, unary_union
from shapely.geometry import LineString

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# category -> (label, list of (key, value regex))
CATEGORIES = {
    "allotments": ("Allotment / community garden", [("landuse", "allotments")]),
    "garden": ("Garden", [("leisure", "garden")]),
    "park": ("Park", [("leisure", "park"), ("landuse", "recreation_ground|village_green")]),
    "golf": ("Golf course (irrigated turf)", [("leisure", "golf_course")]),
    "protected": ("Protected natural area", [("boundary", "protected_area"), ("leisure", "nature_reserve")]),
}


def _query(south: float, west: float, north: float, east: float, category: str) -> str:
    bbox = f"({south},{west},{north},{east})"
    parts = [f'  nwr["{key}"~"^({values})$"]{bbox};' for key, values in CATEGORIES[category][1]]
    return "[out:json][timeout:240];\n(\n" + "\n".join(parts) + "\n);\nout geom qt;"


def _category(tags: dict) -> str | None:
    for cat, (_, rules) in CATEGORIES.items():
        for key, values in rules:
            if tags.get(key) and tags[key] in values.split("|"):
                return cat
    return None


def _fetch(query: str, rounds: int = 4) -> list[dict]:
    last = None
    for attempt in range(rounds):
        for server in OVERPASS_SERVERS:
            try:
                resp = requests.post(server, data={"data": query}, timeout=300,
                                     headers={"User-Agent": "garden-water-atlas/0.1"})
                if resp.status_code == 200:
                    return resp.json()["elements"]
                last = f"HTTP {resp.status_code} from {server}"
            except (requests.RequestException, ValueError) as exc:
                last = f"{server}: {exc}"
            time.sleep(5)
        time.sleep(30 * (attempt + 1))  # busy servers: back off before the next round
    raise RuntimeError(f"All Overpass servers failed ({last})")


def _elements(south, west, north, east, cache_dir: Path | None) -> list[dict]:
    """Raw Overpass elements for every category, cached one file per category."""
    elements = []
    for category in CATEGORIES:
        path = cache_dir / f"osm_{category}.json" if cache_dir else None
        if path and path.exists():
            elements.extend(json.loads(path.read_text()))
            continue
        batch = _fetch(_query(south, west, north, east, category))
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(batch))
        print(f"  OpenStreetMap {category}: {len(batch)} elements", flush=True)
        elements.extend(batch)
    return elements


def _way_polygon(element: dict) -> Polygon | None:
    pts = [(p["lon"], p["lat"]) for p in element.get("geometry", []) if p]
    if len(pts) < 4 or pts[0] != pts[-1]:
        return None
    return Polygon(pts)


def _relation_polygon(element: dict):
    outer, inner = [], []
    for member in element.get("members", []):
        geometry = member.get("geometry")
        if member.get("type") != "way" or not geometry:
            continue
        line = LineString([(p["lon"], p["lat"]) for p in geometry if p])
        (inner if member.get("role") == "inner" else outer).append(line)
    if not outer:
        return None
    shell = unary_union(list(polygonize(linemerge(outer))))
    if shell.is_empty:
        return None
    if inner:
        holes = unary_union(list(polygonize(linemerge(inner))))
        shell = shell.difference(holes)
    return shell


def green_areas(south: float, west: float, north: float, east: float, cache_dir: Path | None = None) -> list[dict]:
    """List of {id, osm, category, name, geometry} clipped to the box. Raw
    answers are cached in ``cache_dir`` (delete the files to refresh)."""
    clip = box(west, south, east, north)
    out, seen = [], set()
    for e in _elements(south, west, north, east, cache_dir):
        if (e["type"], e["id"]) in seen:
            continue
        seen.add((e["type"], e["id"]))
        tags = e.get("tags", {})
        cat = _category(tags)
        if cat is None or e["type"] == "node":
            continue
        geom = _way_polygon(e) if e["type"] == "way" else _relation_polygon(e)
        if geom is None or geom.is_empty:
            continue
        geom = make_valid(geom).intersection(clip)
        if geom.is_empty or geom.area == 0:
            continue
        if not isinstance(geom, (Polygon, MultiPolygon)):
            polys = [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]
            if not polys:
                continue
            geom = MultiPolygon(polys)
        out.append({"id": f"{e['type'][0]}{e['id']}", "osm": f"{e['type']}/{e['id']}", "category": cat,
                    "name": tags.get("name") or tags.get("name:es") or tags.get("name:eu"), "geometry": geom})
    return out
