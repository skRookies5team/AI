"""워크플로우 감지 ML 모델 학습용 데이터셋 생성 스크립트.

YAML 파일의 워크플로우 정의와 프롬프트를 기반으로 학습 데이터셋을 생성합니다.
"""

from __future__ import annotations

import json
import csv
import yaml
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
        workflows_config = yaml.safe_load(f)
    
    return workflows_config


def load_common_prompts() -> Dict[str, Any]:
    """공통 프롬프트를 YAML 파일에서 로드합니다.
    
    Returns:
        공통 프롬프트 딕셔너리
    """
    current_dir = Path(__file__).parent.parent
    yaml_path = current_dir / "agents" / "common_prompts.yaml"
    
    if not yaml_path.exists():
        raise FileNotFoundError(f"프롬프트 파일을 찾을 수 없습니다: {yaml_path}")
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        prompts = yaml.safe_load(f)
    
    return prompts


def generate_workflow_samples() -> List[Dict[str, Any]]:
    """워크플로우 요청 샘플을 생성합니다 (positive samples).
    
    Returns:
        워크플로우 요청 샘플 리스트
    """
    samples = []
    
    # business_trip 워크플로우 예시들
    workflow_queries = [
        # 구체적인 날짜와 목적지가 포함된 요청
        "다음 주 3일간 제주도로 워크샵 다녀올게",
        "12월 20일부터 22일까지 부산 출장 갈게",
        "내일부터 3일간 휴가 신청하고 싶어",
        "다음 주 월요일부터 수요일까지 서울 출장 갈게",
        "12월 25일부터 27일까지 제주도 휴가 다녀올게",
        "1월 5일부터 7일까지 대전 출장 갈게",
        "내일부터 5일간 부산 워크샵 다녀올게",
        "다음 달 1일부터 3일까지 제주도 휴가 신청하고 싶어",
        
        # 간단한 실행 의도 표현
        "출장 준비해줘",
        "출장 신청해줘",
        "휴가 다녀올게",
        "출장 갈게",
        "휴가 신청하고 싶어",
        "출장 다녀올게",
        "여행 갈게",
        "여행 다녀올게",
        
        # 다양한 표현 방식
        "다음 주 제주도 출장 갈게",
        "내일부터 휴가 신청해줘",
        "12월 말에 출장 다녀올게",
        "다음 달 초에 휴가 갈게",
        "이번 주 금요일부터 월요일까지 휴가 신청하고 싶어",
        "3일간 제주도로 출장 갈게",
        "일주일간 부산 출장 다녀올게",
        "2박 3일 제주도 휴가 신청해줘",
        
        # 사유가 포함된 요청
        "다음 주 3일간 제주도로 워크샵 다녀올게",
        "12월 20일부터 22일까지 부산 출장 갈게 (고객 미팅)",
        "내일부터 3일간 휴가 신청하고 싶어 (가족 여행)",
        "다음 주 월요일부터 수요일까지 서울 출장 갈게 (세미나 참석)",
        "12월 25일부터 27일까지 제주도 휴가 다녀올게 (크리스마스 휴가)",
        
        # 더 자연스러운 표현
        "출장 가야 해서 준비해줘",
        "휴가 신청하려고 해",
        "출장 다녀와야 해",
        "휴가 갈 예정이야",
        "출장 준비 부탁해",
        "휴가 신청 도와줘",
    ]
    
    for query in workflow_queries:
        samples.append({
            "query": query,
            "is_workflow": True,
            "workflow_type": "business_trip",
            "confidence": 0.95,  # 높은 신뢰도
            "domain": None,  # 워크플로우는 여러 도메인을 거침
            "label": 1  # ML 모델용 바이너리 레이블
        })
    
    return samples


