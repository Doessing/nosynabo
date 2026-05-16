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
