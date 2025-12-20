"""코랩에서 KLUE-RoBERTa 모델 학습 스크립트.

사용 방법:
1. 코랩에 이 스크립트와 데이터셋 파일 업로드
2. 스크립트 실행
3. 학습된 모델 다운로드

필수 파일:
- train.csv
- val.csv
- test.csv (선택적)
"""

import os
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import torch
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    Trainer,
    TrainingArguments,
    EarlyStoppingCallback
)
from datasets import Dataset
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, confusion_matrix
import json
from datetime import datetime


def check_environment():
    """환경 확인 및 GPU/TPU 체크."""
    print("=" * 60)
    print("환경 확인")
    print("=" * 60)
    
    print(f"PyTorch 버전: {torch.__version__}")
    print(f"CUDA 사용 가능: {torch.cuda.is_available()}")
    
    if torch.cuda.is_available():
        print(f"GPU: {torch.cuda.get_device_name(0)}")
        print(f"GPU 메모리: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.2f} GB")
        device = "cuda"
    else:
        print("GPU 사용 불가 - CPU로 학습 (매우 느림)")
        device = "cpu"
    
    # TPU 확인
    try:
        import torch_xla.core.xla_model as xm
        print(f"TPU 사용 가능: True")
        print(f"TPU 디바이스: {xm.xla_device()}")
        device = "tpu"
    except ImportError:
        print("TPU 사용 불가")
    
    print("=" * 60)
    return device


def load_dataset(data_dir: str = "data/step1_classification_dataset"):
    """데이터셋 로드.
    
    Args:
        data_dir: 데이터셋 디렉토리 경로
        
    Returns:
        (train_df, val_df, test_df) 튜플
    """
    print("=" * 60)
    print("데이터셋 로드")
    print("=" * 60)
    
    data_path = Path(data_dir)
    
    # 파일 경로 확인
    train_path = data_path / "train.csv"
    val_path = data_path / "val.csv"
    test_path = data_path / "test.csv"
    
    if not train_path.exists():
        raise FileNotFoundError(f"학습 데이터를 찾을 수 없습니다: {train_path}")
    if not val_path.exists():
        raise FileNotFoundError(f"검증 데이터를 찾을 수 없습니다: {val_path}")
    
    # CSV 로드
    print(f"학습 데이터 로드: {train_path}")
    train_df = pd.read_csv(train_path)
    
    print(f"검증 데이터 로드: {val_path}")
    val_df = pd.read_csv(val_path)
    
    if test_path.exists():
        print(f"테스트 데이터 로드: {test_path}")
        test_df = pd.read_csv(test_path)
    else:
        print("테스트 데이터 없음 - 검증 데이터로 대체")
        test_df = val_df.copy()
    
    # 데이터 확인
    print(f"\n학습 세트: {len(train_df)}개")
    print(f"검증 세트: {len(val_df)}개")
    print(f"테스트 세트: {len(test_df)}개")
    
    # 클래스 분포
    print(f"\n클래스 분포 (학습 세트):")
    print(train_df['label'].value_counts().sort_index())
    print(f"워크플로우: {train_df['label'].sum()}개 ({train_df['label'].sum()/len(train_df)*100:.1f}%)")
    print(f"일반 작업: {(train_df['label']==0).sum()}개 ({(train_df['label']==0).sum()/len(train_df)*100:.1f}%)")
    
    print("=" * 60)
    return train_df, val_df, test_df


