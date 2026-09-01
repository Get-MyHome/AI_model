# backend 전달 정리

## 결론

AI가 PDF 분석에 받는 값은 `complex_id`, `pdf_url` 두 개가 필수입니다. 선택 주택형의 비용·정액 납부를 정확히 대조하려면 `unit_type_id`, `unit_type_name`, `sale_price_manwon`을 선택값으로 함께 받습니다.

대출 상품, 사용자 소득, 보유 현금은 AI 입력이 아닙니다. AI는 공고문 사실을 추출하고, backend가 그 결과를 사용자 조건·규칙표와 결합해 대출 경로와 완주 가능성을 계산합니다.

## 요청

```json
{
  "complex_id": "2026000372",
  "pdf_url": "https://s3.example/presigned-url",
  "unit_type_id": "59A-3F",
  "unit_type_name": "59A",
  "sale_price_manwon": 108650
}
```

- 금액은 만 원 정수입니다.
- `pdf_url`은 crawler가 S3에 올린 PDF의 짧은 유효기간 URL입니다.
- AI는 요청을 받자마자 URL에서 PDF를 수령합니다. 청약홈 크롤링은 하지 않습니다.

## 핵심 응답

`POST /api/analyze`는 다음 내용을 포함한 v0.3 정본 JSON을 반환합니다.

- 계약금·중도금·잔금: 총비율 또는 정액, 회차별 비율·금액·납부일
- 중도금 대출: 알선 상태, 분양가 대비 대출비율, 자납비율, 은행 공개 여부, 이자 방식
- 추가비용: 유형, 주택형, 총액, 필수 여부, 분양가 포함 여부, 회차별 납부 구간
- `analysis_summary`: 검증값만 조합한 고정 요약문
- `holds`: 고정 사유 코드·화면 문구·다음 행동
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

`analysis_summary`는 LLM 자유 조언이 아닙니다. 예시는 다음과 같습니다.

> 계약금은 분양가의 10%입니다. 중도금은 분양가의 60%입니다. 잔금은 분양가의 30%입니다. 공고문상 중도금 대출 가능 범위는 분양가의 40%입니다. 중도금 중 분양가의 20%는 직접 납부해야 합니다. 취급은행은 공고문에 공개되지 않았습니다.

## 미래 규정·상환 시나리오

미래 규정 변화와 대출 상환 시나리오는 PDF 사실만으로 만들 수 없습니다. 사용자 조건, 정책 규칙표, 금리 가정이 필요한 계산이므로 backend 고정 공식이 담당합니다. AI는 계산에 필요한 공고문 값과 근거만 제공합니다.

## HOLD와 오류

- 문서상 불확실성: HTTP 200 응답 안의 `analysis_status=PARTIAL|HOLD`, `holds[]`
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

세부 차이는 `docs/BACKEND_COMPATIBILITY.md`에 정리돼 있습니다.
