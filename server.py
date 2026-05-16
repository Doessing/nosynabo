"""
nosy-nabo web server.

Serves a map-based UI, a JSON REST API, and an MCP server at POST /mcp.
"""

import copy
import logging
import os
import subprocess
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP
import uvicorn

from nosy_nabo import TinglysningClient, get_loan_type_info
from boligsiden import get_sales_history
from resolver import resolve as resolve_address, ResolveError

DAWA_REVERSE_URL = "https://api.dataforsyningen.dk/adgangsadresser/reverse"
DAWA_JORDSTYKKER_URL = "https://api.dataforsyningen.dk/jordstykker"
DAWA_JORDSTYKKER_REVERSE_URL = "https://api.dataforsyningen.dk/jordstykker/reverse"

log = logging.getLogger(__name__)

_client = TinglysningClient()


def _git_version() -> str:
    """Return a short 'sha branch date' string for injection into the HTML.

    Best-effort — returns 'unknown' if git is unavailable or the directory is
    not a checkout (e.g. when running from a container or tarball).
    """
    try:
        def g(*args: str) -> str:
            return subprocess.check_output(
                ["git", *args], stderr=subprocess.DEVNULL, cwd=os.path.dirname(os.path.abspath(__file__))
            ).decode().strip()
        sha = g("rev-parse", "--short", "HEAD")
        branch = g("rev-parse", "--abbrev-ref", "HEAD")
        date = g("log", "-1", "--format=%cI")
        return f"{sha} {branch} {date}"
    except Exception:
        return "unknown"


# Captured at import time. The service is always restarted on deploy
# (see /opt/nosynabo/update.sh), so this is safe; if the process is
# ever hot-reloaded in future, this needs to move into /api/version.
_VERSION = _git_version()

with open("templates/index.html") as f:
    _index_html = f.read()

# Inject a version marker at the top so `Ctrl+U` + search for "version" reveals
# exactly which commit is serving the page. Purely for operator use.
# Short hash only (first token) for use in URL query strings — the full
# _VERSION contains spaces (branch, timestamp) which are invalid in URLs.
_VERSION_SHORT = _VERSION.split()[0] if _VERSION else "dev"
_index_html = _index_html.replace("{{VERSION}}", _VERSION_SHORT)
_index_html = f"<!-- version: {_VERSION} -->\n{_index_html}"

with open("templates/readme.html") as f:
    _readme_html = f.read()
_readme_html = _readme_html.replace("{{VERSION}}", _VERSION)
_readme_html = f"<!-- version: {_VERSION} -->\n{_readme_html}"


def _annotate_loan_types(tingbog: dict) -> dict:
    # Tingbog can come from _tingbog_cache, so deep-copy before mutating to
    # avoid polluting the cached object with annotations whose format may
    # change across releases.
    tingbog = copy.deepcopy(tingbog)
    for h in tingbog.get("haeftelser") or []:
        rente = float(h.get("rente") or 0)
        if (h.get("fastvariabel") == "variabel"
                and h.get("haeftelsestype") in ("Realkreditpantebrev", "Afgiftspantebrev")
                and rente > 0):
            h["loan_type_info"] = get_loan_type_info(rente, alias=h.get("alias"))
    return tingbog


# ── MCP server ────────────────────────────────────────────────────────────────
mcp_server = FastMCP("nosy-nabo", stateless_http=True, json_response=True)


@mcp_server.tool()
def lookup_property(address: str) -> dict:
    """Look up Danish property records from tinglysning.dk.

    Given a freeform Danish address, returns owners (ejere), official
    valuation (vurdering) with equity estimate, mortgages and liens
    (hæftelser) with loan-type estimation for variable-rate realkreditlån,
    and easements (servitutter).
    """
    try:
        resolved = resolve_address(address)
    except ResolveError as e:
        return {"error": str(e)}
    try:
        tingbog = _client.lookup_address(
            resolved.postnr,
            resolved.vejnavn,
            resolved.husnr,
            matrikelnr=resolved.matrikelnr or None,
            ejerlavskode=resolved.ejerlavskode or None,
        )
    except RuntimeError as e:
        return {"error": str(e)}
    if tingbog is None:
        return {"error": "No property data found"}
    return _annotate_loan_types(tingbog)


