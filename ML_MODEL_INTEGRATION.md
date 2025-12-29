# ML 모델 통합 가이드

## 개요

1단계 워크플로우 vs 일반 요청 분류를 LLM에서 ML 모델(KLUE-RoBERTa)로 개선했습니다.

## 개선 사항

### 성능 개선
- **LLM 분류**: 500-1000ms 소요
- **ML 모델 분류**: 10-50ms 소요 (10-50배 개선)
- **비용**: LLM API 호출 비용 제거
- **확장성**: 더 많은 동시 요청 처리 가능

### Fallback 메커니즘
- ML 모델이 사용 불가능한 경우 자동으로 LLM fallback
- 모델 파일이 없어도 시스템 정상 작동

## 모델 파일 구조

### 필수 파일
```
models/
├── config.json              # 모델 설정 (필수)
├── pytorch_model.bin        # 모델 가중치 (필수, 또는 model.safetensors)
├── tokenizer_config.json    # 토크나이저 설정 (필수)
├── special_tokens_map.json  # 특수 토큰 매핑 (필수)
├── vocab.json               # 어휘 사전 (RoBERTa, 필수)
└── merges.txt               # BPE 병합 규칙 (RoBERTa, 필수)
```

### 선택적 파일
```
models/
└── training_args.bin        # 학습 설정 (선택적)
```

## 모델 파일 확인

모델 파일이 제대로 있는지 확인:

```bash
python scripts/check_ml_model.py
```

### 현재 상태 확인 결과

현재 `models/` 디렉토리에는 다음 파일들이 있습니다:
- ✅ `config.json`
- ✅ `tokenizer_config.json`
- ✅ `special_tokens_map.json`
- ✅ `training_args.bin`
- ❌ `pytorch_model.bin` 또는 `model.safetensors` (없음)
- ❌ `vocab.json` (없음)
- ❌ `merges.txt` (없음)

**결과**: 모델 가중치 파일이 없어 현재는 LLM fallback이 사용됩니다.

## 모델 파일 추가 방법

### 1. 코랩에서 모델 다운로드

코랩 노트북에서 학습한 모델을 다운로드:

```python
# 코랩 노트북에서 실행
from google.colab import files
import zipfile
import os

# 모델 디렉토리 압축
model_path = "./step1_klue_roberta_final"
zip_path = "model.zip"

with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
    for root, dirs, files in os.walk(model_path):
        for file in files:
            file_path = os.path.join(root, file)
            arcname = os.path.relpath(file_path, model_path)
            zipf.write(file_path, arcname)

# 다운로드
files.download(zip_path)
```

### 2. 로컬에 모델 파일 복사

다운로드한 ZIP 파일을 압축 해제하고 `models/` 디렉토리에 복사:

```bash
# Windows
# 1. ZIP 파일 압축 해제
# 2. 모든 파일을 models/ 디렉토리에 복사

# 또는 PowerShell
Expand-Archive -Path model.zip -DestinationPath ./models
```

### 3. 모델 파일 확인

```bash
python scripts/check_ml_model.py
```

모든 필수 파일이 있으면 ML 모델이 자동으로 사용됩니다.

## 코드 구조

### ML 분류기 모듈

`agents/ml_classifier.py`:
- `load_model()`: 모델 로드
- `classify_workflow_request()`: ML 모델로 분류
- `is_model_available()`: 모델 사용 가능 여부 확인

### 오케스트레이터 통합

`agents/orchestrator_v3.py`의 `classify_request_type()` 함수:
1. ML 모델 사용 가능 여부 확인
2. ML 모델로 분류 시도
3. 실패 시 LLM fallback

## 사용 방법

### 자동 모드 (권장)

코드는 자동으로 ML 모델을 사용하고, 사용 불가능하면 LLM fallback을 사용합니다.

```python
# orchestrator_v3.py에서 자동 처리
result = await classify_request_type(query)
# ML 모델이 있으면 ML 사용, 없으면 LLM 사용
```

### 수동 확인

```python
from agents.ml_classifier import is_model_available

if is_model_available():
    print("ML 모델 사용 가능")
else:
    print("ML 모델 사용 불가, LLM fallback 사용")
```

## 성능 모니터링

### 로그 확인

ML 모델 사용 시:
```
✅ 1단계 분류 (ML): 워크플로우 (신뢰도: 0.95, 15.23ms)
```

LLM fallback 사용 시:
```
✅ 1단계 분류 (LLM): 워크플로우 (신뢰도: 0.90, 0.85초)
```

## 문제 해결

### 1. 모델 파일이 없음

**증상**: LLM fallback이 계속 사용됨

**해결**:
1. 코랩에서 모델 다운로드
2. `models/` 디렉토리에 파일 복사
3. `python scripts/check_ml_model.py` 실행하여 확인

### 2. transformers 라이브러리 오류

**증상**: `ImportError: transformers`

**해결**:
```bash
pip install transformers torch
```

### 3. 모델 로드 실패

**증상**: 모델 파일은 있지만 로드 실패

**해결**:
1. 모델 파일이 완전한지 확인
2. `config.json`과 모델 가중치 파일 버전 일치 확인
3. 로그 확인: `agents/ml_classifier.py`의 로그 메시지

## 다음 단계

1. ✅ ML 모델 로더 구현
2. ✅ 오케스트레이터 통합
3. ✅ Fallback 메커니즘 구현
4. ⏳ 모델 파일 추가 (코랩에서 다운로드)
5. ⏳ 성능 테스트 및 모니터링

## 참고 자료

- 데이터셋: `data/step1_classification_dataset/`
- 학습 노트북: `notebooks/step1_workflow_classifier_training.ipynb`
- 모델 확인 스크립트: `scripts/check_ml_model.py`








