# backend 전달 정리

## 결론

문서 공통 분석만 수행할 때는 `complex_id`, `pdf_url` 두 개로 충분합니다. 선택 주택형의 실제 자금판정에 사용할 분석 요청은 `unit_type_id`, `unit_type_name`, `sale_price_manwon`까지 **총 5개를 모두** 보내야 합니다. 세 target 필드 중 일부만 보내면 HTTP 422를 반환합니다.

`POST /api/analyze`에는 대출 상품, 사용자 소득, 보유 현금이 필요하지 않습니다.
AI는 공고문 사실을 추출하고, backend가 사용자 조건·규칙표로 기존 대출 경로와 한도를
계산하는 경계는 유지합니다. 별도 `POST /api/funding-stress`는 소득·부채를 받지 않고,
backend가 이미 산출한 경로별 한도와 현금 스냅샷만 받아 advisory 임계비율을 계산합니다.

## 요청

```json
{
  "complex_id": "2026000372",
  "pdf_url": "https://s3.example/presigned-url",
  "unit_type_id": "01",
  "unit_type_name": "059.9883A",
  "sale_price_manwon": 108650
}
```

- 금액은 만 원 정수입니다.
- `pdf_url`은 crawler가 S3에 올린 PDF의 짧은 유효기간 URL입니다.
- AI는 요청을 받자마자 URL에서 PDF를 수령합니다. 청약홈 크롤링은 하지 않습니다.

## HTTP 연동 조건

- 현재 외부 시험 주소는 `https://server.tailb23d4f.ts.net:10000/api/analyze`입니다.
- `AI_SERVER_URL`에는 위처럼 `/api/analyze` 경로까지 포함합니다.
- 외부 환경에서는 `Authorization: Bearer <AI_API_KEY>` 헤더가 필수입니다.
- 동기 연결 시험의 read timeout은 310초 이상으로 둡니다. 실제 서비스 판정 요청에서 매번 모델을 호출하지 않고, 사전 분석·검수본을 읽는 구조가 원칙입니다.
- 신규 Qwen 분석은 동시 1건입니다. 처리 중인 분석이 있더라도 exact `REVIEWED` 검수본 조회는 세마포어 밖에서 먼저 처리됩니다. 검수본이 없는 신규 분석끼리 경합하면 `503 ANALYSIS_SERVER_BUSY`를 반환하므로 짧은 지수 백오프로 재시도합니다.
- crawler의 10분짜리 URL은 호출 직전에 새로 만들고 캐시하지 않습니다. `401/403` 또는 retryable 다운로드 오류이면 새 URL을 발급합니다.
- crawler S3 signer가 반환한 PDF 호스트는 현재 `getmyhome-pdfs-758862546581.s3.ap-northeast-2.amazonaws.com`과 `getmyhome-pdfs-758862546581.s3.amazonaws.com` 두 형식이 확인됐습니다. AI 운영 환경은 두 정확한 호스트만 `PDF_ALLOWED_HOSTS`에 등록하며, 광범위한 S3 와일드카드는 허용하지 않습니다.
- 주택형을 지정하면 `unit_type_id`, `unit_type_name`, `sale_price_manwon`을 모두 보내야 합니다. 하나라도 빠지면 HTTP 422입니다.
- live backend 매핑은 `unit_types[].unit_type_id → unit_type_id`, `type → unit_type_name`, `sale_price → sale_price_manwon`입니다. AI는 `059.9883A`를 PDF 약식명 `59A`로 정규화합니다.
- 현재 `sale_price`는 동·층별 확정가격이 아니라 주택형 최고가입니다. 사용자 판정에는 **주택형 최고가 기준의 보수적 결과**라고 표시합니다.

## 분석·검수·적재 단위

MVP는 공고 한 건을 한 번만 분석하는 것이 아니라 backend의 각 `unit_types[]`를
`(source_sha256, complex_id, unit_type_id, sale_price_manwon)` 불변 키로 사전 분석합니다.
`/api/analyze`는 fresh URL에서 PDF를 먼저 수령해 SHA-256을 계산하고, 정확히 같은 키의
`REVIEWED` 검수본이 있으면 Qwen 재호출 없이 반환합니다. 검수본이 없으면
`AUTO_EXTRACTED`를 반환하고 backend는 사용자 자금판정을 HOLD합니다. `REVIEWED`에는
검수자·검수시각·검증 통과와 정확한 대상 키가 반드시 있어야 합니다. `unit_type_name`은
`059.9883A → 59A` 정규화 때문에 조회 키에서는 제외하지만 응답에는 정규화해 보존합니다.

## AI 증거 응답 — 최종 금융판정 아님

`POST /api/analyze`는 다음 내용을 포함한 v0.3 정본 JSON을 반환합니다.

### 전체 성공 응답 예시

