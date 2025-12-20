"""2단계 워크플로우 타입 분류 ML 모델 학습용 데이터셋 생성 스크립트.

workflows.yaml을 활용하여 워크플로우 타입 분류 데이터셋을 생성합니다.
- business_trip vs None (향후 확장 가능)
- KLUE-RoBERTa와 SetFit 모델 학습에 최적화
"""

from __future__ import annotations

import json
import csv
import yaml
import random
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime


def load_workflows_config() -> Dict[str, Any]:
    """워크플로우 설정을 YAML 파일에서 로드합니다.
    
    Returns:
        워크플로우 설정 딕셔너리
    """
    current_dir = Path(__file__).parent.parent
    yaml_path = current_dir / "agents" / "workflows.yaml"
    
    if not yaml_path.exists():
        raise FileNotFoundError(f"워크플로우 파일을 찾을 수 없습니다: {yaml_path}")
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    return config


def generate_business_trip_samples() -> List[Dict[str, Any]]:
    """business_trip 워크플로우 샘플을 생성합니다.
    
    Returns:
        business_trip 워크플로우 샘플 리스트
    """
    samples = []
    
    # workflows.yaml의 예시 기반
    base_queries = [
        "다음 주 3일간 제주도로 워크샵 다녀올게",
        "12월 20일부터 22일까지 부산 출장 갈게",
        "내일부터 3일간 휴가 신청하고 싶어",
        "출장 신청해줘",
        "휴가 다녀올게",
    ]
    
    # 날짜 변형
    date_variations = [
        "다음 주", "내일", "오늘", "이번 주", "다음 달",
        "12월 20일", "1월 15일", "2월 10일", "3월 5일",
        "월요일", "화요일", "수요일", "목요일", "금요일",
    ]
    
    # 기간 변형
    duration_variations = [
        "3일간", "일주일간", "2일간", "4일간", "5일간",
        "3박 4일", "2박 3일", "1박 2일",
    ]
    
    # 목적지 변형
    destination_variations = [
        "제주도", "부산", "서울", "대구", "인천", "광주", "대전", "울산",
        "제주", "제주시", "서귀포", "부산시", "서울시",
    ]
    
    # 목적/사유 변형
    purpose_variations = [
        "워크샵", "출장", "휴가", "여행", "회의", "세미나", "교육",
        "출장 가", "출장 다녀올", "출장 갈", "휴가 가", "휴가 다녀올",
    ]
    
    # 동사 변형
    action_variations = [
        "다녀올게", "갈게", "신청하고 싶어", "신청해줘", "준비해줘",
        "다녀올게요", "갈게요", "신청하고 싶어요", "신청해주세요", "준비해주세요",
        "다녀오겠습니다", "가겠습니다", "신청하고 싶습니다",
    ]
    
    # 기본 쿼리 추가
    for query in base_queries:
        samples.append({
            "query": query,
            "workflow_type": "business_trip",
            "confidence": 0.95,
            "label": 0  # business_trip = 0
        })
    
    # 템플릿 기반 변형 생성 (제한적으로 - 클래스 균형 유지)
    templates = [
        "{date} {duration} {destination}로 {purpose} {action}",
        "{date}부터 {duration} {destination}로 {purpose} {action}",
    ]
    
    # 제한적인 변형 생성 (클래스 불균형 방지)
    # None 샘플 수를 고려하여 business_trip 샘플 수 제한
    max_business_trip_variations = 200  # 최대 변형 수 제한
    
    variation_count = 0
    for template in templates:
        for date in date_variations[:4]:  # 4개만
            for duration in duration_variations[:2]:  # 2개만
                for destination in destination_variations[:4]:  # 4개만
                    for purpose in purpose_variations[:3]:  # 3개만
                        for action in action_variations[:2]:  # 2개만
                            if variation_count >= max_business_trip_variations:
                                break
                            try:
                                query = template.format(
                                    date=date,
                                    duration=duration,
                                    destination=destination,
                                    purpose=purpose,
                                    action=action
                                )
                                # 자연스러운 쿼리만 추가
                                if len(query) > 5 and len(query) < 50:
                                    samples.append({
                                        "query": query,
                                        "workflow_type": "business_trip",
                                        "confidence": 0.90,
                                        "label": 0
                                    })
                                    variation_count += 1
                            except KeyError:
                                continue
                        if variation_count >= max_business_trip_variations:
                            break
                    if variation_count >= max_business_trip_variations:
                        break
                if variation_count >= max_business_trip_variations:
                    break
            if variation_count >= max_business_trip_variations:
                break
        if variation_count >= max_business_trip_variations:
            break
    
    # 중복 제거
    seen = set()
    unique_samples = []
    for sample in samples:
        query = sample["query"]
        if query not in seen:
            seen.add(query)
            unique_samples.append(sample)
    
    return unique_samples


