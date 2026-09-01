# Remaining 24-document reference set

This directory contains source-grounded reference labels for the 24 announcement
documents that are not part of `tests/fixtures/golden/`.

## What this set is (and is not)

- Every reviewed value is tied to a page and a verbatim fragment from the saved
  `pdftotext` output of the original announcement PDF.
- Model output was never used as a label source.
- The labels cover the document-level fields that change the funding-completion
  decision: payment ratios, interim installments, balance timing, loan-arrangement
  state, arranged-loan ratio, self-funding ratio, interest type, and the required
  prepayment ratio.
- All 24 locked PDFs have now been visually checked against the coordinate-heavy
  payment tables and loan-guidance pages.
- This is **not 24 additional full v0.3 golden fixtures**. Unit-specific amounts and
  options require a selected housing type and separate field-level labeling; explicit
  loan-bank and guarantee-provider labels also require their own source audit. They are
  excluded from this metric denominator.
- `pdf_visual_review=COMPLETE` means a human checked the coordinate table in the
  source PDF. It does not add the deliberately excluded unit-specific fields to the
  metric. Do not publish a “27-document full-field accuracy” number unless those
  fields are separately labeled.

## Label states

| State | Meaning | Included in metrics |
| --- | --- | --- |
| `VERIFIED_TEXT` | Value is stated directly in the source fragment | yes |
| `VERIFIED_NORMALIZED` | Direct source text normalized to an enum, ISO date, or month | yes |
| `VERIFIED_DERIVED` | Deterministic arithmetic from other verified labels | yes |
| `VERIFIED_NOT_STATED` | Full source review confirms that the value is not stated; expected safe abstention | safety metric |
| `NEEDS_SECOND_REVIEW` | Human annotation remains unresolved; forbidden in a `COMPLETE` reference | no |

Unknown is never converted to zero. When complete source review proves that an
arranged-loan ratio is not stated, the reference remains `null` with
`VERIFIED_NOT_STATED`; a safe model response must also be null and carry the blocking
`INTERIM_LOAN_RATIO_MISSING` HOLD.

## Field definitions

All ratios are fractions of the total sale price.

| Reference field | Canonical meaning |
| --- | --- |
| `payment_schedule.down_payment_ratio` | Total contract/down-payment share |
| `payment_schedule.interim_payment_ratio` | Total interim-payment share |
| `payment_schedule.balance_payment_ratio` | Total balance-payment share |
| `payment_schedule.interim_installments` | Ordered number, ratio, and due date |
| `payment_schedule.balance_due_text` | Source-defined balance due rule |
| `payment_schedule.move_in_month` | Announced expected move-in month |
| `interim_loan.arrangement_status` | Canonical loan-arrangement enum |
| `interim_loan.arranged_ratio` | Maximum project-arranged loan share of sale price |
| `interim_loan.self_funding_ratio` | Interim share not covered by the arranged ratio |
| `interim_loan.interest_type` | Canonical interest treatment |
| `interim_loan.prepay_requirement_ratio` | Sale-price share that must be paid before loan execution |

`self_funding_ratio=0.0` is used only when both the interim ratio and the arranged
ratio are directly verified as equal; it describes the announced project structure,
not an individual's eventual underwriting result.

## Manual-label workflow

1. Lock the PDF/TXT pair by filename, page count, and SHA-256 in `MANIFEST.json`.
2. Run `build_candidates.py` only to locate likely pages. Candidate output is marked
   `UNREVIEWED` and must never be copied into a metric automatically.
3. Read the complete payment table and the complete loan-guidance section.
4. Enter a value only with page evidence. Use `null + NEEDS_SECOND_REVIEW` when the
   document does not safely establish it.
5. Run `python evaluation/validate_references.py --source-dir <gonggo-dir>`.
6. Visually inspect coordinate-heavy PDF tables before upgrading
   `pdf_visual_review` from `PENDING` to `COMPLETE` (complete for all 24 documents).

After actual model artifacts for these IDs exist, measure only the reviewed scope:

```bash
python evaluation/evaluate_core.py \
  --actual-dir artifacts/remaining-24 \
  --output artifacts/evaluation/remaining-24-core.json
```

To reproduce the actual deployed Qwen/API run from the saved ApplyHome detail pages:

```bash
python -m evaluation.run_api \
  --source-dir /path/to/locked/gonggo \
  --output-dir artifacts/remaining-24
```

The API runner checks the downloaded PDF SHA-256 against the locked local source,
continues after a per-document failure, and writes a resumable run report. It never
prints the bearer key or signed URL.

The evaluator embeds the claim limit in its report. Aggregate accuracy is publishable
only when all 24 expected IDs are present and source-locked. A partial run is marked
`INCOMPLETE_NON_PUBLISHABLE`, its public aggregate rate is `null`, and all missing IDs
are listed. Expected abstentions are scored separately from ordinary field matches.
The current evaluator does not claim semantic evidence accuracy; it reports that
metric as `NOT_EVALUATED`.

## Final local Qwen3.5 result (2026-09-02)

The independent `qwen3.5:9b` rerun completed for all 24 locked documents. The
publishable evaluator report recorded:

- 260/260 compared core labels matched;
- 24/24 documents exact on the scored core fields;
- 24/24 documents safety-exact;
- 4/4 `VERIFIED_NOT_STATED` labels returned safe abstentions, with no unsafe value;
- 24/24 outputs passed recomputed deterministic validation.

This result is deliberately narrower than full v0.3 accuracy. It excludes selected
unit amounts, additional costs, loan-bank names, and guarantee providers, and it does
not measure semantic evidence accuracy. The latter remains `NOT_EVALUATED`. The
ignored local report path is
`artifacts/evaluation/remaining-24-qwen3.5-9b-final-v4.json`.

## Known high-risk cases

- `2026000323`: the main payment table is page 7; later option tables are not the
  apartment payment schedule.
- `2026000327`: the source table prints the same `2026-09-30` date for all six
  interim installments. The reference preserves the source instead of “fixing” it.
- `2026000382`: `2026-10-07` is the second contract-payment date. The first interim
  date is `2026-11-20`.
- `2026000295` and `2026000342`: complete visual review confirmed that the
  announcement describes conditional arrangement and deferred interest but does not
  state an arranged-loan percentage. The expected behavior is an explicit, blocking
  abstention rather than an inferred percentage.
