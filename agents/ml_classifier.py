"""ML 모델 기반 워크플로우 분류기.

KLUE-RoBERTa 모델을 사용하여 워크플로우 요청과 일반 요청을 분류합니다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# 전역 변수: 모델 및 토크나이저 (지연 로딩)
_model = None
_tokenizer = None
_model_loaded = False
_model_path: Optional[Path] = None
_warning_shown = False  # 경고 메시지 중복 방지


def preprocess_query(query: str) -> str:
    """사용자 쿼리를 모델 입력에 적합하게 전처리합니다.
    
    전처리 항목:
    - 공백 정리 (연속 공백을 하나로)
    - 앞뒤 공백 제거
    - 특수문자 정규화
    - 반복 문자 정리
    
    Args:
        query: 원본 사용자 쿼리
        
    Returns:
        전처리된 쿼리
    """
    import re
    
    if not query:
        return ""
    
    # 1. 공백 정리 (연속 공백을 하나로, 탭/줄바꿈도 공백으로)
    query = re.sub(r'[\s\t\n\r]+', ' ', query)
    
    # 2. 앞뒤 공백 제거
    query = query.strip()
    
    # 3. 특수문자 정규화 (전각/반각 통일은 한글 특성상 생략)
    # 한글은 전각이 일반적이므로 그대로 유지
    
    # 4. 반복 문자 정리 (예: "출장출장출장" -> "출장출장", "해줘해줘해줘" -> "해줘해줘")
    # 단일 문자가 3번 이상 반복되는 경우 2번으로 제한
    query = re.sub(r'(.)\1{2,}', r'\1\1', query)
    
    # 5. 불필요한 특수문자 제거 (선택적 - 한글 문맥 보존을 위해 제거하지 않음)
    # 예: "출장 신청해줘!" -> "출장 신청해줘!" (유지)
    
    # 6. 빈 쿼리 체크
    if not query:
        return ""
    
    return query


def _get_model_path() -> Optional[Path]:
    """모델 경로를 반환합니다.
    
    여러 가능한 위치를 확인합니다.
    
    Returns:
        모델 디렉토리 Path 또는 None
    """
    # 프로젝트 루트 기준 models 디렉토리
    current_file = Path(__file__)
    project_root = current_file.parent.parent
    
    # 가능한 모델 경로들
    possible_paths = [
        project_root / "models",
        project_root / "step1_klue_roberta_final",
        project_root / "step1_klue_roberta_model",
        project_root / "models" / "step1_klue_roberta_final",
    ]
    
    for model_dir in possible_paths:
        if model_dir.exists() and model_dir.is_dir():
            return model_dir
    
    return None


def _check_model_files(model_path: Path) -> bool:
    """모델 파일들이 존재하는지 확인합니다.
    
    Args:
        model_path: 모델 디렉토리 경로
        
    Returns:
        모델 파일이 모두 존재하면 True
    """
    global _warning_shown
    
    required_files = [
        "config.json",
        "tokenizer_config.json",
    ]
    
    # 모델 가중치 파일 (여러 가능한 이름 확인)
    weight_files = [
        "pytorch_model.bin",
        "model.safetensors",
        "pytorch_model.bin.index.json",  # sharded 모델
    ]
    
    # 필수 파일 확인
    for file in required_files:
        if not (model_path / file).exists():
            if not _warning_shown:
                print(f"⚠️ [ML 모델] 필수 파일이 없습니다: {model_path / file}")
                logger.warning(f"ML 모델 필수 파일이 없습니다: {file} (경로: {model_path})")

            return False
    
    # 가중치 파일 확인 (여러 패턴 시도)
    has_weight = False
    found_weight_file = None
    
    # 1. 일반 모델 파일 확인
    for f in ["pytorch_model.bin", "model.safetensors"]:
        if (model_path / f).exists():
            has_weight = True

            found_weight_file = f
            break
    
    # 2. Sharded 모델 확인 (여러 파일로 나뉜 경우)
    if not has_weight:
        index_file = model_path / "pytorch_model.bin.index.json"
        if index_file.exists():
            has_weight = True

            found_weight_file = "pytorch_model.bin.index.json"
    
    # 3. 다른 가능한 위치 확인 (하위 디렉토리)
    if not has_weight:
        for subdir in model_path.iterdir():
            if subdir.is_dir():
                for f in ["pytorch_model.bin", "model.safetensors"]:
                    if (subdir / f).exists():
                        has_weight = True
                        found_weight_file = str(subdir / f)
                        break
                if has_weight:
                    break
    
    if not has_weight:
        if not _warning_shown:
            print(f"⚠️ [ML 모델] 가중치 파일이 없습니다. 경로: {model_path}")
            print(f"   찾는 파일: pytorch_model.bin 또는 model.safetensors")
            print(f"   LLM fallback을 사용합니다.")
            logger.warning(f"ML 모델 가중치 파일이 없습니다. 경로: {model_path}, LLM fallback을 사용합니다.")
            _warning_shown = True
        return False
    
    if not _warning_shown and found_weight_file:
        print(f"✅ [ML 모델] 모델 파일 확인 완료: {found_weight_file}")
    
    return True


def load_model() -> bool:
    """ML 모델을 로드합니다.
    
    Returns:
        모델 로드 성공 여부
    """
    global _model, _tokenizer, _model_loaded, _model_path, _warning_shown
    
    if _model_loaded:
        return True
    
    try:
        model_path = _get_model_path()
        if not model_path:
            if not _warning_shown:
                print("⚠️ [ML 모델] 모델 디렉토리를 찾을 수 없습니다.")
                print("   확인한 경로:")
                current_file = Path(__file__)
                project_root = current_file.parent.parent
                possible_paths = [
                    project_root / "models",
                    project_root / "step1_klue_roberta_final",
                    project_root / "step1_klue_roberta_model",
                    project_root / "models" / "step1_klue_roberta_final",
                ]
                for path in possible_paths:
                    exists = "✅" if path.exists() else "❌"
                    print(f"   {exists} {path}")
                print("   LLM fallback을 사용합니다.")
                logger.warning("ML 모델 디렉토리를 찾을 수 없습니다.")
                _warning_shown = True
            return False
        
        _model_path = model_path
        print(f"🔍 [ML 모델] 모델 경로 확인: {model_path}")

        
        # 모델 파일 확인
        if not _check_model_files(model_path):
            # 경고는 _check_model_files에서 이미 출력됨
            return False
        
        # transformers 라이브러리 import
        try:
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
        except ImportError:
            if not _warning_shown:

                print("⚠️ [ML 모델] transformers 라이브러리가 설치되지 않았습니다.")
                print("   설치 명령: pip install transformers")
                print("   LLM fallback을 사용합니다.")
                logger.warning("transformers 라이브러리가 설치되지 않았습니다. pip install transformers")
                _warning_shown = True
            return False
        
        # 모델 및 토크나이저 로드
        print(f"📦 [ML 모델] 모델 로드 중: {model_path}")
        _tokenizer = AutoTokenizer.from_pretrained(str(model_path))
        _model = AutoModelForSequenceClassification.from_pretrained(str(model_path))
        
        # 평가 모드로 설정
        _model.eval()
        
        _model_loaded = True

        print("✅ [ML 모델] 모델 로드 완료 - 빠른 분류 사용 가능")
        logger.info("✅ ML 모델 로드 완료 - 빠른 분류 사용 가능")
        return True
        
    except Exception as e:
        if not _warning_shown:

            print(f"❌ [ML 모델] 모델 로드 실패: {e}")
            print(f"   LLM fallback을 사용합니다.")
            logger.error(f"ML 모델 로드 실패: {e}", exc_info=True)
            _warning_shown = True
        _model_loaded = False
        return False


def classify_workflow_request(query: str) -> Dict[str, Any]:
    """ML 모델을 사용하여 워크플로우 요청인지 분류합니다.
    
    Args:
        query: 사용자 쿼리
        
    Returns:
        {
            'is_workflow_request': bool,
            'confidence': float,
            'method': 'ml' or 'fallback'
        }
    """
    global _model, _tokenizer, _model_loaded
    
    # 모델이 로드되지 않았으면 로드 시도
    if not _model_loaded:
        if not load_model():
            # 모델 로드 실패 시 fallback
            return {
                'is_workflow_request': False,
                'confidence': 0.5,
                'method': 'fallback'
            }
    
    try:
        # 쿼리 전처리
        processed_query = preprocess_query(query)
        
        if not processed_query:
            logger.debug("전처리 후 쿼리가 비어있습니다.")
            return {
                'is_workflow_request': False,
                'confidence': 0.5,
                'method': 'fallback'
            }
        
        # 토크나이징 (전처리된 쿼리 사용)
        inputs = _tokenizer(
            processed_query,
            truncation=True,
            padding=True,
            max_length=128,
            return_tensors="pt"
        )
        
        # 추론
        import torch
        with torch.no_grad():
            outputs = _model(**inputs)
            logits = outputs.logits
            probabilities = torch.softmax(logits, dim=-1)
            
            # 예측 (0: 일반 작업, 1: 워크플로우)
            predicted_class = torch.argmax(probabilities, dim=-1).item()
            confidence = probabilities[0][predicted_class].item()
        
        is_workflow = predicted_class == 1
        
        return {
            'is_workflow_request': is_workflow,
            'confidence': float(confidence),
            'method': 'ml'
        }
        
    except Exception as e:
        logger.error(f"ML 모델 추론 실패: {e}", exc_info=True)
        return {
            'is_workflow_request': False,
            'confidence': 0.5,
            'method': 'fallback'
        }


def is_model_available() -> bool:
    """ML 모델이 사용 가능한지 확인합니다.
    
    Returns:
        모델이 사용 가능하면 True
    """
    if _model_loaded:
        return True
    
    # 모델 로드 시도
    return load_model()

