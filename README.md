# EDEN

관광 데이터를 수집하고 게시된 통계를 API와 대시보드로 제공합니다.

| 서비스 | 주소 | 배포 위치 |
| --- | --- | --- |
| 대시보드 | https://edenapi.org | Vercel |
| API 문서 | https://api.edenapi.org/docs | 운영 VPS |
| OpenAPI | https://api.edenapi.org/openapi.json | 운영 VPS |

## 디렉터리

```text
.agents/       작업 기록과 로컬 자료, Git 제외
.ops/          run.sh와 deploy.sh, Git 제외
dashboard/    React + TypeScript + Vite
api/          FastAPI 코드, 마이그레이션, 테스트, Python 의존성
.env           공통 환경 설정 원본, Git 제외
.gitignore
README.md
assets/        공통 문서 이미지
AGENTS.md      프로젝트 작업 가이드, Git 제외
```

Git 관리용 `.git/`은 그대로 유지합니다. API 가상환경과 테스트 캐시는 `api/` 안에 생성됩니다.

## 시작하기

관리자에게 루트 `.env`와 `.ops/`의 두 파일을 받습니다. 다른 Mac에서도 같은 파일을 사용합니다. 스크립트가 현재 사용자 홈과 Homebrew 설치 위치를 찾아 Apple Silicon과 Intel 경로를 선택합니다.

Mac에는 Homebrew와 Xcode Command Line Tools가 필요합니다. 처음 한 번 다음 도구를 설치합니다.

```bash
brew install uv node@24 mariadb-connector-c sshpass
chmod 600 .env
chmod +x .ops/run.sh .ops/deploy.sh
```

Python 3.12는 uv가 준비합니다. DB 연결에는 MariaDB Connector/C 3.4 이상이 필요하며, `mariadb-connector-c`는 로컬 DB 서버를 설치하지 않습니다. `.env`는 셸의 `source`로 실행하지 않습니다. 다른 Mac으로 `api/.venv`나 `dashboard/node_modules`를 복사하지 말고 아래 `sync`로 설치합니다.

루트에서 실행합니다.

```bash
.ops/run.sh sync           # API와 대시보드 의존성 설치
.ops/run.sh check          # 환경 설정 검사
.ops/run.sh db-check       # SSH와 TLS를 통한 읽기 전용 DB 검사
.ops/run.sh                # API: http://127.0.0.1:8000/docs
.ops/run.sh dashboard      # 대시보드: http://127.0.0.1:5173
```

기본 대시보드는 운영 API를 조회합니다. 로컬 API와 인증된 DB 탐색을 함께 실행하려면 `.ops/run.sh explorer`를 사용합니다. 이 명령은 SSH 터널과 두 서버를 함께 관리하며, 내부 토큰은 메모리에서만 공유합니다.

일반 실행은 SSH 터널이 준비되면 시작하며 별도 DB 진단 쿼리는 `db-check`에서만 수행합니다. 개발 명령은 기존 원격 DB에 SSH 터널로 연결합니다. 개발 계정을 사용하고 scheduler를 끕니다. 마이그레이션만 별도 migration 계정을 선택합니다.

```bash
.ops/run.sh migrate current   # 현재 스키마 버전, 읽기 전용
.ops/run.sh exec COMMAND...   # api/ 안에서 개발 DB 환경으로 도구 실행
.ops/run.sh export-catalog    # DB 접속 없이 대시보드의 스키마 정의 갱신
.ops/run.sh test              # API 검사와 전체 테스트, 대시보드 빌드와 테스트
```

`.ops/`가 없는 환경에서도 `api/`에서 uv 명령을, `dashboard/`에서 npm 명령을 실행할 수 있습니다. API 사용법은 [API 문서](api/README.md)를 참고합니다.

## 배포

```bash
.ops/deploy.sh env-check        # 운영 환경 설정 생성 검사
.ops/deploy.sh preflight        # API 설정 생성, 빌드와 개발 DB 연결 검사
.ops/deploy.sh deploy           # API를 기존 VPS에 배포
.ops/deploy.sh dashboard-check  # 대시보드 로컬 빌드
.ops/deploy.sh dashboard        # 대시보드를 Vercel에 운영 배포
```

의존성 설치는 `run.sh sync`, 전체 테스트는 `run.sh test`로 직접 실행합니다. 배포 명령은 전체 테스트를 자동 실행하지 않습니다. `dashboard-check`는 설치된 의존성으로 빌드만 확인합니다.

API 배포는 이미 구성된 VPS에 `api/`와 두 운영 실행 파일을 전송합니다. 운영 의존성을 설치하고 `.env`에서 생성한 설정을 반영한 뒤 API를 재시작합니다. SSH fingerprint와 DB TLS, 계정별 권한을 확인하며 실패하면 이전 release와 환경 파일을 복구합니다.

Nginx와 systemd 설정은 VPS의 기존 구성을 사용합니다. 서버 계정과 타이머를 다시 만들거나 과거 배포 파일을 청소하지 않습니다. 일반 배포에서 DB 마이그레이션과 데이터 수집도 수행하지 않습니다.

API는 설정된 scheduler 상태로 한 번 시작한 뒤 readiness와 공개 OpenAPI 접속을 확인합니다. 성능 baseline 측정과 전체 경로 warmup은 배포 중 자동 실행하지 않습니다. 스키마 전환에 필요한 soak 증거는 별도로 준비해야 하며 기존 전환 조건은 유지합니다.

대시보드를 배포할 Mac에는 `npm install -g vercel`로 Vercel CLI도 설치합니다. 대시보드 배포는 루트 `.env`의 `VERCEL_DEPLOY_KEY`로 `eden-frontend` 프로젝트를 사용합니다. 배포 범위는 `dashboard/`이며 설치와 빌드는 Vercel에서 수행합니다. 브라우저에 필요한 공개 설정만 `VITE_` 접두사를 사용하며 비밀값에는 이 접두사를 붙이지 않습니다.

배포와 데이터 변경 명령은 실제 운영에 영향을 줍니다. 스키마 변경은 별도 요청이 있을 때만 실행하며 DB 백업과 복원 명령은 비활성화돼 있습니다.

## 유지보수

기존 `scripts/`는 제거했습니다. 실행 명령은 두 `.ops/` 파일로 통합하고, 재사용하는 Python 로직은 `api/app/operations/`에 둡니다.

- 수집: `run.sh import-mois`, `import-inbound`, `import-notices`
- 게시: `run.sh seed-reference`, `publish-recommendations`
- 이전 게시본 선택: `run.sh restore-snapshot ENDPOINT KEY`
- 운영 증거: `run.sh phase1-soak`, `phase2-soak`, `pilot` 뒤에 하위 명령 지정

수집과 게시 명령은 DB를 변경합니다. 실제 운영 상태를 판정하는 `deploy.sh soak-7d`와 `deploy.sh finalize-phase1-contract`는 VPS의 현재 release에 있는 `.ops/deploy.sh`로 실행합니다. 후자는 기존 스키마 전환 조건을 유지합니다.