백엔드 DTO 작성과 연동 테스트에는
[`docs/examples/analyze-response-v0.3.json`](examples/analyze-response-v0.3.json)을
사용합니다. 현재 Qwen3.5 분석기가 실제 `2026000372` 주택형을 분석해 반환한
`AnalysisResponse v0.3` 전체 JSON이며, 아래 항목의 중첩 필드와 `null` 필드까지 모두
포함합니다.

- `analysis_status`, `review_status`, `target_unit`
- `payment_schedule`, `interim_loan`, `additional_costs`
- `risk_clauses`, `analysis_summary`, `holds`, `exception_flags`
- `evidence`, `validation`, `meta`

이 파일은 응답 형태를 보여주기 위한 `AUTO_EXTRACTED` 예시입니다. 백엔드가 사용자
최종 자금판정에 사용할 수 있다는 뜻은 아닙니다. 실제 판정에는 정확한 대상 키가
일치하고 `review_status=REVIEWED`, `validation.passed=true`인 응답만 사용합니다.

- 계약금·중도금·잔금: 총비율 또는 정액, 회차별 비율·금액·납부일
- 중도금 대출: 알선 상태, 분양가 대비 대출비율, 자납비율, 은행 공개 여부, 이자 방식
- 추가비용: 유형, 주택형, 총액, 필수 여부, 분양가 포함 여부, 회차별 납부 구간
- 입주 시 중도금 대출 처리: `settlement_requirement`, 처리시점 원문,
  연장 가능성 고지 여부
- 위험조항: 고정 코드, 영향 구간, 고정 안내문·다음 행동, 원문 근거
- `analysis_summary`: 검증값만 조합한 공고문 사실 요약문(`document_fact_summary` 성격)
- `holds`: 고정 사유 코드·종류·차단 여부·화면 문구·다음 행동
- `evidence`: 각 추출값의 PDF 물리 페이지와 원문
- `validation`: 고정식 통과 여부와 이슈
- `review_status`: 자동 추출과 사람 검수 완료 구분

모든 비율은 총 분양가 대비 0~1입니다. 예를 들어 중도금 60% 중 대출 40%, 자납 20%이면 다음과 같습니다.

```json
{
  "payment_schedule": {
    "interim_payment": {"total_ratio": 0.60}
  },
  "interim_loan": {
    "arranged_ratio": 0.40,
    "self_funding_ratio": 0.20
  }
}
```

`arranged_ratio=0.40`은 개인이 40%를 승인받았다는 뜻이 아니라 **공고문상 사업장 알선 상한**입니다. `self_funding_origin=DERIVED`인 20%는 사업장 알선으로 충당되지 않는 별도 조달 구간이며, backend가 사용자의 현금만으로 조달한다고 단정하면 안 됩니다. 다만 MVP 보수 판정에서 확정된 별도 조달 경로가 없으면 해당 금액을 자금 필요액에 반영하고 `CONDITIONAL`/HOLD를 표시합니다.

`analysis_summary`는 LLM 자유 조언이나 사용자 최종 진단이 아닙니다. 예시는 다음과 같습니다.

> 계약금은 분양가의 10%입니다. 중도금은 분양가의 60%입니다. 잔금은 분양가의 30%입니다. 공고문상 분양가의 40% 범위에서 중도금 대출을 알선할 예정입니다. 실제 실행과 개인 승인은 확정되지 않았습니다. 중도금 중 분양가의 20%는 직접 납부해야 합니다. 취급은행은 공고문에 공개되지 않았습니다.

현재 JSON 계약은 additive v0.3을 유지하고 추출기 버전은 `0.2.0`입니다. 새 추출기는
`settlement_requirement`와 `risk_clauses[]`를 추가로 반환합니다. 검수본 조회는
PDF·공고·주택형·가격뿐 아니라 추출기 버전도 정확히 일치해야 하므로 0.1 검수본이 0.2
분석처럼 재사용되지 않습니다.

`holds[].kind=DOCUMENT_UNCERTAINTY`와 `blocking=true`는 공고문 사실이 부족해
계산을 보류해야 한다는 뜻입니다. `kind=PERSONAL_REVIEW`와 `blocking=false`는
문서 추출 실패가 아니라 금융기관의 개인 심사가 남았다는 안내입니다. backend는
두 종류를 같은 최종 HOLD로 처리하면 안 됩니다.

## backend 최종 자금판정의 대표 결과

아래 값은 AI가 생성하지 않고 backend가 사용자 현금·소득·부채·규칙표와 AI 검수본을 결합해 계산합니다.

