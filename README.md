# EDEN API

EDEN은 한국 관광과 방한시장 데이터를 수집하고 정규화한 뒤 FastAPI와 읽기 전용 웹
대시보드로 제공하는 서비스입니다. 공개 API 기본 경로는 `/v1`이며 대시보드는 같은
origin의 `/dashboard/`에서 동작합니다.

## 구성

- FastAPI는 MariaDB에 게시된 현재 snapshot만 조회합니다. 공개 요청 중 외부 원천이나
  LLM을 호출하지 않습니다.
- scheduler는 원천 수집과 정규화 및 snapshot 게시를 담당합니다.
- React와 TypeScript 기반 대시보드는 EDEN의 `/v1/*`만 호출합니다. 값과 순위 및
  freshness를 브라우저에서 다시 계산하지 않습니다.
- Nginx는 `/dashboard/`, `/v1/*`, `/openapi.json`만 공개합니다. `/internal/*`는
  loopback 요청만 허용합니다.
- API 읽기와 ingestion 쓰기 및 migration은 서로 다른 MariaDB 계정을 사용합니다.

## 공개 API

다음 계약이 구현되어 있습니다.

- `GET /v1/trends`
- `GET /v1/regions/{area_code}/insights`
- `GET /v1/places/{content_id}`
- `GET /v1/forecasts/visitors`
- `GET /v1/visitors/timeseries`
- `GET /v1/markets/inbound`
- `GET /v1/markets/{country}/alerts`
- `POST /v1/recommendations/destinations`

운영 설정의 public origin은 `https://api.edenapi.org`입니다. OpenAPI 계약과 트렌드
응답은 다음과 같이 조회할 수 있습니다. 이 주소의 현재 운영 상태는 배포와 smoke test로
별도 확인해야 합니다.

```bash
curl -fsS https://api.edenapi.org/openapi.json
curl -fsS --get https://api.edenapi.org/v1/trends \
  --data-urlencode 'keyword=제주' \
  --data 'period=7d' \
  --data 'time_unit=day' \
  --data 'limit=10'
```

정상 조회 응답은 `data`와 `meta` envelope를 사용합니다. snapshot이 없거나 원천 접근이
승인되지 않은 경우에도 임의의 0을 만들지 않습니다. `data`는 `null`일 수 있고
`meta.availability`는 `unavailable`과 구체적인 `meta.reason`을 반환합니다. 일부 원천만
사용할 수 있으면 각 block과 `meta.sources`에서 가용성과 freshness를 확인할 수 있습니다.

API endpoint 구현 여부와 실제 데이터 가용성은 서로 다릅니다.

- YouTube와 X는 집계 adapter가 구현되어 있습니다. 실제 수집에는 유효한 자격 증명과
  승인 범위가 필요합니다.
- NAVER 검색 추세 adapter가 구현되어 있습니다. 저장 및 재게시 권리가 확인되기 전에는
  영구 저장을 활성화하지 않습니다.
- Instagram, Facebook, TikTok, Reddit은 플랫폼 승인을 확보하기 전까지 명시적으로
  `unavailable`을 반환합니다.
- Weibo, Douyin, Xiaohongshu, LINE도 현재 승인 범위에서 사용할 수 없으며 명시적으로
  `unavailable`을 반환합니다.
- 공공데이터와 날씨 및 환율과 ECOS 원천도 각 서비스의 승인과 자격 증명 및 게시된
  snapshot 상태에 따라 가용성이 달라집니다.
- 공지 번역과 요약은 LLM 설정이 없으면 해당 block만 `unavailable`로 표시합니다.
  원문 공지의 가용성과는 별도로 처리합니다.

따라서 HTTP 200이나 endpoint 목록만으로 모든 원천 연동이 끝났다고 판단하지 않습니다.
응답의 `meta.availability`, `meta.reason`, `meta.sources`를 함께 확인해야 합니다.

## 로컬 실행

Python 3.12와 lock file에 맞춰 의존성을 준비합니다.

```bash
uv sync --frozen
./.ops/run.sh check
./.ops/run.sh db-check
./.ops/run.sh
```

`.ops/run.sh`는 루트 `.env`의 `DEVELOPER_DB_USER`와 `DEVELOPER_DB_PASSWORD`를 개발
프로세스의 유효 DB 계정으로 사용합니다. 운영 VPS를 거치는 인증된 SSH tunnel을
유지하고 scheduler를 끈 상태로 API를 `127.0.0.1:8000`에서 실행합니다. 루트 `.env`의
운영용 `DB_HOST`와 `DB_PORT`는 변경하지 않습니다. tunnel이 끊기면 하위 명령도
종료됩니다.

개발 연결도 MariaDB의 서버 인증서와 비밀번호를 검증합니다. CA 파일이나 TLS 검증 해제
설정은 사용하지 않습니다. Mac에는 MariaDB Connector/C 3.4 이상이 필요합니다.

대시보드 개발 서버는 별도 terminal에서 실행합니다.

```bash
npm ci --prefix dashboard
npm run dev --prefix dashboard
```

브라우저 주소는 `http://127.0.0.1:4173/dashboard/`입니다. 개발 서버는 `/v1` 요청을
loopback의 API 8000 포트로 전달합니다.

같은 개발 DB 연결이 필요한 도구는 launcher를 통해 실행합니다.

```bash
./.ops/run.sh exec COMMAND...
```

## 환경과 DB 보안

`.env`는 유일하게 수동 관리하는 환경 설정 원본이며 권한을 `600`으로 유지합니다.
dotenv를 shell에서 `source`하지 않습니다. 주요 DB 역할은 다음과 같습니다.

