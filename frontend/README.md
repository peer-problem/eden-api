# EDEN Explorer

React + TypeScript frontend for the existing EDEN public API. An optional authenticated internal API supports database browsing; no database schema changes are required.

## Run

From this directory with Node 22.12+ (or supported Node 20.19+):

```sh
npm ci
npm run dev
```

Local URL: `http://127.0.0.1:5173`. Port is strict to avoid silently serving a different checkout.

```sh
npm test
npm run build
```

The static build is written to `dist/`. In development, public API requests use a same-origin Vite proxy to `https://api.edenapi.org`; static builds default to `https://api.edenapi.org/v1`. To use another backend, set `VITE_EDEN_API_URL` in `.env.local` (see `.env.example`) and restart Vite. Vite-prefixed values are public browser configuration; never put secrets there.

## Implemented flows

- Region picker, 7/30/90-day insights, daily visitor history, 7-day demand reference, regional properties.
- Five inbound markets with country filters, sorting, metric/source inspection, and official notices.
- YouTube keyword query, regional search filter, source metrics, series, rising keywords when available.
- Destination recommendations by market, region, season and theme; unapplied inputs remain visible.
- Place detail, translation fallback, related places, nearby shops, external OpenStreetMap link when coordinates exist.
- Filters and place selection in the URL; browser back/forward restores selection. Narrow layouts use Blueprint drawers for navigation and inspection.

## Data integrity

Only actual API responses populate the UI. No sample data or invented statistics are shipped. Null values display as `—`; real zero remains `0`. Charts break across missing observations. Each resource exposes its own date, availability and source information. Query changes clear the previous result immediately; aborted/late responses cannot overwrite the current selection. Requests time out after 15 seconds and expose a manual retry. HTTP errors with non-JSON bodies are handled.

Region codes derive from the explicit MOIS mapping in `app/sources/plans.py`. The list describes selectable regions, not guaranteed data coverage. The API has no public region-list or general place-search route; places are discovered through recommendations and related-place links. Public API requests add no credentials, cookies, background polling, writes, or LLM calls. Internal database browsing uses a server-side bearer token.

The region map uses the simplified 2020 nationwide province boundary SVG from [StatGarten's korea-maps repository](https://github.com/swcho/korea-maps), which was collected through the Statistics Korea SGIS Open API. The vendored asset retains its [MIT license](src/assets/korea-sido.LICENSE). The split map, selection and time-series layout follows Palantir Foundry's documented [Map application interface](https://www.palantir.com/docs/foundry/map/getting-started).

`document.modelContext` tools are feature-detected for navigation and selection read-back. They never claim a data query has completed. Unsupported browsers use the same normal UI. The contract is unit-tested; a browser with native WebMCP support is needed to verify its host integration.

## Validation boundary

Build/type checks and automated data/rendering tests are available above. Tests use synthetic responses and do not prove live API availability or browser interaction behavior. During initial implementation, the public API returned a gateway timeout and timed out on another request; the frontend displays these connection states without substituting fixtures. Browser visual/interaction QA and deployment are separate from these checks.

## Database workspace

See [local connection instructions](CONNECTION.md). The workspace places 26 explicitly allowed table definitions, external sources, code-backed processing routes, actual rows and recorded lineage in one view. Table nodes show their columns, types, PKs and FKs. Selecting a published snapshot expands its recorded normalized inputs and raw provenance onto the same React Flow graph. Exact-match filters, pagination and record inspection remain available below the graph. Schema definitions are exported with `uv run python -m scripts.export_explorer_catalog`; live source state and rows come from the authenticated internal API.

The internal API is disabled by default. DB queries are read-only, bounded and time-limited. Payload bodies and sensitive operational details are omitted. Authentication, query boundaries and reference semantics can be checked without any database using `uv run python -m unittest discover -s tests -p test_explorer.py` from the repository root.
