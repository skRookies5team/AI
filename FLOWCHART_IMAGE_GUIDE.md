# Mermaid 다이어그램을 이미지로 변환하기

플로우차트를 이미지 파일(PNG)로 변환하는 방법입니다.

## 빠른 시작

### 방법 1: mermaid.ink API (간단, 인터넷 필요)

```bash
python convert_mermaid_to_images.py project_flowchart.md
```

변환 방법 선택 시 `1`을 입력하거나 Enter를 누르세요.

### 방법 2: Playwright (고품질, 브라우저 필요)

먼저 Playwright 브라우저를 설치하세요:

```bash
playwright install chromium
```

그 다음 스크립트 실행:

```bash
python convert_mermaid_to_images.py project_flowchart.md
```

변환 방법 선택 시 `2`를 입력하세요.

## 결과

변환된 이미지는 `flowchart_images/` 폴더에 저장됩니다:

```
flowchart_images/
├── 전체_시스템_플로우.png
├── 시나리오_1_연차_신청_Schedule_Leave.png
├── 시나리오_2_회의실_예약_Schedule_Meeting.png
├── 시나리오_3_문서_작성_Document_Generate.png
├── 시나리오_4_메뉴얼_검색_QA_Manual_RAG.png
├── 시나리오_5_식단_조회_QA_Menu.png
├── 시나리오_6_이메일_검색_Email_Search.png
├── 시나리오_7_논문_검색_Tech_Article_하이브리드_검색.png
├── 시나리오_8_특허_검색_Tech_Patent.png
├── 시나리오_9_부품_조회_Tech_Parts.png
├── 시나리오_10_복리후생_조회_QA_Benefit.png
├── Human_in_the_loop_승인_프로세스.png
└── Redis_컨텍스트_관리_플로우.png
```

## 다른 파일 변환하기

다른 마크다운 파일도 변환할 수 있습니다:

```bash
python convert_mermaid_to_images.py project_scenario_flow.md
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

## 고급 사용법

### 출력 디렉토리 변경

스크립트 내부의 `output_dir` 파라미터를 수정하세요:

```python
convert_markdown_to_images(markdown_file, output_dir="my_images")
```

### 배치 처리

여러 파일을 한 번에 변환:

```bash
for file in *.md; do
    python convert_mermaid_to_images.py "$file"
done
```

## 참고

- 변환된 이미지는 PNG 형식입니다
- 원본 Mermaid 다이어그램의 스타일과 색상이 그대로 유지됩니다
- 매우 복잡한 다이어그램은 렌더링에 시간이 걸릴 수 있습니다

