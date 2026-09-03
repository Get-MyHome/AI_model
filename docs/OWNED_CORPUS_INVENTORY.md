# Owned-corpus coverage inventory

`scripts/build_owned_corpus_inventory.py` turns a directory of saved ApplyHome
detail HTML files and announcement PDFs into a deterministic coverage manifest.
It is an inventory tool only: it neither calls an extraction model nor creates,
approves, or stores a reviewed analysis.

## Input layout

The directory must contain one `{complex_id}_detail.html` per announcement. A
saved announcement PDF is optional. For ApplyHome attachments its exact filename
is `{complex_id}_{atchmnflSn}.pdf`, where `atchmnflSn` comes from the detail page's
`getAtchmnfl.do` link.

The generator cross-checks two tables in each detail page:

- `입주자모집공고 공급대상` supplies `complex_id`, `unit_type_id`, and
  `unit_type_name`;
- `공급금액, 2순위 청약금` supplies the highest `sale_price_manwon` shown by
  ApplyHome.

Every price row must match exactly one supply/model row. Ambiguous IDs, duplicate
rows, attachment mismatches, or malformed tables stop the run instead of producing
guessed targets.

## Generate JSON

```bash
python scripts/build_owned_corpus_inventory.py \
  --source-dir /path/to/gonggo \
  --output artifacts/owned-corpus-inventory.json
```

Omit `--output` to write JSON to standard output. PDF page counts come from
Poppler's `pdfinfo`. The output contains no timestamp, uses stable sorting, and
can therefore be byte-compared across repeated runs over the same directory.

## Output contract

Schema `owned_corpus_inventory_v1` contains:

- `summary`: HTML/PDF document counts and PDF-backed/HTML-only tuple counts;
- `documents`: source filenames, detail HTML hash, PDF availability, PDF SHA-256,
  physical page count, and all unit tuples grouped by announcement;
- `targets`: a flat, sorted list of every exact
  `(complex_id, unit_type_id, unit_type_name, sale_price_manwon)` tuple with its
  source availability, hash, and page count.

Paths inside `documents` and `targets` are relative to `source_directory`. Missing
PDFs are represented explicitly with `pdf_available=false` and null PDF metadata.

This manifest is not evidence of human review. In particular, it has no
`review_status` field and must not be used as an approval artifact. Each PDF-backed
tuple still needs source-locked extraction and independent human verification
before any reviewed-only financial calculation can consume it.
