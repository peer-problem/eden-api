"""Public documentation generated together with the API contract."""

import json
from pathlib import Path

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
| `CN` | `韩国旅游` / `首尔旅游` / `济州岛旅游` |
| `JP` | `韓国旅行` / `ソウル旅行` / `済州島旅行` |
| `TW` | `韓國旅遊` / `首爾旅遊` / `濟州島旅遊` |
| `US` 또는 `PH` | `Korea travel` / `Seoul travel` / `Jeju travel` |


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

### 기본 요청 예시

```text
GET /v1/trends?keyword=Korea%20travel&country=US&social_sources=youtube
GET /v1/regions/1100000000/insights?period=30d
GET /v1/places/eden_place_161fb775402b53b78a0a?lang=ko&shops_limit=5
GET /v1/forecasts/visitors?area_code=1100000000&days=7
GET /v1/visitors/timeseries?area_code=1100000000&period=30d&granularity=day
GET /v1/markets/inbound?countries=JP&countries=CN
GET /v1/markets/JP/alerts?limit=20
POST /v1/recommendations/destinations
{"target_country":"JP","travel_window":{"season":"autumn"},"themes":["culture"],"limit":5}
```

### 최소 범위와 계산 계약

계획의 상한은 17개 시도와 JP/CN/TW/US/PH입니다. 실제 수집은 최신 활성 시도 기준입니다.
2026년 7월 행정구역 개편으로 현재 기준정보는 16개 시도이며 광주와 전남의 과거 코드는 보존합니다.
관광지는 한국어와 좌표 및 분류가 있는
고유 장소를 시도당 최대 30곳 선정합니다. 아직 확보하지 못한 지역과 기간은 결측으로 표시합니다.
`keyword`는 NFKC 정규화와 양끝 공백 제거 후 검증합니다. 빈 검색어는 422입니다.
트렌드 기간은 7일, 30일, 90일이며 확보된 최신 관측을 끝점으로 조회합니다.
`social_sources` 생략 시 YouTube를 사용합니다. 명시적으로 선택한 SNS만 지표와 최신성에 반영합니다.
영상 수와 조회 수는 검색 표본이며 매일의 표본 합계를 고유 영상 수나 국적별 시청자로 해석하지 마세요.

방문 시계열은 최근 456일의 제한된 일별 관측에서 계산합니다. `basis_period`는 실제 조회 창이며
자료 발표 지연 때문에 오늘과 다를 수 있습니다. 비교 구간이 불완전하면 변화율은 null입니다.
월간 수요와 다양성은 각자의 최신 `data_period`를 유지합니다.
상세 지역 대신 시도를 사용하면 `requested_area_code`와 응답 `area`가 실제 자료 범위를 나타냅니다.

방문 전망 기본값은 7일입니다. 1일부터 30일까지 명시적 요청을 받습니다.
공식 예측이 없으면 최근 90일 내 28일 이상 관측에서 같은 요일의 중앙값을 구하고
지역 관측 분포의 동률 중간 순위 백분위를 `demand_score`로 제공합니다.
요일 표본이 3개 미만이면 전체 중앙값을 씁니다. 최신 관측이 60일보다 오래되면 제공하지 않습니다.
`historical_weekday_proxy`는 참고 수요 지수이며 인원 예측이 아닙니다. 7일 이후도 같은 역사적 기준을
연장한 값입니다. `expected_visitors`와 검증되지 않은 `confidence`는 null입니다.
날씨와 행사는 별도 참고 정보이며 임의의 방문객 증감 계수를 적용하지 않습니다.

`change_rate`는 백분율(%)입니다. `krw_rate`는 외화 1단위당 원화(KRW)입니다.
항공편 수와 여객 수는 서로 대체하지 않습니다. 각 원천은 자체 발표 주기로 최신성을 판단합니다.
`data_as_of`는 관측 시점, `last_checked_at`은 마지막 확인 시도, `last_success_at`은 마지막
정상 확인 시각입니다. 공지는 새 발표가 없어도 정상 확인 시각으로 갱신 상태를 판단합니다.
`since`는 시간대가 있어야 하며 발표 또는 수집 시각이 경계값 이상인 공지를 반환합니다.
정렬은 수집 시각 내림차순이며 동률이면 발표 시각 내림차순 후 ID 오름차순입니다.
일반 공지는 최근 90일의 최신 20건, 현재 활성 입국 규정은 별도 최신 1건을 보존합니다.

