# Project memory

## Current state

- Batch-first PDF extraction and the thin authenticated FastAPI endpoint are implemented.
- The authenticated API and local Ollama runtime are enabled as lingering user services. Tailscale Funnel exposes only the API through a dedicated HTTPS port; existing Tailscale routes remain intact. Runtime units and the mode-600 secret environment file live under ignored `.local/runtime/` paths.
- A historical public-edge end-to-end request using a real 2026000376 announcement completed with Qwen3 8B; that reviewed artifact keeps its original model provenance. The current public runtime and default provider have been restarted with local Ollama `qwen3.5:9b`. The public URL and API key must not be committed or copied into logs.
- The final independent `qwen3.5:9b` source-locked run completed for all 24 reference documents: 260/260 compared core labels matched, 24/24 documents were exact on scored fields, 24/24 were safety-exact, and all 4 not-stated labels were safe abstentions. This covers only the labeled document-level core fields. Evidence semantic accuracy remains `NOT_EVALUATED`, and this must not be described as 27-document full-field accuracy.
- Canonical v0.3 uses snake_case, sale-price-relative ratios, fixed amounts, exact page evidence, deterministic HOLDs/summaries, and separate AUTO_EXTRACTED/REVIEWED artifacts.
- HOLDs now distinguish blocking document uncertainty from non-blocking personal-review advisories.
- Backend unit types such as `059.9883A` normalize to PDF labels such as `59A`; MVP uses the backend unit-type maximum sale price as a conservative basis.
- The 2026000372 HTTP end-to-end run reproduced 10% contract / 60% interim / 40% arranged loan / 20% self funding / 30% balance and passed structural, arithmetic, and evidence validation.
- The 2026000376 backend-shaped input `01 / 084.7506A / 58660` normalized to `84A` and produced the correct fixed 1,000-manwon interim payment and NOT_AVAILABLE loan state.
- The 24-document source-locked core reference labels and final Qwen3.5 evaluation are complete. The publishable evaluator report is `artifacts/evaluation/remaining-24-qwen3.5-9b-final-v4.json` (ignored runtime artifact). Do not claim a 27-document full-field metric or semantic evidence accuracy.
- The local `codex/ai-v03-integration` backend branch implements canonical v0.3 ingestion and first-discontinuity calculation, but GitHub `origin/develop` remains legacy until those six commits are merged. Neither branch may multiply `arranged_ratio=0.40` by the interim 0.60 again.
- Full verification after the final grounding fix passed: Ruff format/lint, Python compile, 99 pytest tests, 24-document reference validation (260 scored labels, 4 pending labels, 409 source-checked evidence fragments), and `git diff --check`.
- The verified AI repository state is committed locally. Push to the organization repository is blocked by HTTP 403 for the current GitHub account, and the remote AI repository still has no `main` ref. A complete-history handoff bundle is stored at `/mnt/20t/AI_해커톤/get-myhome-ai-complete.bundle`; refresh and verify it after the final commit when the history changes.

## Touched files

- Runtime: `src/get_myhome_ai/`
- Contracts/docs: `docs/`, `README.md`, `.env.example`
- Golden labels and regression tests: `tests/`
- Local actual-model artifacts: ignored Qwen3 8B, Qwen3.5 regression, and remaining-24 evaluation outputs under `artifacts/`.

## Next step

- Preserve the final 24-document metric scope in all handoffs: 260 compared document-level core labels, 4 safe abstentions, no unit-specific/full-v0.3 or semantic-evidence claim.
- The external endpoint is already running but is intentionally limited to the verified ApplyHome static host. Obtain one fresh crawler S3 `pdf_url`, add only its exact hostname to `PDF_ALLOWED_HOSTS`, and then verify crawler → backend → AI end to end.
- Merge the tested local backend integration branch into the remote repository. Direct push currently fails with HTTP 403, so the backend owner must apply `/mnt/20t/AI_해커톤/backend-ai-v03-integration.patch` or merge equivalent commits.
- Give the updated `docs/BACKEND_HANDOFF.md` and `docs/FRONTEND_HANDOFF.md` to the owners. Grant push access or import the complete-history bundle so the verified AI history can become the remote `main` branch.
