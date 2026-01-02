"""1단계 워크플로우 vs 일반 요청 분류 ML 모델 학습용 데이터셋 생성 스크립트.

이 데이터셋은 사용자 요청을 워크플로우 실행 요청과 일반 작업 요청으로 분류하기 위한 것입니다.
워크플로우 타입은 중요하지 않으며, 워크플로우인지만 판단합니다.
"""

from __future__ import annotations

import json
import csv
import yaml
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime


def load_workflow_classification_config() -> Dict[str, Any]:
    """워크플로우 분류 설정을 YAML 파일에서 로드합니다.
    
    Returns:
        워크플로우 분류 설정 딕셔너리
    """
    current_dir = Path(__file__).parent.parent
    yaml_path = current_dir / "agents" / "workflow_classification.yaml"
    
    if not yaml_path.exists():
        raise FileNotFoundError(f"워크플로우 분류 파일을 찾을 수 없습니다: {yaml_path}")
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    return config


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


def generate_workflow_samples() -> List[Dict[str, Any]]:
    """워크플로우 실행 요청 샘플을 생성합니다 (positive samples).
    
    복합 워크플로우 실행 의도가 명확한 요청들만 포함합니다.
    
    Returns:
        워크플로우 요청 샘플 리스트
    """
    samples = []
    
    # workflow_classification.yaml의 예시 기반
    base_workflow_queries = [
        "다음 주 3일간 제주도로 워크샵 다녀올게",
        "12월 20일부터 22일까지 부산 출장 갈게",
        "내일부터 3일간 휴가 신청하고 싶어",
        "출장 신청해줘",
        "휴가 다녀올게",
    ]
    
    # 추가 워크플로우 요청 예시
    additional_workflow_queries = [
        # 구체적인 날짜와 목적지가 포함된 요청
        "다음 주 월요일부터 수요일까지 서울 출장 갈게",
        "12월 25일부터 27일까지 제주도 휴가 다녀올게",
        "1월 5일부터 7일까지 대전 출장 갈게",
        "내일부터 5일간 부산 워크샵 다녀올게",
        "다음 달 1일부터 3일까지 제주도 휴가 신청하고 싶어",
        "이번 주 금요일부터 월요일까지 휴가 갈게",
        "3일간 제주도로 출장 갈게",
        "일주일간 부산 출장 다녀올게",
        "2박 3일 제주도 휴가 신청해줘",
        
        # 간단한 실행 의도 표현
        "출장 준비해줘",
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
        
        # 다양한 목적지와 기간
        "다음 주 인천 출장 갈게",
        "내일부터 광주 워크샵 다녀올게",
        "12월 대구 출장 갈게",
        "울산 출장 다녀올게",
        "부산 휴가 신청하고 싶어",
    ]
    
    all_workflow_queries = base_workflow_queries + additional_workflow_queries
    
    for query in all_workflow_queries:
        samples.append({
            "query": query,
            "is_workflow": True,
            "confidence": 0.95,  # 높은 신뢰도
            "label": 1  # ML 모델용 바이너리 레이블
        })
    
    return samples


