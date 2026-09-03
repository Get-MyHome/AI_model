# Project memory

## Current state

- Batch-first PDF extraction and the thin authenticated FastAPI endpoint are implemented.
- The local Ollama runtime uses `qwen3.5:9b`. Ollama remains loopback-only; the API is exposed through
  Tailscale Funnel at `https://server.tailb23d4f.ts.net:10000/api/analyze`.
- Runtime units and the mode-600 secret environment file live under ignored `.local/runtime/` paths.
  Never commit or print the API key, pre-signed URLs, or full PDF contents.
- Canonical JSON stays additive schema `v0.3`; extractor version is `0.2.0`.
- App version `0.3.0` adds authenticated `POST /api/funding-stress` as an independent advisory.
  It consumes only an exact extractor-0.2 `REVIEWED` artifact, a `PRE_CONTRACT` cash snapshot, and
  backend-produced alternative loan-route limits. It never replaces or mutates backend verdict logic.
- Funding stress deterministically returns the interim continuity threshold, document-cap margin,
  route-specific stress cases, and first shortfall. Unknown required-cost allocation, sale-price
  inclusion, or unit applicability produces `UNKNOWN` plus a blocking HOLD, never a zero assumption.
- Threshold lookup uses bounded integer-bps binary search and the API runs CPU calculation outside the
  async event loop. Route rule/assumption provenance is preserved in every response case.
- Extractor 0.2 adds evidence-grounded interim-loan settlement requirements and deterministic funding
  risk clauses. Unknown settlement terms produce `BALANCE_CONVERSION_UNCERTAIN`; missing facts are
  never converted to zero.
- LLMs extract candidate facts only. Settlement/risk classification, HOLDs, summaries, validation,
  derived self-funding, and cross-document comparison are deterministic.
- Announcement-versus-bank-guidance comparison has a tested internal core but no public endpoint. It
  must remain labeled `NOT_VALIDATED_ON_BANK_GUIDANCE` until real bank-guide pairs are manually labeled.
- A public authenticated end-to-end request against the real 2026000372 ApplyHome PDF completed on
  extractor 0.2/Qwen3.5. It returned 10% contract, 60% interim, 40% arranged, 20% uncovered,
  `REPAY_OR_CONVERT_TO_MORTGAGE`, four risk clauses, and `validation.passed=true`.
- The final independent Qwen3.5 source-locked run covers 24 reference PDFs: 260/260 compared core
  labels, 24/24 exact documents, 24/24 field safety, and 4/4 safe abstentions. This excludes unit-level
  amounts/additional-costs/banks/guarantors and does not measure semantic evidence accuracy.
- Separate page-by-page labels for settlement and six deterministic risk classes cover all 27 PDFs:
  27/27 document exact matches, 189/189 labels, and 89/89 literal evidence-page quote checks. This is
  deterministic post-processing accuracy, not Qwen accuracy. The corpus has zero positive
  `TERMS_DIFFER_BY_HOUSING_TYPE` examples.
- Full AI verification on 2026-09-02: Ruff passed, compileall passed, 130 pytest tests passed, and
  `python scripts/evaluate_risk_settlement.py` passed all 27 source hashes, 189/189 deterministic
  labels, 89/89 risk quotes, and 51/51 settlement quotes.
- Review approval ignores editable `derived_fields`, re-grounds deterministic metadata against the
  exact PDF, and regenerates validation, HOLDs, status, and summary before setting `REVIEWED`.
- A server-local extractor-0.2 `REVIEWED` artifact exists for the exact tuple
  `2026000372 / unit 01 / 59A / 108650만원 / SHA ef0ff3…ba10 / 52 pages`. It is intentionally
  ignored by Git. On 2026-09-03 the public `/api/analyze` returned this artifact and the public
  `/api/funding-stress` returned HTTP 200 for the same official ApplyHome bytes.
- The reviewed 0372 additional-cost scope contains only the manually checked optional 59A balcony
  extension (1,870만원, 187/1,683). The optional system-air-conditioner row is excluded because its
  715,000/6,435,000-won installments cannot be represented exactly by integer manwon fields.
- Derived uncovered interim amounts are described as amounts outside the project-arranged range,
  never as confirmed cash-only self-payment. HOLD and risk next-actions use the same wording.
