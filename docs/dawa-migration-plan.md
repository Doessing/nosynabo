# DAWA lukker - migrationsplan for nosynabo

> Status: UDKAST til beslutning. Skrevet 2026-09-04, opdateret efter POC-runde 2 samme dag.
> Scope: kun `nosynabo`. `vurderingsaudit` er bevidst udskudt til nosynabo er i mål.

---

## 1. Situationen

| Dato | Hændelse |
|---|---|
| 2026-06-30 | DAGI, **Matriklen** og GeoDanmark stoppede med at blive opdateret i DAWA |
| 2026-08-26 | Adressevask blev tilgængelig |
| 2026-09-02 | Officiel DAWA-melding opdateret med endelig lukkedato |
| **2026-10-01 kl. 10:00** | **DAWA lukker. Hårdt stop.** Adresse-WFS lukker samtidig |
| 2027-01-15 | Datafordelerens paralleldrift ophører (legacy REST/filedownload) - separat deadline |

Kilde: https://dataforsyningen.dk/data/4924 (opdateret 2026-09-02)

**Vi har 27 dage.** Ingen annonceret read-only-fase, ingen throttling-fase, ingen grace period.

### Allerede aktivt problem

Matrikeldata i DAWA har ikke været opdateret siden 30. juni. Vores matrikelnumre, ejerlav, arealer og BFE-numre er **allerede over to måneder forældede i produktion**. Migrationen retter det som sidegevinst.

---

## 2. POC-resultat (2026-09-04)

Servicebrugeren `nosy-web`s eksisterende API-nøgle **virker allerede** mod Datafordelerens moderniserede GraphQL-gateway. Ingen tjenesteaktivering var nødvendig:

```
https://graphql.datafordeler.dk/flexibleCurrent/v1?apikey=<KEY>
```

Gatewayen eksponerer **DAR, MAT (Matriklen), EJF, BBR, CVR, CPR og DAGI i ét skema**.

Reproducerbar POC: `tools/dafo_poc.py` (læser `DATAFORDELER_API_KEY` fra miljøet).

### 2.1 Adressekæden - verificeret

`DAR_Husnummer` har et **`jordstykke`-felt** der peger direkte på `MAT_Jordstykke.id_lokalId`. Adresse → matrikel er et native join, ikke noget vi skal genopbygge.

```
"Rosenvangs Allé 228A"
  -> DAR_Husnummer      jordstykke=386831, adgangspunkt=721dd1ef-...
  -> MAT_Jordstykke     matrikelnummer=11s, ejerlav=1031351, areal=825, SFE=4252550
  -> MAT_Ejerlav        "Holme By, Skåde"
  -> DAR_Adressepunkt   POINT (574658.57 6219743.76), crs=25832
```

DAWA's facit for samme adresse: `11s` / `1031351` / "Holme By, Skåde". **1:1 match. 464 ms** for hele kæden uden cache.

Det omvendte join - `DAR_Husnummer(where:{jordstykke:{eq:...}})` - giver alle adresser på en matrikel og erstatter `/adgangsadresser?ejerlavkode=&matrikelnr=` direkte.

### 2.2 Punkt-i-polygon - LØST

Dette var projektets identificerede hovedrisiko. Den er væk.

`MAT_Jordstykke` har ingen geometri og kan ikke filtreres rumligt. Men **`MAT_Lodflade` har både `geometri` og `jordstykkeLokalId`**, og gatewayen understøtter fulde spatiale operatorer:

```
contains, covers, crosses, intersects, within, overlaps, touches, coveredBy
```

Ét kald erstatter `/jordstykker/reverse`:

```graphql
MAT_Lodflade(virkningstid:"...", first:1,
  where:{ geometri:{ contains:{ wkt:"POINT (574658.57 6219743.76)", crs:25832 } } })
{ nodes{ jordstykkeLokalId geometri{ crs wkt } } }
```

Samme kald returnerer **polygonen** - så matrikeloverlay på kortet er dækket samtidig. `MAT_Lodflade` gør både `/jordstykker/reverse` og `/jordstykker?format=geojson` overflødige.

Verificeret mod DAWA på 6 punkter, **6/6 match**:

| Case | GraphQL | DAWA | ms |
|---|---|---|---|
| Aarhus villa | 11s/1031351 | 11s/1031351 | 217 |
| Kbh indre by | 31/2000163 | 31/2000163 | 196 |
| Landbrug Jylland | 4c/800154 | 4c/800154 | 191 |
| Sommerhus vestkyst | (ingen) | (ingen) | 121 |
| Uldum (kendt svær) | 1l/1121852 | 1l/1121852 | 220 |
| Midt i Storebælt | (ingen) | (ingen) | 114 |

