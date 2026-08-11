# EDEN API

한국 관광·인바운드 데이터를 수집하고 MariaDB에 정규화한 뒤 FastAPI로 제공하는
서비스입니다. 공개 API 기본 경로는 `/v1`입니다.

## 로컬 실행

`.env`에 원격 MariaDB 접속 정보(`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`,
`DB_PASSWORD`)를 설정합니다. MariaDB는 비밀번호로 인증하며 PEM 파일은 사용하지
않습니다. 원격 DB 통신은 암호화되지 않으므로 VPS 방화벽이 현재 개발자 IP의
3306 포트만 허용합니다.

```bash
uv sync
./.deploy/run.sh
```

로컬 실행에서는 스케줄러를 끄고 API만 시작합니다.

## DB migrations

`migrations/`는 Alembic이 관리하는 MariaDB 스키마 변경 이력입니다. 새 서버에
테이블을 만들고 기존 DB를 안전하게 최신 구조로 올리는 데 필요하므로 삭제하지
않습니다. 배포 시 아래 명령이 자동 실행됩니다.

```bash
uv run alembic upgrade head
```

## 배포

배포 관련 파일은 모두 `.deploy/`에 있습니다.

```bash
./.deploy/deploy.sh          # 애플리케이션 배포
./.deploy/sync-env.sh        # API 키 등 운영 환경변수 동기화
```

`scripts/`에는 배포 중 필요한 기준 데이터 적재 스크립트만 남겨 두었습니다.