- Backend is a strict no-edit boundary. It remains the production funding/verdict SSOT; AI funding
  stress is additive advisory output only.
- The AI_model deploy key is active and pushes to the Get-MyHome organization repository succeed.
- Production `PDF_ALLOWED_HOSTS` contains both exact crawler bucket forms: the regional
  `getmyhome-pdfs-758862546581.s3.ap-northeast-2.amazonaws.com` host and the global
  `getmyhome-pdfs-758862546581.s3.amazonaws.com` host. Never replace these with a broad S3 wildcard.
- The owned-corpus batch review workflow creates source-locked `REVIEW_DRAFT` manifests,
  editable AUTO/NEEDS_REVIEW artifacts, checklists, and a PENDING approval template. It can create
  `REVIEWED` only from an item-level approval manifest, matching reviewer CLI confirmation, current
  extractor/schema versions, exact draft hash, and a fresh exact-PDF revalidation.
- The 2026-09-04 exact extraction run now covers all 159/159 PDF-backed tuples. The source-locked
  v4 review batch contains 159 drafts/checklists: 86 pass structural validation and 73 fail it;
  none is automatically human-approved. Eight HTML-only tuples remain unavailable without PDFs.
- A separate strict semantic audit covered the recent-30-day 20 PDF documents / 117 target tuples.
  Only the existing manually reviewed 2026000372/unit01 artifact is usable as-is; the remaining
  drafts contain target-cost, table-column, precision, evidence, or installment-allocation issues.
- Integer-manwon grounding now abstains instead of rounding source amounts that are not exactly
  divisible by 10,000 won. This prevents half-manwon and sub-manwon source values from becoming
  false exact values.
- Production review capture is enabled for unmatched `/api/analyze` requests. It stores exact
  source bytes by SHA, URL-free target metadata, and immutable auto outputs under ignored local
  paths. A backend call is still required once per current complex to supply each official PDF.
- The latest review workspace is `../tmp/owned-corpus-review-work-v4-exact`. It contains 159
  source-locked drafts/checklists (86 structural pass, 73 fail) and incorporates fresh precision-safe
  reruns for all three 2026000372 units. `version_compatible=159` is not an approval count.

## Handoff artifacts

- AI patches: `/mnt/20t/AI_해커톤/0001-feat-extract-settlement-and-funding-risk-clauses.patch`
  and `/mnt/20t/AI_해커톤/0002-docs-correct-settlement-principal-handoff.patch`
- Complete AI bundle: `/mnt/20t/AI_해커톤/get-myhome-ai-complete.bundle`
- Do not create or distribute backend patches from this project. Backend is read-only.

## Claims and boundaries

- Do not claim “27-PDF full-field Qwen accuracy 100%” or “semantic evidence accuracy 100%.”
- Do not call the project-arranged ratio a personal approved-loan ratio.
- Do not multiply `arranged_ratio=0.40` by interim ratio `0.60`; both are sale-price-relative.
- For 2026000372, full 60% interim lending still creates a 67,785-manwon balance shortfall because the
  65,190-manwon principal must be repaid/refinanced. The actual 40% case first breaks at interim by
  21,730 and later has a 46,055 balance gap. Do not compare different-stage gaps as a simple delta.
- Only `REVIEWED` artifacts with exact PDF/complex/unit/price/extractor-version keys may drive user
  funding calculations. New or non-matching extractor-0.2 responses remain `AUTO_EXTRACTED` until a
  human checks the generated review sheet. `REVIEWED` removes the AI review-pending gate, not genuine
  document uncertainty such as an undisclosed bank or personal approval.

## Remaining external/human steps

- Backend/crawler must correct or explain the observed source mismatch: a request labeled
  `2026000372` returned p.8/p.10/p.47 evidence matching the 74-page `2026000374` source
  (`a67a4e…72ab`) instead of the locked 52-page 0372 source (`ef0ff3…ba10`). Compare the raw AI
  `meta.source_sha256` and `source_page_count`; never approve the mismatched source.
- Additional target tuples still require independent source-locked human review before production use.
- Do not approve the batch template wholesale. A human must review every approved target's source
  PDF, exact unit/price, payment and loan terms, evidence, and additional-cost applicability; update
  the edited draft hash and leave all unreviewed entries PENDING.
- Real bank loan-guide PDF pairs are required before enabling document-change detection publicly.
