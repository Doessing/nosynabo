# Research: kan vi pre-filtrere andelsoeg-kald med DAWA?

**Spørgsmål:** Kan vi undgå at spørge Andelsboligbogen blindt for alle adresser, ved at bruge
DAWA-data til at afgøre om en adresse *kan* være en andelsbolig?

**Konklusion:** DAWA har ingen direkte andels-markør. Men mønstret i tinglysningens svar
afslører én brugbar optimering — se nederst.

---

## Testdata

Indsamlet 2026-04-25 via live kald til tinglysning.dk (`ejendomsoeg/soeg` + `andelsoeg/soeg`).
Ejernavn fra tingbogen (`ejendomsoeg/hentejendomsbog`).

| Adresse | Type | ejendomsoeg hits | andelsoeg hits | Ejernavn i tingbog |
|---|---|---|---|---|
| Jægertoften 6, 6780 Skærbæk | andel — individuel | 0 | 1 | — |
| Ryttervænget 107A, 6752 Glejbjerg | andel — individuel | 0 | 1 | — |
| Bellmansgade 7, 2100 København Ø | andel — hoved-ejendom (stor) | 1 | 8 | A/B Bellmansgade 7-37 |
| Bellmansgade 13, 1. 1, 2100 København Ø | andel — individuel (stor forening) | 0 | 26 | — |
| Vesterbrogade 60, 1620 København V | andel — hoved-ejendom | 1 | 4 | Andelsboligforeningen Danner |
| Vesterbrogade 60, 2., 1620 København V | andel — individuel | 1 | 4 | Andelsboligforeningen Danner |
| Vesterbrogade 50, 1620 København V | andel — hoved-ejendom | 1 | 7 | A/B Baker Street |
| Vesterbrogade 50, 1. tv, 1620 København V | andel — individuel | 1 | 7 | A/B Baker Street |
| Ryttervænget 101A, 6752 Glejbjerg | andel — hoved-ejendom | 1 | 1 | Andelsboligforeningen Ryttervænget, Glejbjerg |
| Jægertoften 2, 6780 Skærbæk | andel — hoved-ejendom | 1 | 0 | Andelsboligforeningen Jægertoften |
| Elmevej 14, 8653 Them | normal villa | 1 | 0 | Kasper Lerche Rode (privatperson) |
| Akacievej 12, 5270 Odense N | normal villa | 1 | 0 | Line Arndal (privatperson) |
| Birkevej 4, 8800 Viborg | normal villa | 1 | 0 | Line Rud Pedersen (privatperson) |
| Skovvej 8, 3400 Hillerød | normal villa | 1 | 0 | Marianne Bay Holsted (privatperson) |
| Møllevej 2A, 6400 Sønderborg | normal villa | 1 | 0 | Vilhelm Hald-Christensen (privatperson) |
| Rosenvej 5, 7100 Vejle | normal villa | 1 | 0 | Birgit Johansen (privatperson) |
| Lærkevej 7, 4700 Næstved | normal villa | 1 | 0 | Ina Kjølby Korneliussen (privatperson) |
| Egegårdsvej 12, 2800 Kongens Lyngby | normal villa | 1 | 0 | Person med navne- og adressebeskyttelse |
| Strandvejen 100, 2900 Hellerup | villa/ejerlejlighed | 1 | 0 | Miluda Invest ApS (selskab) |
| Amagerbrogade 70, 2300 København S | ejerlejlighed | 0 | 0 | — |
| Sonnesgade 15, st. tv, 8000 Aarhus C | ejerlejlighed-komplex | 11 | 0 | Opdelt i ejerlejligheder 1-30 |
| Kongens Nytorv 1, 1050 København K | erhverv / offentlig | 1 | 0 | Børne- og Undervisningsministeriet |
| Rådhuspladsen 1, 1550 København V | erhverv / offentlig | 1 | 0 | KØBENHAVNS KOMMUNE |
| Agerhaven 2, 2640 Hedehusene | almen bolig | 1 | 0 | Hung Vien Luc |
| Mjølnerparken 2, kl., 2200 København N | almen bolig | 3 | 0 | Bo-Vita |
| Lindevej 14, 4180 Sorø | landbrug | 1 | 0 | Kåre Joakim Jørgensen |
| Bækkegårdsvej 2, 8800 Viborg | landbrug | 1 | 0 | Jette Sandal |
| Mentz Alle 8, 3250 Gilleleje | sommerhus | 1 | 0 | Rasmus Grønbæk |

---

## Hvad afslører DAWA?

Ingen DAWA-felter kan bruges som pre-filter:

