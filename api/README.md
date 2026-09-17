<h1 align="center">EDEN API</h1>

<p align="center">
  <strong>Korean tourism data, in one API.</strong>
</p>

<p align="center">
  Explore Korean destinations and regional visitor patterns.<br>
  Compare inbound markets and explore tourism data.
</p>

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white">
  <img alt="MariaDB" src="https://img.shields.io/badge/MariaDB-003545?style=flat-square&logo=mariadb&logoColor=white">
  <img alt="API v0.3.0" src="https://img.shields.io/badge/API-v0.3.0-516B56?style=flat-square">
</p>

<p align="center">
  <a href="https://api.edenapi.org/docs"><strong>API Docs</strong></a> |
  <a href="https://api.edenapi.org/openapi.json">OpenAPI</a> |
  <a href="#quick-start">Quick Start</a> |
  <a href="CHANGELOG.md">Changelog</a>
</p>

<p align="center">
  <a href="assets/eden-api-overview.svg">
    <img src="../assets/eden-api-overview.svg" alt="Eight independent EDEN API endpoints grouped into discovery, analysis, and destination decisions, with shared response metadata." width="100%">
  </a>
</p>

---

## What It Does

EDEN connects regional codes and place identifiers across tourism data sources and exposes the results as consistent JSON. Use it to add place details, regional visitor analysis, and inbound market comparisons to your application.

| Feature | Available information |
| --- | --- |
| Travel trends | YouTube search samples for market keywords, NAVER search ratios for Korean travel keywords, and the official KTO resource demand index by attraction and area |
| Regional insights | Regional visitor indicators and comparisons with earlier periods |
| Place details | Names, overview, locations, available translations, hub ranking, nearby shops, and related places |
| Visitor outlook | Reference demand indices based on past observations, with available weather context |
| Visitor history | Daily, weekly, or monthly visitor indicators |
| Inbound markets | Visitor, flight, and exchange-rate indicators for Japan, China, Taiwan, the US, and the Philippines |
| Official alerts | Entry and safety notices with links to their sources |

> **Current deployment:** The public API reads published data while a bounded background worker collects and refreshes selected sources. Coverage varies by source. Check observation dates and freshness metadata before using the results.

## Quick Start

The public API does not require an API key.

```text
https://api.edenapi.org/v1
```

Get regional insights for Seoul:

```bash
curl -fsS 'https://api.edenapi.org/v1/regions/1100000000/insights?period=30d'
```

Call the API from a browser:

```javascript
const response = await fetch(
  'https://api.edenapi.org/v1/markets/inbound?countries=JP&countries=US'
);
if (!response.ok) throw new Error(`EDEN API: ${response.status}`);

const { data, meta } = await response.json();
console.log(data, meta.availability, meta.as_of);
```