def generate_none_samples() -> List[Dict[str, Any]]:
    """워크플로우가 아닌 샘플을 생성합니다 (None 타입).
    
    Returns:
        None 타입 샘플 리스트
    """
    samples = []
    
    # workflows.yaml의 예시 기반 (워크플로우가 아닌 경우)
    none_queries = [
        # 취소 요청
        "출장 일정 취소해줘",
        "출장 취소해줘",
        "휴가 취소해줘",
        "출장 일정 취소",
        "휴가 일정 취소",
        
        # 삭제 요청
        "출장 일정 삭제해줘",
        "휴가 일정 삭제해줘",
        "출장 일정 지워줘",
        "휴가 일정 제거해줘",
        
        # 수정 요청
        "출장 일정 수정해줘",
        "휴가 일정 수정해줘",
        "출장 일정 변경해줘",
        "휴가 일정 고쳐줘",
        
        # 정보 질문
        "출장 규정 알려줘",
        "휴가 신청 방법은?",
        "출장 신청 절차 알려줘",
        "출장 신청하려면 어떻게 해야 해?",
        "휴가 신청하려면 어떻게 하나요?",
        "출장 신청서 양식 보여줘",
        "휴가 신청서 양식 알려줘",
        
        # 조회 요청
        "출장 일정 조회해줘",
        "휴가 일정 보여줘",
        "출장 일정 확인해줘",
        "휴가 일정 알려줘",
        
        # 일반 작업 (워크플로우가 아닌 단일 작업)
        "문서 작성해줘",
        "이메일 검색해줘",
        "특허 찾아줘",
        "회의실 예약해줘",
        "오늘 식단 뭐야?",
        "복리후생 정책 확인해줘",
    ]
    
    # 취소/삭제/수정 변형
    cancel_actions = ["취소", "취소해줘", "취소해주세요", "취소하고 싶어"]
    delete_actions = ["삭제", "삭제해줘", "지워줘", "제거해줘"]
    update_actions = ["수정", "수정해줘", "변경", "변경해줘", "고쳐줘"]
    
    cancel_subjects = ["출장 일정", "휴가 일정", "출장", "휴가", "일정"]
    delete_subjects = ["출장 일정", "휴가 일정", "출장", "휴가"]
    update_subjects = ["출장 일정", "휴가 일정", "출장", "휴가"]
    
    for subject in cancel_subjects:
        for action in cancel_actions:
            samples.append({
                "query": f"{subject} {action}",
                "workflow_type": None,
                "confidence": 0.95,
                "label": 1  # None = 1
            })
    
    for subject in delete_subjects:
        for action in delete_actions:
            samples.append({
                "query": f"{subject} {action}",
                "workflow_type": None,
                "confidence": 0.95,
                "label": 1
            })
    
    for subject in update_subjects:
        for action in update_actions:
            samples.append({
                "query": f"{subject} {action}",
                "workflow_type": None,
                "confidence": 0.95,
                "label": 1
            })
    
    # 정보 질문 변형
    info_questions = [
        "알려줘", "알려주세요", "방법은?", "규정은?", "어떻게 해야 해?",
        "어떻게 하나요?", "보여줘", "보여주세요", "찾아줘", "조회해줘",
    ]
    
    info_subjects = [
        "출장 규정", "휴가 규정", "출장 신청 방법", "휴가 신청 방법",
        "출장 신청 절차", "휴가 신청 절차", "출장 신청서 양식", "휴가 신청서 양식",
    ]
    
    for subject in info_subjects:
        for question in info_questions:
            samples.append({
                "query": f"{subject} {question}",
                "workflow_type": None,
                "confidence": 0.95,
                "label": 1
            })
    
    # 기본 쿼리 추가
    for query in none_queries:
        samples.append({
            "query": query,
            "workflow_type": None,
            "confidence": 0.95,
            "label": 1
        })
    
    # 일반 작업 샘플 추가
    general_tasks = [
        "문서 작성해줘", "문서 만들어줘", "보고서 작성해줘",
        "이메일 검색해줘", "이메일 찾아줘",
        "특허 검색해줘", "논문 찾아줘",
        "회의실 예약해줘", "회의실 조회해줘",
        "오늘 식단 뭐야?", "내일 점심 메뉴 알려줘",
        "복리후생 정책 확인해줘", "급여 외 혜택 알려줘",
    ]
    
    for query in general_tasks:
        samples.append({
            "query": query,
            "workflow_type": None,
            "confidence": 0.95,
            "label": 1
        })
    
    # 추가 일반 작업 변형 생성
    document_tasks = [
        "{doc_type} 작성해줘", "{doc_type} 만들어줘", "{doc_type} 생성해줘",
    ]
    doc_types = [
        "문서", "보고서", "제품개발 계획서", "발주서", "인사정보 요약 리포트",
        "월별 수익 분석 리포트", "업무 성과 평가서", "공급업체 평가서",
    ]
    
    for template in document_tasks:
        for doc_type in doc_types:
            samples.append({
                "query": template.format(doc_type=doc_type),
                "workflow_type": None,
                "confidence": 0.95,
                "label": 1
            })
    
    # 검색 요청 변형
    search_tasks = [
        "{item} 검색해줘", "{item} 찾아줘", "{item} 조회해줘",
    ]
    search_items = [
        "특허", "논문", "이메일", "메일", "이력서", "인재",
    ]
    
    for template in search_tasks:
        for item in search_items:
            samples.append({
                "query": template.format(item=item),
                "workflow_type": None,
                "confidence": 0.95,
                "label": 1
            })
    
    # 정보 조회 변형
    info_tasks = [
        "{item} 알려줘", "{item} 보여줘", "{item} 확인해줘",
    ]
    info_items = [
        "오늘 식단", "내일 점심 메뉴", "구내식당 메뉴", "복리후생 정책",
        "급여 외 혜택", "휴가규정", "근무제도", "보험 정보", "수당",
        "연차 사용 내역", "일정", "회의실 예약 가능한 시간",
    ]
    
    for template in info_tasks:
        for item in info_items:
            samples.append({
                "query": template.format(item=item),
                "workflow_type": None,
                "confidence": 0.95,
                "label": 1
            })
    
    # 중복 제거
    seen = set()
    unique_samples = []
    for sample in samples:
        query = sample["query"]
        if query not in seen:
            seen.add(query)
            unique_samples.append(sample)
    
    return unique_samples


