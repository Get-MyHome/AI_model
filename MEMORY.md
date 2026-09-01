# Project memory

## Current state

- Batch-first PDF extraction and the thin authenticated FastAPI endpoint are implemented.
- Local Ollama `qwen3:8b` is the default provider; the model is stored under the ignored project `.local/` directory.
- Canonical v0.3 uses snake_case, sale-price-relative ratios, fixed amounts, exact page evidence, deterministic HOLDs/summaries, and separate AUTO_EXTRACTED/REVIEWED artifacts.
- HOLDs now distinguish blocking document uncertainty from non-blocking personal-review advisories.
- Backend unit types such as `059.9883A` normalize to PDF labels such as `59A`; MVP uses the backend unit-type maximum sale price as a conservative basis.
- Actual Qwen runs cover three real PDFs. The 2026000372 HTTP end-to-end run reproduced 10% contract / 60% interim / 40% arranged loan / 20% self funding / 30% balance and passed structural, arithmetic, and evidence validation.
- The 2026000376 backend-shaped input `01 / 084.7506A / 58660` normalized to `84A` and produced the correct fixed 1,000-manwon interim payment and NOT_AVAILABLE loan state.
- Do not claim a 27-document accuracy metric; the remaining 24 documents are not yet manually labeled and reviewed.
- The current Java backend cannot represent the canonical schedules without loss and must not multiply `arranged_ratio=0.40` by the interim 0.60 again.

## Touched files

- Runtime: `src/get_myhome_ai/`
- Contracts/docs: `docs/`, `README.md`, `.env.example`
- Golden labels and regression tests: `tests/`
- Local actual-model artifacts: ignored `artifacts/qwen3-8b/`

## Next step

- Commit/push the Qwen endpoint changes and give `docs/BACKEND_HANDOFF.md` to the backend owner.
- Obtain one fresh crawler S3 `pdf_url` (or its exact hostname) and a real shared API key before exposing the endpoint; production host allowlisting must remain exact.
- Backend must send all three target fields, add Bearer auth and a 310-second test timeout, ingest canonical v0.3, and store only REVIEWED results keyed by `(complex_id, unit_type_id, sale_price_manwon)`.
- Label and run the remaining 24 PDFs before publishing any 27-document model-accuracy metric.
