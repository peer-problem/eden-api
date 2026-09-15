# EDEN Dashboard

React와 TypeScript로 만든 관광 데이터 탐색 화면입니다. 운영 주소는 https://edenapi.org 이며 https://api.edenapi.org/v1 을 조회합니다.

## 로컬 실행

저장소 루트에서 실행합니다.

```bash
.ops/run.sh sync
.ops/run.sh dashboard
```

http://127.0.0.1:5173 에서 열립니다. 개발 서버의 기본 프록시는 운영 API를 사용합니다. 공개 API 주소를 바꾸려면 루트 `.env`의 `VITE_EDEN_API_URL`을 설정합니다. 비밀값에는 `VITE_` 접두사를 사용하지 않습니다.

`.ops/`가 없는 환경에서는 이 디렉터리에서 직접 실행할 수 있습니다.

```bash
npm ci
npm run dev
npm run build
npm test
```

## DB 작업 공간

루트의 `.ops/run.sh explorer`는 SSH 터널과 로컬 API, 대시보드를 함께 실행합니다. 내부 API 인증 토큰은 실행할 때 생성하고 프로세스 사이에서만 공유합니다. 종료하면 두 서버와 터널을 정리합니다.

실제 DB 조회는 인증된 로컬 내부 API에만 연결합니다. Vercel의 정적 배포에서는 공개 API 화면과 테이블 구조 탐색을 사용할 수 있습니다. 내부 API가 연결되지 않으면 DB 연결 전 상태를 표시합니다.

`.ops/run.sh export-catalog`는 DB에 연결하지 않고 ORM에서 `src/explorer/catalog.json`을 갱신합니다.

## 배포

루트에서 `.ops/deploy.sh dashboard`를 실행합니다. `VERCEL_DEPLOY_KEY`는 루트 `.env`에서 읽으며, Vercel의 `eden-frontend` 프로젝트에 배포합니다. 결과물은 `dist/`에 생성됩니다.

## 표시 원칙

API에서 받은 값만 표시합니다. 누락값과 실제 0을 구분하며 데이터가 없으면 서버가 제공한 사유를 보여줍니다. 출처와 기준일을 함께 표시하고 조회 조건이 바뀌면 이전 응답을 숨깁니다. 기본 요청 대기 시간은 15초입니다.

시도 지도는 [StatGarten의 korea-maps](https://github.com/swcho/korea-maps)에서 배포한 SGIS 2020 경계를 사용합니다. [원본 MIT 라이선스](src/assets/korea-sido.LICENSE)를 유지합니다.
