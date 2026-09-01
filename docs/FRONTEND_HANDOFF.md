# Frontend handoff: 청약 자금 완주 진단 최소 계약

> 상태: **구현 계약서**. 아래 프론트 연동과 GitHub backend 병합은 완료된 기능이 아니다.
> 점검 기준: 2026-09-02, frontend `main` `f60abf1a`, backend `develop` `b6c6156`, 로컬 backend 통합 브랜치 `a2d67a3`.

## 1. 목표 화면

현재 서비스를 “대출상품 목록 + 부족액 계산기”가 아니라 다음 결과로 보이게 한다.

1. **최초 자금 단절 시점과 부족액**
2. **중도금 금융조달 확정도**
3. 계약금→중도금→잔금 납부 타임라인
4. 아직 확인할 조건과 다음 행동(HOLD)
5. 판정을 바꾼 공고문 PDF 페이지·원문 근거

## 2. 현재 결손

### GitHub frontend `main`

- `src/apis/complexes.ts`, `src/queries/complexes.ts`: 공고 API만 있고 `POST /verdicts` 연동이 없다.
- `src/types/`: `VerdictResponse`, `FundingDiagnosis`, HOLD, PDF 근거 타입이 없다.
- `src/components/notices/unitTypeSelector.tsx`: 선택한 `unit_type_id`는 로컬 state에만 있고 **가능성 판정하기** 버튼에 호출·라우팅이 없다. `complexId`도 prop으로 받지 않는다.
- `src/app/eligibility/notices/[id]/page.tsx`: 현재 빈 파일이다.
- `src/app/eligibility/result/`: 결과 라우트가 없다.
- `src/types/eligibility.ts`: 생년월일이 6자리지만 backend는 `YYYY-MM-DD`를 요구한다. `assets`는 backend의 `cash`와 의미가 다르고, `ENGAGED`(결혼 예정)도 없다.

### GitHub backend `develop`

- `POST /api/v1/verdicts`는 있지만 배포 브랜치의 `VerdictResponse`에는 `funding_diagnosis`가 없다.
- 로컬 통합 브랜치에는 `FundingDiagnosisResponse`와 계산이 구현돼 있지만, 팀 backend에 병합되지 않으면 frontend가 받을 수 없다.

## 3. 판정 요청 계약

Frontend는 Next 프록시를 통해 호출한다.

```http
POST /api/proxy/verdicts
Content-Type: application/json
```

```json
{
  "user": {
    "annual_income": 4000,
    "cash": 10865,
    "birth_date": "1995-01-01",
    "marital": "SINGLE",
    "homeless": true,
    "include_deposit_as_cash": false,
    "monthly_saving": 100,
    "household_type": "HEAD",
    "net_asset": 30000
  },
  "complex_id": "2026000372",
  "unit_type_id": "01"
}
```

| 현재 frontend 필드 | backend 필드 | 변환 규칙 |
| --- | --- | --- |
| `annualIncome` | `annual_income` | 빈 문자가 아니면 만 원 정수 |
| `assets` | `cash` | 화면 문구부터 **현재 사용할 수 있는 보유 현금**으로 교정. 순자산을 보내지 않는다. |
| `birthDate` | `birth_date` | `YYYY-MM-DD`. 6자리를 세기 추정으로 변환하지 말고 입력·저장 규격을 ISO 날짜로 바꾼다. |
| `maritalStatus` | `marital` | `미혼→SINGLE`, `기혼→MARRIED`, `결혼 예정→ENGAGED` |
| `homeOwnership` | `homeless` | `none→true`, `owned→false` |
| `includesJeonseDeposit` | `include_deposit_as_cash` | boolean |
| `monthlySaving` | `monthly_saving` | 선택값. 빈 문자면 전송하지 않는다. |
| `householdRole` | `household_type` | `세대주→HEAD`, `단독세대주→SINGLE_HEAD`, `세대원→MEMBER` |
| `netWorth` | `net_asset` | 선택값, 만 원 정수 |
| 공고 카드의 `complex_id` | `complex_id` | 선택한 단지 ID |
| 평형의 `unit_type_id` | `unit_type_id` | 선택한 청약홈 주택형 번호 |

`rule_version`을 보내지 않으면 backend 기본 버전을 적용한다. Frontend가 AI endpoint를 직접 호출하지 않는다.

## 4. 응답을 화면에 연결하는 규칙

### 4.1 대표 결과 카드

| 화면 | `data.funding_diagnosis` 필드 |
| --- | --- |
| 최종 상태 | `funding_status` (`OK`, `GAP`, `BLOCK`, `HOLD`) |
| 최초 단절 구간 | `first_discontinuity.stage` |
| 시점 | `due_date` → `due_month` 순으로 표시 |
| 부족액 | `shortfall_manwon` (만 원) |
| 확정성 | `certainty` (`CONFIRMED`, `CONDITIONAL`) |

- `first_discontinuity == null`: **“현재 입력 기준 최초 자금 단절 없음”**
- `due_date == null` 이고 `due_month == null`: 시점을 숨기거나 추정하지 말고 **“중도금 회차·시점 확인 필요”**

### 4.2 중도금 금융조달 확정도

`data.funding_diagnosis.interim_financing`을 표시한다.

