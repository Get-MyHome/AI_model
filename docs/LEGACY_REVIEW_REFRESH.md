# 현재 Extractor 구 검수 후보 refresh

## 목적

Extractor `0.2.0` 시점에 원문 대조한 교정 후보 10건과 과거
`REVIEWED` 1건을 현재 extractor의 검수 초안으로 안전하게 재구성한다.
이 절차는 과거 승인을 승계하거나 버전 문자열만 바꾸지 않는다.

## 대상

| 공고 | exact target | PDF source lock |
| --- | ---: | --- |
| `2026000365` | 4건 | `97e1c098… / 62p` |
| `2026000368` | 4건 | `bf2de56a… / 49p` |
| `2026000372` | 3건 | `ef0ff3b5… / 52p` |

총 11건이며, `complex_id + unit_type_id + 정규화 주택형 + 분양가 + PDF
SHA-256 + 물리 페이지 수`가 모두 같아야 한다.

## Fail-closed 재구성 방법

1. 현재 extractor로 생성한 full review workspace를 기준 엔벨로프로 사용한다.
2. 구 아티팩트는 대상·출처·구 버전·아티팩트 SHA-256이 하드 잠금된
   11건과 같을 때만 읽는다.
3. 구 아티팩트에서 납부구조·중도금 금융조건·발코니 선택비용·근거만
   옮긴다.
4. 과거 `review_status`, 검수자, 검수 시각, HOLD, 요약, 위험조항,
   예외 플래그, validation은 승계하지 않는다.
5. 현재 원본 PDF로 `prepare_review_draft` 재검증을 두 번 수행해 결과가
   멱등이고 `validation.passed=true`인지 확인한다.
6. 결과는 항상 `AUTO_EXTRACTED`, `reviewer=null`, `reviewed_at=null`,
   승인 매니페스트 `PENDING`으로 저장한다.

하나라도 다르거나 출처 재검증이 실패하면 출력 디렉터리 전체를
생성하지 않는다.

## 실행

```bash
python scripts/refresh_legacy_review_candidates.py \
  --draft-manifest /path/to/current-correction-workspace/review-draft-manifest.json \
  --legacy-workspace /path/to/review-ready-20260904-final \
  --historical-reviewed-artifact artifacts/reviewed/2026000372__01__108650.v02.json \
  --output-dir /path/to/new-full-review-workspace
```

새 워크스페이스의 `review-approval-manifest.template.json`은 모든 항목을
`PENDING`으로 다시 바인딩한다. 사람이 exact PDF와 5개 항목을 대조하고
기존 승인 명령을 수행하기 전에는 운영 `REVIEWED` 저장소에 넣지 않는다.

이 워크스페이스와 provenance manifest에는 로컬 절대경로가 포함되므로
내부 검수용으로만 사용하고 GitHub 또는 공개 첨부물에 올리지 않는다.
