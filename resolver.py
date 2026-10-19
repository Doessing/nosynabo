"""
Address resolver - the single entry point for translating a freeform Danish
address into the structured identifiers every downstream data source needs.

One gsearch hit plus a short DAR/MAT join (see dafo.py) yields every handle
we need: postnr/vejnavn/husnr for Tinglysningen, adresse-UUID for Boligsiden,
adgangsadresse-UUID for BBR, matrikel/ejerlav for the matrikel fallback,
coordinates for the map, kommunekode for statistics, etc.

Callers pass a `ResolvedAddress` to data-source modules so no module has to
repeat the round-trip. Results are cached in memory with a short TTL;
address data itself is stable (new addresses appear, existing ones rarely
mutate), so the TTL mainly protects against traffic spikes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

from cachetools import TTLCache

import dafo

# Matches a Danish house number: one or more digits, optional lowercase/uppercase
# letter suffix (e.g. "12", "12A", "103a").
_HUSNR_RE = re.compile(r"\b(\d{1,3}[A-Za-z]?)\b")
# Matches a 4-digit postal code (1000-9999).
_POSTNR_RE = re.compile(r"\b([1-9]\d{3})\b")

# One hour is generous - addresses don't change mid-session.
_RESOLVE_CACHE: TTLCache[str, "ResolvedAddress"] = TTLCache(maxsize=2048, ttl=3600)


@dataclass(frozen=True)
class ResolvedAddress:
    """Canonical handle for a Danish address across every data source we use."""
    query: str                 # original freeform input (for logging/debug)
    label: str                 # human-readable "Street N, 1234 City"
    postnr: str
    vejnavn: str
    husnr: str
    etage: str | None
    door: str | None
    # DAR adresse UUID (used by Boligsiden). Exception: if no etage/dør was
    # typed and the building only has flats, this is the husnummer
    # (adgangsadresse) UUID instead - Boligsiden then has no history.
    adresse_uuid: str
    adgang_uuid: str           # DAR husnummer (adgangsadresse) UUID
    kommunekode: str
    matrikelnr: str            # e.g. "4hf" - used to find tingbog via matrikel
    ejerlavskode: str          # e.g. "1290159" - pairs with matrikelnr
    ejerlavsnavn: str
    lat: float
    lng: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResolveError(RuntimeError):
    """Raised when a query cannot be resolved to a concrete address."""


def _search(query: str) -> list[dict[str, Any]]:
    """gsearch on the full query, then progressively shorter prefixes.

    gsearch is strict about trailing tokens it cannot match (ASCII town
    names like "hojbjerg", stray words), so drop words from the end until
    something matches - but never below the house number.
    """
    words = query.replace(",", " ").split()
    husnr_idx = max((i for i, w in enumerate(words) if _HUSNR_RE.fullmatch(w)), default=0)
    for n in range(len(words), husnr_idx, -1):
        hits = dafo.gsearch("adresse", " ".join(words[:n]), limit=20)
        if hits:
            return hits
    return []


def _unit_tokens(query: str, husnr_digits: str, postnr: str | None) -> set[str]:
    """Lower-cased query words that could be an etage/dør.

    Drops only the first house-number word and the postnr, so a floor equal
    to the house number ("Vej 1, 1. tv") is still seen as typed.
    """
    words = [w.strip(".").lower() for w in query.replace(",", " ").split()]
    for i, w in enumerate(words):
        m = _HUSNR_RE.fullmatch(w)
        if m and re.match(r"\d+", w).group(0) == husnr_digits:
            del words[i]
            break
    if postnr and postnr in words:
        words.remove(postnr)
    return {w for w in words if w}


def _best_hit(hits: list[dict[str, Any]], husnr_digits: str,
              postnr: str | None, query: str) -> dict[str, Any]:
    """Pick the hit that agrees with what the user typed.

    gsearch ranks well, but for "Vej 6" it may rank "Vej 60" or another town
    first. Prefer hits matching the typed house number (and postnr, if given);
    among those keep gsearch's order. Falls back to the top hit so the
    caller's sanity check can produce a helpful error message.

    Without an etage/dør in the query the user wants the property, not a
    flat: prefer the hit without etage/dør. If every hit is a flat (pure
    apartment building) return the top hit with etage/dør stripped; the
    caller then resolves at husnummer (adgangsadresse) level.
    """
    def ok(h: dict[str, Any]) -> bool:
        m = re.match(r"\d+", h.get("husnummer") or "")
        return bool(m and m.group(0) == husnr_digits
                    and (not postnr or h.get("postnummer") == postnr))

    matching = [h for h in hits if ok(h)]
    if not matching:
        return hits[0]
    typed = _unit_tokens(query, husnr_digits, postnr)

    def unit_typed(h: dict[str, Any]) -> bool:
        unit = [(h.get(k) or "").lower() for k in ("etagebetegnelse", "doerbetegnelse")]
        unit = [u for u in unit if u]
        return bool(unit) and all(u in typed for u in unit)

    for h in matching:
        if unit_typed(h):
            return h
    for h in matching:
        if not h.get("etagebetegnelse") and not h.get("doerbetegnelse"):
            return h
    return {**matching[0], "etagebetegnelse": None, "doerbetegnelse": None,
            "_husnummer_only": True}


def resolve(query: str) -> ResolvedAddress:
    """Resolve a freeform address to a `ResolvedAddress`, cached.

    Raises `ResolveError` if the query cannot be matched to a concrete
    address (e.g. too vague, typo, non-existent street). We also reject
    queries where the resolved result's postnr or husnr digits don't line
    up with what the user typed - fuzzy search happily
    returns a plausible Danish address even for nonsense input, which
    would otherwise surface completely unrelated tingbog data.
    """
    key = query.strip().lower()
    if not key:
        raise ResolveError(
            "Tom søgning - skriv en adresse (gade, husnummer og postnummer)."
        )
    # Require at least a house number; without it we can't distinguish
    # a specific property from the hundreds on a given street.
    user_husnr_match = _HUSNR_RE.search(query)
    user_postnr_match = _POSTNR_RE.search(query)
    if not user_husnr_match:
        raise ResolveError(
            "Manglende husnummer - skriv fx 'Borgergade 12, 6752 Glejbjerg'."
        )
    user_husnr_digits = re.match(r"\d+", user_husnr_match.group(1)).group(0)
    user_postnr = user_postnr_match.group(1) if user_postnr_match else None

    cached = _RESOLVE_CACHE.get(key)
    if cached is not None:
        # Preserve the caller's original query verbatim so downstream
        # logging / debugging is truthful about what was asked.
        if cached.query != query:
            from dataclasses import replace
            return replace(cached, query=query)
        return cached

    hits = _search(query)
    if not hits:
        raise ResolveError(
            f"Ingen adresse matcher '{query}' - skriv gade, husnummer og postnummer."
        )
    hit = _best_hit(hits, user_husnr_digits, user_postnr, query)

    vejnavn = hit.get("vejnavn") or ""
    husnr = hit.get("husnummer") or ""
    postnr = hit.get("postnummer") or ""
    postnavn = hit.get("postnummernavn") or ""
    etage = hit.get("etagebetegnelse") or None
    door = hit.get("doerbetegnelse") or None

    label = f"{vejnavn} {husnr}"
    if etage:
        label += f", {etage}."
    if door:
        label += f" {door}"
    label += f", {postnr} {postnavn}"

    # Sanity-check: fuzzy search is eager - "Kvakkelørevej 9999, 9999 Blabla"
    # still returns *some* Danish address. Reject anything whose house number
    # or postal code does not line up with what the user actually typed.
    resolved_husnr_digits = re.match(r"\d+", husnr).group(0) if husnr else ""
    if resolved_husnr_digits != user_husnr_digits:
        raise ResolveError(
            f"Ingen adresse matcher '{query}'. Bedste gæt var "
            f"{label!r}, men husnummeret stemmer ikke - tjek for stavefejl."
        )
    if user_postnr and postnr and user_postnr != postnr:
        raise ResolveError(
            f"Ingen adresse matcher '{query}'. Bedste gæt var "
            f"{label!r}, men postnummeret stemmer ikke."
        )

    # gsearch has no matrikel/husnummer handles; DAR + MAT supply them.
    dar = dafo.resolve_adresse(hit["id"]) or {}
    adresse_uuid = hit["id"]
    if hit.get("_husnummer_only"):
        # No flat chosen: identify the property by its adgangsadresse.
        # Boligsiden only knows flats, so sales history will be empty.
        adresse_uuid = dar.get("adgang_uuid") or adresse_uuid
    matrikelnr = ejerlavskode = ejerlavsnavn = ""
    if dar.get("jordstykke"):
        j = dafo.jordstykke_by_id(dar["jordstykke"]) or {}
        matrikelnr = j.get("matrikelnummer") or ""
        ejerlavskode = str(j.get("ejerlavLokalId") or "")
        if ejerlavskode:
            ejerlavsnavn = dafo.ejerlavsnavn(ejerlavskode) or ""

    coords = ((hit.get("geometri") or {}).get("coordinates") or [None])[0]
    lat, lng = dafo.to_latlng(coords[0], coords[1]) if coords else (0.0, 0.0)

    resolved = ResolvedAddress(
        query=query,
        label=label,
        postnr=postnr,
        vejnavn=vejnavn,
        husnr=husnr,
        etage=etage,
        door=door,
        adresse_uuid=adresse_uuid,
        adgang_uuid=dar.get("adgang_uuid", ""),
        kommunekode=hit.get("kommunekode") or "",
        matrikelnr=matrikelnr,
        ejerlavskode=ejerlavskode,
        ejerlavsnavn=ejerlavsnavn,
        lat=lat,
        lng=lng,
    )
    _RESOLVE_CACHE[key] = resolved
    return resolved