def generate_regular_task_samples() -> List[Dict[str, Any]]:
    """일반 작업 요청 샘플을 생성합니다 (negative samples).
    
    취소/삭제/수정, 정보 조회, 질문형, 단일 작업 요청들을 포함합니다.
    
    Returns:
        일반 작업 요청 샘플 리스트
    """
    samples = []
    
    # workflow_classification.yaml의 예시 기반
    base_regular_queries = [
        "출장 일정 취소해줘",
        "출장 규정 알려줘",
        "휴가 신청 방법은?",
        "출장 일정 수정해줘",
        "출장 일정 삭제해줘",
        "내일 연차 신청",
        "회의실 예약해줘",
        "이메일에서 육아휴직 관련 내용 찾아줘",
        "문서 작성해줘",
        "오늘 식단 뭐야?",
    ]
    
    # 취소 요청
    cancel_queries = [
        "출장 일정 취소해줘",
        "출장 취소",
        "휴가 취소해줘",
        "휴가 일정 취소",
        "출장 신청 취소해줘",
        "일정 취소해줘",
        "예약 취소해줘",
        "회의실 예약 취소",
        "출장 예약 취소해줘",
    ]
    
    # 삭제 요청
    delete_queries = [
        "출장 일정 삭제해줘",
        "일정 삭제",
        "출장 삭제해줘",
        "휴가 일정 삭제",
        "일정 지워줘",
        "출장 일정 제거해줘",
    ]
    
    # 수정 요청
    update_queries = [
        "출장 일정 수정해줘",
        "일정 수정",
        "출장 수정해줘",
        "휴가 일정 변경해줘",
        "일정 고쳐줘",
        "출장 일정 변경해줘",
        "일정 업데이트해줘",
    ]
    
    # 정보 질문 (질문형)
    info_question_queries = [
        "출장 규정 알려줘",
        "휴가 신청 방법은?",
        "출장 신청 절차 알려줘",
        "출장 신청하려면 어떻게 해야 해?",
        "휴가 신청하려면 어떻게 하나요?",
        "출장 준비는 어떻게 하나요?",
        "휴가 신청 절차 알려줘",
        "출장 가려면 뭐가 필요한가요?",
        "휴가 다니려면 어떻게 해야 하나요?",
        "연차 신청 방법은?",
        "회의실 예약 방법 알려줘",
        "문서 작성 방법은?",
    ]
    
    # 정보 조회 요청
    info_request_queries = [
        "출장 신청서 양식 보여줘",
        "휴가 신청서 보여줘",
        "일정 조회해줘",
        "내 일정 보여줘",
        "이번 달 연차 사용 내역 알려줘",
        "다음 주 회의실 예약 가능한 시간 보여줘",
        "출장 규정 보여줘",
        "휴가 규정 알려줘",
    ]
    
    # 단일 작업 요청 (워크플로우가 아닌 단일 작업)
    single_task_queries = [
        "내일 연차 신청",
        "회의실 예약해줘",
        "이메일에서 육아휴직 관련 내용 찾아줘",
        "문서 작성해줘",
        "보고서 작성해줘",
        "제품개발 계획서 작성해줘",
        "특허 검색해줘",
        "논문 찾아줘",
        "인재 추천해줘",
        "이력서 검색해줘",
        "오늘 식단 뭐야?",
        "내일 점심 메뉴 알려줘",
        "메뉴얼에서 연차 정보 알려줘",
        "복리후생 정책 알려줘",
        # 추가 단일 작업 예시
        "연차 신청해줘",
        "반차 신청",
        "병가 신청해줘",
        "경조사 신청",
        "회의실 예약",
        "회의실 예약 가능한 시간 보여줘",
        "이메일 검색해줘",
        "메일에서 특정 키워드 찾아줘",
        "이메일 답장문 생성해줘",
        "메일 자동응답 설정해줘",
        "발주서 작성해줘",
        "인사정보 요약 리포트 작성",
        "월별 수익 분석 리포트 작성해줘",
        "업무 성과 평가서 작성",
        "공급업체 평가서 작성해줘",
        "계약 갱신 보고서 작성",
        "활동기준 원가 계산서 작성해줘",
        "프로젝트 일정표 작성",
        "제품 비교표 작성해줘",
        "바이러스 관련 특허 검색해줘",
        "KISTI에서 논문 검색",
        "ScienceON에서 자료 찾아줘",
        "특허 정보 알려줘",
        "논문 다운로드해줘",
        "Python 개발자 추천해줘",
        "지원자 찾아줘",
        "채용 관련 정보 알려줘",
        "인재 채용 프로세스 알려줘",
        "구내식당 메뉴 조회",
        "다이어트 중인데 내일 식단을 어떻게 먹어야해?",
        "우리 회사 메뉴얼에서 연차사용 정보 알려줘",
        "매뉴얼 보여줘",
        "급여 외 혜택에 대해 알려줘",
        "휴가규정 알려줘",
        "근무제도에 대해 알려줘",
        "보험 정보 알려줘",
        "수당 관련 정보 알려줘",
        
        # 문서 작성 요청 (워크플로우 오분류 방지용)
        "2024년 1분기 부품 발주서 요구 사항:",
        "발주서 작성해줘",
        "구매 발주서 만들어줘",
        "부품 발주서 작성",
        "요구사항: 발주번호 PO-2024-001",
        "제목: 2024년 1분기 부품 발주서",
        "다음 요구사항으로 발주서 작성해줘",
        "아래 내용으로 구매 주문서 작성",
        "발주서 양식으로 문서 생성",
        "제품개발 계획서 작성해줘",
        "신제품 개발 계획서 만들어줘",
        "프로젝트 일정표 작성",
        "월별 수익 분석 리포트 생성",
        "공급업체 평가서 작성해줘",
        "계약 갱신 보고서 만들어줘",
        "인사정보 요약 리포트 작성",
        "활동기준 원가 계산서 생성",
        "제품 비교표 작성해줘",
        "문서 좀 만들어줘",
        "이 내용으로 보고서 작성해줘",
        "기획서 작성 요청",
        "제안서 만들어줘",
    ]
    
    # 추가 일반 작업 변형 생성
    additional_regular_variations = []
    
    # 취소/삭제/수정 변형
    cancel_delete_update_variations = [
        f"{item} {action}"
        for item in ["출장", "휴가", "일정", "예약"]
        for action in ["취소해줘", "취소", "삭제해줘", "삭제", "수정해줘", "수정", "변경해줘", "변경"]
    ]
    
    # 정보 질문 변형
    info_question_variations = [
        f"{item} {question}"
        for item in ["출장", "휴가", "연차", "회의실", "문서"]
        for question in ["규정 알려줘", "방법은?", "절차 알려줘", "어떻게 해야 해?", "어떻게 하나요?"]
    ]
    
    # 조회 요청 변형
    info_request_variations = [
        f"{item} {request}"
        for item in ["출장", "휴가", "일정", "문서", "이메일"]
        for request in ["보여줘", "알려줘", "조회해줘", "찾아줘", "확인해줘"]
    ]
    
    # 모든 일반 작업 쿼리 합치기
    all_regular_queries = (
        base_regular_queries +
        cancel_queries +
        delete_queries +
        update_queries +
        info_question_queries +
        info_request_queries +
        single_task_queries +
        cancel_delete_update_variations[:20] +  # 일부만 사용
        info_question_variations[:20] +  # 일부만 사용
        info_request_variations[:20]  # 일부만 사용
    )
    
    for query in all_regular_queries:
        samples.append({
            "query": query,
            "is_workflow": False,
            "confidence": 0.95,  # 높은 신뢰도
            "label": 0  # ML 모델용 바이너리 레이블
        })
    
    return samples


