"""Regression test suite for nosynabo.

Run with:
    python3 tests/regression.py

Each test case is a tuple:
    (label, query_address, checks)

where checks is a dict with optional keys:
    has_andel      bool   result["andelsbolig"] is not None
    has_ejd        bool   result["uuid"] is not None
    has_fallback   bool   result["_matrikel_fallback"] is not None
    owner_contains str    substring expected in first owner name (case-insensitive)
    error          bool   lookup_address should raise (True) or not (False, default)

Add new test cases here whenever a bug is fixed or a new feature is verified.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from resolver import resolve, ResolveError
from nosy_nabo import TinglysningClient

# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

CASES = [
    # ------------------------------------------------------------------
    # Andelsboliger — individuelle andele (ejd=0, altid andelsoeg)
    # ------------------------------------------------------------------
    (
        "andel-individuel / Jægertoften 6",
        "Jægertoften 6, 6780 Skærbæk",
        {"has_andel": True},
    ),
    (
        "andel-individuel / Ryttervænget 107A",
        "Ryttervænget 107A, 6752 Glejbjerg",
        {"has_andel": True},
    ),
    (
        "andel-individuel / Bellmansgade 13 stor forening",
        "Bellmansgade 13, 1. 1, 2100 København Ø",
        {"has_andel": True},
    ),
    (
        "andel-individuel / Vesterbrogade 50 1. tv",
        "Vesterbrogade 50, 1. tv, 1620 København V",
        {"has_andel": True},
    ),
    (
        "andel-individuel / Vesterbrogade 60 2.",
        "Vesterbrogade 60, 2., 1620 København V",
        {"has_andel": True},
    ),
    # ------------------------------------------------------------------
    # Andelsboliger — hoved-ejendomme (ejd=1, ejer matcher andel-keyword)
    # ------------------------------------------------------------------
    (
        "andel-hoved / Ryttervænget 101A",
        "Ryttervænget 101A, 6752 Glejbjerg",
        {"has_andel": True, "owner_contains": "andelsboligforeningen"},
    ),
    (
        "andel-hoved / Bellmansgade 7",
        "Bellmansgade 7, 2100 København Ø",
        {"has_andel": True, "owner_contains": "a/b"},
    ),
    (
        "andel-hoved / Vesterbrogade 50",
        "Vesterbrogade 50, 1620 København V",
        {"has_andel": True, "owner_contains": "a/b"},
    ),
    (
        "andel-hoved / Vesterbrogade 60",
        "Vesterbrogade 60, 1620 København V",
        {"has_andel": True, "owner_contains": "andelsboligforeningen"},
    ),
    # Jægertoften 2 er hoved-ejendom for foreningen men har ingen andels-
    # poster i andelsboligbogen — ejere-navn matcher keyword, andelsoeg
    # kaldes, returnerer 0 hits → andelsbolig=None er korrekt.
    (
        "andel-hoved uden andelsboligbog-hit / Jægertoften 2",
        "Jægertoften 2, 6780 Skærbæk",
        {"has_andel": False, "owner_contains": "andelsboligforeningen"},
    ),
    # ------------------------------------------------------------------
    # Normale villaer — privatpersoner (skal IKKE kalde andelsoeg)
    # ------------------------------------------------------------------
    ("villa / Elmevej 14 Them",             "Elmevej 14, 8653 Them",                 {"has_andel": False}),
    ("villa / Akacievej 12 Odense N",        "Akacievej 12, 5270 Odense N",           {"has_andel": False}),
    ("villa / Birkevej 4 Viborg",            "Birkevej 4, 8800 Viborg",               {"has_andel": False}),
    ("villa / Skovvej 8 Hillerød",           "Skovvej 8, 3400 Hillerød",              {"has_andel": False}),
    ("villa / Møllevej 2A Sønderborg",       "Møllevej 2A, 6400 Sønderborg",          {"has_andel": False}),
    ("villa / Rosenvej 5 Vejle",             "Rosenvej 5, 7100 Vejle",                {"has_andel": False}),
    ("villa / Lærkevej 7 Næstved",           "Lærkevej 7, 4700 Næstved",              {"has_andel": False}),
    ("villa / navnebeskyttet Lyngby",        "Egegårdsvej 12, 2800 Kongens Lyngby",   {"has_andel": False}),
    ("villa / selskab Hellerup",             "Strandvejen 100, 2900 Hellerup",         {"has_andel": False}),
    # ------------------------------------------------------------------
    # Ejerlejligheder
    # ------------------------------------------------------------------
    (
        "ejerlejlighed-komplex / Sonnesgade 15 Aarhus",
        "Sonnesgade 15, st. tv, 8000 Aarhus C",
        {"has_andel": False},
    ),
    # ------------------------------------------------------------------
    # Erhverv og offentlige ejendomme
    # ------------------------------------------------------------------
    (
        "erhverv-offentlig / Kongens Nytorv 1",
        "Kongens Nytorv 1, 1050 København K",
        {"has_andel": False, "owner_contains": "undervisningsministeriet"},
    ),
    (
        "erhverv-offentlig / Rådhuspladsen 1",
        "Rådhuspladsen 1, 1550 København V",
        {"has_andel": False, "owner_contains": "kommune"},
    ),
    # ------------------------------------------------------------------
    # Almene boliger
    # ------------------------------------------------------------------
    ("almen-bolig / Agerhaven 2 Hedehusene",  "Agerhaven 2, 2640 Hedehusene",          {"has_andel": False}),
    ("almen-bolig / Mjølnerparken 2 KBH N",   "Mjølnerparken 2, kl., 2200 København N", {"has_andel": False}),
    # ------------------------------------------------------------------
    # Landbrug
    # ------------------------------------------------------------------
    ("landbrug / Lindevej 14 Sorø",           "Lindevej 14, 4180 Sorø",                {"has_andel": False}),
    ("landbrug / Bækkegårdsvej 2 Viborg",     "Bækkegårdsvej 2, 8800 Viborg",          {"has_andel": False}),
    # ------------------------------------------------------------------
    # Sommerhus
    # ------------------------------------------------------------------
    ("sommerhus / Mentz Alle 8 Gilleleje",    "Mentz Alle 8, 3250 Gilleleje",          {"has_andel": False}),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run() -> int:
    client = TinglysningClient()
    passed = failed = 0

    col_label = 46
    col_addr  = 52

    print(f"{'':3} {'Label':<{col_label}} {'Adresse':<{col_addr}} Checks")
    print("-" * 130)

    for label, query, checks in CASES:
        expect_error = checks.get("error", False)
        try:
            r = resolve(query)
            result = client.lookup_address(
                r.postnr, r.vejnavn, r.husnr,
                matrikelnr=r.matrikelnr,
                ejerlavskode=r.ejerlavskode,
            )
            if expect_error:
                failed += 1
                print(f"✗   {label:<{col_label}} {r.label:<{col_addr}} expected error, got result")
                continue
        except Exception as e:
            if expect_error:
                passed += 1
                print(f"✓   {label:<{col_label}} {query:<{col_addr}} raised as expected")
            else:
                failed += 1
                print(f"✗   {label:<{col_label}} {query:<{col_addr}} unexpected error: {e}")
            continue

        failures = []

        if "has_andel" in checks:
            got = result.get("andelsbolig") is not None
            if got != checks["has_andel"]:
                failures.append(f"has_andel={got} (expected {checks['has_andel']})")

        if "has_ejd" in checks:
            got = result.get("uuid") is not None
            if got != checks["has_ejd"]:
                failures.append(f"has_ejd={got} (expected {checks['has_ejd']})")

        if "has_fallback" in checks:
            got = result.get("_matrikel_fallback") is not None
            if got != checks["has_fallback"]:
                failures.append(f"has_fallback={got} (expected {checks['has_fallback']})")

        if "owner_contains" in checks:
            ejere = result.get("ejere") or []
            navn = (ejere[0].get("navn") or "").lower() if ejere else ""
            kw = checks["owner_contains"].lower()
            if kw not in navn:
                failures.append(f"owner_contains={kw!r} not in {navn!r}")

        if failures:
            failed += 1
            print(f"✗   {label:<{col_label}} {r.label:<{col_addr}} {', '.join(failures)}")
        else:
            passed += 1
            print(f"✓   {label:<{col_label}} {r.label:<{col_addr}}")

    print()
    print(f"Passed: {passed}/{passed + failed}   Failed: {failed}")
    return failed


if __name__ == "__main__":
    sys.exit(run())