def prepare_datasets(train_df, val_df, test_df, tokenizer):
    """데이터셋을 토크나이징하여 준비.
    
    Args:
        train_df: 학습 데이터프레임
        val_df: 검증 데이터프레임
        test_df: 테스트 데이터프레임
        tokenizer: 토크나이저
        
    Returns:
        (train_dataset, val_dataset, test_dataset) 튜플
    """
    print("=" * 60)
    print("데이터셋 전처리")
    print("=" * 60)
    
    def tokenize_function(examples):
        return tokenizer(
            examples["query"],
            truncation=True,
            padding=True,
            max_length=128
        )
    
    # DataFrame을 Dataset으로 변환
    train_dataset = Dataset.from_pandas(train_df[["query", "label"]])
    val_dataset = Dataset.from_pandas(val_df[["query", "label"]])
    test_dataset = Dataset.from_pandas(test_df[["query", "label"]])
    
    # 토크나이징
    print("토크나이징 중...")
    train_dataset = train_dataset.map(tokenize_function, batched=True)
    val_dataset = val_dataset.map(tokenize_function, batched=True)
    test_dataset = test_dataset.map(tokenize_function, batched=True)
    
    print("전처리 완료")
    print("=" * 60)
    
    return train_dataset, val_dataset, test_dataset


def compute_metrics(eval_pred):
    """평가 메트릭 계산."""
    predictions, labels = eval_pred
    predictions = np.argmax(predictions, axis=1)
    
    accuracy = accuracy_score(labels, predictions)
    precision, recall, f1, _ = precision_recall_fscore_support(
        labels, predictions, average='weighted'
    )
    
    return {
        'accuracy': accuracy,
        'precision': precision,
        'recall': recall,
        'f1': f1
    }


def train_model(
    train_dataset,
    val_dataset,
    model_name: str = "klue/roberta-base",
    output_dir: str = "./step1_klue_roberta_model",
    num_epochs: int = 5,
    batch_size: int = 16,
    learning_rate: float = 2e-5,
    device: str = "cuda"
):
    """모델 학습.
    
    Args:
        train_dataset: 학습 데이터셋
        val_dataset: 검증 데이터셋
        model_name: 사전 학습 모델 이름
        output_dir: 모델 저장 디렉토리
        num_epochs: 학습 에포크 수
        batch_size: 배치 크기
        learning_rate: 학습률
        device: 디바이스 (cuda/cpu/tpu)
        
    Returns:
        (trainer, tokenizer, model) 튜플
    """
    print("=" * 60)
    print("모델 학습")
    print("=" * 60)
    
    # 모델 및 토크나이저 로드
    print(f"모델 로드 중: {model_name}")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_name,
        num_labels=2,
        problem_type="single_label_classification"
    )
    
    print(f"모델 파라미터 수: {sum(p.numel() for p in model.parameters()):,}")
    
    # 학습 설정
    training_args = TrainingArguments(
        output_dir=output_dir,
        num_train_epochs=num_epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        learning_rate=learning_rate,
        weight_decay=0.01,
        warmup_steps=100,
        logging_dir=f"{output_dir}/logs",
        logging_steps=10,
        evaluation_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="f1",
        greater_is_better=True,
        save_total_limit=3,
        fp16=torch.cuda.is_available() and device == "cuda",
        dataloader_num_workers=2,
        report_to="none",
    )
    
    # Trainer 생성
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=3)]
    )
    
    print(f"\n학습 설정:")
    print(f"  학습 샘플 수: {len(train_dataset)}")
    print(f"  검증 샘플 수: {len(val_dataset)}")
    print(f"  배치 크기: {batch_size}")
    print(f"  학습률: {learning_rate}")
    print(f"  에포크 수: {num_epochs}")
    print(f"  총 스텝 수: {len(train_dataset) // batch_size * num_epochs}")
    print(f"\n학습 시작...")
    
    # 학습
    trainer.train()
    
    print("\n학습 완료!")
    print("=" * 60)
    
    return trainer, tokenizer, model


def evaluate_model(trainer, test_dataset, test_df):
    """모델 평가.
    
    Args:
        trainer: Trainer 객체
        test_dataset: 테스트 데이터셋
        test_df: 테스트 데이터프레임
    """
    print("=" * 60)
    print("모델 평가")
    print("=" * 60)
    
    # 테스트 세트 평가
    print("테스트 세트 평가 중...")
    test_results = trainer.evaluate(test_dataset)
    
    print("\n=== 테스트 세트 성능 ===")
    for key, value in test_results.items():
        if 'loss' not in key:
            print(f"{key}: {value:.4f}")
    
    # 예측 및 혼동 행렬
    predictions = trainer.predict(test_dataset)
    pred_labels = np.argmax(predictions.predictions, axis=1)
    true_labels = test_df['label'].values
    
    print("\n=== 혼동 행렬 ===")
    cm = confusion_matrix(true_labels, pred_labels)
    print(cm)
    print("\n(행: 실제, 열: 예측)")
    print("0: 일반 작업, 1: 워크플로우")
    print("=" * 60)