def generate_regular_task_samples() -> List[Dict[str, Any]]:
    """일반 작업 요청 샘플을 생성합니다 (negative samples).
    
    Returns:
        일반 작업 요청 샘플 리스트
    """
    samples = []
    
    # Schedule 도메인 - 질문형 요청들
    schedule_queries = [
        "출장 규정 알려줘",
        "휴가 신청 방법은?",
        "출장 관련 정책 알려줘",
        "출장 신청서 양식 보여줘",
        "출장 신청 절차 알려줘",
        "출장 신청하려면 어떻게 해야 해?",
        "휴가 신청하려면 어떻게 하나요?",
        "연차 신청 방법 알려줘",
        "회의실 예약 방법은?",
        "일정 조회해줘",
        "내 일정 보여줘",
        "이번 달 연차 사용 내역 알려줘",
        "다음 주 회의실 예약 가능한 시간 보여줘",
    ]
    
    for query in schedule_queries:
        samples.append({
            "query": query,
            "is_workflow": False,
            "workflow_type": None,
            "confidence": 0.95,
            "domain": "schedule",
            "label": 0
        })
    
    # Document 도메인
    document_queries = [
        "문서 작성해줘",
        "보고서 작성해줘",
        "제품개발 계획서 작성해줘",
        "발주서 템플릿 보여줘",
        "문서 양식 알려줘",
        "문서 목록 보여줘",
        "문서 검토 일정 알려줘",
        "문서 확인 일정은 언제야?",
        "문서 관련 일정 보여줘",
    ]
    
    for query in document_queries:
        samples.append({
            "query": query,
            "is_workflow": False,
            "workflow_type": None,
            "confidence": 0.95,
            "domain": "document",
            "label": 0
        })
    
    # QA 도메인
    qa_queries = [
        "메뉴얼에서 연차 정보 알려줘",
        "우리 회사 메뉴얼에서 연차사용 정보 알려줘",
        "매뉴얼 보여줘",
        "오늘 식단 뭐야?",
        "내일 점심 메뉴 알려줘",
        "다이어트 중인데 내일 식단을 어떻게 먹어야해?",
        "구내식당 메뉴 조회",
        "복리후생 정책 알려줘",
        "급여 외 혜택에 대해 알려줘",
        "휴가규정 알려줘",
        "근무제도에 대해 알려줘",
    ]
    
    for query in qa_queries:
        samples.append({
            "query": query,
            "is_workflow": False,
            "workflow_type": None,
            "confidence": 0.95,
            "domain": "qa",
            "label": 0
        })
    
    # Email 도메인
    email_queries = [
        "이메일에서 육아휴직 관련 내용 찾아줘",
        "메일에서 특정 키워드 검색해줘",
        "이메일 검색해줘",
        "메일함에서 최근 메일 보여줘",
        "이메일 답장문 생성해줘",
        "메일 자동응답 설정해줘",
    ]
    
    for query in email_queries:
        samples.append({
            "query": query,
            "is_workflow": False,
            "workflow_type": None,
            "confidence": 0.95,
            "domain": "email",
            "label": 0
        })
    
    # Tech 도메인
    tech_queries = [
        "특허 검색해줘",
        "논문 찾아줘",
        "바이러스 관련 특허 검색해줘",
        "KISTI에서 논문 검색해줘",
        "ScienceON에서 자료 찾아줘",
        "특허 정보 알려줘",
        "논문 다운로드해줘",
    ]
    
    for query in tech_queries:
        samples.append({
            "query": query,
            "is_workflow": False,
            "workflow_type": None,
            "confidence": 0.95,
            "domain": "tech",
            "label": 0
        })
    
    # Recruitment 도메인
    recruitment_queries = [
        "인재 추천해줘",
        "Python 개발자 추천해줘",
        "이력서 검색해줘",
        "지원자 찾아줘",
        "채용 관련 정보 알려줘",
        "인재 채용 프로세스 알려줘",
    ]
    
    for query in recruitment_queries:
        samples.append({
            "query": query,
            "is_workflow": False,
            "workflow_type": None,
            "confidence": 0.95,
            "domain": "recruitment",
            "label": 0
        })
    
    # 경계 케이스 - 워크플로우처럼 보이지만 질문형
    edge_case_queries = [
        "출장 신청하려면 어떻게 해야 해?",
        "휴가 신청하려면 어떻게 하나요?",
        "출장 준비는 어떻게 하나요?",
        "휴가 신청 절차 알려줘",
        "출장 가려면 뭐가 필요한가요?",
        "휴가 다니려면 어떻게 해야 하나요?",
    ]
    
    for query in edge_case_queries:
        samples.append({
            "query": query,
            "is_workflow": False,
            "workflow_type": None,
            "confidence": 0.90,  # 약간 낮은 신뢰도 (경계 케이스)
            "domain": "qa",  # 질문이므로 qa로 분류
            "label": 0
        })
    
    return samples


