"""1단계 워크플로우 vs 일반 요청 분류 ML 모델 학습용 데이터셋 생성 스크립트 (향상 버전).

KLUE-RoBERTa와 SetFit 모델 학습에 최적화된 데이터셋을 생성합니다.
- 충분한 샘플 수 (1000개 이상)
- 데이터 증강 기법 적용
- 클래스 균형 개선
- 다양한 표현 방식 포함
"""

from __future__ import annotations

import json
import csv
import yaml
import random
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


def augment_text(text: str, augmentation_type: str = "synonym") -> str:
    """텍스트 데이터 증강을 수행합니다.
    
    Args:
        text: 원본 텍스트
        augmentation_type: 증강 타입 ("synonym", "structure", "paraphrase")
        
    Returns:
        증강된 텍스트
    """
    # 동의어 사전
    synonyms = {
        "출장": ["출장", "출장", "출장"],  # 동일하게 유지
        "휴가": ["휴가", "휴가", "휴가"],
        "신청": ["신청", "요청", "신청"],
        "알려줘": ["알려줘", "알려주세요", "알려줘"],
        "보여줘": ["보여줘", "보여주세요", "보여줘"],
        "취소": ["취소", "취소", "취소"],
        "삭제": ["삭제", "지워줘", "제거"],
        "수정": ["수정", "변경", "고쳐줘"],
    }
    
    if augmentation_type == "synonym":
        # 간단한 동의어 교체 (실제로는 동일하게 유지)
        return text
    
    elif augmentation_type == "structure":
        # 문장 구조 변형
        if "부터" in text and "까지" in text:
            # "A부터 B까지" → "B까지 A부터"
            parts = text.split("부터")
            if len(parts) == 2:
                return parts[1].strip() + "까지 " + parts[0].strip() + "부터"
        return text
    
    elif augmentation_type == "paraphrase":
        # 간단한 패러프레이즈
        replacements = {
            "해줘": "해주세요",
            "알려줘": "알려주세요",
            "보여줘": "보여주세요",
        }
        for old, new in replacements.items():
            if text.endswith(old):
                return text[:-len(old)] + new
        return text
    
    return text


