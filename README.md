# EDEN API

EDEN은 한국 관광과 방한시장 데이터를 MariaDB에 수집하고 정규화한 뒤 FastAPI와
읽기 전용 웹 대시보드로 제공하는 서비스입니다. 공개 API 기본 경로는 `/v1`이며
대시보드는 같은 origin의 `/dashboard/`에서 동작합니다.

## 구성

- FastAPI는 DB에 게시된 현재 snapshot만 조회합니다. 요청 중 외부 원천이나 LLM을
  호출하지 않습니다.
- React와 TypeScript 기반 대시보드는 EDEN의 `/v1/*`만 호출합니다. 값과 순위 및
  freshness를 브라우저에서 다시 계산하지 않습니다.
- Nginx는 `/dashboard/`, `/v1/*`, `/openapi.json`만 공개합니다. `/internal/*`는
  loopback 요청만 허용합니다.
- API 읽기, ingestion 쓰기, migration 역할은 서로 다른 MariaDB 계정을 사용합니다.

## 로컬 실행

Python 환경과 API를 준비합니다.

```bash
uv sync
./.ops/run.sh
```

`.ops/run.sh`는 `.env`의 원격 MariaDB 설정을 사용하고 scheduler를 끈 상태로 API를
실행합니다. 로컬 MariaDB 연결은 거부합니다. 루트 `.env`의
`DEV_DB_CONNECTION=ssh-tunnel`과 `DEV_DB_SSL_CA`로 운영 DB까지 SSH 연결하고
인증서도 검증합니다. 운영용 `DB_HOST`, `DB_PORT`, `DB_SSL_CA`는 보존합니다.
터널은 명령이 실행되는 동안 유지되며 연결이 끊기면 해당 명령도 종료됩니다.
`.ops/run.sh check`는 실행 환경을 확인하고 `db-check`는 읽기 전용 DB 조회를 합니다.
`.ops/run.sh exec COMMAND`는 같은 개발 설정으로 DB 도구를 실행합니다.

대시보드 개발 서버는 별도로 실행합니다.

```bash
cd dashboard
npm ci
npm run dev
```

## 환경과 DB 보안

`.env`에는 다음 역할별 접속 정보를 둡니다.

- `DB_USER`, `DB_PASSWORD`: 공개 API 읽기 전용 계정
- `INGESTION_DB_USER`, `INGESTION_DB_PASSWORD`: scheduler와 파일럿 집계 쓰기 계정
- `MIGRATION_DB_USER`, `MIGRATION_DB_PASSWORD`: 배포 중 schema 변경 전용 계정

production에서는 `DB_SSL_CA`와 `DB_SSL_VERIFY_CERT=true`가 필수입니다. 세 계정 이름은
서로 달라야 합니다. `DB_NETWORK_MODE`는 승인된 CIDR allowlist 또는 SSH tunnel 중
하나를 사용합니다. 공개 배포의 `ENVIRONMENT`는 반드시 `production`이어야 합니다.
`SCHEDULER_ENABLED=true`인 production은 `SNAPSHOT_RETENTION_ENABLED=true`도 요구합니다.
migration 비밀값은 API 런타임에 제공하지 않습니다.

## 검증

백엔드 진입 gate는 lock, lint, 전체 계약 및 회귀 테스트를 검사합니다.

```bash
uv lock --check
uv run ruff check app scripts migrations tests
uv run pytest -q
uv run python scripts/check_openapi_drift.py
```

대시보드는 OpenAPI type drift, 상태 렌더링, same-origin 경계, 접근성, 브라우저 화면을
검사합니다.

```bash
cd dashboard
npm run check:types
npm test
npm run build
npm run test:browser
```

## DB migration

`migrations/`는 Alembic이 관리합니다. 배포는 별도 DB backup 없이 expand migration을
적용합니다. snapshot contract 변경은 soak 증거가 통과한 뒤 별도 gate로 적용합니다.

