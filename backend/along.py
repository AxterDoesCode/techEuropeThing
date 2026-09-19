"""Events near a route line. Pure functions on GeoJSON; no I/O."""

from __future__ import annotations

from typing import Any

import shapely
from shapely.geometry import Point, shape

from .scoring import _M_PER_DEG_LAT, _M_PER_DEG_LNG, _to_metres

DEFAULT_BUFFER_M = 60.0


def events_along(route_geometry: dict[str, Any], features: list[dict[str, Any]], buffer_m: float = DEFAULT_BUFFER_M) -> list[dict[str, Any]]:
    """Event features whose footprint comes within `buffer_m` of the route line,
    ordered by position along the route.

    The footprint of a Point event is the disc of `properties.radius_m` around it;
    a line or polygon event is its own footprint. Each result is
    `{"feature", "distance_m", "along_m"}`: `distance_m` is the distance from the
    line to the event geometry (the centre of a Point event; 0 when the geometry
    touches the line), `along_m` the distance from the start of the route to the
    point of the route closest to the event geometry (the first point of contact
    when they intersect). Features are not modified. Features without a usable
    geometry are skipped.
    """
    line = _to_metres(shape(route_geometry))
    if line.is_empty:
        return []
    found = []
    for feature in features:
        try:
            geom = _to_metres(shape(feature["geometry"]))
        except (KeyError, TypeError, ValueError, AttributeError, shapely.errors.ShapelyError):
            continue
        if geom.is_empty:
            continue
        distance = line.distance(geom)
        radius = float(feature.get("properties", {}).get("radius_m") or 0.0) if geom.geom_type == "Point" else 0.0
        if distance - radius > buffer_m:
            continue
        if distance == 0:
            contact = shapely.get_coordinates(line.intersection(geom))
            along = min((line.project(Point(xy)) for xy in contact), default=0.0)
        else:
            along = line.project(shapely.shortest_line(line, geom).interpolate(0))
        found.append({"feature": feature, "distance_m": round(distance, 1), "along_m": round(along, 1)})
    found.sort(key=lambda r: (r["along_m"], r["distance_m"]))
    return found


def padded_bbox(geometry: dict[str, Any], pad_m: float) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a GeoJSON geometry, enlarged by pad_m on every side."""
    w, s, e, n = shape(geometry).bounds
    dx, dy = pad_m / _M_PER_DEG_LNG, pad_m / _M_PER_DEG_LAT
    return w - dx, s - dy, e + dx, n + dy


def circle_bbox(lng: float, lat: float, radius_m: float) -> tuple[float, float, float, float]:
    """(west, south, east, north) of the circle of radius_m around a point."""
    dx, dy = radius_m / _M_PER_DEG_LNG, radius_m / _M_PER_DEG_LAT
    return lng - dx, lat - dy, lng + dx, lat + dy
