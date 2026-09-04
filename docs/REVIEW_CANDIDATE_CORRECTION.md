# 감사된 49건 교정 후보 준비

`prepare-audited-review-candidates`는 2026-09-04 전수 감사에서 exact PDF
근거를 확인한 49개 `(complex_id, unit_type_id, unit_type_name,
sale_price_manwon)` tuple만 교정하되, 원본 batch의 전체 검수 workspace를
새 디렉터리에 원자적으로 복제한다.

이 명령은 승인 명령이 아니다.

- 출력 `review_status`는 항상 `AUTO_EXTRACTED`이다.
- 출력 manifest의 `approval_state`는 항상 `PENDING`이다.
- `reviewer`, `reviewed_at`은 항상 `null`이다.
- `REVIEWED`를 만들거나 `artifacts/reviewed`에 기록하지 않는다.

## 준비 범위

교정은 다음 경계 안에서만 실행된다.

1. allowlist에 잠긴 exact target, PDF SHA-256, 페이지 수가 모두 일치해야 한다.
2. 발코니 확장비의 주택형 행·총액·분납 합계가 원문과 정확히 일치해야 한다.
3. `required=false`, `included_in_sale_price=false`는 같은 source lock의
   명시적 선택 문구와 공급금액 미포함 문구가 모두 있을 때만 채운다.
4. 시스템에어컨·가전·가구 등 비발코니 선택품목은 출력에서 제외하고
   `ADDITIONAL_COST_SCOPE_LIMITED`를 유지한다.
5. 정수 만원으로 무손실 표현할 수 없는 금액은 반올림하지 않고 `null`로
   남긴다. 출처 금액으로 계산한 exact ratio만 유지한다.
6. 하나라도 검증하지 못하거나 교정 후 validation이 실패하면 전체 출력을
   생성하지 않는다.
7. `review-draft-manifest.json`은 원본과 byte-for-byte로 같고,
   그 manifest의 상대경로에 있는 전체 초안·체크리스트를 복제한다.
8. 교정된 49건은 같은 상대경로에 덮어쓰고, 승인 template의
   전체 draft SHA-256을 현재 복제본에 맞게 재생성한다. 모든 결정·체크는
   계속 `PENDING`/`false`이며 검수자 정보는 `null`이다.

## 실행

먼저 현재 `schema_version`/`extractor_version`으로 새 `review batch`를
준비한 다음 실행한다. 예전 버전의 batch manifest를 수정해 재사용하지
않는다.

```bash
get-myhome-ai prepare-audited-review-candidates \
  --draft-manifest ../tmp/review-work-current/review-draft-manifest.json \
  --output-dir ../tmp/review-candidates-current
```

출력:

```text
review-candidates-current/
├── review-draft-manifest.json                # 원본 batch manifest과 동일
├── review-approval-manifest.template.json    # 현재 draft hash, 모두 PENDING
├── drafts/                                  # 전체 초안; 49건만 교정
├── checklists/                              # 전체 exact PDF 사람 대조표
└── review-candidate-correction-manifest.json # 49건 교정 감사 로그
```

검수자는 각 checklist와 exact PDF를 대조하고 출력 template의 해당
항목에만 명시적 승인을 기록한 뒤 기존 `validate-review-approval`과
`approve-review-batch`를 그대로 사용한다. 이 디렉터리 생성 자체는 사람
검수 완료를 의미하지 않는다.
