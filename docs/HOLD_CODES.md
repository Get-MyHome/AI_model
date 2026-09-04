# HOLD 코드와 고정 안내문

HOLD 문구는 모델이 자유 생성하지 않습니다. 아래 코드에 고정된 문구와 다음 행동을 사용합니다.
기본값은 `kind=DOCUMENT_UNCERTAINTY`, `blocking=true`입니다.

| 코드 | 화면 문구 | 다음 행동 |
| --- | --- | --- |
| `DOWN_PAYMENT_MISSING` | 계약금 조건을 확인하지 못했어요. | 공고문 공급금액 표의 계약금 비율·정액을 확인하세요. |
| `INTERIM_PAYMENT_MISSING` | 중도금 조건을 확인하지 못했어요. | 시행사에 중도금 총액과 회차별 납부일을 확인하세요. |
| `BALANCE_PAYMENT_MISSING` | 잔금 조건을 확인하지 못했어요. | 잔금 금액과 입주지정일을 확인하세요. |
| `INTERIM_SCHEDULE_MISSING` | 중도금 납부 일정 일부가 확인되지 않았어요. | 회차별 비율 또는 금액과 납부일을 확인하세요. |
| `INTERIM_LOAN_RATIO_MISSING` | 중도금 대출 가능 범위를 확인하지 못했어요. | 시행사에 분양가 대비 대출 가능 비율을 확인하세요. |
| `BANK_NOT_DISCLOSED` | 공고문에서 취급은행을 확인할 수 없어요. | 시행사에 취급은행·금리·신청 기간을 확인하세요. |
| `LOAN_ARRANGEMENT_ONLY` | 대출 알선은 예정이지만 보장된 조건은 아니에요. | 알선 확정 여부와 불가 시 별도 조달 일정을 확인하세요. |
| `SELF_FUNDING_SCHEDULE_UNKNOWN` | 사업주체 알선 범위 밖 중도금의 조달 방법과 적용 회차가 확인되지 않았어요. | 알선 대출과 알선 범위 밖 금액이 각각 어느 회차에 얼마씩 적용되는지 시행사에 확인하세요. |
| `SELF_FUNDING_REQUIRED` | 중도금 일부를 직접 마련해야 해요. | v0.2 호환 코드입니다. 신규 응답은 자납 자체를 예외 플래그로, 회차 미확정만 HOLD로 내려줍니다. |
| `GUARANTEE_PROVIDER_UNKNOWN` | 중도금 대출 보증기관을 확인하지 못했어요. | 시행사 또는 취급은행에 HF·HUG 등 보증기관을 확인하세요. |
| `INTEREST_TERMS_UNKNOWN` | 중도금 이자 방식을 확인하지 못했어요. | 무이자·이자후불·직접 부담 중 어느 방식인지 확인하세요. |
| `INDIVIDUAL_REVIEW_REQUIRED` | 개인별 대출 심사가 남아 있어요. | 소득·기존 대출을 기준으로 실제 한도를 금융기관에 확인하세요. |
| `BALANCE_CONVERSION_UNCERTAIN` | 입주 시 잔금대출 전환 조건이 확정되지 않았어요. | 재심사 여부와 전환 조건을 금융기관에 확인하세요. |
| `TERMS_DIFFER_BY_HOUSING_TYPE` | 주택형에 따라 조건이 달라요. | 선택한 주택형에 같은 대출·납부 조건이 적용되는지 확인하세요. |
| `UNIT_SELECTION_REQUIRED` | 주택형이나 층에 따라 금액이 달라요. | 선택 주택형과 분양가를 지정한 뒤 다시 계산하세요. |
| `ADDITIONAL_COST_UNKNOWN` | 추가비용의 금액 또는 납부 시점이 불명확해요. | 선택품목 계약서에서 총액과 회차를 확인하세요. |
| `ADDITIONAL_COST_SCOPE_LIMITED` | 공고문에 선택 유상옵션 안내가 있으며, 이번 분석은 전체 선택품목 목록을 보장하지 않아요. | 선택할 시스템에어컨·가전·가구 옵션이 있으면 해당 금액과 납부일정을 추가해 다시 계산하세요. |
| `TABLE_REVIEW_REQUIRED` | 표 구조를 자동으로 확정하기 어려워요. | 표시된 공고문 페이지를 사람이 대조하세요. |
| `SOURCE_CONFLICT` | 공고문 안의 조건이 서로 달라요. | 정정공고와 최신 안내문 중 적용 문서를 확인하세요. |
| `EVIDENCE_MISSING` | 추출값의 원문 근거를 확인하지 못했어요. | 표시된 필드를 사람이 원문과 대조하세요. |
| `PDF_TEXT_UNAVAILABLE` | PDF에서 읽을 수 있는 텍스트가 부족해요. | 원본 PDF를 직접 확인하거나 OCR 검수를 진행하세요. |

사업주체 알선 범위 밖 비율·금액 자체는 `exception_flags=SELF_FUNDING_REQUIRED`에 담기는 자금 위험요인입니다. 이 플래그만으로 현금 자납이 원문에 명시됐다고 해석하면 안 됩니다. 해당 금액의 조달 방법이나 적용 회차를 알 수 없을 때 `SELF_FUNDING_SCHEDULE_UNKNOWN` HOLD를 함께 반환합니다.

문서 불확실성은 HTTP 오류가 아니라 정상 응답의 `analysis_status=PARTIAL|HOLD`와 `holds[]`로 반환합니다. 이는 PDF 분석 상태이며, backend가 사용자 자금으로 계산하는 최종 `funding_status=OK|GAP|BLOCK|HOLD`와 다릅니다. 링크 만료, 다운로드 실패, 모델 장애는 별도 기술 오류입니다.

`INDIVIDUAL_REVIEW_REQUIRED`는 `kind=PERSONAL_REVIEW`, `blocking=false`입니다.
문서 추출 실패가 아니라 실제 승인은 개인 심사가 필요하다는 안내이므로, 이 코드만
있을 때 `analysis_status=READY`일 수 있습니다. backend는 문서 불확실성과 개인 심사
조건을 별도로 표시해야 합니다.

`ADDITIONAL_COST_SCOPE_LIMITED`는 선택하지 않은 유상옵션 카탈로그가
응답에서 완전히 나열되지 않았음을 밝히는 `blocking=false` 범위 안내입니다.
응답에 포함된 `required=false` 비용과 별개이며, 이 안내만으로
`analysis_status`를 `PARTIAL`/​`HOLD`로 낮추지 않습니다.

`GUARANTEE_PROVIDER_UNKNOWN`, `BALANCE_CONVERSION_UNCERTAIN`,
`TERMS_DIFFER_BY_HOUSING_TYPE`은 후속 은행 안내문·주택형별 조건 연동을 위한 예약
코드입니다. 현재 공고문 자동 분석에서는 임의로 만들지 않습니다.
