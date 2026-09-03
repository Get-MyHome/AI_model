# 보유 자료 배치 검수

이 절차는 보유 PDF와 주택형 tuple을 사람이 검수할 수 있는 형태로 준비합니다.
준비 명령은 어떤 경우에도 `REVIEWED`를 만들지 않습니다.

## 실서비스 신규 PDF 확보

운영 서버에 `REVIEW_CAPTURE_DIR`를 설정하면 `/api/analyze`가 exact 검수본을 찾지 못한
경우에만 다음 자료를 서버 로컬에 남깁니다.

- `sources/<source_sha256>.pdf`: crawler가 전달한 공식 PDF 원본
- `requests/<request_key>.json`: URL을 제외한 exact target과 source lock
- `auto/<request_key>__<result_sha256>.json`: 해당 자동 분석 실행 결과

pre-signed URL과 API key는 기록하지 않으며 파일 권한은 소유자 전용입니다. 같은 원본은
SHA-256으로 중복 저장하지 않고, 같은 요청의 서로 다른 자동 분석 결과는 덮어쓰지
않습니다. 이 캡처 역시 검수 승인이 아니므로 아래의 원문 대조와 명시적 승인 절차를
그대로 거쳐야 합니다.

백엔드가 현재 공고를 한 번씩 호출한 뒤 캡처를 기존 배치 검수 입력으로 바꿉니다.

```bash
get-myhome-ai build-captured-inventory \
  --capture-dir .local/review-capture \
  --output ../tmp/current-captured-inventory.json

get-myhome-ai prepare-review-batch \
  --inventory ../tmp/current-captured-inventory.json \
  --auto-artifact-dir .local/review-capture/auto \
  --output-dir ../tmp/current-review-work
```

변환기는 캡처 PDF를 다시 해시하고 요청·자동결과의 complex ID, 주택형, 분양가,
source SHA-256과 페이지 수가 모두 같은지 확인합니다. 같은 exact target에 서로 다른
PDF가 들어오거나, target 입력이 빠졌거나, `REVIEWED` 파일이 auto 디렉터리에 섞이면
중단합니다.

캡처는 자동 삭제하지 않습니다. 운영자는 캡처 디렉터리 용량을 모니터링하고, 검수와
백업이 끝난 원본만 별도 보존정책에 따라 정리해야 합니다. 아직 검수되지 않은 원본을
일괄 삭제하지 않습니다.

## 상태 경계

- 초안 JSON 내부의 `review_status`는 `AUTO_EXTRACTED` 또는 `NEEDS_REVIEW`입니다.
- 배치 매니페스트만 해당 파일을 `artifact_type=REVIEW_DRAFT`로 표시합니다.
- 초안을 `artifacts/reviewed/`에 복사해서는 안 됩니다.
- `REVIEWED`는 검수자가 항목별 체크를 완료하고 명시적 승인한 항목에만 생성됩니다.

## 1. 검수 초안 준비

`owned_corpus_inventory_v1` 인벤토리와 하나 이상의 자동 추출 디렉터리를 입력합니다.
자동 추출 디렉터리는 문서 공통 결과나 정확히 같은 주택형·분양가 결과를
담은 `AnalysisResponse` JSON 목록입니다.

```bash
get-myhome-ai prepare-review-batch \
  --inventory ../tmp/owned_corpus_inventory_v1.json \
  --auto-artifact-dir artifacts/remaining-24-regrounded-current \
  --auto-artifact-dir artifacts/qwen3-8b \
  --auto-artifact-dir artifacts/auto \
  --reference-dir evaluation/reference \
  --output-dir ../tmp/review-work-20260904
```

`--output-dir`는 검수자의 기존 수정을 덮어쓰지 않도록 존재하지 않는 새 경로여야
합니다. 출력은 다음과 같습니다.

```text
review-work-20260904/
├── drafts/                                 # 편집 가능한 AUTO/NEEDS_REVIEW JSON
├── checklists/                             # PDF 대조용 항목별 검수표
├── review-draft-manifest.json              # source/target/hash 잠금과 차단 사유
└── review-approval-manifest.template.json  # 사람이 작성할 승인 양식
```

승인 양식 v2는 `draft_manifest_sha256`으로 생성 당시 검수 배치 전체를 잠급니다.
승인 전에 인벤토리·자동추출 artifact·초안의 경로와 SHA-256을 다시 확인하므로,
매니페스트에서 차단 사유나 extractor 버전만 수정해 승인을 우회할 수 없습니다.
CLI의 `version_compatible` 수치는 현재 schema/extractor 버전과 호환되는 초안 수일
뿐입니다. `validation.passed`나 사람 검수 완료를 뜻하지 않습니다.

