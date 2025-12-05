from typing import TypedDict, Annotated, Optional, List, Dict, Any
import operator

class Message(TypedDict, total=False):
    """대화 메시지 구조."""
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: Optional[str]


class OrchestratorState(TypedDict):
    """
    Shared state for the LangGraph Orchestrator.
    """
    # Input
    query: str
    user_id: Optional[str]
    session_id: Optional[str]
    
    # Workflow Context
    domain: Optional[str]  # 'schedule', 'document', 'qa', 'email', 'tech'
    last_domain: Optional[str]  # 이전 대화의 도메인 (단순 답변 시 라우팅용)
    intent: Optional[str]  # 'business_trip', 'research', 'meeting', etc.
    template_name: Optional[str]  # 문서 템플릿 이름 (예: 'work_performance_review', 'hr_summary_report')
    current_step: Optional[str]  # Optional로 변경 (기본값 없음)
    
    # Extracted Data (Entities)
    employee_id: Optional[int]
    employee_name: Optional[str]
    start_date: Optional[str]
    end_date: Optional[str]
    destination: Optional[str]
    topic: Optional[str] # For research or meeting
    participants: List[str]
    
    # Intermediate Artifacts
    schedule_conflicts: List[Dict[str, Any]]
    document_content: Optional[str]
    document_path: Optional[str]
    email_draft: Optional[str]
    search_results: List[Dict[str, Any]] # For patents/papers, emails
    summary_text: Optional[str]
    
    # Email Search Context (Redis 숏텀 메모리로 관리)
    email_search_context: Optional[Dict[str, Any]]  # 이메일 검색 관련 컨텍스트
    # 구조: {
    #   "ids": List[str],  # 검색된 이메일 ID 목록
    #   "items": List[Dict],  # 검색 결과 아이템 목록
    #   "index_map": Dict[int, int],  # 표시 번호 -> 실제 인덱스 매핑
    #   "batch_offset": int,  # 현재 배치 오프셋
    #   "last_keyword": str,  # 마지막 검색 키워드
    #   "keyword_offsets": Dict[str, int]  # 키워드별 마지막 검색 위치
    # }
    
    # Tech Search Context (Redis 숏텀 메모리로 관리)
    tech_search_context: Optional[Dict[str, Any]]  # 기술 검색(특허/논문) 관련 컨텍스트
    # 구조: {
    #   "patent": {  # 특허 검색 컨텍스트
    #     "results": List[Dict],  # 검색 결과 목록
    #     "last_keyword": str,  # 마지막 검색 키워드
    #     "displayed_count": int,  # 표시된 결과 개수
    #   },
    #   "article": {  # 논문 검색 컨텍스트
    #     "results": List[Dict],  # 검색 결과 목록
    #     "last_keyword": str,  # 마지막 검색 키워드
    #     "displayed_count": int,  # 표시된 결과 개수
    #   }
    # }
    
    # QA Search Context (Redis 숏텀 메모리로 관리)
    qa_search_context: Optional[Dict[str, Any]]  # QA 검색(메뉴얼/복리후생/식단) 관련 컨텍스트
    # 구조: {
    #   "manual": {  # 메뉴얼 검색 컨텍스트
    #     "last_query": str,  # 마지막 검색 쿼리
    #     "last_answer": str,  # 마지막 답변
    #     "retrieved_contexts": List[str],  # 검색된 문서 컨텍스트
    #   },
    #   "benefit": {  # 복리후생 검색 컨텍스트
    #     "last_query": str,  # 마지막 검색 쿼리
    #     "last_answer": str,  # 마지막 답변
    #     "retrieved_contexts": List[str],  # 검색된 문서 컨텍스트
    #   },
    #   "cafeteria": {  # 식단 검색 컨텍스트
    #     "last_query": str,  # 마지막 검색 쿼리
    #     "last_answer": str,  # 마지막 답변
    #     "retrieved_contexts": List[str],  # 검색된 문서 컨텍스트
    #   }
    # }
    
    # Schedule Context (Redis 숏텀 메모리로 관리)
    schedule_context: Optional[Dict[str, Any]]  # 스케줄(연차/일정/회의) 관련 컨텍스트
    # 구조: {
    #   "leave": {  # 연차 관련 컨텍스트
    #     "last_query": str,  # 마지막 검색 쿼리
    #     "last_action": str,  # 마지막 액션 (조회/신청/내역/추천)
    #     "last_balance": Dict,  # 마지막 조회한 잔여 연차 정보
    #     "pending_approval": Optional[Dict],  # 승인 대기 중인 연차 신청 정보
    #   },
    #   "meeting": {  # 회의실 관련 컨텍스트
    #     "last_query": str,  # 마지막 검색 쿼리
    #     "recommended_rooms": List[Dict],  # 추천된 회의실 목록
    #     "pending_reservation": Optional[Dict],  # 예약 대기 중인 회의실 정보
    #   },
    #   "schedule": {  # 일정 관련 컨텍스트
    #     "last_query": str,  # 마지막 검색 쿼리
    #     "last_filters": Dict,  # 마지막 조회 필터
    #     "last_results": List[Dict],  # 마지막 조회 결과
    #   }
    # }
    
    # Document Context (Redis 숏텀 메모리로 관리)
    document_context: Optional[Dict[str, Any]]  # 문서 생성/업로드 관련 컨텍스트
    # 구조: {
    #   "last_template_type": str,  # 마지막 생성한 문서 템플릿 타입
    #   "last_variables": Dict,  # 마지막 사용한 변수들
    #   "pending_document": Optional[Dict],  # 생성 대기 중인 문서 정보
    # }
    
    # Final Output
    final_response: Optional[str]
    messages: Annotated[List[str], operator.add]
    conversation_history: Annotated[List[Message], operator.add]  # 멀티턴 대화 히스토리

