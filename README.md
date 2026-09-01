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

설치·실행 명령은 구현과 검증이 끝난 뒤 이 README의 사용법 절에 고정합니다.

