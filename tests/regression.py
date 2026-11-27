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
    adresse_is     str    exact tingbog address (picks the right ejerlejlighed)
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
    # Kept small on purpose: every case costs tinglysning.dk requests, and
    # the full 29-case list hit their daily cap. One case per property type.
    ("andel-individuel / Vesterbrogade 50 1. tv", "Vesterbrogade 50, 1. tv, 1620 København V", {"has_andel": True}),
    ("andel-hoved / Ryttervænget 101A",          "Ryttervænget 101A, 6752 Glejbjerg",
     {"has_andel": True, "owner_contains": "andelsboligforeningen"}),
    ("villa / Elmevej 14 Them",                   "Elmevej 14, 8653 Them",                    {"has_andel": False}),
    ("villa / navnebeskyttet Lyngby",             "Egegårdsvej 12, 2800 Kongens Lyngby",      {"has_andel": False}),
    ("ejerlejlighed / Nygårdsvej 29, 1. 5 Esbjerg", "Nygårdsvej 29, 1. 5, 6700 Esbjerg",
     {"adresse_is": "Nygårdsvej 29, 1. 5, 6700 Esbjerg"}),
    ("ejerlejlighed-hoved / Nygårdsvej 29 Esbjerg", "Nygårdsvej 29, 6700 Esbjerg",
     {"adresse_is": "Nygårdsvej 29, 6700 Esbjerg"}),
    ("erhverv-offentlig / Kongens Nytorv 1",      "Kongens Nytorv 1, 1050 København K",
     {"has_andel": False, "owner_contains": "undervisningsministeriet"}),
    ("almen-bolig / Mjølnerparken 2 KBH N",       "Mjølnerparken 2, kl., 2200 København N",   {"has_andel": False}),
    ("landbrug / Lindevej 14 Sorø",               "Lindevej 14, 4180 Sorø",                   {"has_andel": False}),
    ("sommerhus / Mentz Alle 8 Gilleleje",        "Mentz Alle 8, 3250 Gilleleje",             {"has_andel": False}),
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
                etage=r.etage,
                door=r.door,
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

        if "adresse_is" in checks and result.get("adresse") != checks["adresse_is"]:
            failures.append(f"adresse={result.get('adresse')!r} (expected {checks['adresse_is']!r})")

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
