# EDEN Explorer

React + TypeScript dashboard for the EDEN public API. This directory contains the application's frontend, including API documentation and the public data workspace. Shared setup and deployment commands are documented in the [repository README](../README.md).

## Run

From this directory with Node 24, as used by the repository setup:

```sh
npm ci
npm run dev
```

Local URL: `http://127.0.0.1:5173`. Port is strict to avoid silently serving a different checkout.

```sh
npm test
npm run build
```

The static build is written to `dist/`. In development, public API requests use a same-origin Vite proxy to `https://api.edenapi.org`; static builds default to `https://api.edenapi.org/v1`. To use another backend, set `VITE_EDEN_API_URL` in the repository root `.env` (see [.env.example](.env.example)) and restart Vite. Vite loads environment files from the repository root, not this directory. Vite-prefixed values are public browser configuration; never put secrets there.

## Implemented flows

- API documentation, endpoint parameters, response fields and request examples.
- Public data workspace with source-to-response field connections and related API queries.
- Region picker, 7/30/90-day insights, daily visitor history, 7-day demand reference, regional properties.
- Five inbound markets with country filters, sorting, metric/source inspection, and official notices.
- YouTube keyword query, regional search filter, source metrics, series, rising keywords when available.
- Place list by province or sigungu (`GET /v1/places`) with title language, search and paging.
- Place detail, translation fallback, related places, nearby shops, external OpenStreetMap link when coordinates exist.
- Filters and place selection in the URL; browser back/forward restores selection. Narrow layouts use Blueprint drawers for navigation and inspection.

## Data integrity

Only actual API responses populate the UI. No sample data or invented statistics are shipped. Null values display as `—`; real zero remains `0`. Charts break across missing observations. Each resource exposes its own date, availability and source information. Query changes clear the previous result immediately; aborted/late responses cannot overwrite the current selection. Requests time out after 15 seconds and expose a manual retry. HTTP errors with non-JSON bodies are handled.

Region codes derive from the explicit MOIS mapping in `api/app/sources/plans.py`. The list describes selectable regions, not guaranteed data coverage. Places are listed through `GET /v1/places` and opened by `content_id`. Public API requests add no credentials, cookies, background polling, writes, or LLM calls.

The region map uses the simplified 2020 nationwide province boundary SVG from [StatGarten's korea-maps repository](https://github.com/swcho/korea-maps), which was collected through the Statistics Korea SGIS Open API. The vendored asset retains its [MIT license](src/assets/korea-sido.LICENSE). The split map, selection and time-series layout follows Palantir Foundry's documented [Map application interface](https://www.palantir.com/docs/foundry/map/getting-started).

`document.modelContext` tools are feature-detected for navigation and selection read-back. They never claim a data query has completed. Unsupported browsers use the same normal UI. The contract is unit-tested; a browser with native WebMCP support is needed to verify its host integration.

## Validation boundary

Build/type checks and automated data/rendering tests are available above. Tests use synthetic responses and do not prove live API availability or browser interaction behavior. During initial implementation, the public API returned a gateway timeout and timed out on another request; the frontend displays these connection states without substituting fixtures. Browser visual/interaction QA and deployment are separate from these checks.

## Public data workspace

The workspace uses `src/explorer/catalog.json` and `src/explorer/publicModel.ts` to show sources, processing routes and public API response fields. Selecting a field highlights its connected path. Graph positions are stored locally in the browser. The data panel queries public endpoints and shows their response data and source metadata; it does not browse internal database rows or fetch recorded row lineage.

The API still provides an optional authenticated internal explorer, and the Vite configuration retains its server-side proxy settings. These settings are separate from the current public data workspace. Shared connection and catalog export commands are documented in the repository README.
