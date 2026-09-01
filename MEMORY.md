# Project memory

## Current state

- Batch-first PDF extraction pipeline is implemented with local-file and crawler URL inputs.
- Canonical v0.3 contract uses snake_case, sale-price-relative ratios, fixed amounts, evidence, deterministic HOLDs/summaries, and separate review artifacts.
- Actual golden PDFs 2026000358/0372/0376 pass page extraction, candidate selection, validation, and evidence replay tests.
- Thin optional FastAPI endpoints and a safe current-Java legacy adapter are implemented.
- The current Java backend cannot represent any of the three golden schedules without loss; it must adopt the canonical contract before real integration.
- No model provider API key exists in the development environment, so live OpenAI extraction accuracy is not yet measured.

## Touched files

- Runtime: `src/get_myhome_ai/`
- Contracts/docs: `docs/`, `README.md`, `.env.example`
- Golden labels and regression tests: `tests/`

## Next step

- Give `docs/BACKEND_HANDOFF.md` to the backend owner and agree on canonical v0.3 ingestion or reviewed batch-file storage.
- Set `OPENAI_API_KEY`, run the same three-document evaluation with `AI_PROVIDER=openai`, inspect failures, and iterate the extraction prompt.
- Label and run the remaining 24 PDFs before publishing any 27-document model-accuracy metric.
