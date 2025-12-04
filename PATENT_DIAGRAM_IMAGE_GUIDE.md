# 특허 검색 플로우 다이어그램 이미지 변환 가이드

특허 검색 플로우 다이어그램을 이미지(PNG) 파일로 변환하는 방법입니다.

## 빠른 시작

### 방법 1: mermaid.ink API (간단, 인터넷 필요)

터미널에서 다음 명령어 실행:

```bash
python convert_patent_diagram_to_images.py patent_search_flow_diagram.md
```

변환 방법 선택 시 `1`을 입력하거나 Enter를 누르세요.

### 방법 2: Playwright (고품질, 브라우저 필요)

먼저 Playwright 브라우저를 설치하세요:

```bash
pip install playwright
playwright install chromium
```

그 다음 스크립트 실행:

```bash
python convert_patent_diagram_to_images.py patent_search_flow_diagram.md
```

변환 방법 선택 시 `2`를 입력하세요.

## 결과

변환된 이미지는 `patent_flow_images/` 폴더에 저장됩니다:

```
patent_flow_images/
├── 전체_특허_검색_플로우.png
├── 특허_검색_워크플로우_내부_플로우.png
├── 특허_상세_보기_플로우_PDF_다운로드_및_임베딩.png
├── 주요_노드별_상세_플로우.png
├── check_db_node_벡터DB_검색.png
├── search_kipris_node_KIPRIS_웹_검색.png
├── download_pdf_node_PDF_다운로드_및_임베딩.png
└── rag_answer_node_RAG_답변_생성.png
```

## 스크립트 직접 실행 (비대화형 모드)

대화형 입력 없이 바로 실행하려면:

```bash
python convert_patent_diagram_to_images.py patent_search_flow_diagram.md 1
```

또는 Playwright 방식:

```bash
python convert_patent_diagram_to_images.py patent_search_flow_diagram.md 2
```

## 두 방법의 차이점

### mermaid.ink API (방법 1)
- ✅ **장점**: 빠르고 간단, 추가 설치 불필요
- ❌ **단점**: 인터넷 연결 필요, API 의존

### Playwright (방법 2)
- ✅ **장점**: 고품질, 오프라인 가능, 완전한 제어
- ❌ **단점**: 브라우저 설치 필요, 첫 실행 시 시간 소요

## 문제 해결

### "Playwright가 설치되지 않았습니다" 오류

```bash
pip install playwright
playwright install chromium
```

### 이미지 다운로드 실패

- 인터넷 연결 확인
- mermaid.ink API가 일시적으로 다운되었을 수 있음 (잠시 후 재시도)
- 방법 2 (Playwright) 사용 권장

### 한글 파일명 문제

파일명에서 특수 문자는 자동으로 언더스코어(`_`)로 변환됩니다.

## 참고

- 변환된 이미지는 PNG 형식입니다
- 원본 Mermaid 다이어그램의 스타일과 색상이 그대로 유지됩니다
- 매우 복잡한 다이어그램은 렌더링에 시간이 걸릴 수 있습니다

