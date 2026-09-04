# Project memory

## Current state

- Batch-first PDF extraction and the thin authenticated FastAPI endpoint are implemented.
- Local inference uses loopback-only Ollama with `qwen3.5:9b`; the public API is exposed through
  Tailscale Funnel. Runtime units and the mode-600 secret environment file live under ignored
  `.local/runtime/` paths. Never commit or print API keys, pre-signed URLs, or full PDF contents.
- App version is `0.3.3`, canonical schema is additive `v0.3`, and extractor version is `0.2.3`.
- `POST /api/analyze` accepts exact complex/PDF/unit/price input and returns facts, source evidence,
  deterministic HOLDs, summaries, validation, and review status.
- `POST /api/funding-stress` is an additive advisory only. Backend remains the production
  funding/verdict SSOT and is a strict no-edit boundary.
- Funding-stress calculator version is `0.1.2`. It accepts only an exact current-extractor
  `REVIEWED` artifact with `validation.passed=true`, a `PRE_CONTRACT` cash snapshot, and
  backend-produced alternative route limits.
- Funding calculations use deterministic Python only. Obligations that cannot be represented
  exactly as integer manwon abstain with `PAYMENT_VALUE_UNKNOWN`. Available funding and document
  caps are floored, never rounded upward.
- LLMs extract candidate facts and evidence only. Risk classification, settlement handling,
  HOLDs, summary text, validation, derived self-funding, and calculations are deterministic.
- Missing facts are never replaced by zero. Unknown required-cost allocation, price inclusion,
  unit applicability, settlement, or installment funding order produces HOLD/UNKNOWN.
- Production `PDF_ALLOWED_HOSTS` includes only the exact crawler bucket forms:
  `getmyhome-pdfs-758862546581.s3.ap-northeast-2.amazonaws.com` and
  `getmyhome-pdfs-758862546581.s3.amazonaws.com`; never replace with a broad S3 wildcard.
- The AI_model deploy key works and pushes to the Get-MyHome organization repository succeed.

## Extraction and review guarantees

- Review approval ignores editable `derived_fields`, re-grounds deterministic metadata against the
  exact PDF, regenerates validation/HOLDs/status/summary, and checks the exact complex/unit/price/
  source/extractor key before setting `REVIEWED`.
- The owned-corpus workflow creates source-locked `REVIEW_DRAFT` manifests, editable AUTO or
  NEEDS_REVIEW artifacts, checklists, and an all-PENDING approval template.
- It can create `REVIEWED` only from an item-level approval manifest with matching reviewer CLI
  confirmation, current schema/extractor versions, exact current draft hash, all five checks,
  timezone-aware review time, fixed attestation, and fresh exact-PDF revalidation.
- Production review capture stores exact source bytes by SHA, URL-free target metadata, and
  immutable auto outputs under ignored local paths for unmatched `/api/analyze` requests.
- A historical extractor-`0.2.0` `REVIEWED` artifact exists for
  `2026000372 / unit 01 / 59A / 108650만원 / ef0ff3…ba10 / 52 pages`. It is provenance only;
  extractor `0.2.3` rejects it until a person checks and reapproves the new-version draft.
- The historical 0372 cost scope includes the checked optional 59A balcony extension only.
  A sub-manwon system-air-conditioner installment was excluded because integer manwon cannot
  represent it without loss.
- Audited candidates whose PDFs contain other paid-option sections carry the deterministic,
  non-blocking `ADDITIONAL_COST_SCOPE_LIMITED` notice. This does not claim a complete option catalog.

## Owned corpus status — 2026-09-04

- Inventory: 167 exact unit/price tuples; 159 are PDF-backed and 8 are HTML-only.
- Current extractor-`0.2.3` Qwen rerun: 159/159 completed, 0 execution failures.
- Initial current-run states: 79 AUTO/validation-pass and 80 NEEDS_REVIEW/validation-fail.
- Final source-audited union: 154 disjoint tuples = 143 direct corrections + 11 historical audited
  fact sets rebound into the current envelope.
- On 2026-09-04, the user confirmed the exact-PDF review and the 154-item approval manifest was
  validated and promoted with reviewer `안지홍`. All 154 are schema `v0.3`, extractor `0.2.3`,
  `REVIEWED`, validation-clean, source-locked, canonical-equal to fresh exact-PDF revalidation,
  and idempotent on revalidation.