def save_model(trainer, tokenizer, output_dir: str = "./step1_klue_roberta_final"):
    """모델 저장.
    
    Args:
        trainer: Trainer 객체
        tokenizer: 토크나이저
        output_dir: 저장 디렉토리
    """
    print("=" * 60)
    print("모델 저장")
    print("=" * 60)
    
    # 모델 저장
    trainer.save_model(output_dir)
    tokenizer.save_pretrained(output_dir)
    
    # 메타데이터 저장
    metadata = {
        "model_name": "klue/roberta-base",
        "task": "workflow_classification",
        "num_labels": 2,
        "saved_at": datetime.now().isoformat(),
        "description": "1단계 워크플로우 vs 일반 요청 분류 모델"
    }
    
    metadata_path = Path(output_dir) / "model_metadata.json"
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    
    print(f"모델 저장 완료: {output_dir}")
    
    # 저장된 파일 목록
    saved_files = list(Path(output_dir).glob("*"))
    print(f"\n저장된 파일 ({len(saved_files)}개):")
    for file in sorted(saved_files):
        if file.is_file():
            size = file.stat().st_size
            if size > 1024 * 1024:
                size_str = f"{size / (1024 * 1024):.2f} MB"
            elif size > 1024:
                size_str = f"{size / 1024:.2f} KB"
            else:
                size_str = f"{size} B"
            print(f"  - {file.name} ({size_str})")
    
    print("\n다운로드 방법:")
    print("1. 코랩 파일 브라우저에서 다운로드")
    print("2. 또는 다음 코드 실행:")
    print(f"   from google.colab import files")
    print(f"   !zip -r model.zip {output_dir}")
    print(f"   files.download('model.zip')")
    print("=" * 60)


def main():
    """메인 함수."""
    print("\n" + "=" * 60)
    print("KLUE-RoBERTa 워크플로우 분류 모델 학습")
    print("=" * 60 + "\n")
    
    # 환경 확인
    device = check_environment()
    
    # 데이터셋 로드
    try:
        train_df, val_df, test_df = load_dataset()
    except FileNotFoundError as e:
        print(f"\n오류: {e}")
        print("\n해결 방법:")
        print("1. 코랩에 데이터셋 파일 업로드:")
        print("   - train.csv")
        print("   - val.csv")
        print("   - test.csv (선택적)")
        print("2. 또는 Google Drive 마운트 후 경로 수정")
        return
    
    # 모델 및 토크나이저 로드
    model_name = "klue/roberta-base"
    print(f"\n모델 로드 중: {model_name}")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    
    # 데이터셋 준비
    train_dataset, val_dataset, test_dataset = prepare_datasets(
        train_df, val_df, test_df, tokenizer
    )
    
    # 모델 학습
    trainer, tokenizer, model = train_model(
        train_dataset,
        val_dataset,
        model_name=model_name,
        output_dir="./step1_klue_roberta_model",
        num_epochs=5,
        batch_size=16,
        learning_rate=2e-5,
        device=device
    )
    
    # 모델 평가
    evaluate_model(trainer, test_dataset, test_df)
    
    # 모델 저장
    save_model(trainer, tokenizer, output_dir="./step1_klue_roberta_final")
    
    print("\n" + "=" * 60)
    print("학습 완료!")
    print("=" * 60)
    print("\n다음 단계:")
    print("1. 모델 파일 다운로드")
    print("2. models/ 디렉토리에 모든 파일 복사")
    print("3. python scripts/check_ml_model.py 실행하여 확인")


if __name__ == "__main__":
    main()

