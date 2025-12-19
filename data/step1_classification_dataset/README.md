# 1단계 워크플로우 vs 일반 요청 분류 데이터셋

## 개요

이 데이터셋은 **1단계 분류**를 위한 ML 모델 학습용 데이터입니다.

사용자 요청을 **워크플로우 실행 요청**과 **일반 작업 요청**으로 이진 분류합니다.
워크플로우 타입(business_trip 등)은 중요하지 않으며, 워크플로우인지만 판단합니다.

현재 오케스트레이터(`orchestrator_v3.py`)의 `classify_request_type()` 함수는 LLM을 사용하여 
이 분류를 수행하고 있지만, ML 모델로 대체하여 라우팅 속도를 개선하기 위해 이 데이터셋을 생성했습니다.

## 데이터셋 구조

### 파일 구조

```
step1_classification_dataset/
├── step1_classification_dataset.csv      # 전체 데이터셋 (CSV)
├── step1_classification_dataset.json     # 전체 데이터셋 (JSON)
├── dataset.jsonl                        # 전체 데이터셋 (HuggingFace 형식)
├── train.csv                            # 학습 세트 (80%)
├── train.json                           # 학습 세트 (JSON)
├── train/dataset.jsonl                  # HuggingFace 형식 (학습)
├── val.csv                              # 검증 세트 (10%)
├── val.json                             # 검증 세트 (JSON)
├── val/dataset.jsonl                    # HuggingFace 형식 (검증)
├── test.csv                             # 테스트 세트 (10%)
├── test.json                            # 테스트 세트 (JSON)
├── test/dataset.jsonl                   # HuggingFace 형식 (테스트)
└── README.md                            # 이 파일
```

### 데이터 형식

#### CSV 형식

각 행은 다음과 같은 컬럼을 가집니다:

- `query`: 사용자 질의 (문자열)
- `is_workflow`: 워크플로우 요청 여부 (True/False)
- `confidence`: 레이블링 신뢰도 (0.0 ~ 1.0)
- `label`: ML 모델용 바이너리 레이블 (1: 워크플로우, 0: 일반 작업)

#### JSON 형식

```json
{
  "metadata": {
    "created_at": "2025-12-19T...",
    "total_samples": 541,
    "workflow_samples": 371,
    "regular_task_samples": 170,
    "description": "1단계 워크플로우 vs 일반 요청 분류 데이터셋",
    "classification_type": "binary"
  },
  "samples": [
    {
      "query": "다음 주 3일간 제주도로 워크샵 다녀올게",
      "is_workflow": true,
      "confidence": 0.95,
      "label": 1
    },
    {
      "query": "출장 일정 취소해줘",
      "is_workflow": false,
      "confidence": 0.95,
      "label": 0
    }
  ]
}
```

## 데이터셋 통계

- **총 샘플 수**: 801개
- **워크플로우 샘플**: 471개 (58.8%)
- **일반 작업 샘플**: 330개 (41.2%)
- **학습/검증/테스트**: 640/80/81 (80/10/10)
- **클래스 균형**: 적절함 (1.4:1 비율)

### 워크플로우 요청 특징

- **복합 워크플로우 실행 의도**: 여러 단계의 작업을 순차적으로 자동 처리
- **실행 동사**: "다녀올게", "갈게", "신청하고 싶어", "준비해줘", "신청해줘"
- **명령형/의지 표현**: "~하고 싶어", "~해줘", "~할게"
- **구체적인 정보 포함**: 날짜, 목적지, 기간 등

**예시:**
- "다음 주 3일간 제주도로 워크샵 다녀올게" → 워크플로우
- "12월 20일부터 22일까지 부산 출장 갈게" → 워크플로우
- "출장 신청해줘" → 워크플로우

### 일반 작업 요청 특징

1. **취소/삭제/수정 요청**
   - "출장 일정 취소해줘" → 일반 작업
   - "일정 삭제해줘" → 일반 작업
   - "출장 일정 수정해줘" → 일반 작업

2. **정보 조회/질문형 요청**
   - "출장 규정 알려줘" → 일반 작업
   - "휴가 신청 방법은?" → 일반 작업
   - "출장 신청서 양식 보여줘" → 일반 작업

3. **단일 작업 요청**
   - "내일 연차 신청" → 일반 작업
   - "회의실 예약해줘" → 일반 작업
   - "문서 작성해줘" → 일반 작업
   - "이메일에서 육아휴직 관련 내용 찾아줘" → 일반 작업

