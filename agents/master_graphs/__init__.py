"""마스터그래프 모듈 - 각 도메인별 LangGraph 서브그래프."""

from agents.master_graphs.base import BaseMasterGraph

# 도메인별 마스터그래프 import (새 폴더 구조)
# schedule 마스터그래프는 주석 처리 (폴더가 없음)
# from agents.master_graphs.schedule import get_schedule_master_graph
# document 마스터그래프는 lazy import (sentence_transformers 의존성 때문)
# qa 마스터그래프도 lazy import (qa_benefit_agent, qa_cafeteria_agent가 sentence_transformers 사용)
# email 마스터그래프는 주석 처리 (폴더가 없을 수 있음)
# from agents.master_graphs.email import get_email_master_graph
# tech 마스터그래프는 주석 처리 (폴더가 없을 수 있음)
# from agents.master_graphs.tech import get_tech_master_graph

# document 마스터그래프는 lazy import (sentence_transformers 의존성 때문)
def get_document_master_graph():
    """문서 마스터그래프를 lazy import합니다 (sentence_transformers 의존성 회피)."""
    # document 모듈 자체를 lazy import
    from agents.master_graphs.document.master_graph import get_document_master_graph as _get_document_master_graph
    return _get_document_master_graph()

# qa 마스터그래프도 lazy import (qa_benefit_agent, qa_cafeteria_agent가 sentence_transformers 사용)
# qa 폴더가 없을 수 있으므로 에러 처리 추가
def get_qa_master_graph():
    """QA 마스터그래프를 lazy import합니다 (sentence_transformers 의존성 회피)."""
    try:
        from agents.master_graphs.qa.master_graph import get_qa_master_graph as _get_qa_master_graph
        return _get_qa_master_graph()
    except ImportError as e:
        raise ImportError(f"qa 마스터그래프를 찾을 수 없습니다. qa 폴더가 필요합니다. ({str(e)})") from e
        raise ImportError("qa 마스터그래프를 찾을 수 없습니다. qa 폴더가 필요합니다.")

# schedule 마스터그래프 lazy import (폴더가 없을 수 있음)
def get_schedule_master_graph():
    """Schedule 마스터그래프를 lazy import합니다."""
    try:
        from agents.master_graphs.schedule import get_schedule_master_graph as _get_schedule_master_graph
        return _get_schedule_master_graph()
    except ImportError:
        raise ImportError("schedule 마스터그래프를 찾을 수 없습니다. schedule 폴더가 필요합니다.")

# email 마스터그래프 lazy import (폴더가 없을 수 있음)
def get_email_master_graph():
    """Email 마스터그래프를 lazy import합니다."""
    try:
        from agents.master_graphs.email import get_email_master_graph as _get_email_master_graph
        return _get_email_master_graph()
    except ImportError:
        raise ImportError("email 마스터그래프를 찾을 수 없습니다. email 폴더가 필요합니다.")

# tech 마스터그래프 lazy import (폴더가 없을 수 있음)
def get_tech_master_graph():
    """Tech 마스터그래프를 lazy import합니다."""
    try:
        from agents.master_graphs.tech import get_tech_master_graph as _get_tech_master_graph
        return _get_tech_master_graph()
    except ImportError:
        raise ImportError("tech 마스터그래프를 찾을 수 없습니다. tech 폴더가 필요합니다.")

__all__ = [
    "BaseMasterGraph",
    "get_schedule_master_graph",
    "get_document_master_graph",
    "get_qa_master_graph",
    "get_email_master_graph",
    "get_tech_master_graph",
]

