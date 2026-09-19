"""Crime baseline from 12 months of MPS recorded crime per LSOA.

Data: "MPS Recorded Crime: Geographic Breakdown" on the London Datastore (OGL v2),
the LSOA file covering the most recent 24 months, and the ONS LSOA 2021
boundaries. Nothing is cached on disk.

Method (build):

1. W = weighted count over the last 12 months of the offences in OFFENCE_WEIGHT,
   per LSOA. The City of London is policed by a separate force and is nearly
   empty in the MPS file; its LSOAs take 12 x the police.uk month instead
   (POLICE_UK_FALLBACK_WEIGHT).
2. L = kilometres of walkable street in the LSOA, from the routing graph: every
   undirected edge is assigned to the LSOA containing its midpoint. The rate of
   an LSOA is W / 12 / L, in weighted crimes per street-km per month.
   Approximation: an LSOA that the graph does not cover (not fully inside the
   graph bbox, under MIN_STREET_KM of edges, or a street density under
   MIN_DENSITY_RATIO x D_med, which in the inner-London graph is missing graph
   data, e.g. 0.6 km for the 1 km2 of the Greenwich Peninsula, not a real street
   network), and every LSOA when there is no graph, takes L = area x D_med, where D_med is the median street density
   (km per km2) of the covered LSOAs. Its rate is then the areal crime density
   divided by one London-wide constant, which puts both regimes on one scale but
   ignores the LSOA's own street density.
3. The consumers of the street points (routing.edge_baseline, the client
   heatmap) sum point weights inside a radius, which measures weight per unit of
   area. The points of an LSOA therefore sum to

       S = rate x D_med x area = (W / 12) x (D_med x area / L)

   so that weight per km2 is rate x D_med: proportional to the per-street-km
   rate, and equal to the plain monthly areal density for an LSOA of median
   street density (and for every LSOA in the area regime, where S = W / 12).
   The unit of `weighted` stays "weighted crimes per month".
   S is then reduced by the share of the LSOA's police.uk records that sit on
   recording venues (RECORDING_VENUES: hospital, police station, prison). The
   MPS totals include those records, and without the reduction they would be
   moved onto the streets around the venue. The share comes from one month.
4. Within an LSOA, S is split over its police.uk street points (one month,
   relevant categories, venue points removed) with
   share = (count + k) / (sum + k x n), k = SHRINK_K. An LSOA without points gets
   rows of equal weight at the centres of the H3 res-10 cells (about 130 m apart)
   inside the polygon, labelled with the LSOA name: a uniform density, because
   nothing is known about the distribution inside it.
"""

from __future__ import annotations

import csv
import re
import statistics
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import h3
import httpx
import numpy as np
import shapely
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from ..models import LONDON_BBOX
from . import police_uk
from .police_uk import CrimePoint

DATASET_API = "https://data.london.gov.uk/api/dataset/exy3m"
# Generalised to 20 m (BGC), 4.8 MB for the London bbox. The 200 m version (BSC,
# 1.7 MB) puts 16 % of police.uk points and 18 % of graph edges in a different
# LSOA than BGC does, which is too coarse for LSOAs a few hundred metres across.
BOUNDARY_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "Lower_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V5/FeatureServer/0/query"
)
BOUNDARY_PAGE = 2000
MONTHS = 12

# REVISIT(crime-weights): MPS minor categories (the SubGroup column, upper case in
# the file) that describe a risk to a person walking on the street.
OFFENCE_WEIGHT = {
    "HOMICIDE": 1.0,
    "VIOLENCE WITH INJURY": 1.0,
    "ROBBERY OF PERSONAL PROPERTY": 1.0,
    "POSSESSION OF WEAPONS": 1.0,
    "VIOLENT DISORDER": 1.0,
    "THEFT FROM THE PERSON": 0.7,
    "PUBLIC FEAR, ALARM OR DISTRESS": 0.5,
    "RACE OR RELIGIOUS AGG PUBLIC FEAR": 0.5,
}