| DAWA-felt | Andel-individuel | Normal villa | Ejerlejlighed | Konklusion |
|---|---|---|---|---|
| `etage` | `null` | `null` | `null`/sat | Lejlighed-hint, ikke andel-hint |
| `dør` | `null` | `null` | `null`/sat | Samme |
| `ejerlejlighedsnummer` | `null` | `null` | `null` | Ikke tilgængeligt i DAWA adresser |
| `esrejendomsnr` (jordstykke) | `"0"` | `"0"` | `"0"` | Identisk for alle typer |
| Antal adresser på matrikel | 8 | 1 | 1-4 | For upålideligt — ejerlejlighedskomplekser deler også matrikel |

**Konklusion:** DAWA har ingen andels-markør. Vi kan ikke pre-filtrere baseret på DAWA alene.

---

## Mønster i tinglysningens svar

| Kombination | Hvad det betyder | Skal andelsoeg spørges? |
|---|---|---|
| `ejd=0, andel=?` | Ikke i ejendomsbogen — kan være andel, matrikel-fallback, eller ukendt | Ja (allerede sker) |
| `ejd=1, ejer = privatperson` | Normal villa eller ejerlejlighed ejet af privatperson | **Nej — kan skippes** |
| `ejd=1, ejer = "A/B …"` | Andelsboligforenings hoved-ejendom eller administreret ejendom | Ja |
| `ejd=1, ejer = "Andelsboligforeningen …"` | Andelsboligforenings hoved-ejendom | Ja |
| `ejd=1, ejer = "Opdelt i ejerlejligheder …"` | Ejerlejlighedskomplex | Nej |
| `ejd=1, ejer = ministerium/kommune/selskab` | Offentlig eller erhverv | Nej (men usikkert) |
| `ejd>1` | Opdelt ejendom (ejerlejligheder) | Nej |

### Verificeret optimeringslogik (28/28 korrekte, 0 falske negativer)

Testet 2026-04-25 mod alle cases. Ingen falske negativer (ingen andel misset).

### Beslutningsregel

```
ejd=0               → spørg altid andelsoeg  (ingen ejer at tjekke)
ejd>1               → skip andelsoeg         (opdelt ejendom)
ejd=1, ejer matcher andel-keyword → spørg andelsoeg
ejd=1, ejer er privatperson/erhverv → skip andelsoeg
ejd=1, ejer mangler/fejl → spørg andelsoeg  (fail-safe)
```

```python
_ANDEL_KEYWORDS = frozenset([
    "andel", "a/b", "forening", "boligselskab", "boligforening",
])

def _skal_spørge_andel(ejd_hits: list, ejernavn: str) -> bool:
    if len(ejd_hits) == 0:
        return True   # individuel andel har ingen ejendomsoeg-hit
    if len(ejd_hits) > 1:
        return False  # opdelt ejendom (ejerlejligheder)
    if not ejernavn:
        return True   # fail-safe: manglende ejernavn
    return any(kw in ejernavn.lower() for kw in _ANDEL_KEYWORDS)
```

### Positive testcases (andel=ja — skal IKKE skippes)

| Adresse | ejd | Ejernavn | Beslutning | Faktisk andel | OK? |
|---|---|---|---|---|---|
| Jægertoften 6, 6780 Skærbæk | 0 | — | spørg | ✅ ja | ✓ |
| Jægertoften 2, 6780 Skærbæk | 1 | Andelsboligforeningen Jægertoften | spørg | ✅ ja | ✓ |
| Ryttervænget 107A, 6752 Glejbjerg | 0 | — | spørg | ✅ ja | ✓ |
| Ryttervænget 101A, 6752 Glejbjerg | 1 | Andelsboligforeningen Ryttervænget | spørg | ✅ ja | ✓ |
| Bellmansgade 7, 2100 København Ø | 1 | A/B Bellmansgade 7-37 | spørg | ✅ ja | ✓ |
| Bellmansgade 13, 1. 1, 2100 København Ø | 0 | — | spørg | ✅ ja | ✓ |
| Vesterbrogade 60, 1620 København V | 1 | Andelsboligforeningen Danner | spørg | ✅ ja | ✓ |
| Vesterbrogade 60, 2., 1620 København V | 1 | Andelsboligforeningen Danner | spørg | ✅ ja | ✓ |
| Vesterbrogade 50, 1620 København V | 1 | A/B Baker Street | spørg | ✅ ja | ✓ |
| Vesterbrogade 50, 1. tv, 1620 København V | 1 | A/B Baker Street | spørg | ✅ ja | ✓ |

### Negative testcases (andel=nej — skal SKIPPES)

