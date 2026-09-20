# EDEN

관광 데이터를 수집하고 게시된 통계를 API와 대시보드로 제공합니다.

| 서비스 | 주소 | 배포 위치 |
| --- | --- | --- |
| 대시보드 | https://edenapi.org | Vercel |
| API 문서 | https://api.edenapi.org/docs | 운영 VPS |
| OpenAPI | https://api.edenapi.org/openapi.json | 운영 VPS |

## API 제공 범위 (2026-09-17, API v0.3.0)

공개 엔드포인트 8개의 구현 범위입니다. 구현 여부와 실제 수집 여부는 다르며, 자료가 없으면 `null` 또는 `unavailable`로 응답합니다. 2026-09-17 DB 확인 시 NAVER 관측, 장소 소개문, 국가별 항공 여객 수는 아직 없었습니다. 원천이 없는 필드와 사유는 [API 문서](api/README.md#fields-that-are-not-provided), 수집 제한은 [알려진 제한](api/KNOWN_GAPS.md)에 있습니다.

| 엔드포인트 | 상태 | 제공하는 것 | 미제공 / 제한 |
| --- | --- | --- | --- |
| `GET /v1/trends` | 수집 범위 내 지원 | YouTube 검색 표본과 KTO 관광자원 수요 지수. KTO 저장 키워드는 `관광서비스수요`, `문화자연자원 수요` | NAVER는 수집 경로만 준비됨. 사전 수집되지 않은 키워드는 unavailable |
| `GET /v1/regions/{area_code}/insights` | 완성 (시도 단위) | 일별 방문자(내 및 외국인, 원천 발표 지연 약 30일), 관광 체류 및 소비 강도, 국적 다양성, `compare=previous_period`로 증감률 | 요청 지역에 관측이 없으면 unavailable. `avg_stay_nights`, `age_index`는 원천 없음 |
| `GET /v1/visitors/timeseries` | 완성 (시도 단위, 일 및 주 및 월) | 방문자 시계열과 요약 | `concentration_rate`는 원천 없음. `attraction_name`(관광지별)은 원천이 없어 항상 unavailable |
| `GET /v1/forecasts/visitors` | 완성 | 시군구: 관광지별 KTO 공식 집중률의 평균(`sample_count` 표시). 시도: 소속 시군구 전체 관광지의 평균(`basis`에 시군구·관광지 수). 공식 전망이 없으면 unavailable. 기상청 단기예보, 축제, 공휴일. `requested_area_code`, `data_area_code`, `spatial_resolution`으로 요청 지역과 자료 지역을 구분 | `expected_visitors`, `confidence`는 원천 없음 |
| `GET /v1/markets/inbound` | 지원, 여객 수 수집 대기 | 월별 방한객과 도착 항공편. 향후 운항 일정, 환율, 한국 전체 관광수지, YouTube 검색 표본 | 여객 수는 DB에 아직 없음. YouTube 점수는 국가 신호로 사용하지 않음 |
| `GET /v1/markets/{country}/alerts` | 저장 자료 지원 | 공식 공지 원문과 출처 링크. 기존에 저장된 번역 및 AI 요약 | 신규 유료 보강은 중단. 번역이 없는 공지는 원문으로 fallback |
| `GET /v1/places` | 완성 | 시도 또는 시군구의 관광지 목록(제목 순, 언어별 제목과 한국어 fallback, 제목 검색, `limit`/`offset` 페이지) | 한국어 제목이 없는 관광지는 목록에 없음 |
| `GET /v1/places/{content_id}` | 지원, 일부 수집 대기 | 장소명과 위치, 다국어 제목, 중심 관광지 순위, 연관 장소, 주변 상점 | 소개문은 DB에 아직 없음. 장소별 번역과 연관 데이터의 제공 범위가 다름 |

수집 원천 36개 중 26개가 켜져 있습니다. 꺼진 10개(Instagram 및 Facebook 및 Reddit 및 X 및 TikTok 및 Weibo 및 Douyin 및 Xiaohongshu 및 LINE 및 관광지 입장객)는 어댑터가 없거나 외부 승인이 필요합니다.

대시보드는 지역 비교와 공식 시군구 전망 및 KTO 트렌드를 API에 연결합니다. 시장 상세에는 운항 일정과 한국 전체 관광수지를 표시합니다.

응답은 수집 근거가 있는 값만 제공합니다. 고정 국가 통계 seed와 임의 점수를 제거했습니다. 여행지 추천 API와 화면은 삭제했습니다. 환율은 `currency`를 직접 지정해야 합니다. DB 정리 마이그레이션과 변경된 계약은 [API 데이터 정책](api/README.md#source-backed-responses)을 참고하세요.

## 디렉터리

```text
.agents/       작업 기록과 로컬 자료, Git 제외
.ops/          .env와 실행 및 배포 스크립트, 전체 Git 제외
dashboard/    React + TypeScript + Vite
api/          FastAPI 코드, 마이그레이션, 테스트, Python 의존성 (README, CHANGELOG, KNOWN_GAPS 포함)
.gitignore
README.md
assets/        공통 문서 이미지
AGENTS.md      프로젝트 작업 가이드, Git 제외
```

Git 관리용 `.git/`은 그대로 유지합니다. API 가상환경과 테스트 캐시는 `api/` 안에 생성됩니다.

## 시작하기

관리자에게 `.ops/.env`와 `.ops/`의 세 실행 파일을 받습니다. 다른 Mac에서도 같은 파일을 사용합니다. 스크립트가 현재 사용자 홈과 Homebrew 설치 위치를 찾아 Apple Silicon과 Intel 경로를 선택합니다.

Mac에는 Homebrew와 Xcode Command Line Tools가 필요합니다. 처음 한 번 다음 도구를 설치합니다.

```bash
brew install uv node@24 mariadb-connector-c sshpass
chmod 600 .ops/.env
chmod +x .ops/client.sh .ops/api_and_client.sh .ops/deploy.sh
```

Python 3.12는 uv가 준비합니다. DB 연결에는 MariaDB Connector/C 3.4 이상이 필요하며, `mariadb-connector-c`는 로컬 DB 서버를 설치하지 않습니다. `.ops/.env`는 셸의 `source`로 실행하지 않습니다. 다른 Mac으로 `api/.venv`나 `dashboard/node_modules`를 복사하지 말고 아래 `sync`로 설치합니다.

루트에서 실행합니다.

```bash
.ops/api_and_client.sh sync           # API와 대시보드 의존성 설치
.ops/api_and_client.sh check          # 환경 설정 검사
.ops/api_and_client.sh db-check       # SSH와 TLS를 통한 읽기 전용 DB 검사
.ops/client.sh             # 클라이언트만 실행, VPS API 사용
.ops/api_and_client.sh     # 로컬 API와 클라이언트를 함께 실행
```

두 실행 방식 중 하나를 선택합니다. 클라이언트 주소는 모두 `http://127.0.0.1:5173`입니다.

| 실행 파일 | API | DB 연결 |
| --- | --- | --- |
| `.ops/client.sh` | VPS의 `https://api.edenapi.org` | VPS API가 기존 원격 DB 사용 |
| `.ops/api_and_client.sh` | 로컬의 `http://127.0.0.1:8000` | SSH 터널로 동일한 원격 DB 사용 |

`client.sh`는 API 주소를 VPS로 고정하고 클라이언트만 실행합니다. 로컬 API와 SSH 터널은 시작하지 않습니다. `api_and_client.sh`는 SSH 터널과 두 서버를 함께 관리하며 인증된 DB 탐색도 제공합니다. 내부 토큰은 메모리에서만 공유합니다. API 문서는 `http://127.0.0.1:8000/docs`에서 확인합니다.

DB는 항상 기존 원격 DB 하나만 사용합니다. 로컬 API의 loopback DB 포트는 원격 DB로 전달하는 SSH 터널 입구입니다. 터널 연결에 실패하거나 연결이 끊기면 실행을 중단하며 로컬 DB로 전환하지 않습니다. 두 모드 모두 `.ops/.env`의 DB 주소를 변경하지 않습니다.

API만 실행하려면 `.ops/api_and_client.sh api`를 사용합니다.

일반 실행은 SSH 터널이 준비되면 시작하며 별도 DB 진단 쿼리는 `db-check`에서만 수행합니다. 개발 명령은 기존 원격 DB에 SSH 터널로 연결합니다. 개발 계정을 사용하고 scheduler를 끕니다. 마이그레이션만 별도 migration 계정을 선택합니다.

```bash
.ops/api_and_client.sh migrate current   # 현재 스키마 버전, 읽기 전용
.ops/api_and_client.sh exec COMMAND...   # api/ 안에서 개발 DB 환경으로 도구 실행
.ops/api_and_client.sh export-catalog    # DB 접속 없이 대시보드의 스키마 정의 갱신
.ops/api_and_client.sh test              # API 검사와 전체 테스트, 대시보드 빌드와 테스트
```

설정 원본은 `.ops/.env` 하나이며 `.ops/` 전체는 Git에서 제외합니다. API는 로컬 실행 스크립트 또는 운영 systemd가 전달한 환경값을 사용합니다. 현재 작업 디렉터리의 `.env`는 자동으로 읽지 않습니다. API 사용법은 [API 문서](api/README.md)를 참고합니다.

## 배포

```bash
.ops/deploy.sh env-check        # 운영 환경 설정 생성 검사
.ops/deploy.sh preflight        # API 설정 생성, 빌드와 개발 DB 연결 검사
.ops/deploy.sh deploy           # API를 기존 VPS에 배포
.ops/deploy.sh dashboard-check  # 대시보드 로컬 빌드
.ops/deploy.sh dashboard        # 대시보드를 Vercel에 운영 배포
```

의존성 설치는 `api_and_client.sh sync`, 전체 테스트는 `api_and_client.sh test`로 직접 실행합니다. 배포 명령은 전체 테스트를 자동 실행하지 않습니다. `dashboard-check`는 설치된 의존성으로 빌드만 확인합니다.

API 배포는 이미 구성된 VPS에 `api/`와 두 운영 실행 파일을 전송합니다. 운영 의존성을 설치하고 `.ops/.env`에서 생성한 설정을 반영한 뒤 API를 재시작합니다. SSH fingerprint와 DB TLS, 계정별 권한을 확인하며 실패하면 이전 release와 환경 파일을 복구합니다.

운영 VPS에서 수집 및 게시 및 정리 스케줄러는 `eden-scheduler.service`가 `python -m app.scheduler`로 별도 프로세스에서 실행합니다. `eden-api.service`는 `/opt/eden/shared/api.env`의 `SCHEDULER_ENABLED=false`로 공개 API만 담당하며, 두 서비스는 각자 메모리 상한을 갖습니다. 잡 내용과 주기, 락, 용량 게이트는 프로세스 분리 전과 같습니다. 스케줄러 유닛은 `PartOf`와 `Wants`로 API 유닛에 묶여 있어 배포 스크립트의 `systemctl stop/restart/start eden-api`가 두 서비스에 함께 적용됩니다. 유닛 파일과 최초 설치 절차는 [api/deploy/README.md](api/deploy/README.md)에 있습니다.

Nginx와 systemd 설정은 VPS의 기존 구성을 사용합니다. 서버 계정과 타이머를 다시 만들거나 과거 배포 파일을 청소하지 않습니다. 일반 배포에서 DB 마이그레이션과 데이터 수집도 수행하지 않습니다.

API는 scheduler 없이 시작한 뒤 readiness와 공개 OpenAPI 접속을 확인하며, 스케줄러 서비스는 systemd 의존성으로 API와 함께 재시작됩니다. 성능 baseline 측정과 전체 경로 warmup은 배포 중 자동 실행하지 않습니다. 스키마 전환에 필요한 soak 증거는 별도로 준비해야 하며 기존 전환 조건은 유지합니다.

대시보드를 배포할 Mac에는 `npm install -g vercel`로 Vercel CLI도 설치합니다. 대시보드 배포는 `.ops/.env`의 `VERCEL_DEPLOY_KEY`로 `eden-frontend` 프로젝트를 사용합니다. 배포 범위는 `dashboard/`이며 설치와 빌드는 Vercel에서 수행합니다. 브라우저에 필요한 공개 설정만 `VITE_` 접두사를 사용하며 비밀값에는 이 접두사를 붙이지 않습니다.

배포와 데이터 변경 명령은 실제 운영에 영향을 줍니다. 스키마 변경은 별도 요청이 있을 때만 실행하며 DB 백업과 복원 명령은 비활성화돼 있습니다.

## 유지보수

기존 `scripts/`는 제거했습니다. 클라이언트 실행은 `.ops/client.sh`와 `.ops/api_and_client.sh`로 구분합니다. 공통 실행 및 유지보수는 `.ops/api_and_client.sh`, 배포는 `.ops/deploy.sh`가 담당하고 재사용하는 Python 로직은 `api/app/operations/`에 둡니다.

- 수집: `api_and_client.sh import-mois`, `import-inbound`, `import-notices`
- 게시: `api_and_client.sh seed-reference`
- 이전 게시본 선택: `api_and_client.sh restore-snapshot ENDPOINT KEY`
- 운영 증거: `api_and_client.sh phase1-soak`, `phase2-soak`, `pilot` 뒤에 하위 명령 지정

수집과 게시 명령은 DB를 변경합니다. 실제 운영 상태를 판정하는 `deploy.sh soak-7d`와 `deploy.sh finalize-phase1-contract`는 VPS의 현재 release에 있는 `.ops/deploy.sh`로 실행합니다. 후자는 기존 스키마 전환 조건을 유지합니다.
