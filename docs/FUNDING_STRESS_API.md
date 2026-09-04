# 중도금 임계비율·자금 스트레스 API

## 경계

`POST /api/funding-stress`는 기존 backend 판정을 바꾸지 않는 **advisory 계산**입니다.
기존 `POST /api/analyze`의 요청·응답 규격도 변경하지 않습니다.

- AI는 PDF에서 공고문 사실을 추출합니다.
- backend가 이미 판정한 대출 경로별 한도를 스냅샷으로 받습니다.
- AI 서버의 Python 고정식이 중도금 통과 최소비율과 비율별 자금 스트레스를
  재현합니다. LLM은 계산에 관여하지 않습니다.
- 소득·생년월일·기존부채·DSR/DTI 규칙은 요청하지 않습니다. 대출 자격·한도
  판정의 정본은 계속 backend입니다.

즉, 백엔드 저장소나 기존 계산 코드를 수정하지 않아도 AI 서버 단독으로 계산과
검증을 실행할 수 있습니다. 다만 현재 웹서비스 판정 흐름이 이 advisory 결과를 표시하려면
향후 consumer 연결은 필요합니다.

## 필수 전제

1. 정확히 같은 PDF SHA-256·공고번호·주택형·분양가·추출기 버전의
   `REVIEWED` 검수본
2. `validation.passed=true`
3. 검수된 분양가와 계약금·중도금 금액 또는 비율

자동 추출본(`AUTO_EXTRACTED`)은 HTTP 409 `FUNDING_STRESS_UNAVAILABLE`로 거부합니다.
저장소에는 운영 검수본을 커밋하지 않습니다. 서버 로컬의 검수 저장소에 정확한 원본 PDF,
공고번호, 주택형, 분양가와 extractor 버전이 모두 일치하는 `REVIEWED` 결과가 있을 때만
계산합니다.

## 요청

```json
{
  "analysis_request": {
    "complex_id": "2026000372",
    "pdf_url": "https://<exact-s3-host>/<fresh-presigned-url>",
    "unit_type_id": "01",
    "unit_type_name": "059.9883A",
    "sale_price_manwon": 108650
  },
  "cash_manwon": 10865,
  "cash_snapshot_timing": "PRE_CONTRACT",
  "monthly_saving_manwon": 100,
  "as_of_date": "2026-09-02",
  "loan_routes": [
    {
      "route_id": "bank-mortgage",
      "product_code": "BANK_MORTGAGE",
      "product_name": "은행 주택담보대출",
      "status": "OK",
      "limit_min_manwon": 25000,
      "limit_max_manwon": 30000,
      "rule_version": "2026-08-31",
      "assumption_set_id": "mvp-v1"
    }
  ],
  "interim_ratio_grid_bps": [0, 4000, 5200, 6000]
}
```

- 금액 단위는 만 원 정수입니다.
- `cash_manwon`은 이미 납부한 금액을 제외한 **계약금 납부 전** 보유
  현금 스냅샷입니다. `cash_snapshot_timing`은 `PRE_CONTRACT`만 허용하며,
  공고문의 계약금 납부일보다 `as_of_date`가 늦으면 거부합니다.
- `100bps=1%p`, `4000bps=40%`입니다.
- 경로는 대안입니다. 두 상품 한도를 합산하지 않습니다.
- `limit_min`/`limit_max`는 각각 보수적·최대 시나리오로 분리합니다.
- `HOLD`/`BLOCK` 경로의 미확정 한도를 0으로 바꾸지 않습니다.
- `monthly_saving_manwon`은 부족액 회복 개월에만 쓰며 현금에 누적하지 않습니다.

## 핵심 응답

```json
{
  "advisory": true,
  "calculator_version": "0.1.2",
  "maximum_interim_ratio_bps": 6000,
  "interim_continuity_threshold": {
    "status": "CALCULATED",
    "minimum_ratio_bps": 6000,
    "minimum_loan_amount_manwon": 65190,
    "resolution_bps": 1,
    "limiting_shortfall": null
  },
  "document_cap_comparison": {
    "arrangement_status": "PLANNED",
    "document_cap_ratio_bps": 4000,
    "personal_approval_confirmed": false,
    "interim_continuity": {
      "status": "NEGATIVE",
      "required_ratio_bps": 6000,
      "document_cap_ratio_bps": 4000,
      "margin_bps": -2000,
      "certainty": "CONDITIONAL"
    }
  }
}
```

실제 응답에는 위 필드와 함께 다음이 들어갑니다.

- 대출 경로×한도 시나리오별 `full_completion_threshold`
- 각 경로의 `route_id`, `rule_version`, `assumption_set_id` 출처 스냅샷
- 비율별 `first_shortfall`, 구간별 signed `cash_margin_manwon`
- `worst_margin_manwon`, `balance_margin_manwon`
- HOLD, 계산 가정, 입력 digest, 검수본 fingerprint

## 표현 제한

- `document_cap_ratio_bps`는 **공고문상 사업장 알선 상한**이지 개인 승인비율이
  아닙니다.
- 회차별 대출 충당 순서가 없는 부분 대출은 `stage=INTERIM`, 회차·날짜 `null`,
  `certainty=CONDITIONAL`로 내려줍니다.
- 중도금 대출은 필요 자금을 없애지 않고 중도금 부담을 잔금 상환·대환 시점으로
  이동시킬 수 있습니다.
- 상환·대환 조건이 미기재이면 원금을 잔금 수요에 보수적으로 포함하고 HOLD를
  남깁니다.
- 이자금액, 선택비용, 필수 여부가 미확정인 추가비용은 추정하지 않습니다.
- 비율을 분양가에 곱한 값이 정수 만 원으로 정확히 표현되지 않으면 반올림하지 않고
  `PAYMENT_VALUE_UNKNOWN` 차단 HOLD로 계산을 보류합니다.
- 가용 대출금과 공고문 상한의 만 원·bp 변환은 보수적으로 내림 처리하며,
  반올림으로 조달 가능액이나 상한을 키우지 않습니다. 최대 1만 원 또는 1bp 미만을
  과소 표시할 수 있지만 완주 가능성을 낙관적으로 판정하지 않습니다.
- 필수 추가비용의 분양가 포함 여부, 적용 주택형, 총액·납부 구간 대조 중
  하나라도 미확정이면 결과를 `UNKNOWN`으로 내리고 해당 비용명을 HOLD에
  모두 보존합니다.
- 선택 주택형과 명시적으로 다른 주택형의 추가비용은 합산하지 않습니다.

## 2026000372 회귀 검증값

검수된 실물 값과 가정 `cash=10,865`, 잔금 경로 한도 `30,000` 만 원을 쓴 고정 회귀:

- 공고문 40% 시나리오: 최초 `INTERIM` 21,730만 원 부족,
  이후 잔금 구간 마진 -46,055만 원
- 가상 60% 시나리오: 중도금 구간은 통과하지만 중도금 원금 65,190만 원을
  상환·대환해야 하므로 잔금 구간 67,785만 원 부족
- 따라서 이 입력은 “대출비율을 60%로 늘리면 완주”가 아닙니다.

`tests/test_funding_stress.py`가 위 값과 정액 중도금·만원 미만 금액 기권·경로 min/max
비합산·미검수 거부를 회귀 검증합니다.
