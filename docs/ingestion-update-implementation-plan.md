# 문서 수집 및 DB 구축 구현 계획서

## 1. 목적과 원칙
- 기본 운영은 자동 수집/자동 업데이트를 사용한다.
- 자동 결과는 검증 게이트를 통과한 경우에만 검색 인덱스에 반영한다.
- 자동 수집 누락 문서는 수동 업로드로 보강하고, 동일한 검증 게이트를 적용한다.
- 모든 반영은 추적 가능해야 하며 롤백 가능해야 한다.

## 2. 목표 아키텍처
- Source Connectors
  - API 수집기, 파일 수집기, 크롤러(허용 범위 내)
- Ingestion Orchestrator
  - 스케줄 실행, 증분 판단, 작업 상태 추적
- Normalizer/Parser
  - 문서유형별 파싱(법령/해석/판례)
- Validation Engine
  - 스키마 검증 + 도메인 규칙 검증 + 품질 점수화
- Review Queue
  - 자동 검증 실패/저신뢰 문서의 수동 검수 큐
- Publish Manager
  - 원문 DB 반영 -> 인덱스 반영 -> 버전 태깅

## 3. 운영 모드(혼합형)

### 3.1 자동 수집 모드 (기본)
- 대상
  - 정형 API 제공 소스
  - 규칙 기반 파싱이 안정적인 소스
- 처리
  1. 증분 수집(최신 문서만)
  2. 변경 감지(hash/source revision)
  3. 정규화 및 섹션 분해
  4. 자동 검증 수행
  5. 통과 시 DB/인덱스 자동 반영
  6. 실패 시 검수 큐 전송

### 3.2 수동 업로드 모드 (보강)
- 대상
  - 자동 수집 미지원 문서
  - 자동 파싱 실패 문서
  - 긴급 반영이 필요한 중요 문서
- 처리
  1. 운영자가 원본 파일(PDF/HTML/TXT) 업로드
  2. 최소 메타데이터 입력(문서유형, 기관, 일자, 식별자)
  3. 파서 선택(자동 추천 + 수동 지정)
  4. 미리보기/파싱 결과 확인
  5. 검증 실행
  6. 승인 후 DB/인덱스 반영

## 4. 데이터 모델 확장(구현 필요)
기존 [docs/data-schema.md](docs/data-schema.md)의 `ingestion_jobs`를 확장하고, 아래 테이블을 추가한다.

### 4.1 source_connectors
- 목적: 수집 소스 설정/주기/활성 상태 관리
- 주요 필드
  - id, source_system, connector_type(api|crawler|file), schedule_cron, is_active
  - auth_config_json, rate_limit_config_json

### 4.2 ingestion_runs
- 목적: 작업 실행 단위 추적
- 주요 필드
  - id, connector_id, run_type(full|incremental|manual_upload)
  - started_at, finished_at, status, fetched_count, parsed_count, published_count

### 4.3 ingestion_artifacts
- 목적: 원본 파일 저장소 메타데이터
- 주요 필드
  - id, run_id, file_uri, file_hash, mime_type, size_bytes

### 4.4 validation_results
- 목적: 검증 규칙별 결과 저장
- 주요 필드
  - id, run_id, document_version_id, rule_code, severity, passed, message

### 4.5 review_tasks
- 목적: 수동 검증 업무 큐
- 주요 필드
  - id, document_version_id, reason_code, priority, assignee, status
  - decision(approve|reject|needs_fix), decided_at

### 4.6 publish_events
- 목적: 반영 이력 및 롤백 지점 기록
- 주요 필드
  - id, document_version_id, target(db|keyword_index|vector_index), action(upsert|rollback), applied_at

## 5. 검증 게이트 설계

### 5.1 1차 스키마 검증
- 필수 필드 존재 여부
- 날짜/식별자 포맷 정합성
- 원문 공백/깨짐 여부

### 5.2 2차 도메인 검증
- 법령: 조/항/호 파싱 유효성
- 해석: 문서번호/질의요지/결론 구간 검출
- 판례: 사건번호/판시사항/결론 구간 검출