추천의 `travel_window.days`와 `party_size`는 deprecated 호환 입력입니다.
일정 가능성과 수용량을 보장하지 않습니다. 예산도 검증 원천이 없어 적용하지 않습니다.
`applied_constraints`와 `unapplied_inputs`에서 실제 적용 조건과 미지원 사유를 확인하세요.
접근성이나 이동시간 등의 필수 조건을 검증할 수 없으면 추천을 unavailable로 반환합니다.

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

PARAMETER_DESCRIPTIONS = {
    "keyword": "사전 수집한 검색어. NFKC 정규화 후 양끝 공백을 제거하며 빈 값은 422입니다.",
    "country": ("ISO 3166-1 alpha-2 국가 코드. 초기 시장은 JP/CN/TW/US/PH입니다."),
    "countries": (
        "비교할 국가 목록. 같은 파라미터를 반복합니다. 초기 시장은 JP/CN/TW/US/PH입니다."
    ),
    "area_code": (
        "행정구역 코드 또는 EDEN 지역 ID. 시도 대체 시 실제 자료 지역을 응답에 표시합니다."
    ),
    "social_sources": ("선택 SNS만 조회합니다. 생략 시 youtube. 미승인 원천은 unavailable입니다."),
    "period": "최신 확보 관측을 끝점으로 하는 조회 기간. 미수집 구간은 결측으로 표시합니다.",
    "time_unit": ("트렌드 관측의 집계 간격. day/week/month이며 고유 영상 수를 뜻하지 않습니다."),
    "limit": "반환 항목 수. 기본값과 최대값은 schema에 표시됩니다.",
    "visitor_type": ("all은 전체, domestic은 내국인, foreign은 외국인 방문 지표입니다."),
    "compare": ("이전 동일 길이 기간 또는 전년 동기 비교. 완전한 비교 관측이 없으면 null입니다."),
    "include": (
        "반환할 정보 블록. 배열은 같은 이름을 반복합니다. 생략 시 열거된 기본 블록 전체입니다."
    ),
    "content_id": ("정규화된 EDEN 관광지 ID 또는 검증된 원천 별칭. 존재하지 않으면 404입니다."),
    "lang": "요청 관광지 언어. 미보유 언어는 한국어, 한국어도 없으면 실제 보유 언어를 표시합니다.",
    "radius_m": "주변 상점 검색 반경. 단위 m. 보유한 주변 상점에서 조회합니다.",
    "related_limit": "연관 관광지 최대 반환 수. 주변 상점 개수와 별도입니다.",
    "shops_limit": "가까운 주변 상점 반환 수. 기본 5개, 최대 20개입니다.",
    "place_name": (
        "사전 수집된 공식 전망의 관광지명. 지역 참고 지수를 관광지 인원으로 대체하지 않습니다."
    ),
    "days": ("전망 일수. 기본 7일, 1~30일. 7일 이후 참고 전망은 동일 역사적 기준의 연장입니다."),
    "nx": "기상청 격자의 X 좌표. ny와 함께 입력하며 미수집 격자는 unavailable입니다.",
    "ny": "기상청 격자의 Y 좌표. nx와 함께 입력하며 미수집 격자는 unavailable입니다.",
    "granularity": ("방문 집계 단위. day/week/month. 더 거친 관측을 일별 값으로 나누지 않습니다."),
    "attraction_name": ("관광지별 방문 원천의 관광지명. 관측이 없으면 unavailable입니다."),
    "currency": (
        "ISO 4217 통화 코드. 환율은 외화 1단위당 KRW이며 미보유 통화는 unavailable입니다."
    ),
    "forecast_days": "향후 항공 일정 조회 일수. 기본 7일, 1~7일입니다.",
    "types": "공식 공지 유형 필터. 일반 공지 90일, 활성 입국 규정 최신본을 대상으로 합니다.",
    "source_scope": (
        "korean은 한국 기관, local은 현지 기관, all은 전체. 현지 자동 수집은 미지원입니다."
    ),
    "since": (
        "시간대 있는 ISO 8601 시각. 발표 또는 수집 시각이 해당 경계 이상인 공지를 반환합니다."
    ),
    "language": "공지 요청 언어. 번역이 없으면 실제 원문 언어로 반환합니다.",
}