@mcp_server.tool()
def lookup_sales_history(address: str) -> dict:
    """Look up historical sale prices for a Danish address from Boligsiden.

    Given a freeform Danish address, returns every recorded sale of that
    exact address with date, price (DKK), area (m²), price per m², and
    sale type (normal=Almindeligt salg, family=Familiehandel,
    auction=Tvangsauktion). Sorted newest first.
    """
    try:
        resolved = resolve_address(address)
    except ResolveError as e:
        return {"error": str(e)}
    try:
        return get_sales_history(resolved)
    except requests.RequestException as e:
        return {"error": f"Boligsiden unreachable: {e}"}


# ── FastAPI app ───────────────────────────────────────────────────────────────
_mcp_asgi = mcp_server.streamable_http_app()  # lazily initialises session_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp_server.session_manager.run():
        yield


app = FastAPI(title="nosy-nabo", lifespan=lifespan)

# Self-hosted Leaflet (and any future static assets). Mounted before the MCP
# catch-all on / so /static/* routes resolve here. StaticFiles sets
# Last-Modified + ETag automatically; Cloudflare caches .js/.css by default.
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/api/autocomplete")
def autocomplete(q: str = Query(...), response: Response = None):
    try:
        results = _client.autocomplete_address(q)
    except requests.RequestException as e:
        log.warning("autocomplete upstream error: %s", e)
        raise HTTPException(status_code=502, detail="DAWA unreachable")
    # Address suggestions are public and stable; let CF and the browser cache
    # them briefly so repeated typing of the same prefix is instant.
    if response is not None:
        response.headers["Cache-Control"] = "public, max-age=300"
    out = []
    for r in results:
        d = r.get("data", {})
        if d.get("postnr") and d.get("vejnavn") and d.get("husnr"):
            out.append({
                "type": "adresse",
                "label": r["forslagstekst"],
                "postnr": d["postnr"],
                "vejnavn": d["vejnavn"],
                "husnr": d["husnr"],
                "lat": d["y"],
                "lng": d["x"],
            })
        elif r.get("type") == "vejnavn" and d.get("navn"):
            # Street-name suggestion: user picks it and we refine to addresses.
            out.append({
                "type": "vejnavn",
                "label": r["forslagstekst"],
                "vejnavn": d["navn"],
            })
    return out


@app.get("/api/reverse")
def reverse(lat: float = Query(...), lng: float = Query(...)):
    # DAWA reverse silently ignores maks_afstand and defaults to EPSG:25832 —
    # pass srid=4326 explicitly and post-validate distance ourselves so ocean
    # and cross-border clicks don't silently resolve to a distant address.
    try:
        resp = requests.get(
            DAWA_REVERSE_URL,
            params={"x": lng, "y": lat, "srid": 4326, "maks_afstand": 500},
            timeout=10,
        )
    except requests.RequestException as e:
        log.warning("reverse upstream error: %s", e)
        raise HTTPException(status_code=502, detail="DAWA unreachable")
    if resp.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail="Ingen adresse inden for 500 m af det valgte punkt.",
        )
    resp.raise_for_status()
    d = resp.json()
    a_lat = d["adgangspunkt"]["koordinater"][1]
    a_lng = d["adgangspunkt"]["koordinater"][0]
    # Haversine distance in metres
    from math import radians, sin, cos, asin, sqrt
    dlat = radians(a_lat - lat)
    dlng = radians(a_lng - lng)
    h = sin(dlat / 2) ** 2 + cos(radians(lat)) * cos(radians(a_lat)) * sin(dlng / 2) ** 2
    dist_m = 2 * 6371000 * asin(sqrt(h))
    if dist_m > 500:
        raise HTTPException(
            status_code=404,
            detail="Ingen adresse inden for 500 m af det valgte punkt.",
        )
    return {
        "label": d["adressebetegnelse"],
        "postnr": d["postnummer"]["nr"],
        "vejnavn": d["vejstykke"]["navn"],
        "husnr": d["husnr"],
        "lat": a_lat,
        "lng": a_lng,
    }