# REVISIT(crime-weights): used only for the City of London. police.uk categories
# are broader than the MPS ones: about a quarter of "violent-crime" is violence
# with injury (MPS, 12 months: 64,995 of 254,082 violence against the person),
# and "public-order" includes offences not counted above.
POLICE_UK_FALLBACK_WEIGHT = {
    "violent-crime": 0.25,
    "robbery": 1.0,
    "possession-of-weapons": 1.0,
    "theft-from-the-person": 0.7,
    "public-order": 0.4,
}
CITY_OF_LONDON_PREFIX = "City of London "

MIN_STREET_KM = 0.5
MIN_DENSITY_RATIO = 0.25
# Subset of police_uk.VENUE_LABELS where the record is an artefact of where the
# crime was reported or the suspect was held. Records at the other venue labels
# (supermarket, shopping area, ...) are offences in a public place: their points
# are dropped, but their share of the LSOA total stays on the LSOA's streets.
RECORDING_VENUES = frozenset({"hospital", "police station", "prison"})
SHRINK_K = 1.0
FILL_RES = 10

# Same equirectangular projection as scoring.py and routing.py
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LNG = _M_PER_DEG_LAT * float(np.cos(np.radians(51.5)))


@dataclass
class Lsoa:
    code: str
    name: str
    polygon: BaseGeometry
    weighted_12m: float = 0.0
    count_12m: int = 0
    area_km2: float = 0.0
    graph_km: float = 0.0  # street length the graph has inside the polygon
    street_km: float = 0.0  # denominator: graph_km, or area x D_med
    from_graph: bool = False
    rate_per_km: float = 0.0  # weighted crimes per street-km per month
    point_weight: float = 0.0  # S: what the LSOA's street points sum to


@dataclass
class Baseline:
    month: str  # police.uk month of the street points
    period: str  # MPS months summed, "YYYY-MM..YYYY-MM"
    lsoas: dict[str, Lsoa]
    points: list[CrimePoint]
    median_street_density: float | None  # km per km2; None without a graph
    stats: dict[str, Any]

    def cells(self, res: int) -> dict[str, float]:
        """H3 baseline in [0, 1], summed from the same street-point rows that
        routing.edge_baseline reads, so the cell layer and the routes agree. The
        rows of an LSOA already sum to its value, and an LSOA without street
        points is filled on a grid finer than the cells, so no area is skipped."""
        return police_uk.baseline_cells(self.points, months=1, res=res)

    def payload(self) -> dict[str, Any]:
        return police_uk.points_payload(self.points, self.month, {
            "period": self.period,
            "method": (
                f"MPS recorded crime per LSOA, {self.period}, offences against a person on the street,"
                f" per km of walkable street; placed on police.uk {self.month} street points"
            ),
        })


# --- MPS counts ---


def resolve_csv_url(client: httpx.Client) -> str:
    """URL of the newest "LSOA Level Crime (most recent 24 months)" resource. The
    path segment changes with every monthly release."""
    resp = client.get(DATASET_API, follow_redirects=True)  # 307 to /api/v2/
    resp.raise_for_status()
    found = [
        r for r in resp.json()["resources"].values()
        if "lsoa" in r.get("title", "").lower()
        and "historical" not in r.get("title", "").lower()
        and r.get("format") == "csv"
    ]
    if not found:
        raise RuntimeError("no LSOA CSV among the resources of the MPS dataset")
    newest = max(found, key=lambda r: (r.get("temporal_coverage_to") or "", r.get("check_timestamp") or ""))
    return newest["url"]


