"""Terrain heights from the open Terrarium tiles (Mapzen / AWS Open Data).

Tiles are Web Mercator PNGs where height = R*256 + G + B/256 - 32768 metres.
The stitched raster stays in Web Mercator, so it lines up pixel for pixel with
web map tiles; rows run north to south.
"""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image

from . import utils

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TILE = 256


def _pixel(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    n = TILE * 2**zoom
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def _latlon(x: float, y: float, zoom: int) -> tuple[float, float]:
    n = TILE * 2**zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def elevation_raster(west: float, south: float, east: float, north: float, zoom: int = 11):
    """Return (heights int16 [rows, cols], bounds dict) covering the box.
    Bounds are the exact edges of the cropped raster, in degrees."""
    x0, y0 = _pixel(north, west, zoom)
    x1, y1 = _pixel(south, east, zoom)
    px0, py0, px1, py1 = math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1)
    tx0, ty0, tx1, ty1 = px0 // TILE, py0 // TILE, (px1 - 1) // TILE, (py1 - 1) // TILE
    mosaic = np.zeros(((ty1 - ty0 + 1) * TILE, (tx1 - tx0 + 1) * TILE), dtype=np.float32)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            resp = utils.get(TILE_URL.format(z=zoom, x=tx, y=ty))
            resp.raise_for_status()
            rgb = np.asarray(Image.open(io.BytesIO(resp.content)).convert("RGB"), dtype=np.float32)
            height = rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768
            mosaic[(ty - ty0) * TILE:(ty - ty0 + 1) * TILE, (tx - tx0) * TILE:(tx - tx0 + 1) * TILE] = height
    crop = mosaic[py0 - ty0 * TILE:py1 - ty0 * TILE, px0 - tx0 * TILE:px1 - tx0 * TILE]
    north_edge, west_edge = _latlon(px0, py0, zoom)
    south_edge, east_edge = _latlon(px1, py1, zoom)
    metres_per_pixel = 40075016.686 * math.cos(math.radians((north + south) / 2)) / (TILE * 2**zoom)
    bounds = {"west": west_edge, "east": east_edge, "north": north_edge, "south": south_edge,
              "width": int(crop.shape[1]), "height": int(crop.shape[0]), "metres_per_pixel": metres_per_pixel}
    return np.clip(np.rint(crop), -32768, 32767).astype("<i2"), bounds
