# REVIEWED coverage and runtime handoff

## Purpose

The backend handoff is an explicit allowlist of exact human-reviewed target tuples. It does not
promote an inventory, a document-level evaluation result, or every unit in the same announcement to
`REVIEWED`.

The allowlist identity is:

`(source_sha256, complex_id, unit_type_id, normalized unit_type_name, sale_price_manwon, schema_version, extractor_version)`

The backend must still compare the SHA-256 and physical page count from each fresh `/api/analyze`
response. A complex ID match alone is never sufficient.

Handoff generation is also fail-closed. Before an allowlist is emitted, the builder validates the
`owned_corpus_inventory_v1` contract, resolves each relative PDF path under its recorded absolute
`source_directory`, re-hashes every PDF, and recounts its physical pages. A missing, moved, changed,
or path-escaping source aborts generation. The inventory must not contain duplicate complex IDs or
duplicate normalized target identities.

A REVIEWED artifact is eligible only when its target name is already in the same canonical form used
by the runtime reviewed store (for example `59A`, not `059.9883A`) and `reviewed_at` includes an
explicit UTC offset. The builder never repairs either field while granting REVIEWED coverage.

## Build the handoff

From the repository root:

```bash
python scripts/build_review_handoff.py \
  --inventory ../tmp/owned_corpus_inventory_v1.json \
  --reviewed-dir artifacts/reviewed \
  --observations ../tmp/live_source_observations_20260904.json \
  --output ../tmp/review_coverage_handoff_20260904.json \
  --markdown-output ../tmp/review_coverage_handoff_20260904.md
```

The output deliberately contains no PDF URL, pre-signed query string, API key, reviewer name, or PDF
text. `backend_ready_targets` is the only reviewed allowlist. The other sections are diagnostics:

- `pending_human_review_targets`: PDF-backed tuples that still need source-locked human review
- `source_unavailable_targets`: tuples that cannot be reviewed until the PDF is obtained
- `conflicting_reviewed_targets`: exact tuples with multiple eligible REVIEWED artifacts; these are
  withheld until a human resolves the conflict
- `ineligible_reviewed_artifacts`: stale, malformed, failed, or incomplete local artifacts
- `orphaned_eligible_reviewed_artifacts`: trusted artifacts that do not match the supplied inventory
- `live_source_checks`: live response source identity compared with the owned corpus
- `inventory_source_checks`: relative source paths and source identities independently revalidated
  while this handoff was built

If two eligible REVIEWED artifacts claim the same exact target, the builder does not select the
newest file. It records `MULTIPLE_REVIEWED_ARTIFACTS_FOR_EXACT_TARGET`, excludes the tuple from
`backend_ready_targets`, and exposes the conflict in both JSON and Markdown output.
The live reviewed store follows the same fail-closed rule: zero or multiple exact matches both fall
back to automatic extraction/HOLD. A naive `reviewed_at` without a timezone is never selected.

An optional observation file has this shape:

```json
{
  "schema_version": "live_source_observations_v0.1",
  "observations": [
    {
      "observation_id": "backend-run-identifier-without-a-URL",
      "observed_at": "2026-09-04T00:00:00+09:00",
      "complex_id": "2026000372",
      "unit_type_id": "01",
      "unit_type_name": "059.9883A",
      "sale_price_manwon": 108650,
      "source_sha256": "64-lowercase-hex-characters",
      "source_page_count": 52
    }
  ]
}
```

Use `source_sha256_prefix` only when diagnosing a previously reported truncated digest. A prefix can
prove a mismatch when it conflicts with the locked source, but it cannot prove an exact match or
authorize `REVIEWED` coverage. Obtain the full raw `meta.source_sha256` before closing the incident.

## Live source status meanings

| Status | Meaning | Action |
| --- | --- | --- |
| `REQUEST_SOURCE_MATCH` | Full digest and page count match the requested complex source lock | Continue exact tuple matching |
| `REQUEST_SOURCE_MISMATCH` | Digest or page count conflicts with the requested complex | Keep HOLD; crawler/backend must correct the mapping |
| `REQUEST_SOURCE_INCOMPLETE` | Only part of the source identity was supplied | Obtain full digest and page count |
| `OWNED_SOURCE_IDENTIFIED` | An unlabelled observation maps to one owned PDF | Confirm the request tuple before use |
| `AMBIGUOUS_OWNED_SOURCE` | The supplied metadata is not unique | Obtain the full digest |
| `NOT_IN_OWNED_CORPUS` | No owned PDF matches the supplied source identity | Acquire and independently review the PDF |
| `REQUEST_SOURCE_UNAVAILABLE` | The inventory has no PDF source lock for the complex | Acquire the official PDF first |

## Read-only runtime sync check

Run:

```bash
python scripts/check_runtime_sync.py \
  --expected-provider ollama \
  --output ../tmp/runtime_sync_report_20260904.json
```

This checks the user service state, working directory, installed unit fingerprint, local `/health`
and `/ready`, the explicitly expected provider, and the SHA-256 fingerprint of every Python source
path and byte in the running package. It also records the latest committed runtime-source revision and
uncommitted runtime source changes as diagnostics. It does not read the environment file, process
environment, bearer token, or PDF URLs, and it does not restart anything.

Possible top-level states:

- `FAILED`: service, unit, path, health, readiness, or app-version check failed
- `RESTART_REQUIRED`: the running fingerprint is missing or differs from local Python source
- `EXACT_RUNTIME_MATCH`: source fingerprint, app version, and provider all match

`/health` freezes its fingerprint when the service imports the application. A later on-disk edit
therefore cannot make an old process report the new fingerprint. Git commit and service timestamps do
not grant a pass; they remain informational because commit time is not reliable deployment identity.

## Remaining external or human work

1. Resolve every `conflicting_reviewed_targets` entry before distributing an allowlist. Removing or
   superseding an artifact requires an explicit audit decision; generation will not choose one.
2. A human must inspect the exact PDF, target price row, payment terms, loan terms, and unit-specific
   additional costs before running the existing `review --confirm-source-reviewed` command for each
   tuple.
3. The crawler must provide official PDFs for HTML-only inventory items before they can enter the
   review queue.
4. For every live mismatch, backend/crawler owners must provide the request tuple plus the full raw AI
   `meta.source_sha256` and `meta.source_page_count`, then correct or explain the source mapping.
5. The AI service owner must restart the service after a runtime code change and rerun the sync check.
6. Backend may consume only `backend_ready_targets`, and must leave all other tuples
   `AUTO_EXTRACTED`/HOLD. Backend verdict logic remains the backend SSOT.
