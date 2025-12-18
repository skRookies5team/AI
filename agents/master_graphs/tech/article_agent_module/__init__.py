"""논문 검색 에이전트 패키지."""

# nodes.py에서도 export (ragas_scienceon_eval.py에서 사용)
from .nodes import (
    rag_answer_node,
)

# vector_db.py에서도 export (ragas_scienceon_eval.py에서 사용)
from .vector_db import (
    retrieve_paper_chunks_from_milvus,
)

# tech_article_agent.py의 함수들은 lazy import로 처리 (순환 import 방지)
def __getattr__(name):
    """Lazy import for functions from tech_article_agent."""
    _lazy_imports = {
        "run_scienceon_agent",
        "build_scienceon_agent_graph",
        "create_scienceon_subgraph",
        "extract_scienceon_state",
        "merge_scienceon_state",
        "create_scienceon_node",
        "article_master",
        "article_search",
        "TechArticleSession",
    }
    
    if name in _lazy_imports:
        from agents.master_graphs.tech import tech_article_agent
        value = getattr(tech_article_agent, name)
        # globals()에 저장하여 다음 호출 시 재import 방지
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [ 
    "run_scienceon_agent",
    "build_scienceon_agent_graph",
    "create_scienceon_subgraph",
    "extract_scienceon_state",
    "merge_scienceon_state",
    "create_scienceon_node",
    "article_master",
    "article_search",
    "TechArticleSession",
    "rag_answer_node",
    "retrieve_paper_chunks_from_milvus",
]