@app.get("/api/click")
def click(lat: float = Query(...), lng: float = Query(...), response: Response = None):
    """Resolve a map click to the matrikel under the cursor.

    Map clicks are inherently spatial: the user pointed at a piece of land,
    not at an address. Asking DAWA's adgangsadresser/reverse can resolve to
    a neighbouring address that's geographically close but legally unrelated,
    so we instead ask DAWA which jordstykke (cadastral parcel) the click
    falls inside. The matrikel polygon either contains the click or it
    doesn't — no 500 m fudge factor required.

    Returns matrikelnr + ejerlavkode + ejerlavsnavn + bfenummer + geometry
    so the frontend can highlight the parcel immediately while the slower
    tinglysning lookup runs.
    """
    try:
        resp = requests.get(
            DAWA_JORDSTYKKER_REVERSE_URL,
            params={"x": lng, "y": lat, "srid": 4326, "format": "geojson"},
            timeout=(3, 8),
        )
    except requests.RequestException as e:
        log.warning("click jordstykker/reverse upstream error: %s", e)
        raise HTTPException(status_code=502, detail="DAWA unreachable")
    if resp.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail="Klikket er uden for dansk territorium eller på et område uden matrikel.",
        )
    try:
        resp.raise_for_status()
        feature = resp.json()
    except (requests.RequestException, ValueError):
        raise HTTPException(status_code=502, detail="DAWA returned invalid response")

    props = feature.get("properties") or {}
    matrikelnr = (props.get("matrikelnr") or "").strip()
    ejerlavkode = props.get("ejerlavkode")
    ejerlavsnavn = (props.get("ejerlavsnavn") or "").strip() or None
    if not matrikelnr or ejerlavkode is None:
        raise HTTPException(
            status_code=404,
            detail="Matriklen kunne ikke identificeres på det valgte punkt.",
        )

    # jordstykker/reverse is known to sometimes omit ejerlavsnavn even when
    # ejerlavkode is set. Do a tiny follow-up lookup to get the human-readable
    # name — costs ~100 ms and only fires on the click-flow, not address search.
    if not ejerlavsnavn:
        try:
            r2 = requests.get(
                DAWA_JORDSTYKKER_URL,
                params={
                    "ejerlavkode": ejerlavkode,
                    "matrikelnr": matrikelnr,
                    "per_side": 1,
                },
                timeout=(3, 5),
            )
            if r2.ok:
                arr = r2.json() or []
                if arr:
                    el = arr[0].get("ejerlav") or {}
                    ejerlavsnavn = (el.get("navn") or "").strip() or None
        except requests.RequestException as e:
            log.info("click ejerlavsnavn refinement skipped: %s", e)
            # non-fatal — we can still return matrikel + ejerlavkode

    # Modest cache so re-clicks on the same parcel don't re-query DAWA.
    # Jordstykke polygons change rarely (cadastral updates are infrequent).
    if response is not None:
        response.headers["Cache-Control"] = "public, max-age=300"

    return {
        "matrikelnr": matrikelnr,
        "ejerlavkode": str(ejerlavkode),
        "ejerlavsnavn": ejerlavsnavn,
        "bfenummer": props.get("bfenummer"),
        "kommunekode": props.get("kommunekode"),
        "esrejendomsnr": props.get("esrejendomsnr"),
        "sfeejendomsnr": props.get("sfeejendomsnr"),
        "geometry": feature.get("geometry"),
        "click_lat": lat,
        "click_lng": lng,
    }


