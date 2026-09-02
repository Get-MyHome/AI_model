# Get-MyHome AI

Get-MyHome은 청약 당첨 이후 계약금·중도금·잔금 사이에서 발생하는 **최초 자금 단절 시점과 원인**을 진단합니다. 이 저장소는 공공 API에 없는 공고문 금융조건을 PDF에서 찾아 페이지 근거와 함께 구조화하는 AI 계층입니다.

> 중도금 납부비율 60%가 개인의 대출 승인비율 60%를 뜻하지는 않습니다.

## 핵심 기능

- 계약금·중도금·잔금의 비율, 금액, 회차와 납부일 추출
- 중도금 대출 알선 상태, 알선 상한, 별도 조달 비율과 이자 방식 추출
- 추가비용, 입주 시 대출 상환·전환 조건과 금융 위험조항 분류
- 모든 판단 필드에 PDF 페이지와 원문 근거 연결
- 누락·충돌·불확실한 정보의 `null + HOLD` 처리
- 자동 검증과 사람 검수를 분리한 `AUTO_EXTRACTED → REVIEWED` 흐름

## 시스템 경계

```text
crawler                AI_model                         backend
공고문 수집·PDF 저장  → 문서 사실·근거 추출 및 검수  → 사용자 조건 기반 자금판정
```

- AI는 PDF에 적힌 사실과 불확실성만 구조화합니다.
- backend는 검수된 결과와 사용자 조건·금융규칙을 결합해 최종 부족액과 상태를 계산합니다.
- LLM은 대출 승인 여부, 사용자 부족액 또는 최종 금융 조언을 생성하지 않습니다.
- 모든 비율은 총 분양가 대비 `0~1` 값이며, 알선 상한은 개인 승인액이 아닙니다.
- 원문에 없는 값은 0으로 바꾸거나 추정하지 않습니다.
- 안내문과 요약문은 검증값 기반 고정 템플릿입니다.

## 설치와 실행

Python 3.12, `pdftotext`와 Ollama가 필요합니다.

```bash
python -m pip install -e '.[dev]'
ollama pull qwen3.5:9b
OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NUM_PARALLEL=1 ollama serve
cp .env.example .env
```

로컬 PDF 분석:

```bash
get-myhome-ai analyze-file \
  --complex-id 2026000372 \
  --pdf /path/to/announcement.pdf \
  --unit-type-name 59A \
  --sale-price-manwon 108650
```

자동 추출 JSON과 검수표는 `artifacts/auto/`에 생성됩니다. PDF 원문을 대조해 승인된 `REVIEWED` 결과만 사용자 자금판정에 사용할 수 있습니다.

## HTTP API

```bash
get-myhome-ai serve --host 0.0.0.0 --port 9000
```

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/api/analyze` | PDF 금융조건 분석 |
| `POST` | `/api/funding-stress` | REVIEWED 검수본 기반 선택형 스트레스 분석 |
| `GET` | `/health` | 프로세스 상태 확인 |
| `GET` | `/ready` | 실행 준비 상태 확인 |

외부 분석 요청은 `Authorization: Bearer <AI_API_KEY>` 헤더를 사용하며 실제 키는 저장소에 커밋하지 않습니다.

`POST /api/analyze` 요청 예시:

```json
{
  "complex_id": "2026000372",
  "pdf_url": "https://example.com/fresh-presigned-url",
  "unit_type_id": "01",
  "unit_type_name": "059.9883A",
  "sale_price_manwon": 108650
}
```

문서 공통 분석에는 `complex_id`, `pdf_url`만 필요합니다. 주택형을 지정할 때는 나머지 세 필드를 모두 보내야 하며 금액 단위는 만 원입니다. 응답은 납부 일정, 중도금 대출조건, 추가비용, 위험조항, HOLD, 근거와 검수 상태를 포함합니다.

## 검증

Qwen3.5 독립 재실행은 공고문 24건의 범위 한정 핵심 라벨 260개를, 결정론적 위험·상환 규칙은 공고문 27건의 참조 라벨 189개를 대조했습니다. 이 결과는 명시된 평가 범위에만 적용되며 전체 필드 정확도 또는 근거의 의미적 정확도 100%를 뜻하지 않습니다.

자세한 범위와 한계는 [전수 검증 보고서](docs/FULL_27_AUDIT_2026-09-02.md)를 참고하세요.

```bash
ruff check .
python -m pytest
python -m compileall -q src tests
python scripts/evaluate_risk_settlement.py
```

## 문서

- [아키텍처](docs/ARCHITECTURE.md)
- [추출 명세 v0.3](docs/EXTRACTION_SPEC_v0.3.md)
- [HOLD 코드](docs/HOLD_CODES.md)
- [골든셋](docs/GOLDEN_SET_v0.3.md)
- [스트레스 분석 API](docs/FUNDING_STRESS_API.md)
