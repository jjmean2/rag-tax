# 출처별 수집기 MVP 설계

## 1. 목적
- [실무 문서 소스 인벤토리](docs/document-source-inventory.md)를 실제 구현 가능한 수집기 단위로 분해한다.
- 초기 운영 목표를 `법령 + 국세청 + 기획재정부` 자동 수집에 맞춘다.
- 수동 골드셋 적재와 자동 수집 파이프라인을 동일한 저장 규격으로 통합한다.

## 2. MVP 범위

### 2.1 포함
- 국가법령정보센터: 법령/시행령/시행규칙 주요 세법군
- 국세청: 기본통칙, 예규/해석, 질의회신(공개 목록 기반)
- 기획재정부: 예규, 세법 개정 관련 공지/해설
- 수동 골드셋 업로드: JSON 파일 또는 정적 데이터 파일 기반

### 2.2 제외(후속)
- 조세심판원 전체 자동 수집
- 법원 판례 대량 자동 수집
- PDF/HWP 고정밀 파서
- 실시간 CDC 기반 반영

## 3. 산출물 정의
- 원문 저장: `documents`, `document_versions`, `document_sections`, `citations`
- 작업 이력: `ingestion_jobs` (기존 테이블 재사용)
- 실행 로그: 소스별 수집 통계(가져온 건수/변경 건수/실패 건수)

## 4. 수집기 구조

### 4.1 디렉터리 초안
```
app/ingestion/
  __init__.py
  models.py
  runner.py
  normalize.py
  writers.py
  connectors/
    __init__.py
    base.py
    law.go.kr.py
    nts.py
    moef.py
  manual/
    __init__.py
    load_goldset.py
```

### 4.2 공통 인터페이스
모든 수집기는 아래 단계를 구현한다.
1. `fetch_index(since)`
2. `fetch_detail(item)`
3. `normalize(raw)`
4. `emit_documents()`

필수 반환 모델(개념):
- `SourceDocument`
  - `source_system`, `source_id`, `title`, `doc_type`, `authority`, `canonical_url`
- `SourceVersion`
  - `version_label`, `publish_date`, `effective_from`, `effective_to`, `status`, `raw_text`, `metadata`
- `SourceSection`
  - `section_type`, `section_ref`, `heading`, `content`, `order_no`, `metadata`

## 5. 표준화 규칙

### 5.1 식별자 규칙
- `documents.id`: `{source_system}:{source_id}`
- `document_versions.id`: `{documents.id}:{version_key}`
- `document_sections.id`: `{version_id}:{section_ref_or_seq}`

### 5.2 문서 유형 매핑
- `statute`: 법령, 시행령, 시행규칙
- `ruling`: 예규, 유권해석, 질의회신, 통칙
- `case`: 심판례, 판례

### 5.3 기관 코드 매핑
- `moef`: 기획재정부
- `nts`: 국세청
- `scourt`: 법원
- `klri`: 국가법령정보센터(법령 시스템 코드)

## 6. 소스별 수집 설계

### 6.1 국가법령정보센터 수집기
- 입력
  - 대상 법률 코드 목록(법인세법, 국세기본법 등)
- 출력
  - 법/시행령/시행규칙 문서와 조문 단위 섹션
- 구현 포인트
  - 개정 이력 기반 버전 생성
  - 조/항/호 파싱 실패 시 조문 텍스트를 최소 단위로 저장
- 실패 처리
  - 특정 조문 파싱 실패는 경고로 기록하고 문서 전체는 저장

### 6.2 국세청 수집기
- 입력
  - 게시판/목록 URL, 최근 N일 또는 마지막 수집 시점
- 출력
  - 문서번호/작성일/본문/첨부 메타데이터 포함 문서
- 구현 포인트
  - 목록에서 `source_id` 안정 추출
  - 본문 정규화 시 불필요한 네비게이션 텍스트 제거
  - 질의요지/회신요지를 section으로 분리 시도
- 실패 처리
  - 상세 본문 파싱 실패 문서는 검수 대상 마킹 후 저장 보류

