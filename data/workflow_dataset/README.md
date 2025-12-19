# 워크플로우 감지 ML 모델 학습 데이터셋

## 개요

이 데이터셋은 워크플로우 요청과 일반 작업 요청을 분류하기 위한 ML 모델 학습용 데이터입니다.

현재 오케스트레이터(`orchestrator_v3.py`)는 LLM을 사용하여 워크플로우를 감지하고 있지만, 
ML 모델로 대체하여 라우팅 속도를 개선하기 위해 이 데이터셋을 생성했습니다.

## 데이터셋 구조

### 파일 구조

```
workflow_dataset/
├── workflow_detection_dataset.csv      # 전체 데이터셋 (CSV)
├── workflow_detection_dataset.json     # 전체 데이터셋 (JSON)
├── train.csv                          # 학습 세트 (80%)
├── train.json                         # 학습 세트 (JSON)
├── val.csv                            # 검증 세트 (10%)
├── val.json                           # 검증 세트 (JSON)
├── test.csv                           # 테스트 세트 (10%)
├── test.json                          # 테스트 세트 (JSON)
└── README.md                          # 이 파일
```

### 데이터 형식

#### CSV 형식

각 행은 다음과 같은 컬럼을 가집니다:

- `query`: 사용자 질의 (문자열)
- `is_workflow`: 워크플로우 요청 여부 (True/False)
- `workflow_type`: 워크플로우 타입 (예: "business_trip" 또는 None)
- `confidence`: 레이블링 신뢰도 (0.0 ~ 1.0)
- `domain`: 일반 작업인 경우 도메인 (schedule, document, qa, email, tech, recruitment)
- `label`: ML 모델용 바이너리 레이블 (1: 워크플로우, 0: 일반 작업)

#### JSON 형식

```json
{
  "metadata": {
    "created_at": "2024-12-19T...",
    "total_samples": 201,
    "workflow_samples": 143,
    "regular_task_samples": 58,
    "workflow_types": ["business_trip"],
    "domains": ["schedule", "document", "qa", "email", "tech", "recruitment"]
  },
  "samples": [
    {
      "query": "다음 주 3일간 제주도로 워크샵 다녀올게",
      "is_workflow": true,
      "workflow_type": "business_trip",
      "confidence": 0.95,
      "domain": null,
      "label": 1
    },
    ...
  ]
}
```

## 데이터셋 통계

- **총 샘플 수**: 201개
- **워크플로우 샘플**: 143개 (71.1%)
- **일반 작업 샘플**: 58개 (28.9%)

### 워크플로우 타입

- `business_trip`: 출장/휴가 관리 워크플로우

### 일반 작업 도메인

- `schedule`: 일정 관리
- `document`: 문서 작성
- `qa`: 질문/문의
- `email`: 이메일
- `tech`: 기술 검색
- `recruitment`: 채용

## 데이터셋 생성 방법

데이터셋은 `scripts/generate_workflow_dataset.py` 스크립트를 실행하여 생성됩니다.

```bash
python scripts/generate_workflow_dataset.py
```

### 생성 로직

1. **워크플로우 샘플 생성** (`generate_workflow_samples()`)
   - `workflows.yaml`의 `detection_prompt`에서 예시 추출
   - 실행 의도가 명확한 요청들 ("다녀올게", "갈게", "신청해줘" 등)
   - 다양한 날짜, 목적지, 기간 표현 포함

2. **일반 작업 샘플 생성** (`generate_regular_task_samples()`)
   - 각 도메인별 질문형 요청들
   - `common_prompts.yaml`의 분류 기준 참고
   - 경계 케이스 포함 (워크플로우처럼 보이지만 질문형)

3. **추가 변형 생성** (`generate_additional_variations()`)
   - 기본 샘플을 기반으로 다양한 변형 생성
   - 날짜 표현, 목적지, 사유 등의 변형

4. **데이터셋 분할**
   - 학습 세트: 80%
   - 검증 세트: 10%
   - 테스트 세트: 10%
   - 랜덤 시드: 42 (재현성 보장)

## ML 모델 학습 예시

### Python (scikit-learn)

```python
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report

# 데이터 로드
train_df = pd.read_csv("data/workflow_dataset/train.csv")
val_df = pd.read_csv("data/workflow_dataset/val.csv")

# 특징 추출
vectorizer = TfidfVectorizer(max_features=1000, ngram_range=(1, 2))
X_train = vectorizer.fit_transform(train_df["query"])
y_train = train_df["label"]

X_val = vectorizer.transform(val_df["query"])
y_val = val_df["label"]

# 모델 학습
model = LogisticRegression()
model.fit(X_train, y_train)

# 평가
y_pred = model.predict(X_val)
print(classification_report(y_val, y_pred))
```

### Python (transformers)

```python
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from transformers import Trainer, TrainingArguments
from datasets import Dataset

# 데이터 로드
train_df = pd.read_csv("data/workflow_dataset/train.csv")
val_df = pd.read_csv("data/workflow_dataset/val.csv")

# 토크나이저 및 모델 로드
tokenizer = AutoTokenizer.from_pretrained("klue/bert-base")
model = AutoModelForSequenceClassification.from_pretrained(
    "klue/bert-base",
    num_labels=2
)

# 데이터셋 준비
train_dataset = Dataset.from_pandas(train_df[["query", "label"]])
val_dataset = Dataset.from_pandas(val_df[["query", "label"]])

def tokenize_function(examples):
    return tokenizer(examples["query"], truncation=True, padding=True)

train_dataset = train_dataset.map(tokenize_function, batched=True)
val_dataset = val_dataset.map(tokenize_function, batched=True)

# 학습 설정
training_args = TrainingArguments(
    output_dir="./workflow_model",
    num_train_epochs=3,
    per_device_train_batch_size=16,
    per_device_eval_batch_size=16,
    evaluation_strategy="epoch",
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset,
    eval_dataset=val_dataset,
)

# 학습
trainer.train()
```

## 데이터셋 확장

새로운 워크플로우나 일반 작업 예시를 추가하려면:

1. `scripts/generate_workflow_dataset.py` 수정
2. `generate_workflow_samples()` 또는 `generate_regular_task_samples()` 함수에 예시 추가
3. 스크립트 재실행

## 주의사항

1. **클래스 불균형**: 현재 워크플로우 샘플이 일반 작업 샘플보다 많습니다 (143 vs 58).
   - 필요시 일반 작업 샘플을 추가하거나 클래스 가중치를 조정하세요.

2. **경계 케이스**: "출장 신청하려면 어떻게 해야 해?" 같은 질문형 요청은 
   워크플로우가 아닌 일반 작업으로 레이블링되어 있습니다.

3. **신뢰도**: 모든 샘플에 `confidence` 값이 포함되어 있지만, 
   실제 ML 모델 학습 시에는 사용하지 않을 수 있습니다.

## 라우팅 속도 개선 목표

현재 LLM 기반 워크플로우 감지는 약 500-1000ms가 소요됩니다.
ML 모델로 대체하면:
- **예상 속도**: 10-50ms (10-50배 개선)
- **비용**: LLM API 호출 비용 제거
- **확장성**: 더 많은 동시 요청 처리 가능

## 참고 자료

- `agents/workflows.yaml`: 워크플로우 정의
- `agents/common_prompts.yaml`: 도메인 분류 기준
- `agents/orchestrator_v3.py`: 현재 LLM 기반 워크플로우 감지 로직
- `agents/structured_output.py`: `WorkflowDetectionResult` 모델 정의

