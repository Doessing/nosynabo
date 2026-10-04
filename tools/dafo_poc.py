#!/usr/bin/env python3
"""POC: erstat DAWA-opslag med Datafordeler GraphQL (DAR + Matriklen).

Beviser at hele DAWA-kæden kan genskabes på flexibleCurrent-gatewayen:

    adresse  -> DAR_Husnummer      (jordstykke-ref + adgangspunkt-ref)
             -> MAT_Jordstykke     (matrikelnummer, ejerlav, areal, SFE)
             -> MAT_Ejerlav        (ejerlavsnavn)
             -> DAR_Adressepunkt   (koordinat, EPSG:25832)

og det omvendte join, som DAWA lavede via /adgangsadresser?matrikelnr=:

    jordstykke -> DAR_Husnummer    (alle adresser på matriklen)

Nøglen commit'es ikke. Sæt den i miljøet først:

    export DATAFORDELER_API_KEY=...      # findes i /home/ubuntu/.secrets
    python3 tools/dafo_poc.py "Rosenvangs Allé 228A"
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

GATEWAY = "https://graphql.datafordeler.dk/flexibleCurrent/v1"
TIMEOUT = 30


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def query(gql: str, api_key: str) -> dict:
    payload = json.dumps({"query": gql}).encode()
    req = urllib.request.Request(
        f"{GATEWAY}?apikey={api_key}",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = json.load(resp)
    if "errors" in body:
        raise RuntimeError(json.dumps(body["errors"], ensure_ascii=False))
    return body["data"]


def nodes(data: dict, entity: str) -> list[dict]:
    return data[entity]["nodes"]


def resolve(betegnelse: str, api_key: str) -> dict:
    """Genskab DAWA's adgangsadresse-objekt fra DAR + Matriklen."""
    now = _now()

    hus = nodes(
        query(
            f'query{{ DAR_Husnummer(virkningstid:"{now}", first:1,'
            f' where:{{adgangsadressebetegnelse:{{startsWith:"{betegnelse}"}}}})'
            "{ nodes{ id_lokalId adgangsadressebetegnelse husnummertekst status"
            " jordstykke adgangspunkt } } }",
            api_key,
        ),
        "DAR_Husnummer",
    )
    if not hus:
        raise SystemExit(f"ingen adresse fundet for {betegnelse!r}")
    h = hus[0]

    jord = nodes(
        query(
            f'query{{ MAT_Jordstykke(virkningstid:"{now}", first:1,'
            f' where:{{id_lokalId:{{eq:"{h["jordstykke"]}"}}}})'
            "{ nodes{ matrikelnummer ejerlavLokalId registreretAreal vejareal"
            " samletFastEjendomLokalId kommuneLokalId } } }",
            api_key,
        ),
        "MAT_Jordstykke",
    )
    j = jord[0] if jord else {}

    ejerlavsnavn = None
    if j.get("ejerlavLokalId"):
        ejerlav = nodes(
            query(
                f'query{{ MAT_Ejerlav(virkningstid:"{now}", first:1,'
                f' where:{{id_lokalId:{{eq:"{j["ejerlavLokalId"]}"}}}})'
                "{ nodes{ ejerlavsnavn } } }",
                api_key,
            ),
            "MAT_Ejerlav",
        )
        if ejerlav:
            ejerlavsnavn = ejerlav[0]["ejerlavsnavn"]

    punkt = nodes(
        query(
            f'query{{ DAR_Adressepunkt(virkningstid:"{now}", first:1,'
            f' where:{{id_lokalId:{{eq:"{h["adgangspunkt"]}"}}}})'
            "{ nodes{ position{ crs wkt } } } }",
            api_key,
        ),
        "DAR_Adressepunkt",
    )
    position = punkt[0]["position"] if punkt else None

    return {
        "adgangsadressebetegnelse": h["adgangsadressebetegnelse"],
        "adgang_uuid": h["id_lokalId"],
        "husnr": h["husnummertekst"],
        "status": h["status"],
        "jordstykke_id": h["jordstykke"],
        "matrikelnr": j.get("matrikelnummer"),
        "ejerlavskode": j.get("ejerlavLokalId"),
        "ejerlavsnavn": ejerlavsnavn,
        "areal_m2": j.get("registreretAreal"),
        "sfeejendomsnr": j.get("samletFastEjendomLokalId"),
        "kommunekode": j.get("kommuneLokalId"),
        "position": position,
    }