FIELD_DESCRIPTIONS = {
    "change_rate": ("이전 비교 구간 대비 변화율(%). 기준값 0 또는 비교 관측 부족 시 null."),
    "visitor_change_rate": ("전기 대비 방문 지표 변화율(%). 비교 관측 부족 시 null."),
    "krw_rate": "외화 1단위당 원화(KRW). 검증된 환율 관측이 없으면 null.",
    "data_as_of": "해당 원천 관측의 기준 시점. 마지막 수집 시각과 다를 수 있습니다.",
    "last_checked_at": ("마지막 수집 확인을 시도한 시각. 성공 여부는 status로 구분합니다."),
    "last_success_at": "변경 없음도 포함한 마지막 정상 원천 확인 시각.",
    "generated_at": "현재 응답 또는 번역을 생성한 시각. 원천 관측 시각이 아닙니다.",
    "as_of": "선택한 게시 자료의 기준 시각. 원천마다 실제 관측 시점이 다릅니다.",
    "basis_period": ("계산 또는 조회에 사용한 실제 관측 구간. start와 end 경계를 포함합니다."),
    "data_period": ("해당 월간 지표의 최신 관측 월(YYYY-MM). 일별 조회 기간과 독립적입니다."),
    "demand_score": (
        "0~100 참고 수요 지수. 역사적 요일 중앙값의 동률 중간 순위 백분위 또는 공식 지표."
    ),
    "expected_visitors": (
        "공식 원천이 제공한 예상 인원. 참고 지수로 인원을 만들지 않으며 없으면 null."
    ),
    "confidence": "검증된 신뢰도만 허용합니다. 현재 참고 전망에서는 null.",
    "method": ("official은 공식 전망. historical_weekday_proxy는 방문 관측에 기반한 참고 지수."),
    "sample_count": "참고 전망의 분포에 사용한 유효 일별 관측 수. 같은 날짜는 한 번 셉니다.",
    "basis": "같은 요일 표본 수와 전체 중앙값 대체 여부 및 장기 연장 설명.",
    "posts": "검색 표본의 게시물 또는 영상 관측 수. 날짜별 합계는 고유 게시물 수가 아닙니다.",
    "views": "검색 표본 영상의 조회 수 합계. 실제 국적별 시청자 수가 아닙니다.",
    "reactions": "해당 원천 표본의 반응 수. 미제공 반응을 0으로 대체하지 않습니다.",
    "passengers": ("원천이 제공한 항공 여객 수(명). 항공편 수를 대입하지 않으며 없으면 null."),
    "arriving_flights": "해당 국가에서 도착하는 운항편 수. 여객 수와 다른 지표입니다.",
    "completeness_ratio": ("요청 관측 창의 제공 비율(0~1). 누락된 날짜를 0명으로 해석하지 마세요."),
    "estimated_budget_krw": ("검증된 여행 비용(KRW). 현재 관광지별 원천이 없어 null."),
    "applied_constraints": "실제 필터와 순위에 반영한 입력 조건 및 값.",
    "unapplied_inputs": "명시적으로 전달했지만 적용하지 못한 입력의 이름, 값과 사유.",
    "requested_area_code": (
        "원래 요청한 지역. 시도 대체 자료를 요청 지역의 관측으로 해석하지 마세요."
    ),
    "data_area_code": "실제 전망 산출에 사용한 지역 코드.",
    "fallback": "요청 언어와 실제 반환 언어가 다르면 true.",
    "adjustment_factors": "현재 전망은 임의의 날씨 및 행사 보정을 하지 않으므로 빈 객체.",
}