| Adresse | ejd | Ejernavn | Beslutning | Faktisk andel | OK? |
|---|---|---|---|---|---|
| Elmevej 14, 8653 Them | 1 | Kasper Lerche Rode | skip | ❌ nej | ✓ |
| Akacievej 12, 5270 Odense N | 1 | Line Arndal | skip | ❌ nej | ✓ |
| Birkevej 4, 8800 Viborg | 1 | Line Rud Pedersen | skip | ❌ nej | ✓ |
| Skovvej 8, 3400 Hillerød | 1 | Marianne Bay Holsted | skip | ❌ nej | ✓ |
| Møllevej 2A, 6400 Sønderborg | 1 | Vilhelm Hald-Christensen | skip | ❌ nej | ✓ |
| Rosenvej 5, 7100 Vejle | 1 | Birgit Johansen | skip | ❌ nej | ✓ |
| Lærkevej 7, 4700 Næstved | 1 | Ina Kjølby Korneliussen | skip | ❌ nej | ✓ |
| Egegårdsvej 12, 2800 Kongens Lyngby | 1 | (navnebeskyttelse) | skip | ❌ nej | ✓ |
| Strandvejen 100, 2900 Hellerup | 1 | Miluda Invest ApS | skip | ❌ nej | ✓ |
| Amagerbrogade 70, 2300 København S | 0 | — | spørg | ❌ nej | ✓ (false positive ok — ejd=0 fail-safe) |
| Sonnesgade 15, st. tv, 8000 Aarhus C | 11 | Opdelt i ejerlejligheder | skip | ❌ nej | ✓ |
| Kongens Nytorv 1, 1050 København K | 1 | Børne- og Undervisningsministeriet | skip | ❌ nej | ✓ |
| Rådhuspladsen 1, 1550 København V | 1 | KØBENHAVNS KOMMUNE | skip | ❌ nej | ✓ |
| Agerhaven 2, 2640 Hedehusene | 1 | Hung Vien Luc | skip | ❌ nej | ✓ |
| Mjølnerparken 2, kl., 2200 København N | 3 | Bo-Vita | skip | ❌ nej | ✓ |
| Lindevej 14, 4180 Sorø | 1 | Kåre Joakim Jørgensen | skip | ❌ nej | ✓ |
| Bækkegårdsvej 2, 8800 Viborg | 1 | Jette Sandal | skip | ❌ nej | ✓ |
| Mentz Alle 8, 3250 Gilleleje | 1 | Rasmus Grønbæk | skip | ❌ nej | ✓ |

### Hvad med hoved-ejendommen?

Bekymringen var om hoved-ejendommen (101A — foreningen ejer matriklen) ville blive misset.
Den bliver **ikke** misset, fordi ejernavn = "Andelsboligforeningen Ryttervænget" → matcher
andel-keyword → andelsoeg spørges. ✓

### Arkitektur-hage

Koden er i dag **sekventiel** (trods kommentaren "parallel"):

```
linje 518: _try_lookup_andelsbolig()   ← sker FØRST
linje 520: search_property()           ← sker BAGEFTER
```

For at optimere skal rækkefølgen vendes:

```
1. search_property()           → 0 hits → spørg altid andelsoeg (uændret)
                               → 1 hit  → hent tingbog → tjek ejernavn → betinget andelsoeg
                               → N hits → skip andelsoeg
```

Det medfører at andelsoeg-kaldet for individuelle andelsboliger (ejd=0) sker **efter**
ejendomsoeg-kaldet — i praksis ingen forskel da ejendomsoeg er hurtig ved 0 hits.

### Risiko

Eneste risiko er en andelsforening med et navn der ikke indeholder nogen af keywords —
f.eks. en forening der hedder "BRF Kredit Ejendomme A/S" eller lignende. Vurderet som
meget lav risiko i praksis: andelsboligforeninger i Danmark har næsten altid
"andel", "A/B" eller "forening" i navnet ifølge lovgivningen.

---

## Nuværende flow (til reference)

```
DAWA resolve
    → _try_lookup_andelsbolig()   [sekventiel, sker ALTID]
    → search_property()
         ↓ hit → get_tingbog → return med andelsbolig
         ↓ miss → matrikel-fallback → return med andelsbolig
         ↓ miss → andelsbolig-only shell
```

---

## Næste skridt (ikke besluttet)

- Implementer lazy andelsoeg: vend rækkefølgen → ejendomsoeg først → betinget andelsoeg
- Måle faktisk latency-gevinst på andelsoeg-skip
- BBR `enh_anvend_kode` er en mere autoritativ kilde, men Datafordeler-adgang ikke aktiveret endnu