def generate_edge_case_samples() -> List[Dict[str, Any]]:
    """경계 케이스 샘플을 생성합니다.
    
    워크플로우처럼 보이지만 일반 작업인 경우, 또는 그 반대의 경우를 포함합니다.
    
    Returns:
        경계 케이스 샘플 리스트
    """
    samples = []
    
    # 워크플로우처럼 보이지만 질문형 (일반 작업)
    workflow_like_but_question = [
        "출장 신청하려면 어떻게 해야 해?",
        "휴가 신청하려면 어떻게 하나요?",
        "출장 준비는 어떻게 하나요?",
        "휴가 신청 절차 알려줘",
        "출장 가려면 뭐가 필요한가요?",
        "휴가 다니려면 어떻게 해야 하나요?",
        "출장 다녀오려면 어떻게 해야 하나요?",
    ]
    
    for query in workflow_like_but_question:
        samples.append({
            "query": query,
            "is_workflow": False,
            "confidence": 0.90,  # 약간 낮은 신뢰도 (경계 케이스)
            "label": 0
        })
    
    # 일반 작업처럼 보이지만 워크플로우 (실행 의도가 명확)
    regular_like_but_workflow = [
        "출장 신청해줘",  # 단순해 보이지만 워크플로우
        "휴가 신청해줘",  # 단순해 보이지만 워크플로우
        "출장 준비해줘",  # 단순해 보이지만 워크플로우
    ]
    
    for query in regular_like_but_workflow:
        samples.append({
            "query": query,
            "is_workflow": True,
            "confidence": 0.90,  # 약간 낮은 신뢰도 (경계 케이스)
            "label": 1
        })
    
    return samples


