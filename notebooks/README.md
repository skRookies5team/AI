# 코랩 모델 학습 노트북

## 개요

이 디렉토리에는 Google Colab에서 모델을 학습하기 위한 노트북이 포함되어 있습니다.

## 노트북 파일

### `step1_workflow_classifier_training.ipynb`

1단계 워크플로우 vs 일반 요청 분류 모델 학습 노트북

**지원 모델:**
- KLUE-RoBERTa
- SetFit

**주요 기능:**
- GPU/TPU 자동 감지
- 데이터셋 로드 (CSV, JSONL, Google Drive, GitHub)
- 모델 학습 및 평가
- 모델 저장 및 다운로드
- 추론 테스트

## 사용 방법

### 1. 노트북 열기

1. Google Colab 접속: https://colab.research.google.com/
2. 파일 → 노트북 업로드 → `step1_workflow_classifier_training.ipynb` 선택
3. 또는 GitHub에서 직접 열기

### 2. 데이터셋 준비

**방법 1: 코랩에 직접 업로드**
- 왼쪽 파일 아이콘 클릭
- `train.csv`, `val.csv`, `test.csv` 업로드
- 노트북에서 해당 경로 사용

**방법 2: Google Drive 사용**
- Google Drive에 데이터셋 폴더 업로드
- 노트북의 Google Drive 마운트 코드 실행
- Drive 경로로 데이터셋 로드

**방법 3: GitHub에서 다운로드**
- 노트북의 wget 코드 주석 해제
- 저장소 URL 수정
- 실행

**방법 4: 샘플 데이터 사용**
- 노트북에 내장된 샘플 데이터 사용 (테스트용)

### 3. 런타임 설정

**GPU 사용:**
- 런타임 → 런타임 유형 변경 → GPU 선택

**TPU 사용:**
- 런타임 → 런타임 유형 변경 → TPU 선택
- TPU 관련 코드 자동 실행

### 4. 모델 학습

1. 모든 셀을 순차적으로 실행
2. KLUE-RoBERTa 또는 SetFit 중 선택하여 학습
3. 학습 완료 후 모델 다운로드

### 5. 모델 다운로드

**방법 1: 직접 다운로드**
- 노트북의 다운로드 코드 실행
- ZIP 파일로 다운로드

**방법 2: Google Drive에 저장**
- Google Drive 마운트
- 모델을 Drive에 복사
- 나중에 다운로드

## 예상 학습 시간

### v5e-1 TPU
- KLUE-RoBERTa: 18-30분
- SetFit: 7-13분

### GTX 5080 16GB
- KLUE-RoBERTa: 21-37분
- SetFit: 4-10분

## 데이터셋 형식

### CSV 형식
```csv
query,is_workflow,confidence,label
다음 주 3일간 제주도로 워크샵 다녀올게,True,0.95,1
출장 일정 취소해줘,False,0.95,0
```

### JSONL 형식 (HuggingFace)
```jsonl
{"text": "다음 주 3일간 제주도로 워크샵 다녀올게", "label": 1}
{"text": "출장 일정 취소해줘", "label": 0}
```

## 주의사항

1. **데이터셋 경로**: 노트북의 데이터셋 로드 부분을 실제 경로에 맞게 수정
2. **메모리**: GPU 메모리 부족 시 배치 크기 조정
3. **학습 시간**: TPU는 첫 실행 시 컴파일 시간 추가 (1-2분)
4. **모델 저장**: 학습 완료 후 반드시 모델 다운로드 또는 Drive에 저장

## 문제 해결

### GPU 메모리 부족
```python
# 배치 크기 줄이기
per_device_train_batch_size=8  # 16 → 8
per_device_eval_batch_size=8
```

### TPU 연결 실패
- 런타임 재시작
- TPU 런타임 다시 선택

### 데이터셋 로드 실패
- 파일 경로 확인
- 파일 인코딩 확인 (UTF-8)
- CSV 형식 확인

## 참고 자료

- 데이터셋: `data/step1_classification_dataset/`
- 데이터셋 생성 스크립트: `scripts/generate_step1_classification_dataset_enhanced.py`
- 통합 가이드: `data/step1_classification_dataset/README.md`

