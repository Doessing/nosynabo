# Feature tracks (working names)

This file defines stable feature names so we can refer to the same scope in PRs,
issues, and chat without ambiguity.

## F1 - Kortkildevaelger + matrikeloverlay

Short name: `mapstack`

Scope:
- Multiple selectable basemaps in the Leaflet UI (OSM + optional alternatives).
- Draw all matrikler found via a lookup as polygon overlays on the map.
- Keep overlays tied to result entries (color/grouping, show/hide, cleanup on remove).

Why:
- Makes the existing hierarchical matrikel search visible and easy to understand.
- Creates a clear differentiator: visualizing all related matrikler at once.

## F1.5 - Energidata via BFE

Short name: `energi-bfe`

Scope:
- Add energy report enrichment (energimaerke) keyed by `bfeNummer`.
- Present results in a dedicated card/section with links and key metadata.

Notes:
- Treated as a separate feature from F1.
- Prefer stable/official data interfaces over fragile HTML scraping.

## F1.6 - Matrikel-first map clicks (MERGED #15)

Short name: `matrikel-click`

Scope:
- Map clicks resolve via the matrikel under the cursor (`/api/click` -> DAWA
  `jordstykker/reverse`) instead of the legacy 500m haversine reverse-geocoder.
  Eliminates the case where a click on parcel A returned an address on
  neighbouring parcel B.
- New `/api/lookup-matrikel` drives tingbog lookup directly from
  `(matrikelnr, ejerlavskode)`, with two fallbacks:
  - SFE fallback: walk sibling parcels sharing `sfeejendomsnr` when the
    clicked parcel has no own adgangsadresse (mark/skov/faelleslod).
  - OIS deeplink card for genuine BFE-isolerede matrikler (fx 37b Magleby,
    3v Skovbølling) where tinglysningens offentlige REST is structurally
    unreachable.
- Andelsbolig enrichment on click: when a forening-tingbog comes back, the
  click coordinates are reverse-geocoded to the nearest adgangsadresse on
  the parcel and andelsoeg is queried for that specific dwelling. Restores
  per-flat lookup parity with the address flow.
- Boligsiden sales-history is fetched on click as well, mirroring the
  address flow.
- Robusthed: transient tinglysning-fejl (connection reset / 5xx) overflades
  som 502/504 saa frontend's eksisterende auto-retry + "Proev igen"-knap
  aktiveres - tidligere blev de fejltolket som "matrikel uden tingbog".
  `_get_json` nulstiller cached ALTCHA-token paa ConnectionError foer retry.

Address flow (typed search) is unchanged.

## F1.7 - GPS locate + grundareal + sales-table overflow

Short name: `gps-grundareal`

Scope:
- Add a GPS locate button in the search row that resolves the user's current
  position via `/api/reverse` and performs a normal address lookup.
- Add inline user feedback for geolocation states (locating, permission
  denied, timeout, no nearby address) and loading state on the GPS button.
- Enrich tingbog responses with parcel area:
  - `matrikler[].areal_m2` from DAWA `jordstykker`
  - aggregate `grundareal_m2` only when all matrikler have known area (no
    partial totals presented as exact).
- UI rendering:
  - show `Grundareal` in the valuation card when available
  - show matrikel area in the matrikler table
- Fix mobile horizontal overflow in sales-history tables for large
  multi-million prices by moving currency unit to header and tightening
  responsive table behavior.

## F2 - Historiske ejere (auth bridge)

Short name: `owner-history`

Scope:
- Investigate and (later) integrate historical owner/conveyance data from
  authenticated tinglysning endpoints.

Current findings:
- `rest/ejdhistoriskadkomst/uuid/<uuid>` is MitID-gated.
- Anonymous `unsecrest` access does not expose historical owners.
- UUIDs are stable across `unsecrest` and `rest`, enabling a future bridge flow.

## F2 colors - Design system + self-hosted font

Short name: `colors-f2` (MERGED #13)

Scope:
- Introduce CSS custom-property design tokens with two themes:
  Copper Slate (light) and Slate Dusk (dark).
- Replace ad-hoc inline color/background styles with semantic helper classes.
- Self-host Inter variable font (woff2 upright + italic) - no Google Fonts in prod.
- Subtle in-palette special card variants for Andelsbolig and paraplyejendom.

Follow-up: `cleanup-f2-followup` (PR #14)
- Remove dead CSS, replace remaining inline styles, tighten dark muted contrast.
- XSS hardening: escape creditor names in mortgage rendering.
- Autocomplete: lower threshold (3 to 2 chars), shorter debounce, vejnavn
  refinement flow, race-safety via AbortController + sequence-id,
  stale-keep-alive retry on DAWA, Cache-Control on /api/autocomplete.

## Future request - Luftfoto/satellit basemap

Short name: `aerial-basemap`

Scope:
- Add one or more aerial/satellite basemap options to mapstack when we have a
  stable source and acceptable terms.

Notes:
- Requested while finalizing F1 mapstack.
- Keep current F1 baseline at two options: `Standard kort` and
  `Topografisk kort`.