def parse_counts(lines: Iterable[str], months: int = MONTHS) -> tuple[str, dict[str, tuple[str, float, int]]]:
    """(period, {LSOA code: (name, weighted count, count)}) over the last `months`
    month columns. Columns: LSOA Code, LSOA Name, Borough, Group, SubGroup, then
    one column per month named YYYYMM."""
    reader = csv.reader(lines)
    header = [h.strip().lstrip("\ufeff") for h in next(reader)]
    code_i, name_i, sub_i = header.index("LSOA Code"), header.index("LSOA Name"), header.index("SubGroup")
    month_cols = sorted((h, i) for i, h in enumerate(header) if re.fullmatch(r"\d{6}", h))
    if len(month_cols) < months:
        raise ValueError(f"{len(month_cols)} month columns, {months} needed")
    window = month_cols[-months:]
    first, last = window[0][0], window[-1][0]
    period = f"{first[:4]}-{first[4:]}..{last[:4]}-{last[4:]}"

    out: dict[str, tuple[str, float, int]] = {}
    for row in reader:
        if not row:
            continue
        # Every LSOA of the file gets an entry, also one without a relevant offence
        name, weighted, count = out.get(row[code_i], (row[name_i], 0.0, 0))
        weight = OFFENCE_WEIGHT.get(row[sub_i].strip().upper())
        if weight is not None:
            n = sum(int(row[i] or 0) for _, i in window)
            weighted, count = weighted + weight * n, count + n
        out[row[code_i]] = (name, weighted, count)
    return period, out


def fetch_counts(client: httpx.Client) -> tuple[str, dict[str, tuple[str, float, int]]]:
    with client.stream("GET", resolve_csv_url(client), follow_redirects=True) as resp:
        resp.raise_for_status()
        return parse_counts(resp.iter_lines())


# --- boundaries ---


def _boundary_pages(client: httpx.Client) -> Iterator[list[dict[str, Any]]]:
    offset = 0
    while True:
        resp = client.get(BOUNDARY_URL, params={
            "where": "1=1",
            "geometry": ",".join(str(v) for v in LONDON_BBOX),
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "LSOA21CD",
            "outSR": 4326,
            "geometryPrecision": 5,
            "f": "geojson",
            "resultRecordCount": BOUNDARY_PAGE,
            "resultOffset": offset,
        })
        resp.raise_for_status()
        body = resp.json()
        if "features" not in body:  # ArcGIS reports errors with status 200
            raise RuntimeError(f"LSOA boundary query failed: {str(body)[:200]}")
        yield body["features"]
        if not body["features"] or not (body.get("properties") or {}).get("exceededTransferLimit"):
            return
        offset += len(body["features"])


def fetch_boundaries(client: httpx.Client, codes: set[str]) -> dict[str, BaseGeometry]:
    """Polygons (WGS84) of the LSOAs in `codes`. The bbox query also returns
    LSOAs outside London, which the join on the MPS codes removes."""
    out: dict[str, BaseGeometry] = {}
    for page in _boundary_pages(client):
        for f in page:
            code = f["properties"]["LSOA21CD"]
            if code in codes:
                out[code] = shapely.make_valid(shape(f["geometry"]))
    return out


def make_lsoas(counts: dict[str, tuple[str, float, int]], polygons: dict[str, BaseGeometry]) -> dict[str, Lsoa]:
    """LSOAs present in both inputs, with areas."""
    out = {}
    for code, (name, weighted, count) in counts.items():
        poly = polygons.get(code)
        if poly is None:
            continue
        metric = shapely.transform(poly, lambda xy: xy * [_M_PER_DEG_LNG, _M_PER_DEG_LAT])
        out[code] = Lsoa(code, name, poly, weighted, count, area_km2=metric.area / 1e6)
    return out


# --- normalisation ---


