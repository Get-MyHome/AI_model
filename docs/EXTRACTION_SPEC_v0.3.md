# PDF 추출 정본 명세 v0.3

## 변경 이유

v0.2는 모든 납부를 비율로 표현하고 추가비용에 납부 구간 하나만 허용했습니다. 실제 달서자이 제니크 공고에는 `중도금 1회 1,000만 원` 정액 구조가 있고, 충정로역자이르네 발코니 확장비는 계약·중도·잔금 세 번으로 나뉩니다. v0.3은 실제 문서를 손실 없이 표현하도록 고쳤습니다.

## 입력

운영 API 입력:

| 필드 | 필수 | 뜻 |
| --- | --- | --- |
| `complex_id` | 예 | 청약 공고 식별자 |
| `pdf_url` | 예 | crawler가 생성한 짧은 유효기간의 S3 URL |
| `unit_type_id` | 조건부 | backend의 주택형 식별자 |
| `unit_type_name` | 조건부 | backend 주택형 이름. `059.9883A`는 내부에서 `59A`로 정규화 |
| `sale_price_manwon` | 조건부 | backend 주택형 최고 분양가, 만 원 단위 |

주택형을 지정하지 않는 문서 공통 분석에서는 세 필드를 모두 생략합니다. 주택형별
분석에서는 세 필드를 모두 보내야 하며 하나만 생략하면 HTTP 422입니다. MVP의
`sale_price_manwon`은 동·층별 확정가격이 아니라 청약홈 상세 API의 주택형 최고가입니다.
따라서 backend 최종 화면에는 **주택형 최고가 기준의 보수적 판정**이라고 표시합니다.

개발·평가는 `pdf_url` 대신 로컬 파일 경로를 받지만 분석 이후 과정은 같습니다.

대출 상품과 사용자 소득·현금은 입력하지 않습니다. PDF에 적힌 사실 추출과 사용자별 대출 판정은 서로 다른 단계입니다.

## 정본 출력 규칙

- JSON 이름은 `snake_case`입니다.
- 금액은 이름에 단위를 붙인 만 원 정수입니다.
- 비율은 모두 총 분양가 대비 0~1입니다. `0.40`은 분양가의 40%입니다.
- 미확인 값은 `null`입니다. 0은 공고가 실제 0 또는 대출 불가를 명시한 경우에만 씁니다.
- `RATIO`, `FIXED_AMOUNT`, `MIXED`, `UNKNOWN`으로 납부 기준을 구분합니다.

계약금·중도금·잔금은 동일한 구조를 사용합니다.

```json
{
  "total_ratio": 0.60,
  "total_amount_manwon": null,
  "basis": "RATIO",
  "installments": [
    {
      "number": 1,
      "ratio": 0.10,
      "amount_manwon": null,
      "due_date": "2027-04-13",
      "due_text": null
    }
  ],
  "due_date": null,
  "due_month": null,
  "due_text": null
}
```

정액 중도금은 다음처럼 표현합니다.

```json
{
  "total_ratio": null,
  "total_amount_manwon": 1000,
  "basis": "FIXED_AMOUNT",
  "installments": [
    {
      "number": 1,
      "ratio": null,
      "amount_manwon": 1000,
      "due_date": "2026-11-17",
      "due_text": null
    }
  ],
  "due_date": null,
  "due_month": null,
  "due_text": null
}
```

중도금 대출은 중도금 총액과 분리합니다.

```json
{
  "arrangement_status": "PLANNED",
  "arranged_ratio": 0.40,
  "arranged_amount_manwon": null,
  "self_funding_ratio": 0.20,
  "self_funding_amount_manwon": null,
  "self_funding_origin": "EXTRACTED",
  "bank_names": [],
  "guarantee_provider": null,
  "interest_type": "DEFERRED_INTEREST",
  "interest_note": "입주 시 대납이자 정산",
  "prepay_requirement_ratio": 0.10,
  "settlement_requirement": "REPAY_OR_CONVERT_TO_MORTGAGE",
  "settlement_deadline_text": "입주 시 중도금 대출 원금을 상환하거나 담보대출로 전환",
  "extension_contingency_disclosed": false
}
```

`arranged_ratio`/`arranged_amount_manwon`은 **공고문상 사업장 대출 알선 범위·상한**이며 개인 심사 승인비율이 아닙니다. `self_funding_origin=EXTRACTED`는 공고문이 직접 납부·자납을 명시한 경우이고, `DERIVED`는 중도금에서 알선 상한을 뺈 결과인 **사업장 알선 외 별도 조달 구간**입니다. 후자를 “반드시 현금으로 납부”라고 확정하지 않습니다.

`settlement_requirement`는 입주·잔금 시점의 기존 중도금 대출 처리 의무입니다.