Fuldt kortklik-flow (punkt → matrikel → areal → BFE → polygon) på **221 ms**.

### 2.3 WFS er ikke en vej

Matriklen WFS svarer **HTTP 503: "This service has been deliberately (and temporarily) taken out of service."** Både `MATRIKLEN2` og `Matriklen2`. Vi bygger ikke på den.

Det er også irrelevant nu - GraphQL løser opgaven bedre.

### 2.4 Konsekvenser for koden

- **Ingen PostGIS-komponent nødvendig.** Ingen lokal kopi, intet importjob, ingen ny database.
- **`pyproj` skal blive.** Gatewayen afviser `crs:4326` på MAT-felter (`DAF-GQL-0011`). Kortklik skal projiceres til EPSG:25832 før forespørgsel, og polygonen tilbage til 4326 for Leaflet. Det gør vi allerede.
- `samletFastEjendomLokalId` er samme værdi som DAWA's `bfenummer` **og** `sfeejendomsnr` - verificeret.
- Koordinater kommer i EPSG:25832, præcis det skraafoto-deeplinket kræver.

### 2.5 Sidegevinst

`EJF_Ejerskab` og `EJF_PersonVirksomhedsoplys` ligger på samme gateway med samme nøgle. Det er `owner-history`-featuren vi har haft på hold siden Find Ejeren-analysen. Ikke en del af dette projekt, men blokeringen er væk.

### 2.6 Det eneste tilbageværende hul

**Fuzzy typeahead.** GraphQL har kun `eq` og `startsWith` på `adgangsadressebetegnelse`, og det er versalfølsomt. Det er ikke nok til søgefeltet. Adressevælger er den officielle afløser - og den eneste tilbageværende ukendte i projektet.

---

## 3. Kodegennemgang - hvad rammer os

13 aktive DAWA-kaldsteder i 3 filer. Frontend kalder aldrig DAWA direkte; alt går via vores egne `/api/*`-ruter. **Hele migrationen kan ske server-side uden at ændre API-kontrakten mod browseren.**

### 3.1 Adresseopslag - `resolver.py`

| Linje | Kald | Erstatning | Vurdering |
|---|---|---|---|
| 115-127 | `/autocomplete` (fuzzy) | Adressevælger | **Middel - eneste reelle ukendte** |
| 156-161 | `/adresser?postnr&vejnavn&husnr` | `DAR_Husnummer` | Lav - POC-bevist |
| 172-197 | `/adresser/{id}` + 404-fallback | `DAR_Husnummer` på `id_lokalId` | Lav |

Alle felter vi forbruger er dækket af POC-kæden. `etage`/`dør` ligger på `DAR_Adresse` frem for `DAR_Husnummer`.

### 3.2 Matrikel og geometri - `server.py`

| Linje | Kald | Erstatning | Vurdering |
|---|---|---|---|
| 138 | `/jordstykker` (areal) | `MAT_Jordstykke.registreretAreal` | Lav - POC-bevist |
| 312-315 | `/adgangsadresser/reverse` (GPS) | `MAT_Lodflade` → `DAR_Husnummer` | Lav |
| 367-370 | `/jordstykker/reverse` (kortklik) | `MAT_Lodflade.geometri.contains` | Lav - POC-bevist |
| 401-408 | `/jordstykker` (ejerlavsnavn) | `MAT_Ejerlav` | Lav - POC-bevist |
| 628-631 | `/jordstykker` (GeoJSON polygon) | `MAT_Lodflade.geometri` | Lav - POC-bevist |

### 3.3 Matrikel → adresse - `nosy_nabo.py`

| Linje | Kald | Erstatning | Vurdering |
|---|---|---|---|
| 360 | `dawa.aws.dk/autocomplete` (legacy CLI) | Slettes - død kode i webflowet | Ingen |
| 733-741 | `/adgangsadresser?ejerlavkode&matrikelnr` | `DAR_Husnummer(where:{jordstykke})` | Lav - POC-bevist |
| 800-807 | `/jordstykker` → `sfeejendomsnr` | `MAT_Jordstykke.samletFastEjendomLokalId` | Lav - POC-bevist |
| 819-823 | `/jordstykker?sfeejendomsnr` | `MAT_Jordstykke(where:{samletFastEjendomLokalId})` | Lav |
| 835-843 | `/adgangsadresser` på søster-matrikler | Som 733-741 | Lav |

### 3.4 Test og dokumentation