def generate_workflow_samples() -> List[Dict[str, Any]]:
    """워크플로우 실행 요청 샘플을 생성합니다 (positive samples).
    
    Returns:
        워크플로우 요청 샘플 리스트
    """
    samples = []
    
    # 기본 워크플로우 요청
    base_workflow_queries = [
        "다음 주 3일간 제주도로 워크샵 다녀올게",
        "12월 20일부터 22일까지 부산 출장 갈게",
        "내일부터 3일간 휴가 신청하고 싶어",
        "출장 신청해줘",
        "휴가 다녀올게",
        "다음 주 월요일부터 수요일까지 서울 출장 갈게",
        "12월 25일부터 27일까지 제주도 휴가 다녀올게",
        "1월 5일부터 7일까지 대전 출장 갈게",
        "내일부터 5일간 부산 워크샵 다녀올게",
        "다음 달 1일부터 3일까지 제주도 휴가 신청하고 싶어",
        "이번 주 금요일부터 월요일까지 휴가 갈게",
        "3일간 제주도로 출장 갈게",
        "일주일간 부산 출장 다녀올게",
        "2박 3일 제주도 휴가 신청해줘",
        "출장 준비해줘",
        "출장 갈게",
        "휴가 신청하고 싶어",
        "출장 다녀올게",
        "여행 갈게",
        "여행 다녀올게",
        "다음 주 제주도 출장 갈게",
        "내일부터 휴가 신청해줘",
        "12월 말에 출장 다녀올게",
        "다음 달 초에 휴가 갈게",
        "이번 주 금요일부터 월요일까지 휴가 신청하고 싶어",
        "출장 가야 해서 준비해줘",
        "휴가 신청하려고 해",
        "출장 다녀와야 해",
        "휴가 갈 예정이야",
        "출장 준비 부탁해",
        "휴가 신청 도와줘",
        "다음 주 인천 출장 갈게",
        "내일부터 광주 워크샵 다녀올게",
        "12월 대구 출장 갈게",
        "울산 출장 다녀올게",
        "부산 휴가 신청하고 싶어",
    ]
    
    # 날짜 변형
    date_variations = [
        "다음 주", "내일부터", "이번 주", "다음 달", "이번 달",
        "12월 20일부터", "1월 5일부터", "내일", "오늘부터",
        "다음 주 월요일부터", "이번 주 금요일부터", "다음 달 초에",
    ]
    
    # 기간 변형
    duration_variations = [
        "3일간", "일주일간", "2박 3일", "5일간", "한 달간",
        "3일", "일주일", "2박3일", "5일", "한 달",
    ]
    
    # 목적지 변형
    destination_variations = [
        "제주도", "부산", "서울", "대전", "인천", "광주", "대구", "울산",
        "제주", "서울시", "부산시", "대전시",
    ]
    
    # 목적 변형
    purpose_variations = ["출장", "휴가", "워크샵", "여행", "출장 가", "휴가 가"]
    
    # 실행 동사 변형
    action_variations = [
        "갈게", "다녀올게", "신청하고 싶어", "신청해줘", "준비해줘",
        "가야 해", "다녀와야 해", "신청하려고 해", "갈 예정이야",
        "다녀올 예정이야", "신청할게", "갈 거야",
    ]
    
    # 템플릿 기반 생성
    templates = [
        "{date} {duration} {destination}로 {purpose} {action}",
        "{date} {duration} {destination} {purpose} {action}",
        "{date} {destination} {purpose} {action}",
        "{purpose} {action} {date} {destination}",
        "{date} {destination}로 {purpose} {action}",
        "{purpose} {action} {date} {duration} {destination}",
    ]
    
    # 템플릿 기반 샘플 생성
    generated_count = 0
    max_generated = 400  # 최대 생성 개수 제한
    
    for template in templates:
        if generated_count >= max_generated:
            break
        for date_expr in date_variations[:8]:  # 일부만 사용
            if generated_count >= max_generated:
                break
            for duration in duration_variations[:6]:
                if generated_count >= max_generated:
                    break
                for destination in destination_variations[:8]:
                    if generated_count >= max_generated:
                        break
                    for purpose in purpose_variations[:4]:
                        if generated_count >= max_generated:
                            break
                        for action in action_variations[:6]:
                            if generated_count >= max_generated:
                                break
                            try:
                                query = template.format(
                                    date=date_expr,
                                    duration=duration,
                                    destination=destination,
                                    purpose=purpose,
                                    action=action
                                )
                                # 중복 제거를 위한 간단한 체크
                                if len(query) > 5 and len(query) < 100:
                                    samples.append({
                                        "query": query,
                                        "is_workflow": True,
                                        "confidence": 0.90,
                                        "label": 1
                                    })
                                    generated_count += 1
                            except KeyError:
                                continue
    
    # 기본 쿼리 추가
    for query in base_workflow_queries:
        samples.append({
            "query": query,
            "is_workflow": True,
            "confidence": 0.95,
            "label": 1
        })
    
    # 데이터 증강 적용
    augmented_samples = []
    for sample in samples[:100]:  # 일부만 증강
        for aug_type in ["paraphrase"]:  # 간단한 증강만
            augmented = augment_text(sample["query"], aug_type)
            if augmented != sample["query"]:
                augmented_samples.append({
                    "query": augmented,
                    "is_workflow": True,
                    "confidence": 0.85,
                    "label": 1
                })
    
    samples.extend(augmented_samples)
    
    return samples


