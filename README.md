# EDEN API

EDEN은 한국 관광과 방한시장 데이터를 수집하고 정규화해 제공하는 API 전용 서비스입니다.

- [공식 API 문서](https://api.edenapi.org/docs): 요청 파라미터와 응답 스키마 및 실제 호출
- [OpenAPI JSON](https://api.edenapi.org/openapi.json): 클라이언트 타입 생성에 사용할 명세
- 공개 API 기본 경로: `https://api.edenapi.org/v1`

대시보드 소스와 정적 호스팅은 제거했습니다. 별도 프론트는 Vercel 등에서 독립적으로
개발하고 배포할 수 있습니다. 현재 API는 인증 없이 공개하며 쿠키를 사용하지 않는
cross-origin GET과 POST를 허용합니다. `fetch`에 `credentials: 'include'`를 설정하지 마세요.

## 구성

- FastAPI는 MariaDB에 게시된 현재 snapshot만 조회합니다. 공개 요청 중 외부 원천이나
  LLM을 호출하지 않습니다.
- scheduler는 원천 수집과 정규화 및 snapshot 게시를 담당합니다.
- Nginx는 `/`, `/docs`, `/v1/*`, `/openapi.json`을 공개합니다. `/`는 문서로 이동합니다.
  `/internal/*`는 loopback 요청만 허용합니다. 기존 `/dashboard/`와 `/assets/`는 410입니다.
- 문서는 FastAPI의 Swagger UI를 사용합니다. 별도 Node 서버나 프론트 빌드가 없으며
  문서용 Swagger UI 자산만 브라우저가 CDN에서 로드합니다.
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
  --data-urlencode 'keyword=Korea travel' \
  --data 'country=US' \
  --data 'social_sources=youtube' \
  --data 'period=7d' \
  --data 'time_unit=day' \
  --data 'limit=10'
```

정상 조회 응답은 `data`와 `meta` envelope를 사용합니다. snapshot이 없거나 원천 접근이
승인되지 않은 경우에도 임의의 0을 만들지 않습니다. `data`는 `null`일 수 있고
`meta.availability`는 `unavailable`과 구체적인 `meta.reason`을 반환합니다. 일부 원천만
사용할 수 있으면 각 block과 `meta.sources`에서 가용성과 freshness를 확인할 수 있습니다.

API endpoint 구현 여부와 실제 데이터 가용성은 서로 다릅니다.

- 트렌드는 미리 수집한 키워드만 조회합니다. 지원 키워드와 국가 조합은 공식 문서를 따릅니다.
- YouTube는 검색 결과 영상의 공개 지표를 집계합니다. 검색 국가 조건은 시청자 국적을
  나타내지 않으므로 국가별 실제 관심도로 사용하지 않습니다.
- NAVER 검색 추세 adapter가 구현되어 있습니다. 저장 및 재게시 권리가 확인되기 전에는
  영구 저장을 활성화하지 않습니다.
- Instagram, Facebook, Reddit은 플랫폼 승인을 확보하기 전까지 명시적으로
  `unavailable`을 반환합니다.
- X와 TikTok은 사용 범위에서 제외합니다. Weibo와 Douyin 및 Xiaohongshu와 LINE도
  수집과 공개 지표에서 제외합니다.
- 공공데이터와 날씨 및 환율과 ECOS 원천도 각 서비스의 승인과 자격 증명 및 게시된
  snapshot 상태에 따라 가용성이 달라집니다.
- 공지 번역과 요약은 Upstage Solar를 사용합니다. `LLM_API_KEY`에는 Upstage 키를,
  `LLM_MODEL`에는 `solar-pro4`를 설정합니다. 설정이 없으면 해당 block만
  `unavailable`로 표시합니다.
  원문 공지의 가용성과는 별도로 처리합니다.
- 한국은행 ECOS의 일반여행 수지는 한국 전체의 월간 수입에서 지출을 뺀 값입니다.
  국가별 양자 수지가 아니며 방한시장 응답에서 발표월과 함께 제공합니다.

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

로컬 문서는 `http://127.0.0.1:8000/docs`에서 확인합니다. Node와 npm은 필요하지 않습니다.

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

`env-check`는 환경값 생성만 검사합니다. `preflight`는 설정 생성과 백엔드 검증 및 개발 DB 연결을
검사하며 운영 배포를 수행하지 않습니다. `deploy`가 운영 설정과 release를 함께 반영합니다.
VPS host fingerprint와 DB TLS 및 계정 분리도 배포 gate에 포함됩니다.

release에는 `.agents/`, `.env`, cache와 로컬 테스트 산출물을 포함하지 않습니다. TLS
인증서가 없으면 Nginx는 공개 API를 fail-closed 상태로 유지합니다. readiness나 smoke
test가 실패하면 이전 API release와 운영 설정을 복구합니다. 성공한 배포는 기존 대시보드 정적
파일과 과거 release의 dashboard 디렉터리 및 전용 Nginx 로그를 제거합니다.
삭제된 구형 스크립트를 호출하던 중복 soak timer와 maintenance timer도 제거합니다.

이 프로젝트는 DB backup 파일을 생성하거나 보관하지 않습니다. `backup`과 `restore`
하위 명령은 정책상 비활성화되어 있습니다. Phase 2 soak는 24시간 Phase 1 증거와 분리된
`/opt/eden/phase2-evidence/soak.jsonl`에 기록합니다. 7일 soak와 실제 파일럿 28일 증거는
배포 직후 생성할 수 없으므로 운영 기간이 지난 뒤 gate 결과를 확인해야 합니다.

`.ops/`는 Git에서 제외하는 기기별 실행 파일이며 Mac의 runtime 경로를 사용합니다.
루트 `AGENTS.md`, `.env`, `.agents/`도 Git에서 제외해 로컬에서 관리합니다.


## 소형 서버 수집 및 저장 제한

운영 기본값은 CPU 1개와 메모리 약 1.6GiB인 서버를 기준으로 한다. `.env`에서 설정을
관리하며 배포가 운영 설정에 반영한다. API는 저장된 게시본만 읽고 요청 중 수집하지 않는다.

| 항목 | 적용 기준 |
| --- | --- |
| 원천 HTTP | 실행당 실제 요청 20회, 월간 전국 비교 통계만 최대 60회. 누적 8MiB, 응답 하나 2MiB, 실행 120초 |
| 공공데이터 레코드 | 실행당 최대 10,000건. 초과 시 부분 수집으로 기록 |
| 수집 주기 | 원천당 최소 1시간. 원래 일간 또는 월간 주기가 더 느리면 유지 |
| 저장 및 재처리 | raw 저장 묶음 20개. 오류 재처리는 1시간 간격으로 최대 5개 |
| 집계 게시 | 제품군별 15분 간격. 수집과 집계의 무거운 DB 작업은 동시 1개 |
| 공지 번역 | 1시간 간격으로 최대 2건. 건당 최초 호출과 필요 시 언어 복구 1회 |
| 새 게시본 | 압축 전 payload 최대 8MiB. 읽기 캐시는 직렬화 크기 합계 16MiB |
| DB 보호 | 전체 테이블 및 인덱스 추정 합계 20GiB 또는 하루 증가량 100MiB 초과 시 새 수집과 게시 중지 |
| 서버 보호 | 메모리 사용률 85%에서 쓰기 중지. 디스크 70% 경고, 75% 게시 중지, 80% 수집 중지 |
| 보관 정리 | 7일 지난 정리 가능 게시본 대상. 실행당 후보 20개와 연결 기록 1,000행까지만 정리 |
| 용량 계측 | 15분 간격 저장. 7일 지난 계측은 실행당 최대 500행씩 정리 |
| 공개 API | IP당 초당 5회, 서버 전체 초당 20회. 일시 초과 허용량은 각각 20회와 40회이며 초과 요청은 HTTP 429 |
| API 프로세스 | 동시 처리 32개, CPU 최대 80%, 메모리 512MiB부터 압박 조절 및 768MiB 상한 |

용량과 증가량 기준은 계측에 따른 다음 작업의 진입 제한이며 DB 파일의 엄격한 바이트
할당량은 아니다. 이미 진행 중인 한 작업은 경계를 넘을 수 있다. 메모리와 디스크 보호로
갱신이 멈추어도 기존 게시본 조회는 유지되며 내부 readiness에 경고를 남긴다.

현재 게시본과 직전 복구용 게시본은 보관 기간이 지나도 삭제하지 않는다. 참조 중인 raw와
원천 출처도 임의 삭제하지 않는다. 오래된 정리 대상이 많이 쌓여 있으면 여러 실행에 걸쳐
처리한다. 배포는 과거 데이터 전체 적재나 강제 집계 재생성을 자동 실행하지 않는다.

지역 방문 데이터는 약 30일의 발표 지연을 반영해 30~39일 전의 10일 구간을 갱신한다.
신선도 기준은 예상 발표 지연 31일에 3일의 여유를 더한 34일이다. 월간 전국 비교 통계는 한 달의 모든 지역과 지표를
묶어 순환 수집한다. 중간에 제한에 걸리면 일부 지역으로 전국 지수를 다시 계산하지 않고
기존 값을 유지한다. 집계 조회 범위는 방한 비교 48개월, 지역 방문 731일로 제한하며 기존
역사 데이터 자체를 삭제하지는 않는다.