FIELD_DESCRIPTIONS.update(
    {
        "accessibility_required": "필수 접근성 조건. 검증 원천이 없어 true이면 추천 불가.",
        "address": "실제 반환 언어의 원천 주소. 미보유 시 null.",
        "age_index": "공식 원천의 연령 다양성 지수(0~100). 원천이 없으면 null.",
        "age_seconds": "응답 생성 시각에서 자료 기준 시각까지 지난 초. 기준일이 없으면 null.",
        "area": "실제 자료에 대응하는 행정구역.",
        "area_code": "행정구역 코드. 요청 시에는 EDEN 지역 ID도 허용.",
        "attraction_name": "관광지별 방문 원천의 조회 대상 이름. 지역 집계이면 null.",
        "availability": "available은 제공 가능, partial은 일부 제공, unavailable은 제공 불가.",
        "available_languages": "현재 보유한 콘텐츠 언어 코드 목록.",
        "avg_stay_nights": "공식 관측의 평균 숙박일 수(박). 원천이 없으면 null.",
        "avoid_crowds": "보유한 혼잡 참고 지표를 추천 순위에 반영. 기본 false.",
        "baseline_start": "비교 관측 창 시작 날짜. 경계 포함.",
        "baseline_end": "비교 관측 창 마지막 날짜. 경계 포함.",
        "budget_availability": "비용 검증 가능 여부. 현재 원천이 없어 unavailable.",
        "budget_krw": "요청 예산(KRW). 현재 비용 검증 원천이 없어 순위에 미반영.",
        "category": "원천에서 확인한 장소 분류 코드 또는 이름. 임의 테마를 만들지 않음.",
        "code": "클라이언트가 오류를 구별하는 안정된 오류 코드.",
        "comparison": "요청한 이전 기간 또는 전년 동기 비교. 미요청 시 null.",
        "concentration_rate": "원천 방문 집중률(%). 원천이 없으면 null.",
        "condition": "기상청 관측 코드에서 해석한 날씨 상태. 미보유 시 null.",
        "constraints": "추천 필터와 필수 조건. 적용 여부는 응답에서 확인.",
        "content_id": "상세 조회에 사용할 고유 EDEN 관광지 ID.",
        "country": "시장 ISO alpha-2 코드. 트렌드 all은 수집 시장 전체.",
        "crowd_index": "보유 원천에 기반한 0~100 혼잡 참고 지수. 근거가 없으면 null.",
        "currency": "ISO 4217 통화 코드.",
        "daily": "요청 시작일부터 날짜별 전망. 전망 근거가 없으면 해당 값은 null.",
        "data": "선택한 API의 결과. 핵심 자료를 제공할 수 없으면 null일 수 있음.",
        "date": "Asia/Seoul 기준 전망 대상 날짜.",
        "demand": "공식 월간 수요 지표 블록. 미선택 시 null.",
        "destination": "항공편의 도착 공항 또는 노선 목적지 원천 코드.",
        "destination_searches": "해당 관측 구간의 관광지 검색 수. 원천이 없으면 null.",
        "details": "오류 필드 위치와 사유. 부가 정보가 없으면 null.",
        "distance_m": "관광지 좌표에서 상점 좌표까지 직선거리(m). 이동거리가 아님.",
        "diversity": "공식 월간 다양성 지표 블록. 미선택 시 null.",
        "domestic": "원천 내국인 방문 지표(연인원). 없거나 외국인만 요청하면 null.",
        "eden_area_id": "행정구역의 EDEN 고유 ID.",
        "end": "관측 또는 계산 구간의 마지막 날짜. 경계 포함.",
        "error": "실패 코드와 메시지 및 입력 오류 정보.",
        "extra": "추가 필수 조건. 비어 있지 않으면 미지원 키를 밝히고 추천 불가.",
        "festivals": "실제 원천의 해당 구간 행사 참고 정보. 전망 인원 보정에 사용하지 않음.",
        "field": "적용하지 못한 입력의 필드 경로.",
        "flight_schedule": "요청한 향후 1~7일의 항공 일정 집계. 미선택 시 null.",
        "flights": "해당 기간 또는 노선의 운항편 수. 미수집 집계는 null.",
        "forecast_days": "항공 일정 집계 일수. 기본 7, 최소 1, 최대 7.",
        "foreign": "원천 외국인 방문 지표(연인원). 없거나 내국인만 요청하면 null.",
        "formula_version": "추천 점수 계산의 공식 버전.",
        "formula_versions": "사용한 제품과 지표별 계산 공식 버전.",
        "freshness": "선택한 원천의 발표 주기를 반영한 최신성 상태.",
        "fx": "해당 시장 통화의 환율 블록. 미선택 시 null.",
        "grain": "원천 관측의 집계 단위. 실제 원천보다 세밀하게 분해하지 않음.",
        "granularity": "반환 방문 시계열의 day/week/month 집계 단위.",
        "grid_source": "기상 자료의 공간 근거. parent_area는 시도 대표 격자 대체.",
        "holiday": "공식 공휴일 참고 정보. 미보유 시 null 또는 빈 목록.",
        "horizon_days": "전망 반환 일수. 기본 7, 최소 1, 최대 30.",
        "hub": "보유한 관광거점 원천의 정보. 원천이 없으면 null.",
        "id": "공지 고유 ID. 수정본은 같은 ID를 사용.",
        "inbound_score": "관측에 기반한 방한시장 비교 지수(0~100). 근거 부족 시 null.",
        "interest_index": "비교 가능한 관측의 관광 관심 참고 지수(0~100). 비교 부족 시 null.",
        "is_hub": "관광거점 공식 원천에서 거점으로 분류했는지 여부.",
        "items": "필터와 정렬을 적용한 실제 공지 목록. 없으면 빈 목록.",
        "keyword": "NFKC 정규화하고 양끝 공백을 제거한 수집 검색어.",
        "language": "반환 콘텐츠의 실제 언어 코드.",
        "language_original": "공지 원문의 실제 언어 코드.",
        "lat": "WGS84 위도(도). 미보유 시 null.",
        "limit": "최대 반환 건수. 추천 기본 5, 최소 1, 최대 20.",
        "lng": "WGS84 경도(도). 미보유 시 null.",
        "location": "원천 WGS84 좌표. 위치 가용성을 함께 확인.",
        "location_availability": "좌표 제공 가능 여부. 미보유 좌표를 0으로 대체하지 않음.",
        "major_routes": "기간 내 운항편 수 상위 주요 노선. 최대 10개.",
        "markets": "선택한 국가별 방한시장 정보.",
        "max_acceptable_age_seconds": "발표 주기를 반영한 허용 자료 수명(초). 미정이면 null.",
        "max_travel_minutes": "필수 최대 이동시간(분). 이동 원천이 없어 지정 시 추천 불가.",
        "message": "사람이 읽을 수 있는 오류 설명.",
        "meta": "가용성과 최신성 및 출처와 요청 추적 정보.",
        "name": "해당 원천에서 확인한 지역 또는 장소 이름.",
        "nationality_index": "공식 국적 다양성 지수(0~100). 원천이 없으면 null.",
        "nearby_shops": "반경 내 보유 상점을 직선거리순으로 반환. 기본 5, 최대 20.",
        "nx": "기상청 대표 격자 X 좌표. 미수집 시 null.",
        "ny": "기상청 대표 격자 Y 좌표. 미수집 시 null.",
        "observed_at": "해당 검색 표본 지표의 실제 관측 시각. 미보유 시 null.",
        "origin": "항공편의 출발 공항 또는 노선 출발지 원천 코드.",
        "overview": "보유한 관광지 소개. 요청 언어가 없으면 실제 반환 언어 사용.",
        "peak_concentration_rate": "조회 구간의 최대 방문 집중률(%). 없으면 null.",
        "peak_visitors": "조회 구간의 단일 집계 최대 방문 지표(연인원). 없으면 null.",
        "period": "최신 확보 관측을 끝점으로 하는 조회 기간.",
        "period_start": "해당 반환 집계 구간의 시작 날짜.",
        "place": "상세 조회로 연결되는 추천 관광지.",
        "place_category_counts": "선정 관광지의 실제 분류별 수(곳). 전수 통계가 아님.",
        "place_name": "요청한 공식 관광지 전망 이름. 지역 전망이면 null.",
        "precipitation_probability_pct": "기상청 강수 확률(%). 실제 예보가 없으면 null.",
        "published_at": "공지의 원문 발표 시각. 수집 시각과 구분.",
        "rank": "원천 거점 순위 또는 추천 결과 내 순위. 1부터 시작.",
        "rate_date": "적용한 환율의 관측 날짜. 미보유 시 null.",
        "reason": "자료 부족 또는 미지원이나 미적용의 구체적인 사유. 정상 제공이면 null.",
        "reasons": "추천의 실제 필터 또는 점수 근거 설명.",
        "recommendations": "조건에 맞는 고유 관광지 추천. 검증 불가 필수 조건이면 빈 목록.",
        "reference_information": "공식 지수와 구분한 보유 관광지 참고 정보. 구형 게시본이면 null.",
        "region": "추천 관광지가 실제 속하는 지역.",
        "related_places": "보유한 연관 관광지. 주변 상점과 별도 목록.",
        "relation_type": "원천에서 확인한 장소 간 연관 유형.",
        "request_id": "요청 추적 ID. X-Request-ID 응답 헤더와 같음.",
        "requested_language": "클라이언트가 요청한 언어 코드. 실제 language와 비교.",
        "rising_keywords": "비교 관측이 있는 수집 검색어의 상승 순위. 없으면 빈 목록.",
        "scope": "참고 정보의 실제 모집단과 해석 범위.",
        "score": "원천 또는 계산한 참고 점수(0~100). 근거 부족 시 null 가능한 필드만 null.",
        "score_as_of": "거점 또는 관계 점수의 원천 기준 시각. 미보유 시 null.",
        "search_ratio": "해당 검색 원천의 상대 검색 비율. 선택 원천이 제공하지 않으면 null.",
        "season": "여행 계절. 계절 혼잡 원천이 있는 경우에만 순위에 반영.",
        "semantics": "SNS 지표의 표본 의미. 실제 국적별 시청자 수가 아님.",
        "series": "시간순 관측 시계열. 결측은 0으로 채우지 않음.",
        "shop_id": "상점 원천의 고유 ID.",
        "sns_mentions": "선택 SNS 표본의 게시물 관측 수. 미보유 시 null.",
        "social_interest": "수집 SNS 표본의 참고 관심도. 미선택 시 null.",
        "source_availability": "선택한 원천별 제공 가능 여부와 사유.",
        "source_concentration_rate": "공식 전망 원천의 집중률(%). 미보유 시 null.",
        "source_country": "공식 공지 발행기관이 속한 국가 코드. 대상 시장과 구분.",
        "source_id": "EDEN 원천 레지스트리의 식별자.",
        "source_metrics": "선택한 원천의 실제 표본 수와 관측 지표.",
        "source_name": "공식 공지 발행기관 이름.",
        "source_type": "공식 발행기관의 유형.",
        "source_url": "원문을 직접 확인할 수 있는 URL.",
        "sources": "실제 사용한 출처 ID 또는 원천별 최신성 정보 목록.",
        "spatial_resolution": "실제 자료의 공간 단위. 요청 지역보다 넓을 수 있음.",
        "spend_index": "공식 원천의 관광 지출 지수(0~100). 없으면 null.",
        "stale": "선택한 자료가 원천 발표 주기별 허용 수명을 초과했는지 여부.",
        "start": "관측 또는 계산 구간의 시작 날짜. 경계 포함.",
        "status": "원천 수집 상태 또는 최신성 상태. 공지에서는 가용성의 상태 값.",
        "stay_index": "공식 원천의 관광 체류 지수(0~100). 없으면 null.",
        "subject_type": "area는 지역 전체 관측, attraction은 관광지별 관측.",
        "summary": "방문 집계 요약 또는 공지의 보유 요약. 없는 요약을 생성해 채우지 않음.",
        "summary_availability": "공지 요약의 제공 가능 여부.",
        "target_country": "추천 대상 시장 ISO alpha-2 코드. 초기 JP/CN/TW/US/PH.",
        "temperature_c": "기상청 예보 기온(섭씨). 실제 예보가 없으면 null.",
        "themes": "nature/culture/food/kpop 중 일치하는 테마를 가진 관광지 필터.",
        "time_unit": "트렌드 집계 간격 day/week/month.",
        "timestamp": "트렌드 반환 집계의 기준 시각.",
        "timezone": "날짜 해석 기본 시간대. Asia/Seoul.",
        "title": "실제로 반환한 언어의 콘텐츠 제목.",
        "title_original": "공지 원문의 제목. 번역 여부와 무관하게 제공.",
        "total": "선택한 방문 유형의 관측 합계(연인원). 고유 인원 수가 아니며 없으면 null.",
        "tourism_balance_period": "한국 전체 관광수지의 최신 관측 월(YYYY-MM). 없으면 null.",
        "tourism_balance_scope": "관광수지 적용 범위. 국가별 양자 수지가 아닌 한국 전체.",
        "translation_availability": "요청 언어 번역 제공 여부. 불가여도 원문은 제공 가능.",
        "translation_model": "보유 번역과 요약을 생성한 모델. 미생성 시 null.",
        "travel_window": "희망 계절과 deprecated 체류일 호환 입력.",
        "type": "공지 유형 또는 요청한 기간 비교 유형.",
        "updated_at": "공지 현재 수정본의 갱신 시각.",
        "value": "미적용 입력으로 명시적으로 전달한 원래 값.",
        "visitor_type": "all/domestic/foreign 중 반환 방문 유형.",
        "visitors": "방문 관측(연인원) 또는 지역 방문 블록. 보조 자료 미선택 시 null.",
        "weather": "실제로 제공되는 구간의 기상 참고 정보. 없으면 null.",
        "youtube_views": "YouTube 검색 표본의 영상 조회 수 합계. 시청자 국적 통계가 아님.",
    }
)