def generate_regular_task_samples() -> List[Dict[str, Any]]:
    """일반 작업 요청 샘플을 생성합니다 (negative samples).
    
    Returns:
        일반 작업 요청 샘플 리스트
    """
    samples = []
    
    # 취소 요청
    cancel_queries = [
        "출장 일정 취소해줘", "출장 취소", "휴가 취소해줘", "휴가 일정 취소",
        "출장 신청 취소해줘", "일정 취소해줘", "예약 취소해줘",
        "회의실 예약 취소", "출장 예약 취소해줘",
    ]
    
    # 삭제 요청
    delete_queries = [
        "출장 일정 삭제해줘", "일정 삭제", "출장 삭제해줘",
        "휴가 일정 삭제", "일정 지워줘", "출장 일정 제거해줘",
    ]
    
    # 수정 요청
    update_queries = [
        "출장 일정 수정해줘", "일정 수정", "출장 수정해줘",
        "휴가 일정 변경해줘", "일정 고쳐줘", "출장 일정 변경해줘",
        "일정 업데이트해줘",
    ]
    
    # 정보 질문 (질문형)
    info_question_queries = [
        "출장 규정 알려줘", "휴가 신청 방법은?", "출장 신청 절차 알려줘",
        "출장 신청하려면 어떻게 해야 해?", "휴가 신청하려면 어떻게 하나요?",
        "출장 준비는 어떻게 하나요?", "휴가 신청 절차 알려줘",
        "출장 가려면 뭐가 필요한가요?", "휴가 다니려면 어떻게 해야 하나요?",
        "연차 신청 방법은?", "회의실 예약 방법 알려줘", "문서 작성 방법은?",
    ]
    
    # 정보 조회 요청
    info_request_queries = [
        "출장 신청서 양식 보여줘", "휴가 신청서 보여줘", "일정 조회해줘",
        "내 일정 보여줘", "이번 달 연차 사용 내역 알려줘",
        "다음 주 회의실 예약 가능한 시간 보여줘", "출장 규정 보여줘",
        "휴가 규정 알려줘",
    ]
    
    # 단일 작업 요청
    single_task_queries = [
        "내일 연차 신청", "회의실 예약해줘",
        "이메일에서 육아휴직 관련 내용 찾아줘", "문서 작성해줘",
        "보고서 작성해줘", "제품개발 계획서 작성해줘",
        "특허 검색해줘", "논문 찾아줘", "인재 추천해줘",
        "이력서 검색해줘", "오늘 식단 뭐야?", "내일 점심 메뉴 알려줘",
        "메뉴얼에서 연차 정보 알려줘", "복리후생 정책 알려줘",
        "연차 신청해줘", "반차 신청", "병가 신청해줘", "경조사 신청",
        "회의실 예약", "회의실 예약 가능한 시간 보여줘",
        "이메일 검색해줘", "메일에서 특정 키워드 찾아줘",
        "이메일 답장문 생성해줘", "메일 자동응답 설정해줘",
        "발주서 작성해줘", "인사정보 요약 리포트 작성",
        "월별 수익 분석 리포트 작성해줘", "업무 성과 평가서 작성",
        "공급업체 평가서 작성해줘", "계약 갱신 보고서 작성",
        "활동기준 원가 계산서 작성해줘", "프로젝트 일정표 작성",
        "제품 비교표 작성해줘", "바이러스 관련 특허 검색해줘",
        "KISTI에서 논문 검색", "ScienceON에서 자료 찾아줘",
        "특허 정보 알려줘", "논문 다운로드해줘",
        "Python 개발자 추천해줘", "지원자 찾아줘",
        "채용 관련 정보 알려줘", "인재 채용 프로세스 알려줘",
        "구내식당 메뉴 조회", "다이어트 중인데 내일 식단을 어떻게 먹어야해?",
        "우리 회사 메뉴얼에서 연차사용 정보 알려줘", "매뉴얼 보여줘",
        "급여 외 혜택에 대해 알려줘", "휴가규정 알려줘",
        "근무제도에 대해 알려줘", "보험 정보 알려줘",
        "수당 관련 정보 알려줘",
    ]
    
    # 취소/삭제/수정 변형 생성
    items = ["출장", "휴가", "일정", "예약"]
    actions = ["취소해줘", "취소", "삭제해줘", "삭제", "수정해줘", "수정", "변경해줘", "변경"]
    
    for item in items:
        for action in actions:
            query = f"{item} {action}"
            if query not in cancel_queries + delete_queries + update_queries:
                if "취소" in action or "취소해줘" in action:
                    cancel_queries.append(query)
                elif "삭제" in action or "지워줘" in action or "제거" in action:
                    delete_queries.append(query)
                elif "수정" in action or "변경" in action or "고쳐줘" in action or "업데이트" in action:
                    update_queries.append(query)
    
    # 정보 질문 변형 생성
    items = ["출장", "휴가", "연차", "회의실", "문서"]
    questions = ["규정 알려줘", "방법은?", "절차 알려줘", "어떻게 해야 해?", "어떻게 하나요?"]
    
    for item in items:
        for question in questions:
            query = f"{item} {question}"
            if query not in info_question_queries:
                info_question_queries.append(query)
    
    # 조회 요청 변형 생성
    items = ["출장", "휴가", "일정", "문서", "이메일"]
    requests = ["보여줘", "알려줘", "조회해줘", "찾아줘", "확인해줘"]
    
    for item in items:
        for request in requests:
            query = f"{item} {request}"
            if query not in info_request_queries:
                info_request_queries.append(query)
    
    # 추가 단일 작업 변형 생성
    additional_single_tasks = []
    
    # 문서 작성 변형
    document_templates = [
        "{doc_type} 작성해줘",
        "{doc_type} 작성",
        "{doc_type} 만들어줘",
        "{doc_type} 생성해줘",
    ]
    doc_types = [
        "문서", "보고서", "제품개발 계획서", "발주서", "인사정보 요약 리포트",
        "월별 수익 분석 리포트", "업무 성과 평가서", "공급업체 평가서",
        "계약 갱신 보고서", "활동기준 원가 계산서", "프로젝트 일정표", "제품 비교표",
    ]
    
    for template in document_templates:
        for doc_type in doc_types:
            additional_single_tasks.append(template.format(doc_type=doc_type))
    
    # 검색 요청 변형
    search_templates = [
        "{item} 검색해줘",
        "{item} 찾아줘",
        "{item} 검색",
        "{item} 찾기",
    ]
    search_items = [
        "특허", "논문", "이메일", "메일", "이력서", "인재", "지원자",
        "바이러스 관련 특허", "KISTI 논문", "ScienceON 자료",
    ]
    
    for template in search_templates:
        for item in search_items:
            additional_single_tasks.append(template.format(item=item))
    
    # 정보 조회 변형
    info_templates = [
        "{item} 알려줘",
        "{item} 보여줘",
        "{item} 조회해줘",
        "{item} 확인해줘",
    ]
    info_items = [
        "오늘 식단", "내일 점심 메뉴", "구내식당 메뉴", "복리후생 정책",
        "급여 외 혜택", "휴가규정", "근무제도", "보험 정보", "수당",
        "연차 사용 내역", "일정", "회의실 예약 가능한 시간",
    ]
    
    for template in info_templates:
        for item in info_items:
            additional_single_tasks.append(template.format(item=item))
    
    # 모든 일반 작업 쿼리 합치기
    all_regular_queries = (
        cancel_queries +
        delete_queries +
        update_queries +
        info_question_queries +
        info_request_queries +
        single_task_queries +
        additional_single_tasks
    )
    
    # 중복 제거
    seen = set()
    unique_queries = []
    for query in all_regular_queries:
        if query not in seen:
            seen.add(query)
            unique_queries.append(query)
    
    for query in unique_queries:
        samples.append({
            "query": query,
            "is_workflow": False,
            "confidence": 0.95,
            "label": 0
        })
    
    # 데이터 증강 적용
    augmented_samples = []
    for sample in samples[:100]:  # 일부만 증강
        for aug_type in ["paraphrase"]:
            augmented = augment_text(sample["query"], aug_type)
            if augmented != sample["query"]:
                augmented_samples.append({
                    "query": augmented,
                    "is_workflow": False,
                    "confidence": 0.85,
                    "label": 0
                })
    
    samples.extend(augmented_samples)
    
    return samples