def click(x_25832: float, y_25832: float, api_key: str) -> dict:
    """Erstat DAWA /jordstykker/reverse med punkt-i-polygon på MAT_Lodflade.

    MAT_Jordstykke har ingen geometri og kan ikke filtreres rumligt. MAT_Lodflade
    har begge dele, og `jordstykkeLokalId` fører videre til selve jordstykket.

    Koordinatet SKAL være EPSG:25832 - gatewayen afviser crs 4326 på MAT-felter.
    """
    now = _now()
    wkt = f"POINT ({x_25832} {y_25832})"

    lod = nodes(
        query(
            f'query{{ MAT_Lodflade(virkningstid:"{now}", first:1,'
            f' where:{{geometri:{{contains:{{wkt:"{wkt}", crs:25832}}}}}})'
            "{ nodes{ jordstykkeLokalId geometri{ crs wkt } } } }",
            api_key,
        ),
        "MAT_Lodflade",
    )
    if not lod:
        return {}
    l = lod[0]

    jord = nodes(
        query(
            f'query{{ MAT_Jordstykke(virkningstid:"{now}", first:1,'
            f' where:{{id_lokalId:{{eq:"{l["jordstykkeLokalId"]}"}}}})'
            "{ nodes{ matrikelnummer ejerlavLokalId registreretAreal"
            " samletFastEjendomLokalId kommuneLokalId } } }",
            api_key,
        ),
        "MAT_Jordstykke",
    )
    j = jord[0] if jord else {}

    return {
        "jordstykke_id": l["jordstykkeLokalId"],
        "matrikelnr": j.get("matrikelnummer"),
        "ejerlavskode": j.get("ejerlavLokalId"),
        "areal_m2": j.get("registreretAreal"),
        # DAWA's bfenummer og sfeejendomsnr er samme værdi som denne
        "bfenummer": j.get("samletFastEjendomLokalId"),
        "kommunekode": j.get("kommuneLokalId"),
        "polygon_wkt": l["geometri"]["wkt"],
        "polygon_crs": l["geometri"]["crs"],
    }


def adresser_paa_jordstykke(jordstykke_id: str, api_key: str) -> list[str]:
    """Det join DAWA lavede med /adgangsadresser?ejerlavkode=&matrikelnr=."""
    data = query(
        f'query{{ DAR_Husnummer(virkningstid:"{_now()}", first:50,'
        f' where:{{jordstykke:{{eq:"{jordstykke_id}"}}}})'
        "{ nodes{ adgangsadressebetegnelse status } } }",
        api_key,
    )
    return [n["adgangsadressebetegnelse"] for n in nodes(data, "DAR_Husnummer")]


def main() -> None:
    api_key = os.environ.get("DATAFORDELER_API_KEY")
    if not api_key:
        raise SystemExit("sæt DATAFORDELER_API_KEY i miljøet først")

    betegnelse = sys.argv[1] if len(sys.argv) > 1 else "Rosenvangs Allé 228A"

    resolved = resolve(betegnelse, api_key)
    print(json.dumps(resolved, ensure_ascii=False, indent=2))

    print(f"\nAdresser på jordstykke {resolved['jordstykke_id']}:")
    for label in adresser_paa_jordstykke(resolved["jordstykke_id"], api_key):
        print(f"  {label}")


if __name__ == "__main__":
    main()
