# 보유 공고문 exact-target 자동 추출

`scripts/run_owned_corpus_extraction.py`는 인벤토리에 기록된 PDF 보유 주택형을
`(complex_id, unit_type_id, unit_type_name, sale_price_manwon)` 단위로 순차
분석합니다. 실행 중단 뒤 같은 명령을 다시 실행하면 source/target/schema/extractor
lock이 모두 일치하는 결과는 건너뜁니다.

```bash
python scripts/run_owned_corpus_extraction.py \
  --inventory artifacts/owned-corpus-inventory.json \
  --output-dir artifacts/owned-corpus-auto
```

일부 공고만 실행할 때는 `--complex-id`를 반복합니다.

```bash
python scripts/run_owned_corpus_extraction.py \
  --inventory artifacts/owned-corpus-inventory.json \
  --output-dir artifacts/owned-corpus-auto \
  --complex-id 2026000358 \
  --complex-id 2026000372
```

각 exact target마다 자동 추출 JSON과 `.review.md` 검수표를 만들고,
`run-report.json`에 완료·재개 건너뜀·실패를 원자적으로 기록합니다. 모델 서버를
한 번에 과부하시키지 않도록 병렬 호출하지 않습니다.

현재 lock과 다른 기존 자동 파일은 기본 실행에서 덮어쓰지 않습니다. 내용을 확인한
뒤 `--force`를 사용해야 하며, `REVIEWED` 파일과 스키마를 읽을 수 없는 파일은
`--force`로도 덮어쓸 수 없습니다.
중간에 종료되면 보고서는 `run_state=INTERRUPTED`로 남고, 같은 명령으로 완료 파일을
건너뛰며 재개할 수 있습니다.

## 안전 경계

- 이 배치는 `REVIEWED` 결과를 만들거나 덮어쓰지 않습니다.
- 원본 PDF SHA-256, 페이지 수, 정확 주택형, 분양가, schema/extractor 버전이
  일치하지 않으면 완료 결과로 인정하지 않습니다.
- 자동 결과가 실수로 `REVIEWED`를 반환하면 해당 작업을 실패 처리합니다.
- PDF가 없는 HTML-only 대상은 인벤토리에 남지만 추출 대상에서는 제외됩니다.
- 원문 금액이 정확히 10,000원 단위로 나누어지지 않으면 정수 `*_manwon`으로
  반올림하지 않고 `null`로 남겨 검수 대상임을 드러냅니다.
- 자동 추출 이후에는 `prepare-review-batch`로 초안과 체크리스트를 만들고,
  원본 PDF와 정확 주택형을 실제로 확인한 사람이 승인 매니페스트를 작성해야
  운영용 `REVIEWED` 결과가 생성됩니다.

특히 문서 공통 결과를 여러 주택형 초안에 재사용하더라도 발코니 확장비와 유상옵션
같은 추가비용의 주택형별 금액·포함 여부는 반드시 별도로 확인해야 합니다.
