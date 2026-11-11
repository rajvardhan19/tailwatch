"""Regenerate src/tailwatch/static/land.json from Natural Earth land polygons.

Usage (download into its own empty directory first):
    python3 -I scripts/land_dots.py ne_110m_land.geojson src/tailwatch/static/land.json 1.8

Source: https://github.com/nvkelso/natural-earth-vector (geojson/ne_110m_land.geojson),
public domain. Output: an equal-area dot sample used only to draw the dashboard globe.
"""

import json
import math
import sys

source, target, step = sys.argv[1], sys.argv[2], float(sys.argv[3])
features = json.load(open(source))["features"]
polygons = []  # (bbox, rings) with rings[0] outer, others holes
for feature in features:
    geometry = feature["geometry"]
    coordinates = geometry["coordinates"]
    parts = coordinates if geometry["type"] == "MultiPolygon" else [coordinates]
    for rings in parts:
        xs = [p[0] for p in rings[0]]
        ys = [p[1] for p in rings[0]]
        polygons.append(((min(xs), min(ys), max(xs), max(ys)), rings))


def inside(ring, x, y):
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


points = []
lat = -90 + step / 2
while lat < 90:
    count = max(1, round(360 * math.cos(math.radians(lat)) / step))
    for i in range(count):
        lon = -180 + (i + 0.5) * 360 / count
        for (x0, y0, x1, y1), rings in polygons:
            if (
                x0 <= lon <= x1
                and y0 <= lat <= y1
                and inside(rings[0], lon, lat)
                and not any(inside(hole, lon, lat) for hole in rings[1:])
            ):
                points += [round(lon, 1), round(lat, 1)]
                break
    lat += step
json.dump(
    {
        "source": "Natural Earth 1:110m land polygons (public domain), naturalearthdata.com",
        "derivation": (
            f"equal-area dot sample, {step} degree spacing, coordinates rounded to 0.1 degree"
        ),
        "points": points,
    },
    open(target, "w"),
    separators=(",", ":"),
)
print(len(points) // 2, "dots")