| 화면 | 필드 | 주의 |
| --- | --- | --- |
| 확정도 문구/단계 | `arrangement_label`, `document_certainty_level` | 단계는 1~5 |
| 중도금 납부비율 | `interim_payment_ratio` | `0.60` → `60%` |
| 사업주체 알선 범위 | `project_arranged_ratio` | **개인 승인 대출액이 아님** |
| 별도 조달 필요 비율 | `uncovered_ratio` | `0.20` → `20%` |
| 별도 조달 비율 근거 | `uncovered_ratio_origin` | 원문/산술 파생 구분 |
| 은행·이자 | `bank_names`, `interest_type` | 빈 값은 “미확인” |
| 개인 심사 | `personal_review_required` | `true`면 사업장 조건과 별도로 개인 심사가 남음 |

### 4.3 타임라인·HOLD·근거

- 납부 타임라인: `timeline[].sequence`, `stage`, `label`, `due_date`, `due_month`, `due_text`, `required_manwon`, `available_manwon`, `loan_applied_manwon`, `shortfall_manwon`, `status`, `certainty`
- 자금 진단 HOLD: `funding_diagnosis.unresolved_conditions[]`
- 전체 입력·상품 HOLD: 최상위 `holds[]`
- 두 HOLD 목록을 합칠 때는 `reason_code`로 중복 제거한다. `message`와 `next_action`만 표시하고 자유 조언을 추가하지 않는다. 현재 backend 공통 HOLD는 `kind`, `blocking`, `message`가 `null`일 수 있으므로 null-safe 처리가 필요하다.
- PDF 근거: `funding_diagnosis.document_evidence[].field`, `page`, `raw_text`
- 규정·계산 근거: 최상위 `evidence[]`. PDF 원문 근거와 섹션을 분리한다.
- `analysis_summary`는 검증된 추출값으로 만든 고정 요약이며 AI 조언으로 표시하지 않는다. `price_basis`도 함께 표시한다.

## 5. 화면 구성 순서

1. **최초 자금 단절 시점·부족액** 대표 카드
2. **중도금 금융조달 확정도**: 확인된 값과 미확정 값
3. 계약금→중도금→잔금 타임라인
4. HOLD 사유와 은행·시행사 확인 행동
5. 공고문 페이지·원문 근거
6. 보조 정보: 대출상품 비교·청약 자격·규정 근거

대출상품 목록을 첫 화면의 주인공으로 두지 않는다.

## 6. 2026000372 실측 데모

검수된 실제 공고문과 로컬 backend 테스트에서 다음을 재현했다.

- 분양가: `108,650만 원`
- 중도금 납부비율: `60%`
- 사업주체 알선 범위: `40%`
- 알선 범위 밖 별도 조달: `20%`
- 알선 상태: `PLANNED`, 확정도 `2단계 — 대출 알선 예정`

| 비교 | 최초 단절 | 부족액 |
| --- | --- | ---: |
| 테스트 전용 오류 가정: 중도금 60%를 모두 확정 대출로 처리 | 2030-01 잔금 | 2,595만 원 |
| 실제 PDF 검수값: 알선 40% + 별도 조달 20% | 중도금 | 21,730만 원 |

부족액은 `19,135만 원` 늘고, 최초 단절 구간은 잔금에서 중도금으로 앞당겨진다. 다만 실제 알선 조건은 보장된 개인 대출이 아니므로 **최종 상태는 `HOLD`**다. 시점 원문을 확정할 수 없어 날짜는 `null`이며, 화면에는 **“중도금 회차·시점 확인 필요”**로 표시한다.

### 발표·화면에서 금지할 표현

- **“AI 전 GAP → AI 후 BLOCK”이라고 말하지 않는다.** 실측 결과가 아니다.
- 정확한 표현: **“PDF 실제 조건을 반영하자 최초 자금 단절이 잔금에서 중도금으로 앞당겨졌고, 예상 부족액이 2,595만 원에서 21,730만 원으로 늘었다. 알선 미확정으로 최종은 HOLD다.”**
- 60% 전액 대출은 테스트용 반사실 가정이며 AI 결과로 저장·표시하지 않는다.

## 7. 최소 구현 순서

1. 로컬 backend 통합 커밋/패치를 팀 `develop`에 병합하고 `funding_diagnosis` 실제 JSON을 Swagger로 고정한다.
2. Frontend에 `src/types/verdict.ts`, `src/apis/verdicts.ts`, `src/queries/verdicts.ts`를 추가하고 TanStack Query mutation으로 `POST /verdicts`를 연동한다.
3. 생년월일·현금·혼인 상태를 포함한 요청 변환 함수를 만든다.
4. `notices/[id]/page.tsx`에 `NoticeDetail`을 연결하고 `UnitTypeSelector`가 `complexId` + `unitTypeId`로 판정을 호출하게 한다.
5. `/eligibility/result` 화면을 대표 결과→확정도→타임라인→HOLD→근거 순서로 구현한다.
6. Mutation 결과는 TanStack Query cache에 `verdict_id`로 보관한다. 새로고침 복원이 필수면 backend의 기존 30분 임시 캐시를 재사용한 `GET /verdicts/{verdictId}`를 별도 합의한다.
7. 2026000372 검수본으로 수치·null 날짜·HOLD·근거 펼침을 통합 테스트한다.

이 순서가 완료되기 전에는 새 방향이 **사용자 화면에 구현됐다고 설명하지 않는다.**