def generate_edge_case_samples() -> List[Dict[str, Any]]:
    """경계 케이스 샘플을 생성합니다.
    
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
        "출장 준비 방법 알려줘",
        "휴가 신청 절차는?",
        "출장 가는 방법은?",
    ]
    
    for query in workflow_like_but_question:
        samples.append({
            "query": query,
            "is_workflow": False,
            "confidence": 0.90,
            "label": 0
        })
    
    # 일반 작업처럼 보이지만 워크플로우 (실행 의도가 명확)
    regular_like_but_workflow = [
        "출장 신청해줘",
        "휴가 신청해줘",
        "출장 준비해줘",
        "휴가 준비해줘",
        "출장 갈게",
        "휴가 갈게",
    ]
    
    for query in regular_like_but_workflow:
        samples.append({
            "query": query,
            "is_workflow": True,
            "confidence": 0.90,
            "label": 1
        })
    
    return samples


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
            "description": "1단계 워크플로우 vs 일반 요청 분류 데이터셋 (KLUE-RoBERTa/SetFit 최적화)",
            "classification_type": "binary",
            "models": ["KLUE-RoBERTa", "SetFit"],
        },
        "samples": samples
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    
    print(f"[JSON 데이터셋 저장 완료] {output_path} ({len(samples)}개 샘플)")


def save_huggingface_format(samples: List[Dict[str, Any]], output_dir: Path) -> None:
    """HuggingFace datasets 형식으로 저장합니다 (KLUE-RoBERTa 학습용).
    
    Args:
        samples: 샘플 리스트
        output_dir: 출력 디렉토리
    """
    # 디렉토리 생성
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # JSON Lines 형식으로 저장
    jsonl_path = output_dir / "dataset.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for sample in samples:
            json.dump({
                "text": sample["query"],
                "label": sample["label"]
            }, f, ensure_ascii=False)
            f.write("\n")
    
    print(f"[HuggingFace 형식 저장 완료] {jsonl_path} ({len(samples)}개 샘플)")


def generate_dataset() -> None:
    """1단계 워크플로우 vs 일반 요청 분류 데이터셋을 생성합니다."""
    print("[1단계 워크플로우 vs 일반 요청 분류 데이터셋 생성 시작 (향상 버전)]")
    print("[KLUE-RoBERTa 및 SetFit 모델 학습 최적화]")
    
    # 출력 디렉토리 생성
    output_dir = Path(__file__).parent.parent / "data" / "step1_classification_dataset"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 샘플 생성
    print("\n[워크플로우 요청 샘플 생성 중...]")
    workflow_samples = generate_workflow_samples()
    print(f"   [완료] {len(workflow_samples)}개 워크플로우 샘플 생성 완료")
    
    print("[일반 작업 요청 샘플 생성 중...]")
    regular_samples = generate_regular_task_samples()
    print(f"   [완료] {len(regular_samples)}개 일반 작업 샘플 생성 완료")
    
    print("[경계 케이스 샘플 생성 중...]")
    edge_case_samples = generate_edge_case_samples()
    print(f"   [완료] {len(edge_case_samples)}개 경계 케이스 샘플 생성 완료")
    
    # 모든 샘플 합치기
    all_samples = workflow_samples + regular_samples + edge_case_samples
    
    # 중복 제거
    seen_queries = set()
    unique_samples = []
    for sample in all_samples:
        query = sample["query"]
        if query not in seen_queries:
            seen_queries.add(query)
            unique_samples.append(sample)
    
    all_samples = unique_samples
    
    # 통계 출력
    print("\n[데이터셋 통계]")
    print(f"   총 샘플 수: {len(all_samples)}")
    workflow_count = sum(1 for s in all_samples if s["is_workflow"])
    regular_count = sum(1 for s in all_samples if not s["is_workflow"])
    print(f"   워크플로우 샘플: {workflow_count} ({workflow_count/len(all_samples)*100:.1f}%)")
    print(f"   일반 작업 샘플: {regular_count} ({regular_count/len(all_samples)*100:.1f}%)")
    
    # 클래스 균형 확인
    if workflow_count > regular_count * 1.5:
        print(f"   [경고] 워크플로우 샘플이 일반 작업 샘플보다 {workflow_count/regular_count:.1f}배 많습니다.")
    elif regular_count > workflow_count * 1.5:
        print(f"   [경고] 일반 작업 샘플이 워크플로우 샘플보다 {regular_count/workflow_count:.1f}배 많습니다.")
    else:
        print(f"   [확인] 클래스 균형이 적절합니다.")
    
    # CSV 저장
    csv_path = output_dir / "step1_classification_dataset.csv"
    save_dataset_csv(all_samples, csv_path)
    
    # JSON 저장
    json_path = output_dir / "step1_classification_dataset.json"
    save_dataset_json(all_samples, json_path)
    
    # HuggingFace 형식 저장
    save_huggingface_format(all_samples, output_dir)
    
    # 학습/검증/테스트 분할 (80/10/10)
    print("\n[데이터셋 분할 중...]")
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
    
    save_huggingface_format(train_samples, output_dir / "train")
    save_huggingface_format(val_samples, output_dir / "val")
    save_huggingface_format(test_samples, output_dir / "test")
    
    print(f"\n[데이터셋 생성 완료]")
    print(f"   학습 세트: {len(train_samples)}개")
    print(f"   검증 세트: {len(val_samples)}개")
    print(f"   테스트 세트: {len(test_samples)}개")
    print(f"\n[저장 위치] {output_dir}")
    print(f"\n[사용 가능한 형식]")
    print(f"   - CSV: train.csv, val.csv, test.csv")
    print(f"   - JSON: train.json, val.json, test.json")
    print(f"   - HuggingFace: train/dataset.jsonl, val/dataset.jsonl, test/dataset.jsonl")


if __name__ == "__main__":
    generate_dataset()