- `DB_USER`, `DB_PASSWORD`: 공개 API 읽기 전용 계정
- `INGESTION_DB_USER`, `INGESTION_DB_PASSWORD`: scheduler 쓰기 계정
- `DEVELOPER_DB_USER`, `DEVELOPER_DB_PASSWORD`: 로컬 개발과 도구 계정
- `MIGRATION_DB_USER`, `MIGRATION_DB_PASSWORD`: schema 변경 전용 계정

모든 역할은 운영 VPS에 있는 같은 MariaDB의 host와 port 및 database를 사용합니다.
운영 앱은 loopback으로 연결하고 개발 연결은 SSH tunnel을 사용합니다. DB의 3306 포트는
외부에 공개하지 않습니다. API와 migration 및 MariaDB CLI는 별도 CA 파일 없이
인증서를 검증합니다.
공개 배포의 `ENVIRONMENT`는 `production`이어야 합니다. scheduler가 활성화된 production은
snapshot retention도 활성화해야 합니다. migration 비밀값은 API runtime 설정에서
제외합니다.

## 검증

백엔드 기본 검증은 lock과 lint 및 전체 계약과 회귀 테스트를 확인합니다.

```bash
uv lock --check
uv run ruff check app scripts migrations tests
uv run pytest -q
```

대시보드는 현재 API에서 타입을 생성하고 상태 렌더링 및 브라우저 동작을 검사합니다.

```bash
npm run check:types --prefix dashboard
npm test --prefix dashboard
npm run build --prefix dashboard
npm run test:browser --prefix dashboard
```

4173 포트를 다른 작업이 사용 중이면 `EDEN_BROWSER_TEST_PORT=4183 npm run test:browser
--prefix dashboard`로 검사 전용 포트를 선택할 수 있습니다.

## DB migration

`migrations/`는 Alembic이 관리합니다. 수동 점검도 launcher가 migration 계정을 선택하도록
실행합니다.

```bash
./.ops/run.sh migrate current
./.ops/run.sh migrate heads
```

`upgrade`나 `downgrade`는 실제 schema 변경 요청이 있을 때만 실행합니다. 배포는 DB backup
파일을 만들지 않으며 expand migration을 적용합니다. snapshot contract 변경은 운영 soak
증거가 통과한 뒤 별도 gate로 적용합니다.

## 파일럿 계측

고객과 합의한 `X-EDEN-Pilot` 값 또는 `EDEN-Pilot` User-Agent는 수신 즉시 해시됩니다.
원문 식별자는 DB와 구조화 로그에 저장하지 않습니다. 핵심 5개 API의 일별 호출 수와 성공
수 및 stale 응답 수를 집계합니다.

파일럿 시작과 운영 이벤트를 기록하고 28일 보고서를 생성할 수 있습니다. 로컬에서 실행할
때는 개발 DB launcher를 사용합니다.

```bash
./.ops/run.sh exec python scripts/phase2_pilot.py start customer-agreed-id
./.ops/run.sh exec python scripts/phase2_pilot.py correction customer-agreed-id --note 'approved correction'
./.ops/run.sh exec python scripts/phase2_pilot.py incident customer-agreed-id --note 'service incident'
./.ops/run.sh exec python scripts/phase2_pilot.py recovered customer-agreed-id
./.ops/run.sh exec python scripts/phase2_pilot.py report
```

보고서는 고정된 핵심 5개 endpoint 모두의 반복 사용을 검사합니다. 수작업 보정이 한 번이라도
기록되면 4주 gate는 통과하지 않습니다.

## 운영과 배포

기기별 운영 파일은 `.ops/deploy.sh`와 `.ops/run.sh`만 유지합니다. 공통 함수와 VPS 배포
본문은 `scripts/deploy_support.sh`와 `scripts/deploy_remote.sh`에 있습니다. 루트 `.env`에서
운영 `/opt/eden/shared/.env`와 별도 `/opt/eden/shared/migration.env`를 생성합니다. 생성된
운영 env 파일은 직접 편집하지 않습니다.

```bash
./.ops/deploy.sh env-check
./.ops/deploy.sh preflight
./.ops/deploy.sh deploy
./.ops/deploy.sh finalize-phase1-contract
./.ops/deploy.sh soak-7d
```

`env-check`는 환경값 생성만 검사합니다. `preflight`는 설정 생성과 build 및 개발 DB 연결을
검사하며 운영 배포를 수행하지 않습니다. `deploy`가 운영 설정과 release를 함께 반영합니다.
VPS host fingerprint와 DB TLS 및 계정 분리도 배포 gate에 포함됩니다.

release에는 `.agents/`, `.env`, cache와 로컬 테스트 산출물을 포함하지 않습니다. TLS
인증서가 없으면 Nginx는 공개 API를 fail-closed 상태로 유지합니다. readiness나 smoke
test가 실패하면 이전 API release와 대시보드 symlink 및 운영 설정을 복구합니다.

이 프로젝트는 DB backup 파일을 생성하거나 보관하지 않습니다. `backup`과 `restore`
하위 명령은 정책상 비활성화되어 있습니다. Phase 2 soak는 24시간 Phase 1 증거와 분리된
`/opt/eden/phase2-evidence/soak.jsonl`에 기록합니다. 7일 soak와 실제 파일럿 28일 증거는
배포 직후 생성할 수 없으므로 운영 기간이 지난 뒤 gate 결과를 확인해야 합니다.

`.ops/`는 Git에서 제외하는 기기별 실행 파일이며 Mac의 runtime 경로를 사용합니다.
루트 `AGENTS.md`, `.env`, `.agents/`도 Git에서 제외해 로컬에서 관리합니다.