| 값 | 뜻 |
| --- | --- |
| `REPAY_OR_CONVERT_TO_MORTGAGE` | 원금을 상환하거나 담보대출로 전환해야 함 |
| `REPAY_REQUIRED` | 원금 상환이 명시됨 |
| `CONVERT_TO_MORTGAGE_REQUIRED` | 담보대출 전환이 명시됨 |
| `CONTINUE_EXPLICITLY_ALLOWED` | 기존 중도금 대출의 계속 이용이 명시적으로 허용됨 |
| `NOT_STATED` | 공고문에 처리 조건이 없음 |
| `NOT_APPLICABLE` | 사업장 중도금 대출 자체가 불가함 |

단순히 “대출 연장 시 추가 이자는 계약자 부담”이라는 문장이 있다고 해서 기존 대출이
입주 후에도 계속된다고 해석하지 않습니다. 그런 문장은
`extension_contingency_disclosed=true`로만 보존하고, 상환·전환 조건이 없으면
`settlement_requirement=NOT_STATED`와 HOLD를 유지합니다.

사용자 자금에 영향을 주는 위험조항은 자유 요약이 아니라 고정 코드로 반환합니다.

```json
{
  "risk_clauses": [
    {
      "code": "LOAN_MEDIATION_NOT_GUARANTEED",
      "impact_stage": "INTERIM",
      "origin": "EXTRACTED",
      "message": "중도금 대출 알선은 예정사항이며 실행을 보장하지 않습니다.",
      "next_action": "사업주체 또는 취급 금융기관에 실제 실행 가능 여부를 확인하세요.",
      "evidence": [
        {
          "field": "/risk_clauses/0",
          "page": 7,
          "raw_text": "대출 알선이 불가할 수 있으며 이는 사업주체의 의무사항이 아님"
        }
      ]
    }
  ]
}
```

지원 코드는 `LOAN_MEDIATION_NOT_GUARANTEED`, `INDIVIDUAL_REVIEW_REQUIRED`,
`SELF_FUNDING_REQUIRED`, `INTEREST_PAYMENT_RISK`, `LOAN_NOT_AVAILABLE`,
`TERMS_DIFFER_BY_HOUSING_TYPE`입니다. `SELF_FUNDING_REQUIRED`가 계산으로 파생된 경우에는
중도금 총비율과 알선 상한 두 근거를 모두 연결합니다. 보유 27건에는
`TERMS_DIFFER_BY_HOUSING_TYPE` 양성 사례가 없으므로 이 코드는 규칙과 음성 테스트만 있고
실제 양성 성능은 주장하지 않습니다. `message`와 `next_action`은 LLM 자유 생성물이 아니라
코드별 고정 템플릿입니다.

추가비용은 주택형과 여러 납부 회차를 보존합니다. `included_in_sale_price=true`인 비용은 별도 필요자금에 더하지 않습니다. `required=false`인 선택비용도 사용자가 선택하기 전에는 합산하지 않습니다.

`additional_costs` 목록은 사용자가 선택할 수 있는 모든 가전·가구·시스템에어컨
조합의 전수 카탈로그를 보장하지 않습니다. 공고문에 발코니 외 선택 유상옵션이
있으면 `exception_flags` 및 비차단 HOLD에
`ADDITIONAL_COST_SCOPE_LIMITED`를 내려 선택 전 기본 자금계산과 옵션 포함
계산을 구분합니다. 실제 선택 품목은 금액·납부일정을 추가해 다시
계산해야 합니다.

## 상태

| 상태 | 뜻 |
| --- | --- |
| `READY` | 핵심 값과 근거가 고정 검증을 통과함 |
| `PARTIAL` | 핵심 계산은 가능하지만 은행명·이자 등 확인할 항목이 남음 |
| `HOLD` | 핵심 납부값 또는 근거가 없어 계산에 넣으면 안 됨 |

`READY`는 사람 검수를 뜻하지 않습니다. 검수 상태는 `AUTO_EXTRACTED`, `NEEDS_REVIEW`, `REVIEWED`로 별도 관리합니다.

## 고정 검증

- 비율형이면 계약금+중도금+잔금 = 1.0
- 정액형이고 선택 세대 분양가가 있으면 계약금+중도금+잔금 = 분양가
- 회차 합 = 해당 구간 총비율 또는 총액
- 대출비율+자납비율 = 중도금 총비율
- 납부일은 오름차순이고 잔금일은 마지막 중도금일보다 빠르지 않음
- EXTRACTED 값은 실제 페이지 원문 근거가 있음
- DERIVED 값은 계산식과 입력 근거가 있음
- 상환·전환 조건과 위험조항은 실제 페이지 원문 근거가 있음
- 동일 위험코드가 중복되지 않고, 파생 위험조항은 모든 입력 근거를 연결함
- 선택비용과 분양가 포함 비용은 기본 합산에서 제외