@app.get("/api/lookup-matrikel")
def lookup_matrikel(
    matrikelnr: str = Query(..., min_length=1),
    ejerlavskode: str = Query(..., min_length=1),
):
    """Tingbog lookup driven by (matrikelnr, ejerlavskode) — the click path.

    Reuses TinglysningClient._find_tingbog_by_matrikel, which iterates the
    DAWA adgangsadresser tied to the parcel and returns the first tingbog
    whose `matrikler` actually contains our parcel. Far more reliable than
    guessing an address from the click coordinates.

    Returns the same shape as /api/lookup so the existing frontend cards
    work unchanged. `_matrikel_fallback` is always set (this code path is
    by definition a matrikel-first lookup).
    """
    matrikelnr = matrikelnr.strip()
    ejerlavskode = ejerlavskode.strip()
    try:
        fallback = _client._find_tingbog_by_matrikel(matrikelnr, ejerlavskode)
    except requests.Timeout:
        log.warning("lookup-matrikel timeout for %s / %s", matrikelnr, ejerlavskode)
        raise HTTPException(
            status_code=504,
            detail="Tinglysning.dk svarer ikke lige nu — prøv igen om lidt.",
        )
    except requests.RequestException as e:
        log.warning("lookup-matrikel upstream error: %s", e)
        raise HTTPException(status_code=502, detail="Tinglysning.dk unreachable")

    if fallback is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Matriklen kunne ikke kobles til en tingbog. "
                "Det er typisk arealer uden adgangsadresse — fx ubebyggede "
                "marker, veje, vandarealer eller fælleslodder. Matriklen "
                "har stadig en ejer, men tinglysning.dk's API kræver en "
                "adresse for at slå tingbogen op."
            ),
        )

    tingbog, parent_adresse = fallback
    tingbog = _annotate_loan_types(tingbog)
    tingbog["_matrikel_fallback"] = {
        "matrikelnr": matrikelnr,
        "ejerlavskode": ejerlavskode,
        "parent_adresse": parent_adresse,
    }
    tingbog.setdefault("andelsbolig", None)
    return tingbog


@app.get("/api/lookup")
def lookup(q: str = Query(...)):
    try:
        resolved = resolve_address(q)
    except ResolveError as e:
        raise HTTPException(status_code=404, detail=str(e))
    try:
        tingbog = _client.lookup_address(
            resolved.postnr,
            resolved.vejnavn,
            resolved.husnr,
            matrikelnr=resolved.matrikelnr or None,
            ejerlavskode=resolved.ejerlavskode or None,
        )
    except requests.Timeout:
        log.warning("tinglysning timeout for %r", q)
        raise HTTPException(
            status_code=504,
            detail="Tinglysning.dk svarer ikke lige nu — prøv igen om lidt.",
        )
    except requests.RequestException as e:
        log.warning("tinglysning upstream error for %r: %s", q, e)
        raise HTTPException(status_code=502, detail="Tinglysning.dk unreachable")
    except RuntimeError as e:
        msg = str(e)
        if "No property found" in msg:
            msg = (
                "Adressen har ingen selvstændig tingbog. Det sker typisk for "
                "andelsboliger, lejeboliger og ejendomme uden selvstændig BFE. "
                "Prøv foreningens hovedadresse."
            )
        raise HTTPException(status_code=404, detail=msg)
    if tingbog is None:
        raise HTTPException(status_code=404, detail="No property data found")
    return _annotate_loan_types(tingbog)


@app.get("/api/sales-history")
def sales_history(q: str = Query(...)):
    """Historical sale prices for a given address, sourced from Boligsiden."""
    try:
        resolved = resolve_address(q)
    except ResolveError as e:
        raise HTTPException(status_code=404, detail=str(e))
    try:
        return get_sales_history(resolved)
    except requests.RequestException as e:
        log.warning("sales-history upstream error: %s", e)
        raise HTTPException(status_code=502, detail="Boligsiden unreachable")


@app.get("/api/resolve")
def resolve_endpoint(q: str = Query(...)):
    """Return the structured identifiers nosy-nabo derives for an address.

    Useful for debugging (why does source X not find my address?) and as a
    shared primitive for any client that wants to call multiple data-source
    endpoints without re-paying the DAWA round-trip cost.
    """
    try:
        return resolve_address(q).to_dict()
    except ResolveError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except requests.RequestException as e:
        log.warning("resolver upstream error: %s", e)
        raise HTTPException(status_code=502, detail="DAWA unreachable")


