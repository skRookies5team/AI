# 코랩에서 KLUE-RoBERTa 모델 학습 가이드

## 개요

코랩에서 KLUE-RoBERTa 모델을 학습하여 워크플로우 vs 일반 요청 분류 모델을 생성합니다.

## 필수 파일

### 1. 학습 스크립트
- `scripts/train_klue_roberta_colab.py`

### 2. 데이터셋 파일
- `data/step1_classification_dataset/train.csv`
- `data/step1_classification_dataset/val.csv`
- `data/step1_classification_dataset/test.csv` (선택적)

## 코랩에서 사용 방법

### 방법 1: 파일 직접 업로드

1. **코랩 노트북 열기**
   - Google Colab 접속: https://colab.research.google.com/
   - 새 노트북 생성

2. **파일 업로드**
   - 왼쪽 파일 아이콘 클릭
   - `train_klue_roberta_colab.py` 업로드
   - `data/step1_classification_dataset/` 폴더 전체 업로드

3. **스크립트 실행**
   ```python
   # 노트북 셀에서 실행
   !python train_klue_roberta_colab.py
   ```

### 방법 2: Google Drive 사용

1. **Google Drive에 파일 업로드**
   - `train_klue_roberta_colab.py` → Drive
   - `data/step1_classification_dataset/` → Drive

2. **코랩에서 Drive 마운트**
   ```python
   from google.colab import drive
   drive.mount('/content/drive')
   ```

3. **스크립트 수정 및 실행**
   ```python
   # 스크립트의 데이터 경로 수정
   # data_dir = "/content/drive/MyDrive/step1_classification_dataset"
   
   !python /content/drive/MyDrive/train_klue_roberta_colab.py
   ```

### 방법 3: GitHub에서 클론

1. **저장소 클론**
   ```python
   !git clone https://github.com/your-repo/ai_module.git
   %cd ai_module
   ```

2. **스크립트 실행**
   ```python
   !python scripts/train_klue_roberta_colab.py
   ```

## 런타임 설정

### GPU 사용 (권장)
- 런타임 → 런타임 유형 변경 → GPU 선택
- 학습 시간: 약 21-37분

### TPU 사용 (v5e-1)
- 런타임 → 런타임 유형 변경 → TPU 선택
- 학습 시간: 약 18-30분

### CPU 사용 (비권장)
- 학습 시간: 수 시간 소요 가능

## 학습 과정

스크립트는 다음 단계를 자동으로 수행합니다:

1. **환경 확인**: GPU/TPU 확인
2. **데이터셋 로드**: CSV 파일 로드 및 검증
3. **데이터 전처리**: 토크나이징
4. **모델 학습**: KLUE-RoBERTa fine-tuning
5. **모델 평가**: 테스트 세트 성능 평가
6. **모델 저장**: 모든 모델 파일 저장

## 출력 파일

학습 완료 후 다음 파일들이 생성됩니다:

```
step1_klue_roberta_final/
├── config.json              # 모델 설정
├── pytorch_model.bin        # 모델 가중치 (또는 model.safetensors)
├── tokenizer_config.json     # 토크나이저 설정
├── special_tokens_map.json   # 특수 토큰
├── training_args.bin         # 학습 설정
├── vocab.json               # 어휘 사전 (RoBERTa)
├── merges.txt               # BPE 병합 규칙 (RoBERTa)
└── model_metadata.json      # 메타데이터
```

## 모델 다운로드

### 방법 1: 파일 브라우저
1. 왼쪽 파일 아이콘 클릭
2. `step1_klue_roberta_final` 폴더 우클릭
3. 다운로드

### 방법 2: ZIP 파일로 다운로드
```python
from google.colab import files
import zipfile

# ZIP 파일 생성
with zipfile.ZipFile('model.zip', 'w', zipfile.ZIP_DEFLATED) as zipf:
    for root, dirs, files_list in os.walk('step1_klue_roberta_final'):
        for file in files_list:
            file_path = os.path.join(root, file)
            arcname = os.path.relpath(file_path, 'step1_klue_roberta_final')
            zipf.write(file_path, arcname)

# 다운로드
files.download('model.zip')
```

## 로컬에 모델 설치

1. **다운로드한 파일 압축 해제**
2. **모든 파일을 `models/` 디렉토리에 복사**
   ```
   models/
   ├── config.json
   ├── pytorch_model.bin
   ├── tokenizer_config.json
   ├── special_tokens_map.json
   ├── vocab.json
   └── merges.txt
   ```

3. **모델 파일 확인**
   ```bash
   python scripts/check_ml_model.py
   ```

## 학습 파라미터 조정

스크립트의 `train_model()` 함수에서 파라미터를 조정할 수 있습니다:

```python
trainer, tokenizer, model = train_model(
    train_dataset,
    val_dataset,
    model_name="klue/roberta-base",
    output_dir="./step1_klue_roberta_model",
    num_epochs=5,          # 에포크 수
    batch_size=16,         # 배치 크기
    learning_rate=2e-5,    # 학습률
    device=device
)
```

### 권장 설정

- **GPU 메모리 부족 시**: `batch_size=8` 또는 `batch_size=4`
- **더 빠른 학습**: `num_epochs=3`
- **더 높은 정확도**: `num_epochs=7`, `learning_rate=1e-5`

## 문제 해결

### GPU 메모리 부족
```
RuntimeError: CUDA out of memory
```
**해결**: 배치 크기 줄이기
```python
batch_size=8  # 16 → 8
```

### 데이터셋 파일을 찾을 수 없음
```
FileNotFoundError: 학습 데이터를 찾을 수 없습니다
```
**해결**: 
1. 파일 경로 확인
2. 코랩에 파일 업로드 확인
3. `data_dir` 파라미터 수정

### 학습이 너무 느림
- GPU/TPU 런타임 사용 확인
- 배치 크기 증가 (메모리 허용 시)
- Mixed Precision 사용 (자동 활성화)

## 예상 학습 시간

| 하드웨어 | 예상 시간 |
|---------|----------|
| v5e-1 TPU | 18-30분 |
| T4 GPU | 21-37분 |
| CPU | 수 시간 |

## 다음 단계

1. ✅ 모델 학습 완료
2. ✅ 모델 파일 다운로드
3. ✅ `models/` 디렉토리에 복사
4. ✅ `python scripts/check_ml_model.py` 실행하여 확인
5. ✅ 시스템 재시작 후 ML 모델 자동 사용

## 참고 자료

- 데이터셋: `data/step1_classification_dataset/`
- 모델 확인 스크립트: `scripts/check_ml_model.py`
- 통합 가이드: `ML_MODEL_INTEGRATION.md`















