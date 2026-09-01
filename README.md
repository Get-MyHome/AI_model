# Get-MyHome AI

청약 공고문 PDF에서 계약금·중도금·잔금과 중도금 대출 조건을 페이지 근거와 함께 구조화하는 배치 우선 파이프라인입니다.

## 역할 경계

1. `crawler`가 청약홈에서 공고문을 수집하고 S3에 저장합니다.
2. 이 저장소는 crawler가 준 `complex_id`와 짧게 유효한 `pdf_url`을 즉시 수령하거나, 개발 중에는 로컬 PDF를 입력받습니다.
3. AI는 문서 사실만 추출합니다. 자격·한도·부족액 판정은 backend의 고정 규칙이 수행합니다.
4. 자동 검증을 통과해도 `AUTO_EXTRACTED`이며, 사람 검수 후 `REVIEWED`가 된 결과만 backend 적재 대상입니다.

청약홈 페이지를 찾거나 크롤링하는 코드는 이 저장소에 두지 않습니다.

## 안전 원칙

- 모든 비율은 총 분양가 대비 0~1 값입니다.
- 공고에 비율 대신 정액이 있으면 임의 환산하지 않고 정액으로 보존합니다.
- 없는 값은 0이 아니라 `null`과 HOLD로 표현합니다.
- LLM은 요약·조언·판정을 만들지 않습니다. 안내문과 요약은 검증값 기반 고정 템플릿입니다.
- 추출값은 PDF 페이지와 실제 원문 근거가 있어야 합니다.
- 발코니 확장비 등 선택비용은 사용자 선택 전 자동 합산하지 않습니다.

## 먼저 읽을 문서

- `docs/ARCHITECTURE.md`: crawler·AI·backend 책임 경계와 처리 흐름
- `docs/EXTRACTION_SPEC_v0.3.md`: 정본 요청·응답 규격
- `docs/HOLD_CODES.md`: 고정 안내문과 다음 행동
- `docs/GOLDEN_SET_v0.3.md`: 실제 공고문 3건의 수작업 정답
- `docs/BACKEND_COMPATIBILITY.md`: 현재 Java DTO와 정본 계약의 차이

## 현재 개발 방식

배치 명령이 본체이고 HTTP API는 동일 파이프라인을 호출하는 얇은 선택 계층입니다.

- 개발·평가: 로컬 PDF 입력
- 운영 연동: crawler가 만든 S3 pre-signed URL 입력
- 자동 추출본: `artifacts/auto/`
- 사람 검수 완료본: `artifacts/reviewed/`

## 설치

Python 3.12와 `pdftotext`(Ubuntu 패키지 `poppler-utils`)가 필요합니다.

```bash
python -m pip install -e '.[dev]'
```

OpenAI를 사용할 때만 `.env.example`을 `.env`로 복사하고 API 키를 넣습니다. 키가 없으면 운영 모드가 fixture로 자동 전환되지 않습니다.

## 로컬 PDF 배치 분석

개발 중에는 crawler를 기다리지 않고 이미 확보한 PDF를 같은 파이프라인에 넣습니다.

```bash
get-myhome-ai analyze-file \
  --complex-id 2026000372 \
  --pdf /path/to/2026000372_7.pdf \
  --unit-type-name 59A \
  --sale-price-manwon 108650
```

결과는 기본적으로 `artifacts/auto/{complex_id}.json`, 검수표는 같은 위치의 `*.review.md`에 저장됩니다.

## crawler URL 분석

운영 입력은 crawler가 만든 10분짜리 S3 pre-signed URL입니다. AI는 URL을 받자마자 PDF만 수령하며 청약홈을 직접 크롤링하지 않습니다.

```bash
get-myhome-ai analyze-url \
  --complex-id 2026000372 \
  --pdf-url 'https://crawler-bucket.example/temporary-signed-url' \
  --unit-type-name 59A \
  --sale-price-manwon 108650
```

## 사람 검수

자동 검증 통과와 사람 검수 완료를 구분합니다. PDF 원문을 직접 대조한 후에만 다음 명령을 실행합니다.

```bash
get-myhome-ai review \
  --input artifacts/auto/2026000372.json \
  --output artifacts/reviewed/2026000372.json \
  --reviewer 안지홍 \
  --confirm-source-reviewed
```

## 실제 PDF 골든셋 회귀 테스트

PDF는 저장소에 커밋하지 않습니다. 세 파일을 별도 디렉터리에 둔 뒤 실행합니다.

```bash
get-myhome-ai --provider fixture evaluate \
  --pdf-dir /path/to/golden-pdfs \
  --output artifacts/evaluation/golden-fixture-report.json
```

fixture 평가는 PDF 수령·페이지 추출·후보 선택·고정 검증·근거 연결을 재현하는 테스트이지 LLM 정확도 측정이 아닙니다. 실제 모델 평가는 `AI_PROVIDER=openai`와 API 키로 같은 명령을 실행해 별도로 측정합니다.

## 선택형 HTTP API

배치 코어가 본체이며, backend가 동기 호출을 원할 때만 얇은 API를 띄웁니다.

```bash
get-myhome-ai serve --host 0.0.0.0 --port 9000
```

- `POST /api/analyze`: v0.3 정본 응답
- `POST /api/analyze/legacy`: 현재 Java DTO 임시 호환 응답
- `GET /health`: 프로세스 생존 확인
- `GET /ready`: `pdftotext`·임시 디렉터리·Provider 설정 확인

현재 backend가 URL 자체에 POST하므로 서버 방식으로 연동할 때는 `AI_SERVER_URL=http://ai-host:9000/api/analyze`처럼 경로까지 넣어야 합니다. 정본 계약과 현재 Java DTO의 손실 문제는 `docs/BACKEND_COMPATIBILITY.md`를 확인하세요.

## 검증

```bash
ruff check .
python -m pytest
python -m compileall -q src tests
```

실제 OpenAI 호출은 API 키가 없어 자동 테스트에 포함하지 않습니다. Structured Outputs도 값의 사실성을 보장하지 않으므로, 모델 출력 뒤의 고정 검증과 사람 검수를 생략하면 안 됩니다.