수동 점검이 필요한 경우에도 migration 계정을 명시적으로 선택합니다.

```bash
uv run alembic heads
uv run alembic current
```

## 파일럿 계측

고객과 합의한 `X-EDEN-Pilot` 값 또는 `EDEN-Pilot` User-Agent는 수신 즉시 해시됩니다.
원문 식별자는 DB와 구조화 로그에 저장하지 않습니다. 핵심 5개 API의 일별 호출 수,
성공 수, stale 응답 수를 집계합니다.

파일럿 시작과 운영 이벤트를 기록하고 28일 보고서를 생성할 수 있습니다.

```bash
uv run python scripts/phase2_pilot.py start customer-agreed-id
uv run python scripts/phase2_pilot.py correction customer-agreed-id --note "approved correction"
uv run python scripts/phase2_pilot.py incident customer-agreed-id --note "service incident"
uv run python scripts/phase2_pilot.py recovered customer-agreed-id
uv run python scripts/phase2_pilot.py report --output /opt/eden/phase2-evidence/pilot-report.json
```

보고서는 고정된 핵심 5개 endpoint 모두의 반복 사용을 검사합니다. 수작업 보정이 한
번이라도 기록되면 4주 gate는 통과하지 않습니다.

## 운영과 배포

운영 파일은 `.ops/deploy.sh`, `.ops/run.sh`, `.ops/sync-env.sh` 세 개만 사용합니다.
contract 전환과 7일 soak 검사는 deploy 스크립트의 하위 명령입니다.
공통 함수와 VPS 배포 본문은 각각 `scripts/deploy_support.sh`와
`scripts/deploy_remote.sh`에 분리되어 있습니다.
`sync-env.sh`는 루트 `.env`에서 배포 설정을 생성합니다. migration credentials는
API 설정에서 제외하고 `/opt/eden/shared/migration.env`에 별도로 생성합니다.
이 파일의 소유자는 `root:root`, mode는 `600`입니다. 생성된 운영 env 파일은 직접
편집하지 않습니다. `sync-env.sh --check`는 전송 없이 값 보존을 검사합니다.

```bash
./.ops/sync-env.sh
./.ops/deploy.sh preflight
./.ops/deploy.sh deploy
./.ops/deploy.sh finalize-phase1-contract
./.ops/deploy.sh soak-7d
```

배포는 로컬 진입 gate와 대시보드 build가 성공한 뒤에만 시작합니다. VPS host
fingerprint와 DB TLS를 확인합니다. `VPS_HOST_FINGERPRINT`에는 서버가 광고하는 모든
SSH key fingerprint를 공백 또는 쉼표로 구분해 정확히 고정해야 합니다. 일부 key만
일치하거나 추가 key가 발견되면 배포와 환경 동기화가 중단됩니다. DB 계정 권한과
네트워크 제한도 gate에 포함합니다.
release에는 `.agents/`, `.env`, cache, 로컬 테스트 산출물을 포함하지 않습니다. TLS
인증서가 없으면 Nginx는 공개 API를 fail-closed 상태로 유지합니다. readiness나 smoke
test가 실패하면 이전 API release와 대시보드 symlink를 복구합니다.

이 프로젝트는 DB backup 파일을 생성하거나 보관하지 않습니다. `backup`과 `restore`
하위 명령은 정책상 비활성화되어 있습니다. Phase 2 soak는 24시간 Phase 1 증거와 분리된
`/opt/eden/phase2-evidence/soak.jsonl`에 5분마다 기록합니다. 7일 soak와 실제 파일럿
28일 증거는 배포 직후 생성할 수 없으므로 운영 기간이 지난 뒤 gate 결과를 확인해야
합니다.

`.ops/`는 Git에서 제외하는 기기별 실행 파일입니다. Mac과 Tommy 각각의 런타임
경로를 사용합니다. 루트 `AGENTS.md`, `.env`, `.agents/`는 Tommy 통합 전송으로
동기화하고 `.ops/`는 전송하지 않습니다.