@app.get("/api/matrikel-geometry")
def matrikel_geometry(
    matrikelnr: str = Query(..., min_length=1),
    ejerlavskode: str | None = Query(None),
    ejerlavsnavn: str | None = Query(None),
):
    """Return GeoJSON polygon(s) for one matrikel on WGS84 (EPSG:4326)."""
    matrikelnr = matrikelnr.strip()
    ejerlavskode = (ejerlavskode or "").strip() or None
    ejerlavsnavn = (ejerlavsnavn or "").strip() or None
    if not ejerlavskode and not ejerlavsnavn:
        raise HTTPException(
            status_code=400,
            detail="Provide either ejerlavskode or ejerlavsnavn",
        )
    params = {
        "matrikelnr": matrikelnr,
        "format": "geojson",
        "srid": 4326,
        "per_side": 1,
    }
    if ejerlavskode:
        params["ejerlavkode"] = ejerlavskode
    else:
        params["ejerlavsnavn"] = ejerlavsnavn
    try:
        resp = requests.get(
            DAWA_JORDSTYKKER_URL,
            params=params,
            timeout=10,
        )
    except requests.RequestException as e:
        log.warning("matrikel-geometry upstream error: %s", e)
        raise HTTPException(status_code=502, detail="DAWA unreachable")
    try:
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError):
        raise HTTPException(status_code=502, detail="DAWA returned invalid response")

    features = payload.get("features") or []
    if not features:
        raise HTTPException(status_code=404, detail="Matrikel geometry not found")
    return {
        "type": "FeatureCollection",
        "features": [features[0]],
    }