- The 159-draft workspace covers every PDF-backed tuple. The five tuples outside the audited union
  are all `2026000356` targets; ApplyHome's integer-manwon price truncation does not preserve the
  exact PDF price, so their source identity cannot be approved under the current request contract.
- Current compatible `REVIEWED` count is 154. The production service loads this exact allowlist
  from ignored local runtime storage; unknown or mismatched targets still return automatic output.
- Of the 154 candidates, 141 have exact integer-manwon core obligations suitable for funding-stress
  after approval. Thirteen must retain `PAYMENT_VALUE_UNKNOWN` due lossless precision limits.
- Latest internal workspace:
  `../tmp/owned-corpus-review-v023-audited154-independent-20260904`. It contains absolute local
  paths and must not be uploaded as a public artifact.
- The older `../tmp/owned-corpus-review-work-v4-exact` and
  `../tmp/review-ready-20260904-final` workspaces are extractor-0.2.0 historical inputs, not current
  completion evidence.

## Evaluation scope

- On 2026-09-02 a separate document-level Qwen3.5 source-locked evaluation covered 24 reference
  PDFs: 260/260 compared core labels, 24/24 document exact, 24/24 field safety, and 4/4 safe
  abstentions. Unit-level amounts, additional costs, banks, guarantors, and semantic evidence
  accuracy were outside that denominator.
- Separate page labels for settlement and six deterministic risk classes cover all 27 PDFs:
  27/27 source hashes, 189/189 labels, 89/89 risk literal-evidence checks, and 51/51 settlement
  evidence checks. This evaluates deterministic post-processing, not full-field Qwen accuracy.
- The corpus has no positive `TERMS_DIFFER_BY_HOUSING_TYPE` reference example.

## Product claims and boundaries

- Do not claim “27-PDF full-field Qwen accuracy 100%” or “159 tuples reviewed”. It is accurate to
  say 154 exact tuples were human-approved, but only within their locked PDF/unit/price identities.
- Do not call project-arranged ratio a personal approved-loan ratio.
- Do not multiply `arranged_ratio=0.40` by `interim_ratio=0.60`; both are total-sale-price-relative.
- Derived uncovered interim amounts mean amounts outside the project-arranged range, never confirmed
  cash-only self-payment.
- For 2026000372, 40% project-arranged financing first leaves 21,730 manwon at interim and later
  a 46,055-manwon balance gap under the regression assumptions. A hypothetical 60% interim loan
  still leaves a 67,785-manwon balance gap because its principal must be repaid/refinanced.
- Do not compare shortfalls from different stages as a simple delta.
- A `REVIEWED` document may still carry genuine uncertainty HOLDs, such as undisclosed bank,
  non-guaranteed arrangement, or personal credit review.
- The announcement-versus-bank-guide comparison core is tested but must stay labeled
  `NOT_VALIDATED_ON_BANK_GUIDANCE` until real bank-guide pairs are manually labeled.

## Handoff artifacts

- AI patches: `/mnt/20t/AI_해커톤/0001-feat-extract-settlement-and-funding-risk-clauses.patch`
  and `/mnt/20t/AI_해커톤/0002-docs-correct-settlement-principal-handoff.patch`
- Complete AI bundle: `/mnt/20t/AI_해커톤/get-myhome-ai-complete.bundle`
- Do not create or distribute backend patches from this project.

## Remaining external/human steps

- No additional approval is required for the current 154-item exact allowlist. New PDFs, changed
  source hashes, unmatched unit/price targets, and the five excluded `2026000356` tuples still need
  separate human review and must remain automatic/HOLD until then.
- Backend must call `/api/analyze` once per new current complex/unit target so the official PDF can
  be captured and source-locked; unknown exact targets correctly remain `AUTO_EXTRACTED`.
- Real bank loan-guide PDF pairs are needed before publicly enabling document-change detection.
- The API key pasted in conversation must be rotated and the GitHub secret updated without exposing
  the new value in chat, logs, commits, or documentation.

## Latest files touched and next step

- Added current-version correction/legacy-refresh workflows and tests.
- Hardened additional-cost scope checks and funding-stress precision/fail-closed behavior.
- Updated corpus status, review-candidate status, strict-audit, handoff, extraction, HOLD, example,
  and funding-stress documentation.
- The 154 approved artifacts are deployed under ignored local runtime storage and the service has
  been restarted with the exact reviewed directory configured. Next step for integration is a
  backend retry with an exact covered PDF/unit/price request; no backend engine changes are needed.