def generate_edge_case_samples() -> List[Dict[str, Any]]:
    """경계 케이스 샘플을 생성합니다.
    
    Returns:
        경계 케이스 샘플 리스트
    """
    samples = []
    
    # 애매한 케이스들
    edge_cases = [
        # 워크플로우처럼 보이지만 질문형
        ("출장 신청하려면 어떻게 해야 해?", None, 1),
        ("휴가 신청 방법은?", None, 1),
        ("출장 신청 절차 알려줘", None, 1),
        
        # 워크플로우 실행 의도
        ("출장 신청하고 싶어", "business_trip", 0),
        ("휴가 신청하고 싶어", "business_trip", 0),
        ("출장 준비해줘", "business_trip", 0),
    ]
    
    for query, workflow_type, label in edge_cases:
        samples.append({
            "query": query,
            "workflow_type": workflow_type,
            "confidence": 0.85,
            "label": label
        })
    
    return samples


def save_dataset_csv(samples: List[Dict[str, Any]], output_path: Path) -> None:
    """데이터셋을 CSV 형식으로 저장합니다.
    
    Args:
        samples: 샘플 리스트
        output_path: 출력 파일 경로
    """
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["query", "workflow_type", "confidence", "label"])
        writer.writeheader()
        for sample in samples:
            writer.writerow({
                "query": sample["query"],
                "workflow_type": sample["workflow_type"] or "",
                "confidence": sample["confidence"],
                "label": sample["label"]
            })
    
    print(f"[CSV 데이터셋 저장 완료] {output_path} ({len(samples)}개 샘플)")


def save_dataset_json(samples: List[Dict[str, Any]], output_path: Path) -> None:
    """데이터셋을 JSON 형식으로 저장합니다.
    
    Args:
        samples: 샘플 리스트
        output_path: 출력 파일 경로
    """
    metadata = {
        "created_at": datetime.now().isoformat(),
        "total_samples": len(samples),
        "business_trip_samples": sum(1 for s in samples if s["label"] == 0),
        "none_samples": sum(1 for s in samples if s["label"] == 1),
        "description": "2단계 워크플로우 타입 분류 데이터셋 (KLUE-RoBERTa/SetFit 최적화)",
        "classification_type": "multiclass",
        "models": ["KLUE-RoBERTa", "SetFit"],
        "classes": {
            "0": "business_trip",
            "1": "none"
        }
    }
    
    data = {
        "metadata": metadata,
        "samples": samples
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"[JSON 데이터셋 저장 완료] {output_path} ({len(samples)}개 샘플)")


