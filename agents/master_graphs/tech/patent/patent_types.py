"""특허 검색 관련 타입 정의."""

from typing import TypedDict, List, Dict, Any, Literal, Optional


class PatentSearchState(TypedDict):
    """특허 검색 상태 정의."""
    user_query: str
    keyword: str
    search_results: List[Dict[str, Any]]
    selected_patent: Optional[Dict[str, Any]]
    selected_indices: List[int]
    vector_db_results: List[Dict[str, Any]]
    pdf_content: str
    pdf_link: str
    answer: str
    next_action: Literal["check_db", "search_kipris", "download_pdf", "rag_answer", "end", "ask_search_mode", "ask_web_search"]
    progress_status: str  # 진행 상황 메시지
    search_mode: Optional[Literal["server", "web"]]  # 검색 모드: 서버 기반 또는 웹 검색
    use_server_first: bool  # 서버 기반 먼저 시도 여부
    session_id: Optional[str]  # 세션 ID (검색 모드 선택 대기 상태 관리용)

