"""Public documentation generated together with the API contract."""

DESCRIPTION = """
한국 관광 트렌드와 지역 관광 정보 및 방한시장 데이터를 제공하는 **EDEN 공식 API 명세**입니다.
아래 각 API를 펼치면 입력값, 응답 스키마와 `Try it out`을 사용할 수 있습니다.
[OpenAPI JSON 다운로드](/openapi.json)

### 시작하기

- 운영 주소: `https://api.edenapi.org`
- 현재 공개 API는 별도 인증 키 없이 사용할 수 있습니다. 원천 서비스의 키는 전달하지 않습니다.
- 브라우저의 다른 도메인에서도 호출할 수 있습니다. 쿠키와 인증 정보를 포함하지 마세요.
- API는 DB에 게시된 데이터만 조회합니다. 추천 POST도 외부 수집이나 LLM 호출을 실행하지 않습니다.

```bash
curl --get 'https://api.edenapi.org/v1/trends' \
  --data-urlencode 'keyword=Korea travel' --data 'country=US&social_sources=youtube'
curl 'https://api.edenapi.org/v1/markets/inbound?countries=JP&countries=CN&include=fx'
```

```javascript
const response = await fetch('https://api.edenapi.org/v1/markets/JP/alerts?limit=5');
if (!response.ok) throw new Error(`HTTP ${response.status}`);
const { data, meta } = await response.json();
console.log(data, meta.availability, meta.as_of);
```

### 입력 규칙

- `country`와 `countries`는 ISO 3166-1 두 글자 국가 코드입니다. 예: `JP`, `CN`, `US`.
- `currency`는 ISO 4217 세 글자 통화 코드입니다. 예: `JPY`, `CNY`, `USD`.
- 배열은 같은 이름을 반복합니다. 예: `countries=JP&countries=CN`, `include=visitors&include=fx`.
- `area_code`에는 행정구역 코드 또는 EDEN 지역 ID를 사용합니다.
- `content_id`에는 EDEN 관광지 ID를 사용합니다. 기존 TourAPI 콘텐츠 ID도 별칭으로 조회됩니다.
- 날짜는 ISO 8601 형식입니다. 시간에는 시간대 정보를 포함하세요.
  응답의 기본 시간대는 `Asia/Seoul`입니다.
  원천이 날짜만 제공하는 경우에는 시각 단위의 정밀도를 보장하지 않습니다.
- 필수값과 허용값 및 상한은 각 API의 Parameters와 Schemas에 명시되어 있습니다.

### 응답 읽기

성공 응답은 `{ "data": ..., "meta": ... }` 구조입니다. HTTP 200만으로 데이터 존재를 판단하지 마세요.

| 필드 | 의미 |
| --- | --- |
| `meta.availability` | `available`: 가능, `partial`: 일부 가능, `unavailable`: 불가 |
| `meta.reason` | 데이터가 부족하거나 제공되지 않는 구체적인 이유 |
| `meta.as_of` | 게시 데이터의 기준 시점. 현재 시각이나 원천 공통 발표일이 아닙니다. |
| `meta.generated_at` | 이 응답을 만든 시각. 원천 데이터 갱신 시각이 아닙니다. |
| `meta.freshness` / `meta.stale` | 허용된 데이터 수명과 최신성 상태 |
| `meta.sources` | 원천별 가용성, 데이터 기준일, 마지막 성공 시각과 사유 |
| `meta.formula_versions` | 지표 계산에 사용된 공식 버전 |
| `meta.request_id` | 문제 확인에 사용할 요청 식별자. `X-Request-ID` 헤더에도 포함됩니다. |

`data`나 개별 값은 `null`일 수 있습니다. 이를 0으로 바꾸지 마세요. 데이터 블록에도 별도의
`availability`와 `reason`이 있을 수 있으므로 화면에서 해당 블록의 상태를 함께 확인하세요.
수집은 제한된 분량으로 순환 실행합니다. 월간 통계는 발표 주기를 따릅니다.
모든 지역이 동시에 갱신되지는 않습니다.
지역 방문 데이터는 약 30일 늦게 발표되는 원천이며, 해당 지연을 반영해 최신성을 판단합니다.

### 지표와 제공 범위

트렌드는 사전에 수집한 키워드만 조회합니다. 임의의 검색어를 입력해도 수집을 시작하지 않습니다.
현재 YouTube의 수집 키워드는 다음과 같습니다.
다른 키워드나 지역 세부 조건에는 데이터가 없을 수 있습니다.

| `country` | `keyword` |
| --- | --- |
| `CN` | `韩国旅游` |
| `JP` | `韓国旅行` |
| `TW` | `韓國旅遊` |
| `US` 또는 `PH` | `Korea travel` |


- YouTube 지표는 검색 결과 영상의 공개 지표입니다. 검색 국가 조건은 시청자 국적을 뜻하지 않습니다.
- Instagram과 Facebook 및 Reddit은 승인된 데이터가 없어 `unavailable`로 표시될 수 있습니다.
  NAVER 검색 추세는 저장과 재제공 권리 확인 전까지 비활성 상태입니다.
- X와 TikTok은 수집 대상이 아닙니다.
  Weibo와 Douyin 및 Xiaohongshu도 제외하며 LINE도 수집하지 않습니다.
- ECOS 관광수지는 한국 전체의 월간 일반여행 수지입니다. 국가별 양자 수지가 아닙니다.
- 여행 예상 예산과 연령 지수처럼 근거 원천이 없는 값은 추정해서 채우지 않습니다.
- 공지의 번역과 요약은 Upstage를 사용합니다. 원문 링크와 번역 가용성을 함께 확인하세요.
- 공지 자동 갱신 대상은 5개 시장의 한국 공관과 K-ETA 및 한국관광공사입니다.
  `source_scope=local`의 현지 기관 공지는 자동 갱신을 지원하지 않습니다.
  과거 저장된 공지가 반환되더라도 원천의 가용성과 갱신 시각을 확인하세요.
- 공지나 날씨 등 원천에 발표 시각이 없으면 그 사유를 표시합니다. 
  수집 시각을 발표 시각으로 대체하지 않습니다.

### 호출 제한과 오류

운영 서버는 IP당 초당 5회와 전체 초당 20회로 제한합니다. 일시 초과 허용량은 각각 20회와 40회입니다.
프론트에서는 응답을 재사용하고 불필요한 반복 조회를 피하세요. 
429와 일시적인 5xx는 간격을 늘려 재시도하세요.
요청 본문 상한은 64KiB이며 응답 상한은 2MiB입니다.

| HTTP | 처리 방법 |
| --- | --- |
| 404 | 지역이나 관광지 ID 또는 URL을 확인하세요. |
| 413 | 요청 본문 크기를 줄이세요. |
| 422 | 필수값과 허용값을 확인하세요. `error.details`에 검증 위치가 있습니다. |
| 429 | 호출 제한 초과입니다. 잠시 후 재시도하세요. 프록시 오류 본문은 JSON이 아닐 수 있습니다. |
| 500 | `request_id`를 남기세요. `RESPONSE_TOO_LARGE`이면 조회 범위를 줄이세요. |
| 502 / 503 / 504 | 서버의 일시적인 처리 제한이나 장애입니다. 잠시 후 재시도하세요. |

앱 오류는 `{ "request_id": "...", "error": { "code": "...", "message": "..." } }` 형식입니다.
프록시에서 생성한 오류에는 이 형식이 없을 수 있으므로 HTTP 상태부터 검사하세요.
"""

TAGS = [
    {"name": "trends", "description": "키워드 관심도와 원천별 지표"},
    {"name": "regions", "description": "지역 방문과 관광 수요 및 다양성"},
    {"name": "places", "description": "관광지 상세와 주변 정보"},
    {"name": "forecasts", "description": "방문 전망과 날씨 및 행사"},
    {"name": "visitors", "description": "방문 지표 시계열"},
    {"name": "markets", "description": "방한시장 비교와 국가별 공식 공지"},
    {"name": "recommendations", "description": "여행 조건에 따른 목적지 추천"},
]