Cross-origin GET and POST requests are supported without cookies. Do not set `credentials: 'include'`. See [Swagger UI](https://api.edenapi.org/docs) for parameters and response examples.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/trends` | Travel keyword trends |
| `GET` | `/v1/regions/{area_code}/insights` | Regional insights |
| `GET` | `/v1/places/{content_id}` | Place details |
| `GET` | `/v1/forecasts/visitors` | Visitor outlook |
| `GET` | `/v1/visitors/timeseries` | Visitor history |
| `GET` | `/v1/markets/inbound` | Inbound market comparisons |
| `GET` | `/v1/markets/{country}/alerts` | Official notices |

Use an EDEN place ID or TourAPI content ID to fetch place details. Supported country codes are `JP`, `CN`, `TW`, `US`, and `PH`. Visitor outlook requests default to 7 days and support up to 30 days.

## Reading Responses

Successful queries return a `data` and `meta` envelope. Missing information is not replaced with invented values or zeros.

| Field | Meaning |
| --- | --- |
| `data` | Query result; may be `null` or empty when data is unavailable |
| `meta.availability` | Availability of the overall result |
| `meta.reason` | Why a result is limited or unavailable |
| `meta.as_of` | Data reference timestamp |
| `meta.freshness` | Freshness status and acceptable age |
| `meta.sources` | Observation timestamps and collection status for each source |

Sources publish on different schedules. Check individual data blocks as well as the overall metadata. Invalid inputs return `422`, unknown IDs return `404`, and requests exceeding rate limits return `429`.

### Fields That Are Not Provided

Some declared fields have no verified source today. They are always `null` (or `unavailable` for their availability block), and they are excluded from `meta.availability`, so a response is `available` when every sourced field is present. The list is the current product decision (2026-09-16); adding a source to any of them is a separate change.

| Endpoint | Field | Why |
| --- | --- | --- |
| `/v1/regions/{area_code}/insights` | `demand.avg_stay_nights`, `diversity.age_index` | The official regional statistics publish no such dimension |
| `/v1/visitors/timeseries` | `concentration_rate`, `summary.peak_concentration_rate` | The visitor statistics do not publish concentration |
| `/v1/forecasts/visitors` | `expected_visitors`, `confidence`, `adjustment_factors` | No source forecasts headcounts or confidence; the reference index is not scaled into people |
| `/v1/markets/inbound` | `social_interest.youtube.score` | A search sample is not a country signal |
| `/v1/trends` | `destination_searches`, `sns_mentions` | No absolute search-count source; the other social platforms need external approval. `search_ratio` comes from NAVER for Korean province travel keywords only |

### Understanding the Indicators

- **YouTube metrics** describe a sample of publicly available search results. A search region filter does not identify viewers' nationalities.
- **Visitor outlook** requires collected official forecasts. Missing forecasts return `unavailable`; historical visits are not used as substitutes.
- **Notice translations and summaries** are returned when available. Original notices remain accessible when a translation is missing.

Availability depends on source permissions and collection coverage. NAVER collection requires verified storage rights and successful collection before search ratios can be served. Unsupported social platforms have no invented metrics.

## Data Coverage and Refresh

Implementation coverage and database observations checked on 2026-09-17. Sources publish with a lag. A collection path being enabled does not mean its data has arrived; check `meta.freshness` and `meta.sources`.

| Endpoint | Works today | Cadence and lag | Not answered |
| --- | --- | --- | --- |
| `GET /v1/trends` | Stored YouTube market keywords include `Korea travel`, `Seoul travel`, `Jeju travel` and their JP/CN/TW translations. Stored KTO keywords are `관광서비스수요` and `문화자연자원 수요`, with `area_code` filtering | YouTube daily; KTO resource demand monthly; NAVER has a daily collection path | NAVER had no observations at the database check. Uncollected keywords return `unavailable`; `destination_searches` and `sns_mentions` are always `null` |
| `GET /v1/regions/{area_code}/insights` | Province (sido) codes; `period` 7d/30d/90d; add `compare=previous_period` to get `visitors.change_rate` and `comparison` | Daily visitors publish about 30 days late; demand and diversity are monthly, about two months late | Areas without their own observations return `unavailable`; `avg_stay_nights`, `age_index` always `null` |
| `GET /v1/visitors/timeseries` | Province codes; day/week/month; 7d/30d/90d/12m | Same daily visitor source | `attraction_name` has no source (`unavailable`); `concentration_rate` always `null` |
| `GET /v1/forecasts/visitors` | Sigungu codes get the official KTO concentration forecast averaged over the area's attractions (`method: official`, `sample_count`); areas without official forecasts return `unavailable`; weather, festivals and holidays per day | Forecast horizon 30 days; weather refreshed every 3 hours for one grid per province; festivals weekly; holidays monthly | `expected_visitors`, `confidence`, `adjustment_factors` always `null`; `nx`/`ny` other than the province grid are `unavailable` |
| `GET /v1/markets/inbound` | `JP`, `CN`, `TW`, `US`, `PH`; `period` 3m/6m/12m/24m; `include` blocks visitors, flights, flight_schedule, fx, social_interest; `forecast_days` up to 7 | Visitors monthly (about two months late); flights and passengers monthly; 7-day schedule daily; FX daily | `social_interest.youtube.score` is always `null` by design; only YouTube is collected among social sources |
| `GET /v1/markets/{country}/alerts` | Originals with source links; existing stored translations and summaries; `types`, `since`, `limit` | Sources refresh every 12 hours; new paid enrichment is disabled | Missing translations return the original with `fallback: true`; `source_scope=local` has no collector; summaries are nullable |
| `GET /v1/places/{content_id}` | Korean title, category, address, coordinates; `overview`, `en`/`ja`/`zh-CN` titles, `hub`, `related_places`, `nearby_shops` as collection fills them | Overview 60 places per day, translations province by province every six days, hub and related places daily, nearby shops 20 places every six hours | `zh-TW` has no source; places whose KTO name does not match a TourAPI entry keep empty `hub`/`related_places` |

The dashboard sends `compare=previous_period`, displays missing evidence, serves KTO trends without forcing the YouTube filter, and exposes official sigungu forecasts by area code. Market details include flight schedules and the Korean national tourism balance. Place details include hub data when present.

At the database check, NAVER observations, nonempty place overviews and passenger values had not arrived. These remain missing in the UI. Existing alert revisions included 150 stored Korean and English summaries from earlier AI enrichment; disabling new enrichment does not remove them. The dashboard labels these summaries and retains links to the originals.

## Source-backed responses

Fixed market statistics and seeded country language/currency defaults are removed. Countries are registered only when KTO inbound records contain an ISO identifier and a source nationality name. Supported country codes and source request settings remain collection configuration, not response data.

- Missing evidence returns `null`, an empty result or `unavailable` with a reason. Observed zero values remain zero.
- No historical weekday forecast, language affinity bonus, title-keyword theme guess or weighted inbound score is served.
- Related places expose the collected `rank`; `score` is `null`, including for old stored rank-derived scores.
- The destination recommendation endpoint and dashboard view have been removed. Public API requests to the former route return 404.
- FX requires an explicit `currency`; country defaults are not inferred.
- Collected historical data can still be returned with `stale=true`. Source-derived totals, averages and normalization remain supported.

Deploy the new reader code, then apply `../.ops/run.sh migrate upgrade 20260917_0011` with collection paused. This cleanup branches directly from `20260911_0009` and does not apply the separately gated snapshot contract migration. Do not use `upgrade heads` to bypass that gate. It removes the fixed cohort table and country defaults, clears rank-derived relation scores and retires old metric definitions. It preserves collected observations and raw evidence.

## How It Works

```text
External sources -> Collection and normalization -> Published MariaDB data -> FastAPI -> Client
```

Public requests only read published database snapshots. They do not trigger external collection or LLM calls.

Collection uses one source worker, request budgets and resource limits. Monthly regional demand and diversity sources are checked weekly. Monthly flight refreshes cover the two most recent months while existing history remains stored. Cleanup keeps the current snapshot and two recent retired versions, preserving their referenced facts and source evidence. Superseded catalog errors and sources outside the maintained scope are quarantined without deleting their raw evidence. The API records pilot usage rows (daily counts per pilot key) and nothing else about requests.

`SCHEDULER_ENABLED=false` pauses collection, refresh and automatic cleanup. In production the scheduler runs as its own process (`python -m app.scheduler`, systemd unit `eden-scheduler`) with the same jobs, intervals and locks, while the API process serves requests with the scheduler disabled; the API still records pilot usage with the ingestion account. The unit files and one-time installation steps are in [deploy/README.md](deploy/README.md). `ALERT_ENRICHMENT_BATCH_SIZE=0` independently disables paid translation jobs; original official notices remain available. The deployment currently uses this zero translation budget. Fields that no current source can fill and deferred decisions are listed in [KNOWN_GAPS.md](KNOWN_GAPS.md).

| Component | Responsibility |
| --- | --- |
| FastAPI + Pydantic | Input validation, response contracts, and OpenAPI documentation |
| MariaDB + SQLAlchemy | Raw records, normalized data, and published snapshots |
| Alembic | Database migrations |
| Nginx + systemd | HTTPS entry point, rate limits, and process management |

## Development

Run the commands in this section from `api/`. Use Python 3.12 and `uv`. MariaDB Connector/C 3.4 or newer is required. Database connections use TLS with server certificate verification.

```bash
uv sync --frozen
```

Obtain the repository root development `.env` and machine-specific `.ops/` launchers from a project maintainer. Credentials and server connection settings are not included in the repository.

```bash
../.ops/run.sh check
../.ops/run.sh db-check
../.ops/run.sh
```

The development launcher connects to the existing remote MariaDB through an SSH tunnel. It does not create a local database. The API runs at `127.0.0.1:8000` with the scheduler disabled. Local documentation is available at `http://127.0.0.1:8000/docs`.

Run the checks:

```bash
uv lock --check
uv run ruff check app migrations tests
uv run pytest -q
```

### Repository Layout

```text
app/
  api/             # Endpoints, schemas, and documentation
  sources/         # External data adapters
  ingestion/       # Collection and retention policies
  normalization/   # Regional and place identity resolution
  products/        # Published aggregates and outlooks
  readmodels/      # Public API queries
  scheduler/       # Bounded collection, refresh and cleanup jobs; `python -m app.scheduler` runs them standalone
  operations/      # Maintenance and deployment validation logic
deploy/            # systemd units for the two production services and the watchdog
migrations/        # Alembic migrations
tests/             # Contract, integration, unit, and operations checks
```

<details>
<summary><strong>Maintainer Operations and Deployment</strong></summary>

The repository root `.env` is the only manually maintained configuration source and must have mode `600`. Do not load it with shell `source`. API reads, ingestion writes, and migrations use separate database accounts.

```bash
# Inspect migration status without changing the schema
../.ops/run.sh migrate current

# Check configuration and connectivity
../.ops/deploy.sh env-check
../.ops/deploy.sh preflight

# Deploy to production
../.ops/deploy.sh deploy
```

Deployment generates production configuration from the root `.env`. It verifies the SSH host fingerprint, database TLS, and account separation. Failed readiness checks restore the previous release and configuration. `preflight` does not deploy to production.

Schema changes require a separate request. Database backup and restore commands are disabled. Public API documentation is served at `/docs`; internal readiness is available on loopback at `/internal/readiness`.

The seed cleanup advances the production database from `20260911_0009` to `20260917_0011`. Deployment does not run migrations; apply the explicit target with `../.ops/run.sh migrate` in the order described above. The Phase 1 storage-contract migration `20260829_0007` is gated behind the soak evidence described in the root README and has not been applied; databases that complete it advance to the merge revision `20260911_0010`.

</details>
