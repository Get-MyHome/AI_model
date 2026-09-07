# 신규 원본 준비와 전달 경계

백엔드가 `/api/analyze`로 전달한 PDF가 준비 대상입니다. AI 서버는 청약홈을
별도로 크롤링하지 않습니다. 전달되지 않은 PDF의 존재나 최신성은 보장하지 않습니다.

기존 검수본의 원본·주택형·분양가가 일치하면 검수본을 반환합니다. 새 원본은
Qwen 분석 결과와 원본을 캡처하고 검수 초안을 준비합니다. 자동 준비가 사람의
원문 검수를 의미하지는 않으며 `REVIEWED` 승인을 자동으로 생성하지 않습니다.

자동 분석 결과는 기존 API로 반환할 수 있지만 최종 자금 계산은 기존의
`REVIEWED + validation.passed=true` 조건과 항목별 HOLD를 그대로 따릅니다.

## 자동화 범위

기존 `/api/analyze`가 수신·분석·캡처한 자료를 `get-myhome-review-inbox.timer`가
약 1분 간격으로 검사한다. 요청별 PDF 해시·자동 결과 해시·주택형·분양가를 대조한 후
검수 JSON 초안, 체크리스트, PENDING 승인 양식을 만든다. 같은 자료는 재생성하지 않고
편집된 초안은 보존한다. 원본/분석 버전 변경은 별도 작업으로 분리하며, 일부 잘못된
요청이 다른 요청의 준비를 막지 않는다.

타이머 자체는 크롤링·추가 Qwen 추론·사람 검수를 수행하지 않는다. 의미 오류의 원문
대조·교정이 매분 자동 실행된다는 뜻도 아니다. 분석 실패 재시도는 기존 API 호출
정책을 따른다. 서버와 사용자 서비스 관리자가 가동 중이어야 한다.

## 로컬 준비 자료

- `.local/review-capture/`: 받은 PDF·URL 없는 대상 메타데이터·자동 분석 원본.
- `.local/prepared-inbox/status.json`: 수신 대상별 준비 상태.
- `.local/prepared-inbox/jobs/<작업ID>/`: 수정 가능한 초안·체크리스트·승인 양식.
- `.local/prepared-inbox/source-audit-20260907.json`: 이번에 교정한 10개 조합의 파일 경로·SHA·검증 결과.
- `.local/runtime/reviewed-v023-anjihong-20260904/`: 기존 운영 승인본. 변경하지 않았다.

| 상태 | 의미 |
|---|---|
| REVIEWED_AVAILABLE | 정확한 원본·대상·버전에 맞는 운영 검수본이 있음 |
| DRAFT_PREPARED | 검수 초안 파일 준비. 검증 통과나 사람 승인 완료와 다름 |
| WAITING_FOR_ANALYSIS | 원본은 수신했지만 완성된 자동 결과가 없음 |
| VERSION_BLOCKED | 자동 결과가 현재 승인 기준 버전과 다름 |
| PREPARATION_BLOCKED / INVALID_CAPTURE_OR_PREPARATION_FAILED | 자료 누락·불일치·손상 등으로 준비 보류 |

## 2026-09-07 원문 대조 결과

수신함에 기존 승인 대상 1개와 신규 PDF 7개·주택형 조합 10개가 있었다.
아래 가격은 요청된 주택형의 가격행으로, 모든 동·호·유상옵션의 검증을 의미하지 않는다.

| 공고번호 | unit_type_id / unit_type_name | 가격(만원) | 교정 후 자동 검증 |
|---|---|---:|---|
| 2026000202 | 01 / 084.2259A | 59600 | 통과·사람 승인 대기 |
| 2026000399 | 01 / 059.9442A | 91000 | 통과·사람 승인 대기 |
| 2026000399 | 02 / 059.9442B | 89200 | 통과·사람 승인 대기 |
| 2026000399 | 03 / 059.9293C | 86200 | 통과·사람 승인 대기 |
| 2026000399 | 04 / 074.9610 | 107500 | 통과·사람 승인 대기 |
| 2026000394 | 01 / 084.6536A | 51965 | 보류: 회차 금액 만 원 미만 정밀도 |
| 2026000401 | 01 / 059.8041A | 22100 | 통과하나 10/0/90 구조의 엔진 적용 별도 검증 필요 |
| 2026000403 | 01 / 059.8300A | 57600 | 통과·알선비율 미기재 HOLD 유지 |
| 2026000414 | 11 / 077.4100C | 65453 | 보류: 별도 기금융자 7500만원·금액 정밀도 |
| 2026000419 | 01 / 084.9730A | 87000 | 통과·사람 승인 대기 |

타 주택형 옵션 혼입 제거, 발코니 비용 명칭·무상/선택 여부·지급일정 등을 교정했다.
정확한 PDF로 `prepare_review_draft`를 재실행하여 8개 통과, 2개 검증 오류 유지 및
재실행 동일성을 확인했다. **새 REVIEWED 등록은 0개이며 기존 154개는 유지**한다.
validation 통과는 전체 금융조건 확정이나 개인 대출 승인이 아니다.

원문 대조 기록: `../tmp/captured-review-20260907/audit-*.md`.
교정본 승인 양식은 각 작업의 `review-approval-after-audit.template.json`이며,
현재 해시만 반영하고 검수자·승인 체크는 비워 두었다.

## 운영

```bash
python -m get_myhome_ai.prepare_inbox \
  --capture-dir .local/review-capture \
  --output-dir .local/prepared-inbox
```

운영과 동일한 `REVIEWED_ARTIFACT_DIR`·schema/extractor 설정을 사용해야 한다.
`deploy/get-myhome-review-inbox.{service,timer}`는 이 서버의 경로를 사용한 설치 예시다.
API key와 pre-signed URL을 상태 파일/로그에 기록하지 않으며 준비 자료는 자동 삭제하지 않는다.

- 확인: `systemctl --user list-timers get-myhome-review-inbox.timer`
- 중지: `systemctl --user disable --now get-myhome-review-inbox.timer`

타이머를 중지해도 API 분석과 기존 검수본 반환에는 영향이 없다.
백엔드는 기존 API를 그대로 호출한다. 새 교정본의 최종 자금판정 사용은
`BATCH_REVIEW.md`의 실제 사람 검수·승인·운영 등록 후 가능하다.