@app.get("/", response_class=HTMLResponse)
def index():
    # Swap in the live debug flag on every request so toggling the config
    # file takes effect immediately without a restart. The script tag in
    # index.html only tries to load /static/_debug.js when both (a) the
    # URL carries ?debug=1 and (b) this server-side flag is true.
    html = _index_html.replace("__DEBUG_ALLOWED__",
                               "true" if _debug_enabled() else "false")
    # no-cache forces browsers to revalidate the HTML on every load. The
    # static assets it references use ?v=<hash> cache-busting, so they stay
    # cacheable — but the HTML must be fresh to reference the new hashes.
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/readme", response_class=HTMLResponse)
def readme():
    """Render the project README. The page fetches the raw Markdown from
    GitHub client-side, so the server only serves the viewer shell.
    """
    return HTMLResponse(
        content=_readme_html,
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


# ── Debug instrumentation endpoint ─────────────────────────────────────────
# Accepts JSONL event payloads from /static/_debug.js and appends one line per
# event to /tmp/nosynabo-debug.jsonl.
#
# On/off switch (checked on every request, so no restart is needed when you
# flip it). Precedence, highest wins:
#   1. /etc/nosynabo/debug.conf  — a plain file. If it contains "enabled=1"
#      or just "1" or "on" / "true", debug is ON. "0"/"off"/"false" → OFF.
#      File missing → fall through to next level.
#   2. NOSY_DEBUG env var         — "1"/"on"/"true" forces ON. "0" forces OFF.
#   3. Branch name heuristic      — ON when running on a feat/ / fix/ / debug/
#      branch (so ad-hoc feature work gets instrumentation for free), OFF on
#      main.
# Rate-limited by file size: drops writes once log exceeds 10 MB.
_DEBUG_LOG_PATH = "/tmp/nosynabo-debug.jsonl"
_DEBUG_LOG_MAX_BYTES = 10 * 1024 * 1024
_DEBUG_CONFIG_PATH = "/etc/nosynabo/debug.conf"


def _debug_enabled() -> bool:
    """Return True if the debug instrumentation is currently enabled.

    Re-read on every call so flipping /etc/nosynabo/debug.conf takes effect
    without a service restart.
    """
    truthy = {"1", "on", "true", "yes", "enabled"}
    falsy = {"0", "off", "false", "no", "disabled"}

    # 1. Config file — highest priority so ops can force-toggle without deploy.
    try:
        with open(_DEBUG_CONFIG_PATH) as f:
            raw = f.read().strip().lower()
        # Support either a bare value ("1") or INI-ish "enabled=1".
        if "=" in raw:
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "enabled":
                    raw = v.strip()
                    break
        if raw in truthy:
            return True
        if raw in falsy:
            return False
    except OSError:
        pass  # file missing or unreadable → fall through

    # 2. Env var override.
    env = os.environ.get("NOSY_DEBUG", "").strip().lower()
    if env in truthy:
        return True
    if env in falsy:
        return False

    # 3. Branch-name heuristic — feature branches default to ON, main to OFF.
    return any(seg in _VERSION for seg in ("feat/", "fix/", "debug/"))


@app.get("/api/_debug/status")
def _debug_status():
    """Expose current debug state so you can verify the toggle without SSH."""
    enabled = _debug_enabled()
    return {
        "enabled": enabled,
        "config_file": _DEBUG_CONFIG_PATH,
        "config_file_exists": os.path.exists(_DEBUG_CONFIG_PATH),
        "env_NOSY_DEBUG": os.environ.get("NOSY_DEBUG"),
        "version": _VERSION,
        "log_path": _DEBUG_LOG_PATH,
        "log_size_bytes": os.path.getsize(_DEBUG_LOG_PATH)
            if os.path.exists(_DEBUG_LOG_PATH) else 0,
        "log_max_bytes": _DEBUG_LOG_MAX_BYTES,
    }


@app.post("/api/_debug", status_code=204)
async def _debug_ingest(request: Request):
    """Accept a single JSON event from the debug instrumentation script.
    Writes one JSONL line with server-side timestamp and client IP.
    """
    if not _debug_enabled():
        raise HTTPException(status_code=404, detail="not found")
    try:
        size = os.path.getsize(_DEBUG_LOG_PATH)
    except OSError:
        size = 0
    if size >= _DEBUG_LOG_MAX_BYTES:
        return Response(status_code=204)  # silently drop; log is full
    try:
        body = await request.body()
        if len(body) > 4096:
            body = body[:4096]
        import json as _json
        try:
            payload = _json.loads(body)
        except Exception:
            payload = {"raw": body.decode("utf-8", "replace")[:500]}
        payload["_ip"] = request.client.host if request.client else "?"
        payload["_server_t"] = int(__import__("time").time() * 1000)
        with open(_DEBUG_LOG_PATH, "a") as f:
            f.write(_json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("debug ingest failed: %s", e)
    return Response(status_code=204)


# Cheeky little globe with glasses — served inline so we don't have to add
# a binary asset to the repo. SVG is fine for favicons in all modern browsers.
_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <circle cx="32" cy="32" r="28" fill="#4a90d9"/>
  <path d="M8 32h48M32 6c8 7 8 45 0 52M32 6c-8 7-8 45 0 52M12 18c6 4 34 4 40 0M12 46c6-4 34-4 40 0"
        fill="none" stroke="#2e7d32" stroke-width="2" opacity="0.55"/>
  <path d="M6 34c5-5 10-5 14 0M24 34c2-3 6-3 8 0M46 34c4-5 9-5 12 0"
        fill="#a8d8a8" opacity="0.7"/>
  <g transform="translate(0,4)">
    <circle cx="22" cy="30" r="8" fill="none" stroke="#1a1a1a" stroke-width="3"/>
    <circle cx="42" cy="30" r="8" fill="none" stroke="#1a1a1a" stroke-width="3"/>
    <path d="M30 30h4" stroke="#1a1a1a" stroke-width="3" stroke-linecap="round"/>
    <path d="M14 28l-5-2M50 28l5-2" stroke="#1a1a1a" stroke-width="3" stroke-linecap="round"/>
    <circle cx="22" cy="30" r="6" fill="#ffffff" opacity="0.85"/>
    <circle cx="42" cy="30" r="6" fill="#ffffff" opacity="0.85"/>
    <circle cx="23" cy="31" r="1.6" fill="#1a1a1a"/>
    <circle cx="43" cy="31" r="1.6" fill="#1a1a1a"/>
  </g>
</svg>"""


@app.get("/favicon.ico")
@app.get("/favicon.svg")
def favicon():
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml")


# Tell crawlers to stay out — the site is geo-blocked to DK and lives behind
# a free-tier Cloudflare WAF; there's nothing useful for search engines here
# and indexing only invites scraping attempts.
_ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return _ROBOTS_TXT


@app.get("/api/version")
def version():
    return {"version": _VERSION}


# Mount MCP last so FastAPI routes take priority when matching paths.
# streamable_http_app() registers its handler at /mcp inside the sub-app;
# mounting the sub-app at / keeps the final endpoint at POST /mcp.
app.mount("/", _mcp_asgi)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        ssl_certfile=os.environ.get("SSL_CERTFILE"),
        ssl_keyfile=os.environ.get("SSL_KEYFILE"),
    )