### 6.3 기획재정부 수집기
- 입력
  - 공지/보도 목록 URL, 카테고리 필터
- 출력
  - 예규/개정해설 문서 + 첨부파일 메타데이터
- 구현 포인트
  - HTML 본문 우선, 첨부파일은 링크와 해시만 우선 저장
  - 개정자료는 제목 규칙 기반 태깅(`issue_tags`)
- 실패 처리
  - 첨부 다운로드 실패는 재시도 큐에 적재

## 7. 실행 흐름
1. 러너 시작: `source`, `mode(full|incremental)`, `since` 입력
2. `ingestion_jobs`에 `running` 상태 기록
3. 소스 수집기 `fetch_index` 실행
4. 각 아이템 상세 수집 + 정규화
5. 해시 비교 후 변경 문서만 upsert
6. 성공/실패 집계 후 `ingestion_jobs` 업데이트
7. 변경 섹션이 있으면 임베딩 대상 큐에 추가

## 8. 증분 업데이트 정책
- 기본 키: `(source_system, source_id)`
- 변경 감지: `hash_sha256` 비교
- 배치 정책
  - 법령: 일 1회
  - 국세청/기재부: 일 1회
- 수동 재수집
  - `--full` 옵션 시 전체 재수집

## 9. 품질 게이트(MVP 버전)
- 필수 필드 누락 검사
  - `source_id`, `title`, `doc_type`, `raw_text`
- 최소 본문 길이 검사
  - 너무 짧은 본문은 경고
- 날짜 형식 검사
  - 파싱 불가 시 메타데이터 경고 저장
- 중복 섹션 검사
  - 동일 `content` 반복률이 높으면 경고

통과 정책
- 치명 오류: 저장 중단
- 경고: 저장은 진행, `metadata_json`에 경고 코드 기록

## 10. CLI 초안

### 10.1 실행 명령
```bash
uv run python -m app.ingestion.runner --source law --mode incremental
uv run python -m app.ingestion.runner --source nts --mode incremental --since 2026-01-01
uv run python -m app.ingestion.runner --source moef --mode full
uv run python -m app.ingestion.manual.load_goldset --file data/goldset.json
```

### 10.2 옵션
- `--source`: `law|nts|moef|all`
- `--mode`: `full|incremental`
- `--since`: `YYYY-MM-DD`
- `--limit`: 테스트 수집 건수 제한
- `--dry-run`: DB 반영 없이 파싱/검증만 수행

## 11. 단계별 구현 계획(4주)
1. 1주차
  - 공통 모델/러너/업서트 라이터 구현
  - 수동 골드셋 로더 구현
2. 2주차
  - 법령 수집기 구현
  - 기본 검증 게이트 및 로그 추가
3. 3주차
  - 국세청 수집기 구현
  - 증분/해시 비교 안정화
4. 4주차
  - 기획재정부 수집기 구현
  - 운영 태스크(make target) 및 README 실행 가이드 추가

## 12. 리스크 및 대응
- 소스 HTML 구조 변경
  - 대응: 소스별 파서 버전 필드 추가, 실패율 알림
- 문서번호 파싱 불안정
  - 대응: fallback 식별자 생성 규칙과 수동 매핑 테이블 운영
- 첨부파일 포맷 다양성
  - 대응: MVP는 첨부 메타데이터만 저장하고 본문 파싱은 후속 단계로 분리
- 과도한 재수집 비용
  - 대응: 증분 기본, full 모드는 수동 실행으로 제한

## 13. 완료 기준(Definition of Done)
- `law`, `nts`, `moef` 3개 수집기가 `incremental` 모드로 최소 1회 성공
- 수집 결과가 기존 스키마(`documents`, `document_versions`, `document_sections`)에 적재
- `ingestion_jobs`에서 실행 상태와 건수 확인 가능
- 수동 골드셋 적재 경로와 자동 수집 경로가 동일 저장 규격 사용
- 수집 실패 케이스가 로그와 메타데이터 경고로 추적 가능