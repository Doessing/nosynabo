"""
Address + cadastre client - replaces DAWA (shut down 2026-10-01).

Two upstreams, both server-side only:

* Dataforsyningen gsearch v2 (``DATAFORSYNINGEN_TOKEN``) - fuzzy text search
  for addresses and street names. Drives the search box.
* Datafordeler GraphQL ``flexibleCurrent`` (``DATAFORDELER_API_KEY``) - DAR
  (addresses) and MAT (Matriklen): matrikel <-> address joins, areas, SFE,
  parcel polygons and point-in-polygon for map clicks.

Neither credential may ever be committed; both are read from the environment.

Coordinates: both upstreams speak EPSG:25832. The browser speaks WGS84, so
everything leaving this module is converted to lat/lng (GeoJSON in 4326).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from math import hypot
from typing import Any

import requests
from cachetools import TTLCache, cached
from pyproj import Transformer

log = logging.getLogger(__name__)

GSEARCH_URL = "https://api.dataforsyningen.dk/rest/gsearch/v2.0"
GRAPHQL_URL = "https://graphql.datafordeler.dk/flexibleCurrent/v1"

_TIMEOUT = (3, 10)
_session = requests.Session()

_to_wgs84 = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
_to_utm = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)

# DAR status: 2 = foreløbig, 3 = gældende. 4/5 (nedlagt/henlagt) are history.
_LIVE_DAR = ("2", "3")


class DafoError(requests.RequestException):
    """Upstream failure (network, HTTP or GraphQL error, missing credential).

    Subclasses RequestException so existing ``except requests.RequestException``
    handlers in server.py / nosy_nabo.py treat it as an upstream outage.
    """


# ── transport ────────────────────────────────────────────────────────────────

def _env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise DafoError(f"{name} is not set")
    return val


def _get(url: str, **kw: Any) -> requests.Response:
    """GET/POST with one retry on stale keep-alive or timeout."""
    method = kw.pop("method", "GET")
    for attempt in range(2):
        try:
            resp = _session.request(method, url, timeout=_TIMEOUT, **kw)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 1:
                raise
    raise AssertionError("unreachable")


def _json(resp: requests.Response, what: str) -> Any:
    try:
        return resp.json()
    except ValueError:
        raise DafoError(f"{what}: invalid JSON response") from None


def gsearch(resource: str, q: str, limit: int = 10) -> list[dict]:
    try:
        resp = _get(
        f"{GSEARCH_URL}/{resource}",
        # gsearch rejects stray commas ("Vej , 1234") with HTTP 400 and
        # doesn't need them for matching; collapse to plain words.
            params={"q": " ".join(q.replace(",", " ").split()), "limit": limit, "token": _env("DATAFORSYNINGEN_TOKEN")},
        )
    except requests.HTTPError as e:
        # Never let the token in the URL leak into logs via the exception.
        status = e.response.status_code if e.response is not None else "?"
        raise DafoError(f"gsearch {resource} HTTP {status}") from None
    except DafoError:
        raise
    except requests.RequestException as e:
        raise DafoError(f"gsearch {resource} unreachable: {type(e).__name__}") from None
    return _json(resp, f"gsearch {resource}") or []


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _lit(value: Any) -> str:
    """Render a GraphQL string literal. json.dumps escaping is GraphQL-safe,
    so user input (matrikelnr etc.) can never break out of the literal."""
    return json.dumps(str(value), ensure_ascii=True)


def _list(values: list[Any]) -> str:
    return "[" + ",".join(_lit(v) for v in values) + "]"


def gql(body: str) -> dict:
    """Run a GraphQL query. ``body`` is the selection set inside ``query{}``;
    the literal ``$NOW`` is replaced with the current virkningstid."""
    query = "query{" + body.replace("$NOW", _lit(_now())) + "}"
    try:
        resp = _get(
            GRAPHQL_URL,
            method="POST",
            params={"apikey": _env("DATAFORDELER_API_KEY")},
            json={"query": query},
        )
    except requests.HTTPError as e:
        # Don't let the apikey in the URL leak into logs via the exception.
        status = e.response.status_code if e.response is not None else "?"
        raise DafoError(f"Datafordeler GraphQL HTTP {status}") from None
    except requests.RequestException as e:
        raise DafoError(f"Datafordeler GraphQL unreachable: {type(e).__name__}") from None
    data = _json(resp, "Datafordeler GraphQL")
    if data.get("errors"):
        msg = "; ".join(str(err.get("message")) for err in data["errors"])
        raise DafoError(f"Datafordeler GraphQL error: {msg}")
    return data["data"]


def _nodes(data: dict, key: str) -> list[dict]:
    return (data.get(key) or {}).get("nodes") or []


# ── geometry helpers ─────────────────────────────────────────────────────────

def to_latlng(x: float, y: float) -> tuple[float, float]:
    lng, lat = _to_wgs84.transform(x, y)
    return lat, lng


def to_utm(lat: float, lng: float) -> tuple[float, float]:
    return _to_utm.transform(lng, lat)


_PAIR_RE = re.compile(r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s+(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")


def wkt_to_geojson(wkt: str) -> dict:
    """Convert an EPSG:25832 (MULTI)POLYGON/POINT WKT to WGS84 GeoJSON."""
    kind = wkt.split("(", 1)[0].strip().upper().split()[0]
    body = wkt[wkt.index("("):]
    body = _PAIR_RE.sub(r"[\1,\2]", body).replace("(", "[").replace(")", "]")
    coords = json.loads(body)

    def conv(c: Any) -> Any:
        if isinstance(c[0], (int, float)):
            lat, lng = to_latlng(c[0], c[1])
            return [round(lng, 7), round(lat, 7)]
        return [conv(x) for x in c]

    types = {"POLYGON": "Polygon", "MULTIPOLYGON": "MultiPolygon", "POINT": "Point"}
    coords = conv(coords)
    if kind == "POINT":
        coords = coords[0]
    return {"type": types[kind], "coordinates": coords}


def _point_xy(wkt: str | None) -> tuple[float, float] | None:
    m = _PAIR_RE.search(wkt or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def _merge(geoms: list[dict]) -> dict | None:
    if not geoms:
        return None
    if len(geoms) == 1:
        return geoms[0]
    polys: list = []
    for g in geoms:
        polys.extend([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"])
    return {"type": "MultiPolygon", "coordinates": polys}


# ── search box (gsearch) ─────────────────────────────────────────────────────

def _gs_latlng(hit: dict) -> tuple[float, float] | None:
    coords = ((hit.get("geometri") or {}).get("coordinates") or [None])[0]
    if not coords:
        return None
    return to_latlng(coords[0], coords[1])


def autocomplete(q: str) -> list[dict]:
    """Suggestions for the search box, in the shape /api/autocomplete returns.

    Without a digit the user is still typing a street, so offer distinct
    street names (picking one refines to house numbers). With a digit, offer
    adgangsadresser (husnummer level - what tinglysning wants).
    """
    if not re.search(r"\d", q):
        # One suggestion per street *and town* (like DAWA did); a street name
        # alone is ambiguous. gsearch caps at 50 and has no useful ranking,
        # so sort by postnr; typing the town name narrows the list.
        out, seen = [], set()
        for h in gsearch("navngivenvej", q, limit=50):
            navn = h.get("vejnavn")
            postnr = (h.get("postnummer") or "").split(",")[0].strip()
            postnavn = (h.get("postnummernavn") or "").split(",")[0].strip()
            if not (navn and postnr) or (navn, postnr) in seen:
                continue
            seen.add((navn, postnr))
            out.append({"type": "vejnavn", "label": f"{navn}, {postnr} {postnavn}",
                        "vejnavn": navn, "postnr": postnr, "postnavn": postnavn})
        if out:
            return sorted(out, key=lambda r: (r["postnr"], r["vejnavn"]))
    out = []
    for h in gsearch("husnummer", q, limit=20):
        ll = _gs_latlng(h)
        if not (ll and h.get("postnummer") and h.get("vejnavn") and h.get("husnummertekst")):
            continue
        out.append({
            "type": "adresse",
            "label": h["visningstekst"],
            "postnr": h["postnummer"],
            "vejnavn": h["vejnavn"],
            "husnr": h["husnummertekst"],
            "lat": ll[0],
            "lng": ll[1],
        })
    return out


# ── DAR ──────────────────────────────────────────────────────────────────────

_HUS_FIELDS = ("id_lokalId adgangsadressebetegnelse husnummertekst status"
               " jordstykke adgangspunkt")


def _candidate(h: dict, xy: tuple[float, float] | None) -> dict | None:
    """DAR_Husnummer -> the dict shape nosy_nabo's matrikel code consumes
    (DAWA's old 'mini' adgangsadresse: postnr/vejnavn/husnr/betegnelse/x/y)."""
    bet = h.get("adgangsadressebetegnelse") or ""
    husnr = h.get("husnummertekst") or ""
    parts = [p.strip() for p in bet.split(",")]
    if len(parts) < 2 or not husnr or not parts[0].endswith(" " + husnr):
        return None
    m = re.match(r"(\d{4})\b", parts[-1])
    if not m:
        return None
    c = {
        "id": h["id_lokalId"],
        "betegnelse": bet,
        "vejnavn": parts[0][: -len(husnr) - 1],
        "husnr": husnr,
        "postnr": m.group(1),
        "jordstykke": h.get("jordstykke"),
        "x": None,
        "y": None,
    }
    if xy:
        lat, lng = to_latlng(*xy)
        c["x"], c["y"] = lng, lat
    return c


def _with_points(husnumre: list[dict]) -> list[dict]:
    husnumre = [h for h in husnumre if h.get("status") in _LIVE_DAR]
    pids = [h["adgangspunkt"] for h in husnumre if h.get("adgangspunkt")]
    pts: dict[str, tuple[float, float]] = {}
    if pids:
        data = gql(f'DAR_Adressepunkt(virkningstid:$NOW, first:{len(pids)},'
                   f' where:{{id_lokalId:{{in:{_list(pids)}}}}}){{ nodes{{ id_lokalId position{{wkt}} }} }}')
        for p in _nodes(data, "DAR_Adressepunkt"):
            xy = _point_xy((p.get("position") or {}).get("wkt"))
            if xy:
                pts[p["id_lokalId"]] = xy
    out = []
    for h in husnumre:
        c = _candidate(h, pts.get(h.get("adgangspunkt")))
        if c:
            out.append(c)
    out.sort(key=lambda c: c["betegnelse"])
    return out


def husnumre_on_jordstykke(jordstykke_id: str) -> list[dict]:
    data = gql(f'DAR_Husnummer(virkningstid:$NOW, first:500,'
               f' where:{{jordstykke:{{eq:{_lit(jordstykke_id)}}}}}){{ nodes{{ {_HUS_FIELDS} }} }}')
    return _with_points(_nodes(data, "DAR_Husnummer"))


def resolve_adresse(adresse_id: str) -> dict | None:
    """DAR adresse UUID -> {adgang_uuid, jordstykke} via DAR_Adresse -> DAR_Husnummer."""
    data = gql(f'DAR_Adresse(virkningstid:$NOW, first:1,'
               f' where:{{id_lokalId:{{eq:{_lit(adresse_id)}}}}}){{ nodes{{ husnummer }} }}')
    a = _nodes(data, "DAR_Adresse")
    if not a or not a[0].get("husnummer"):
        return None
    data = gql(f'DAR_Husnummer(virkningstid:$NOW, first:1,'
               f' where:{{id_lokalId:{{eq:{_lit(a[0]["husnummer"])}}}}}){{ nodes{{ id_lokalId jordstykke }} }}')
    h = _nodes(data, "DAR_Husnummer")
    if not h:
        return None
    return {"adgang_uuid": h[0]["id_lokalId"], "jordstykke": h[0].get("jordstykke")}


def nearest_address(lat: float, lng: float, max_m: float = 500) -> dict | None:
    """Closest live adgangsadresse within ``max_m`` metres (GPS button).

    Searches growing boxes so dense cities stay cheap and rural areas still
    find something; the box is then filtered to a true circle.
    """
    x, y = to_utm(lat, lng)
    for r in (60, 200, max_m):
        box = (f"POLYGON (({x - r} {y - r}, {x + r} {y - r}, {x + r} {y + r},"
               f" {x - r} {y + r}, {x - r} {y - r}))")
        data = gql(f'DAR_Adressepunkt(virkningstid:$NOW, first:300,'
                   f' where:{{position:{{within:{{wkt:{_lit(box)}, crs:25832}}}}}})'
                   f'{{ nodes{{ id_lokalId position{{wkt}} }} }}')
        pts = []
        for p in _nodes(data, "DAR_Adressepunkt"):
            xy = _point_xy((p.get("position") or {}).get("wkt"))
            if xy and hypot(xy[0] - x, xy[1] - y) <= r:
                pts.append((hypot(xy[0] - x, xy[1] - y), p["id_lokalId"]))
        if not pts:
            continue
        pts.sort()
        ids = [pid for _, pid in pts[:50]]
        data = gql(f'DAR_Husnummer(virkningstid:$NOW, first:200,'
                   f' where:{{adgangspunkt:{{in:{_list(ids)}}}}}){{ nodes{{ {_HUS_FIELDS} }} }}')
        cands = {c["id"]: c for c in _with_points(_nodes(data, "DAR_Husnummer"))}
        by_point: dict[str, dict] = {}
        for h in _nodes(data, "DAR_Husnummer"):
            if h["id_lokalId"] in cands:
                by_point.setdefault(h["adgangspunkt"], cands[h["id_lokalId"]])
        for _, pid in pts:
            if pid in by_point:
                return by_point[pid]
    return None


# ── MAT (Matriklen) ──────────────────────────────────────────────────────────

_JORD_FIELDS = ("id_lokalId matrikelnummer ejerlavLokalId registreretAreal"
                " samletFastEjendomLokalId kommuneLokalId status")

_mat_cache: TTLCache = TTLCache(maxsize=4096, ttl=3600)
# FastAPI runs sync endpoints in a thread pool; TTLCache is not thread-safe.
_mat_lock = threading.RLock()


def _pick_live(nodes: list[dict]) -> dict | None:
    for n in nodes:
        if n.get("status") == "Gældende":
            return n
    return nodes[0] if nodes else None


@cached(_mat_cache, lock=_mat_lock, key=lambda m, e: ("jordstykke", m, e))
def jordstykke(matrikelnr: str, ejerlavskode: str) -> dict | None:
    data = gql(f'MAT_Jordstykke(virkningstid:$NOW, first:5, where:{{'
               f'matrikelnummer:{{eq:{_lit(matrikelnr)}}}, ejerlavLokalId:{{eq:{_lit(ejerlavskode)}}}'
               f'}}){{ nodes{{ {_JORD_FIELDS} }} }}')
    return _pick_live(_nodes(data, "MAT_Jordstykke"))


@cached(_mat_cache, lock=_mat_lock, key=lambda i: ("jordstykke_id", i))
def jordstykke_by_id(jordstykke_id: str) -> dict | None:
    data = gql(f'MAT_Jordstykke(virkningstid:$NOW, first:5,'
               f' where:{{id_lokalId:{{eq:{_lit(jordstykke_id)}}}}}){{ nodes{{ {_JORD_FIELDS} }} }}')
    return _pick_live(_nodes(data, "MAT_Jordstykke"))


def jordstykker_by_sfe(sfe: str) -> list[dict]:
    data = gql(f'MAT_Jordstykke(virkningstid:$NOW, first:200,'
               f' where:{{samletFastEjendomLokalId:{{eq:{_lit(sfe)}}}}}){{ nodes{{ {_JORD_FIELDS} }} }}')
    return [n for n in _nodes(data, "MAT_Jordstykke") if n.get("status") == "Gældende"]


@cached(_mat_cache, lock=_mat_lock, key=lambda k: ("ejerlavsnavn", k))
def ejerlavsnavn(ejerlavskode: str) -> str | None:
    data = gql(f'MAT_Ejerlav(virkningstid:$NOW, first:1,'
               f' where:{{id_lokalId:{{eq:{_lit(ejerlavskode)}}}}}){{ nodes{{ ejerlavsnavn }} }}')
    n = _nodes(data, "MAT_Ejerlav")
    return n[0]["ejerlavsnavn"] if n else None


@cached(_mat_cache, lock=_mat_lock, key=lambda n: ("ejerlavskode", n))
def ejerlavskode_by_navn(navn: str) -> str | None:
    data = gql(f'MAT_Ejerlav(virkningstid:$NOW, first:2,'
               f' where:{{ejerlavsnavn:{{eq:{_lit(navn)}}}}}){{ nodes{{ id_lokalId }} }}')
    n = _nodes(data, "MAT_Ejerlav")
    # Ejerlav names are not globally unique; refuse to guess.
    return n[0]["id_lokalId"] if len(n) == 1 else None


@cached(_mat_cache, lock=_mat_lock, key=lambda i: ("geometry", i))
def jordstykke_geometry(jordstykke_id: str) -> dict | None:
    """WGS84 GeoJSON geometry for a jordstykke (all its lodflader merged)."""
    data = gql(f'MAT_Lodflade(virkningstid:$NOW, first:50,'
               f' where:{{jordstykkeLokalId:{{eq:{_lit(jordstykke_id)}}}}}){{ nodes{{ geometri{{wkt}} }} }}')
    return _merge([wkt_to_geojson(n["geometri"]["wkt"])
                   for n in _nodes(data, "MAT_Lodflade") if (n.get("geometri") or {}).get("wkt")])


def jordstykke_at(lat: float, lng: float) -> str | None:
    """Point-in-polygon: id of the jordstykke under a WGS84 point."""
    x, y = to_utm(lat, lng)
    data = gql(f'MAT_Lodflade(virkningstid:$NOW, first:1, where:{{geometri:{{contains:'
               f'{{wkt:{_lit(f"POINT ({x:.2f} {y:.2f})")}, crs:25832}}}}}}){{ nodes{{ jordstykkeLokalId }} }}')
    n = _nodes(data, "MAT_Lodflade")
    return n[0]["jordstykkeLokalId"] if n else None