def save_huggingface_format(samples: List[Dict[str, Any]], output_dir: Path) -> None:
    """HuggingFace datasets 형식으로 저장합니다 (KLUE-RoBERTa 학습용).
    
    Args:
        samples: 샘플 리스트
        output_dir: 출력 디렉토리
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    jsonl_path = output_dir / "dataset.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for sample in samples:
            json.dump({
                "text": sample["query"],
                "label": sample["label"]
            }, f, ensure_ascii=False)
            f.write("\n")
    
    print(f"[HuggingFace 형식 저장 완료] {jsonl_path} ({len(samples)}개 샘플)")


def split_dataset(samples: List[Dict[str, Any]], train_ratio: float = 0.8, val_ratio: float = 0.1) -> tuple:
    """데이터셋을 학습/검증/테스트 세트로 분할합니다.
    
    Args:
        samples: 샘플 리스트
        train_ratio: 학습 세트 비율
        val_ratio: 검증 세트 비율
        
    Returns:
        (train_samples, val_samples, test_samples) 튜플
    """
    random.seed(42)
    random.shuffle(samples)
    
    total = len(samples)
    train_size = int(total * train_ratio)
    val_size = int(total * val_ratio)
    
    train_samples = samples[:train_size]
    val_samples = samples[train_size:train_size + val_size]
    test_samples = samples[train_size + val_size:]
    
    return train_samples, val_samples, test_samples


def generate_dataset() -> None:
    """데이터셋을 생성하고 저장합니다."""
    print("=" * 60)
    print("2단계 워크플로우 타입 분류 데이터셋 생성 시작")
    print("=" * 60)
    
    # 워크플로우 설정 로드
    workflows_config = load_workflows_config()
    workflows = workflows_config.get("workflows", {})
    
    print(f"\n워크플로우 타입: {list(workflows.keys())}")
    
    # 샘플 생성
    print("\n[워크플로우 타입 샘플 생성 중...]")
    business_trip_samples = generate_business_trip_samples()
    print(f"   [완료] {len(business_trip_samples)}개 business_trip 샘플 생성 완료")
    
    print("\n[None 타입 샘플 생성 중...]")
    none_samples = generate_none_samples()
    print(f"   [완료] {len(none_samples)}개 None 샘플 생성 완료")
    
    print("\n[경계 케이스 샘플 생성 중...]")
    edge_samples = generate_edge_case_samples()
    print(f"   [완료] {len(edge_samples)}개 경계 케이스 샘플 생성 완료")
    
    # 모든 샘플 합치기
    all_samples = business_trip_samples + none_samples + edge_samples
    
    # 데이터셋 통계
    print("\n[데이터셋 통계]")
    print(f"   총 샘플 수: {len(all_samples)}")
    business_trip_count = sum(1 for s in all_samples if s["label"] == 0)
    none_count = sum(1 for s in all_samples if s["label"] == 1)
    print(f"   business_trip: {business_trip_count}개 ({business_trip_count/len(all_samples)*100:.1f}%)")
    print(f"   None: {none_count}개 ({none_count/len(all_samples)*100:.1f}%)")
    
    # 클래스 균형 확인
    ratio = business_trip_count / none_count if none_count > 0 else 0
    if 0.5 <= ratio <= 2.0:
        print(f"   [확인] 클래스 균형이 적절합니다.")
    else:
        print(f"   [경고] 클래스 불균형이 있습니다 (비율: {ratio:.2f})")
    
    # 출력 디렉토리 생성
    output_dir = Path(__file__).parent.parent / "data" / "step2_workflow_type_dataset"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 전체 데이터셋 저장
    save_dataset_csv(all_samples, output_dir / "step2_workflow_type_dataset.csv")
    save_dataset_json(all_samples, output_dir / "step2_workflow_type_dataset.json")
    save_huggingface_format(all_samples, output_dir)
    
    # 데이터셋 분할
    print("\n[데이터셋 분할 중...]")
    train_samples, val_samples, test_samples = split_dataset(all_samples)
    
    # 분할된 데이터셋 저장
    save_dataset_csv(train_samples, output_dir / "train.csv")
    save_dataset_csv(val_samples, output_dir / "val.csv")
    save_dataset_csv(test_samples, output_dir / "test.csv")
    
    save_dataset_json(train_samples, output_dir / "train.json")
    save_dataset_json(val_samples, output_dir / "val.json")
    save_dataset_json(test_samples, output_dir / "test.json")
    
    # HuggingFace 형식으로 분할 저장
    (output_dir / "train").mkdir(exist_ok=True)
    (output_dir / "val").mkdir(exist_ok=True)
    (output_dir / "test").mkdir(exist_ok=True)
    
    save_huggingface_format(train_samples, output_dir / "train")
    save_huggingface_format(val_samples, output_dir / "val")
    save_huggingface_format(test_samples, output_dir / "test")
    
    print("\n[데이터셋 생성 완료]")
    print(f"   학습 세트: {len(train_samples)}개")
    print(f"   검증 세트: {len(val_samples)}개")
    print(f"   테스트 세트: {len(test_samples)}개")
    
    print(f"\n[저장 위치] {output_dir}")
    print("\n[생성된 파일]")
    print("   - CSV: train.csv, val.csv, test.csv")
    print("   - JSON: train.json, val.json, test.json")
    print("   - HuggingFace: train/dataset.jsonl, val/dataset.jsonl, test/dataset.jsonl")


if __name__ == "__main__":
    generate_dataset()