def generate_additional_variations(base_workflow_samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """기본 워크플로우 샘플을 기반으로 다양한 변형을 생성합니다.
    
    Args:
        base_workflow_samples: 기본 워크플로우 샘플 리스트
        
    Returns:
        변형된 샘플 리스트
    """
    variations = []
    
    # 워크플로우 변형은 제한적으로 생성 (클래스 불균형 방지)
    # 날짜 표현 변형
    date_expressions = [
        "다음 주", "내일부터", "이번 주", "다음 달"
    ]
    
    # 기간 표현 변형
    duration_expressions = [
        "3일간", "일주일간", "2박 3일"
    ]
    
    # 목적지 변형
    destinations = [
        "제주도", "부산", "서울", "대전"
    ]
    
    # 목적 변형
    purposes = ["출장", "휴가"]
    
    # 실행 동사 변형
    action_verbs = [
        "갈게", "다녀올게", "신청하고 싶어"
    ]
    
    # 템플릿 기반 변형 생성 (제한적으로)
    templates = [
        "{date} {duration} {destination}로 {purpose} {action}",
        "{date} {destination} {purpose} {action}",
        "{purpose} {action} {date} {destination}",
    ]
    
    for template in templates:
        for date_expr in date_expressions[:3]:  # 약간 확장
            for duration in duration_expressions[:2]:  # 제한적으로
                for destination in destinations[:3]:  # 약간 확장
                    for purpose in purposes[:2]:  # 약간 확장
                        for action in action_verbs[:3]:  # 약간 확장
                            try:
                                query = template.format(
                                    date=date_expr,
                                    duration=duration,
                                    destination=destination,
                                    purpose=purpose,
                                    action=action
                                )
                                variations.append({
                                    "query": query,
                                    "is_workflow": True,
                                    "confidence": 0.90,
                                    "label": 1
                                })
                            except KeyError:
                                # 템플릿에 없는 변수는 건너뛰기
                                continue
    
    return variations


def save_dataset_csv(samples: List[Dict[str, Any]], output_path: Path) -> None:
    """데이터셋을 CSV 형식으로 저장합니다.
    
    Args:
        samples: 샘플 리스트
        output_path: 출력 파일 경로
    """
    fieldnames = ["query", "is_workflow", "confidence", "label"]
    
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
            "description": "1단계 워크플로우 vs 일반 요청 분류 데이터셋",
            "classification_type": "binary",  # 워크플로우인지만 판단
        },
        "samples": samples
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    
    print(f"[JSON 데이터셋 저장 완료] {output_path} ({len(samples)}개 샘플)")


def generate_dataset() -> None:
    """1단계 워크플로우 vs 일반 요청 분류 데이터셋을 생성합니다."""
    print("[1단계 워크플로우 vs 일반 요청 분류 데이터셋 생성 시작]")
    
    # 출력 디렉토리 생성
    output_dir = Path(__file__).parent.parent / "data" / "step1_classification_dataset"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 샘플 생성
    print("[워크플로우 요청 샘플 생성 중...]")
    workflow_samples = generate_workflow_samples()
    print(f"   [완료] {len(workflow_samples)}개 워크플로우 샘플 생성 완료")
    
    print("[일반 작업 요청 샘플 생성 중...]")
    regular_samples = generate_regular_task_samples()
    print(f"   [완료] {len(regular_samples)}개 일반 작업 샘플 생성 완료")
    
    print("[경계 케이스 샘플 생성 중...]")
    edge_case_samples = generate_edge_case_samples()
    print(f"   [완료] {len(edge_case_samples)}개 경계 케이스 샘플 생성 완료")
    
    print("[추가 변형 샘플 생성 중...]")
    variations = generate_additional_variations(workflow_samples)
    print(f"   [완료] {len(variations)}개 변형 샘플 생성 완료")
    
    # 모든 샘플 합치기
    all_samples = workflow_samples + regular_samples + edge_case_samples + variations
    
    # 통계 출력
    print("\n[데이터셋 통계]")
    print(f"   총 샘플 수: {len(all_samples)}")
    print(f"   워크플로우 샘플: {sum(1 for s in all_samples if s['is_workflow'])}")
    print(f"   일반 작업 샘플: {sum(1 for s in all_samples if not s['is_workflow'])}")
    print(f"   워크플로우 비율: {sum(1 for s in all_samples if s['is_workflow']) / len(all_samples) * 100:.1f}%")
    print(f"   일반 작업 비율: {sum(1 for s in all_samples if not s['is_workflow']) / len(all_samples) * 100:.1f}%")
    
    # CSV 저장
    csv_path = output_dir / "step1_classification_dataset.csv"
    save_dataset_csv(all_samples, csv_path)
    
    # JSON 저장
    json_path = output_dir / "step1_classification_dataset.json"
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