def generate_additional_variations(base_samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """기본 샘플을 기반으로 다양한 변형을 생성합니다.
    
    Args:
        base_samples: 기본 샘플 리스트
        
    Returns:
        변형된 샘플 리스트
    """
    variations = []
    
    # 날짜 표현 변형
    date_variations = [
        ("다음 주", "내일", "이번 주", "다음 달", "이번 달"),
        ("3일간", "일주일간", "2박 3일", "5일간", "한 달간"),
    ]
    
    # 목적지 변형
    destination_variations = [
        "제주도", "부산", "서울", "대전", "인천", "광주", "대구", "울산"
    ]
    
    # 워크플로우 요청 변형 생성
    workflow_templates = [
        "{date} {destination}로 {purpose} {action}",
        "{date} {destination} {purpose} {action}",
        "{action} {date} {destination} {purpose}",
    ]
    
    purposes = ["출장", "휴가", "워크샵", "여행"]
    actions = ["갈게", "다녀올게", "신청하고 싶어", "신청해줘", "준비해줘"]
    
    for template in workflow_templates:
        for date_expr in ["다음 주", "내일부터", "12월 20일부터"]:
            for destination in destination_variations[:3]:  # 일부만 사용
                for purpose in purposes[:2]:  # 일부만 사용
                    for action in actions[:2]:  # 일부만 사용
                        query = template.format(
                            date=date_expr,
                            destination=destination,
                            purpose=purpose,
                            action=action
                        )
                        variations.append({
                            "query": query,
                            "is_workflow": True,
                            "workflow_type": "business_trip",
                            "confidence": 0.90,
                            "domain": None,
                            "label": 1
                        })
    
    return variations


def save_dataset_csv(samples: List[Dict[str, Any]], output_path: Path) -> None:
    """데이터셋을 CSV 형식으로 저장합니다.
    
    Args:
        samples: 샘플 리스트
        output_path: 출력 파일 경로
    """
    fieldnames = ["query", "is_workflow", "workflow_type", "confidence", "domain", "label"]
    
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(samples)
    
    print(f"[CSV 데이터셋 저장 완료] {output_path} ({len(samples)}개 샘플)")


def save_dataset_json(samples: List[Dict[str, Any]], output_path: Path) -> None:
    """데이터셋을 JSON 형식으로 저장합니다.
    
    Args:
        samples: 샘플 리스트
        output_path: 출력 파일 경로
    """
    dataset = {
        "metadata": {
            "created_at": datetime.now().isoformat(),
            "total_samples": len(samples),
            "workflow_samples": sum(1 for s in samples if s["is_workflow"]),
            "regular_task_samples": sum(1 for s in samples if not s["is_workflow"]),
            "workflow_types": list(set(s["workflow_type"] for s in samples if s["workflow_type"])),
            "domains": list(set(s["domain"] for s in samples if s["domain"])),
        },
        "samples": samples
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    
    print(f"[JSON 데이터셋 저장 완료] {output_path} ({len(samples)}개 샘플)")


def generate_dataset() -> None:
    """워크플로우 감지 데이터셋을 생성합니다."""
    print("[워크플로우 감지 데이터셋 생성 시작]")
    
    # 출력 디렉토리 생성
    output_dir = Path(__file__).parent.parent / "data" / "workflow_dataset"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 샘플 생성
    print("[워크플로우 요청 샘플 생성 중...]")
    workflow_samples = generate_workflow_samples()
    print(f"   [완료] {len(workflow_samples)}개 워크플로우 샘플 생성 완료")
    
    print("[일반 작업 요청 샘플 생성 중...]")
    regular_samples = generate_regular_task_samples()
    print(f"   [완료] {len(regular_samples)}개 일반 작업 샘플 생성 완료")
    
    # 추가 변형 생성 (선택적)
    print("[추가 변형 샘플 생성 중...]")
    variations = generate_additional_variations(workflow_samples)
    print(f"   [완료] {len(variations)}개 변형 샘플 생성 완료")
    
    # 모든 샘플 합치기
    all_samples = workflow_samples + regular_samples + variations
    
    # 통계 출력
    print("\n[데이터셋 통계]")
    print(f"   총 샘플 수: {len(all_samples)}")
    print(f"   워크플로우 샘플: {sum(1 for s in all_samples if s['is_workflow'])}")
    print(f"   일반 작업 샘플: {sum(1 for s in all_samples if not s['is_workflow'])}")
    print(f"   워크플로우 타입: {set(s['workflow_type'] for s in all_samples if s['workflow_type'])}")
    print(f"   도메인: {set(s['domain'] for s in all_samples if s['domain'])}")
    
    # CSV 저장
    csv_path = output_dir / "workflow_detection_dataset.csv"
    save_dataset_csv(all_samples, csv_path)
    
    # JSON 저장
    json_path = output_dir / "workflow_detection_dataset.json"
    save_dataset_json(all_samples, json_path)
    
    # 학습/검증/테스트 분할 (80/10/10)
    print("\n[데이터셋 분할 중...]")
    import random
    random.seed(42)  # 재현성을 위한 시드 설정
    random.shuffle(all_samples)
    
    total = len(all_samples)
    train_size = int(total * 0.8)
    val_size = int(total * 0.1)
    
    train_samples = all_samples[:train_size]
    val_samples = all_samples[train_size:train_size + val_size]
    test_samples = all_samples[train_size + val_size:]
    
    # 분할된 데이터셋 저장
    save_dataset_csv(train_samples, output_dir / "train.csv")
    save_dataset_csv(val_samples, output_dir / "val.csv")
    save_dataset_csv(test_samples, output_dir / "test.csv")
    
    save_dataset_json(train_samples, output_dir / "train.json")
    save_dataset_json(val_samples, output_dir / "val.json")
    save_dataset_json(test_samples, output_dir / "test.json")
    
    print(f"\n[데이터셋 생성 완료]")
    print(f"   학습 세트: {len(train_samples)}개")
    print(f"   검증 세트: {len(val_samples)}개")
    print(f"   테스트 세트: {len(test_samples)}개")
    print(f"\n[저장 위치] {output_dir}")


if __name__ == "__main__":
    generate_dataset()