def document_contract(schema):
    """Attach shared units and source semantics to the generated OpenAPI schema."""
    examples = json.loads(Path(__file__).with_name("examples.json").read_text())
    unavailable = {
        "summary": "자료 부족 시 구조 예시",
        "description": "수치나 관측을 채우지 않은 unavailable 응답의 구조 예시입니다.",
        "value": {
            "data": None,
            "meta": {
                "request_id": "example-unavailable",
                "generated_at": "2026-09-11T12:00:00+09:00",
                "as_of": None,
                "timezone": "Asia/Seoul",
                "spatial_resolution": "none",
                "stale": False,
                "freshness": {
                    "status": "unavailable",
                    "age_seconds": None,
                    "max_acceptable_age_seconds": None,
                },
                "availability": "unavailable",
                "reason": "요청 조건에 게시된 자료가 없습니다.",
                "formula_versions": {},
                "sources": [],
            },
        },
    }
    for path, methods in schema.get("paths", {}).items():
        if not path.startswith("/v1/"):
            continue
        for operation in methods.values():
            if path in examples:
                operation["responses"]["200"]["content"]["application/json"]["examples"] = {
                    "observed": examples[path],
                    "unavailable": unavailable,
                }
            for parameter in operation.get("parameters", []):
                description = PARAMETER_DESCRIPTIONS.get(parameter["name"])
                if description and not parameter.get("description"):
                    parameter["description"] = description
                if parameter["name"] == "country" and not parameter.get("example"):
                    parameter["example"] = "US" if path == "/v1/trends" else "JP"
    for model in schema.get("components", {}).get("schemas", {}).values():
        for name, field in model.get("properties", {}).items():
            if name in FIELD_DESCRIPTIONS and not field.get("description"):
                field["description"] = FIELD_DESCRIPTIONS[name]
    return schema