### 5.3 3차 품질 검증
- 섹션 분할 품질(과도한 단문/과도한 장문 탐지)
- 중복/근접중복 탐지
- 인덱싱 전 토큰 길이 임계치 점검

### 5.4 검증 결과에 따른 라우팅
- 전부 통과: 자동 반영
- 경미 경고: 자동 반영 + 사후 샘플 검수
- 치명 오류/저신뢰: 검수 큐 필수

## 6. 업데이트 정책(SLA)

### 6.1 문서유형별 기본 주기
- 법령: 일 1회 증분 + 중요 개정 이벤트 즉시 재수집
- 행정해석: 일 1회 증분
- 판례/심판례: 주 3회 증분
- 전체 정합성 점검: 주 1회
- 전체 재수집(백필/누락 보정): 분기 1회

### 6.2 반영 SLA
- 일반 문서: 수집 후 24시간 내 반영
- 중요 문서(우선순위 High): 4시간 내 반영 목표

## 7. 자동/수동 품질운영 전략
- 기본: 자동 파이프라인으로 커버리지 확보
- 보강: 신규 반영분 10~20% 랜덤 샘플 수동 검수
- 집중: 오류 다발 소스는 100% 수동 승인 모드로 임시 전환
- 회귀: 파서 변경 시 골든셋 재검증 후 재배포

## 8. 수동 업로드 기능 요구사항

### 8.1 사용자 기능
- 파일 업로드(다중 파일)
- 문서유형 선택 및 메타데이터 입력
- 파싱 미리보기(원문 vs 정규화 텍스트)
- 검증 결과 확인(규칙별 pass/fail)
- 승인/반려/수정요청 처리

### 8.2 시스템 기능
- 동일 원문 hash 중복 경고
- 기존 문서와 버전 병합 제안
- 반영 전 스테이징 저장
- 승인 시에만 프로덕션 인덱스 반영

## 9. 구현 단계(12주)
1. 1-2주: 소스 카탈로그, connector 프레임워크, ingestion_runs 구현
2. 3-4주: 문서유형별 파서/정규화, 변경 감지, artifact 저장
3. 5-6주: 검증 엔진(rule registry), 검수 큐(review_tasks), 운영 화면 기본
4. 7-8주: 자동 반영/롤백(publish_events), 인덱스 동기화 파이프라인
5. 9-10주: 수동 업로드 UI/API, 승인 워크플로우, 권한 관리
6. 11주: 샘플 검수 운영, SLA 모니터링 대시보드
7. 12주: 운영 안정화, 장애 시나리오 훈련, 배포

## 10. API 초안

### 10.1 자동 수집 제어
- POST /api/ingestion/connectors
- POST /api/ingestion/runs/trigger
- GET /api/ingestion/runs/{id}

### 10.2 수동 업로드
- POST /api/manual-ingestion/uploads
- POST /api/manual-ingestion/parse-preview/{uploadId}
- POST /api/manual-ingestion/validate/{uploadId}
- POST /api/manual-ingestion/submit/{uploadId}

### 10.3 검수/승인
- GET /api/review-tasks
- POST /api/review-tasks/{id}/approve
- POST /api/review-tasks/{id}/reject

## 11. 모니터링/알림
- 지표
  - 수집 성공률, 파싱 성공률, 검증 통과율, 반영 지연시간
  - 소스별 오류율, 문서유형별 누락률
- 알림
  - SLA 초과, 치명 오류 급증, 특정 connector 연속 실패

## 12. 리스크와 대응
- 소스 구조 변경으로 파싱 실패
  - 대응: 소스별 parser versioning + 빠른 핫픽스 경로
- 자동 수집 누락 장기화
  - 대응: 월간 누락 탐지 리포트 + 수동 업로드 캠페인
- 잘못된 자동 반영
  - 대응: publish_events 기반 즉시 롤백

## 13. 완료 기준(Definition of Done)
- 자동 수집 커버리지 목표 달성(핵심 소스 80% 이상)
- 수동 업로드에서 승인 후 반영까지 end-to-end 동작
- 검증 실패 문서의 검수 큐 전환 자동화
- 문서 반영 이력/롤백 이력 추적 가능
- 주기 실행 및 SLA 모니터링 대시보드 운영 가능