- `funding_status`: `OK | GAP | BLOCK | HOLD`
- `first_discontinuity.stage`: `CONTRACT | INTERIM | BALANCE`
- `first_discontinuity.due_date` 또는 `due_month`
- `first_discontinuity.shortfall_manwon`
- `first_discontinuity.certainty`: `CONFIRMED | CONDITIONAL`
- `timeline[]`: 회차별 필요금액·조달금액·부족액
- `unresolved_conditions[]`: HOLD 사유와 확인 질문
- `evidence_refs[]`: 해당 판정을 바꾼 공고문 근거

자납 20%의 회차별 배분이 공고문에 없으면 특정 날짜를 임의 생성하지 않습니다. `first_discontinuity.stage=INTERIM`, 날짜는 `null`, `certainty=CONDITIONAL`로 두고 HOLD 질문을 표시해야 합니다.

`settlement_requirement`가 `REPAY_OR_CONVERT_TO_MORTGAGE`, `REPAY_REQUIRED` 또는
`CONVERT_TO_MORTGAGE_REQUIRED`이면 잔금 시점 계산에서 기존 중도금 대출 원금을 없애면 안
됩니다. 기존 원금은 상환 또는 잔금 담보대출로 대환해야 할 금액이므로 잔금 조달 수요에
포함합니다. `NOT_STATED`는 계속 이용 가능으로 해석하지 않고
`BALANCE_CONVERSION_UNCERTAIN` HOLD를 유지합니다.

`2026000372` 실측 데모에서 중도금 60%를 전액 대출로 가정하더라도 입주 때 기존 원금
65,190만 원을 상환·대환해야 하므로 `BALANCE` 부족액은 67,785만 원입니다. 검수된 공고문
조건인 사업장 알선 상한 40%와 알선 외 조달 구간 20%를 반영하면 최초 단절은
`INTERIM`, 날짜 `null`, 부족액 21,730만 원이며, 이후 잔금 시점에는 기존 대출원금
43,460만 원을 포함해 46,055만 원이 부족합니다. 서로 다른 시점의 금액을 단순 증감으로
비교하지 않습니다. 알선은 예정·비보장 상태이므로 최종 상태는 `HOLD`, 확정도는
`CONDITIONAL`입니다. 이 데모를 `GAP→BLOCK`으로 표현하지 않습니다.

## 미래 규정·상환 시나리오

미래 규정 변화와 대출 상환 시나리오는 PDF 사실만으로 만들 수 없습니다. 사용자 조건, 정책 규칙표, 금리 가정이 필요한 계산이므로 backend 고정 공식이 담당합니다. AI는 계산에 필요한 공고문 값과 근거만 제공합니다.

공고문과 추후 은행 안내문 비교의 고정 비교 코어는 준비돼 있지만, 은행 안내문용 별도
추출·검수 골든셋이 아직 없습니다. 따라서 공개 API로 열지 않으며 결과에는
`NOT_VALIDATED_ON_BANK_GUIDANCE`를 명시합니다. 실제 문서 쌍을 검수하기 전에는 “조건 변경
탐지 완료”라고 표현하지 않습니다.

## AI 서버 advisory 계산

기존 backend 자금판정을 수정하지 않고, AI 서버에 별도
`POST /api/funding-stress`를 추가했습니다. 이 API는 다음만 계산합니다.

- 중도금 구간을 통과하기 위한 최소 대출비율
- 공고문상 알선 상한과 임계비율의 조건부 마진
- 0%·공고문 비율·임계비율·중도금 총비율 스트레스
- backend 경로별 min/max 한도를 합산하지 않은 독립 시나리오
- 상환·대환 중도금 원금을 잔금 필요액에 포함한 부족 구간

부분 대출의 회차별 충당 순서가 없으면 정확한 회차·날짜를 생성하지 않습니다.
세부 규격과 2026000372 고정 회귀값은 `docs/FUNDING_STRESS_API.md`입니다.

## HOLD와 오류

- 문서상 불확실성: HTTP 200 응답 안의 `analysis_status=PARTIAL|HOLD`와 `blocking=true` HOLD
- 개인심사 안내: `analysis_status=READY`에서도 `kind=PERSONAL_REVIEW`, `blocking=false` HOLD가 존재할 수 있음
- 요청 형식 오류: HTTP 422
- 만료·접근 거부·다운로드 실패: HTTP 502, `retryable=true`
- 모델 미설정: HTTP 503
- 모델 호출·구조화 실패: HTTP 502, `retryable=true`

HOLD 문구는 `docs/HOLD_CODES.md` 및 `holds.py`에 고정되어 있습니다. 같은 입력이면 같은 문구가 반환됩니다.

## backend 유지 원칙

이 버전은 backend 저장소·DTO·판정 로직을 수정하지 않습니다. `POST /api/analyze`와
`POST /api/funding-stress`는 AI 저장소 내의 독립 계약입니다. 기존 backend는 계속 현재 판정의
정본이며, advisory를 서비스 화면에 쓸지는 향후 팀 선택입니다.