def _locate(lsoas: list[Lsoa], lng: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Index into `lsoas` of the polygon containing each position, -1 for none.
    A position on a shared boundary goes to one of the two."""
    found = np.full(len(lng), -1, dtype=np.int64)
    if lsoas and len(lng):
        tree = shapely.STRtree([a.polygon for a in lsoas])
        at, poly = tree.query(shapely.points(lng, lat), predicate="intersects")
        found[at] = poly
    return found


def street_km(lsoas: dict[str, Lsoa], graph: Any | None) -> dict[str, float]:
    """Kilometres of graph edges per LSOA code. An undirected edge counts once,
    in the LSOA that contains the midpoint of its end nodes. `graph` is a
    routing.Graph (node_lng, node_lat, edge_src, edge_dst, edge_length are read)."""
    if graph is None or not lsoas:
        return {}
    m = len(graph.edge_src) // 2
    src, dst = graph.edge_src[:m], graph.edge_dst[:m]
    lng = (graph.node_lng[src].astype(np.float64) + graph.node_lng[dst]) / 2
    lat = (graph.node_lat[src].astype(np.float64) + graph.node_lat[dst]) / 2
    ordered = list(lsoas.values())
    at = _locate(ordered, lng, lat)
    inside = at >= 0
    km = np.bincount(at[inside], weights=graph.edge_length[:m][inside].astype(np.float64), minlength=len(ordered)) / 1000
    return {a.code: float(v) for a, v in zip(ordered, km)}


def normalise(lsoas: dict[str, Lsoa], graph: Any | None) -> float | None:
    """Fill street_km, rate_per_km and point_weight of every LSOA (module
    docstring, steps 2 and 3). Returns D_med, None when no LSOA is covered."""
    km = street_km(lsoas, graph)
    bbox = getattr(graph, "bbox", None)
    for a in lsoas.values():
        a.graph_km = km.get(a.code, 0.0)
        w, s, e, n = a.polygon.bounds
        # An LSOA cut by the edge of the graph has only part of its streets in it
        whole = bbox is None or (bbox[0] <= w and bbox[1] <= s and e <= bbox[2] and n <= bbox[3])
        a.from_graph = whole and a.graph_km >= MIN_STREET_KM
    covered = [a.graph_km / a.area_km2 for a in lsoas.values() if a.from_graph]
    d_med = statistics.median(covered) if covered else None
    if d_med is not None:
        # The median is taken before this exclusion; the few LSOAs it removes do not move it
        for a in lsoas.values():
            a.from_graph = a.from_graph and a.graph_km / a.area_km2 >= MIN_DENSITY_RATIO * d_med
    for a in lsoas.values():
        monthly = a.weighted_12m / MONTHS
        if a.from_graph and d_med is not None:
            a.street_km = a.graph_km
            a.rate_per_km = monthly / a.street_km
            a.point_weight = a.rate_per_km * d_med * a.area_km2
        else:
            # Area regime: L = area x D_med, so S = monthly. Without any graph
            # D_med is unknown and the per-km rate is not available.
            a.street_km = a.area_km2 * d_med if d_med is not None else 0.0
            a.rate_per_km = monthly / a.street_km if a.street_km else 0.0
            a.point_weight = monthly
    return d_med


# --- street points ---


def _fill_positions(polygon: BaseGeometry) -> list[tuple[float, float]]:
    """Centres of the H3 cells (FILL_RES) whose centre is inside the polygon; one
    position inside the polygon when it is too small to contain a cell centre."""
    cells = h3.geo_to_cells(polygon, FILL_RES)
    if not cells:
        pos = polygon.representative_point()
        return [(round(pos.x, 6), round(pos.y, 6))]
    return [(round(lng, 6), round(lat, 6)) for lat, lng in (h3.cell_to_latlng(c) for c in sorted(cells))]


def distribute(lsoas: dict[str, Lsoa], points: list[CrimePoint], k: float = SHRINK_K) -> list[CrimePoint]:
    """Set `weighted` of the street points so that the points of each LSOA sum to
    its point_weight. The share of a point is (count + k) / (sum + k x n): with
    k = 0 proportional to the one month of counts, with large k uniform. Points
    outside every LSOA are dropped (the MPS data covers London only). An LSOA
    without points gets rows of equal weight on a grid inside its polygon
    (_fill_positions), with count 0."""
    ordered = list(lsoas.values())
    at = _locate(ordered, np.array([p.lng for p in points]), np.array([p.lat for p in points]))
    members: dict[int, list[CrimePoint]] = {}
    for p, i in zip(points, at):
        if i >= 0:
            members.setdefault(int(i), []).append(p)
    out: list[CrimePoint] = []
    for i, a in enumerate(ordered):
        inside = members.get(i)
        if not inside:
            if a.point_weight > 0:
                positions = _fill_positions(a.polygon)
                out.extend(
                    CrimePoint(lng, lat, f"LSOA {a.name}", weighted=a.point_weight / len(positions))
                    for lng, lat in positions
                )
            continue
        denominator = sum(p.count for p in inside) + k * len(inside)
        for p in inside:
            p.weighted = a.point_weight * (p.count + k) / denominator
        out.extend(inside)
    return out


def subtract_recording_venues(lsoas: dict[str, Lsoa], crimes: list[dict[str, Any]]) -> float:
    """Multiply point_weight by 1 - venue / (all + SHRINK_K): the LSOA's records at
    RECORDING_VENUES and all its records, over the relevant police.uk categories
    of the month. SHRINK_K keeps an LSOA with very few records from being removed
    entirely. Returns the total point weight removed."""
    records = [c for c in crimes if c["category"] in police_uk.RELEVANT_CATEGORIES and c.get("location")]
    ordered = list(lsoas.values())
    at = _locate(
        ordered,
        np.array([float(c["location"]["longitude"]) for c in records]),
        np.array([float(c["location"]["latitude"]) for c in records]),
    )
    total = np.bincount(at[at >= 0], minlength=len(ordered))
    is_recording = np.array(
        [police_uk.venue_label(c["location"]["street"]["name"]) in RECORDING_VENUES for c in records], dtype=bool
    )
    venue = np.bincount(at[(at >= 0) & is_recording], minlength=len(ordered))
    removed = 0.0
    for a, n, v in zip(ordered, total, venue):
        if v:
            share = v / (n + SHRINK_K)
            removed += a.point_weight * share
            a.point_weight *= 1 - share
    return removed


def add_police_uk_counts(lsoas: dict[str, Lsoa], crimes: list[dict[str, Any]]) -> int:
    """City of London LSOAs: weighted_12m = 12 x the police.uk month, since the
    MPS file has almost no records there. Venue points are included, as they are
    in the MPS totals of the other LSOAs. Returns the number of LSOAs changed."""
    city = [a for a in lsoas.values() if a.name.startswith(CITY_OF_LONDON_PREFIX)]
    records = [
        c for c in crimes
        if c["category"] in POLICE_UK_FALLBACK_WEIGHT and c.get("location")
    ]
    if not city or not records:
        return 0
    at = _locate(
        city,
        np.array([float(c["location"]["longitude"]) for c in records]),
        np.array([float(c["location"]["latitude"]) for c in records]),
    )
    for a in city:
        a.weighted_12m, a.count_12m = 0.0, 0
    for c, i in zip(records, at):
        if i >= 0:
            city[i].weighted_12m += MONTHS * POLICE_UK_FALLBACK_WEIGHT[c["category"]]
            city[i].count_12m += MONTHS
    return len(city)


def combine(
    period: str,
    lsoas: dict[str, Lsoa],
    month: str,
    crimes: list[dict[str, Any]],
    graph: Any | None,
) -> Baseline:
    """Steps 1 to 4 on fetched inputs. No network access."""
    city = add_police_uk_counts(lsoas, crimes)
    d_med = normalise(lsoas, graph)
    removed = subtract_recording_venues(lsoas, crimes)
    street_points = police_uk.aggregate_points(crimes)
    points = distribute(lsoas, street_points)
    stats = {
        "lsoas": len(lsoas),
        "lsoas_from_graph": sum(a.from_graph for a in lsoas.values()),
        "lsoas_from_police_uk": city,
        "recording_venue_weight_removed": round(float(removed), 1),
        "crimes": len(crimes),
        "street_points": len(street_points),
        "rows": len(points),
        "rows_without_street_point": sum(p.count == 0 for p in points),
    }
    return Baseline(month, period, lsoas, points, d_med, stats)


def build(graph: Any | None, month: str | None = None) -> Baseline:
    """Fetch everything and build the baseline. `month` is the police.uk month of
    the street points (default: latest published)."""
    with httpx.Client(timeout=120) as client:
        period, counts = fetch_counts(client)
        polygons = fetch_boundaries(client, set(counts))
    resolved, crimes = police_uk.fetch_month(month)
    return combine(period, make_lsoas(counts, polygons), resolved, crimes, graph)


def load_graph_if_present(path: str) -> Any | None:
    """The routing graph at `path`, or None (area regime for every LSOA)."""
    from pathlib import Path

    from .. import routing

    return routing.load_graph(path) if Path(path).is_file() else None