- `tests/regression.py` (27 cases) og `tests/parity_matrikel.py` kører **live mod DAWA uden mocks**. Når DAWA lukker, dør også tinglysning-testdækningen.
- `README.md`, `docs/feature-tracks.md`, `docs/andelsoeg-prefilter-research.md` beskriver DAWA-flows gennemgående.
- CSP ligger i nginx uden for repoet (host2 + webhost1) og skal have `graphql.datafordeler.dk` - eller ingenting, hvis alle kald forbliver server-side. Adressevælger kan kræve en frontend-post.

### 3.5 Ikke berørt

`api.dataforsyningen.dk/orto_foraar_DAF` (WMS satellitkort) og `skraafoto.dataforsyningen.dk` er andre tjenester på samme host og fortsætter.

---

## 4. Vurdering af opgavens størrelse

Efter POC-runde 2:

| Kategori | Andel | Karakter |
|---|---|---|
| Mekanisk oversættelse til GraphQL | ~65% | Lav risiko, POC-bevist |
| Adressevælger-typeahead | ~10% | Eneste reelle ukendte |
| Testinfrastruktur, docs, drift | ~25% | Kedeligt, men blokerende |

Det er **ikke** et arkitekturprojekt. Det er en veldefineret oversættelsesopgave med én ukendt. 27 dage er rigeligt, forudsat vi starter fixture-optagelsen nu.

Vi kommer ud på den anden side med **færre eksterne afhængigheder** (én gateway i stedet for DAWA + Datafordeler-planer), **friske matrikeldata** og EJF inden for rækkevidde.

---

## 5. Faseplan

### Fase 0 - Adgang (afsluttet, minus én ting)

- API-nøglen virker mod DAR, MAT og EJF. Bekræftet.
- Rest: **skaf Adressevælger-token** og bekræft rate limits hos support@kds.dk. Skemaet har `@cost`-vægte (spatiale operationer koster 5000), hvilket antyder et kvotesystem vi skal kende før vi sætter caching-strategi.

### Fase 1 - Afkobl testsuiten fra DAWA (START NU)

Optag nuværende DAWA-svar for alle 27 regressionscases som fixtures, **mens DAWA stadig kører**. Efter 1. oktober kan de ikke genskabes. Det er facitlisten til fase 4 og tidskritisk uafhængigt af alt andet.

### Fase 2 - Byg abstraktionslag

Ét adresse-/matrikel-interface som `resolver.py`, `server.py` og `nosy_nabo.py` kalder. To implementeringer bag: DAWA og Datafordeler, skiftbar via env-variabel. Gør migrationen trinvis og rullebar tilbage.

### Fase 3 - Implementér Datafordeler-backend

1. Løft `tools/dafo_poc.py` til en rigtig klient med timeouts, retry og caching
2. Adresseopslag, matrikeljoins, areal, SFE, søster-matrikler
3. Kortklik + polygon via `MAT_Lodflade`
4. Adressevælger til typeahead

Punkt 1-3 er POC-bevist. Punkt 4 er den eneste rigtige udvikling.

### Fase 4 - Parallelkørsel og verifikation

Kør begge implementeringer mod de 27 cases og sammenlign felt for felt mod fase 1-fixtures. Afvigelser skal forklares - ikke accepteres. Nogle afvigelser er **forventede og korrekte**, fordi DAWA's matrikeldata har været frosset siden 30. juni.

Manuel browsertest: adressesøgning, kortklik, GPS-knap, polygonvisning.

### Fase 5 - Udrul

Dev først, så prod. Nøglen ind i `/etc/nosynabo/secrets.env` - **aldrig i repoet**, det er offentligt og AGPL. nginx-CSP hvis nødvendigt. README, docs, projektkontekst og wiki i samme PR.

---

## 6. Risici

| Risiko | Konsekvens | Håndtering |
|---|---|---|
| Adressevælger umoden eller token svært tilgængeligt | Dårligere søgeoplevelse | `startsWith`-fallback med normaliseret input |
| Ukendte `@cost`-kvoter (spatial = 5000/kald) | Prod fejler under last | Afklar skriftligt. Cache kortklik aggressivt |
| API-nøgle udløber 2028-04-20 | Total nedetid på sigt | Noteres i drift. Ikke akut |
| Nøgle lækket i offentligt repo | Kompromittering | Kun env. Gitleaks kører i CI |
| Testsuiten dør med DAWA | Ingen regressionsdækning | Fase 1 først |
| Datafordeler tager tjenester offline | Jf. WFS 503 | GraphQL er den strategiske flade, men overvåg |

### Hvis vi ikke når det

