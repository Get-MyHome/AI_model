# backend 전달 정리

## 결론

AI가 PDF 분석에 받는 값은 `complex_id`, `pdf_url` 두 개가 필수입니다. 선택 주택형의 비용·정액 납부를 정확히 대조하려면 `unit_type_id`, `unit_type_name`, `sale_price_manwon`을 선택값으로 함께 받습니다.

대출 상품, 사용자 소득, 보유 현금은 AI 입력이 아닙니다. AI는 공고문 사실을 추출하고, backend가 그 결과를 사용자 조건·규칙표와 결합해 대출 경로와 완주 가능성을 계산합니다.

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

- `AI_SERVER_URL`에는 경로까지 포함합니다: `https://<ai-host>/api/analyze`
- 외부 환경에서는 `Authorization: Bearer <AI_API_KEY>` 헤더가 필수입니다.
- 동기 연결 시험의 read timeout은 310초 이상으로 둡니다. 실제 서비스 판정 요청에서 매번 모델을 호출하지 않고, 사전 분석·검수본을 읽는 구조가 원칙입니다.
- 동시 분석은 1건입니다. 처리 중이면 `503 ANALYSIS_SERVER_BUSY`를 반환하므로 짧은 지수 백오프로 재시도합니다.
- crawler의 10분짜리 URL은 호출 직전에 새로 만들고 캐시하지 않습니다. `401/403` 또는 retryable 다운로드 오류이면 새 URL을 발급합니다.
- 주택형을 지정하면 `unit_type_id`, `unit_type_name`, `sale_price_manwon`을 모두 보내야 합니다. 하나라도 빠지면 HTTP 422입니다.
- live backend 매핑은 `unit_types[].unit_type_id → unit_type_id`, `type → unit_type_name`, `sale_price → sale_price_manwon`입니다. AI는 `059.9883A`를 PDF 약식명 `59A`로 정규화합니다.
- 현재 `sale_price`는 동·층별 확정가격이 아니라 주택형 최고가입니다. 사용자 판정에는 **주택형 최고가 기준의 보수적 결과**라고 표시합니다.

## 분석·검수·적재 단위

MVP는 공고 한 건을 한 번만 분석하는 것이 아니라 backend의 각 `unit_types[]`를
`(complex_id, unit_type_id, sale_price_manwon)` 키로 사전 분석합니다. `/api/analyze`의
응답은 항상 `AUTO_EXTRACTED`이며 연결 시험과 검수 재료입니다. 사람이 공고 원문을
대조해 `REVIEWED`로 승인한 JSON만 backend 저장소에 같은 키로 적재하고 사용자 판정에
사용합니다. 사용자가 공고를 누를 때 Qwen을 새로 호출하면 안 됩니다.

## AI 증거 응답 — 최종 금융판정 아님

`POST /api/analyze`는 다음 내용을 포함한 v0.3 정본 JSON을 반환합니다.

- 계약금·중도금·잔금: 총비율 또는 정액, 회차별 비율·금액·납부일
- 중도금 대출: 알선 상태, 분양가 대비 대출비율, 자납비율, 은행 공개 여부, 이자 방식
- 추가비용: 유형, 주택형, 총액, 필수 여부, 분양가 포함 여부, 회차별 납부 구간
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

`analysis_summary`는 LLM 자유 조언이나 사용자 최종 진단이 아닙니다. 예시는 다음과 같습니다.

> 계약금은 분양가의 10%입니다. 중도금은 분양가의 60%입니다. 잔금은 분양가의 30%입니다. 공고문상 분양가의 40% 범위에서 중도금 대출을 알선할 예정입니다. 실제 실행과 개인 승인은 확정되지 않았습니다. 중도금 중 분양가의 20%는 직접 납부해야 합니다. 취급은행은 공고문에 공개되지 않았습니다.

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

대표 데모는 공공데이터만으로 계산할 때와 AI 검수본의 `중도금 60%·알선 예정 40%·자납 20%`를 반영했을 때 부족액과 `funding_status`가 달라지는 장면입니다. 수치는 반드시 실제 공고문과 backend 계산 결과를 사용합니다.

## 미래 규정·상환 시나리오

미래 규정 변화와 대출 상환 시나리오는 PDF 사실만으로 만들 수 없습니다. 사용자 조건, 정책 규칙표, 금리 가정이 필요한 계산이므로 backend 고정 공식이 담당합니다. AI는 계산에 필요한 공고문 값과 근거만 제공합니다.

## HOLD와 오류

- 문서상 불확실성: HTTP 200 응답 안의 `analysis_status=PARTIAL|HOLD`와 `blocking=true` HOLD
- 개인심사 안내: `analysis_status=READY`에서도 `kind=PERSONAL_REVIEW`, `blocking=false` HOLD가 존재할 수 있음
- 요청 형식 오류: HTTP 422
- 만료·접근 거부·다운로드 실패: HTTP 502, `retryable=true`
- 모델 미설정: HTTP 503
- 모델 호출·구조화 실패: HTTP 502, `retryable=true`

HOLD 문구는 `docs/HOLD_CODES.md` 및 `holds.py`에 고정되어 있습니다. 같은 입력이면 같은 문구가 반환됩니다.

## 현재 Java backend가 먼저 고칠 부분

현재 Java DTO는 v0.3 정본을 손실 없이 받을 수 없습니다. 특히 60% 중도금에서 분양가 대비 대출 40%를 정확히 표현하지 못하고, 정액 중도금도 받지 못합니다. AI가 값을 왜곡해 맞추지 않고 `/api/analyze/legacy`에서 위험한 변환을 거부합니다.

backend는 다음을 지원해야 합니다.

- snake_case 정본 역직렬화 또는 명시적 매핑
- 대출·자납 비율을 총 분양가 기준으로 수신
- 비율과 정액 납부 모두 수신
- 개별 필드 `null`을 0으로 계산하지 않음
- HOLD·근거·검수 상태 수신
- 선택비용과 분양가 포함비용을 기본 잔금에 자동 합산하지 않음
- `analysis_status`와 최종 `funding_status`를 별개 필드로 관리
- `validation.passed=false` 또는 `analysis_status=HOLD`이면 계산 차단
- 사용자 서비스에는 `review_status=REVIEWED` 데이터만 사용
- 중도금 회차·추가비용 회차를 날짜순 납부 이벤트로 만들고 첫 부족 이벤트를 계산
- 자납 회차가 미확정이면 특정 날짜를 만들지 않고 `INTERIM/CONDITIONAL`로 표시

현재 backend의 legacy DTO와 총액 중심 계산은 `중도금 60% × 대출 40% = 24%`로 오해할 수 있습니다. 정본의 `arranged_ratio=0.40`은 이미 **총 분양가 대비 40%**이므로 다시 중도금 비율을 곱하면 안 됩니다.

세부 차이는 `docs/BACKEND_COMPATIBILITY.md`에 정리돼 있습니다.