원본 PDF SHA-256·페이지 수가 인벤토리와 다르거나 호환되는 자동 추출본이 없는
tuple은 `unavailable_targets`에 남습니다. 이전 extractor 결과도 검수 참고용 초안으로는
만들지만 `EXTRACTOR_VERSION_MISMATCH` 차단 사유가 남아 승인되지 않습니다.

`evaluation/reference` 라벨은 같은 PDF의 문서 공통 핵심필드를 대조하는 참고입니다.
주택형별 금액, 추가비용, 중도금 취급은행, 보증기관은 라벨 범위 밖이므로 각각
원문을 다시 확인해야 합니다.
특히 문서 공통 auto artifact를 여러 target에 재사용한 초안은 추가비용의 금액·필수 여부·
분양가 포함 여부·주택형 적용 범위를 target별로 확인해야 합니다. 승인 양식은
`additional_cost_scope_reviewed=true`가 아닌 항목의 `APPROVE`를 거부합니다.

## 2. 항목별 사람 검수

검수자는 checklist의 주택형·동·층, 분양가, 납부일정·대출조건, 근거 페이지,
추가비용 적용 범위를 PDF 원문과 대조합니다. 추출값이 틀리면 `drafts/*.json`을
수정합니다. 수정한 파일은 다시 SHA-256을 계산해 승인 매니페스트의 해당
`draft_sha256`에 기록해야 합니다.

```bash
sha256sum ../tmp/review-work-20260904/drafts/<draft>.json
```

템플릿을 별도 파일로 복사한 뒤, 승인할 각 항목에 다음을 기록합니다.

- 최상위 `reviewer`가 실제 검수자 이름
- timezone이 포함된 `reviewed_at`
- `attestation=I_REVIEWED_EACH_APPROVED_DRAFT_AGAINST_THE_EXACT_SOURCE_PDF`
- 항목 `decision=APPROVE`
- 현재 편집본의 `draft_sha256`
- 다섯 개 `checks` 전부 `true`

아직 보지 않은 항목은 `PENDING`, 승인하지 않을 항목은 `REJECT`로 남겨둡니다.

## 3. 쓰기 없는 승인 사전 검증

```bash
get-myhome-ai validate-review-approval \
  --draft-manifest ../tmp/review-work-20260904/review-draft-manifest.json \
  --approval-manifest ../tmp/review-approval-20260904.json \
  --reviewer "검수자 이름" \
  --confirm-approval-manifest
```

이 명령은 `REVIEWED` 파일을 쓰지 않습니다. 다음을 모두 재검증합니다.

- draft batch ID, 검수자, attestation, 항목별 체크
- 생성 당시 draft manifest SHA-256과 원본 inventory SHA-256
- 전체 inventory target과 초안·준비불가 target의 누락·중복 여부
- 원본 자동추출 artifact SHA-256, 실제 schema/extractor 버전, 승인 차단 사유 재계산
- 승인 매니페스트의 draft SHA-256
- PDF SHA-256·페이지 수·complex ID·unit ID·unit name·분양가
- 현재 schema/extractor version
- 근거 문장·JSON Pointer·비율·금액·회차 결정론적 검증

## 4. 명시적 승인

사전 검증이 통과한 동일 명령에 `approve-review-batch`와 새 출력 디렉터리를
사용합니다.

```bash
get-myhome-ai approve-review-batch \
  --draft-manifest ../tmp/review-work-20260904/review-draft-manifest.json \
  --approval-manifest ../tmp/review-approval-20260904.json \
  --output-dir ../tmp/reviewed-approved-20260904 \
  --reviewer "검수자 이름" \
  --confirm-approval-manifest
```

출력 디렉토리도 기존 승인본을 덮어쓰지 않도록 새 경로여야 합니다. 명령은
먼저 모든 APPROVE 항목을 사전 검증한 뒤 결과와
`review-approval-receipt.json`을 한 디렉토리에 저장합니다. receipt에는 백엔드 전달에 필요한
`complex_id`, `unit_type_id`, 원본 `unit_type_name`, `sale_price_manwon`, PDF SHA-256,
페이지 수, 검수자와 최종 아티팩트 SHA-256이 남습니다.
receipt에는 승인에 사용한 draft manifest SHA-256도 함께 남아 승인 이력을 다시
대조할 수 있습니다.