1. **Adressesøgning** - uden den er appen ubrugelig. Skal virke.
2. **Kortklik** - nu POC-bevist og billig. Bør nås.
3. **GPS-knap** - lille, kan deaktiveres
4. **Grundareal** - kan udelades fra visningen

---

## 7. Åbne spørgsmål

1. Er Adressevælger-token gratis og offentligt udstedt?
2. Hvad er de faktiske rate limits og `@cost`-kvoter på gatewayen?
3. Skal `virkningstid` være "nu", eller vil vi kunne slå historik op? (gatewayen kræver parameteren - vi sender nu)

Spørgsmålet om PostGIS er bortfaldet.

---

## 8. Næste handling

1. **Start fase 1.** Optag fixtures mens DAWA kører. Tidskritisk.
2. Skriv til support@kds.dk om Adressevælger-token og rate limits.
3. Byg abstraktionslaget (fase 2) - kan køre parallelt med at vi venter på svar.

---

## 9. Beslutninger (ADR-review 2026-10-04)

Implementeret på `feat/dawa-to-datafordeler`. DAWA svarede `410 Gone`, så fase 1 (fixtures) og fase 2 (DAWA/Dafo-switch) udgik.

| ADR | Beslutning |
|---|---|
| 1 | Søgefelt via Dataforsyningen **gsearch v2** (`DATAFORSYNINGEN_TOKEN`); alt andet via **Datafordeler GraphQL** (`DATAFORDELER_API_KEY`). Adressevælger fravalgt indtil videre, se note |
| 2 | Ét klientmodul `dafo.py`. Ingen switch - intet at rulle tilbage til |
| 3 | API-kontrakten mod browseren er uændret |
| 4 | Egen lille WKT-parser (2D POINT/POLYGON/MULTIPOLYGON) frem for shapely - KISS |
| 5 | Resolver: gsearch, forkort bagfra ned til husnummer, vælg hit på husnr/postnr, join DAR -> MAT |
| 6 | Uden ciffer: ét forslag pr. vej + by (sorteret på postnr, maks 50 fra gsearch; bynavn indsnævrer). Valg giver `Vej , 1234 By` med markør efter vejnavnet. Med ciffer: adgangsadresser |
| 7 | GPS: `DAR_Adressepunkt within` i felter på 60/200/500 m |
| 8 | Kortklik: `MAT_Lodflade contains`. Punktopslaget caches ikke (giver ingen mening) |
| 9 | `DafoError` arver `requests.RequestException` -> 502 |
| 10 | Nøgler kun i env/server-side, `apikey` strippes fra fejl, GraphQL-literals escapes |
| 11 | Ingen DAWA-paritetstest; POC-verifikationen fra 2026-09-04 accepteres som facit |

Ingen henvendelse til KDS om `@cost`-kvoter.

**Note om Adressevælger (ADR-1):** `adressevaelger.dk` kræver en `token`-parameter, men har endnu ingen brugerstyring ("først tilgængelig efter DAWA er lukket"). Dokumentationens demo-token virker. Rigtigt token fås via support@kds.dk. API'et (`/adresser/soeg`) kræver strukturerede felter (`vejnavn`, `husnummer`) og sorterer efter postnummer, ikke relevans - gsearch er derfor bedre til fri tekst lige nu.

## 10. Long-term TODO

- [ ] **API-oprydning:** `esrejendomsnr` er nu altid `null`, og `bfenummer`/`sfeejendomsnr` er samme værdi. Fjern/saml i en senere PR (ADR-3)
- [ ] **Overvågning af tavse fejl:** konfigurationsfejl (fx manglende nøgle) og ændringer i gsearch/GraphQL-skema ligner bare upstream-udfald (502). Vi skal opdage det, når noget breaker - fx health-check eller alarm på 502-rate (ADR-9)
- [ ] **Adressevælger:** revurder når brugerstyring er åben; skaf token via support@kds.dk (ADR-1)
- [x] **/readme er brudt i prod** (rettet i fix/readme-csp) (siden 2026-08-20): prod-CSP blokerer `marked` fra jsdelivr og `fetch` til raw.githubusercontent.com. Fix: self-host `marked` i `static/vendor/` og server repoets egen `README.md` fra appen - ingen CSP-ændring. Separat PR
- [ ] **Dev mangler CSP:** `nosy.dossing.net` sender ingen CSP-header, så CSP-fejl ses kun i prod. Overvej prods CSP kun i nosy-vhosten på webhost1 (delt server - se projektkontekst)
- [ ] **DAWA-rester i CSP/preconnect:** prod `connect-src` og `<link rel="preconnect">` peger stadig på `api.dataforsyningen.dk`; satellitkortet går via `img-src`. Verificér og fjern
