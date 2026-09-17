# 알려진 제한과 보류 항목

2026-09-16 공개 API 8개 엔드포인트 점검 결과 중, 코드만으로 고치지 않고 기록으로 남긴 항목이다. 각 항목은 팀 결정, 외부 승인, 또는 API 계약 변경이 필요하다.

## 팀 결정이 필요한 항목

- ~~**관광지 원천 5개 재활성화**~~: 2026-09-17 결정으로 다시 켰고, KTO_TATS↔TourAPI 매핑(`app/normalization/place_crosswalk.py`)을 구현했다. 이름이 같은 시도 안에서 유일하지 않거나 좌표가 1km 넘게 어긋나는 행은 매핑하지 않으므로 일부 관광지는 `hub`, `related_places`가 계속 비어 있을 수 있다. 매핑률은 배포 후 운영 DB에서 확인한다.
- ~~**관광지 소개문(`overview`)**~~: 2026-09-17 결정으로 구현했다. essential 관광지의 소개문을 detailCommon2로 한 실행에 60곳씩 수집한다(무료 쿼터 1,000/일 안).
- **공지 번역·요약**: `ALERT_ENRICHMENT_BATCH_SIZE=0`으로 유료 LLM 보강이 꺼져 있다. 켜면 시간당 2건, 하루 최대 48건 처리한다.
- ~~**시군구 방문 전망**~~: 2026-09-17 결정. 지역 요청은 지역 내 관광지들의 공식 집중률 평균을 `official`로 내고 `sample_count`와 `basis`로 근거를 밝힌다.
- ~~**트렌드의 KTO 관광자원 수요**~~: 2026-09-17 결정. 관측이 있으면 응답에 포함되어 한국어 관광지명 키워드와 `area_code` 필터가 동작한다. YouTube 키워드 범위(15개 고정)는 그대로다.
- **대시보드 파라미터**: `compare=previous_period`(기간 대비 증감률)와 `constraints.avoid_crowds`(계절 반영)를 프런트가 보내지 않는다. 백엔드는 준비돼 있다.

## 외부 승인이나 원천 부재로 채울 수 없는 필드

| 엔드포인트 | 필드 | 상태 |
| --- | --- | --- |
| trends | `search_ratio` | NAVER 데이터랩 전용, 소스 비활성 + 저장 정책 미승인 |
| trends, inbound | instagram / facebook / reddit 블록, `sns_mentions` | 어댑터 없음, 외부 승인 필요 |
| trends | `destination_searches` | 어떤 원천도 연결되지 않음 |
| regions/insights | `demand.avg_stay_nights`, `diversity.age_index` | KTO 원천에 대응 지표 없음, 정규화가 None 고정 (insights는 이 때문에 항상 partial) |
| visitors/timeseries | `concentration_rate`, `attraction_name` 경로 | 작성 경로 없음, `SRC_TOURISM_ADMISSION`은 HTTP 전용이라 어댑터가 unavailable |
| forecasts/visitors | `expected_visitors`, `confidence`, `adjustment_factors` | 원천 없음, `formulas.adjusted_forecast`는 호출되지 않음 |
| markets/inbound | `passengers`, `social_interest.youtube.score` | 공항공사 월별 자료에 여객 수 없음(DB 245행 모두 null), YouTube는 설계상 국가 신호에서 제외 |
| markets/{country}/alerts | `source_scope=local`, `status=inactive`, `source_type=foreign_affairs` | 현지 기관 수집기 없음, 비활성 문서는 삭제되므로 도달 불가 |
| recommendations | `estimated_budget_krw`, `budget_krw`, `days`, `party_size`, 접근성·이동시간 조건 | 검증 원천 없음 |

**결정(2026-09-16):** 위 필드는 "미제공"으로 문서화하고(README "Fields That Are Not Provided", OpenAPI 필드 설명) 가용성 판정에서 제외한다. 필드와 응답 구조는 그대로 두어 대시보드와 클라이언트는 영향을 받지 않는다. 그 결과 insights·inbound·recommendations는 원천이 있는 필드가 모두 채워지면 `available`로 응답한다. 원천이 생기면 해당 필드를 채우고 이 목록에서 빼면 된다.

## 데이터 품질로 격리된 항목

- 축제(`SRC_FESTIVAL`) 격리 50행: 종료일이 시작일보다 앞서거나 주소가 여러 지역에 걸치는 원천 데이터다. 코드가 추측하지 않고 제외한 것이며 버그가 아니다.
- 10개 비활성 SNS 원천이 주기마다 0건 실행 기록(`ingestion_run`)을 남긴다. 무해하지만 노이즈다.

## 운영 참고

- 6055a3e에서 soak 필수 서비스와 `eden-scheduler.service` 의존성에서 `mariadb.service`를 뺐다. 운영 VPS의 MariaDB는 같은 호스트에 있으므로 부팅 순서는 `Restart=always`가 흡수하지만, soak 증거는 더 이상 MariaDB 재시작을 잡지 않는다.