## 데이터셋 생성 방법

데이터셋은 `scripts/generate_step1_classification_dataset_enhanced.py` 스크립트를 실행하여 생성됩니다.

```bash
python scripts/generate_step1_classification_dataset_enhanced.py
```

### 향상된 기능

- **더 많은 샘플**: 801개 (기존 541개에서 증가)
- **클래스 균형 개선**: 58.8% vs 41.2% (기존 68.6% vs 31.4%)
- **데이터 증강**: 패러프레이즈 기법 적용
- **다양한 표현**: 템플릿 기반 변형 생성
- **HuggingFace 형식**: KLUE-RoBERTa 학습에 최적화된 JSONL 형식 제공

### 생성 로직

1. **워크플로우 샘플 생성** (`generate_workflow_samples()`)
   - `workflow_classification.yaml`의 예시 기반
   - 복합 워크플로우 실행 의도가 명확한 요청들
   - 다양한 날짜, 목적지, 기간 표현 포함

2. **일반 작업 샘플 생성** (`generate_regular_task_samples()`)
   - 취소/삭제/수정 요청
   - 정보 조회/질문형 요청
   - 단일 작업 요청
   - 각 도메인별 예시 (schedule, document, qa, email, tech, recruitment)

3. **경계 케이스 샘플 생성** (`generate_edge_case_samples()`)
   - 워크플로우처럼 보이지만 질문형인 경우
   - 일반 작업처럼 보이지만 워크플로우인 경우

4. **추가 변형 생성** (`generate_additional_variations()`)
   - 기본 샘플을 기반으로 다양한 변형 생성
   - 날짜 표현, 목적지, 사유 등의 변형

5. **데이터셋 분할**
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
from sklearn.metrics import classification_report, confusion_matrix

# 데이터 로드
train_df = pd.read_csv("data/step1_classification_dataset/train.csv")
val_df = pd.read_csv("data/step1_classification_dataset/val.csv")

# 특징 추출
vectorizer = TfidfVectorizer(
    max_features=1000,
    ngram_range=(1, 2),  # 단어와 2-gram 사용
    min_df=2,
    max_df=0.95
)
X_train = vectorizer.fit_transform(train_df["query"])
y_train = train_df["label"]

X_val = vectorizer.transform(val_df["query"])
y_val = val_df["label"]

# 모델 학습 (클래스 불균형 고려)
from sklearn.utils.class_weight import compute_class_weight
class_weights = compute_class_weight(
    'balanced',
    classes=[0, 1],
    y=y_train
)
class_weight_dict = {0: class_weights[0], 1: class_weights[1]}

model = LogisticRegression(
    class_weight=class_weight_dict,
    max_iter=1000,
    random_state=42
)
model.fit(X_train, y_train)

# 평가
y_pred = model.predict(X_val)
print(classification_report(y_val, y_pred))
print(confusion_matrix(y_val, y_pred))
```

### Python (transformers - KLUE-RoBERTa)

```python
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from transformers import Trainer, TrainingArguments
from datasets import load_dataset
import pandas as pd

# HuggingFace 형식으로 데이터 로드 (JSONL)
train_dataset = load_dataset("json", data_files="train/dataset.jsonl", split="train")
val_dataset = load_dataset("json", data_files="val/dataset.jsonl", split="train")

# 또는 CSV로 로드
# train_df = pd.read_csv("data/step1_classification_dataset/train.csv")
# train_dataset = Dataset.from_pandas(train_df[["query", "label"]])
# val_df = pd.read_csv("data/step1_classification_dataset/val.csv")
# val_dataset = Dataset.from_pandas(val_df[["query", "label"]])

# 토크나이저 및 모델 로드
tokenizer = AutoTokenizer.from_pretrained("klue/roberta-base")
model = AutoModelForSequenceClassification.from_pretrained(
    "klue/roberta-base",
    num_labels=2
)

# 데이터셋 토크나이징
def tokenize_function(examples):
    return tokenizer(
        examples["text"],  # JSONL 형식에서는 "text" 필드 사용
        truncation=True,
        padding=True,
        max_length=128
    )

train_dataset = train_dataset.map(tokenize_function, batched=True)
val_dataset = val_dataset.map(tokenize_function, batched=True)

# 학습 설정
training_args = TrainingArguments(
    output_dir="./step1_workflow_classifier",
    num_train_epochs=5,
    per_device_train_batch_size=16,
    per_device_eval_batch_size=16,
    evaluation_strategy="epoch",
    save_strategy="epoch",
    load_best_model_at_end=True,
    metric_for_best_model="f1",
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

### Python (SetFit)

```python
from setfit import SetFitModel, SetFitTrainer
from datasets import load_dataset

# 데이터 로드 (CSV 또는 JSONL)
import pandas as pd
train_df = pd.read_csv("data/step1_classification_dataset/train.csv")
train_dataset = load_dataset("pandas", data=train_df[["query", "label"]])

# 또는 JSONL 직접 로드
# train_dataset = load_dataset("json", data_files="train/dataset.jsonl", split="train")

# SetFit 모델 초기화 (다국어 지원 모델 사용)
model = SetFitModel.from_pretrained(
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

# 학습 설정
trainer = SetFitTrainer(
    model=model,
    train_dataset=train_dataset["train"],
    num_epochs=1,  # SetFit은 보통 1 epoch로 충분
    batch_size=16,
    learning_rate=2e-5,
    num_iterations=20,  # Contrastive learning 반복 횟수
)

# 학습
trainer.train()

# 모델 저장
trainer.model.save_pretrained("./step1_setfit_model")
```

## 데이터셋 확장

새로운 워크플로우나 일반 작업 예시를 추가하려면:

1. `scripts/generate_step1_classification_dataset_enhanced.py` 수정
2. `generate_workflow_samples()` 또는 `generate_regular_task_samples()` 함수에 예시 추가
3. 스크립트 재실행

## 주의사항

1. **클래스 균형**: 현재 워크플로우 샘플이 일반 작업 샘플보다 약간 많습니다 (58.8% vs 41.2%).
   - 클래스 비율이 1.4:1로 적절한 수준입니다.
   - 필요시 학습 시 클래스 가중치를 조정할 수 있습니다.

2. **경계 케이스**: "출장 신청하려면 어떻게 해야 해?" 같은 질문형 요청은 
   워크플로우가 아닌 일반 작업으로 레이블링되어 있습니다.

3. **단일 작업 vs 워크플로우**: "내일 연차 신청"은 단일 작업이므로 일반 작업으로 분류됩니다.
   반면 "출장 신청해줘"는 복합 워크플로우 실행 의도이므로 워크플로우로 분류됩니다.

4. **취소/삭제/수정**: 취소/삭제/수정 동사가 포함된 요청은 **반드시** 일반 작업으로 분류됩니다.

## 라우팅 속도 개선 목표

현재 LLM 기반 1단계 분류는 약 500-1000ms가 소요됩니다.
ML 모델로 대체하면:
- **예상 속도**: 10-50ms (10-50배 개선)
- **비용**: LLM API 호출 비용 제거
- **확장성**: 더 많은 동시 요청 처리 가능

## 통합 방법

학습된 모델을 `orchestrator_v3.py`의 `classify_request_type()` 함수에 통합:

```python
async def classify_request_type(query: str) -> Dict[str, Any]:
    """1단계: 사용자 요청을 워크플로우 요청과 일반 요청으로 분류합니다."""
    
    # ML 모델 사용 (빠른 분류)
    try:
        # 모델 로드 (한 번만 로드, 메모리에 캐싱)
        if not hasattr(classify_request_type, '_model'):
            classify_request_type._model = joblib.load("step1_classifier.pkl")
            classify_request_type._vectorizer = joblib.load("step1_vectorizer.pkl")
        
        # 추론
        features = classify_request_type._vectorizer.transform([query])
        proba = classify_request_type._model.predict_proba(features)[0]
        
        is_workflow = proba[1] > 0.5  # 워크플로우 확률
        confidence = max(proba)
        
        return {
            "is_workflow_request": bool(is_workflow),
            "confidence": float(confidence)
        }
    except Exception as e:
        # Fallback: LLM 사용
        return await classify_request_type_llm(query)
```

## 참고 자료

- `agents/workflow_classification.yaml`: 1단계 분류 프롬프트 및 예시
- `agents/workflows.yaml`: 워크플로우 정의
- `agents/orchestrator_v3.py`: `classify_request_type()` 함수 (현재 LLM 기반)
- `agents/structured_output.py`: `WorkflowDetectionResult` 모델 정의

